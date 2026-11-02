import os
import time
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

OW_API_KEY_NAMES = ("OW_API_KEY", "API_KEY")

GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/direct"
REVERSE_GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/reverse"
CURRENT_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
AIR_POLLUTION_URL = "https://api.openweathermap.org/data/2.5/air_pollution"

RETRY_DELAYS = (1, 2, 4)


class WeatherAPIError(Exception):
    """User-safe OpenWeather error."""


def _api_key() -> str | None:
    for key_name in OW_API_KEY_NAMES:
        value = os.getenv(key_name)
        if value and value != "your_openweather_key" and value != "...":
            return value
    return None


def _request_json(
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int = 0,
    timeout_s: float = 7,
) -> dict | list | None:
    api_key = _api_key()
    if not api_key:
        return {"error": "Не задан OW_API_KEY или API_KEY в .env."}

    request_params = {**params, "appid": api_key}
    max_retries = max(0, min(max_retries, len(RETRY_DELAYS)))
    attempts = max_retries + 1

    for attempt in range(attempts):
        try:
            response = requests.get(url, params=request_params, timeout=timeout_s)
        except requests.RequestException:
            if attempt == attempts - 1:
                return {"error": "Не удалось подключиться к OpenWeather. Попробуйте позже."}
            time.sleep(RETRY_DELAYS[attempt])
            continue

        if response.status_code == 429 and attempt < max_retries:
            time.sleep(RETRY_DELAYS[attempt])
            continue

        if 400 <= response.status_code < 600:
            return {"error": _format_http_error(response)}

        try:
            return response.json()
        except ValueError:
            return {"error": "OpenWeather вернул некорректный ответ."}

    return {"error": "OpenWeather временно недоступен. Попробуйте позже."}


def _format_http_error(response: requests.Response) -> str:
    if response.status_code == 429:
        return "Слишком много запросов к OpenWeather. Попробуйте позже."
    if response.status_code == 401:
        return "Неверный API-ключ OpenWeather."
    if response.status_code == 404:
        return "Город не найден."

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    message = payload.get("message")
    if message:
        return f"Ошибка OpenWeather: {message}."
    return f"Ошибка OpenWeather: HTTP {response.status_code}."


def has_error(data: Any) -> bool:
    return isinstance(data, dict) and bool(data.get("error"))


def error_message(data: Any, default: str = "Не удалось получить данные.") -> str:
    if has_error(data):
        return str(data["error"])
    return default


def search_locations(query: str, limit: int = 5) -> list[dict]:
    """Find cities worldwide and return normalized OpenWeather locations."""
    if not query.strip():
        return []

    data = _request_json(
        GEOCODING_URL,
        {
            "q": query.strip(),
            "limit": max(1, min(limit, 5)),
            "lang": "ru",
        },
    )
    if has_error(data):
        return [{"error": error_message(data)}]
    if not isinstance(data, list):
        return []

    # OpenWeather officially expects a two-letter country code. If a user
    # enters a full country name, retry by city and offer matching countries.
    if not data and "," in query:
        city_only = query.split(",", 1)[0].strip()
        data = _request_json(
            GEOCODING_URL,
            {
                "q": city_only,
                "limit": max(1, min(limit, 5)),
                "lang": "ru",
            },
        )
        if has_error(data):
            return [{"error": error_message(data)}]
        if not isinstance(data, list):
            return []

    locations = []
    for item in data:
        try:
            name = (item.get("local_names") or {}).get("ru") or item["name"]
            locations.append(
                {
                    "name": str(name),
                    "state": str(item.get("state", "")),
                    "country": str(item.get("country", "")).upper(),
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return locations


def get_coordinates(city: str, limit: int = 1) -> tuple[float, float] | None:
    locations = search_locations(city, limit)
    if not locations or has_error(locations[0]):
        return None

    return locations[0]["lat"], locations[0]["lon"]


def get_location_by_coordinates(lat: float, lon: float) -> dict | None:
    data = _request_json(
        REVERSE_GEOCODING_URL,
        {
            "lat": lat,
            "lon": lon,
            "limit": 1,
            "lang": "ru",
        },
    )
    if has_error(data) or not isinstance(data, list) or not data:
        return None

    item = data[0]
    try:
        name = (item.get("local_names") or {}).get("ru") or item["name"]
        return {
            "name": str(name),
            "state": str(item.get("state", "")),
            "country": str(item.get("country", "")).upper(),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def get_city_name_by_coordinates(lat: float, lon: float) -> str | None:
    location = get_location_by_coordinates(lat, lon)
    return location["name"] if location else None


def get_current_weather(
    lat: float,
    lon: float,
    *,
    max_retries: int = 0,
    timeout_s: float = 7,
) -> dict:
    data = _request_json(
        CURRENT_WEATHER_URL,
        {
            "lat": lat,
            "lon": lon,
            "units": "metric",
            "lang": "ru",
        },
        max_retries=max_retries,
        timeout_s=timeout_s,
    )
    return data if isinstance(data, dict) else {"error": "Не удалось получить текущую погоду."}


def get_forecast_5d3h(lat: float, lon: float) -> list[dict]:
    data = _request_json(
        FORECAST_URL,
        {
            "lat": lat,
            "lon": lon,
            "units": "metric",
            "lang": "ru",
        },
    )
    if has_error(data):
        return [{"error": error_message(data)}]
    if not isinstance(data, dict):
        return [{"error": "Не удалось получить прогноз."}]

    forecast = data.get("list")
    return forecast if isinstance(forecast, list) else [{"error": "Прогноз пуст."}]


def get_air_pollution(lat: float, lon: float) -> dict:
    data = _request_json(
        AIR_POLLUTION_URL,
        {
            "lat": lat,
            "lon": lon,
        },
        max_retries=0,
        timeout_s=5,
    )
    if has_error(data):
        return {"error": error_message(data)}
    if not isinstance(data, dict):
        return {"error": "Не удалось получить данные о качестве воздуха."}

    items = data.get("list")
    if not isinstance(items, list) or not items:
        return {"error": "Данные о качестве воздуха пусты."}

    components = items[0].get("components", {})
    return components if isinstance(components, dict) else {"error": "Данные о качестве воздуха пусты."}


def analyze_air_pollution(components: dict, extended: bool = False) -> dict:
    if has_error(components):
        return {"status": "Нет данных", "summary": error_message(components), "details": {}}

    rules = {
        "pm2_5": (15, 35, "PM2.5"),
        "pm10": (45, 100, "PM10"),
        "no2": (40, 100, "NO2"),
        "so2": (40, 125, "SO2"),
        "o3": (100, 180, "O3"),
        "co": (4000, 10000, "CO"),
    }

    worst_level = 0
    details = {}
    for key, (good_limit, warning_limit, label) in rules.items():
        value = components.get(key)
        if value is None:
            continue

        if value <= good_limit:
            level = 0
            status = "норма"
        elif value <= warning_limit:
            level = 1
            status = "повышено"
        else:
            level = 2
            status = "высоко"

        worst_level = max(worst_level, level)
        details[label] = {"value": value, "status": status}

    statuses = {
        0: ("Хороший", "Качество воздуха хорошее."),
        1: ("Умеренный", "Есть повышенные показатели, чувствительным людям стоит быть внимательнее."),
        2: ("Плохой", "Загрязнение воздуха высокое, лучше сократить длительные прогулки."),
    }
    status, summary = statuses[worst_level]

    result = {"status": status, "summary": summary}
    if extended:
        result["details"] = details
    return result
