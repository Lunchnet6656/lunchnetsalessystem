"""実績分析：期間を決めて、売り場・メニューの実績と前月比・前週比・12か月推移を出す。

数字は販売場所別売れ行きランキング・メニュー売れ行きランキングとそろえる：
お弁当（メニュー1〜10）だけを数え、売り場は店名でまとめる（販売場所No.は振り直しがあるため）。
前月比・前週比は、指定期間をそのまま1か月前／7日前にずらした期間と比べる（期間が自由指定のため）。
"""
from datetime import timedelta
from statistics import mean, median

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from orders.models import OrderItem
from sales.models import AnalysisBasisSetting, rule_text, DailyReport, DailyReportEntry, SalesLocation

TREND_MONTHS = 12

LOCATION_LABEL_ORDER = ["優良", "持参を減らす", "テコ入れ", "撤退検討", "様子見"]
OTHER_LABEL = "様子見"  # 評価ごとに決めるとき、どの条件にも当てはまらないもの
# 評価ごとに決めるときの判定順（良い→悪いの両端を先に決め、残りを中間で拾う）
RULE_ORDER = {
    "location": ["優良", "持参を減らす", "撤退検討", "テコ入れ"],
    "menu": ["人気", "作りすぎ", "不人気", "増やせる"],
}
LOCATION_LABELS = {
    (True, True): "優良",
    (True, False): "持参を減らす",
    (False, True): "テコ入れ",
    (False, False): "撤退検討",
}
MENU_LABELS = {
    (True, True): "人気",
    (True, False): "作りすぎ",
    (False, True): "増やせる",
    (False, False): "不人気",
}
MENU_LABEL_ORDER = ["人気", "作りすぎ", "増やせる", "不人気", "様子見"]
BADGE = {
    "優良": "success", "人気": "success", "持参を減らす": "warning", "作りすぎ": "warning",
    "テコ入れ": "info", "増やせる": "info", "撤退検討": "danger", "不人気": "danger", "様子見": "secondary",
}


def default_period(today):
    """今月1日〜昨日。1日に開いたときは前月まるごと。"""
    yesterday = today - timedelta(days=1)
    return yesterday.replace(day=1), yesterday


def _bento_entries(start, end):
    return DailyReportEntry.objects.filter(
        report__date__range=(start, end), product_no__gte=1, product_no__lte=10,
    )


def _rates(q, s, r, days):
    return {
        "quantity": q, "sales": s, "remaining": r, "days": days,
        "waste_rate": (r / q * 100) if q else None,
        "avg_sales": (s / days) if days else None,
    }


def _period_stats(start, end):
    entries = _bento_entries(start, end)

    locations = {}
    for row in (
        entries.exclude(report__location="")
        .values("report__location")
        .annotate(q=Sum("quantity"), s=Sum("sales_quantity"), r=Sum("remaining_number"),
                  days=Count("report__date", distinct=True))
    ):
        locations[row["report__location"]] = _rates(row["q"] or 0, row["s"] or 0, row["r"] or 0, row["days"])

    revenue_by_location = dict(
        DailyReport.objects.filter(date__range=(start, end))
        .values("location").annotate(rev=Sum("total_revenue")).values_list("location", "rev")
    )
    for name, stats in locations.items():
        stats["revenue"] = int(revenue_by_location.get(name) or 0)

    menus = {}
    for row in (
        entries.exclude(product="")
        .values("product")
        .annotate(q=Sum("quantity"), s=Sum("sales_quantity"), r=Sum("remaining_number"),
                  days=Count("report__date", distinct=True))
    ):
        menus[row["product"]] = _rates(row["q"] or 0, row["s"] or 0, row["r"] or 0, row["days"])

    store_q = sum(v["quantity"] for v in locations.values())
    store_s = sum(v["sales"] for v in locations.values())
    store_r = sum(v["remaining"] for v in locations.values())
    store_days = sum(v["days"] for v in locations.values())
    orders = int(
        OrderItem.objects.filter(order__delivery_date__range=(start, end))
        .aggregate(n=Sum("quantity"))["n"] or 0
    )
    # 受注は注文どおり作って全部渡す＝持参数・販売数に足し、残数0として廃棄率に入れる（2026-11-02 しょうへい確定）
    total = _rates(store_q + orders, store_s + orders, store_r, store_days)
    # 平均販売数は売り場だけ。分母が売り場ごとの稼働日数の合計＝「1売り場1日あたり」で、受注には稼働日がないため
    total["avg_sales"] = (store_s / store_days) if store_days else None
    # 売り場・メニューの評価の基準は売り場だけの廃棄率（受注は売り場・メニューの数字に入っていないので、同じ土俵で比べる）
    total["store_waste_rate"] = (store_r / store_q * 100) if store_q else None
    total["revenue"] = int(sum(revenue_by_location.values(), 0) or 0)
    total["orders"] = orders
    return {"total": total, "locations": locations, "menus": menus}


# 指標ごとに「上がると良いか」。None は良し悪しを付けない（持参数・稼働日数）
DIRECTION = {
    "quantity": None, "days": None, "sales": True, "remaining": False, "revenue": True, "orders": True,
    "avg_sales": True, "waste_rate": False,
}
# 差で比べる指標と単位。それ以外は増減率%
DIFF_UNITS = {"waste_rate": "pt", "days": "日"}
WITH_WEEK = ("avg_sales", "waste_rate")
SR_UNITS = {"%": "%", "pt": "ポイント", "日": "日"}


def _change(key, cur, prev):
    """前月比・前週比の1行ぶん。廃棄率・稼働日数は差、それ以外は増減率%。比較できなければ None（画面では「—」）。"""
    if cur is None or prev is None:
        return None
    unit = DIFF_UNITS.get(key)
    if unit:
        value = cur - prev
    else:
        if not prev:
            return None
        value, unit = (cur - prev) / prev * 100, "%"
    value = round(value, 1)
    if value == 0:
        return {"value": 0.0, "unit": unit, "arrow": "±", "tone": "neutral"}
    up = value > 0
    good = DIRECTION[key]
    tone = "neutral" if good is None else ("good" if up == good else "bad")
    return {"value": abs(value), "unit": unit, "arrow": "▲" if up else "▼", "tone": tone}


def _line(label, ch):
    """画面の1行：`▲ 12.5%` ＋ 比較名。100%以上は整数にして幅を抑える（折り返さないため）。"""
    if ch is None:
        return {"label": label, "text": "—", "tone": "neutral", "sr": f"{label} 比較データなし"}
    v = ch["value"]
    if ch["unit"] == "日":
        num = f"{v:.0f}"
    elif ch["unit"] == "%" and v >= 100:
        num = f"{v:,.0f}"
    else:
        num = f"{v:.1f}"
    word = {"▲": "増", "▼": "減", "±": "変化なし"}[ch["arrow"]]
    sr = f"{label} 変化なし" if ch["arrow"] == "±" else f"{label} {num}{SR_UNITS[ch['unit']]}{word}"
    return {"label": label, "text": f"{ch['arrow']} {num}{ch['unit']}", "tone": ch["tone"], "sr": sr}


def _compare(stats, prev_month, prev_week, keys):
    """stats に changes（生の値）と deltas（画面の行。前月比が上・前週比が下）を付ける。前週比は平均販売数と廃棄率だけ。"""
    changes, deltas = {}, {}
    for key in keys:
        cur = stats.get(key)
        pm = prev_month.get(key) if prev_month else None
        pw = prev_week.get(key) if prev_week else None
        changes[key] = {"month": _change(key, cur, pm)}
        if key in WITH_WEEK:
            changes[key]["week"] = _change(key, cur, pw)
        deltas[key] = [_line("前月比", changes[key]["month"])]
        if key in WITH_WEEK:
            deltas[key].append(_line("前週比", changes[key]["week"]))
    stats["changes"] = changes
    stats["deltas"] = deltas
    return stats


def basis_setting(target):
    """評価の基準設定。まだ保存されていなければ既定値（中央値・売り場全体・3日）の未保存の行を返す。"""
    return AnalysisBasisSetting.objects.filter(target=target).first() or AnalysisBasisSetting(target=target)


def default_rules(target, basis):
    """「評価ごとに決める」に切り替えた直後の条件。今の基準線そのままにして、切り替えただけでは評価が変わらないようにする。"""
    avg = basis["avg_sales"] if basis and basis["avg_sales"] is not None else 0
    waste = basis["waste_rate"] if basis and basis["waste_rate"] is not None else 0
    ops = {  # 判定順の各評価が 4象限のどこか
        "優良": ("gte", "lte"), "人気": ("gte", "lte"),
        "持参を減らす": ("gte", "gt"), "作りすぎ": ("gte", "gt"),
        "撤退検討": ("lt", "gt"), "不人気": ("lt", "gt"),
        "テコ入れ": ("lt", "lte"), "増やせる": ("lt", "lte"),
    }
    return [{"label": label, "avg_op": ops[label][0], "avg_value": avg, "waste_op": ops[label][1], "waste_value": waste}
            for label in RULE_ORDER[target]]


def _matches(item, rule):
    if rule.get("avg_op") == "gte" and not item["avg_sales"] >= float(rule["avg_value"]):
        return False
    if rule.get("avg_op") == "lt" and not item["avg_sales"] < float(rule["avg_value"]):
        return False
    if rule.get("waste_op") == "lte" and not item["waste_rate"] <= float(rule["waste_value"]):
        return False
    if rule.get("waste_op") == "gt" and not item["waste_rate"] > float(rule["waste_value"]):
        return False
    return True


def _evaluate_rules(items, eligible, setting):
    eligible_ids = {id(it) for it in eligible}
    for it in items:
        if id(it) not in eligible_ids:
            it["label"] = None
            continue
        it["label"] = next((r["label"] for r in setting.rules if _matches(it, r)), OTHER_LABEL)
        it["badge"] = BADGE[it["label"]]
        it["above_median"] = None
    return {"mode": "rules", "rules": [rule_text(r) for r in setting.rules], "min_days": setting.min_days,
            "avg_word": "", "waste_word": "", "avg_sales": None, "waste_rate": None, "empty": not eligible}


def _evaluate(items, setting, summary, labels):
    """評価。「2本の線で分ける」（4象限）か「評価ごとに決める」（条件を上から順に）。評価しない日数も設定に従う。"""
    eligible = [it for it in items if it["days"] >= setting.min_days and it["quantity"] > 0]
    if setting.rule_mode == "rules" and setting.rules:
        return _evaluate_rules(items, eligible, setting)
    if setting.waste_mode == "fixed":
        waste_line = float(setting.waste_fixed)
    elif setting.waste_mode == "total":
        waste_line = summary["waste_rate"]
    else:
        waste_line = summary["store_waste_rate"]
    words = {
        "avg_word": {"median": "中央値", "mean": "平均値", "fixed": "固定"}[setting.avg_mode],
        "waste_word": {"store": "売り場全体", "total": "受注込み", "fixed": "固定"}[setting.waste_mode],
        "min_days": setting.min_days, "mode": "lines",
    }
    if not eligible or waste_line is None:
        for it in items:
            it["label"] = None
        return dict(words, avg_sales=None, waste_rate=None, empty=True)
    if setting.avg_mode == "fixed":
        avg_line = float(setting.avg_fixed)
    elif setting.avg_mode == "mean":
        avg_line = mean(it["avg_sales"] for it in eligible)
    else:
        avg_line = median(it["avg_sales"] for it in eligible)
    # 画面のチップと判定を食い違わせないため、表示と同じ小数1桁で判定する
    avg_line, waste_line = round(avg_line, 1), round(waste_line, 1)
    eligible_ids = {id(it) for it in eligible}
    for it in items:
        if id(it) in eligible_ids:
            it["label"] = labels[(it["avg_sales"] >= avg_line, it["waste_rate"] <= waste_line)]
            it["badge"] = BADGE[it["label"]]
            it["above_median"] = it["avg_sales"] >= avg_line
        else:
            it["label"] = None
    return dict(words, avg_sales=avg_line, waste_rate=waste_line, empty=False)


def _trend(end):
    # 先頭の月にも前月比を出すため、1か月前から集めて最後に落とす
    first = (end.replace(day=1) - relativedelta(months=TREND_MONTHS))
    last = end.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
    bento = {
        row["m"].date() if hasattr(row["m"], "date") else row["m"]: row
        for row in _bento_entries(first, last)
        .annotate(m=TruncMonth("report__date")).values("m")
        .annotate(q=Sum("quantity"), s=Sum("sales_quantity"), r=Sum("remaining_number"))
    }
    orders = {
        (row["m"].date() if hasattr(row["m"], "date") else row["m"]): row["n"]
        for row in OrderItem.objects.filter(order__delivery_date__range=(first, last))
        .annotate(m=TruncMonth("order__delivery_date")).values("m").annotate(n=Sum("quantity"))
    }
    revenue = {
        (row["m"].date() if hasattr(row["m"], "date") else row["m"]): row["rev"]
        for row in DailyReport.objects.filter(date__range=(first, last))
        .annotate(m=TruncMonth("date")).values("m").annotate(rev=Sum("total_revenue"))
    }
    months = []
    for i in range(TREND_MONTHS + 1):
        m = first + relativedelta(months=i)
        b = bento.get(m)
        rev = revenue.get(m)
        o = orders.get(m) or 0
        if b is None and rev is None and not o:
            months.append({"month": m, "empty": True})
            continue
        q, s, r = (b["q"] or 0, b["s"] or 0, b["r"] or 0) if b else (0, 0, 0)
        q, s = q + o, s + o  # サマリーと同じく受注を含める
        months.append({
            "month": m, "empty": False, "quantity": q, "sales": s, "remaining": r,
            "waste_rate": (r / q * 100) if q else None, "revenue": int(rev or 0),
        })
    keys = ["quantity", "sales", "remaining", "waste_rate", "revenue"]
    for prev, cur in zip(months, months[1:]):
        if cur["empty"]:
            continue
        cur["deltas"] = {
            k: [_line("前月比", None if prev["empty"] else _change(k, cur[k], prev[k]))] for k in keys
        }
    return months[1:]


def analyze(start, end):
    cur = _period_stats(start, end)
    pm_start, pm_end = start - relativedelta(months=1), end - relativedelta(months=1)
    pw_start, pw_end = start - timedelta(days=7), end - timedelta(days=7)
    pm = _period_stats(pm_start, pm_end)
    pw = _period_stats(pw_start, pw_end)

    def has_data(stats):
        return stats["quantity"] > 0

    summary = _compare(
        cur["total"],
        pm["total"] if has_data(pm["total"]) else None,
        pw["total"] if has_data(pw["total"]) else None,
        ["revenue", "sales", "avg_sales", "waste_rate", "quantity", "remaining", "orders"],
    )

    row_keys = ["quantity", "sales", "remaining", "waste_rate", "avg_sales", "revenue", "days"]
    locations = [
        dict(_compare(stats, pm["locations"].get(name), pw["locations"].get(name), row_keys), name=name)
        for name, stats in cur["locations"].items() if stats["quantity"] > 0
    ]
    location_setting = basis_setting("location")
    location_basis = _evaluate(locations, location_setting, summary, LOCATION_LABELS)
    _waste_colors(locations, location_setting, location_basis)
    numbers = _location_numbers([l["name"] for l in locations])
    for l in locations:
        l["no"] = numbers.get(l["name"])
        l["eval_rank"] = LOCATION_LABEL_ORDER.index(l["label"]) if l["label"] else -1
    # 1日平均の多い順。評価対象外（稼働が足りない）は最後。上半分＝中央値以上になるので区切り行で基準線を見せる
    locations.sort(key=lambda x: (x["label"] is None, -(x["avg_sales"] or 0)))
    _mark_breaks(locations)

    menu_keys = ["quantity", "sales", "remaining", "waste_rate", "avg_sales"]
    menus = [
        dict(_compare(stats, pm["menus"].get(name), pw["menus"].get(name), menu_keys), name=name)
        for name, stats in cur["menus"].items() if stats["quantity"] > 0
    ]
    menu_setting = basis_setting("menu")
    menu_basis = _evaluate(menus, menu_setting, summary, MENU_LABELS)
    _waste_colors(menus, menu_setting, menu_basis)
    # 既定は販売数の多い順。並べ替えは画面で行う
    menus.sort(key=lambda x: -x["sales"])
    for m in menus:
        # 評価で並べ替えるときの順位（人気が先）。評価なしは -1 で常に最後
        m["eval_rank"] = MENU_LABEL_ORDER.index(m["label"]) if m["label"] else -1

    return {
        "start": start, "end": end,
        "prev_month": (pm_start, pm_end), "prev_week": (pw_start, pw_end),
        "summary": summary,
        "locations": locations, "location_basis": location_basis, "location_setting": location_setting,
        "location_counts": [
            {"label": label, "badge": BADGE[label], "count": sum(1 for l in locations if l["label"] == label)}
            for label in LOCATION_LABEL_ORDER
        ] + [{"label": None, "badge": None, "count": sum(1 for l in locations if l["label"] is None)}],
        "menus": menus, "menu_basis": menu_basis, "menu_setting": menu_setting,
        "menu_counts": {label: sum(1 for m in menus if m["label"] == label) for label in MENU_LABEL_ORDER},
        "menu_count_badges": [
            {"label": label, "badge": BADGE[label], "count": sum(1 for m in menus if m["label"] == label)}
            for label in MENU_LABEL_ORDER
        ] + [{"label": None, "badge": None, "count": sum(1 for m in menus if m["label"] is None)}],
        "trend": _trend(end),
    }


def _location_numbers(names):
    """売り場No.はマスタの今の番号。マスタにない店（閉店・臨時）は日計表に残る最後の番号。"""
    numbers = {}
    for no, name in SalesLocation.objects.filter(name__in=names).order_by("-no").values_list("no", "name"):
        numbers[name] = no
    for name in set(names) - set(numbers):
        last = DailyReport.objects.filter(location=name).exclude(location_no=0).order_by("-date").values_list("location_no", flat=True).first()
        if last:
            numbers[name] = last
    return numbers


def _waste_colors(items, setting, basis):
    """廃棄率の文字の色。基準と比べる＝基準より高ければ赤太字。数値で決める＝当てはまるいちばん高い段。"""
    bands = sorted(setting.waste_bands, key=lambda b: -float(b["min"])) if setting.waste_color_mode == "bands" else None
    line = basis.get("waste_rate") if basis else None
    for it in items:
        w, cls = it["waste_rate"], ""
        if w is not None and bands is not None:
            band = next((b for b in bands if w >= float(b["min"])), None)
            if band:
                cls = f"text-{band['color']}" + (" fw-bold" if band["bold"] else "")
        elif w is not None and line is not None and w > line:
            cls = "text-danger fw-bold"
        it["waste_class"] = cls


def _mark_breaks(locations):
    """区切り行の位置：中央値未満の先頭、対象外（稼働3日未満）の先頭。"""
    seen_below = seen_na = False
    for loc in locations:
        loc["median_break"] = loc["na_break"] = False
        if loc["label"] is None:
            if not seen_na:
                loc["na_break"] = seen_na = True
        elif loc.get("above_median") is False and not seen_below:  # 評価ごとに決めるときは基準線がないので None
            loc["median_break"] = seen_below = True
