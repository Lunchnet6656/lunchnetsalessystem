"""予約システム テスト（S1 モデル／S2・S2.5 ロジック・ビュー）。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md
"""
from datetime import date, datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sales.models import Product, SalesLocation
from reservations import line_api, line_notify, services
from reservations.models import LineMember, Reservation, ReservationItem

User = get_user_model()


def _make_location(**kw):
    defaults = dict(name="テスト拠点", type="テーブル", price_type="A", service_name="-")
    defaults.update(kw)
    return SalesLocation.objects.create(**defaults)


def _aware(y, m, d, hh, mm=0):
    return timezone.make_aware(datetime(y, m, d, hh, mm), timezone.get_current_timezone())


# ===== S1: モデル =====
class SalesLocationReservationFieldsTest(TestCase):
    def test_defaults(self):
        loc = _make_location()
        self.assertFalse(loc.reservation_enabled)
        self.assertEqual(loc.default_product_cap, 10)
        self.assertTrue(loc.qr_reserve_token)

    def test_reserve_token_is_unique(self):
        self.assertNotEqual(_make_location().qr_reserve_token, _make_location().qr_reserve_token)


class LineMemberTest(TestCase):
    def test_create_and_str(self):
        self.assertIn("田中", str(LineMember.objects.create(line_user_id="U1234567890", name="田中")))

    def test_line_user_id_unique(self):
        LineMember.objects.create(line_user_id="Udup", name="A")
        with self.assertRaises(Exception):
            LineMember.objects.create(line_user_id="Udup", name="B")


class ReservationItemTest(TestCase):
    def setUp(self):
        self.loc = _make_location()
        self.member = LineMember.objects.create(line_user_id="U1", name="佐藤")
        self.product = Product.objects.create(week="20260610", name="唐揚げ弁当", price_A=650)
        self.r = Reservation.objects.create(member=self.member, sales_location=self.loc, pickup_date=date(2026, 6, 10))

    def test_quantity_is_sum_of_sizes(self):
        item = ReservationItem.objects.create(
            reservation=self.r, product=self.product,
            quantity_large=1, quantity_regular=2, quantity_small=1,
            unit_price=650, large_surcharge=50,
        )
        self.assertEqual(item.quantity, 4)                 # 合計＝1+2+1
        self.assertEqual(item.product_name, "唐揚げ弁当")    # スナップショット

    def test_subtotal_applies_large_surcharge_only(self):
        item = ReservationItem.objects.create(
            reservation=self.r, product=self.product,
            quantity_large=1, quantity_regular=1, quantity_small=0,
            unit_price=650, large_surcharge=50,
        )
        # 650*2(合計) + 50*1(大盛り) = 1350
        self.assertEqual(item.subtotal, 1350)

    def test_reservation_number_autogen_and_unique(self):
        r2 = Reservation.objects.create(member=self.member, sales_location=self.loc, pickup_date=date(2026, 6, 11))
        self.assertTrue(self.r.reservation_number)
        self.assertNotEqual(self.r.reservation_number, r2.reservation_number)

    def test_is_active(self):
        self.assertTrue(self.r.is_active)
        self.r.status = Reservation.STATUS_CANCELLED
        self.assertFalse(self.r.is_active)


# ===== S2 / S2.5: ドメインロジック =====
class WeekAndCalendarTest(TestCase):
    def test_week_key_is_wednesday_start(self):
        self.assertEqual(services.week_key_for_date(date(2026, 6, 10)), "2026-06-10")  # 水
        self.assertEqual(services.week_key_for_date(date(2026, 6, 16)), "2026-06-10")  # 火＝前週水曜
        self.assertEqual(services.week_key_for_date(date(2026, 6, 8)), "2026-06-03")   # 月＝前週水曜

    def test_default_pickup_is_next_business_day(self):
        self.assertEqual(services.default_pickup_date(date(2026, 6, 12)), date(2026, 6, 15))  # 金→月

    def test_deadline_is_prev_business_day_1600(self):
        dl = services.reservation_deadline(date(2026, 6, 10))
        self.assertEqual((dl.year, dl.month, dl.day, dl.hour), (2026, 6, 9, 16))

    def test_is_open_before_and_after_deadline(self):
        self.assertTrue(services.is_open_for(date(2026, 6, 10), now=_aware(2026, 6, 9, 15)))
        self.assertFalse(services.is_open_for(date(2026, 6, 10), now=_aware(2026, 6, 9, 17)))

    def test_selectable_dates_within_same_week(self):
        # 火6/9 起点 → 翌営業日6/10(水)。その週6/10〜6/16内の営業日が候補
        dates = services.selectable_pickup_dates(date(2026, 6, 9), now=_aware(2026, 6, 9, 10))
        self.assertIn(date(2026, 6, 10), dates)
        self.assertIn(date(2026, 6, 16), dates)   # 火＝週末
        self.assertNotIn(date(2026, 6, 9), dates)  # 当日は対象外
        self.assertTrue(all(services.week_key_for_date(d) == "2026-06-10" for d in dates))


class PriceTest(TestCase):
    def setUp(self):
        self.product = Product.objects.create(week="2026-06-10", name="魚弁当", price_A=650, price_B=700, price_C=600)

    def test_unit_price_follows_price_type(self):
        self.assertEqual(services.unit_price_for(_make_location(price_type="A"), self.product), 650)
        self.assertEqual(services.unit_price_for(_make_location(price_type="B"), self.product), 700)

    def test_large_surcharge_from_oomori_product(self):
        Product.objects.create(week="2026-06-10", name="大盛りごはん", price_A=50)
        self.assertEqual(services.large_surcharge_for(_make_location(price_type="A"), date(2026, 6, 10)), 50)

    def test_large_surcharge_fallback_when_no_product(self):
        self.assertEqual(services.large_surcharge_for(_make_location(), date(2026, 6, 10)),
                         services.DEFAULT_LARGE_SURCHARGE)

    def test_reservable_menu_excludes_oomori(self):
        Product.objects.create(week="2026-06-10", name="大盛りごはん", price_A=50)
        names = [p.name for p in services.reservable_menu(date(2026, 6, 10))]
        self.assertIn("魚弁当", names)
        self.assertNotIn("大盛りごはん", names)


class CreateReservationTest(TestCase):
    def setUp(self):
        self.loc = _make_location(reservation_enabled=True)
        self.member = LineMember.objects.create(line_user_id="Uc", name="佐藤")
        self.product = Product.objects.create(week="2026-06-10", name="唐揚げ弁当", price_A=650)
        Product.objects.create(week="2026-06-10", name="大盛りごはん", price_A=50)
        self.pickup = date(2026, 6, 10)
        self.before = _aware(2026, 6, 9, 10)

    def test_success_with_sizes_and_surcharge(self):
        r = services.create_reservation(self.member, self.loc, self.pickup, [(self.product, 1, 1, 0)], now=self.before)
        self.assertEqual(r.total_quantity, 2)
        self.assertEqual(r.total_amount, 650 * 2 + 50)  # 大盛り1個に+50
        item = r.items.first()
        self.assertEqual(item.quantity_large, 1)
        self.assertEqual(item.large_surcharge, 50)

    def test_remaining_decrements_by_total_qty(self):
        self.assertEqual(services.remaining_for(self.loc, self.pickup, self.product), 10)
        services.create_reservation(self.member, self.loc, self.pickup, [(self.product, 1, 2, 0)], now=self.before)
        self.assertEqual(services.remaining_for(self.loc, self.pickup, self.product), 7)  # 3個分減る

    def test_after_deadline_rejected(self):
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.member, self.loc, self.pickup, [(self.product, 0, 1, 0)],
                                        now=_aware(2026, 6, 9, 17))

    def test_over_cap_total_rejected(self):
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.member, self.loc, self.pickup, [(self.product, 5, 6, 0)], now=self.before)

    def test_disabled_location_rejected(self):
        off = _make_location(reservation_enabled=False)
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.member, off, self.pickup, [(self.product, 0, 1, 0)], now=self.before)

    def test_empty_rejected(self):
        with self.assertRaises(services.ReservationError):
            services.create_reservation(self.member, self.loc, self.pickup, [(self.product, 0, 0, 0)], now=self.before)


# ===== ビュー =====
@override_settings(LINE_CHANNEL_ACCESS_TOKEN="")  # 通知の実送信を抑止（テストはネット非依存）
class ReserveViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.loc = _make_location(reservation_enabled=True, name="本社前店")
        self.pickup = services.default_pickup_date(timezone.localdate())
        wk = services.week_key_for_date(self.pickup)
        self.product = Product.objects.create(week=wk, name="日替わり弁当", price_A=650)
        Product.objects.create(week=wk, name="大盛りごはん", price_A=50)

    def test_get_enabled_200(self):
        url = reverse("reservations:reserve_page", args=[self.loc.qr_reserve_token])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_get_disabled_403(self):
        off = _make_location(reservation_enabled=False)
        url = reverse("reservations:reserve_page", args=[off.qr_reserve_token])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_get_invalid_404(self):
        url = reverse("reservations:reserve_page", args=["bogustoken"])
        self.assertEqual(self.client.get(url).status_code, 404)

    @mock.patch("reservations.services.is_open_for", return_value=True)
    def test_post_creates_with_sizes_and_redirects(self, _open):
        # 名前は登録画面で設定済みの前提。予約フォームには名前欄なし（個数と同意のみ）。
        url = reverse("reservations:reserve_submit", args=[self.loc.qr_reserve_token])
        resp = self.client.post(url, {
            "pickup_date": self.pickup.strftime("%Y-%m-%d"),
            "agree_policy": "1",
            f"large_{self.product.id}": "1", f"regular_{self.product.id}": "1",
        })
        self.assertEqual(resp.status_code, 302)
        r = Reservation.objects.get()
        self.assertEqual(r.total_quantity, 2)
        self.assertEqual(r.total_amount, 650 * 2 + 50)

    @mock.patch("reservations.services.is_open_for", return_value=True)
    def test_post_without_policy_agreement_400(self, _open):
        url = reverse("reservations:reserve_submit", args=[self.loc.qr_reserve_token])
        resp = self.client.post(url, {
            "pickup_date": self.pickup.strftime("%Y-%m-%d"),
            f"regular_{self.product.id}": "1",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)

    @mock.patch("reservations.services.is_open_for", return_value=True)
    def test_complete_page_shows_breakdown(self, _open):
        submit = reverse("reservations:reserve_submit", args=[self.loc.qr_reserve_token])
        resp = self.client.post(submit, {
            "pickup_date": self.pickup.strftime("%Y-%m-%d"),
            "agree_policy": "1", f"regular_{self.product.id}": "2",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(Reservation.objects.get().reservation_number, body)
        self.assertIn("日替わり弁当", body)
        self.assertIn("css/tokens.css", body)


# ===== S3: LIFF・LINE友だち必須・userId紐付け・履歴参照 =====
@override_settings(RESERVE_REQUIRE_LINE=True, LINE_CHANNEL_ACCESS_TOKEN="")
@mock.patch("reservations.services.is_open_for", return_value=True)
class LineGatedReserveTest(TestCase):
    """本番相当（LINE必須）の予約フロー。line_api をモックして検証する。"""

    def setUp(self):
        self.client = Client()
        self.loc = _make_location(reservation_enabled=True, name="本社前店")
        self.pickup = services.default_pickup_date(timezone.localdate())
        wk = services.week_key_for_date(self.pickup)
        self.product = Product.objects.create(week=wk, name="日替わり弁当", price_A=650)
        Product.objects.create(week=wk, name="大盛りごはん", price_A=50)

    def _post(self, **extra):
        url = reverse("reservations:reserve_submit", args=[self.loc.qr_reserve_token])
        data = {
            "pickup_date": self.pickup.strftime("%Y-%m-%d"),
            "name": "山田", "agree_policy": "1",
            f"regular_{self.product.id}": "1",
        }
        data.update(extra)
        return self.client.post(url, data)

    def test_no_token_rejected(self, _open):
        resp = self._post()  # line_access_token なし
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)

    @mock.patch("reservations.line_api.is_friend", return_value=True)
    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Uabc123", "display_name": "山田太郎"})
    def test_valid_friend_creates_and_binds_userid(self, _prof, _fr, _open):
        resp = self._post(line_access_token="tok-abc")
        self.assertEqual(resp.status_code, 302)
        r = Reservation.objects.get()
        self.assertEqual(r.member.line_user_id, "Uabc123")
        # 登録画面を通っていない場合の保険＝LINE表示名で会員作成（通常は register で本人設定済み）
        self.assertEqual(r.member.name, "山田太郎")

    @mock.patch("reservations.line_api.is_friend", return_value=False)
    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Uabc123", "display_name": "山田太郎"})
    def test_not_friend_rejected(self, _prof, _fr, _open):
        resp = self._post(line_access_token="tok-abc")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("友だち追加", resp.content.decode())
        self.assertEqual(Reservation.objects.count(), 0)

    @mock.patch("reservations.line_api.is_friend", return_value=True)
    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Usame", "display_name": "佐藤"})
    def test_returning_member_reused_and_same_day_dedup(self, _prof, _fr, _open):
        # 同一会員が同じ受取日に2回送信 → 会員は1人で再利用、予約は二重に作られない（既存へ誘導）
        self._post(line_access_token="tok-1")
        resp2 = self._post(line_access_token="tok-2")
        self.assertEqual(LineMember.objects.filter(line_user_id="Usame").count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(resp2.status_code, 302)
        self.assertIn("dup=1", resp2["Location"])

    @mock.patch("reservations.line_api.get_profile",
                side_effect=line_api.LineAuthError("期限切れ"))
    def test_invalid_token_rejected(self, _prof, _open):
        resp = self._post(line_access_token="bad")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)


@override_settings(RESERVE_REQUIRE_LINE=True)
class MyReservationsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.loc = _make_location(reservation_enabled=True, name="本社前店")
        self.member = LineMember.objects.create(line_user_id="Umine", name="自分")
        self.r = Reservation.objects.create(
            member=self.member, sales_location=self.loc,
            pickup_date=services.default_pickup_date(timezone.localdate()),
        )

    def test_get_renders_loader(self):
        resp = self.client.get(reverse("reservations:my_reservations"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("読み込み中", resp.content.decode())

    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Umine", "display_name": "自分"})
    def test_post_lists_own_reservations(self, _prof):
        resp = self.client.post(reverse("reservations:my_reservations"),
                                {"line_access_token": "tok"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.r.reservation_number, resp.content.decode())

    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Uother", "display_name": "他人"})
    def test_post_hides_others_reservations(self, _prof):
        resp = self.client.post(reverse("reservations:my_reservations"),
                                {"line_access_token": "tok"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(self.r.reservation_number, resp.content.decode())
        self.assertIn("ご予約はまだありません", resp.content.decode())

    def test_post_falls_back_to_session_member(self):
        # トークンが取れない文脈（LINEのトーク内リンク等）でも、セッションに覚えた本人で表示できる
        session = self.client.session
        session["reserve_member_id"] = self.member.id
        session.save()
        resp = self.client.post(reverse("reservations:my_reservations"))  # トークン無し
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.r.reservation_number, resp.content.decode())


# ===== S4: 社内管理・受取画面 =====
class ManageScreensTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.crew = User.objects.create_user("crew", password="x", is_staff=False)
        self.loc = _make_location(reservation_enabled=True, name="本社前店")
        self.member = LineMember.objects.create(line_user_id="Um", name="田中")
        self.today = timezone.localdate()
        self.r = Reservation.objects.create(member=self.member, sales_location=self.loc,
                                             pickup_date=self.today)
        ReservationItem.objects.create(reservation=self.r, product_name="唐揚げ弁当",
                                       quantity_regular=2, unit_price=650)

    def test_pickup_requires_login(self):
        resp = self.client.get(reverse("reservations:manage_pickup"))
        self.assertEqual(resp.status_code, 302)

    def test_pickup_lists_for_crew(self):
        self.client.force_login(self.crew)
        resp = self.client.get(reverse("reservations:manage_pickup"),
                               {"loc": self.loc.id, "date": self.today.strftime("%Y-%m-%d")})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("田中", resp.content.decode())

    def test_pickup_update_marks_handed(self):
        self.client.force_login(self.crew)
        resp = self.client.post(reverse("reservations:manage_pickup_update"),
                                {"reservation_id": self.r.id, "action": "hand"})
        self.assertEqual(resp.status_code, 302)
        self.r.refresh_from_db()
        self.assertEqual(self.r.status, Reservation.STATUS_HANDED)

    def test_list_requires_staff(self):
        self.client.force_login(self.crew)
        resp = self.client.get(reverse("reservations:manage_list"))
        self.assertEqual(resp.status_code, 302)

    def test_list_for_staff(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("reservations:manage_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("田中", resp.content.decode())

    def test_locations_toggle_and_cap(self):
        self.client.force_login(self.staff)
        off = _make_location(reservation_enabled=False, name="OFF店")
        resp = self.client.post(reverse("reservations:manage_locations"),
                                {"location_id": off.id, "reservation_enabled": "1",
                                 "default_product_cap": "7"})
        self.assertEqual(resp.status_code, 302)
        off.refresh_from_db()
        self.assertTrue(off.reservation_enabled)
        self.assertEqual(off.default_product_cap, 7)


# ===== S3-B: LINE通知 =====
class NotifyConfigTest(TestCase):
    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="")
    def test_push_skipped_without_token(self):
        self.assertFalse(line_notify.is_configured())
        self.assertFalse(line_notify.push_text("U1", "hi"))


class NotifyMessageTest(TestCase):
    def setUp(self):
        self.loc = _make_location(name="本社前店")
        self.member = LineMember.objects.create(line_user_id="Um", name="田中")
        self.r = Reservation.objects.create(member=self.member, sales_location=self.loc,
                                             pickup_date=timezone.localdate())
        ReservationItem.objects.create(reservation=self.r, product_name="唐揚げ弁当",
                                       quantity_regular=2, unit_price=650)

    def test_confirm_message_has_name_and_number(self):
        msg = line_notify.build_confirm_message(self.r)
        self.assertIn("田中", msg)
        self.assertIn(self.r.reservation_number, msg)
        self.assertIn("唐揚げ弁当", msg)


@override_settings(RESERVE_REQUIRE_LINE=False)
@mock.patch("reservations.services.is_open_for", return_value=True)
@mock.patch("reservations.line_notify.notify_reservation_confirmed")
class ConfirmNotifyOnSubmitTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.loc = _make_location(reservation_enabled=True, name="本社前店")
        self.pickup = services.default_pickup_date(timezone.localdate())
        wk = services.week_key_for_date(self.pickup)
        self.product = Product.objects.create(week=wk, name="唐揚げ弁当", price_A=650)
        Product.objects.create(week=wk, name="大盛りごはん", price_A=50)

    def test_confirm_notify_called_on_submit(self, notify, _open):
        url = reverse("reservations:reserve_submit", args=[self.loc.qr_reserve_token])
        resp = self.client.post(url, {
            "pickup_date": self.pickup.strftime("%Y-%m-%d"),
            "name": "田中", "agree_policy": "1", f"regular_{self.product.id}": "1",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(notify.called)


class ReminderCommandTest(TestCase):
    def setUp(self):
        self.loc = _make_location(name="本社前店")
        self.member = LineMember.objects.create(line_user_id="Um", name="田中")
        self.tomorrow = timezone.localdate() + timedelta(days=1)
        self.r = Reservation.objects.create(member=self.member, sales_location=self.loc,
                                             pickup_date=self.tomorrow)
        ReservationItem.objects.create(reservation=self.r, product_name="唐揚げ弁当",
                                       quantity_regular=1, unit_price=650)

    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="tok")
    @mock.patch("reservations.line_notify.push_text", return_value=True)
    def test_sends_and_marks(self, push):
        call_command("send_reservation_reminders")
        self.assertTrue(push.called)
        self.r.refresh_from_db()
        self.assertTrue(self.r.reminder_sent)

    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="tok")
    @mock.patch("reservations.line_notify.push_text", return_value=True)
    def test_no_double_send(self, push):
        self.r.reminder_sent = True
        self.r.save(update_fields=["reminder_sent"])
        call_command("send_reservation_reminders")
        self.assertFalse(push.called)


# ===== 二重注文防止・受け取り完了/取消・通知リンク =====
class DuplicateAndSelfActionTest(TestCase):
    def setUp(self):
        self.loc = _make_location(reservation_enabled=True, name="本社前店")
        self.member = LineMember.objects.create(line_user_id="Ud", name="佐藤")
        self.product = Product.objects.create(week="2026-06-10", name="唐揚げ弁当", price_A=650)
        self.pickup = date(2026, 6, 10)
        self.before = _aware(2026, 6, 9, 10)

    def _make(self):
        return services.create_reservation(self.member, self.loc, self.pickup,
                                           [(self.product, 0, 1, 0)], now=self.before)

    def test_duplicate_same_day_blocked(self):
        self._make()
        with self.assertRaises(services.DuplicateReservation):
            self._make()

    def test_cancel_then_rebook_allowed(self):
        r = self._make()
        services.cancel_reservation(r)
        self.assertEqual(r.status, Reservation.STATUS_CANCELLED)
        r2 = self._make()  # 取消後は同日に取り直せる
        self.assertNotEqual(r.id, r2.id)

    def test_cancel_only_when_received(self):
        r = self._make()
        services.mark_handed_by_customer(r)
        with self.assertRaises(services.ReservationError):
            services.cancel_reservation(r)

    def _login_as_owner(self):
        session = self.client.session
        session["reserve_member_id"] = self.member.id
        session.save()

    def test_self_handover_view_marks_handed_for_owner(self):
        r = self._make()
        self._login_as_owner()
        resp = self.client.post(reverse("reservations:reserve_self_handover", args=[r.reservation_number]))
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_HANDED)

    def test_self_cancel_view_cancels_for_owner(self):
        r = self._make()
        self._login_as_owner()
        resp = self.client.post(reverse("reservations:reserve_cancel", args=[r.reservation_number]))
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CANCELLED)

    def test_self_action_denied_for_non_owner(self):
        # 本人特定できない（セッションも無い）と取消できず ?denied=1 へ。状態は不変。
        r = self._make()
        resp = self.client.post(reverse("reservations:reserve_cancel", args=[r.reservation_number]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("denied=1", resp["Location"])
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_RECEIVED)

    def test_complete_shows_actions_when_received(self):
        r = self._make()
        resp = self.client.get(reverse("reservations:reserve_complete", args=[r.reservation_number]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("受け取り完了にする", body)
        self.assertIn("予約を取り消す", body)

    def test_number_has_location_prefix(self):
        r = self._make()
        # 売り場識別プレフィックス（拠点no・2桁）＋"-"＋ランダム
        self.assertRegex(r.reservation_number, r"^\d{2}-[0-9A-F]+$")
        self.assertTrue(r.reservation_number.startswith(f"{self.loc.no:02d}-"))

    def test_db_constraint_blocks_duplicate_active(self):
        from django.db import IntegrityError, transaction
        self._make()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Reservation.objects.create(member=self.member, sales_location=self.loc,
                                           pickup_date=self.pickup)


@override_settings(LIFF_RESERVE_URL="https://liff.line.me/test-liff")
class ConfirmMessageLinkTest(TestCase):
    def setUp(self):
        loc = _make_location(name="本社前店")
        member = LineMember.objects.create(line_user_id="Ux", name="田中")
        self.r = Reservation.objects.create(member=member, sales_location=loc, pickup_date=date(2026, 6, 10))
        ReservationItem.objects.create(reservation=self.r, product_name="唐揚げ弁当",
                                       quantity_regular=1, unit_price=650)

    def test_message_includes_detail_url(self):
        msg = line_notify.build_confirm_message(self.r, detail_url="https://example.com/reserve/complete/ABC/")
        self.assertIn("https://example.com/reserve/complete/ABC/", msg)

    def test_message_includes_reserve_link(self):
        # 次回のご予約（QRなし再注文）リンクが本文に入る
        msg = line_notify.build_confirm_message(self.r)
        self.assertIn("https://liff.line.me/test-liff", msg)


# ===== 初回会員登録（お名前設定）・whoami =====
@override_settings(RESERVE_REQUIRE_LINE=True, LINE_RESERVE_RICHMENU_ID="")
class RegistrationFlowTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.loc = _make_location(reservation_enabled=True, name="本社前店")

    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Unew", "display_name": "新規太郎"})
    def test_whoami_unregistered(self, _prof):
        resp = self.client.post(reverse("reservations:reserve_whoami"), {"line_access_token": "tok"})
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertFalse(d["registered"])
        self.assertEqual(d["display_name"], "新規太郎")

    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Ureg", "display_name": "登録済"})
    def test_whoami_registered(self, _prof):
        LineMember.objects.create(line_user_id="Ureg", name="本名さん")
        d = self.client.post(reverse("reservations:reserve_whoami"), {"line_access_token": "tok"}).json()
        self.assertTrue(d["registered"])
        self.assertEqual(d["name"], "本名さん")

    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Unew", "display_name": "新規太郎"})
    def test_register_creates_member_and_redirects_to_reserve(self, _prof):
        resp = self.client.post(reverse("reservations:register_member"),
                                {"line_access_token": "tok", "name": "田中花子",
                                 "loc": self.loc.qr_reserve_token})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.loc.qr_reserve_token, resp["Location"])
        self.assertEqual(LineMember.objects.get(line_user_id="Unew").name, "田中花子")

    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Ureg", "display_name": "x"})
    def test_register_updates_existing_name(self, _prof):
        LineMember.objects.create(line_user_id="Ureg", name="旧名")
        self.client.post(reverse("reservations:register_member"),
                         {"line_access_token": "tok", "name": "新名"})
        self.assertEqual(LineMember.objects.get(line_user_id="Ureg").name, "新名")

    def test_register_without_name_400(self):
        resp = self.client.post(reverse("reservations:register_member"), {"line_access_token": "tok"})
        self.assertEqual(resp.status_code, 400)

    def test_register_get_renders(self):
        resp = self.client.get(reverse("reservations:register_member"),
                               {"loc": self.loc.qr_reserve_token})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("お名前", resp.content.decode())


# ===== per-user リッチメニュー =====
from reservations import line_richmenu


class RichMenuTest(TestCase):
    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="", LINE_RESERVE_RICHMENU_ID="")
    def test_link_noop_without_config(self):
        self.assertFalse(line_richmenu.link_to_user("U1"))

    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="tok", LINE_RESERVE_RICHMENU_ID="rm1")
    @mock.patch("reservations.line_richmenu.requests.post")
    def test_link_calls_api_with_user_and_menu(self, post):
        post.return_value.status_code = 200
        self.assertTrue(line_richmenu.link_to_user("Uabc"))
        url = post.call_args[0][0]
        self.assertIn("Uabc", url)
        self.assertIn("rm1", url)


@override_settings(RESERVE_REQUIRE_LINE=True, LINE_CHANNEL_ACCESS_TOKEN="tok",
                   LINE_RESERVE_RICHMENU_ID="rm1")
class RegisterLinksRichMenuTest(TestCase):
    @mock.patch("reservations.views.line_richmenu.link_to_user", return_value=True)
    @mock.patch("reservations.line_api.get_profile",
                return_value={"user_id": "Unew", "display_name": "x"})
    def test_register_links_richmenu_to_user(self, _prof, link):
        loc = _make_location(reservation_enabled=True)
        self.client.post(reverse("reservations:register_member"),
                         {"line_access_token": "tok", "name": "田中", "loc": loc.qr_reserve_token})
        self.assertTrue(link.called)
        self.assertEqual(link.call_args[0][0], "Unew")


class ReserveViewMineRedirectTest(TestCase):
    def test_view_mine_redirects_to_history(self):
        loc = _make_location(reservation_enabled=True)
        url = reverse("reservations:reserve_page", args=[loc.qr_reserve_token])
        resp = self.client.get(url + "?view=mine")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("reservations:my_reservations"), resp["Location"])
