"""
날씨 도구 - Open-Meteo API (무료, API 키 불필요, 즉시 응답)

현재 날씨 + 3일간 예보를 구조화된 형태로 반환.
"""

import json
import urllib.request
import urllib.parse


# 한국어 도시명 → 위도/경도 매핑
_CITY_COORDS = {
    # 한국 주요 도시
    "서울": (37.5665, 126.978), "부산": (35.1796, 129.0756), "인천": (37.4563, 126.7052),
    "대구": (35.8714, 128.6014), "대전": (36.3504, 127.3845), "광주": (35.1595, 126.8526),
    "울산": (35.5384, 129.3114), "수원": (37.2636, 127.0286), "세종": (36.4800, 127.2550),
    "제주": (33.4996, 126.5312), "강릉": (37.7519, 128.8761), "춘천": (37.8813, 127.7298),
    "전주": (35.8242, 127.1480), "청주": (36.6424, 127.4890), "포항": (36.0190, 129.3435),
    "경주": (35.8562, 129.2247), "여수": (34.7604, 127.6622), "목포": (34.8118, 126.3922),
    "속초": (38.2070, 128.5918), "안동": (36.5684, 128.7294), "김해": (35.2285, 128.8894),
    "창원": (35.2270, 128.6811), "성남": (37.4200, 127.1263), "고양": (37.6584, 126.8320),
    "용인": (37.2411, 127.1776), "평택": (36.9921, 127.0853), "천안": (36.8151, 127.1139),
    "원주": (37.3422, 127.9202), "파주": (37.7599, 126.7803), "남양주": (37.6360, 127.2165),
    "양양": (38.0753, 128.6190), "화성": (37.1995, 126.8311),
    # 해외 주요 도시
    "도쿄": (35.6762, 139.6503), "오사카": (34.6937, 135.5023),
    "뉴욕": (40.7128, -74.0060), "런던": (51.5074, -0.1278),
    "파리": (48.8566, 2.3522), "베이징": (39.9042, 116.4074),
    "상하이": (31.2304, 121.4737), "방콕": (13.7563, 100.5018),
    "싱가포르": (1.3521, 103.8198), "호놀룰루": (21.3069, -157.8583),
    "LA": (34.0522, -118.2437), "샌프란시스코": (37.7749, -122.4194),
    "시드니": (-33.8688, 151.2093), "홍콩": (22.3193, 114.1694),
    "타이베이": (25.0330, 121.5654), "하노이": (21.0285, 105.8542),
}


# WMO weather codes → emoji + description
_WMO_CODES = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌧️", "Drizzle"), 55: ("🌧️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("⛈️", "Heavy rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
    77: ("🌨️", "Sleet"), 80: ("🌦️", "Light showers"), 81: ("🌧️", "Showers"), 82: ("⛈️", "Heavy showers"),
    85: ("🌨️", "Light snow showers"), 86: ("❄️", "Heavy snow showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm with hail"), 99: ("⛈️", "Severe thunderstorm with hail"),
}


def _resolve_coords(location: str) -> tuple:
    """도시명 → (위도, 경도) 변환"""
    # 매핑에서 찾기
    for name, coords in _CITY_COORDS.items():
        if name in location:
            return coords

    # 영문 도시명도 처리
    loc_lower = location.lower().strip()
    en_map = {
        "seoul": (37.5665, 126.978), "busan": (35.1796, 129.0756),
        "tokyo": (35.6762, 139.6503), "new york": (40.7128, -74.0060),
        "london": (51.5074, -0.1278), "paris": (48.8566, 2.3522),
        "beijing": (39.9042, 116.4074), "bangkok": (13.7563, 100.5018),
        "singapore": (1.3521, 103.8198), "jeju": (33.4996, 126.5312),
    }
    for name, coords in en_map.items():
        if name in loc_lower:
            return coords

    # 기본: 서울
    return (37.5665, 126.978)


def _get_city_name(location: str) -> str:
    """입력에서 도시명 추출"""
    for name in _CITY_COORDS:
        if name in location:
            return name
    # 불필요 단어 제거
    clean = location
    for remove in ["날씨", "기온", "예보", "오늘", "내일", "모레", "이번주", "주말",
                    "3일", "일주일", "weather", "의", "에서", "지역", "알려줘", "어때"]:
        clean = clean.replace(remove, "")
    return clean.strip() or "서울"


def _wmo_desc(code: int) -> tuple:
    """WMO 코드 → (이모지, 설명)"""
    return _WMO_CODES.get(code, ("🌡️", f"Code {code}"))


def _wind_direction(degrees: float) -> str:
    """풍향 각도 → 방위"""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(degrees / 22.5) % 16
    return dirs[idx]


def tool_weather(location: str, days: int = 3) -> str:
    """Open-Meteo API로 날씨 조회 (현재 + 3일 예보)"""
    try:
        lat, lon = _resolve_coords(location)
        city = _get_city_name(location)

        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,wind_speed_10m_max,sunrise,sunset",
            "timezone": "Asia/Seoul",
            "forecast_days": min(days, 7),
        })

        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Androi/1.0"})

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # ── 현재 날씨 ──
        cur = data.get("current", {})
        temp = cur.get("temperature_2m", "?")
        feels = cur.get("apparent_temperature", "?")
        humidity = cur.get("relative_humidity_2m", "?")
        wind_speed = cur.get("wind_speed_10m", "?")
        wind_deg = cur.get("wind_direction_10m", 0)
        precip = cur.get("precipitation", 0)
        wmo_code = cur.get("weather_code", 0)

        emoji, desc = _wmo_desc(wmo_code)
        wind_dir = _wind_direction(wind_deg)

        lines = []
        lines.append(f"📍 {city}")
        lines.append(f"")
        lines.append(f"{emoji} Current: {desc}")
        lines.append(f"🌡️ Temp: {temp}°C (feels like {feels}°C)")
        lines.append(f"💧 Humidity: {humidity}% | Precip: {precip}mm")
        lines.append(f"💨 Wind: {wind_speed}km/h {wind_dir}")

        # ── 일별 예보 ──
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        maxs = daily.get("temperature_2m_max", [])
        mins = daily.get("temperature_2m_min", [])
        rain_probs = daily.get("precipitation_probability_max", [])
        codes = daily.get("weather_code", [])
        winds = daily.get("wind_speed_10m_max", [])
        sunrises = daily.get("sunrise", [])
        sunsets = daily.get("sunset", [])

        if dates:
            lines.append(f"\n{'─' * 35}")
            lines.append(f"📅 {len(dates)}-day forecast")
            lines.append(f"{'─' * 35}")

            for i, date in enumerate(dates):
                d_emoji, d_desc = _wmo_desc(codes[i] if i < len(codes) else 0)
                max_t = maxs[i] if i < len(maxs) else "?"
                min_t = mins[i] if i < len(mins) else "?"
                rain_p = rain_probs[i] if i < len(rain_probs) else 0
                wind_m = winds[i] if i < len(winds) else "?"

                line = f"{d_emoji} {date}: {min_t}°C ~ {max_t}°C | {d_desc}"
                if rain_p and int(rain_p) > 0:
                    line += f" | 🌧️ Precip {rain_p}%"
                line += f" | 💨 Max {wind_m}km/h"
                lines.append(line)

                # 일출/일몰
                if i < len(sunrises) and i < len(sunsets):
                    sr = sunrises[i].split("T")[1] if "T" in sunrises[i] else sunrises[i]
                    ss = sunsets[i].split("T")[1] if "T" in sunsets[i] else sunsets[i]
                    lines.append(f"    🌅 {sr} ~ 🌇 {ss}")

        return "\n".join(lines)

    except Exception as e:
        return f"Weather query error: {e}\n💡 Please check the city name. (e.g., Seoul, Busan, Jeju, Tokyo)"
