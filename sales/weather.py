"""昼どきの天気予報（Open-Meteo・東京）と、販売形式ごとの雨の効き方。

実データ（2025-09〜2026-10、昼11〜13時の雨量×販売形式）で、外の売場は雨で減り、
室内（ビル内）は減らない（むしろ少し増える）ことが分かった。一律に減らすと室内が完売する。
"""
import json
import urllib.request

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=precipitation,snowfall,temperature_2m,apparent_temperature"
    "&timezone=Asia%2FTokyo&forecast_days=7"
)
LUNCH_HOURS = (11, 12)  # 11:00〜12:59

# 予報を取る地点。横浜は雨の日の半分近くで都心と予報が違う（2025-09〜の前日予報で、
# 雨の段階が新橋と違った日：日本大通り12/27日・新横浜13/30日・渋谷5/27日）
WEATHER_AREAS = {
    "tokyo": ("東京", 35.667, 139.75),          # 新橋・内幸町
    "yokohama": ("横浜みなと", 35.444, 139.643),  # 日本大通り
    "shinyokohama": ("新横浜", 35.507, 139.617),
}
DEFAULT_AREA = "tokyo"

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


def fetch_lunch_weather(area=DEFAULT_AREA):
    """日付('YYYY-MM-DD')ごとの昼の予報。取得できなければ例外を投げる（呼び出し側で扱う）。"""
    _, lat, lon = WEATHER_AREAS[area]
    with urllib.request.urlopen(OPEN_METEO_URL.format(lat=lat, lon=lon), timeout=5) as resp:
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


def fetch_all_areas():
    """{地点: {日付: 昼の予報}}。取れなかった地点は入れない。"""
    result = {}
    for area in WEATHER_AREAS:
        try:
            result[area] = fetch_lunch_weather(area)
        except Exception:
            continue
    return result


RECENT_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=precipitation,snowfall,temperature_2m,apparent_temperature,wind_speed_10m"
    "&wind_speed_unit=ms&timezone=Asia%2FTokyo&past_days={past}&forecast_days=1"
)


ARCHIVE_URL = (
    "https://archive-api.open-meteo.com/v1/archive"
    "?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
    "&hourly=precipitation,snowfall,temperature_2m,apparent_temperature,wind_speed_10m"
    "&wind_speed_unit=ms&timezone=Asia%2FTokyo"
)


def _lunch_actuals(h):
    days = {}
    for t, p, s, temp, feels, wind in zip(h.get("time", []), h.get("precipitation", []), h.get("snowfall", []),
                                          h.get("temperature_2m", []), h.get("apparent_temperature", []),
                                          h.get("wind_speed_10m", [])):
        if int(t[11:13]) not in LUNCH_HOURS:
            continue
        d = days.setdefault(t[:10], {"precip": 0.0, "snow": 0.0, "temp": [], "feels": [], "wind": []})
        d["precip"] += p or 0
        d["snow"] += s or 0
        for key, v in (("temp", temp), ("feels", feels), ("wind", wind)):
            if v is not None:
                d[key].append(v)
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    return {day: {"precip": round(d["precip"], 1), "snow": round(d["snow"], 1), "temp": avg(d["temp"]),
                  "feels": avg(d["feels"]), "wind": avg(d["wind"])} for day, d in days.items()}


def fetch_lunch_recent(area=DEFAULT_AREA, past_days=7):
    """直近の日の昼（11〜13時）の天気の実際の値。日計表に自動で添えるため。
    {日付: {precip, snow, temp, feels, wind}}。取れなければ例外。"""
    _, lat, lon = WEATHER_AREAS[area]
    with urllib.request.urlopen(RECENT_URL.format(lat=lat, lon=lon, past=past_days), timeout=5) as resp:
        data = json.loads(resp.read().decode())
    return _lunch_actuals(data.get("hourly", {}))


def fetch_lunch_archive(area, start, end):
    """過去の日の昼の天気の実際の値（数日前まで）。過去の日計表を埋めるときだけ使う。形は fetch_lunch_recent と同じ。"""
    _, lat, lon = WEATHER_AREAS[area]
    url = ARCHIVE_URL.format(lat=lat, lon=lon, start=start.isoformat(), end=end.isoformat())
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return _lunch_actuals(data.get("hourly", {}))
