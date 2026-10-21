"""日計表の「売場の様子」：画面に埋め込む値と、送信された答えの保存（入力画面・編集画面で共通）。

送信は止めない：判定が画面とずれても、理由が空のまま記録する（redirect で入力が消えるより良いため）。
"""
import unicodedata
from datetime import datetime, timedelta

from sales import daily_questions as dq
from sales.models import DailyReport, DailyReportQuestionSetting, StaffQuantityPlan, StoreEvent

WEEKDAYS = "月火水木金土日"
NOTE_MAX = 40


def day_label(d):
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"


def _num(value, lo=0, hi=999):
    """全角でも受け付ける。空・数字でないものは None。"""
    s = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not s:
        return None
    try:
        n = int(float(s))
    except ValueError:
        return None
    return min(max(n, lo), hi)


def _pair(lo, hi):
    if lo is None and hi is not None:
        return hi, None
    if lo is not None and hi is not None:
        if hi < lo:
            lo, hi = hi, lo
        if hi == lo:
            hi = None
    return lo, hi


def _time(value):
    """「00:00」は空（時間の欄の初期値のため）。"""
    if not value or str(value)[:5] == "00:00":
        return None
    try:
        return datetime.strptime(str(value)[:5], "%H:%M").time()
    except ValueError:
        return None


def _pairs(items, extra=()):
    """選択肢の部品に渡す (値, 表示) の並び。記録にあって今は無い理由は後ろに注記して残す。"""
    pairs = [(c, c) for c in items]
    known = set(items)
    for c in extra:
        if c not in known:
            pairs.insert(len(pairs) - 1, (c, f"{c}（今は選択肢にありません）"))
    return pairs


def written_label(plan):
    """「10/20に書かれた数：40〜45（取置き10・雨なら38）」"""
    extras = []
    if plan.kept is not None:
        extras.append(f"取置き{plan.kept}")
    if plan.rain_label:
        extras.append(f"雨なら{plan.rain_label}")
    head = plan.qty_label or "—"
    tail = f"（{'・'.join(extras)}）" if extras else ""
    return f"{plan.written_on.month}/{plan.written_on.day}に書かれた数：{head}{tail}"


def latest_plans(location_name, dates, exclude_report=None):
    """{日付: いちばん新しいStaffQuantityPlan}"""
    qs = StaffQuantityPlan.objects.filter(location=location_name, target_date__in=list(dates))
    if exclude_report is not None:
        qs = qs.exclude(report=exclude_report)
    out = {}
    for p in qs.order_by("-created_at", "-id"):
        out.setdefault(p.target_date, p)
    return out


def input_context(location, report_date, report=None):
    """入力画面（と編集画面）の「売場の様子」に出す値。"""
    setting = DailyReportQuestionSetting.load()
    group = dq.sales_group(location.type if location else "")
    usual = dq.usual_for(location.name, report_date, setting.low_one_in) if (location and group) else None
    dates = dq.plan_dates(report_date)
    latest = latest_plans(location.name, dates, exclude_report=report) if location else {}
    events = list(StoreEvent.objects.filter(location=location.name, target_date__gt=report_date)
                  .order_by("target_date", "created_at")[:3]) if location else []
    js = {
        "usual": usual["usual"] if usual else None,
        "threshold": usual["threshold"] if usual else None,
        "earlyBefore": setting.early_before.strftime("%H:%M"),
        "group": group,
        "planDates": [{"v": d.isoformat(), "label": day_label(d)} for d in dates],
        "written": {d.isoformat(): written_label(p) for d, p in latest.items()},
        "eventMin": (report_date + timedelta(days=1)).isoformat(),
        "eventMax": (report_date + timedelta(days=dq.EVENT_DAYS_AHEAD)).isoformat(),
    }
    return {
        "q_group": group,
        "q_choices_low": _pairs(dq.choices_for(setting, group, "low")) if group else [],
        "q_choices_early": _pairs(dq.choices_for(setting, group, "early")) if group else [],
        "q_early_before": setting.early_before,
        "q_no_question": dq.NO_QUESTION,
        "q_first_plan_date": dates[0] if dates else None,
        "q_first_written": js["written"].get(dates[0].isoformat()) if dates else None,
        "q_events": events,
        "q_js": js,
        "weather_choices": DailyReport.WEATHER_CHOICES,
        "feels_choices": DailyReport.FEELS_CHOICES,
    }


def judge_report(location, report_date, sold, remaining, sold_out_time):
    """サーバー側の判定（画面のJSと同じ式）。(kind, usual)"""
    setting = DailyReportQuestionSetting.load()
    if not location or not dq.sales_group(location.type):
        return "", None
    usual = dq.usual_for(location.name, report_date, setting.low_one_in)
    kind = dq.judge(sold, remaining, _time(sold_out_time), usual, setting.early_before)
    return kind, (usual["usual"] if usual else None)


def _reasons(post, kind):
    if not kind:
        return []
    vals = [v.strip()[:NOTE_MAX] for v in post.getlist(f"reasons_{kind}") if v.strip()]
    if dq.NO_QUESTION in vals:
        return [dq.NO_QUESTION]
    return list(dict.fromkeys(vals))[:10]


def answer_fields(post, location, report_date, sold, remaining, sold_out_time, user=None):
    """DailyReport に入れる値（update_or_create の defaults に足す）。"""
    kind, usual = judge_report(location, report_date, sold, remaining, sold_out_time)
    weather = post.get("weather_during_sales", "")
    feels = post.get("feels", "")
    weather_labels = dict(DailyReport.WEATHER_CHOICES)
    feels_labels = dict(DailyReport.FEELS_CHOICES)
    weather = weather if weather in weather_labels else ""
    feels = feels if feels in feels_labels else ""
    sold_out = (remaining == 0 and sold > 0) or _time(sold_out_time) is not None
    fields = {
        "weather_during_sales": weather,
        "feels": feels,
        # 古い集計・CSVが読む欄にも同じ答えを日本語で残す
        "weather": weather_labels.get(weather, ""),
        "temp": feels_labels.get(feels, ""),
        "lost_customers": _num(post.get("lost_customers"), 0, 99) if sold_out else None,
        "unusual_kind": kind,
        "unusual_reasons": _reasons(post, kind),
        "unusual_note": (post.get("unusual_note", "") or "").strip()[:200] if kind else "",
        "usual_sales": usual,
    }
    if user is not None and location is not None:
        try:
            fields["staff_is_regular"] = dq.staff_is_regular(location, report_date, user)
        except Exception:  # シフトが無い店でも日計表は送れるように
            fields["staff_is_regular"] = None
    return fields


def save_plans_and_events(report, post, location_name, report_date):
    """明日以降の食数・予定を、この日計表の分だけ書き直す（送り直しで二重にならないように）。"""
    allowed = set(dq.plan_dates(report_date))
    report.quantity_plans.all().delete()
    seen = set()
    rows = zip(post.getlist("plan_date"), post.getlist("plan_qty_min"), post.getlist("plan_qty_max"),
               post.getlist("plan_kept"), post.getlist("plan_rain_min"), post.getlist("plan_rain_max"))
    for d, qmin, qmax, kept, rmin, rmax in rows:
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if target not in allowed or target in seen:
            continue
        qmin, qmax = _pair(_num(qmin), _num(qmax))
        rmin, rmax = _pair(_num(rmin), _num(rmax))
        kept = _num(kept)
        if qmin is None and kept is None and rmin is None:
            continue
        seen.add(target)
        StaffQuantityPlan.objects.create(
            target_date=target, location=location_name, qty_min=qmin, qty_max=qmax, kept=kept,
            rain_min=rmin, rain_max=rmax, report=report, written_on=report_date)

    report.store_events.all().delete()
    last = report_date + timedelta(days=dq.EVENT_DAYS_AHEAD)
    for d, note in zip(post.getlist("event_date"), post.getlist("event_note")):
        note = (note or "").strip()[:NOTE_MAX]
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if not note or not (report_date < target <= last):
            continue
        StoreEvent.objects.create(target_date=target, location=location_name, note=note,
                                  report=report, written_on=report_date)


def _fmt(v, unit, digits=0):
    if v is None:
        return "—"
    return f"{v:.{digits}f}{unit}"


def auto_weather_text(report, today=None):
    vals = (report.auto_lunch_precip, report.auto_lunch_temp, report.auto_lunch_feels,
            report.auto_lunch_wind, report.auto_lunch_snow)
    if any(v is not None for v in vals):
        return "／".join([
            "雨 " + _fmt(report.auto_lunch_precip, "mm", 1),
            "気温 " + _fmt(report.auto_lunch_temp, "℃"),
            "体感 " + _fmt(report.auto_lunch_feels, "℃"),
            "風 " + _fmt(report.auto_lunch_wind, "m/s"),
            "雪 " + _fmt(report.auto_lunch_snow, "cm"),
        ])
    today = today or datetime.now().date()
    if report.date >= today - timedelta(days=7):
        return "まだ記録されていません（あとで自動で入ります）"
    return "この日計表には記録がありません"


def judgement_text(report, group, setting):
    if report.unusual_kind == "low":
        return f"いつもより売れなかった日（いつも約{report.usual_sales}食 → {report.total_sales_quantity}食）"
    if report.unusual_kind == "early":
        t = report.sold_out_time.strftime("%H:%M") if report.sold_out_time else "—"
        return f"{setting.early_before.strftime('%H:%M')}より前に売り切れた日（完売 {t}）"
    if group is None:
        return "配達など注文どおりの売場なので、質問は出していません"
    if report.usual_sales is not None:
        return "ふだんどおりの日（質問は出ていません）"
    return "記録なし（この機能より前の日計表）"


def edit_context(report):
    """編集画面の「売場の様子」。判定に関係なく、全部の欄を出す。"""
    from sales.models import SalesLocation

    setting = DailyReportQuestionSetting.load()
    location = SalesLocation.objects.filter(name=report.location).first()
    group = dq.sales_group(location.type) if location else None
    sold_out = (report.total_remaining == 0 and (report.total_sales_quantity or 0) > 0) or bool(
        report.sold_out_time and report.sold_out_time.strftime("%H:%M") != "00:00")
    kind = report.unusual_kind or ("early" if sold_out else "low")
    reasons = list(report.unusual_reasons or [])
    choices = _pairs(dq.choices_for(setting, group or "indoor", kind), extra=reasons)
    dates = dq.plan_dates(report.date)
    return {
        "q_judgement": judgement_text(report, group, setting),
        "q_choices_edit": choices,
        "q_selected_reasons": reasons,
        "q_no_question": dq.NO_QUESTION,
        "q_selected_weather": [report.weather_during_sales] if report.weather_during_sales else [],
        "q_selected_feels": [report.feels] if report.feels else [],
        # 新しい答えが無い古い日計表だけ、前の書き方を見せる
        "q_legacy_weather": report.weather if not report.weather_during_sales else "",
        "q_legacy_temp": report.temp if not report.feels else "",
        "q_auto_weather": auto_weather_text(report),
        "q_plans": list(report.quantity_plans.order_by("target_date")),
        "q_events": list(report.store_events.order_by("target_date")),
        "q_plan_dates": [(d.isoformat(), day_label(d)) for d in dates],
        "q_event_min": (report.date + timedelta(days=1)).isoformat(),
        "q_event_max": (report.date + timedelta(days=dq.EVENT_DAYS_AHEAD)).isoformat(),
        "weather_choices": DailyReport.WEATHER_CHOICES,
        "feels_choices": DailyReport.FEELS_CHOICES,
    }


def legacy_weather_temp(report):
    """選択式より前に文字で書かれた天気・体感（選択式の答えから作った文字は含めない）。"""
    return (report.weather if not report.weather_during_sales else "",
            report.temp if not report.feels else "")


def apply_edit_answers(report, post, legacy_weather, legacy_temp):
    """編集画面の保存（form.save(commit=False) の後・report.save() の前に呼ぶ）。
    天気・体感を選ばなかった古い日計表は、前の書き方（文字）をそのまま残す。"""
    weather_labels = dict(DailyReport.WEATHER_CHOICES)
    feels_labels = dict(DailyReport.FEELS_CHOICES)
    weather = post.get("weather_during_sales", "")
    feels = post.get("feels", "")
    report.weather_during_sales = weather if weather in weather_labels else ""
    report.feels = feels if feels in feels_labels else ""
    report.weather = weather_labels.get(report.weather_during_sales) or legacy_weather or ""
    report.temp = feels_labels.get(report.feels) or legacy_temp or ""
    vals = [v.strip()[:NOTE_MAX] for v in post.getlist("reasons_edit") if v.strip()]
    report.unusual_reasons = [dq.NO_QUESTION] if dq.NO_QUESTION in vals else list(dict.fromkeys(vals))[:10]
    report.unusual_note = (post.get("unusual_note", "") or "").strip()[:200]
    report.lost_customers = _num(post.get("lost_customers"), 0, 99)
