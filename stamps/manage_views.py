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
from datetime import datetime, time as dtime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Max, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from sales.models import DailyReport, SalesLocation, _generate_qr_token
from reservations.models import LineMember
from stamps.models import (
    FriendInsightSnapshot, FriendTagConfig, MemberTag, MemberTagLink,
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


def _nice_axis(dmin, dmax, target_ticks=5):
    """データの最小・最大から、キリの良い(y_min, y_max, step)を算出する（自動ズーム用）。

    上下に少し余白を取り、目盛り幅は 1/2/5×10^k に丸める。両端も step の倍数へ丸める。
    """
    import math
    if dmax <= dmin:
        dmax = dmin + 1
    span = dmax - dmin
    pad = span * 0.15
    lo, hi = dmin - pad, dmax + pad
    raw_step = (hi - lo) / target_ticks
    mag = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
    step = next(m * mag for m in (1, 2, 5, 10) if raw_step <= m * mag)
    step = max(1, int(step))          # 整数カウント軸なので最小刻みは1（0除算・0刻み防止）
    y_min = math.floor(lo / step) * step
    y_max = math.ceil(hi / step) * step
    return int(y_min), int(y_max), step


def _nice_upper(dmax, target_ticks=4):
    """0..dmax の「キリの良い上限と目盛り幅」を返す（右軸＝カウント用）。"""
    import math
    if dmax <= 0:
        return 1, 1
    raw = dmax / target_ticks
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next(m * mag for m in (1, 2, 5, 10) if raw <= m * mag)
    step = max(1, int(step))          # 整数カウント軸なので最小刻みは1
    top = math.ceil(dmax / step) * step
    return int(top), step


def _xlabel_keep_class(i, n):
    """X軸ラベルの間引き用クラス。CSSのメディアクエリで画面幅ごとに表示/非表示を切替える。

    - edge：両端（常に表示）／k2：偶数番（中画面）／k6：6個おき（小画面）
    PCは全ラベル表示、狭い画面はCSSで端折る（サーバーは全ラベルを出す）。
    """
    c = ["xl"]
    if i == 0 or i == n - 1:
        c.append("edge")
    if i % 2 == 0:
        c.append("k2")
    if i % 6 == 0:
        c.append("k6")
    return " ".join(c)


def _svg_combo_chart(rows, line, bars, width=1160, height=300):
    """2軸コンボチャート：左軸＝折れ線（友だち推移・自動ズーム）／右軸＝グループ棒（日次増減）。

    rows: [{"label", <line.key>, <bar.key>...}, ...]（日付昇順）。棒の値は None 可（初日など）。
    line: {"key","color","name"}     … 折れ線（実質有効友だち）
    bars: [{"key","color","name"}, ...] … 棒（新規追加・新規ブロック）
    横幅は広め（既定1160）。SVGは width:100% でコンテンツ幅いっぱいに伸縮する。
    """
    pad_l, pad_r, pad_t, pad_b = 48, 40, 14, 30
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(rows)
    slot = iw / n

    # 左軸（折れ線＝友だち）：データ範囲へ自動ズーム。
    lvals = [r[line["key"]] for r in rows]
    ly_min, ly_max, ly_step = _nice_axis(min(lvals), max(lvals))
    lspan = (ly_max - ly_min) or 1

    # 右軸（棒＝日次増減）：0起点でキリの良い上限。
    bvals = [r[b["key"]] for r in rows for b in bars if r.get(b["key"]) is not None]
    r_max, r_step = _nice_upper(max(bvals + [1]))
    rspan = r_max or 1

    def cx(i):                       # スロット中心（折れ線・棒・ラベルを揃える）
        return round(pad_l + slot * i + slot / 2, 1)

    def ly(v):
        return round(pad_t + ih - ih * (v - ly_min) / lspan, 1)

    def ry_h(v):                     # 右軸：棒の高さ
        return round(ih * v / rspan, 1)

    # 棒（右軸・日次増減）
    nb = len(bars)
    bw = min(slot * 0.6 / nb, 10)
    group_w = bw * nb
    bar_out = []
    for bi, b in enumerate(bars):
        bb = []
        for i, r in enumerate(rows):
            v = r.get(b["key"])
            if v is None:
                continue
            h = ry_h(v)
            x = pad_l + slot * i + (slot - group_w) / 2 + bw * bi
            bb.append({"x": round(x, 1), "y": round(pad_t + ih - h, 1),
                       "w": round(bw, 1), "h": h, "v": v, "label": r["label"]})
        bar_out.append({"name": b["name"], "color": b["color"], "bars": bb})

    # 折れ線（左軸・友だち推移）
    pts = [{"x": cx(i), "y": ly(r[line["key"]]), "v": r[line["key"]]} for i, r in enumerate(rows)]
    line_out = {"name": line["name"], "color": line["color"],
                "points": " ".join(f"{p['x']},{p['y']}" for p in pts), "dots": pts}

    lyticks = [{"v": v, "y": ly(v)} for v in range(ly_min, ly_max + 1, ly_step)]
    ryticks = [{"v": v, "y": round(pad_t + ih - ry_h(v), 1)} for v in range(0, r_max + 1, r_step)]
    # 全ラベルを出し、間引きはCSS（画面幅）に任せる＝PCは30日全部、狭い画面は端折る。
    xlabels = [{"x": cx(i), "t": r["label"], "cls": _xlabel_keep_class(i, n)}
               for i, r in enumerate(rows)]
    return {
        "width": width, "height": height, "line": line_out, "bars": bar_out,
        "lyticks": lyticks, "ryticks": ryticks, "xlabels": xlabels,
        "axis_x0": pad_l, "axis_x1": width - pad_r,
        "axis_y0": pad_t, "axis_y1": height - pad_b,
    }


def _friend_delta_rows(snaps, prev):
    """累計スナップショットから日次増減（新規追加・新規ブロック）を計算する。

    followers/blocks は累計（単調増加）なので、前日との差＝その日の新規イベント数。
    prev は期間開始日より前の直近 ready スナップショット（あれば初日も差分が出せる）。
    """
    seq = ([prev] if prev else []) + list(snaps)
    rows = []
    for i in range(1, len(seq)):
        cur, before = seq[i], seq[i - 1]
        rows.append({
            "date": cur.date,
            "label": f"{cur.date.month}/{cur.date.day}",
            "adds": max(cur.followers - before.followers, 0),
            "blocks": max(cur.blocks - before.blocks, 0),
        })
    return rows


def _friend_insight_context(start, end):
    """全友だち母数のカード値＋推移グラフのコンテキストを組む。

    - base_snap: 期間endに最も近い ready スナップショット（母数・カードの現在値に使う）
    - chart: 期間内 ready スナップショットの友だち数/ブロック数の推移SVG（2点未満はNone）
    """
    base_snap = FriendInsightSnapshot.latest_ready(on_or_before=end)
    snaps = list(FriendInsightSnapshot.objects
                 .filter(status=FriendInsightSnapshot.STATUS_READY,
                         date__gte=start, date__lte=end)
                 .order_by("date"))
    # 推移＝2軸コンボ：左軸に友だち折れ線（ブロック線は外した）、右軸に日次増減の棒を統合。
    combo = None
    if len(snaps) >= 2:
        prev = (FriendInsightSnapshot.objects
                .filter(status=FriendInsightSnapshot.STATUS_READY, date__lt=start)
                .order_by("-date").first())
        delta_by_date = {d["date"]: d for d in _friend_delta_rows(snaps, prev)}
        rows = []
        for s in snaps:
            d = delta_by_date.get(s.date)
            rows.append({
                "label": f"{s.date.month}/{s.date.day}",
                "friends": s.effective_friends,
                "adds": d["adds"] if d else None,     # 初日など差分が出せない日は None（棒を描かない）
                "blocks": d["blocks"] if d else None,
            })
        combo = _svg_combo_chart(
            rows,
            {"key": "friends", "color": "#ff0000", "name": "実質有効友だち"},
            [{"key": "adds", "color": "#2e9e5b", "name": "新規追加"},
             {"key": "blocks", "color": "#e8830c", "name": "新規ブロック"}],
        )
    return {"friend_snap": base_snap, "friend_combo": combo, "friend_points": len(snaps)}


@staff_member_required
def dashboard(request):
    """KPIサマリー：参加率（全友だちベース）・友だち/ブロック統計とその推移・
    各pt達成率・来店数・特典発行/使用・推定特典コスト。"""
    start, end = _period(request)
    loc_id = request.GET.get("loc") or ""

    logs = _logs_in_period(start, end, loc_id)
    visits = logs.count()
    participant_ids = set(logs.values_list("card__member_id", flat=True))
    participants = len(participant_ids)

    # 参加率の母数＝全友だち（実質有効友だち数）。旧・登録者ベース(LineMember数)は廃止。
    fi = _friend_insight_context(start, end)
    base_snap = fi["friend_snap"]
    friend_base = base_snap.effective_friends if base_snap else 0
    if friend_base:
        # 母数取得ズレで参加者が上回った場合は100%でクランプ。
        participation_rate = min(round(participants / friend_base * 100, 1), 100.0)
    else:
        participation_rate = None  # 集計待ち（スナップショット未取得）

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
        "friend_base": friend_base,
        "participation_rate": participation_rate,
        "friend_snap": base_snap,
        "friend_combo": fi["friend_combo"],
        "friend_points": fi["friend_points"],
        "n_cards": n_cards,
        "reach": reach,
        "issued": issued,
        "used": used,
        "est_cost": est_cost,
    })


def _pct(numer, denom):
    """割合（％・小数1桁）。分母0は None（画面で「-」表示）。"""
    return round(numer / denom * 100, 1) if denom else None


def _sales_correlation_rows(start, end, loc_no=None, stamp_only=True):
    """売上×スタンプ相関の行を作る（日付×販売場所）。

    - 売上系（持参/販売/残/総売上）は sales.DailyReport（同日・同店で1行）。
    - スタンプ利用者数は StampLog を (stamped_on, location.no) で distinct member 集計し突き合わせる。
    - 結合キー：DailyReport.location_no == SalesLocation.no。
    """
    enabled_nos = set(SalesLocation.objects.filter(stamp_enabled=True)
                      .values_list("no", flat=True))

    reports = DailyReport.objects.filter(date__gte=start, date__lte=end)
    if loc_no is not None:
        reports = reports.filter(location_no=loc_no)
    elif stamp_only:
        reports = reports.filter(location_no__in=enabled_nos)
    reports = reports.order_by("-date", "location_no")

    # スタンプ利用者数＝(日付, 店no) ごとの distinct 会員数。2クエリで一括取得。
    usage = (StampLog.objects
             .filter(stamped_on__gte=start, stamped_on__lte=end)
             .values("stamped_on", "location__no")
             .annotate(u=Count("card__member_id", distinct=True)))
    usage_map = {(r["stamped_on"], r["location__no"]): r["u"] for r in usage}

    rows = []
    for rep in reports:
        brought = int(rep.total_quantity or 0)
        sold = int(rep.total_sales_quantity or 0)
        remaining = int(rep.total_remaining or 0)
        users = usage_map.get((rep.date, rep.location_no), 0)
        rows.append({
            "date": rep.date,
            "location": rep.location,
            "location_no": rep.location_no,
            "stamp_enabled": rep.location_no in enabled_nos,
            "brought": brought,
            "sold": sold,
            "remaining": remaining,
            "waste_rate": _pct(remaining, brought),        # 廃棄率＝残数÷持参数
            "stamp_users": users,
            "stamp_rate": _pct(users, sold),               # 利用割合＝利用者数÷販売数
            "revenue": int(rep.total_revenue or 0),
        })
    return rows


@staff_member_required
def sales_correlation(request):
    """売上×スタンプ相関：日付×販売場所で持参/販売/残/廃棄率/スタンプ利用/総売上を並べる。

    期間・店舗フィルタ＋「スタンプ対応店のみ」トグル（既定ON）。CSVは ?export=csv。
    """
    start, end = _period(request)
    loc_id = request.GET.get("loc") or ""
    # 店舗フィルタは SalesLocation.id → no に変換して DailyReport と結合。
    loc_no = None
    if loc_id:
        loc = SalesLocation.objects.filter(id=loc_id).first()
        loc_no = loc.no if loc else -1  # 該当なしは -1（0件に）
    # トグル：未指定は既定ON。store フィルタ時は全店（loc優先）。
    stamp_only = request.GET.get("stamp_only", "1") == "1"

    rows = _sales_correlation_rows(start, end, loc_no=loc_no, stamp_only=stamp_only)

    if request.GET.get("export") == "csv":
        return _sales_correlation_csv(rows, start, end)

    return render(request, "stamps/manage/sales_correlation.html", {
        "nav": "sales_correlation",
        "start": start, "end": end, "f_loc": loc_id, "stamp_only": stamp_only,
        "locations": SalesLocation.objects.order_by("no", "name"),
        "rows": rows,
        "total": len(rows),
    })


def _sales_correlation_csv(rows, start, end):
    resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    resp["Content-Disposition"] = (
        f'attachment; filename="sales_stamp_correlation_{start:%Y%m%d}-{end:%Y%m%d}.csv"')
    w = csv.writer(resp)
    w.writerow(["日付", "販売場所", "持参数", "販売数", "残数", "廃棄率(%)",
                "スタンプ利用者数", "スタンプ利用割合(%)", "総売上"])
    for r in rows:
        w.writerow([
            r["date"].strftime("%Y-%m-%d"), r["location"],
            r["brought"], r["sold"], r["remaining"],
            "" if r["waste_rate"] is None else r["waste_rate"],
            r["stamp_users"],
            "" if r["stamp_rate"] is None else r["stamp_rate"],
            r["revenue"],
        ])
    return resp


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


def _reward_usage_series(start, end):
    """特典を「使ったタイミング」の推移。used_at のローカル日付でバケットする。

    期間が長い（62日超）ときは週次（月曜起点）に丸めて行数を抑える。戻り値の rows は
    期間内を欠けなく埋めた連続系列（使用0の日も0で並ぶ＝谷が見える）。
    """
    start_dt = timezone.make_aware(datetime.combine(start, dtime.min))
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), dtime.min))
    used = (Reward.objects
            .filter(status=Reward.STATUS_USED, used_at__gte=start_dt, used_at__lt=end_dt)
            .only("used_at"))

    weekly = (end - start).days > 62
    counts = {}
    for r in used:
        d = timezone.localtime(r.used_at).date()
        key = d - timedelta(days=d.weekday()) if weekly else d   # 週次は月曜へ丸める
        counts[key] = counts.get(key, 0) + 1

    step = timedelta(days=7 if weekly else 1)
    cur = (start - timedelta(days=start.weekday())) if weekly else start
    rows = []
    while cur <= end:
        rows.append({"date": cur, "count": counts.get(cur, 0), "weekly": weekly})
        cur += step
    total = sum(counts.values())
    peak = max((row["count"] for row in rows), default=0)
    return rows, total, peak, weekly


@staff_member_required
def analytics(request):
    """参加率・達成率・来店頻度分布・店舗別比較・特典使用の推移。CSVは ?export=csv。"""
    start, end = _period(request)
    logs = _logs_in_period(start, end)
    usage_rows, usage_total, usage_peak, usage_weekly = _reward_usage_series(start, end)

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
        return _analytics_csv(by_loc, dist, usage_rows, usage_weekly, start, end)

    return render(request, "stamps/manage/analytics.html", {
        "nav": "analytics",
        "start": start, "end": end,
        "by_loc": list(by_loc),
        "dist": dist,
        "active_members": per_member.count(),
        "usage_rows": usage_rows,
        "usage_total": usage_total,
        "usage_peak": usage_peak,
        "usage_weekly": usage_weekly,
    })


def _analytics_csv(by_loc, dist, usage_rows, usage_weekly, start, end):
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
    w.writerow([])
    w.writerow([f"■特典使用の推移（{'週次・週の月曜' if usage_weekly else '日次'}／使用日ベース）"])
    w.writerow(["日付", "使用数"])
    for row in usage_rows:
        w.writerow([f'{row["date"]:%Y-%m-%d}', row["count"]])
    return resp


def _friend_tags(visits, last_visit, registered_on, has_redeemable, today, cfg):
    """来店データから自動でタグ付け。しきい値は FriendTagConfig（画面で編集可）から取る。"""
    tags = []
    if (registered_on and (today - registered_on).days <= cfg.new_within_days
            and visits <= cfg.new_max_visits):
        tags.append("新規")
    if visits >= cfg.heavy_min_visits:
        tags.append("ヘビー")
    elif visits >= cfg.regular_min_visits:
        tags.append("常連")
    elif visits >= cfg.repeater_min_visits:
        tags.append("リピーター")
    if last_visit and (today - last_visit).days >= cfg.dormant_days:
        tags.append("離反ぎみ")
    if has_redeemable:
        tags.append("特典保有")
    return tags


def _friend_rows(today, cfg=None):
    """友だち一覧の各行を、会員数によらず一定クエリ数（N+1なし）で組み立てる。

    来店・特典・現在pt・よく行く店舗・利用可能クーポン・手動タグをそれぞれ1クエリで集計して
    member_id で引き当てる。※来店数と特典数を同一annotateで多重JOINすると来店数が
    水増しされるため、集計は関係ごとに分ける。自動タグのしきい値は FriendTagConfig 参照。
    """
    cfg = cfg or FriendTagConfig.get_solo()
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

    # 使用履歴（いつ・何の特典を使ったか）＝会員ごとに1クエリでまとめて引く（N+1なし）。
    used_history = {}
    for rw in (Reward.objects.filter(card__member_id__in=ids,
                                     status=Reward.STATUS_USED, used_at__isnull=False)
               .values("card__member_id", "used_at", "label")
               .order_by("card__member_id", "-used_at")):
        used_history.setdefault(rw["card__member_id"], []).append(rw)

    # 手動タグ（有効なもの）＝会員ごとに1クエリでまとめて引く（N+1なし）。
    manual_tags = {}
    for lk in (MemberTagLink.objects.filter(member_id__in=ids, tag__active=True)
               .values("member_id", "tag_id", "tag__name", "tag__color", "tag__order")
               .order_by("member_id", "tag__order", "tag_id")):
        manual_tags.setdefault(lk["member_id"], []).append({
            "id": lk["tag_id"], "name": lk["tag__name"], "color": lk["tag__color"]})

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
            "used_history": used_history.get(m.id, []),
            "manual_tags": manual_tags.get(m.id, []),
            "manual_ids": {mt["id"] for mt in manual_tags.get(m.id, [])},
            "tags": _friend_tags(visits, last_visit, registered,
                                 m.id in redeemable_ids, today, cfg),
        })
    return rows


_FRIEND_SORTS = {
    "recent": lambda r: (r["last_visit"] is not None, r["last_visit"]),
    "visits": lambda r: r["visits"],
    "pt": lambda r: r["pt"],
    "registered": lambda r: (r["registered"] is not None, r["registered"]),
    # 特典の使用：使った回数の多い順／最後に使った日時の新しい順（used_history は降順なので先頭が最新）。
    "used": lambda r: r["used"],
    "lastused": lambda r: (bool(r["used_history"]),
                           r["used_history"][0]["used_at"] if r["used_history"] else None),
}


def _friends_assign_tags(request):
    """1人の友だちの手動タグを、チェック状態に合わせて付け外し（有効タグのみ対象）。"""
    member = get_object_or_404(LineMember, pk=request.POST.get("member_id"))
    active = {t.id: t for t in MemberTag.objects.filter(active=True)}
    chosen = {int(x) for x in request.POST.getlist("tag_ids") if x.isdigit()} & set(active)
    existing = set(MemberTagLink.objects.filter(member=member, tag__active=True)
                  .values_list("tag_id", flat=True))
    for tid in chosen - existing:
        MemberTagLink.objects.get_or_create(member=member, tag=active[tid])
    remove = existing - chosen
    if remove:
        MemberTagLink.objects.filter(member=member, tag_id__in=remove).delete()
    messages.success(request, f"{member.name} のタグを更新しました。")


def _friends_redirect(request):
    """タグ更新後、元の並び替え・絞り込み状態を保ったまま友だち一覧へ戻す。"""
    params = {k: request.POST.get(k, "") for k in ("sort", "tag", "loc")}
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v)
    url = reverse("stamps:manage_friends")
    return redirect(f"{url}?{qs}" if qs else url)


@staff_member_required
@require_http_methods(["GET", "POST"])
def friends(request):
    """友だち（LINE会員）一覧。自動タグ＋手動タグ＋ソート。CSVは ?export=csv。

    プラットフォームの「友だち中核エンティティ」を一覧化する画面。将来のセグメント配信の土台。
    手動タグの付け外しは各行から（POST）。しきい値・タグ定義は「タグ設定」画面で編集する。
    """
    if request.method == "POST":
        _friends_assign_tags(request)
        return _friends_redirect(request)

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
        rows = [r for r in rows
                if tag_filter in r["tags"]
                or tag_filter in {mt["name"] for mt in r["manual_tags"]}]

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
        "manual_tag_defs": list(MemberTag.objects.filter(active=True)),
    })


def _friends_csv(rows):
    resp = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    resp["Content-Disposition"] = 'attachment; filename="stamp_friends.csv"'
    w = csv.writer(resp)
    w.writerow(["名前", "LINEユーザーID", "登録日", "来店回数", "現在pt",
                "獲得特典", "使用特典", "最終来店", "よく行く店舗", "自動タグ", "手動タグ"])
    for r in rows:
        w.writerow([
            r["m"].name, r["m"].line_user_id,
            r["registered"] or "", r["visits"], r["pt"],
            r["earned"], r["used"], r["last_visit"] or "",
            r["fav_store"], "/".join(r["tags"]),
            "/".join(mt["name"] for mt in r["manual_tags"]),
        ])
    return resp


# --- タグ設定（自動タグのしきい値＋手動タグの定義） -----------------------------
_TAGCFG_FIELDS = [
    "new_within_days", "new_max_visits", "repeater_min_visits",
    "regular_min_visits", "heavy_min_visits", "dormant_days",
]


def _tagcfg_save(request):
    cfg = FriendTagConfig.get_solo()
    try:
        for f in _TAGCFG_FIELDS:
            setattr(cfg, f, max(0, int(request.POST.get(f) or getattr(cfg, f))))
    except (TypeError, ValueError):
        messages.error(request, "しきい値は0以上の数値で入力してください。")
        return
    cfg.save()
    messages.success(request, "自動タグのしきい値を保存しました。")


def _memtag_apply(request, tag):
    tag.color = request.POST.get("color") or tag.color
    tag.active = bool(request.POST.get("active"))
    try:
        tag.order = max(0, int(request.POST.get("order") or 0))
    except (TypeError, ValueError):
        tag.order = 0


def _memtag_add(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "タグ名を入力してください。")
        return
    if MemberTag.objects.filter(name=name).exists():
        messages.error(request, f"「{name}」は既にあります。")
        return
    tag = MemberTag(name=name)
    _memtag_apply(request, tag)
    tag.save()
    messages.success(request, f"手動タグ「{name}」を追加しました。")


def _memtag_save(request):
    tag = get_object_or_404(MemberTag, pk=request.POST.get("tag_id"))
    name = (request.POST.get("name") or "").strip()
    if name and name != tag.name:
        if MemberTag.objects.filter(name=name).exclude(pk=tag.pk).exists():
            messages.error(request, f"「{name}」は既にあります。")
            return
        tag.name = name
    _memtag_apply(request, tag)
    tag.save()
    messages.success(request, f"手動タグ「{tag.name}」を保存しました。")


def _memtag_delete(request):
    tag = get_object_or_404(MemberTag, pk=request.POST.get("tag_id"))
    name = tag.name
    tag.delete()   # 割当て（MemberTagLink）はCASCADEで一緒に消える
    messages.success(request, f"手動タグ「{name}」を削除しました。")


_TAG_ACTIONS = {
    "config": _tagcfg_save,
    "add": _memtag_add,
    "save": _memtag_save,
    "delete": _memtag_delete,
}


@staff_member_required
@require_http_methods(["GET", "POST"])
def tag_settings(request):
    """友だちタグの設定：自動タグのしきい値と、手動タグ（定義）の追加・編集・削除。"""
    if request.method == "POST":
        _TAG_ACTIONS.get(request.POST.get("action") or "config", _tagcfg_save)(request)
        return redirect("stamps:manage_tag_settings")

    counts = {r["tag_id"]: r["c"] for r in
              MemberTagLink.objects.values("tag_id").annotate(c=Count("id"))}
    tag_rows = [{"t": t, "count": counts.get(t.id, 0)}
                for t in MemberTag.objects.all()]
    return render(request, "stamps/manage/tag_settings.html", {
        "nav": "tag_settings",
        "cfg": FriendTagConfig.get_solo(),
        "tag_rows": tag_rows,
        "colors": MemberTag.COLOR_CHOICES,
    })


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
