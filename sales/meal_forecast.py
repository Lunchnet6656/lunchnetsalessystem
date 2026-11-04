"""食数予測（店舗別・直近の営業日）。

- 店は「名前」で数える。販売場所No.は途中で振り直されている（2026-06・2026-10）ため、
  日計表の location_no で集計すると別の店の実績が混ざる。
- 完売した日は「売れた数＝持っていった数」で頭打ちなので、完売時刻が早いほど需要を上乗せして見る。
- 予測＝直近の水準 × その店の曜日の癖。直近の水準＝新しい日ほど重い直近5営業日の平均7割＋直近20営業日の平均3割
  （2026-10検証。日計表で「その日だけの理由」が選ばれた日は水準から外す）。当初の「直近5営業日の平均 × 曜日の癖」も
  実データ（2026-06〜10・30店）で「同じ曜日4回の加重平均」より誤差が小さかった（ズレ率 10.0%→9.1%）。
  直近の増減率をさらに掛けると反応しすぎて悪化したので、増減率は表示だけにする。
- 月内のクセ（上旬・中旬・下旬）は一部の店にだけ本物があるが、予測に掛けても当たりは変わらなかった
  （2026-10検証）ので、はっきりしている店に目印を出すだけにする。
- 雨の補正＝形態ごとの値 × その店の倍率（rain_multiplier）。1年分の答え合わせで雨の日の誤差が約6%減った（2026-10）。
- 飛び石（前の日も次の日も休み）の日は全店 ×0.90、昼の風6m以上はテーブル・行商 ×0.95（2026-11検証）。
  給料日・月末・五十日は効かなかったので入れない（research/topics/2026-11-04-食数予測-外れ要素の探索）。
"""
from datetime import timedelta

import jpholiday
from django.conf import settings

from sales.models import DailyReport, Holiday, SalesLocation
from sales.daily_questions import ONE_OFF_REASONS
from sales.weather import rain_factor, rain_level, wind_factor

RECENT_DAYS = 5          # 直近の水準を見る営業日数（新しい日ほど重く 1〜5）
RECENT_LONG_DAYS = 20    # 直近の水準に混ぜる長めの平均
RECENT_LONG_SHARE = 0.3  # 長めの平均の割合（2026-10検証：一時的な落ち込みに引っ張られにくく、誤差5.08→4.96食）
WEEKDAY_SAMPLES = 8      # 曜日の癖：同じ曜日の直近何回
LEVEL_DAYS = 40          # 曜日の癖の分母：直近何営業日の平均
WEEKDAY_MIN = 4          # 曜日の癖を使う最低回数（足りなければ癖なし＝1倍）
WEEKDAY_CLIP = (0.7, 1.3)
TREND_DAYS = 10          # 増減率：直近10営業日 vs その前の10営業日
SOLD_OUT_UPLIFT = 0.2    # 完売日の上乗せ：閉店までに残っていた時間の割合 × 0.2（0.4は強すぎた・2026-10検証）
MIN_HISTORY = 5
RAIN_STORE_K = 8               # 店ごとの雨の倍率：雨の日の実績が少ない店ほど1.0（形態の値のまま）に寄せる強さ
RAIN_STORE_CLIP = (0.6, 1.4)   # 2026-10検証：雨の日の誤差 6.71→6.31食（research/topics/2026-10-23-食数予測-雨補正の店別化…）
BRIDGE_FACTOR = 0.90           # 飛び石の日：過去3回 −14%・−16%・−6%。答え合わせで誤差 6.27→2.13食


def _minutes(t):
    if not t:
        return None
    m = t.hour * 60 + t.minute
    return m or None


def estimated_demand(sold, remaining, sold_out_time, opening_time, closing_time, lost=None):
    """完売した日は、完売時刻が早いほど需要を上乗せする（12:00完売・11-13時営業なら+20%）。
    現場が「買えなかったお客さん」の人数を書いた日は、推定より確かなので 販売数＋人数 を使う。"""
    if remaining > 0:
        return float(sold)
    if lost is not None:
        return float(sold + lost)
    o, c, t = _minutes(opening_time), _minutes(closing_time), _minutes(sold_out_time)
    left = 0.0
    if o and c and t and c > o:
        left = min(max((c - t) / (c - o), 0.0), 1.0)
    return sold * (1 + SOLD_OUT_UPLIFT * left)


def bridge_days(dates):
    """dates のうち飛び石の日（前の日も次の日も休み）。11/2(月)のように日曜と祝日に挟まれると全店で落ちる。"""
    dates = list(dates)
    if not dates:
        return set()
    company = set(Holiday.objects.filter(date__gte=min(dates) - timedelta(days=1),
                                         date__lte=max(dates) + timedelta(days=1)).values_list("date", flat=True))

    def off(d):
        return d.weekday() >= 5 or jpholiday.is_holiday(d) or d in company

    one = timedelta(days=1)
    return {d for d in dates if not off(d) and off(d - one) and off(d + one)}


def day_factor(level, wind, bridge, sales_type, rain_mult=1.0):
    """雨（形態×店の倍率）・強風・飛び石をまとめた掛け率。"""
    f = rain_factor(level, sales_type) * (rain_mult if level else 1.0) * wind_factor(wind, sales_type)
    return f * BRIDGE_FACTOR if bridge else f


def _excluded_names():
    names = set(getattr(settings, "MENU_HISTORY_EXCLUDED_LOCATIONS", []))
    names |= set(SalesLocation.objects.filter(type="配達").values_list("name", flat=True))
    return names


def load_history(since, until):
    """{店名: [行(日付順)]}。行は dict(date, wd, sold, rem, demand, sold_out, lost, level, wind, bridge, qty, rev, one_off)。"""
    excluded = _excluded_names()
    rows = (
        DailyReport.objects
        .filter(date__gte=since, date__lt=until, total_quantity__gt=0)
        .exclude(location__in=excluded)
        .values_list("date", "location", "total_sales_quantity", "total_remaining",
                     "sold_out_time", "opening_time", "closing_time", "lost_customers",
                     "auto_lunch_precip", "auto_lunch_snow", "auto_lunch_wind", "total_quantity", "total_revenue",
                     "unusual_reasons")
        .order_by("date")
    )
    bridges = bridge_days({r[0] for r in rows})
    hist = {}
    for d, name, sold, rem, so_t, op_t, cl_t, lost, precip, snow, wind, qty, rev, reasons in rows:
        sold, rem = int(sold or 0), int(rem or 0)
        hist.setdefault(name.strip(), []).append({
            "date": d, "wd": d.weekday(), "sold": sold, "rem": rem,
            "demand": estimated_demand(sold, rem, so_t, op_t, cl_t, lost), "sold_out": rem <= 0,
            "lost": lost if rem <= 0 else None,
            # 実際の昼の雨の段階（日計表に自動で添えた天気。無い日は None）
            "level": rain_level(precip, snow or 0) if precip is not None else None,
            "wind": wind, "bridge": d in bridges,
            "qty": int(qty or 0), "rev": float(rev or 0),
            # その日だけの理由（出張・ビルの休み・イベント等）の日は、直近の水準に入れない
            "one_off": bool(set(reasons or []) & ONE_OFF_REASONS),
        })
    return hist


def _mean(values):
    return sum(values) / len(values) if values else None


def predict(rows, weekday):
    """rows（予測日より前・日付順）から、その曜日の需要の見込みと根拠を返す。足りなければ None。"""
    if len(rows) < MIN_HISTORY:
        return None
    demand = [r["demand"] for r in rows]
    # 飛び石の日も、その日だけ落ちる日なので水準に入れない（翌週の予測を引き下げない）
    usual = [r["demand"] for r in rows if not r.get("one_off") and not r.get("bridge")]
    if len(usual) < MIN_HISTORY:
        usual = demand
    last = usual[-RECENT_DAYS:]
    weights = list(range(1, len(last) + 1))
    recent = ((1 - RECENT_LONG_SHARE) * sum(w * x for w, x in zip(weights, last)) / sum(weights)
              + RECENT_LONG_SHARE * _mean(usual[-RECENT_LONG_DAYS:]))

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


def rain_multiplier(rows, sales_type):
    """その店の雨の強さ・弱さ（形態ごとの雨の補正に掛ける倍率）と、使った雨の日の数。
    雨の日（完売しなかった日）の「実績 ÷（雨なしの予測 × 形態の補正）」の中央値を、日数が少ないほど1.0に寄せる。
    室内でも雨の日に売れる店（＞1）・落ちる店（＜1）がある。"""
    obs = []
    for i, r in enumerate(rows):
        if not r.get("level") or r["sold_out"] or r["sold"] <= 0:
            continue
        p = predict(rows[:i], r["wd"])
        if not p or p["pred"] <= 0:
            continue
        obs.append(r["demand"] / (p["pred"] * day_factor(r["level"], r.get("wind"), r.get("bridge"), sales_type)))
    if not obs:
        return 1.0, 0
    obs.sort()
    n = len(obs)
    median = obs[n // 2] if n % 2 else (obs[n // 2 - 1] + obs[n // 2]) / 2
    lo, hi = RAIN_STORE_CLIP
    return min(max((n * median + RAIN_STORE_K) / (n + RAIN_STORE_K), lo), hi), n


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


# ---- 3パターンの持参数（売上UP／現状維持／廃棄率重視）
# 予測（需要）に、その店の「実際の需要 ÷ 予測」の散らばりを掛けて、方針ごとに持たせる数を出す。
PLAN_SAMPLE_DAYS = 120      # 散らばりを見る期間
PLAN_MIN_SAMPLES = 30       # これより少ない店は全店の散らばりを足して使う（「実績少」）
PLAN_UP_QUANTILE = 0.8      # 売上UP：10回中8回は売り切れない数（オーナー決定 2026-10-27）
PLAN_WASTE_TARGET = 0.03    # 廃棄率重視：見込み廃棄率3%以下の最大の数（オーナー決定）
PLAN_KEEP_SAMPLES = 20      # 現状維持：直近20回の「持参数÷需要」の中央値
COST_RATE = 0.5             # 粗利の計算の原価率


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _quantile(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def demand_ratios(rows, sales_type, mult, since):
    """直近の「実際の需要 ÷ 予測（雨・強風・飛び石の補正込み）」。完売した日は需要の推定を使う。"""
    out = []
    for i, r in enumerate(rows):
        if r["date"] < since or r["sold"] <= 0:
            continue
        p = predict(rows[:i], r["wd"])
        if not p or p["pred"] <= 0:
            continue
        f = day_factor(r.get("level"), r.get("wind"), r.get("bridge"), sales_type, mult)
        out.append(r["demand"] / (p["pred"] * f))
    return out


def store_plan_basis(rows):
    """現状維持の倍率（持参数÷需要）と1食の値段（直近の平均）。出せなければ None。"""
    recent = [r for r in rows if r["qty"] > 0 and r["demand"] > 0][-PLAN_KEEP_SAMPLES:]
    keep = _median([r["qty"] / r["demand"] for r in recent]) if len(recent) >= 5 else None
    sold = sum(r["sold"] for r in recent)
    price = sum(r["rev"] for r in recent) / sold if sold else None
    return keep, (price if price and price > 0 else None)


def outlook(qty, demands, price):
    """持参数 qty のときの見込み（売れる数・廃棄率・粗利）。demands は需要の散らばり（食数）。"""
    sold = sum(min(qty, d) for d in demands) / len(demands)
    return {
        "qty": qty, "sold": sold,
        "waste_rate": (qty - sold) / qty if qty else 0.0,
        "profit": sold * price - qty * price * COST_RATE if price else None,
    }


def plan_quantities(pred, ratios, keep_ratio, price):
    """{up, keep, waste}：3パターンの持参数と見込み。keep は出せなければ None。"""
    demands = [pred * r for r in ratios]
    up = max(1, int(_quantile(demands, PLAN_UP_QUANTILE) + 0.5))
    waste = 1
    for q in range(1, int(max(demands) + 2)):
        if outlook(q, demands, price)["waste_rate"] > PLAN_WASTE_TARGET:
            break
        waste = q
    plans = {"up": outlook(up, demands, price), "waste": outlook(waste, demands, price), "keep": None}
    if keep_ratio:
        plans["keep"] = outlook(max(1, int(pred * keep_ratio + 0.5)), demands, price)
    return plans
