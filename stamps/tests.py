"""スタンプカード（来店計測）のテスト。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md

中核ロジック（付与判定）を厚く検証する：
  - 出店時間内のみ付与／時間外は弾く
  - 1日1回（同日2回目は弾く・翌日は押せる）
  - 5/10/20pt で特典が発行される
  - 20pt 打ち止め（以降は新カードを作らず据え置き）
  - 期限切れ→新カードで再スタート
  - 特典使用（B案）と二重使用防止
管理画面はスモークテスト（200で開く・CSVが返る）。
"""
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from sales.models import SalesLocation
from reservations.models import LineMember
from stamps import services
from stamps.models import CAP_PT, Reward, RewardTier, StampCard, StampConfig, StampLog


def _dt(y, m, d, hh, mm):
    """JSTの aware datetime を作る。"""
    naive = timezone.datetime(y, m, d, hh, mm)
    return timezone.make_aware(naive, timezone.get_current_timezone())


class StampServiceTests(TestCase):
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="テスト本店", type="A", price_type="A")
        self.member = LineMember.objects.create(line_user_id="U_test_1", name="テスト太郎")

    def _award(self, hh=12, mm=0, day=None):
        now = _dt(2026, 7, 1, hh, mm) if day is None else day
        return services.award_stamp(self.member, self.loc, now=now)

    def test_stamp_inside_hours(self):
        r = self._award(hh=12)
        self.assertTrue(r.ok)
        self.assertEqual(r.card.stamp_count, 1)
        self.assertEqual(StampLog.objects.count(), 1)

    def test_stamp_outside_hours_rejected(self):
        r = self._award(hh=9)   # 11:00前
        self.assertEqual(r.status, services.OUTSIDE_HOURS)
        self.assertEqual(StampLog.objects.count(), 0)

    def test_per_location_hours_override(self):
        # 本店は製造中（9:00〜）にも来店あり＝個別に早める
        self.loc.stamp_open_time = time(9, 0)
        self.loc.save(update_fields=["stamp_open_time"])
        r = self._award(hh=9, mm=30)
        self.assertTrue(r.ok)

    def test_once_per_day(self):
        self.assertTrue(self._award(hh=12).ok)
        r2 = self._award(hh=13)  # 同日2回目
        self.assertEqual(r2.status, services.ALREADY_TODAY)
        self.assertEqual(StampLog.objects.count(), 1)

    def test_next_day_ok(self):
        self.assertTrue(self._award(day=_dt(2026, 7, 1, 12, 0)).ok)
        r2 = services.award_stamp(self.member, self.loc, now=_dt(2026, 7, 2, 12, 0))
        self.assertTrue(r2.ok)
        self.assertEqual(r2.card.stamp_count, 2)

    def _stamp_n_days(self, n, start_day=1):
        """n日連続でスタンプ（毎日12:00）。最後の結果を返す。"""
        result = None
        for i in range(n):
            day = _dt(2026, 7, 1, 12, 0) + timedelta(days=start_day - 1 + i)
            result = services.award_stamp(self.member, self.loc, now=day)
        return result

    def test_reward_issued_at_5(self):
        r = self._stamp_n_days(5)
        self.assertTrue(r.ok)
        self.assertIsNotNone(r.new_reward)
        self.assertEqual(r.new_reward.threshold_pt, 5)
        self.assertEqual(Reward.objects.filter(threshold_pt=5).count(), 1)

    def test_reward_issued_at_10(self):
        self._stamp_n_days(10)
        self.assertEqual(Reward.objects.filter(threshold_pt=10).count(), 1)

    def test_cap_at_20_then_no_more(self):
        r20 = self._stamp_n_days(20)
        self.assertTrue(r20.ok)
        self.assertEqual(r20.card.stamp_count, CAP_PT)
        self.assertEqual(r20.card.status, StampCard.STATUS_COMPLETED)
        # 21日目：打ち止め＝新カードを作らず据え置き
        r21 = self._stamp_n_days(1, start_day=21)
        self.assertEqual(r21.status, services.COMPLETED)
        self.assertEqual(StampCard.objects.filter(member=self.member).count(), 1)
        # お弁当無料は2個まで
        self.assertEqual(Reward.objects.filter(kind="free").count(), 2)

    def test_expiry_starts_new_card(self):
        first = self._award(day=_dt(2026, 7, 1, 12, 0))
        self.assertTrue(first.ok)
        # 期限後（31日後）に押すと新カード
        later = services.award_stamp(self.member, self.loc, now=_dt(2026, 8, 5, 12, 0))
        self.assertTrue(later.ok)
        self.assertEqual(StampCard.objects.filter(member=self.member).count(), 2)
        self.assertEqual(later.card.stamp_count, 1)
        old = StampCard.objects.get(pk=first.card.pk)
        self.assertEqual(old.status, StampCard.STATUS_EXPIRED)

    def test_reward_outlives_expired_card(self):
        """クーポンは獲得日から独立した期限を持つ＝カードが先に失効しても使える。"""
        # base=8/1。カードは35日前(6/27)開始で7/27失効済み。10個目（クーポン獲得）は8日前(7/24)。
        base = _dt(2026, 8, 1, 12, 0)
        offsets = [35, 30, 25, 20, 18, 16, 14, 12, 10, 8]
        for d in offsets:
            services.award_stamp(self.member, self.loc, now=base - timedelta(days=d))
        card = self.member.stamp_cards.first()
        reward = card.rewards.get(threshold_pt=10)
        today = base.date()
        self.assertTrue(today > card.expires_on)     # カードは失効済み
        self.assertTrue(today <= reward.expires_on)  # クーポンはまだ有効（独立期限）
        self.assertTrue(services.use_reward(reward, now=base))  # 失効カードのクーポンが使える

    def test_reward_not_usable_on_acquisition_visit(self):
        """獲得した来店（同日）にはクーポンを使えない（10回購入で1個無料を守る）。"""
        r = self._stamp_n_days(5)         # 5個目は 2026-07-05 に獲得
        reward = r.new_reward
        self.assertEqual(reward.valid_from, reward.issued_at.date() + timedelta(days=1))
        # 獲得当日（7/05）は使えない
        same_day = _dt(2026, 7, 5, 13, 0)
        self.assertFalse(services.use_reward(reward, now=same_day))
        reward.refresh_from_db()
        self.assertEqual(reward.status, Reward.STATUS_ISSUED)  # 据え置き（消費されない）

    def test_use_reward_and_double_use(self):
        r = self._stamp_n_days(5)         # 5個目は 2026-07-05、利用開始 7/06
        reward = r.new_reward
        later = _dt(2026, 7, 8, 12, 0)    # 次回来店以降
        self.assertTrue(services.use_reward(reward, now=later))
        reward.refresh_from_db()
        self.assertEqual(reward.status, Reward.STATUS_USED)
        # 二重使用は弾く
        self.assertFalse(services.use_reward(reward, now=later))


class RichMenuAssignTests(TestCase):
    """スタンプ用リッチメニューの per-user 割当て（LINE APIはモック）。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="本店", type="A", price_type="A",
                                                stamp_enabled=True)
        self.member = LineMember.objects.create(line_user_id="U_rm", name="メニュー太郎")

    def test_assign_records_link_when_configured(self):
        from unittest import mock
        from stamps import richmenu
        from stamps.models import RichMenuLink
        with self.settings(LINE_STAMP_RICHMENU_ID="rm-123"), \
             mock.patch("stamps.richmenu.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.richmenu.line_richmenu.link_to_user", return_value=True) as link:
            ok = richmenu.assign(self.member)
        self.assertTrue(ok)
        link.assert_called_once_with("U_rm", "rm-123")
        rec = RichMenuLink.objects.get(member=self.member)
        self.assertEqual(rec.status, RichMenuLink.STATUS_LINKED)
        self.assertEqual(rec.rich_menu_id, "rm-123")

    def test_assign_failed_when_not_configured(self):
        from stamps import richmenu
        from stamps.models import RichMenuLink
        with self.settings(LINE_STAMP_RICHMENU_ID=""):
            ok = richmenu.assign(self.member)
        self.assertFalse(ok)
        self.assertEqual(RichMenuLink.objects.get(member=self.member).status,
                         RichMenuLink.STATUS_FAILED)

    def test_register_stamp_menu_stores_id(self):
        from unittest import mock
        from stamps import richmenu
        from stamps.models import RichMenu
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100  # ダミーPNGバイト
        with mock.patch("stamps.richmenu.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.richmenu.line_richmenu.create_rich_menu", return_value="rm-new") as create, \
             mock.patch("stamps.richmenu.line_richmenu.upload_image") as upload:
            ok, err = richmenu.register_stamp_menu(image_png_bytes=png, urls={"menu": "https://x"})
        self.assertTrue(ok, err)
        create.assert_called_once()
        upload.assert_called_once()
        menu = RichMenu.objects.get(purpose=RichMenu.PURPOSE_STAMP)
        self.assertEqual(menu.line_rich_menu_id, "rm-new")
        self.assertEqual(len(menu.areas), 4)
        # DB登録IDが stamp_richmenu_id に反映される
        self.assertEqual(richmenu.stamp_richmenu_id(), "rm-new")

    def test_register_requires_image(self):
        from unittest import mock
        from stamps import richmenu
        with mock.patch("stamps.richmenu.line_richmenu._token", return_value="tok"):
            ok, err = richmenu.register_stamp_menu(image_png_bytes=None, urls={})
        self.assertFalse(ok)

    def test_first_stamp_triggers_assign(self):
        from unittest import mock
        with self.settings(STAMP_REQUIRE_LINE=False, LINE_STAMP_RICHMENU_ID="rm-1",
                           STAMP_RICHMENU_AUTO_ASSIGN=True), \
             mock.patch("stamps.richmenu.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.richmenu.line_richmenu.link_to_user", return_value=True) as link:
            self.loc.stamp_open_time = time(0, 0)
            self.loc.stamp_close_time = time(23, 59)
            self.loc.save(update_fields=["stamp_open_time", "stamp_close_time"])
            resp = self.client.post(f"/stamp/{self.loc.qr_stamp_token}/scan/")
            self.assertEqual(resp.status_code, 200)
            # 初回スタンプで割当てが1回呼ばれる
            self.assertEqual(link.call_count, 1)


class StampManageViewTests(TestCase):
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="テスト店", type="A", price_type="A",
                                                stamp_enabled=True)
        User = get_user_model()
        self.staff = User.objects.create_user(username="staff", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_dashboard_opens(self):
        self.assertEqual(self.client.get("/stamp/manage/").status_code, 200)

    def test_visits_csv(self):
        resp = self.client.get("/stamp/manage/visits/?export=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_analytics_opens(self):
        self.assertEqual(self.client.get("/stamp/manage/analytics/").status_code, 200)

    def test_locations_opens(self):
        self.assertEqual(self.client.get("/stamp/manage/locations/").status_code, 200)

    def test_rewards_opens(self):
        self.assertEqual(self.client.get("/stamp/manage/rewards/").status_code, 200)

    def test_requires_staff(self):
        self.client.logout()
        resp = self.client.get("/stamp/manage/")
        self.assertIn(resp.status_code, (301, 302))  # ログインへリダイレクト

    def test_friends_opens(self):
        LineMember.objects.create(line_user_id="U_f1", name="友だちA")
        self.assertEqual(self.client.get("/stamp/manage/friends/").status_code, 200)

    def test_friends_csv(self):
        resp = self.client.get("/stamp/manage/friends/?export=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_friends_store_filter(self):
        loc2 = SalesLocation.objects.create(no=2, name="二号店", type="A", price_type="A",
                                            stamp_enabled=True)
        a = LineMember.objects.create(line_user_id="U_a", name="来店Aさん")
        b = LineMember.objects.create(line_user_id="U_b", name="来店Bさん")
        noon = timezone.make_aware(timezone.datetime(2026, 5, 1, 12, 0),
                                   timezone.get_current_timezone())
        services.award_stamp(a, self.loc, now=noon)   # Aは本店
        services.award_stamp(b, loc2, now=noon)        # Bは二号店
        resp = self.client.get(f"/stamp/manage/friends/?loc={self.loc.id}")
        self.assertContains(resp, "来店Aさん")
        self.assertNotContains(resp, "来店Bさん")   # 本店フィルタでBは出ない

    def test_richmenu_opens(self):
        self.assertEqual(self.client.get("/stamp/manage/richmenu/").status_code, 200)

    def test_location_pop_opens(self):
        resp = self.client.get(f"/stamp/manage/locations/{self.loc.id}/pop/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "スタンプカード")
        self.assertContains(resp, "data:image/png;base64,")  # QRが埋め込まれている


class StampCustomerViewTests(TestCase):
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="テスト店", type="A", price_type="A",
                                                stamp_enabled=True)

    def test_stamp_page_invalid_token_404(self):
        self.assertEqual(self.client.get("/stamp/NOPE/").status_code, 404)

    def test_liff_entry_redirects_liffstate(self):
        # LINEが ?liff.state=/stamp/<token>/ でルートを開く → 本来のURLへ302
        path = f"/stamp/{self.loc.qr_stamp_token}/"
        resp = self.client.get("/", {"liff.state": path})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], path)

    def test_liff_entry_root_without_state_404(self):
        self.assertEqual(self.client.get("/").status_code, 404)   # 従来どおりルートは404

    def test_liff_entry_blocks_open_redirect(self):
        self.assertEqual(self.client.get("/", {"liff.state": "https://evil.com/"}).status_code, 404)
        self.assertEqual(self.client.get("/", {"liff.state": "//evil.com/"}).status_code, 404)

    def test_stamp_page_disabled_403(self):
        self.loc.stamp_enabled = False
        self.loc.save(update_fields=["stamp_enabled"])
        resp = self.client.get(f"/stamp/{self.loc.qr_stamp_token}/")
        self.assertEqual(resp.status_code, 403)

    def test_stamp_page_entry_200(self):
        resp = self.client.get(f"/stamp/{self.loc.qr_stamp_token}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "スタンプを準備しています")

    def test_my_card_get_loader(self):
        resp = self.client.get("/stamp/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "スタンプを準備しています")

    def test_my_card_post_shows_card_without_stamping(self):
        with self.settings(STAMP_REQUIRE_LINE=False):
            resp = self.client.post("/stamp/me/")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(StampLog.objects.count(), 0)  # 来店せず＝スタンプは増えない

    def test_scan_dev_fallback_awards(self):
        # STAMP_REQUIRE_LINE=False（dev）＝トークン無しでも仮会員で押せる
        with self.settings(STAMP_REQUIRE_LINE=False):
            self.loc.stamp_open_time = time(0, 0)
            self.loc.stamp_close_time = time(23, 59)
            self.loc.save(update_fields=["stamp_open_time", "stamp_close_time"])
            resp = self.client.post(f"/stamp/{self.loc.qr_stamp_token}/scan/")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(StampLog.objects.count(), 1)


class FriendsAggregationTests(TestCase):
    """F2：友だち集計がN+1なし・来店数が特典数で水増しされない。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="集計店", type="A", price_type="A",
                                                stamp_enabled=True)
        self.member = LineMember.objects.create(line_user_id="U_agg", name="集計太郎")

    def test_visits_not_inflated_by_rewards(self):
        from stamps.manage_views import _friend_rows
        base = _dt(2026, 3, 1, 12, 0)
        for i in range(10):                      # 10来店＝特典2個(5,10pt)獲得
            services.award_stamp(self.member, self.loc, now=base + timedelta(days=i))
        rows = _friend_rows((base + timedelta(days=11)).date())
        row = next(r for r in rows if r["m"].id == self.member.id)
        self.assertEqual(row["visits"], 10)      # 特典数で水増しされない
        self.assertEqual(row["earned"], 2)
        self.assertEqual(row["pt"], 10)
        self.assertEqual(row["fav_store"], "集計店")


class RewardFlexibilityTests(TestCase):
    """特典設定の自由度：動的cap・段階追加/削除・運用設定。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="店", type="A", price_type="A",
                                                stamp_enabled=True)
        User = get_user_model()
        self.staff = User.objects.create_user(username="s2", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_effective_cap_follows_max_active_tier(self):
        self.assertEqual(RewardTier.effective_cap(), 20)
        RewardTier.objects.create(threshold_pt=30, kind=RewardTier.KIND_FREE, label="30pt", active=True)
        RewardTier.sync_cap_flag()
        self.assertEqual(RewardTier.effective_cap(), 30)          # カードが伸びる
        self.assertTrue(RewardTier.objects.get(threshold_pt=30).is_cap)   # 打ち止めが移動
        self.assertFalse(RewardTier.objects.get(threshold_pt=20).is_cap)

    def test_card_view_state_slots_match_cap(self):
        RewardTier.objects.filter(threshold_pt=20).update(active=False)   # 最大を10ptに
        RewardTier.sync_cap_flag()
        state = services.card_view_state(None)
        self.assertEqual(state["cap"], 10)
        self.assertEqual(len(state["slots"]), 10)

    def test_add_tier_via_screen(self):
        resp = self.client.post("/stamp/manage/rewards/", {
            "action": "add", "threshold_pt": "3", "label": "30円引き",
            "kind": RewardTier.KIND_DISCOUNT, "discount_yen": "30",
            "benefit_cost": "30", "active": "on"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(RewardTier.objects.filter(threshold_pt=3, discount_yen=30).exists())

    def test_edit_threshold_pt_via_screen(self):
        tier5 = RewardTier.objects.get(threshold_pt=5)
        self.client.post("/stamp/manage/rewards/", {
            "action": "save", "tier_id": tier5.id, "threshold_pt": "4",
            "label": tier5.label, "kind": tier5.kind, "discount_yen": "50",
            "benefit_cost": "50", "active": "on"})
        tier5.refresh_from_db()
        self.assertEqual(tier5.threshold_pt, 4)

    def test_delete_tier_blocked_when_issued(self):
        member = LineMember.objects.create(line_user_id="U_d", name="D")
        base = _dt(2026, 4, 1, 12, 0)
        for i in range(5):
            services.award_stamp(member, self.loc, now=base + timedelta(days=i))
        tier5 = RewardTier.objects.get(threshold_pt=5)
        self.client.post("/stamp/manage/rewards/", {"action": "delete", "tier_id": tier5.id})
        self.assertTrue(RewardTier.objects.filter(threshold_pt=5).exists())   # 発行済みで保護

    def test_delete_tier_ok_when_unused(self):
        RewardTier.objects.create(threshold_pt=7, kind=RewardTier.KIND_DISCOUNT,
                                  label="70円引き", discount_yen=70, active=True)
        tier7 = RewardTier.objects.get(threshold_pt=7)
        self.client.post("/stamp/manage/rewards/", {"action": "delete", "tier_id": tier7.id})
        self.assertFalse(RewardTier.objects.filter(threshold_pt=7).exists())

    def test_config_change_affects_new_card(self):
        self.client.post("/stamp/manage/rewards/", {
            "action": "config", "card_validity_days": "14",
            "reward_validity_days": "7", "reward_starts_next_day": ""})
        cfg = StampConfig.get_solo()
        self.assertEqual(cfg.card_validity_days, 14)
        self.assertFalse(cfg.reward_starts_next_day)
        member = LineMember.objects.create(line_user_id="U_c", name="C")
        base = _dt(2026, 4, 1, 12, 0)
        r = services.award_stamp(member, self.loc, now=base)
        self.assertEqual(r.card.expires_on, base.date() + timedelta(days=14))


class CouponAccessTests(TestCase):
    """S1：クーポン閲覧のIDOR防御が本番で常時効く。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="店", type="A", price_type="A")
        self.member = LineMember.objects.create(line_user_id="U_own", name="所有者")

    def _make_reward(self):
        base = _dt(2026, 3, 1, 12, 0)
        for i in range(5):
            services.award_stamp(self.member, self.loc, now=base + timedelta(days=i))
        return self.member.stamp_cards.first().rewards.get(threshold_pt=5)

    def test_coupon_detail_denies_non_owner_in_prod(self):
        reward = self._make_reward()
        with self.settings(STAMP_REQUIRE_LINE=True):
            resp = self.client.get(f"/stamp/reward/{reward.id}/")
        self.assertEqual(resp.status_code, 403)  # 本人特定できない第三者は弾く
