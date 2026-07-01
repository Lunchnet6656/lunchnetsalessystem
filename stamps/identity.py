"""スタンプ用の会員特定（LIFFアクセストークン→LINE userId→LineMember）。

予約システムと同じ仕組み（reservations.line_api でトークン検証＝なりすまし防止）を再利用する。
スタンプは名前を必要としないので、初回はLINE表示名で会員を自動作成する。

本番（STAMP_REQUIRE_LINE=True）：アクセストークン必須。
ローカル開発（False）：トークンが無ければセッション単位の仮会員にフォールバック。
"""
import secrets

from django.conf import settings

from reservations import line_api
from reservations.models import LineMember


_SESSION_KEY = "stamp_member_id"
_TEMP_UID_KEY = "stamp_temp_uid"


class StampAuthError(Exception):
    """会員特定に失敗（トークン無効・LINE未確認など）。利用者向け文言を持つ。"""


def _remember(request, member):
    if member is not None:
        request.session[_SESSION_KEY] = member.id


def _session_member(request):
    mid = request.session.get(_SESSION_KEY)
    return LineMember.objects.filter(id=mid).first() if mid else None


def _temp_member(request):
    sid = request.session.get(_TEMP_UID_KEY)
    if not sid:
        sid = "stamptmp-" + secrets.token_hex(8)
        request.session[_TEMP_UID_KEY] = sid
    member, _ = LineMember.objects.get_or_create(
        line_user_id=sid, defaults={"name": "（開発確認）"})
    return member


def stamp_require_line():
    return getattr(settings, "STAMP_REQUIRE_LINE", settings.RESERVE_REQUIRE_LINE)


def resolve_member(request):
    """スタンプ押印用に会員を特定（無ければ作成）。本番はトークン必須。"""
    token = (request.POST.get("line_access_token") or "").strip()
    if not token:
        if stamp_require_line():
            raise StampAuthError(
                "LINEログインが確認できませんでした。LINEのトーク画面から開き直してください。")
        member = _temp_member(request)
        _remember(request, member)
        return member

    profile = line_api.get_profile(token)  # 無効トークンは LineAuthError
    member, _ = LineMember.objects.get_or_create(
        line_user_id=profile["user_id"],
        defaults={"name": profile.get("display_name") or "お客様"},
    )
    _remember(request, member)
    return member


def member_readonly(request):
    """参照用に会員を特定（新規作成しない）。トークン→セッションの順。無ければ None。"""
    token = (request.POST.get("line_access_token") or "").strip()
    if token:
        try:
            profile = line_api.get_profile(token)
        except line_api.LineAuthError:
            profile = None
        if profile:
            member = LineMember.objects.filter(line_user_id=profile["user_id"]).first()
            if member is not None:
                _remember(request, member)
                return member
    return _session_member(request)
