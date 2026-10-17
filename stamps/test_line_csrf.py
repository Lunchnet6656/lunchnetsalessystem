"""スタンプの送信：LINEトークンがあればクッキーの合言葉なしで通し、無ければ今までどおり合言葉で守る。

2026-10-17 LINE内ブラウザでクッキーが消え、お客様が1日15〜20件「期限切れ」になっていた件。
"""
import datetime
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from reservations.models import LineMember
from stamps import services
from stamps.models import Reward, RewardTier

SITE = "http://testserver"
OUR_CHANNEL = "2010353511"


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def fake_line(client_id=OUR_CHANNEL, user_id="U_line_1"):
    def get(url, **kwargs):
        if url.endswith("/oauth2/v2.1/verify"):
            return FakeResponse(200, {"client_id": client_id, "expires_in": 3600, "scope": "profile"})
        if url.endswith("/v2/profile"):
            return FakeResponse(200, {"userId": user_id, "displayName": "お客様"})
        return FakeResponse(404, {})
    return mock.patch("reservations.line_api.requests.get", side_effect=get)


class LineTokenCsrfTest(TestCase):
    def setUp(self):
        # クッキーの合言葉を本番と同じく確かめるクライアント（LINE内ブラウザでクッキーが消えた状態を再現）
        self.client = Client(enforce_csrf_checks=True)

    def post_card(self, **headers):
        return self.client.post("/stamp/me/", {"line_access_token": "at-123"}, **headers)

    def test_token_without_cookie_passes(self):
        with fake_line():
            res = self.post_card(HTTP_ORIGIN=SITE)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(LineMember.objects.filter(line_user_id="U_line_1").exists())

    def test_no_origin_header_still_passes_with_token(self):
        with fake_line():
            self.assertEqual(self.post_card().status_code, 200)

    def test_referer_from_our_site_passes(self):
        with fake_line():
            self.assertEqual(self.post_card(HTTP_REFERER=f"{SITE}/stamp/me/").status_code, 200)

    def test_foreign_origin_is_rejected(self):
        with fake_line():
            res = self.post_card(HTTP_ORIGIN="https://evil.example.com")
        self.assertEqual(res.status_code, 403)
        self.assertFalse(LineMember.objects.exists())

    def test_token_from_another_channel_is_rejected(self):
        with fake_line(client_id="9999999999"):
            res = self.post_card(HTTP_ORIGIN=SITE)
        self.assertEqual(res.status_code, 400)
        self.assertContains(res, "このLINEログインでは利用できません", status_code=400)
        self.assertFalse(LineMember.objects.exists())

    def test_no_token_and_no_cookie_is_rejected(self):
        with self.settings(STAMP_REQUIRE_LINE=False):
            res = self.client.post("/stamp/me/", {})
        self.assertEqual(res.status_code, 403)

    def test_no_token_with_valid_cookie_works_as_before(self):
        with self.settings(STAMP_REQUIRE_LINE=False):
            self.client.get("/stamp/me/")
            token = self.client.cookies["csrftoken"].value
            res = self.client.post("/stamp/me/", {"csrfmiddlewaretoken": token})
        self.assertEqual(res.status_code, 200)

    def test_reward_use_with_token(self):
        member = LineMember.objects.create(line_user_id="U_line_1", name="お客様")
        today = timezone.localdate()
        card = services._new_card(member, today - datetime.timedelta(days=3))
        tier = RewardTier.objects.create(threshold_pt=55, kind=RewardTier.KIND_FREE, label="テスト特典", active=False)
        reward = Reward.objects.create(card=card, tier=tier, threshold_pt=5, kind="item", label="唐揚げ1個",
                                       issued_at=timezone.now() - datetime.timedelta(days=2),
                                       valid_from=today - datetime.timedelta(days=1),
                                       expires_on=today + datetime.timedelta(days=30))
        with fake_line(user_id="U_line_1"):
            res = self.client.post(f"/stamp/reward/{reward.pk}/use/", {"line_access_token": "at-123"},
                                   HTTP_ORIGIN=SITE)
        self.assertEqual(res.status_code, 200)
        reward.refresh_from_db()
        self.assertEqual(reward.status, Reward.STATUS_USED)
        # 他人のトークンでは使えない（今までどおり本人確認）
        with fake_line(user_id="U_other"):
            res = self.client.post(f"/stamp/reward/{reward.pk}/use/", {"line_access_token": "at-999"},
                                   HTTP_ORIGIN=SITE)
        self.assertEqual(res.status_code, 403)
