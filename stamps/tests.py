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
        # 基礎メカニクス（1来店＝1pt）を検証するため、2倍イベントはOFFに固定する。
        cfg = StampConfig.get_solo()
        cfg.streak_bonus_enabled = False
        cfg.save()
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


class StampBonusTests(TestCase):
    """スタンプ2倍イベント（雨の日・連続来店）の判定。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="テスト本店", type="A", price_type="A")
        self.member = LineMember.objects.create(line_user_id="U_bonus_1", name="ボーナス太郎")

    def _award_day(self, dd, hh=12):
        return services.award_stamp(self.member, self.loc, now=_dt(2026, 7, dd, hh, 0))

    def _set_config(self, **kw):
        cfg = StampConfig.get_solo()
        for k, v in kw.items():
            setattr(cfg, k, v)
        cfg.save()
        return cfg

    # --- 雨の日 -----------------------------------------------------------
    def test_rain_bonus_doubles(self):
        self._set_config(streak_bonus_enabled=False, rain_bonus_date=_dt(2026, 7, 1, 0, 0).date())
        r = self._award_day(1)
        self.assertTrue(r.ok)
        self.assertTrue(r.doubled)
        self.assertEqual(r.points, 2)
        self.assertEqual(r.bonus_reason, "rain")
        self.assertEqual(r.card.stamp_count, 2)
        log = StampLog.objects.get()
        self.assertEqual(log.points, 2)
        self.assertEqual(log.bonus_reason, "rain")

    def test_rain_bonus_only_that_day(self):
        self._set_config(streak_bonus_enabled=False, rain_bonus_date=_dt(2026, 7, 1, 0, 0).date())
        self.assertTrue(self._award_day(1).doubled)   # 7/1 は2倍
        r2 = self._award_day(2)                        # 7/2 は通常（対象日は7/1のみ）
        self.assertFalse(r2.doubled)
        self.assertEqual(r2.points, 1)

    # --- 連続来店 ---------------------------------------------------------
    def test_streak_doubles_on_third_day_only(self):
        # 既定：3日連続の節目（3日目）で2倍。
        r1 = self._award_day(1); r2 = self._award_day(2); r3 = self._award_day(3)
        self.assertFalse(r1.doubled)
        self.assertFalse(r2.doubled)
        self.assertTrue(r3.doubled)                    # 3日目＝節目
        self.assertEqual(r3.bonus_reason, "streak")
        self.assertEqual(r3.card.stamp_count, 4)       # 1+1+2
        r4 = self._award_day(4)
        self.assertFalse(r4.doubled)                   # 4日目は据え置き（節目でない）
        r5 = self._award_day(5)
        r6 = self._award_day(6)
        self.assertTrue(r6.doubled)                    # 6日目＝次の節目
        self.assertEqual(r6.card.stamp_count, 8)       # 4,5,6,8

    def test_streak_resets_after_gap(self):
        self._award_day(1); self._award_day(2)         # 2日連続
        # 7/3 を飛ばす → 連続が途切れる
        r4 = self._award_day(4)
        self.assertFalse(r4.doubled)                   # 途切れ後の1日目
        self._award_day(5)
        r6 = self._award_day(6)
        self.assertTrue(r6.doubled)                    # 4,5,6で3日連続＝節目

    def test_streak_can_be_disabled(self):
        self._set_config(streak_bonus_enabled=False)
        self._award_day(1); self._award_day(2)
        r3 = self._award_day(3)
        self.assertFalse(r3.doubled)

    # --- 重複・上限・閾値跨ぎ ---------------------------------------------
    def test_rain_and_streak_still_max_double(self):
        # 3日連続の節目かつ雨の日 → 最大2倍で据え置き（3倍にしない）。
        self._set_config(rain_bonus_date=_dt(2026, 7, 3, 0, 0).date())
        self._award_day(1); self._award_day(2)
        r3 = self._award_day(3)
        self.assertEqual(r3.points, 2)                 # 3倍にはならない
        self.assertEqual(r3.card.stamp_count, 4)

    def test_reward_issued_when_double_crosses_threshold(self):
        # 連続OFF・4個まで貯める → 5個目手前(4)で雨の日2倍 → 5ptを跨いで特典発行。
        self._set_config(streak_bonus_enabled=False)
        for d in range(1, 5):                           # 7/1..7/4 で4個
            self._award_day(d)
        self._set_config(streak_bonus_enabled=False, rain_bonus_date=_dt(2026, 7, 5, 0, 0).date())
        r = self._award_day(5)
        self.assertEqual(r.card.stamp_count, 6)         # 4 → 6（5を跨ぐ）
        self.assertIsNotNone(r.new_reward)
        self.assertEqual(r.new_reward.threshold_pt, 5)
        self.assertEqual(Reward.objects.filter(threshold_pt=5).count(), 1)

    def test_double_clamped_at_cap(self):
        # 19個まで貯めてから雨の日2倍 → 20で打ち止め（21にしない）。
        self._set_config(streak_bonus_enabled=False)
        for d in range(1, 20):                           # 7/1..7/19 で19個
            self._award_day(d)
        self._set_config(streak_bonus_enabled=False, rain_bonus_date=_dt(2026, 7, 20, 0, 0).date())
        r = self._award_day(20)
        self.assertEqual(r.card.stamp_count, CAP_PT)     # 20で頭打ち
        self.assertEqual(r.points, 1)                    # 2倍でも1しか入らない
        log = StampLog.objects.get(stamped_on=_dt(2026, 7, 20, 0, 0).date())
        self.assertEqual(log.points, 1)
        self.assertEqual(log.bonus_reason, "")           # 実質1個なので理由は付けない
        self.assertEqual(r.card.status, StampCard.STATUS_COMPLETED)


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
            self.assertEqual(resp.status_code, 303)          # PRG：トークン無しカードへ
            self.assertIn("/stamp/me/", resp["Location"])
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

    def _open_all_day(self):
        self.loc.stamp_open_time = time(0, 0)
        self.loc.stamp_close_time = time(23, 59)
        self.loc.save(update_fields=["stamp_open_time", "stamp_close_time"])

    def test_scan_dev_fallback_awards(self):
        # STAMP_REQUIRE_LINE=False（dev）＝トークン無しでも仮会員で押せる
        with self.settings(STAMP_REQUIRE_LINE=False):
            self._open_all_day()
            resp = self.client.post(f"/stamp/{self.loc.qr_stamp_token}/scan/")
            self.assertEqual(resp.status_code, 303)          # PRG リダイレクト
            self.assertEqual(StampLog.objects.count(), 1)

    def test_scan_redirects_to_tokenless_card(self):
        # PRG：押印後の最終URLに店舗トークンを残さない（復元/リロードでの再押印を防ぐ）
        with self.settings(STAMP_REQUIRE_LINE=False):
            self._open_all_day()
            resp = self.client.post(f"/stamp/{self.loc.qr_stamp_token}/scan/")
            self.assertEqual(resp.status_code, 303)
            self.assertNotIn(self.loc.qr_stamp_token, resp["Location"])
            self.assertIn("/stamp/me/", resp["Location"])

    def test_prg_flash_shown_once_on_card(self):
        # 押印の演出はリダイレクト先カードで1回だけ出て、次に開くと出ない（＆再表示で増えない）
        with self.settings(STAMP_REQUIRE_LINE=False):
            self._open_all_day()
            self.client.post(f"/stamp/{self.loc.qr_stamp_token}/scan/")   # 押印→session flash
            r1 = self.client.post("/stamp/me/")
            self.assertContains(r1, "スタンプを押しました")
            r2 = self.client.post("/stamp/me/")
            self.assertNotContains(r2, "スタンプを押しました")
            self.assertEqual(StampLog.objects.count(), 1)


class FriendsAggregationTests(TestCase):
    """F2：友だち集計がN+1なし・来店数が特典数で水増しされない。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        # 集計の基礎（1来店＝1pt）を見るため、2倍イベントはOFFに固定する。
        cfg = StampConfig.get_solo()
        cfg.streak_bonus_enabled = False
        cfg.save()
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


class SalesCorrelationTests(TestCase):
    """売上×スタンプ相関：DailyReport と StampLog の突き合わせ。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="スタンプ店", type="A",
                                                price_type="A", stamp_enabled=True)
        self.other = SalesLocation.objects.create(no=2, name="非対応店", type="A",
                                                  price_type="A", stamp_enabled=False)
        User = get_user_model()
        self.staff = User.objects.create_user(username="sc", password="x", is_staff=True)
        self.client.force_login(self.staff)
        self.day = timezone.localdate()

    def _report(self, loc_no, location, brought, sold, remaining, revenue, date=None):
        from sales.models import DailyReport
        return DailyReport.objects.create(
            date=date or self.day, location=location, location_no=loc_no,
            total_quantity=brought, total_sales_quantity=sold,
            total_remaining=remaining, total_revenue=revenue)

    def _stamp(self, name, loc):
        m = LineMember.objects.create(line_user_id=f"U_{name}", name=name)
        services.award_stamp(m, loc, now=_dt(self.day.year, self.day.month, self.day.day, 12, 0))
        return m

    def _get(self, **params):
        params.setdefault("from", self.day.isoformat())
        params.setdefault("to", self.day.isoformat())
        return self.client.get("/stamp/manage/sales-correlation/", params)

    def test_row_metrics(self):
        from stamps.manage_views import _sales_correlation_rows
        self._report(1, "スタンプ店", brought=100, sold=80, remaining=20, revenue=50000)
        self._stamp("a", self.loc)
        self._stamp("b", self.loc)
        rows = _sales_correlation_rows(self.day, self.day)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["brought"], r["sold"], r["remaining"]), (100, 80, 20))
        self.assertEqual(r["waste_rate"], 20.0)      # 20/100
        self.assertEqual(r["stamp_users"], 2)        # a,b の distinct
        self.assertEqual(r["stamp_rate"], 2.5)       # 2/80
        self.assertEqual(r["revenue"], 50000)

    def test_stamp_only_toggle(self):
        self._report(2, "非対応店", brought=50, sold=40, remaining=10, revenue=20000)
        # 既定（スタンプ対応店のみON）＝非対応店の行は出ない（0件）。
        self.assertEqual(self._get().context["total"], 0)
        # OFF にすると行に出る。
        locs = [r["location"] for r in self._get(stamp_only="0").context["rows"]]
        self.assertIn("非対応店", locs)

    def test_zero_division_shows_dash(self):
        from stamps.manage_views import _sales_correlation_rows
        self._report(1, "スタンプ店", brought=0, sold=0, remaining=0, revenue=0)
        r = _sales_correlation_rows(self.day, self.day)[0]
        self.assertIsNone(r["waste_rate"])           # 持参0 → 廃棄率なし
        self.assertIsNone(r["stamp_rate"])           # 販売0 → 利用割合なし

    def test_unmatched_location_no_zero(self):
        # location_no=0（旧データ）はスタンプ0で落ちない。
        from stamps.manage_views import _sales_correlation_rows
        self._report(0, "旧データ店", brought=30, sold=30, remaining=0, revenue=9000)
        rows = _sales_correlation_rows(self.day, self.day, stamp_only=False)
        r = [x for x in rows if x["location"] == "旧データ店"][0]
        self.assertEqual(r["stamp_users"], 0)

    def test_csv_export(self):
        self._report(1, "スタンプ店", brought=100, sold=80, remaining=20, revenue=50000)
        resp = self._get(export="csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_page_opens(self):
        self.assertEqual(self._get().status_code, 200)


class FriendInsightModelTests(TestCase):
    """友だち統計スナップショットの派生値。"""
    def _snap(self, **kw):
        from stamps.models import FriendInsightSnapshot
        base = dict(date=timezone.localdate(), status="ready",
                    followers=100, targeted_reaches=80, blocks=20,
                    fetched_at=timezone.now())
        base.update(kw)
        return FriendInsightSnapshot.objects.create(**base)

    def test_effective_friends_prefers_targeted_reaches(self):
        s = self._snap(targeted_reaches=80, followers=100, blocks=20)
        self.assertEqual(s.effective_friends, 80)

    def test_effective_friends_fallback(self):
        # targeted_reaches が0（少人数で欠落）なら followers-blocks で代替。
        s = self._snap(targeted_reaches=0, followers=100, blocks=30)
        self.assertEqual(s.effective_friends, 70)

    def test_block_rate(self):
        s = self._snap(followers=200, blocks=50)
        self.assertEqual(s.block_rate, 25.0)

    def test_latest_ready_skips_unready(self):
        from stamps.models import FriendInsightSnapshot
        today = timezone.localdate()
        self._snap(date=today - timedelta(days=2), status="ready", followers=90)
        self._snap(date=today - timedelta(days=1), status="unready", followers=999)
        latest = FriendInsightSnapshot.latest_ready(on_or_before=today)
        self.assertEqual(latest.followers, 90)  # unready は母数に採用しない


class FriendInsightServiceTests(TestCase):
    """LINE Insight 取得（requestsをモック）と保存。"""
    def _resp(self, payload, status_code=200):
        from unittest import mock
        m = mock.Mock()
        m.status_code = status_code
        m.json.return_value = payload
        m.text = str(payload)
        return m

    def test_fetch_parses_ready(self):
        from unittest import mock
        from stamps import line_insight
        payload = {"status": "ready", "followers": 500, "targetedReaches": 420, "blocks": 30}
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.line_insight.requests.get", return_value=self._resp(payload)):
            d = line_insight.fetch_friend_insight(timezone.localdate())
        self.assertEqual((d["status"], d["followers"], d["targeted_reaches"], d["blocks"]),
                         ("ready", 500, 420, 30))

    def test_fetch_tolerates_missing_fields(self):
        from unittest import mock
        from stamps import line_insight
        # 少人数で targetedReaches / blocks が欠落しても落ちない。
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.line_insight.requests.get",
                        return_value=self._resp({"status": "ready", "followers": 12})):
            d = line_insight.fetch_friend_insight(timezone.localdate())
        self.assertEqual((d["followers"], d["targeted_reaches"], d["blocks"]), (12, 0, 0))

    def test_fetch_requires_token(self):
        from unittest import mock
        from stamps import line_insight
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value=""):
            with self.assertRaises(line_insight.InsightError):
                line_insight.fetch_friend_insight(timezone.localdate())

    def test_save_upserts(self):
        from unittest import mock
        from stamps import line_insight
        from stamps.models import FriendInsightSnapshot
        day = timezone.localdate()
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.line_insight.requests.get",
                        return_value=self._resp({"status": "ready", "followers": 100,
                                                 "targetedReaches": 80, "blocks": 20})):
            line_insight.save_friend_insight(day)
            # 同じ日を再取得しても1行のまま更新（重複しない）。
            with mock.patch("stamps.line_insight.requests.get",
                            return_value=self._resp({"status": "ready", "followers": 110,
                                                     "targetedReaches": 88, "blocks": 22})):
                line_insight.save_friend_insight(day)
        self.assertEqual(FriendInsightSnapshot.objects.filter(date=day).count(), 1)
        self.assertEqual(FriendInsightSnapshot.objects.get(date=day).followers, 110)


class DashboardFriendBaseTests(TestCase):
    """ダッシュボードの参加率が全友だち（実質有効）ベースになっているか。"""
    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="店", type="A", price_type="A",
                                                stamp_enabled=True)
        User = get_user_model()
        self.staff = User.objects.create_user(username="d1", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def _snap(self, day, **kw):
        from stamps.models import FriendInsightSnapshot
        base = dict(date=day, status="ready", followers=100, targeted_reaches=50,
                    blocks=10, fetched_at=timezone.now())
        base.update(kw)
        return FriendInsightSnapshot.objects.create(**base)

    def test_participation_rate_uses_effective_friends(self):
        # 実質有効友だち50・参加者1 → 2.0%（登録者ベースなら100%になる）。
        member = LineMember.objects.create(line_user_id="U1", name="来店者")
        services.award_stamp(member, self.loc,
                             now=timezone.make_aware(timezone.datetime(2026, 7, 20, 12, 0)))
        # スナップショットは要求期間内の固定日に置く（実行日に依存しない＝毎日安定して通す）。
        self._snap(_dt(2026, 8, 1, 0, 0).date(), targeted_reaches=50)
        resp = self.client.get("/stamp/manage/?from=2026-07-01&to=2026-08-01")
        self.assertContains(resp, "全友だち50人")
        self.assertContains(resp, "2.0%")

    def test_waiting_when_no_snapshot(self):
        resp = self.client.get("/stamp/manage/")
        self.assertContains(resp, "集計待ち")  # スナップショット未取得なら参加率は待ち表示

    def test_combo_chart_renders_line_and_bars(self):
        # 2軸コンボ：友だち折れ線（左軸）＋日次増減の棒（右軸）が1つのチャートに出る。
        today = timezone.localdate()
        self._snap(today - timedelta(days=1), targeted_reaches=48, blocks=8)
        self._snap(today, targeted_reaches=52, blocks=10)
        resp = self.client.get(f"/stamp/manage/?from={today - timedelta(days=5)}&to={today}")
        self.assertContains(resp, "<polyline")        # 友だち折れ線
        self.assertContains(resp, "<rect")            # 日次増減の棒
        self.assertContains(resp, "実質有効友だち（左軸）")
        self.assertContains(resp, "新規ブロック（右軸）")

    def test_daily_delta_merged_into_trend(self):
        # 日次増減がコンボの棒として出る（ブロックのスパイクがツールチップに）。
        today = timezone.localdate()
        self._snap(today - timedelta(days=2), followers=100, blocks=10)
        self._snap(today - timedelta(days=1), followers=105, blocks=24)  # 新規ブロック+14の山
        self._snap(today, followers=108, blocks=25)
        resp = self.client.get(f"/stamp/manage/?from={today - timedelta(days=5)}&to={today}")
        self.assertContains(resp, "新規ブロック14")   # スパイクがツールチップに出る
        self.assertNotContains(resp, "日次の増減（")   # 別グラフの見出しは無くなった

    def test_delta_uses_prev_snapshot_before_range(self):
        # 期間開始前の直近スナップショットを使って初日も差分が出る。
        from stamps.manage_views import _friend_delta_rows
        from stamps.models import FriendInsightSnapshot
        today = timezone.localdate()
        prev = self._snap(today - timedelta(days=3), followers=90, blocks=5)
        s1 = self._snap(today - timedelta(days=2), followers=100, blocks=8)
        s2 = self._snap(today - timedelta(days=1), followers=103, blocks=8)
        rows = _friend_delta_rows([s1, s2], prev)
        self.assertEqual(len(rows), 2)              # prev利用で初日も差分あり
        self.assertEqual(rows[0]["adds"], 10)       # 100-90
        self.assertEqual(rows[0]["blocks"], 3)      # 8-5


class FetchFriendInsightCommandTests(TestCase):
    """管理コマンド fetch_friend_insight。"""
    def test_dry_run_does_not_save(self):
        from unittest import mock
        from django.core.management import call_command
        from stamps.models import FriendInsightSnapshot
        payload = {"status": "ready", "followers": 300, "targetedReaches": 250, "blocks": 20}
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.line_insight.requests.get") as g:
            g.return_value.status_code = 200
            g.return_value.json.return_value = payload
            call_command("fetch_friend_insight", "--date", "2026-07-27", "--dry-run")
        self.assertEqual(FriendInsightSnapshot.objects.count(), 0)

    def test_saves_specified_date(self):
        from unittest import mock
        from django.core.management import call_command
        from stamps.models import FriendInsightSnapshot
        payload = {"status": "ready", "followers": 300, "targetedReaches": 250, "blocks": 20}
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.line_insight.requests.get") as g:
            g.return_value.status_code = 200
            g.return_value.json.return_value = payload
            call_command("fetch_friend_insight", "--date", "2026-07-27")
        s = FriendInsightSnapshot.objects.get()
        self.assertEqual((str(s.date), s.followers, s.targeted_reaches), ("2026-07-27", 300, 250))

    def test_days_backfill(self):
        from unittest import mock
        from django.core.management import call_command
        from stamps.models import FriendInsightSnapshot
        payload = {"status": "ready", "followers": 300, "targetedReaches": 250, "blocks": 20}
        with mock.patch("stamps.line_insight.line_richmenu._token", return_value="tok"), \
             mock.patch("stamps.line_insight.requests.get") as g:
            g.return_value.status_code = 200
            g.return_value.json.return_value = payload
            call_command("fetch_friend_insight", "--days", "30")
        self.assertEqual(FriendInsightSnapshot.objects.count(), 30)  # 30日ぶん保存


class CrmAnalyticsTests(TestCase):
    """CRM分析ダッシュボード（第3弾）：会員単位のリピート率・来店間隔・コホート・RFM・言語化。

    仕様: lunchnetsale-スタンプCRM分析ダッシュボード-要件定義.md §5・§9
    """

    def setUp(self):
        RewardTier.ensure_defaults()
        self.loc = SalesLocation.objects.create(no=1, name="CRM店", type="A", price_type="A",
                                                stamp_enabled=True)
        User = get_user_model()
        self.staff = User.objects.create_user(username="crm", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def _visit(self, member, y, m, d):
        return services.award_stamp(member, self.loc, now=_dt(y, m, d, 12, 0))

    def _seed(self):
        """会員5人・来店を作り込む（期待値が明快になるデータ）。"""
        mk = lambda uid, name: LineMember.objects.create(line_user_id=uid, name=name)
        a, b, c, d, f = (mk("U_a", "Aさん"), mk("U_b", "Bさん"), mk("U_c", "Cさん"),
                         mk("U_d", "Dさん"), mk("U_f", "Fさん"))
        for day in (1, 3, 6):
            self._visit(a, 2026, 7, day)          # A：3回（間隔2,3）
        self._visit(b, 2026, 7, 1)                # B：1回のみ
        self._visit(c, 2026, 7, 2)
        self._visit(c, 2026, 7, 6)                # C：2回（間隔4）
        self._visit(d, 2026, 6, 1)               # D：期間前に来店＝新規ではない
        self._visit(d, 2026, 7, 5)               # D：期間内は1回
        for day in (1, 2, 3, 4, 5):
            self._visit(f, 2026, 7, day)          # F：5回・最終7/5＝要フォロー候補
        return dict(a=a, b=b, c=c, d=d, f=f)

    def _metrics(self):
        from stamps.manage_views import _crm_metrics
        from datetime import date
        return _crm_metrics(date(2026, 7, 1), date(2026, 7, 31))

    def test_repeat_rates(self):
        self._seed()
        m = self._metrics()
        self.assertEqual(m["n_members"], 5)
        self.assertEqual(m["period_rate"], 60.0)      # A,C,F が2回以上 / 5人
        self.assertEqual(m["new_count"], 4)           # D は期間前来店ありで除外
        self.assertEqual(m["new_rate"], 75.0)         # 新規4人中 A,C,F の3人が再来
        self.assertEqual(m["within_obs"], 5)
        self.assertEqual(m["within_rate"], 60.0)      # 14日以内に2回目：A,C,F

    def test_interval(self):
        self._seed()
        m = self._metrics()
        self.assertEqual(m["interval_target"], 3)     # A,C,F（2回以上）
        self.assertEqual(m["interval_single"], 2)     # B,D（1回のみ）
        self.assertIsNotNone(m["interval_median"])
        self.assertEqual(sum(x["count"] for x in m["interval_dist"]), 3)

    def test_rfm_followup(self):
        self._seed()
        m = self._metrics()
        rfm = m["rfm"]
        # 最終来店から21日以上（7/31基準）かつ5回以上＝F さん1人が要フォロー
        self.assertEqual(rfm["followup"], 1)
        total = sum(cell["count"] for row in rfm["rows"] for cell in row["cells"])
        self.assertEqual(total, 5)                    # 全会員がどこかのセルに1人ずつ

    def test_cohort_matrix(self):
        self._seed()
        m = self._metrics()
        coh = m["cohort"]
        self.assertTrue(coh["rows"])                  # 新規4人ぶんのコホートができる
        self.assertEqual(coh["rows"][0]["cells"][0]["pct"], 100.0)  # W0は必ず100%

    def test_new_visit_excluded_from_cohort(self):
        """期間前に来店した会員（D）は当期の新規コホート母数に入らない。"""
        self._seed()
        m = self._metrics()
        cohort_total = sum(r["size"] for r in m["cohort"]["rows"])
        self.assertEqual(cohort_total, 4)             # A,B,C,F のみ（D除外）

    def test_insights_no_causal_claims(self):
        """自動サマリーは記述に徹し、因果（おかげ/効果で/が原因）を主張しない。"""
        from stamps.manage_views import _crm_metrics, _crm_insights
        from datetime import date
        self._seed()
        cur = _crm_metrics(date(2026, 7, 1), date(2026, 7, 31))
        prev = _crm_metrics(date(2026, 6, 1), date(2026, 6, 30), light=True)
        texts = " ".join(i["text"] for i in _crm_insights(cur, prev))
        for banned in ("おかげ", "効果で", "が原因"):
            self.assertNotIn(banned, texts)
        self.assertTrue(_crm_insights(cur, prev))     # 何かしら気づき文が出る

    def test_insights_with_padded_cohort(self):
        """直近1週だけのコホート行（未到来セルでパディング）でも落ちない（本番500の回帰防止）。"""
        from stamps.manage_views import _crm_metrics, _crm_insights
        from datetime import date
        early = LineMember.objects.create(line_user_id="U_e", name="早期")
        self._visit(early, 2026, 6, 1)
        self._visit(early, 2026, 6, 8)               # 幅のあるコホート（複数経過週）
        late = LineMember.objects.create(line_user_id="U_l", name="直近")
        self._visit(late, 2026, 7, 30)               # 最終週に初来店＝row_max=0でパディングされる行
        cur = _crm_metrics(date(2026, 6, 1), date(2026, 7, 31))
        _crm_insights(cur, {})                       # KeyError:'pct' を出さない
        resp = self.client.get(
            "/stamp/manage/analytics/?from=2026-06-01&to=2026-07-31&export=csv")
        self.assertEqual(resp.status_code, 200)      # CSVも空セルで落ちない

    def test_empty_period(self):
        """来店ゼロでも落ちない（率は None、要フォロー0）。"""
        m = self._metrics()
        self.assertEqual(m["n_members"], 0)
        self.assertIsNone(m["period_rate"])
        self.assertEqual(m["rfm"]["followup"], 0)

    def test_dashboard_view_opens_with_data(self):
        self._seed()
        resp = self.client.get("/stamp/manage/analytics/?from=2026-07-01&to=2026-07-31")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "リピート率")
        self.assertContains(resp, "コホート継続")
        self.assertContains(resp, "RFM")

    def test_cohort_month_toggle(self):
        self._seed()
        resp = self.client.get("/stamp/manage/analytics/?from=2026-07-01&to=2026-07-31&cohort=month")
        self.assertEqual(resp.status_code, 200)

    def test_csv_export(self):
        self._seed()
        resp = self.client.get("/stamp/manage/analytics/?from=2026-07-01&to=2026-07-31&export=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        body = resp.content.decode("utf-8-sig")
        self.assertIn("リピート率", body)
        self.assertIn("コホート継続", body)
        self.assertIn("RFM", body)
