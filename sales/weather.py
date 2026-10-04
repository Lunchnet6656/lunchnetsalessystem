"""昼どきの天気予報（Open-Meteo・東京）と、販売形式ごとの雨の効き方。

実データ（2025-09〜2026-10、昼11〜13時の雨量×販売形式）で、外の売場は雨で減り、
室内（ビル内）は減らない（むしろ少し増える）ことが分かった。一律に減らすと室内が完売する。
"""
import json
import urllib.request

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=35.667&longitude=139.75"
    "&hourly=precipitation,snowfall,temperature_2m,apparent_temperature"
    "&timezone=Asia%2FTokyo&forecast_days=7"
)
LUNCH_HOURS = (11, 12)  # 11:00〜12:59

# (下限mm, 段階)。雪が少しでもあれば大雨扱い
RAIN_LEVELS = [(8.0, "大雨"), (3.0, "雨"), (1.0, "小雨")]

# 昼に雨のない日を1としたときの目安（実測を丸めた値。室内・配達は減らさない）
RAIN_FACTORS = {
    "小雨": {"テーブル": 0.85, "行商": 0.85, "車": 0.90},
    "雨": {"テーブル": 0.80, "行商": 0.80, "車": 0.85},
    "大雨": {"テーブル": 0.60, "行商": 0.60, "車": 0.80},
}


def rain_level(precip_mm, snow_cm):
    if snow_cm and snow_cm > 0:
        return "大雨"
    for threshold, level in RAIN_LEVELS:
        if precip_mm >= threshold:
            return level
    return None


def rain_factor(level, sales_type):
    if not level:
        return 1.0
    return RAIN_FACTORS[level].get(sales_type, 1.0)


def fetch_lunch_weather():
    """日付('YYYY-MM-DD')ごとの昼の予報。取得できなければ例外を投げる（呼び出し側で扱う）。"""
    with urllib.request.urlopen(OPEN_METEO_URL, timeout=5) as resp:
        data = json.loads(resp.read().decode())
    hourly = data.get("hourly", {})
    days = {}
    for t, p, s, temp, feels in zip(
        hourly.get("time", []), hourly.get("precipitation", []), hourly.get("snowfall", []),
        hourly.get("temperature_2m", []), hourly.get("apparent_temperature", []),
    ):
        if int(t[11:13]) not in LUNCH_HOURS:
            continue
        d = days.setdefault(t[:10], {"precip": 0.0, "snow": 0.0, "temps": [], "feels": []})
        d["precip"] += p or 0
        d["snow"] += s or 0
        if temp is not None:
            d["temps"].append(temp)
        if feels is not None:
            d["feels"].append(feels)
    result = {}
    for day, d in days.items():
        precip, snow = round(d["precip"], 1), round(d["snow"], 1)
        result[day] = {
            "precip": precip,
            "snow": snow,
            "temp": round(sum(d["temps"]) / len(d["temps"]), 1) if d["temps"] else None,
            "feels": round(sum(d["feels"]) / len(d["feels"]), 1) if d["feels"] else None,
            "level": rain_level(precip, snow),
        }
    return result
