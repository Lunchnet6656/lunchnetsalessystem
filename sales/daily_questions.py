"""日計表の「売場の様子」：いつもと違った日の判定・選択肢・明日以降の食数。

「いつもと比べて」は販売員に聞かずアプリが判断する（代わりの担当でも答えられるように）。
- 売れなかった日：その店のふだんのブレに対して「N回に1回しかない少なさ」（一律の2割だと、
  上下の大きい店は何もない日に聞かれ、安定した店は本当の異変を見逃す）。かつ完売していない
- 早く売り切れた日：設定の時刻（既定12:20）より前に完売
仕様：engineering/harness/specs/lunchnetsale-日計表-売場情報の選択式入力-要件定義.md
"""
from datetime import timedelta

from sales.item_quantity_import import is_business_day
from sales.models import DailyReport, DailyReportQuestionSetting, SalesLocation

USUAL_SAMPLES = 20       # ふだんの数：直近20回の、完売しなかった日の販売数の中央値
USUAL_MIN = 8            # これより少なければ判定しない（新しい店など）
RATIO_SAMPLES = 60       # 店ごとの基準：直近60回の「販売数÷ふだんの数」
THRESHOLD_CLIP = (0.5, 0.95)
PLAN_DAYS_AHEAD = 14     # 明日以降の食数は2週間先まで
EVENT_DAYS_AHEAD = 60

OUTDOOR_TYPES = ("車", "行商", "テーブル")
NO_QUESTION = "特になし（分からない）"

DEFAULT_CHOICES = {
    "indoor_low": [
        "ビル・会社が休み／在宅勤務が多い日だった",
        "会議・研修・行事で人がいなかった（外出・出張が多い）",
        "人の入れ替わりがあった（異動・退職・部署の移動）",
        "社内でお弁当やケータリングが出た",
        "開店が遅れた・人手が足りなかった",
        "特定のメニューが不人気だった",
    ],
    "indoor_early": [
        "雨・寒さで外に出ない人が多かった",
        "会議や仕事が立て込んで、ビルにいる人が多かった",
        "新しい部署・人が入って増えた",
        "まとめ買い・団体があった",
        "特定のメニューが人気だった",
    ],
    "outdoor_low": [
        "雨・寒さ・暑さで人通りが少なかった",
        "近くのビル・会社が休みで人通りが少なかった",
        "近くにキッチンカー・競合のお店が出ていた",
        "いつもの場所が使えなかった（工事・行事など）",
        "近くのイベントに人が流れた",
        "開店が遅れた・人手が足りなかった",
        "特定のメニューが不人気だった",
    ],
    "outdoor_early": [
        "天気が良くて人通りが多かった",
        "近くのイベントで人が多かった",
        "近くのお店・キッチンカーが休みだった",
        "まとめ買い・団体があった",
        "特定のメニューが人気だった",
    ],
}
CHOICE_KEYS = (("indoor_low", "室内・いつもより売れなかった日"), ("indoor_early", "室内・早く売り切れた日"),
               ("outdoor_low", "外売り・いつもより売れなかった日"), ("outdoor_early", "外売り・早く売り切れた日"))


def sales_group(sales_type):
    """室内／外売り。配達など注文どおりに作る売場は None（聞かない）。"""
    if sales_type == "室内":
        return "indoor"
    if sales_type in OUTDOOR_TYPES:
        return "outdoor"
    return None


def choices_for(setting, group, kind):
    """その売場・その日の種類の選択肢（最後に必ず「特になし」）。"""
    key = f"{group}_{kind}"
    saved = setting.choices or {}
    # 設定で全部消した一覧（空）は「特になし」だけにする。既定に戻さない
    items = saved[key] if key in saved else DEFAULT_CHOICES[key]
    return [c for c in items if c and c != NO_QUESTION] + [NO_QUESTION]


def _percentile(values, pct):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def usual_for(location_name, before_date, one_in=10):
    """{usual: ふだんの販売数, threshold: これ以下の比なら「売れなかった」}。データが足りなければ None。"""
    rows = list(
        DailyReport.objects
        .filter(location=location_name, date__lt=before_date, date__gte=before_date - timedelta(days=200),
                total_quantity__gt=0)
        .order_by("-date")
        .values_list("total_sales_quantity", "total_remaining")[:RATIO_SAMPLES * 2]
    )
    not_sold_out = [int(s or 0) for s, r in rows if int(r or 0) > 0]
    recent = not_sold_out[:USUAL_SAMPLES]
    if len(recent) < USUAL_MIN:
        return None
    usual = sorted(recent)[len(recent) // 2]
    if usual <= 0:
        return None
    ratios = [s / usual for s in not_sold_out[:RATIO_SAMPLES]]
    threshold = _percentile(ratios, 1 / max(one_in, 2))
    lo, hi = THRESHOLD_CLIP
    threshold = min(max(threshold if threshold is not None else 0.8, lo), hi)
    return {"usual": usual, "threshold": round(threshold, 3)}


def judge(sold, remaining, sold_out_time, usual_info, early_before):
    """'low'（いつもより売れなかった）／'early'（早く売り切れた）／''。画面のJSと同じ式。"""
    if sold_out_time and early_before and sold_out_time < early_before:
        return "early"
    if usual_info and remaining > 0 and sold <= usual_info["usual"] * usual_info["threshold"]:
        return "low"
    return ""


def staff_is_regular(location, report_date, user):
    """その日の担当が「いつもの担当」か。直近2か月でその店の2割以上の日に入った人（複数可）。"""
    from shifts.models import ShiftAssignment

    since = report_date - timedelta(days=60)
    rows = list(ShiftAssignment.objects.filter(sales_location=location, date__gte=since, date__lt=report_date,
                                                 special_type="").values_list("user_id", "external_staff_id"))
    if not rows:
        return None
    counts = {}
    for u, e in rows:
        key = ("u", u) if u else ("e", e)
        counts[key] = counts.get(key, 0) + 1
    regulars = {k for k, n in counts.items() if n / len(rows) >= 0.2}
    today = list(ShiftAssignment.objects.filter(sales_location=location, date=report_date, special_type="")
                 .values_list("user_id", "external_staff_id"))
    people = [("u", u) if u else ("e", e) for u, e in today] or [("u", user.id)]
    return any(p in regulars for p in people)


def business_days_after(start, count):
    out, d = [], start
    while len(out) < count:
        d += timedelta(days=1)
        if is_business_day(d):
            out.append(d)
    return out


def plan_dates(report_date):
    """明日以降の食数で選べる日：日計表の日の次の営業日から2週間先まで。"""
    end = report_date + timedelta(days=PLAN_DAYS_AHEAD)
    days, d = [], report_date
    while True:
        d += timedelta(days=1)
        if d > end:
            break
        if is_business_day(d):
            days.append(d)
    return days


def location_type(name):
    return SalesLocation.objects.filter(name=name).values_list("type", flat=True).first() or ""
