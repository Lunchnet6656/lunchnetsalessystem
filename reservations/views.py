"""セルフ予約ページ（顧客向け・公開）。

仕様: .company/engineering/harness/specs/w001-予約システム-要件定義.md

S2 + S2.5：拠点token識別 → 受取日選択（その週の範囲）→ メニュー/残枠 →
盛り（大盛り/普通/小盛り）入力 → 合計動的表示（JS）→ キャンセルポリシー同意 →
締切・枠検証 → 確定 → 控え。ソフトガード（FR2）：無効token=404 / 無効化拠点=403。

会員特定（誰の予約か）＝S3で LIFF のアクセストークン経由で LINE userId を確定する
（_resolve_member 参照）。ローカル開発（RESERVE_REQUIRE_LINE=False）では LINE 未連携でも
動くよう、セッション単位の仮会員にフォールバックする。
"""
import secrets
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from sales.models import SalesLocation
from reservations import line_api, line_notify, line_richmenu, services
from reservations.models import LineMember, Reservation


def _location_by_token(token):
    return SalesLocation.objects.filter(qr_reserve_token=token).first()


_MEMBER_SESSION_KEY = "reserve_member_id"


def _remember_member(request, member):
    """このブラウザセッションで本人特定できた会員を覚える。

    LINEのトーク内リンク（素URL）から開いた画面では LIFF の文脈が無く、アクセストークンを
    取れないことがある。一度でも特定できた会員をセッションに残しておけば、履歴等でトークンが
    取れなくても本人を引ける（控えと履歴で「自分の予約」が消える問題の対策）。
    """
    if member is not None:
        request.session[_MEMBER_SESSION_KEY] = member.id


def _session_member(request):
    mid = request.session.get(_MEMBER_SESSION_KEY)
    return LineMember.objects.filter(id=mid).first() if mid else None


def _resolve_member(request):
    """会員を特定する（案L＝LIFFのアクセストークンで本人識別＋友だち判定）。

    名前の登録は別画面（register_member）で済んでいる前提なので、ここでは userId で会員を引くだけ。
    本番（RESERVE_REQUIRE_LINE=True）：トークン必須・友だち必須。
    ローカル開発（False）：トークンが無ければセッション仮会員にフォールバック。
    """
    token = (request.POST.get("line_access_token") or "").strip()
    if not token:
        if settings.RESERVE_REQUIRE_LINE:
            raise services.ReservationError(
                "LINEログインが確認できませんでした。LINEのトーク画面から開き直してください。")
        member = _resolve_temp_member(request)
        _remember_member(request, member)
        return member

    profile = line_api.get_profile(token)  # 無効トークンはここで LineAuthError
    if settings.RESERVE_REQUIRE_LINE and not line_api.is_friend(token):
        raise services.ReservationError(
            "ご予約には、ランチネットLINEの友だち追加が必要です。追加してから開き直してください。")
    # 通常は register_member で作成済み。保険として display_name で get_or_create。
    member, _ = LineMember.objects.get_or_create(
        line_user_id=profile["user_id"],
        defaults={"name": profile.get("display_name") or "お客様"},
    )
    _remember_member(request, member)
    return member


def _resolve_temp_member(request):
    """ローカル開発フォールバック：LINE未連携でも動かすセッション仮会員。"""
    sid = request.session.get("reserve_temp_uid")
    if not sid:
        sid = "devtmp-" + secrets.token_hex(8)
        request.session["reserve_temp_uid"] = sid
    member, _ = LineMember.objects.get_or_create(
        line_user_id=sid, defaults={"name": "（開発確認）"})
    return member


def _member_from_token_readonly(request):
    """履歴参照用：会員を特定する（新規作成しない）。

    トークンが取れれば userId で本人を引く。取れなければ、このセッションで一度特定済みの会員に
    フォールバックする。どちらでも特定できなければ None。
    """
    token = (request.POST.get("line_access_token") or "").strip()
    if token:
        try:
            profile = line_api.get_profile(token)
        except line_api.LineAuthError:
            profile = None
        if profile:
            member = LineMember.objects.filter(line_user_id=profile["user_id"]).first()
            if member is not None:
                _remember_member(request, member)
                return member

    member = _session_member(request)
    if member is not None:
        return member
    if settings.RESERVE_REQUIRE_LINE and not token:
        raise services.ReservationError(
            "LINEログインが確認できませんでした。LINEのトーク画面から開き直してください。")
    sid = request.session.get("reserve_temp_uid")
    return LineMember.objects.filter(line_user_id=sid).first() if sid else None


def _parse_date(raw):
    try:
        return datetime.strptime(raw or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_selected_date(selectable, raw):
    d = _parse_date(raw)
    if d and d in selectable:
        return d
    return selectable[0] if selectable else None


def _form_context(request, location, token, selected_date, error=None, selectable=None):
    if selectable is None:
        selectable = services.selectable_pickup_dates(timezone.localdate())
    rows = []
    if selected_date is not None:
        for p in services.reservable_menu(selected_date):
            remaining = services.remaining_for(location, selected_date, p)
            rows.append({
                "product": p,
                "remaining": remaining,
                "price": services.unit_price_for(location, p),
                "choices": list(range(0, remaining + 1)),
            })
    return {
        "location": location,
        "token": token,
        "selectable_dates": selectable,
        "pickup_date": selected_date,
        "deadline": services.reservation_deadline(selected_date) if selected_date else None,
        "surcharge": services.large_surcharge_for(location, selected_date) if selected_date else 0,
        "rows": rows,
        "has_menu": bool(rows),
        "is_open": selected_date is not None,
        "error": error,
        "liff_id": settings.LIFF_ID,
        "require_line": settings.RESERVE_REQUIRE_LINE,
    }


def reserve_page(request, token):
    # リッチメニューの「予約を確認」用：?view=mine で履歴へ（LIFF文脈を保ったまま遷移）。
    if request.GET.get("view") == "mine":
        return redirect("reservations:my_reservations")
    location = _location_by_token(token)
    if location is None:
        return render(request, "reservations/reserve_unavailable.html",
                      {"reason": "invalid"}, status=404)
    if not location.reservation_enabled:
        return render(request, "reservations/reserve_unavailable.html",
                      {"reason": "disabled", "location": location}, status=403)

    selectable = services.selectable_pickup_dates(timezone.localdate())
    selected = _resolve_selected_date(selectable, request.GET.get("date"))
    return render(request, "reservations/reserve.html",
                  _form_context(request, location, token, selected, selectable=selectable))


@require_http_methods(["POST"])
def reserve_whoami(request):
    """LIFFのアクセストークンから、その人が会員登録済みかを返す（予約ページのルーティング用）。

    予約ページのJSがこれを叩き、未登録なら登録画面へ誘導／登録済みなら名前を表示して予約へ進む。
    """
    token = (request.POST.get("line_access_token") or "").strip()
    if not token:
        return JsonResponse({"registered": False, "name": "", "display_name": ""})
    try:
        profile = line_api.get_profile(token)
    except line_api.LineAuthError:
        return JsonResponse({"registered": False, "name": "", "display_name": ""})
    member = LineMember.objects.filter(line_user_id=profile["user_id"]).first()
    return JsonResponse({
        "registered": bool(member),
        "name": member.name if member else "",
        "display_name": profile.get("display_name", ""),
    })


def _redirect_after_register(loc):
    if loc:
        return redirect("reservations:reserve_page", token=loc)
    return redirect("reservations:my_reservations")


@require_http_methods(["GET", "POST"])
def register_member(request):
    """初回の会員登録（お名前設定）。LINE表示名をデフォルトに、編集可。保存後は予約ページへ。

    ?loc=<拠点token> を受けて、登録後にその拠点の予約ページへ戻す。
    """
    loc = (request.GET.get("loc") or request.POST.get("loc") or "").strip()

    def _render(error=None, name="", status=200):
        return render(request, "reservations/register.html", {
            "loc": loc, "liff_id": settings.LIFF_ID,
            "require_line": settings.RESERVE_REQUIRE_LINE,
            "error": error, "name": name,
        }, status=status)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        token = (request.POST.get("line_access_token") or "").strip()
        if not name:
            return _render(error="お名前を入力してください。", name=name, status=400)
        if not token:
            if settings.RESERVE_REQUIRE_LINE:
                return _render(error="LINEログインが確認できませんでした。開き直してください。",
                               name=name, status=400)
            member = _resolve_temp_member(request)  # devフォールバック
            member.name = name
            member.save(update_fields=["name"])
            _remember_member(request, member)
            return _redirect_after_register(loc)
        try:
            profile = line_api.get_profile(token)
        except line_api.LineAuthError as e:
            return _render(error=str(e), name=name, status=400)
        member, _ = LineMember.objects.get_or_create(
            line_user_id=profile["user_id"], defaults={"name": name})
        if member.name != name:           # 登録画面は名前の変更にも使える
            member.name = name
            member.save(update_fields=["name"])
        _remember_member(request, member)
        # 登録済みのこの人にだけ予約リッチメニューを出す（best-effort）。
        line_richmenu.link_to_user(profile["user_id"])
        return _redirect_after_register(loc)

    return _render()


@require_http_methods(["POST"])
def reserve_submit(request, token):
    location = _location_by_token(token)
    if location is None or not location.reservation_enabled:
        return render(request, "reservations/reserve_unavailable.html",
                      {"reason": "disabled", "location": location}, status=403)

    selectable = services.selectable_pickup_dates(timezone.localdate())
    selected = _parse_date(request.POST.get("pickup_date"))
    if selected is None or selected not in selectable:
        ctx = _form_context(request, location, token,
                            selectable[0] if selectable else None,
                            error="受取日を選び直してください。", selectable=selectable)
        return render(request, "reservations/reserve.html", ctx, status=400)

    agreed = bool(request.POST.get("agree_policy"))

    def _q(prefix, pid):
        try:
            return max(0, int(request.POST.get(f"{prefix}_{pid}", 0) or 0))
        except (TypeError, ValueError):
            return 0

    items = []
    for p in services.reservable_menu(selected):
        large, regular, small = _q("large", p.id), _q("regular", p.id), _q("small", p.id)
        if large + regular + small > 0:
            items.append((p, large, regular, small))

    # 名前は別画面（register_member）で登録済み。ここでは個数・同意・会員特定のみ。
    if not agreed:
        ctx = _form_context(request, location, token, selected,
                            error="キャンセルについての確認にチェックしてください。", selectable=selectable)
        return render(request, "reservations/reserve.html", ctx, status=400)

    try:
        member = _resolve_member(request)
        reservation = services.create_reservation(member, location, selected, items)
    except services.DuplicateReservation as dup:
        # 二重注文防止：新規は作らず、既存予約の控えへ誘導する。
        url = reverse("reservations:reserve_complete",
                      kwargs={"reservation_number": dup.existing.reservation_number})
        return redirect(f"{url}?dup=1")
    except (services.ReservationError, line_api.LineAuthError) as e:
        ctx = _form_context(request, location, token, selected, error=str(e), selectable=selectable)
        return render(request, "reservations/reserve.html", ctx, status=400)

    # 確定控えを LINE へ（FR7）。控えページへのリンク付き。失敗しても予約は成立（best-effort）。
    detail_url = request.build_absolute_uri(
        reverse("reservations:reserve_complete",
                kwargs={"reservation_number": reservation.reservation_number}))
    line_notify.notify_reservation_confirmed(reservation, detail_url=detail_url)

    return redirect("reservations:reserve_complete",
                    reservation_number=reservation.reservation_number)


def reserve_complete(request, reservation_number):
    reservation = get_object_or_404(
        Reservation.objects.select_related("sales_location", "member"),
        reservation_number=reservation_number,
    )
    return render(request, "reservations/reserve_complete.html", {
        "reservation": reservation,
        "dup": request.GET.get("dup") == "1",
        "denied": request.GET.get("denied") == "1",
        "liff_reserve_url": settings.LIFF_RESERVE_URL,
        "liff_id": settings.LIFF_ID,
        "require_line": settings.RESERVE_REQUIRE_LINE,
    })


def _verify_owner(request, reservation):
    """操作者がこの予約の本人か（LIFFトークン or セッション会員で照合）。

    控えの「受け取り完了」「取消」は破壊的操作なので、予約番号を知るだけの第三者には
    実行させない（IDOR対策）。本人特定できなければ False。
    """
    try:
        member = _member_from_token_readonly(request)
    except (services.ReservationError, line_api.LineAuthError):
        member = None
    return member is not None and member.id == reservation.member_id


def _deny_redirect(reservation_number):
    url = reverse("reservations:reserve_complete",
                  kwargs={"reservation_number": reservation_number})
    return redirect(f"{url}?denied=1")


@require_http_methods(["POST"])
def reserve_self_handover(request, reservation_number):
    """お客様自身による「受け取り完了」。受付済のみ受渡済へ。本人のみ。"""
    reservation = get_object_or_404(Reservation, reservation_number=reservation_number)
    if not _verify_owner(request, reservation):
        return _deny_redirect(reservation_number)
    try:
        services.mark_handed_by_customer(reservation)
    except services.ReservationError:
        pass  # 既に受渡済/取消済なら何もしない（控えに現状を表示）
    return redirect("reservations:reserve_complete", reservation_number=reservation_number)


@require_http_methods(["POST"])
def reserve_cancel(request, reservation_number):
    """お客様自身による「予約の取消」。受付済のみ取消（枠を解放＝取り直し可能）。本人のみ。"""
    reservation = get_object_or_404(Reservation, reservation_number=reservation_number)
    if not _verify_owner(request, reservation):
        return _deny_redirect(reservation_number)
    try:
        services.cancel_reservation(reservation)
    except services.ReservationError:
        pass
    return redirect("reservations:reserve_complete", reservation_number=reservation_number)


@require_http_methods(["GET", "POST"])
def my_reservations(request):
    """予約履歴参照（§13 #6）。LIFFのアクセストークンで本人を特定し、自分の予約一覧を表示。

    GET：LIFF初期化用ページ（JSがアクセストークンを取って自分自身へPOST）。
    POST：トークンから本人を特定 → その会員の予約一覧をサーバーレンダリング。
    """
    member = None
    error = None
    if request.method == "POST":
        try:
            member = _member_from_token_readonly(request)
        except (services.ReservationError, line_api.LineAuthError) as e:
            error = str(e)

    reservations = []
    if member is not None:
        reservations = (Reservation.objects
                        .filter(member=member)
                        .select_related("sales_location")
                        .prefetch_related("items")
                        .order_by("-pickup_date", "-created_at"))

    return render(request, "reservations/my_reservations.html", {
        "liff_id": settings.LIFF_ID,
        "require_line": settings.RESERVE_REQUIRE_LINE,
        "loaded": request.method == "POST",
        "member": member,
        "reservations": reservations,
        "error": error,
        "liff_reserve_url": settings.LIFF_RESERVE_URL,
    })
