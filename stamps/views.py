"""お客様向けスタンプカード画面（LIFF・公開）。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md（§4, §7.1）

- stamp_page  … 店舗別QR（qr_stamp_token）でLIFFを開く入口。
                GET：LIFF初期化ページ（JSがアクセストークンを取って自分自身へPOST）。
- stamp_scan  … POST：本人特定 → スタンプ付与判定（1日1回・時間帯・打ち止め）→ カード表示。
- my_card     … LINE内から自分のカードを開く（来店せず進捗・クーポン確認）。リッチメニュー遷移先。
- coupon_detail / reward_use … クーポン詳細と使用（B案：客が押す→販売員が目視）。本人のみ。

会員特定は stamps.identity（予約と同じトークン検証）。名前登録は不要（来店計測が主目的）。
開発確認用のデモ画面は stamps.dev_views（本番は404）。
"""
from urllib.parse import unquote

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from sales.models import SalesLocation
from reservations import line_api
from stamps import identity, services
from stamps.models import Reward, StampLog
from stamps.services import card_view_state


def liff_entry(request):
    """LIFFのディープリンク入口（サイトのルート＝スタンプ用LIFFのエンドポイント）。

    LINEはLIFFのエンドポイント（ルート `/`）を開き、本来のパスを
    `?liff.state=/stamp/<token>/` というクエリで渡す（SPA向けのディープリンク方式）。
    当アプリはサーバーレンダリングなので、ここで liff.state を受けて本来のスタンプURLへ
    サーバー側でリダイレクトする。liff.state が無い通常アクセスは404（従来どおり）。
    """
    target = unquote(request.GET.get("liff.state", ""))
    # オープンリダイレクト防止：自サイトの /stamp/ パスのみ許可（外部URL・二重スラッシュ排除）。
    if target.startswith("/stamp/") and "//" not in target and "://" not in target:
        return redirect(target)
    raise Http404("liff entry")


def _location_by_token(token):
    return SalesLocation.objects.filter(qr_stamp_token=token).first()


def _is_first_stamp(member):
    """この会員にとって初めてのスタンプか（来店ログが1件だけ＝今押したのが最初）。"""
    return StampLog.objects.filter(card__member=member).count() == 1


_RESULT_MESSAGES = {
    services.STAMPED: ("ok", "スタンプを押しました！"),
    services.ALREADY_TODAY: ("info", "本日はもう押印済みです。また明日のご来店をお待ちしています。"),
    services.OUTSIDE_HOURS: ("warn", "ただいまは出店時間外です。出店時間内にスタンプできます。"),
    services.COMPLETED: ("info", "カードが満了しました（お弁当無料2個ぶん）。次のカードは期限の翌日からです。"),
}


def render_stamp(request, *, location=None, token="", post_action=None, status=200, **extra):
    """stamp.html を統一コンテキストで描画する。

    全スタンプ画面（入口・スキャン結果・マイカード・デモ）が同じ土台を使い、
    個別の値（loaded/member/card/state/error など）は extra で上書きする。
    ＝各ビューでの巨大な辞書の手組み・食い違いを無くす。
    """
    if post_action is None:
        post_action = (reverse("stamps:stamp_scan", args=[token]) if token
                       else reverse("stamps:my_card"))
    ctx = {
        "loaded": False,
        "location": location,
        "token": token,
        # スタンプ画面の liff.init はスタンプ用LIFF（＝スタンプを配信するドメインのLIFF）を使う。
        # QR/リッチメニューと同じ STAMP_LIFF_ID に揃える（予約が別ドメインでも食い違わない）。
        "liff_id": settings.STAMP_LIFF_ID,
        "require_line": identity.stamp_require_line(),
        "post_action": post_action,
    }
    ctx.update(extra)
    return render(request, "stamps/stamp.html", ctx, status=status)


def stamp_page(request, token):
    """GET：QRから開いた入口。LIFFでトークンを取り、自分自身へPOSTさせる。"""
    location = _location_by_token(token)
    if location is None:
        return render(request, "stamps/unavailable.html", {"reason": "invalid"}, status=404)
    if not location.stamp_enabled:
        return render(request, "stamps/unavailable.html",
                      {"reason": "disabled", "location": location}, status=403)
    return render_stamp(request, location=location, token=token, loaded=False)


@require_http_methods(["GET", "POST"])
def my_card(request):
    """LINE内から自分のスタンプカードを開く（来店せず進捗・クーポンを確認）。リッチメニュー遷移先。"""
    if request.method == "GET":
        return render_stamp(request, loaded=False)
    try:
        member = identity.resolve_member(request)
    except (identity.StampAuthError, line_api.LineAuthError) as e:
        return render_stamp(request, loaded=True, error=str(e), status=400)

    card = services._current_card(member)
    return render_stamp(
        request, loaded=True, member=member, card=card,
        state=card_view_state(card), popped=False,
        other_coupons=services.usable_rewards(member, exclude_card=card),
    )


@require_http_methods(["POST"])
def stamp_scan(request, token):
    """POST：本人特定 → スタンプ付与判定 → カード表示。"""
    location = _location_by_token(token)
    if location is None:
        return render(request, "stamps/unavailable.html", {"reason": "invalid"}, status=404)
    if not location.stamp_enabled:
        return render(request, "stamps/unavailable.html",
                      {"reason": "disabled", "location": location}, status=403)

    try:
        member = identity.resolve_member(request)
    except (identity.StampAuthError, line_api.LineAuthError) as e:
        return render_stamp(request, location=location, token=token,
                            loaded=True, error=str(e), status=400)

    result = services.award_stamp(member, location)
    # 初回スタンプ時：スタンプ用リッチメニューを本人へ割り当て（自作Lステップのセグメント配信）。
    if result.ok and _is_first_stamp(member):
        from stamps import richmenu
        richmenu.assign_on_first_stamp(member)

    tone, message = _RESULT_MESSAGES.get(result.status, ("info", ""))
    # 時間外などで result.card が無いときも、会員の現行カード（無ければ空カード）を表示する。
    card = result.card or services._current_card(member)

    return render_stamp(
        request, location=location, token=token, loaded=True,
        member=member, card=card, state=card_view_state(card),
        result_tone=tone, result_message=message, new_reward=result.new_reward,
        popped=result.ok,   # 今スタンプを押せたときだけ「押した感」アニメを出す
        other_coupons=services.usable_rewards(member, exclude_card=card),
    )


def coupon_ctx(reward, member=None):
    return {
        "reward": reward,
        "is_used": reward.status == Reward.STATUS_USED,
        "member": member,
        "liff_id": settings.STAMP_LIFF_ID,   # クーポン画面の liff.init もスタンプ用LIFF
        "require_line": identity.stamp_require_line(),
    }


def coupon_detail(request, reward_id):
    """クーポン詳細画面（LINEのクーポン画面に倣う）。本人のみ閲覧（IDOR対策・常時）。"""
    reward = get_object_or_404(Reward.objects.select_related("card__member", "card"), pk=reward_id)
    member = identity.member_readonly(request)
    if member is None or member.id != reward.card.member_id:
        return render(request, "stamps/unavailable.html", {"reason": "denied"}, status=403)
    return render(request, "stamps/coupon.html", coupon_ctx(reward, member))


@require_http_methods(["POST"])
def reward_use(request, reward_id):
    """クーポンを使う（B案）。本人のみ。使用済みにしてクーポン画面（使用済み）を表示。"""
    reward = get_object_or_404(Reward.objects.select_related("card__member", "card"), pk=reward_id)
    try:
        member = identity.member_readonly(request)
    except (identity.StampAuthError, line_api.LineAuthError):
        member = None
    # 本人確認：このカードの持ち主だけが使える（予約の控えと同じIDOR対策）。
    if member is None or member.id != reward.card.member_id:
        return render(request, "stamps/unavailable.html", {"reason": "denied"}, status=403)

    services.use_reward(reward)
    reward.refresh_from_db()
    return render(request, "stamps/coupon.html", coupon_ctx(reward, member))
