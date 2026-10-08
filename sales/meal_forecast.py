"""食数予測（店舗別・直近の営業日）。

- 店は「名前」で数える。販売場所No.は途中で振り直されている（2026-06・2026-10）ため、
  日計表の location_no で集計すると別の店の実績が混ざる。
- 完売した日は「売れた数＝持っていった数」で頭打ちなので、完売時刻が早いほど需要を上乗せして見る。
- 予測＝直近5営業日の需要 × その店の曜日の癖。実データ（2026-06〜10・30店）で
  「同じ曜日4回の加重平均」より誤差が小さかった（ズレ率 10.0%→9.1%）。
  直近の増減率をさらに掛けると反応しすぎて悪化したので、増減率は表示だけにする。
- 月内のクセ（上旬・中旬・下旬）は一部の店にだけ本物があるが、予測に掛けても当たりは変わらなかった
  （2026-10検証）ので、はっきりしている店に目印を出すだけにする。
"""
from datetime import timedelta

from django.conf import settings

from sales.models import DailyReport, SalesLocation

RECENT_DAYS = 5          # 直近の水準を見る営業日数
WEEKDAY_SAMPLES = 8      # 曜日の癖：同じ曜日の直近何回
LEVEL_DAYS = 40          # 曜日の癖の分母：直近何営業日の平均
WEEKDAY_MIN = 4          # 曜日の癖を使う最低回数（足りなければ癖なし＝1倍）
WEEKDAY_CLIP = (0.7, 1.3)
TREND_DAYS = 10          # 増減率：直近10営業日 vs その前の10営業日
SOLD_OUT_UPLIFT = 0.2    # 完売日の上乗せ：閉店までに残っていた時間の割合 × 0.2（0.4は強すぎた・2026-10検証）
MIN_HISTORY = 5


def _minutes(t):
    if not t:
        return None
    m = t.hour * 60 + t.minute
    return m or None


def estimated_demand(sold, remaining, sold_out_time, opening_time, closing_time):
    """完売した日は、完売時刻が早いほど需要を上乗せする（12:00完売・11-13時営業なら+20%）。"""
    if remaining > 0:
        return float(sold)
    o, c, t = _minutes(opening_time), _minutes(closing_time), _minutes(sold_out_time)
    left = 0.0
    if o and c and t and c > o:
        left = min(max((c - t) / (c - o), 0.0), 1.0)
    return sold * (1 + SOLD_OUT_UPLIFT * left)


def _excluded_names():
    names = set(getattr(settings, "MENU_HISTORY_EXCLUDED_LOCATIONS", []))
    names |= set(SalesLocation.objects.filter(type="配達").values_list("name", flat=True))
    return names


def load_history(since, until):
    """{店名: [行(日付順)]}。行は dict(date, wd, sold, rem, demand, sold_out)。"""
    excluded = _excluded_names()
    rows = (
        DailyReport.objects
        .filter(date__gte=since, date__lt=until, total_quantity__gt=0)
        .exclude(location__in=excluded)
        .values_list("date", "location", "total_sales_quantity", "total_remaining",
                     "sold_out_time", "opening_time", "closing_time")
        .order_by("date")
    )
    hist = {}
    for d, name, sold, rem, so_t, op_t, cl_t in rows:
        sold, rem = int(sold or 0), int(rem or 0)
        hist.setdefault(name.strip(), []).append({
            "date": d, "wd": d.weekday(), "sold": sold, "rem": rem,
            "demand": estimated_demand(sold, rem, so_t, op_t, cl_t), "sold_out": rem <= 0,
        })
    return hist


def _mean(values):
    return sum(values) / len(values) if values else None


def predict(rows, weekday):
    """rows（予測日より前・日付順）から、その曜日の需要の見込みと根拠を返す。足りなければ None。"""
    if len(rows) < MIN_HISTORY:
        return None
    demand = [r["demand"] for r in rows]
    recent = _mean(demand[-RECENT_DAYS:])

    same = [r["demand"] for r in rows if r["wd"] == weekday][-WEEKDAY_SAMPLES:]
    level = _mean(demand[-LEVEL_DAYS:])
    idx = 1.0
    if len(same) >= WEEKDAY_MIN and level:
        lo, hi = WEEKDAY_CLIP
        idx = min(max(_mean(same) / level, lo), hi)

    trend = None
    if len(demand) >= TREND_DAYS + 5:
        now, before = _mean(demand[-TREND_DAYS:]), _mean(demand[-2 * TREND_DAYS:-TREND_DAYS])
        if before:
            trend = now / before - 1

    return {"pred": recent * idx, "recent": recent, "weekday_idx": idx, "trend": trend}


def backtest(hist, today, days=28):
    """直近 days 日を、その日より前のデータだけで予測して答え合わせ。
    需要が正確に分かる「完売しなかった日」だけで誤差を測る。"""
    cut = today - timedelta(days=days)
    errors, pcts = [], []
    for rows in hist.values():
        for i, r in enumerate(rows):
            if r["date"] <= cut or r["sold_out"] or r["sold"] <= 0:
                continue
            p = predict(rows[:i], r["wd"])
            if not p:
                continue
            errors.append(abs(p["pred"] - r["sold"]))
            pcts.append(abs(p["pred"] - r["sold"]) / r["sold"])
    if not errors:
        return None
    return {"n": len(errors), "mae": round(_mean(errors), 1), "mape": round(_mean(pcts) * 100)}


THIRD_LABELS = ("上旬", "中旬", "下旬")
THIRD_SHRINK = 40        # データが少ないほど「クセなし」に寄せる強さ
THIRD_MIN_EFFECT = 0.03  # これ以上の差がある店だけ目印を出す


def month_third(d):
    return 0 if d.day <= 10 else 1 if d.day <= 20 else 2


def third_effect(rows, third):
    """その店の、上旬・中旬・下旬のどれかでの売れ方の差（直近20営業日の水準比）。はっきりしなければ None。"""
    rel = []
    for i in range(20, len(rows)):
        level = sorted(r["demand"] for r in rows[i - 20:i])[10]
        if level > 0:
            rel.append((month_third(rows[i]["date"]), rows[i]["demand"] / level))
    if len(rel) < 60:
        return None
    overall = _mean([v for _, v in rel])
    same = [v for t, v in rel if t == third]
    if not same or not overall:
        return None
    n = len(same)
    effect = (_mean(same) / overall - 1) * n / (n + THIRD_SHRINK)
    return effect if abs(effect) >= THIRD_MIN_EFFECT else None
