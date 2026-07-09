"""スタンプカードの管理画面（運営用・社内）。

仕様: .company/engineering/harness/specs/lunchnetsale-スタンプカード来店計測-MVP.md（§1.1, §7.2, §12）

この管理画面は将来「自作のLINE拡張プラットフォーム（Lステップ相当）」へ育てる土台＝シェル。
スタンプカードはその第1モジュール。配信・セグメント・友だち管理などの他モジュールは
今回は中身を作らず、ナビに「準備中」で置くだけ（base.html）。

画面（スタンプモジュール）:
- dashboard … 参加率／5・10・20pt達成率／来店数／特典発行・使用／推定コスト。期間・店舗フィルタ。
- visits    … 来店ログ一覧（会員・店舗・日時）＋CSV。
- analytics … 参加率／達成率／来店頻度分布／店舗別比較＋CSV。
- locations … 既存SalesLocationを読み込み、スタンプ用QRの発行/印刷/再発行・出店時間・ON/OFF。
- rewards   … 特典段階（pt・内容・額・コスト）の編集、発行/使用状況とコスト集計。

すべて @staff_member_required（運営限定）。Django admin は使わない（アプリ内画面で実装）。
"""
import csv
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Max, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from sales.models import SalesLocation, _generate_qr_token
from reservations.models import LineMember
from stamps.models import (
    Reward, RewardTier, RichMenuLink, StampCard, StampConfig, StampLog,
)
from stamps.services import (
    DEFAULT_CLOSE_TIME, DEFAULT_OPEN_TIME, location_open_window, reward_short_label,
)


def _parse_date(raw):
    try:
        return datetime.strptime(raw or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def _period(request, default_days=30):
    """フィルタの期間 (start, end)。既定は直近30日。"""
    end = _parse_date(request.GET.get("to")) or timezone.localdate()
    start = _parse_date(request.GET.get("from")) or (end - timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    return start, end


def _logs_in_period(start, end, location_id=None):
    qs = StampLog.objects.filter(stamped_on__gte=start, stamped_on__lte=end)
    if location_id:
        qs = qs.filter(location_id=location_id)
    return qs


@staff_member_required
def dashboard(request):
    """KPIサマリー：参加率・各pt達成率・来店数・特典発行/使用・推定特典コスト。"""
    start, end = _period(request)
    loc_id = request.GET.get("loc") or ""

    logs = _logs_in_period(start, end, loc_id)
    visits = logs.count()
    participant_ids = set(logs.values_list("card__member_id", flat=True))
    participants = len(participant_ids)
    friends = LineMember.objects.count()
    participation_rate = round(participants / friends * 100, 1) if friends else 0.0

    # 達成率：期間内に来店した参加者のカードのうち、各ptに到達した割合。
    cards = (StampCard.objects
             .filter(logs__stamped_on__gte=start, logs__stamped_on__lte=end)
             .distinct())
    if loc_id:
        cards = cards.filter(logs__location_id=loc_id).distinct()
    cards = list(cards)
    n_cards = len(cards)

    def _reach_rate(pt):
        if not n_cards:
            return 0.0
        reached = sum(1 for c in cards if c.stamp_count >= pt)
        return round(reached / n_cards * 100, 1)

    reach = {pt: _reach_rate(pt) for pt in (5, 10, 20)}

    rewards = Reward.objects.filter(issued_at__date__gte=start, issued_at__date__lte=end)
    issued = rewards.count()
    used = rewards.filter(status=Reward.STATUS_USED).count()
    est_cost = rewards.filter(status=Reward.STATUS_USED).aggregate(s=Sum("benefit_cost"))["s"] or 0

    return render(request, "stamps/manage/dashboard.html", {
        "nav": "dashboard",
        "start": start, "end": end, "f_loc": loc_id,
        "locations": SalesLocation.objects.order_by("no", "name"),
        "visits": visits,
        "participants": participants,
        "friends": friends,
        "participation_rate": participation_rate,
        "n_cards": n_cards,
        "reach": reach,
        "issued": issued,
        "used": used,
        "est_cost": est_cost,
    })


@staff_member_required
def visits(request):
    """来店ログ一覧（会員・店舗・日時）。期間・店舗フィルタ。CSVは ?export=csv。"""
    start, end = _period(request)
    loc_id = request.GET.get("loc") or ""
    logs = (_logs_in_period(start, end, loc_id)
            .select_related("card__member", "location")
            .order_by("-stamped_at"))

    if request.GET.get("export") == "csv":
        return _visits_csv(logs, start, end)

    return render(request, "stamps/manage/visits.html", {
        "nav": "visits",
        "start": start, "end": end, "f_loc": loc_id,
        "locations": SalesLocation.objects.order_by("no", "name"),
        "logs": logs[:500],
        "total": logs.count(),
    })


def _visits_csv(logs, start, end):
    resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    resp["Content-Disposition"] = (
        f'attachment; filename="stamp_visits_{start:%Y%m%d}-{end:%Y%m%d}.csv"')
    w = csv.writer(resp)
    w.writerow(["来店日時", "会員", "LINEユーザーID", "店舗", "カードID", "カード時点pt"])
    for lg in logs:
        w.writerow([
            timezone.localtime(lg.stamped_at).strftime("%Y-%m-%d %H:%M"),
            lg.card.member.name,
            lg.card.member.line_user_id,
            lg.location.name,
            lg.card_id,
            lg.card.stamp_count,
        ])
    return resp


@staff_member_required
def analytics(request):
    """参加率・達成率・来店頻度分布・店舗別比較。CSVは ?export=csv。"""
    start, end = _period(request)
    logs = _logs_in_period(start, end)

    # 店舗別の来店数・参加人数
    by_loc = (logs.values("location_id", "location__name")
              .annotate(visits=Count("id"),
                        members=Count("card__member_id", distinct=True))
              .order_by("-visits"))

    # 来店頻度分布（期間内の会員ごとの来店回数）
    per_member = (logs.values("card__member_id")
                  .annotate(n=Count("id")))
    dist = {"1回": 0, "2回": 0, "3-4回": 0, "5-9回": 0, "10回以上": 0}
    for row in per_member:
        n = row["n"]
        if n == 1:
            dist["1回"] += 1
        elif n == 2:
            dist["2回"] += 1
        elif n <= 4:
            dist["3-4回"] += 1
        elif n <= 9:
            dist["5-9回"] += 1
        else:
            dist["10回以上"] += 1

    if request.GET.get("export") == "csv":
        return _analytics_csv(by_loc, dist, start, end)

    return render(request, "stamps/manage/analytics.html", {
        "nav": "analytics",
        "start": start, "end": end,
        "by_loc": list(by_loc),
        "dist": dist,
        "active_members": per_member.count(),
    })


def _analytics_csv(by_loc, dist, start, end):
    resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    resp["Content-Disposition"] = (
        f'attachment; filename="stamp_analytics_{start:%Y%m%d}-{end:%Y%m%d}.csv"')
    w = csv.writer(resp)
    w.writerow(["■店舗別"])
    w.writerow(["店舗", "来店数", "来店人数"])
    for r in by_loc:
        w.writerow([r["location__name"], r["visits"], r["members"]])
    w.writerow([])
    w.writerow(["■来店頻度分布（期間内の来店回数別 人数）"])
    for k, v in dist.items():
        w.writerow([k, v])
    return resp


def _friend_tags(visits, last_visit, registered_on, has_redeemable, today):
    """来店データから自動でタグ付け（手動タグの土台。将来セグメント配信に使う）。"""
    tags = []
    if registered_on and (today - registered_on).days <= 14 and visits <= 2:
        tags.append("新規")
    if visits >= 10:
        tags.append("ヘビー")
    elif visits >= 5:
        tags.append("常連")
    elif visits >= 1:
        tags.append("リピーター")
    if last_visit and (today - last_visit).days >= 21:
        tags.append("離反ぎみ")
    if has_redeemable:
        tags.append("特典保有")
    return tags


def _friend_rows(today):
    """友だち一覧の各行を、会員数によらず一定クエリ数（N+1なし）で組み立てる。

    来店・特典・現在pt・よく行く店舗・利用可能クーポンをそれぞれ1クエリで集計して
    member_id で引き当てる。※来店数と特典数を同一annotateで多重JOINすると来店数が
    水増しされるため、集計は関係ごとに分ける。
    """
    members = list(LineMember.objects.all())
    ids = [m.id for m in members]

    visit_agg = {r["card__member_id"]: r for r in
                 StampLog.objects.filter(card__member_id__in=ids)
                 .values("card__member_id")
                 .annotate(visits=Count("id"), last_visit=Max("stamped_on"))}

    reward_agg = {r["card__member_id"]: r for r in
                  Reward.objects.filter(card__member_id__in=ids)
                  .values("card__member_id")
                  .annotate(earned=Count("id"),
                            used=Count("id", filter=Q(status=Reward.STATUS_USED)))}

    redeemable_ids = set(Reward.objects.filter(
        card__member_id__in=ids, status=Reward.STATUS_ISSUED,
        valid_from__lte=today, expires_on__gte=today,
    ).values_list("card__member_id", flat=True))

    # 現在pt＝各会員の最新カードの stamp_count。
    latest_pt = {}
    for c in (StampCard.objects.filter(member_id__in=ids)
              .order_by("member_id", "-started_on", "-created_at")
              .values("member_id", "stamp_count")):
        latest_pt.setdefault(c["member_id"], c["stamp_count"])

    # よく行く店舗＝会員×店舗の来店数が最大の店舗名。
    fav = {}
    for r in (StampLog.objects.filter(card__member_id__in=ids)
              .values("card__member_id", "location__name")
              .annotate(c=Count("id")).order_by("card__member_id", "-c")):
        fav.setdefault(r["card__member_id"], r["location__name"])

    rows = []
    for m in members:
        v = visit_agg.get(m.id, {})
        rw = reward_agg.get(m.id, {})
        visits = v.get("visits", 0)
        last_visit = v.get("last_visit")
        registered = m.created_at.date() if m.created_at else None
        rows.append({
            "m": m,
            "visits": visits,
            "pt": latest_pt.get(m.id, 0),
            "earned": rw.get("earned", 0),
            "used": rw.get("used", 0),
            "last_visit": last_visit,
            "registered": registered,
            "fav_store": fav.get(m.id, ""),
            "tags": _friend_tags(visits, last_visit, registered,
                                 m.id in redeemable_ids, today),
        })
    return rows


_FRIEND_SORTS = {
    "recent": lambda r: (r["last_visit"] is not None, r["last_visit"]),
    "visits": lambda r: r["visits"],
    "pt": lambda r: r["pt"],
    "registered": lambda r: (r["registered"] is not None, r["registered"]),
}


@staff_member_required
def friends(request):
    """友だち（LINE会員）一覧。来店データの自動タグ＋ソート。CSVは ?export=csv。

    プラットフォームの「友だち中核エンティティ」を一覧化する画面。将来のセグメント配信の土台。
    """
    today = timezone.localdate()
    rows = _friend_rows(today)

    # 店舗フィルタ：その店舗に1回でも来店した友だちに絞る（表示中の集計は全店累計のまま）。
    loc_id = request.GET.get("loc") or ""
    if loc_id:
        visited_ids = set(StampLog.objects.filter(location_id=loc_id)
                          .values_list("card__member_id", flat=True))
        rows = [r for r in rows if r["m"].id in visited_ids]

    tag_filter = request.GET.get("tag") or ""
    if tag_filter:
        rows = [r for r in rows if tag_filter in r["tags"]]

    sort = request.GET.get("sort", "recent")
    keyfn = _FRIEND_SORTS.get(sort, _FRIEND_SORTS["recent"])
    rows.sort(key=keyfn, reverse=True)

    if request.GET.get("export") == "csv":
        return _friends_csv(rows)

    return render(request, "stamps/manage/friends.html", {
        "nav": "friends",
        "rows": rows,
        "total": len(rows),
        "sort": sort,
        "tag": tag_filter,
        "f_loc": loc_id,
        "locations": SalesLocation.objects.order_by("no", "name"),
        "all_tags": ["新規", "リピーター", "常連", "ヘビー", "離反ぎみ", "特典保有"],
    })


def _friends_csv(rows):
    resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    resp["Content-Disposition"] = 'attachment; filename="stamp_friends.csv"'
    w = csv.writer(resp)
    w.writerow(["名前", "LINEユーザーID", "登録日", "来店回数", "現在pt",
                "獲得特典", "使用特典", "最終来店", "よく行く店舗", "タグ"])
    for r in rows:
        w.writerow([
            r["m"].name, r["m"].line_user_id,
            r["registered"] or "", r["visits"], r["pt"],
            r["earned"], r["used"], r["last_visit"] or "",
            r["fav_store"], "/".join(r["tags"]),
        ])
    return resp


@staff_member_required
@require_http_methods(["GET", "POST"])
def richmenu(request):
    """スタンプ用リッチメニューの割当て管理（per-user 配信）。LINE公式UIにできないセグメント配信。"""
    from stamps import richmenu as rm

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "register":
            img = request.FILES.get("image")
            img_bytes = _normalize_richmenu_image(img) if img else None
            if img is not None and img_bytes is None:
                messages.error(request, "画像を読み込めませんでした（PNG/JPGの画像をアップロードしてください）。")
                return redirect("stamps:manage_richmenu")
            urls = {
                "menu": (request.POST.get("url_menu") or "").strip(),
                "status": (request.POST.get("url_status") or "").strip(),
                "map": (request.POST.get("url_map") or "").strip(),
                "stamp": (request.POST.get("url_stamp") or "").strip(),
            }
            urls = {k: v for k, v in urls.items() if v}
            ok, err = rm.register_stamp_menu(image_png_bytes=img_bytes, urls=urls)
            if ok:
                messages.success(request, "リッチメニューを登録/更新しました。スタンプ利用者へ自動で出ます。")
            else:
                messages.error(request, f"登録できませんでした：{err}")
            return redirect("stamps:manage_richmenu")
        if action == "reassign_all":
            targets = LineMember.objects.filter(stamp_cards__isnull=False).distinct()
            ok = sum(1 for m in targets if rm.assign(m))
            messages.success(request, f"スタンプ利用者 {targets.count()}人中 {ok}人に適用しました。")
        elif action in ("link", "unlink"):
            member = get_object_or_404(LineMember, pk=request.POST.get("member_id"))
            if action == "link":
                messages.success(request, f"{member.name} に適用{'しました' if rm.assign(member) else 'できませんでした（設定/通信を確認）'}。")
            else:
                rm.unassign(member)
                messages.success(request, f"{member.name} の割当てを解除しました（デフォルトに戻ります）。")
        return redirect("stamps:manage_richmenu")

    # スタンプ利用者（カードを持つ会員）＋割当て状況
    members = list(LineMember.objects.filter(stamp_cards__isnull=False).distinct()
                   .prefetch_related("stamp_richmenu_link"))
    rows = []
    n_linked = n_failed = 0
    for m in members:
        link = getattr(m, "stamp_richmenu_link", None)
        status = link.status if link else "none"
        if status == RichMenuLink.STATUS_LINKED:
            n_linked += 1
        elif status == RichMenuLink.STATUS_FAILED:
            n_failed += 1
        rows.append({"m": m, "link": link, "status": status})
    rows.sort(key=lambda r: (r["status"] != "linked", r["m"].name))

    menu = rm.get_or_create_stamp_menu()
    url_by_label = {a.get("action", {}).get("label"): a.get("action", {}).get("uri", "")
                    for a in (menu.areas or [])}
    return render(request, "stamps/manage/richmenu.html", {
        "nav": "richmenu",
        "configured": rm.is_configured(),
        "has_token": bool(rm.line_richmenu._token()),
        "menu_id": rm.stamp_richmenu_id(),
        "menu": menu,
        "url_menu": url_by_label.get("今週のメニュー") or rm.DEFAULT_URLS["menu"],
        "url_status": url_by_label.get("本日の出店状況") or rm.DEFAULT_URLS["status"],
        "url_map": url_by_label.get("販売場所") or rm.DEFAULT_URLS["map"],
        "url_stamp": url_by_label.get("スタンプ") or rm.stamp_url(),
        "auto_assign": getattr(settings, "STAMP_RICHMENU_AUTO_ASSIGN", True),
        "rows": rows,
        "total": len(rows),
        "n_linked": n_linked,
        "n_failed": n_failed,
    })


def _normalize_richmenu_image(uploaded):
    """アップロード画像をLINE仕様のPNG(2500x1686・≤1MB)に整える。失敗時 None。"""
    import io

    from PIL import Image
    try:
        im = Image.open(uploaded).convert("RGB")
    except Exception:
        return None
    if im.size != (2500, 1686):
        im = im.resize((2500, 1686))
    for _ in range(4):
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= 1_000_000:
            return data
        im = im.resize((int(im.width * 0.9), int(im.height * 0.9))).resize((2500, 1686))
    # それでも大きければJPEG化（LINEはJPEGも可）
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@staff_member_required
def richmenu_image(request):
    """登録済みリッチメニュー画像のプレビューを返す。"""
    from stamps.richmenu import get_or_create_stamp_menu
    menu = get_or_create_stamp_menu()
    if not menu.image_data:
        return HttpResponse(status=404)
    return HttpResponse(bytes(menu.image_data), content_type="image/png")


@staff_member_required
def locations(request):
    """既存SalesLocationを読み込み、スタンプのON/OFF・出店時間・QRトークンを管理。"""
    if request.method == "POST":
        loc = get_object_or_404(SalesLocation, pk=request.POST.get("location_id"))
        action = request.POST.get("action")
        if action == "regen_qr":
            loc.qr_stamp_token = _generate_qr_token()
            loc.save(update_fields=["qr_stamp_token"])
            messages.success(request, f"{loc.name}のスタンプQRを再発行しました（古いQRは無効になりました）。")
        else:
            loc.stamp_enabled = bool(request.POST.get("stamp_enabled"))
            loc.stamp_open_time = _parse_time(request.POST.get("stamp_open_time")) or None
            loc.stamp_close_time = _parse_time(request.POST.get("stamp_close_time")) or None
            loc.save(update_fields=["stamp_enabled", "stamp_open_time", "stamp_close_time"])
            messages.success(request, f"{loc.name}の設定を保存しました。")
        return redirect("stamps:manage_locations")

    rows = []
    for loc in SalesLocation.objects.order_by("-stamp_enabled", "no", "name"):
        open_t, close_t = location_open_window(loc)
        rows.append({
            "loc": loc,
            "open": open_t, "close": close_t,
            "custom_hours": bool(loc.stamp_open_time or loc.stamp_close_time),
            "stamp_url": _stamp_liff_url(loc.qr_stamp_token),
        })
    return render(request, "stamps/manage/locations.html", {
        "nav": "locations",
        "rows": rows,
        "default_open": DEFAULT_OPEN_TIME,
        "default_close": DEFAULT_CLOSE_TIME,
    })


def _stamp_liff_url(token):
    """スタンプQRに埋め込むURL。通常カメラで読んでも LINE 内で開くよう LIFF URL にする。"""
    from django.conf import settings
    return f"https://liff.line.me/{settings.STAMP_LIFF_ID}/stamp/{token}/"


def _parse_time(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


@staff_member_required
def location_qr_pdf(request):
    """有効店舗のスタンプQRをまとめてA4 PDFで出力（印刷・貼付用）。"""
    from sales import qr_pdf as base_qr_pdf

    locs = list(SalesLocation.objects.filter(stamp_enabled=True).order_by("no", "name"))
    if not locs:
        return HttpResponse("有効なスタンプ店舗がありません。", status=404)

    def build_url(token, kind):
        return _stamp_liff_url(token)

    pdf = _build_stamp_qr_pdf(base_qr_pdf, locs, build_url)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = 'attachment; filename="stamp_qr.pdf"'
    return resp


def _build_stamp_qr_pdf(base_qr_pdf, locations, build_url):
    """sales.qr_pdf の描画部品を流用し、スタンプ用1ページ/店舗のPDFを作る。"""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    base_qr_pdf._ensure_jp_font()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("ランチネット スタンプQR")
    BRAND = (1.0, 0.0, 0.0)  # ブランド赤
    for loc in locations:
        base_qr_pdf._draw_page(c, loc.name, "スタンプ", BRAND, build_url(loc.qr_stamp_token, "stamp"))
        c.showPage()
    c.save()
    return buf.getvalue()


def _qr_data_uri(url):
    """URLからQRのPNGを生成し data URI で返す（POPにインライン埋め込み）。"""
    import base64
    import io

    from sales import qr_pdf as base_qr_pdf
    img = base_qr_pdf._make_qr_image(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@staff_member_required
def location_pop(request, location_id):
    """店舗ごとの宣伝POP（QR込み・印刷用）。売場に貼って集客＋スタンプ導線にする。"""
    loc = get_object_or_404(SalesLocation, pk=location_id)
    url = _stamp_liff_url(loc.qr_stamp_token)
    RewardTier.ensure_defaults()
    tiers = list(RewardTier.objects.filter(active=True).order_by("threshold_pt"))

    # 10マスのモック・スタンプカード（最初の3個は押印済みの見本・5/10に特典タグ）。
    tier_by_pt = {t.threshold_pt: t for t in tiers}
    slots = []
    for n in range(1, 11):
        t = tier_by_pt.get(n)
        slots.append({"n": n, "on": n <= 3, "goal": t is not None,
                      "tag": reward_short_label(t) if t else ""})

    # 背景画像のキャッシュ無効化用バージョン（画像を更新したら自動で再取得される）。
    import os
    from django.conf import settings as dj_settings
    bg_path = os.path.join(dj_settings.BASE_DIR, "static", "images", "pop2", "full_v2.png")
    try:
        bg_version = int(os.path.getmtime(bg_path))
    except OSError:
        bg_version = 0

    return render(request, "stamps/manage/pop.html", {
        "loc": loc,
        "qr": _qr_data_uri(url),
        "url": url,
        "tiers": tiers,
        "slots": slots,
        "bg_version": bg_version,
    })


@staff_member_required
def location_stand_pop(request, location_id):
    """卓上スタンド版POP（A5横1枚にA6を2面付け・QR込み・印刷用）。

    完成画像 stand_v3.png を背景にまるごと使い、白いQR空枠へ店舗別の本物QRを重ねる
    （A4版 location_pop と同じ方式）。真ん中で切ればA6の卓上POPが2枚取れる。
    """
    loc = get_object_or_404(SalesLocation, pk=location_id)
    url = _stamp_liff_url(loc.qr_stamp_token)

    import os
    from django.conf import settings as dj_settings
    bg_path = os.path.join(dj_settings.BASE_DIR, "static", "images", "pop2", "stand_v4.png")
    try:
        bg_version = int(os.path.getmtime(bg_path))
    except OSError:
        bg_version = 0

    return render(request, "stamps/manage/pop_stand.html", {
        "loc": loc,
        "qr": _qr_data_uri(url),
        "url": url,
        "bg_version": bg_version,
    })


def _parse_pt(raw):
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None


def _apply_tier_fields(request, tier):
    """POSTの共通フィールド（特典名・種類・割引額・基準コスト・有効）を tier に反映。"""
    tier.label = (request.POST.get("label") or tier.label or "").strip()
    kind = request.POST.get("kind")
    if kind in (RewardTier.KIND_DISCOUNT, RewardTier.KIND_FREE):
        tier.kind = kind
    try:
        tier.discount_yen = max(0, int(request.POST.get("discount_yen") or 0))
        tier.benefit_cost = max(0, int(request.POST.get("benefit_cost") or 0))
    except (TypeError, ValueError):
        pass
    # 弁当無料は割引額の概念がないので0に正規化。
    if tier.kind == RewardTier.KIND_FREE:
        tier.discount_yen = 0
    tier.active = bool(request.POST.get("active"))


def _rewards_save(request):
    tier = get_object_or_404(RewardTier, pk=request.POST.get("tier_id"))
    new_pt = _parse_pt(request.POST.get("threshold_pt"))
    if new_pt and new_pt != tier.threshold_pt:
        if RewardTier.objects.filter(threshold_pt=new_pt).exclude(pk=tier.pk).exists():
            messages.error(request, f"到達pt {new_pt} は他の段階と重複しています。")
            return
        tier.threshold_pt = new_pt
    _apply_tier_fields(request, tier)
    tier.save()
    RewardTier.sync_cap_flag()
    messages.success(request, f"{tier.threshold_pt}ptの特典を保存しました。")


def _rewards_add(request):
    new_pt = _parse_pt(request.POST.get("threshold_pt"))
    if not new_pt:
        messages.error(request, "到達ptは1以上の数値で入力してください。")
        return
    if RewardTier.objects.filter(threshold_pt=new_pt).exists():
        messages.error(request, f"到達pt {new_pt} の段階は既にあります。編集してください。")
        return
    tier = RewardTier(threshold_pt=new_pt)
    _apply_tier_fields(request, tier)
    if not tier.label:
        tier.label = f"{new_pt}pt特典"
    tier.save()
    RewardTier.sync_cap_flag()
    messages.success(request, f"到達pt {new_pt} の段階を追加しました。")


def _rewards_delete(request):
    tier = get_object_or_404(RewardTier, pk=request.POST.get("tier_id"))
    pt = tier.threshold_pt
    try:
        tier.delete()
    except ProtectedError:
        messages.error(request,
                       f"{pt}ptは発行済みの特典があるため削除できません。『有効』を外して無効化してください。")
        return
    RewardTier.sync_cap_flag()
    messages.success(request, f"{pt}ptの段階を削除しました。")


def _rewards_config(request):
    cfg = StampConfig.get_solo()
    try:
        cfg.card_validity_days = max(1, int(request.POST.get("card_validity_days")
                                            or cfg.card_validity_days))
        cfg.reward_validity_days = max(1, int(request.POST.get("reward_validity_days")
                                              or cfg.reward_validity_days))
    except (TypeError, ValueError):
        messages.error(request, "有効日数は1以上の数値で入力してください。")
        return
    cfg.reward_starts_next_day = bool(request.POST.get("reward_starts_next_day"))
    # スタンプ2倍イベント：雨の日（当日セット）／連続来店。
    cfg.rain_bonus_date = timezone.localdate() if request.POST.get("rain_bonus_today") else None
    cfg.streak_bonus_enabled = bool(request.POST.get("streak_bonus_enabled"))
    try:
        cfg.streak_bonus_days = max(1, int(request.POST.get("streak_bonus_days")
                                           or cfg.streak_bonus_days))
    except (TypeError, ValueError):
        messages.error(request, "連続来店の節目は1以上の数値で入力してください。")
        return
    cfg.save()
    messages.success(request, "運用ルールを保存しました。")


_REWARD_ACTIONS = {
    "add": _rewards_add,
    "delete": _rewards_delete,
    "config": _rewards_config,
    "save": _rewards_save,
}


@staff_member_required
@require_http_methods(["GET", "POST"])
def rewards(request):
    """特典段階の編集・追加・削除、運用ルール（有効日数）の編集、発行/使用状況とコスト集計。"""
    RewardTier.ensure_defaults()
    if request.method == "POST":
        handler = _REWARD_ACTIONS.get(request.POST.get("action") or "save", _rewards_save)
        handler(request)
        return redirect("stamps:manage_rewards")

    # 段階ごとの発行・使用・実コスト（Reward.threshold_pt のスナップショット基準）。
    agg = (Reward.objects.values("threshold_pt")
           .annotate(issued=Count("id"),
                     used=Count("id", filter=Q(status=Reward.STATUS_USED)),
                     cost=Sum("benefit_cost", filter=Q(status=Reward.STATUS_USED))))
    stats = {a["threshold_pt"]: a for a in agg}

    cap = RewardTier.effective_cap()
    tier_rows = []
    total_cost = 0
    for t in RewardTier.objects.all().order_by("threshold_pt"):
        s = stats.get(t.threshold_pt, {})
        cost = s.get("cost") or 0
        total_cost += cost
        tier_rows.append({
            "tier": t,
            "issued": s.get("issued") or 0,
            "used": s.get("used") or 0,
            "cost": cost,
            "is_cap": t.active and t.threshold_pt == cap,
            "deletable": (s.get("issued") or 0) == 0,   # 発行済みが無ければ削除可
        })

    cfg = StampConfig.get_solo()
    return render(request, "stamps/manage/rewards.html", {
        "nav": "rewards",
        "tier_rows": tier_rows,
        "total_cost": total_cost,
        "cap_pt": cap,
        "config": cfg,
        "rain_on_today": cfg.is_rain_bonus_on(timezone.localdate()),
        "today": timezone.localdate(),
        "kind_discount": RewardTier.KIND_DISCOUNT,
        "kind_free": RewardTier.KIND_FREE,
    })
