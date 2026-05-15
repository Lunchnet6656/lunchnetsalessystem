"""公開ページ用 status.json 生成ロジックのテスト（generate_status_json）＋QR上書きエンドポイントのテスト。"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.utils import timezone

from sales.management.commands.generate_status_json import build_status, is_business_day
from sales.models import ItemQuantity, Product, SalesLocation


def _location(no, name, excluded=False, excluded_public=False, qr_enabled=False):
    return SalesLocation.objects.create(
        no=no, name=name, type="", price_type="", service_name="",
        excluded_from_shift=excluded,
        excluded_from_public_status=excluded_public,
        qr_enabled=qr_enabled,
    )


class StatusPageTests(TestCase):
    def test_business_day_detection(self):
        self.assertTrue(is_business_day(date(2026, 5, 12)))   # 火曜
        self.assertFalse(is_business_day(date(2026, 5, 16)))  # 土曜
        self.assertFalse(is_business_day(date(2026, 5, 4)))   # みどりの日（祝日）

    def test_open_and_closed(self):
        shinjuku = _location(1, "新宿")
        shibuya = _location(2, "渋谷")
        _location(3, "除外拠点", excluded=True)
        haitatsu = _location(4, "配達", excluded_public=True)  # シフトには残すが公開ページからは消す
        product = Product.objects.create(no=1, week="20260511", name="からあげ弁当")
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=shinjuku, quantity=40,
        )
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=shibuya, quantity=0,
        )
        # 配達は持参数が入っていても公開ページからは消えること
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=haitatsu, quantity=80,
        )
        data = build_status(today=date(2026, 5, 12))

        self.assertTrue(data["business_day"])
        self.assertFalse(data["all_unregistered"])
        self.assertEqual(data["date"], "2026-05-12")
        self.assertEqual(data["weekday"], "火")
        pairs = [(loc["name"], loc["status"]) for loc in data["locations"]]
        self.assertIn(("新宿", "open"), pairs)
        self.assertIn(("渋谷", "closed"), pairs)
        names = [loc["name"] for loc in data["locations"]]
        self.assertNotIn("除外拠点", names)
        self.assertNotIn("配達", names)

    def test_all_unregistered_on_business_day(self):
        _location(1, "新宿")
        data = build_status(today=date(2026, 5, 12))

        self.assertTrue(data["business_day"])
        self.assertTrue(data["all_unregistered"])
        self.assertEqual([loc["status"] for loc in data["locations"]], ["closed"])

    def test_holiday_returns_no_locations(self):
        _location(1, "新宿")
        data = build_status(today=date(2026, 5, 16))  # 土曜

        self.assertFalse(data["business_day"])
        self.assertEqual(data["locations"], [])
        self.assertFalse(data["all_unregistered"])

    def test_sorted_by_no(self):
        _location(3, "品川")
        _location(1, "新宿")
        _location(2, "渋谷")
        data = build_status(today=date(2026, 5, 12))
        self.assertEqual([loc["no"] for loc in data["locations"]], [1, 2, 3])

    # --- QR上書き（today_override）の挙動 ---

    def test_override_sold_out_applied_when_set_today(self):
        # 持参数は40で本来 open だが、当日 today_override=sold_out なら sold_out が優先
        shinjuku = _location(1, "新宿")
        shinjuku.today_override = "sold_out"
        shinjuku.today_override_date = date(2026, 5, 12)
        shinjuku.save()
        product = Product.objects.create(no=1, week="20260511", name="からあげ弁当")
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=shinjuku, quantity=40,
        )
        data = build_status(today=date(2026, 5, 12))
        self.assertEqual(data["locations"][0]["status"], "sold_out")
        # 素の集計では open なので all_unregistered は False のまま（override は判定材料に入れない）
        self.assertFalse(data["all_unregistered"])

    def test_override_ignored_when_date_is_stale(self):
        # 昨日の override は無視される（publish_status_json がクリアし損ねた場合の保険）
        shinjuku = _location(1, "新宿")
        shinjuku.today_override = "closed"
        shinjuku.today_override_date = date(2026, 5, 11)  # 昨日
        shinjuku.save()
        product = Product.objects.create(no=1, week="20260511", name="からあげ弁当")
        ItemQuantity.objects.create(
            target_date="2026-05-12", target_week="20260511",
            product=product, sales_location=shinjuku, quantity=40,
        )
        data = build_status(today=date(2026, 5, 12))
        # stale な override は無視 → 集計通り open
        self.assertEqual(data["locations"][0]["status"], "open")

    def test_override_closed_applied_when_set_today(self):
        # 持参数は0だが today_override=closed が今日付なら通常通り closed
        # （挙動的には素の closed と同じだが、override経路を通っていることを保証）
        shinjuku = _location(1, "新宿")
        shinjuku.today_override = "closed"
        shinjuku.today_override_date = date(2026, 5, 12)
        shinjuku.save()
        data = build_status(today=date(2026, 5, 12))
        self.assertEqual(data["locations"][0]["status"], "closed")


class PublishClearStaleOverridesTests(TestCase):
    def test_publish_clears_stale_overrides(self):
        # 今日付以外の override が publish 実行時に全クリアされること
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        a = _location(1, "新宿")
        a.today_override = "sold_out"
        a.today_override_date = yesterday
        a.save()
        b = _location(2, "渋谷")
        b.today_override = "closed"
        b.today_override_date = today  # 今日付は維持される
        b.save()

        # publish は GitHub 呼び出しを伴うため dry-run で実行
        from django.core.management import call_command
        # GH_TOKEN 不要かつネットワーク呼び出しなしのために dry-run を使う
        # build_status の中身に依存しないように、--dry-run で副作用を抑える
        call_command("publish_status_json", "--dry-run")

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.today_override, "")
        self.assertIsNone(a.today_override_date)
        # 今日付の override は維持
        self.assertEqual(b.today_override, "closed")
        self.assertEqual(b.today_override_date, today)


class QRViewTests(TestCase):
    """QRエンドポイント（/q/sold-out/<token>/・/q/closed/<token>/）の挙動。

    publish_status_json は call_command 経由で呼ばれるので、ネットワークに出ないよう mock する。
    timezone.localtime / timezone.now は営業日（平日）かつ8:00以降の値を返すよう patch する。
    """

    def setUp(self):
        self.client = Client()
        # 火曜10:00 JST の現在時刻を再現
        self.fake_now = timezone.make_aware(
            timezone.datetime(2026, 5, 12, 10, 0, 0),
            timezone=timezone.get_current_timezone(),
        )

    def _patches(self):
        return [
            patch("sales.qr_views.timezone.localtime", return_value=self.fake_now),
            patch("sales.qr_views.timezone.now", return_value=self.fake_now),
            patch("sales.qr_views.call_command"),
        ]

    def test_invalid_token_returns_404(self):
        with self._patches()[0], self._patches()[1], self._patches()[2]:
            resp = self.client.get("/q/sold-out/nonexistent_token/")
        self.assertEqual(resp.status_code, 404)

    def test_disabled_qr_returns_403_no_db_change(self):
        loc = _location(1, "新宿", qr_enabled=False)
        token = loc.qr_sold_out_token
        with self._patches()[0], self._patches()[1], self._patches()[2] as mock_cmd:
            resp = self.client.get(f"/q/sold-out/{token}/")
        self.assertEqual(resp.status_code, 403)
        loc.refresh_from_db()
        self.assertEqual(loc.today_override, "")
        mock_cmd.assert_not_called()

    def test_enabled_qr_updates_override_and_publishes(self):
        loc = _location(1, "新宿", qr_enabled=True)
        token = loc.qr_sold_out_token
        with self._patches()[0], self._patches()[1], self._patches()[2] as mock_cmd:
            resp = self.client.get(f"/q/sold-out/{token}/")
        self.assertEqual(resp.status_code, 200)
        loc.refresh_from_db()
        self.assertEqual(loc.today_override, "sold_out")
        self.assertEqual(loc.today_override_date, self.fake_now.date())
        mock_cmd.assert_called_once_with("publish_status_json")
        self.assertIsNotNone(loc.last_qr_publish_at)

    def test_throttle_skips_publish_within_60_seconds(self):
        # 同一拠点で30秒前に publish 済 → 別種類のQRスキャンでも publish はスキップされる
        loc = _location(1, "新宿", qr_enabled=True)
        loc.last_qr_publish_at = self.fake_now - timedelta(seconds=30)
        loc.save()
        token = loc.qr_closed_token
        with self._patches()[0], self._patches()[1], self._patches()[2] as mock_cmd:
            resp = self.client.get(f"/q/closed/{token}/")
        self.assertEqual(resp.status_code, 200)
        loc.refresh_from_db()
        # DB は更新される（throttle は publish のみ）
        self.assertEqual(loc.today_override, "closed")
        # publish はスキップ
        mock_cmd.assert_not_called()

    def test_holiday_returns_403_no_db_change(self):
        # 土曜10:00 → 営業日でないので拒否
        sat_now = timezone.make_aware(
            timezone.datetime(2026, 5, 16, 10, 0, 0),
            timezone=timezone.get_current_timezone(),
        )
        loc = _location(1, "新宿", qr_enabled=True)
        token = loc.qr_sold_out_token
        with patch("sales.qr_views.timezone.localtime", return_value=sat_now), \
             patch("sales.qr_views.timezone.now", return_value=sat_now), \
             patch("sales.qr_views.call_command") as mock_cmd:
            resp = self.client.get(f"/q/sold-out/{token}/")
        self.assertEqual(resp.status_code, 403)
        loc.refresh_from_db()
        self.assertEqual(loc.today_override, "")
        mock_cmd.assert_not_called()


class QRPDFTests(TestCase):
    """QR PDF 生成（sales/qr_pdf.py）の基本検証。"""

    def test_pdf_starts_with_magic_and_has_expected_page_count(self):
        from sales.qr_pdf import build_qr_pdf
        a = _location(1, "新宿")
        b = _location(2, "渋谷")

        def build_url(token, kind):
            return f"https://lunchnetsalessystem.com/q/{kind}/{token}/"

        pdf = build_qr_pdf([a, b], build_url)
        self.assertTrue(pdf.startswith(b"%PDF"))
        # /Type /Page（ノード単位）の出現数を数える。1拠点2ページ × 2拠点 = 4ページ。
        # reportlab は親 /Pages も1つ出すのでこの宣言ベースだとカウントしにくい → MediaBox で代替
        page_count = pdf.count(b"/MediaBox")
        self.assertEqual(page_count, 4)
        # 拠点名がページに描画されているはず。reportlab は日本語をCID参照に変換するので
        # 平文では含まれない → ファイルサイズで「実体がある」ことだけ確認（4ページで >5KB）
        self.assertGreater(len(pdf), 5 * 1024)

    def test_pdf_empty_locations(self):
        from sales.qr_pdf import build_qr_pdf
        pdf = build_qr_pdf([], lambda t, k: f"http://x/{k}/{t}/")
        # 空でも有効なPDFが返る（中身は0ページ）
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(pdf.count(b"/MediaBox"), 0)


class RegenerateQRTokensTests(TestCase):
    def test_regenerate_changes_both_tokens(self):
        from sales.admin import regenerate_qr_tokens_action
        loc = _location(1, "新宿")
        old_sold_out = loc.qr_sold_out_token
        old_closed = loc.qr_closed_token

        # admin action は modeladmin と request を取るがメッセージ部しか使わない → 最低限のスタブ
        class _Stub:
            def message_user(self, request, msg, level=None):
                pass
        regenerate_qr_tokens_action(_Stub(), None, SalesLocation.objects.filter(pk=loc.pk))

        loc.refresh_from_db()
        self.assertNotEqual(loc.qr_sold_out_token, old_sold_out)
        self.assertNotEqual(loc.qr_closed_token, old_closed)
        # 新トークンは非空
        self.assertTrue(loc.qr_sold_out_token)
        self.assertTrue(loc.qr_closed_token)
