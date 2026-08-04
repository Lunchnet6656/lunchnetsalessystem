"""開発確認用のデモ画面（本番は404）。

STAMP_REQUIRE_LINE=True（本番）では identity.stamp_require_line() が真になり、全て 404 を返す。
ローカル開発でカード各段階・クーポン各状態の見た目を素早く確認するための足場。
固定のデモ会員(devdemo)・デモ店舗を使い、過去日付でスタンプを積む。
"""
from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from sales.models import SalesLocation
from reservations.models import LineMember
from stamps import identity, services
from stamps.services import card_view_state
from stamps.views import coupon_ctx, render_stamp


def _demo_blocked():
    """本番（LINE必須）ではデモを塞ぐ。"""
    return identity.stamp_require_line()


def _demo_member(request):
    """毎回まっさらなデモ会員。セッションにも記憶（クーポン詳細の本人チェックを通すため）。"""
    member, _ = LineMember.objects.get_or_create(
        line_user_id="devdemo", defaults={"name": "デモ太郎"})
    member.stamp_cards.all().delete()
    identity._remember(request, member)
    return member


def _demo_location():
    return (SalesLocation.objects.filter(stamp_enabled=True).order_by("no").first()
            or SalesLocation.objects.order_by("no").first())


def stamp_demo(request):
    """好きな個数（?n=0〜20）で埋めたカードをプレビュー。?prev/?fresh/?use で状態切替。"""
    if _demo_blocked():
        return render(request, "stamps/unavailable.html", {"reason": "invalid"}, status=404)

    try:
        n = max(0, min(services.effective_cap(), int(request.GET.get("n", 7))))
    except (TypeError, ValueError):
        n = 7

    location = _demo_location()
    member = _demo_member(request)
    now_local = timezone.localtime(timezone.now())

    # ?prev=1：先に「失効した前カード＋まだ有効なクーポン」を作る（前カードのクーポン導線の確認用）。
    if request.GET.get("prev") == "1":
        for d in (35, 30, 25, 20, 18, 16, 14, 12, 10, 8):
            day = (now_local - timedelta(days=d)).replace(hour=12, minute=0, second=0, microsecond=0)
            services.award_stamp(member, location, now=day)

    # ?completed=1：先に「20pt満了カード」を近い過去に作る（満了→次回来店で新カード の確認用）。
    # 満了カードのクーポン（5/10/20pt・獲得から30日内）が新カードへ繰り越して表示されるのを見る。
    # 続く n ループ（下）が“満了後の次回来店”に相当し、新カードが自動発行される。n は6以下推奨。
    if request.GET.get("completed") == "1":
        n = min(n, 6)
        cap = services.effective_cap()
        # 2倍デー(streak/雨)が乗っても“1枚ちょうど”で止める：満了した瞬間にループを抜ける。
        # 26日前から降順に押す＝満了は概ね10日前前後に着地し、5/10/20ptのクーポンは全て30日内で繰り越す。
        for d in range(26, 6, -1):
            c = services._current_card(member)
            if c is not None and c.stamp_count >= cap:
                break
            day = (now_local - timedelta(days=d)).replace(hour=12, minute=0, second=0, microsecond=0)
            services.award_stamp(member, location, now=day)

    # ?fresh=1：最後の1個を「今日」にして、当日獲得＝利用開始前(pending)の状態を再現する。
    fresh = request.GET.get("fresh") == "1"
    last_offset = 0 if fresh else 1
    for i in range(n):
        off = (n - 1 - i) + last_offset
        day = (now_local - timedelta(days=off)).replace(hour=12, minute=0, second=0, microsecond=0)
        services.award_stamp(member, location, now=day)

    card = services._current_card(member)

    # ?use=<pt> で、その段階の特典を「使用済み」にした画面（クーポン使用後）を再現する。
    tone = "info"
    if request.GET.get("completed") == "1":
        message = f"デモ：満了カードの次回来店＝新カード（{n}個）で再スタート。下に前カードのクーポン繰り越し"
    else:
        message = f"デモ：スタンプ{n}個のカード（?n=0〜20 で切替）"
    use_pt = request.GET.get("use")
    if use_pt and card:
        reward = card.rewards.filter(threshold_pt=use_pt, status="issued").first()
        if reward:
            services.use_reward(reward)
            tone = "ok"
            message = f"「{reward.label}」を使用しました。販売員にこの画面をお見せください。"

    # デモ：n が特典段階ちょうどなら獲得演出（中央の獲得モーダル）も再現する。
    demo_new_reward = card.rewards.filter(threshold_pt=n).first() if card else None

    return render_stamp(
        request, location=location, loaded=True, liff_id="", require_line=False,
        member=member, card=card, state=card_view_state(card),
        result_tone=tone, result_message=message, new_reward=demo_new_reward,
        popped=n > 0, other_coupons=services.usable_rewards(member, exclude_card=card),
    )


def demo_coupon(request):
    """クーポン画面を表示（?pt=5|10|20・?used=1 で使用済み）。本番は404。"""
    if _demo_blocked():
        return render(request, "stamps/unavailable.html", {"reason": "invalid"}, status=404)

    try:
        pt = int(request.GET.get("pt", 10))
    except (TypeError, ValueError):
        pt = 10

    location = _demo_location()
    member = _demo_member(request)
    now_local = timezone.localtime(timezone.now())
    for i in range(pt):
        day = (now_local - timedelta(days=(pt - i))).replace(hour=12, minute=0, second=0, microsecond=0)
        services.award_stamp(member, location, now=day)

    card = services._current_card(member)
    reward = card.rewards.filter(threshold_pt=pt).first() if card else None
    if reward is None:
        return render(request, "stamps/unavailable.html", {"reason": "invalid"}, status=404)
    if request.GET.get("used") == "1":
        services.use_reward(reward)
        reward.refresh_from_db()
    return render(request, "stamps/coupon.html", coupon_ctx(reward, member))
