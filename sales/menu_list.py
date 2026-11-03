"""販売員用のメニューリスト（見るだけ）。週ごとのメニュー名・値段の種類・容器。

これから始まる週は、本部が［確認完了］を押すまで出さない（種類・容器が仮のままのため）。
"""
import datetime

from sales.menu_registry import CONTAINER_ALIASES, week_end, week_state
from sales.menu_week import week_products
from sales.models import MenuWeekCheck, PriceRank, Product
from sales.pricing import legacy_role


def _to_week(text):
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


WEEKDAYS = "月火水木金土日"


def this_week(today):
    return today - datetime.timedelta(days=(today.weekday() - 2) % 7)


def visible_weeks(today):
    """選べる週（新しい順）。これから始まる週は確認済みのものだけ。"""
    weeks = {w for w in (_to_week(t) for t in Product.objects.values_list("week", flat=True).distinct()) if w}
    confirmed = set(MenuWeekCheck.objects.filter(week__in=weeks, confirmed_at__isnull=False)
                    .values_list("week", flat=True))
    return sorted((w for w in weeks if w <= today or w in confirmed), reverse=True)


def default_week(weeks, today):
    """今の週。なければ今日より前でいちばん新しい週。"""
    current = this_week(today)
    if current in weeks:
        return current
    return next((w for w in weeks if w <= today), weeks[-1] if weeks else None)


def week_list(week, today):
    """1品ずつ：No・メニュー名・値段の種類・容器。"""
    products = week_products(week)
    ranks_by_role = {r.role: r for r in PriceRank.objects.exclude(role="")}
    items = []
    for p in products:
        # 古い登録方法の週は種類がないので、★あり→特選のように名前から決める
        rank = p.rank if p.rank_id else ranks_by_role.get(legacy_role(p))
        container = CONTAINER_ALIASES.get(p.container_type, p.container_type)
        items.append({
            "no": p.no,
            "name": p.name,
            "rank": rank.name if rank else "",
            # バッジの色。種類の名前は本部が変えられるので役割で決める（役割なし＝お手頃など）
            "rank_role": (rank.role or "other") if rank else "",
            "container": container,
            # スマホでメニュー名の幅を取るため、見出しに「容器」と出して中身は「赤」「黒」に短くする
            "container_short": container[:-2] if container.endswith("容器") and len(container) > 2 else container,
        })
    check = MenuWeekCheck.objects.filter(week=week).first()
    state = week_state(week, today)
    return {
        "week": week,
        "week_end": week_end(week),
        "period": f"{week.month}/{week.day}（{WEEKDAYS[week.weekday()]}）〜"
                  f"{week_end(week).month}/{week_end(week).day}（{WEEKDAYS[week_end(week).weekday()]}）",
        "label": f"{week.month}/{week.day}週",
        "state": state,
        "items": items,
        # 販売中・これからの週で本部の確認がまだ（種類・容器が仮）
        "unconfirmed": bool(products) and state != "ended" and any(p.rank_id for p in products)
                       and not (check and check.is_confirmed),
    }


def _label(week, today):
    return f"{week.month}/{week.day}週" if week.year == today.year else f"{week.year}/{week.month}/{week.day}週"


def screen(week_text, today):
    weeks = visible_weeks(today)
    week = _to_week(week_text) if week_text else default_week(weeks, today)
    if week not in weeks:
        week = default_week(weeks, today)
    ctx = {"weeks": [{"week": w, "label": _label(w, today), "state": week_state(w, today)} for w in weeks]}
    if week:
        ctx.update(week_list(week, today))
        idx = weeks.index(week)
        ctx["newer_week"] = weeks[idx - 1] if idx > 0 else None
        ctx["older_week"] = weeks[idx + 1] if idx + 1 < len(weeks) else None
    return ctx
