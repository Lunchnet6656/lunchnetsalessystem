"""メニューごとの過去実績（メニュー表の木曜日のベース設定数づくり用）。

メニュー表（.xlsx・マクロ不可）から Excel の「Webから」で取り込み、メニュー名で引いて横に表示する。
同じメニューは数か月おきに1週間ずつ出るので、日付が10日以上空いたら別の「出た回」として数える。
お店の数字は日計表（DailyReportEntry）、別注は受注（OrderItem）の全件から（「全体注文数に含める」に関係なく、
作った数を見たいため）。別注は注文どおり作るので
廃棄率・完売率はお店の分だけで出す。
"""
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum

from orders.models import OrderItem
from sales.models import DailyReportEntry, SalesLocation

RUN_GAP_DAYS = 9          # これより空いたら別の回
HISTORY_SINCE = "2025-09-01"
MENU_MAX_NO = 10          # No.11 は大盛りご飯なので除く

# 「前回」は前回出た週（通常5日）の1日あたり平均
COLUMNS = [
    "メニュー", "前回の週", "日数", "1日の持参", "1日の販売", "1日の別注", "1日の合計",
    "廃棄率", "完売率", "通算の平均持参", "通算の平均販売", "通算の平均廃棄率", "通算の回数",
]


def _excluded_locations():
    names = set(getattr(settings, "MENU_HISTORY_EXCLUDED_LOCATIONS", []))
    # 配達は注文どおりに持っていくので、売れ残りの傾向を見る対象にしない
    names |= set(SalesLocation.objects.filter(type="配達").values_list("name", flat=True))
    return names


def _runs(days):
    """[(date, q, s, r, lines, sold_out_lines)] を日付順に受け取り、出た回ごとに分ける。"""
    runs, current = [], []
    for row in days:
        if current and (row[0] - current[-1][0]).days > RUN_GAP_DAYS:
            runs.append(current)
            current = []
        current.append(row)
    if current:
        runs.append(current)
    return runs


def _orders_by_name_and_date(names, since):
    totals = defaultdict(int)
    rows = (
        OrderItem.objects
        .filter(product_name__in=names, order__delivery_date__gte=since)
        .values("product_name", "order__delivery_date")
        .annotate(q=Sum("quantity"))
    )
    for row in rows:
        totals[(row["product_name"].strip(), row["order__delivery_date"])] += row["q"] or 0
    first = OrderItem.objects.order_by("order__delivery_date").values_list("order__delivery_date", flat=True).first()
    return totals, first


def build_menu_history(today):
    """メニューごとに「前回出た週の1日平均」と「通算の1日平均」を返す。today 当日は集計途中なので含めない。"""
    per_day = (
        DailyReportEntry.objects
        .filter(report__date__gte=HISTORY_SINCE, report__date__lt=today,
                product_no__lte=MENU_MAX_NO, quantity__gt=0)
        .exclude(report__location__in=_excluded_locations())
        .values("product", "report__date")
        .annotate(
            q=Sum("quantity"), s=Sum("sales_quantity"), r=Sum("remaining_number"),
            lines=Count("id"),
            sold_out=Count("id", filter=Q(sold_out=True) | Q(remaining_number__lte=0)),
        )
    )
    by_name = defaultdict(list)
    for row in per_day:
        name = (row["product"] or "").strip()
        if name:
            by_name[name].append((row["report__date"], row["q"], row["s"], row["r"], row["lines"], row["sold_out"]))

    orders, orders_since = _orders_by_name_and_date(list(by_name), HISTORY_SINCE)

    result = []
    for name, days in by_name.items():
        days.sort()
        runs = _runs(days)
        last = runs[-1]
        n = len(last)
        q = sum(d[1] for d in last)
        s = sum(d[2] for d in last)
        r = sum(d[3] for d in last)
        lines = sum(d[4] for d in last)
        sold_out = sum(d[5] for d in last)
        start, end = last[0][0], last[-1][0]
        # 受注アプリを使い始める前の回は、別注が「無い」のではなく「分からない」
        if orders_since and start >= orders_since:
            extra = sum(orders.get((name, start + timedelta(days=i)), 0) for i in range((end - start).days + 1))
            extra_per_day = round(extra / n)
            total_per_day = round(s / n) + extra_per_day
        else:
            extra_per_day = total_per_day = None
        all_days = len(days)
        all_q = sum(d[1] for d in days)
        all_s = sum(d[2] for d in days)
        all_r = sum(d[3] for d in days)
        result.append({
            "メニュー": name,
            "前回の週": start,
            "日数": n,
            "1日の持参": round(q / n),
            "1日の販売": round(s / n),
            "1日の別注": extra_per_day,
            "1日の合計": total_per_day,
            "廃棄率": round(r / q, 3) if q else None,
            "完売率": round(sold_out / lines, 3) if lines else None,
            "通算の平均持参": round(all_q / all_days),
            "通算の平均販売": round(all_s / all_days),
            "通算の平均廃棄率": round(all_r / all_q, 3) if all_q else None,
            "通算の回数": len(runs),
        })
    result.sort(key=lambda row: row["メニュー"])
    return result
