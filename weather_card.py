"""Mobile-first premium weather card rendered as a Telegram-ready PNG."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from io import BytesIO
from pathlib import Path
from statistics import mean
from typing import Iterable

from astral import Observer
from astral.sun import sun
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1080
HEIGHT = 1480
BACKGROUND = "#090B10"
PANEL = "#141820"
PANEL_EDGE = "#2A3140"
TILE = "#1B202A"
TILE_EDGE = "#303847"
TEXT = "#F7F9FC"
MUTED = "#A3ADBD"
FAINT = "#6C7687"
ACCENT = "#F8C84A"
ACCENT_SECONDARY = "#FF735F"
BLUE = "#70B7FF"
GREEN = "#57D49B"
ORANGE = "#F5A647"
RED = "#FF6B6B"

PERIODS = (
    ("Утром", 6),
    ("Днём", 12),
    ("Вечером", 18),
    ("Ночью", 21),
)

RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
RU_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
)


def _font_candidates(bold: bool) -> list[str]:
    configured = os.getenv("WEATHER_CARD_FONT_BOLD" if bold else "WEATHER_CARD_FONT")
    bundled = Path(__file__).resolve().parent / "assets" / "fonts"
    names = [
        configured,
        str(bundled / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")),
        "C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return [value for value in names if value]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for candidate in _font_candidates(bold):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise RuntimeError("Не найден шрифт погодной карточки.")


FONTS = {
    "title": _font(44, True),
    "subtitle": _font(24),
    "day": _font(32, True),
    "date": _font(24),
    "period": _font(24, True),
    "temperature": _font(50, True),
    "metric": _font(18),
    "metric_bold": _font(20, True),
    "rain": _font(19, True),
    "footer": _font(21),
    "footer_bold": _font(21, True),
    "small": _font(19),
}


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _clean_title(value: str) -> str:
    value = "".join(character for character in value if not 0x1F1E6 <= ord(character) <= 0x1F1FF)
    return re.sub(r"\s+", " ", value).strip()


def _format_temperature(value: object) -> str:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return "—"
    return f"{number:+d}°"


def _format_number(value: object, suffix: str = "") -> str:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return "—"
    return f"{number}{suffix}"


def _timezone_info(current: dict) -> timezone:
    try:
        offset = int(current.get("timezone", 0))
    except (TypeError, ValueError):
        offset = 0
    return timezone(timedelta(seconds=max(-43200, min(50400, offset))))


def _item_datetime(item: dict, tz: timezone) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(item["dt"]), tz=tz)
    except (KeyError, TypeError, ValueError, OSError):
        pass
    try:
        parsed = datetime.strptime(str(item["dt_txt"]), "%Y-%m-%d %H:%M:%S")
        return parsed.replace(tzinfo=timezone.utc).astimezone(tz)
    except (KeyError, TypeError, ValueError):
        return None


def _group_by_local_day(forecast: list[dict], tz: timezone) -> dict[date, list[dict]]:
    grouped: dict[date, list[dict]] = defaultdict(list)
    for item in forecast:
        value = _item_datetime(item, tz)
        if value:
            grouped[value.date()].append(item)
    for items in grouped.values():
        items.sort(key=lambda item: _item_datetime(item, tz) or datetime.min.replace(tzinfo=tz))
    return dict(sorted(grouped.items()))


def _nearest_period(items: list[dict], target_hour: int, tz: timezone) -> dict | None:
    candidates: list[tuple[float, dict]] = []
    for item in items:
        value = _item_datetime(item, tz)
        if not value:
            continue
        target = datetime.combine(value.date(), dt_time(target_hour), tzinfo=tz)
        candidates.append((abs((value - target).total_seconds()) / 3600, item))
    if not candidates:
        return None
    distance, result = min(candidates, key=lambda pair: pair[0])
    return result if distance <= 2.0 else None


def _weather_id(item: dict | None) -> int:
    try:
        return int(((item or {}).get("weather") or [{}])[0].get("id", 800))
    except (TypeError, ValueError):
        return 800


def _draw_weather_icon(draw: ImageDraw.ImageDraw, center: tuple[int, int], condition_id: int, size: int = 42) -> None:
    x, y = center
    clear = condition_id == 800
    rain = 300 <= condition_id < 600
    snow = 600 <= condition_id < 700
    fog = 700 <= condition_id < 800
    storm = 200 <= condition_id < 300

    if fog:
        for offset in (-14, 0, 14):
            draw.rounded_rectangle((x - size, y + offset, x + size, y + offset + 5), radius=3, fill=MUTED)
        return

    if clear:
        radius = int(size * 0.38)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=ACCENT)
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0), (0.7, 0.7), (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7)):
            draw.line((x + dx * (radius + 7), y + dy * (radius + 7), x + dx * (radius + 17), y + dy * (radius + 17)), fill=ACCENT, width=4)
        return

    sun_radius = int(size * 0.27)
    draw.ellipse((x - size * 0.68, y - size * 0.72, x - size * 0.68 + sun_radius * 2, y - size * 0.72 + sun_radius * 2), fill=ACCENT)
    cloud = "#9BBCE8" if not storm else "#8792A8"
    draw.ellipse((x - size * 0.75, y - size * 0.12, x + size * 0.65, y + size * 0.52), fill=cloud)
    draw.ellipse((x - size * 0.48, y - size * 0.58, x + size * 0.18, y + size * 0.42), fill=cloud)
    draw.ellipse((x - size * 0.02, y - size * 0.45, x + size * 0.68, y + size * 0.46), fill=cloud)
    draw.rounded_rectangle((x - size * 0.75, y + size * 0.10, x + size * 0.68, y + size * 0.52), radius=10, fill=cloud)

    if rain:
        for offset in (-22, 0, 22):
            draw.line((x + offset, y + size * 0.65, x + offset - 6, y + size * 0.92), fill=BLUE, width=5)
    elif snow:
        for offset in (-22, 0, 22):
            draw.ellipse((x + offset - 4, y + size * 0.69, x + offset + 4, y + size * 0.86), fill="#D9EEFF")
    elif storm:
        draw.polygon([(x + 3, y + 24), (x - 9, y + 55), (x + 3, y + 53), (x - 5, y + 84), (x + 22, y + 42), (x + 8, y + 44)], fill=ACCENT)


def _wind_direction(degrees: object) -> str:
    try:
        value = float(degrees) % 360
    except (TypeError, ValueError):
        return ""
    return ("С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ")[int((value + 22.5) // 45) % 8]


def _rain_probability(item: dict | None) -> int | None:
    if not item:
        return None
    if item.get("_is_current"):
        condition_id = _weather_id(item)
        has_rain = 200 <= condition_id < 600 or bool(item.get("rain"))
        return 100 if has_rain else 0
    try:
        return max(0, min(100, round(float(item.get("pop", 0)) * 100)))
    except (TypeError, ValueError):
        return None


def _rain_status(item: dict | None) -> tuple[str, str]:
    probability = _rain_probability(item)
    if probability is None:
        return "Нет данных о дожде", FAINT
    if item and item.get("_is_current"):
        return ("Сейчас идёт дождь", BLUE) if probability else ("Сейчас без дождя", GREEN)
    if probability < 20:
        return "Дождя не будет", GREEN
    if probability < 50:
        return f"Дождь маловероятен · {probability}%", MUTED
    if probability < 70:
        return f"Возможен дождь · {probability}%", ORANGE
    return f"Ожидается дождь · {probability}%", BLUE


def _daily_rain_summary(items: Iterable[dict]) -> tuple[str, str]:
    probabilities = [value for item in items if (value := _rain_probability(item)) is not None]
    maximum = max(probabilities, default=0)
    if maximum < 20:
        return "День без дождя", GREEN
    if maximum < 50:
        return f"Дождь маловероятен · до {maximum}%", MUTED
    if maximum < 70:
        return f"Возможен дождь · до {maximum}%", ORANGE
    return f"Ожидается дождь · до {maximum}%", BLUE


def _daily_average(items: Iterable[dict], path: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for item in items:
        current: object = item
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        try:
            values.append(float(current))
        except (TypeError, ValueError):
            continue
    return mean(values) if values else None


def _sun_times(day: date, lat: float, lon: float, tz: timezone) -> tuple[datetime | None, datetime | None]:
    try:
        result = sun(Observer(latitude=lat, longitude=lon), date=day, tzinfo=tz)
        return result.get("sunrise"), result.get("sunset")
    except (ValueError, KeyError, OverflowError):
        return None, None


def _time_text(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "—"


def _duration_text(start: datetime | None, end: datetime | None) -> str:
    if not start or not end or end <= start:
        return "—"
    minutes = int((end - start).total_seconds() // 60)
    return f"{minutes // 60} ч {minutes % 60:02d} мин"


def _date_heading(day: date, today: date) -> tuple[str, str]:
    if day == today:
        label = "Сегодня"
    elif day == today + timedelta(days=1):
        label = "Завтра"
    else:
        label = RU_WEEKDAYS[day.weekday()].capitalize()
    return label, f"{day.day} {RU_MONTHS[day.month - 1]}"


def _current_item(current: dict, now: datetime) -> dict:
    return {
        **current,
        "dt": int(now.timestamp()),
        "_is_current": True,
    }


def _panel_slots(day: date, items: list[dict], today: date, current: dict, tz: timezone) -> list[tuple[str, dict | None]]:
    if day == today:
        now = datetime.now(tz=tz)
        future = [item for item in items if (_item_datetime(item, tz) or now) > now]
        slots: list[tuple[str, dict | None]] = [("Сейчас", _current_item(current, now))]
        for item in future[:3]:
            value = _item_datetime(item, tz)
            slots.append((value.strftime("В %H:%M") if value else "Позже", item))
        while len(slots) < 4:
            slots.append(("Позже", None))
        return slots
    return [(label, _nearest_period(items, hour, tz)) for label, hour in PERIODS]


def _draw_rain_pill(draw: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int], text: str, color: str) -> None:
    draw.rounded_rectangle(bounds, radius=18, fill="#202833", outline=color, width=2)
    left, top, right, _ = bounds
    text_x = left + (right - left - _text_width(draw, text, FONTS["rain"])) / 2
    draw.text((text_x, top + 8), text, font=FONTS["rain"], fill=color)


def _draw_slot(draw: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int], label: str, item: dict | None) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=24, fill=TILE, outline=TILE_EDGE, width=2)
    draw.text((left + 24, top + 20), label, font=FONTS["period"], fill=TEXT)

    if not item:
        draw.text((left + 24, top + 84), "Нет данных", font=FONTS["metric_bold"], fill=FAINT)
        _draw_rain_pill(draw, (left + 24, bottom - 54, right - 24, bottom - 14), "Прогноз ещё не доступен", FAINT)
        return

    main = item.get("main", {})
    wind = item.get("wind", {})
    _draw_weather_icon(draw, (left + 68, top + 108), _weather_id(item), size=36)
    draw.text((left + 124, top + 58), _format_temperature(main.get("temp")), font=FONTS["temperature"], fill=TEXT)
    feels = _format_temperature(main.get("feels_like"))
    metrics_x = left + 280
    draw.text((metrics_x, top + 70), f"Ощущается: {feels}", font=FONTS["metric"], fill=MUTED)

    wind_speed = _format_number(wind.get("speed"))
    direction = _wind_direction(wind.get("deg"))
    wind_text = f"Ветер {wind_speed} м/с"
    if direction:
        wind_text += f" · {direction}"
    draw.text((metrics_x, top + 104), wind_text, font=FONTS["metric"], fill=TEXT)

    pressure_hpa = main.get("pressure")
    pressure = "—" if pressure_hpa is None else _format_number(float(pressure_hpa) * 0.750062)
    draw.text((metrics_x, top + 138), f"Давление: {pressure} мм", font=FONTS["metric"], fill=MUTED)

    rain_text, rain_color = _rain_status(item)
    _draw_rain_pill(draw, (left + 24, bottom - 54, right - 24, bottom - 14), rain_text, rain_color)


def _draw_day_panel(
    draw: ImageDraw.ImageDraw,
    top: int,
    day: date,
    items: list[dict],
    today: date,
    index: int,
    tz: timezone,
    lat: float,
    lon: float,
    current: dict,
) -> None:
    left, right, height = 28, WIDTH - 28, 640
    bottom = top + height
    accent = ACCENT if index == 0 else ACCENT_SECONDARY
    draw.rounded_rectangle((left, top, right, bottom), radius=34, fill=PANEL, outline=PANEL_EDGE, width=2)

    label, date_text = _date_heading(day, today)
    draw.text((left + 32, top + 28), label, font=FONTS["day"], fill=accent)
    label_width = _text_width(draw, label, FONTS["day"])
    draw.text((left + 46 + label_width, top + 35), f"· {date_text}", font=FONTS["date"], fill=TEXT)

    slots = _panel_slots(day, items, today, current, tz)
    summary, summary_color = _daily_rain_summary(item for _, item in slots if item)
    summary_width = _text_width(draw, summary, FONTS["rain"])
    draw.ellipse((right - summary_width - 64, top + 39, right - summary_width - 50, top + 53), fill=summary_color)
    draw.text((right - summary_width - 38, top + 32), summary, font=FONTS["rain"], fill=summary_color)

    tile_left = left + 32
    tile_top = top + 94
    tile_gap = 18
    tile_width = (right - left - 64 - tile_gap) // 2
    tile_height = 218
    for slot_index, (period, item) in enumerate(slots):
        column = slot_index % 2
        row = slot_index // 2
        x1 = tile_left + column * (tile_width + tile_gap)
        y1 = tile_top + row * (tile_height + tile_gap)
        _draw_slot(draw, (x1, y1, x1 + tile_width, y1 + tile_height), period, item)

    sunrise, sunset = _sun_times(day, lat, lon, tz)
    footer_top = top + 570
    draw.line((left + 32, footer_top - 12, right - 32, footer_top - 12), fill=TILE_EDGE, width=2)
    footer_items = (
        ("Восход", _time_text(sunrise)),
        ("Закат", _time_text(sunset)),
        ("Световой день", _duration_text(sunrise, sunset)),
    )
    column_width = (right - left - 64) // 3
    for column, (name, value) in enumerate(footer_items):
        x = left + 32 + column * column_width
        draw.text((x, footer_top), name, font=FONTS["footer"], fill=MUTED)
        name_width = _text_width(draw, name, FONTS["footer"])
        draw.text((x + name_width + 12, footer_top), value, font=FONTS["footer_bold"], fill=TEXT)


def render_weather_card(
    forecast: list[dict],
    current_weather: dict,
    lat: float,
    lon: float,
    title: str,
) -> BytesIO:
    """Return a seeked, mobile-readable PNG stream for ``bot.send_photo``."""
    tz = _timezone_info(current_weather)
    grouped = _group_by_local_day(forecast, tz)
    if not grouped:
        raise ValueError("Прогноз не содержит корректных дат.")

    today = datetime.now(tz=tz).date()
    days = list(grouped)[:2]
    if today in grouped:
        days = [today] + [day for day in days if day != today]
    while len(days) < 2:
        days.append(days[-1] + timedelta(days=1))
        grouped.setdefault(days[-1], [])
    days = days[:2]

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    clean_title = _clean_title(title)
    draw.text((38, 24), clean_title, font=FONTS["title"], fill=TEXT)
    draw.text((40, 78), "Понятный прогноз на сегодня и завтра", font=FONTS["subtitle"], fill=MUTED)
    updated = datetime.now(tz=tz).strftime("Обновлено в %H:%M")
    draw.text((WIDTH - 38 - _text_width(draw, updated, FONTS["small"]), 42), updated, font=FONTS["small"], fill=MUTED)

    _draw_day_panel(draw, 120, days[0], grouped[days[0]], today, 0, tz, lat, lon, current_weather)
    _draw_day_panel(draw, 790, days[1], grouped[days[1]], today, 1, tz, lat, lon, current_weather)

    output = BytesIO()
    output.name = "weather_forecast.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output

