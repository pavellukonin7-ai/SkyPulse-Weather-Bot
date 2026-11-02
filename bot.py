import os
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape

import telebot
from dotenv import load_dotenv
from telebot import types

from storage import load_all_users, load_user, save_user
from weather_card import render_weather_card
from weather_app import (
    analyze_air_pollution,
    error_message,
    get_air_pollution,
    get_current_weather,
    get_forecast_5d3h,
    get_location_by_coordinates,
    has_error,
    search_locations,
)


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("weather_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN == "your_telegram_token":
    raise RuntimeError("Укажите BOT_TOKEN в файле .env.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
user_states: dict[int, dict] = defaultdict(dict)
notified_at: dict[int, float] = defaultdict(float)


def main_menu() -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row("🌤 Сейчас", "🔎 Найти город")
    keyboard.row("📅 Прогноз", "⚖️ Сравнить")
    keyboard.row("📍 Геолокация", "🌿 Воздух")
    keyboard.row("🔔 Уведомления")
    return keyboard


def back_menu() -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("← Назад")
    return keyboard


def location_keyboard() -> types.ReplyKeyboardMarkup:
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    button = types.KeyboardButton("📍 Отправить местоположение", request_location=True)
    keyboard.add(button)
    keyboard.add("← Назад")
    return keyboard


def notification_keyboard(enabled: bool) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    toggle_text = "🔕 Выключить" if enabled else "🔔 Включить"
    keyboard.add(types.InlineKeyboardButton(toggle_text, callback_data="notify:toggle"))
    keyboard.row(
        types.InlineKeyboardButton("1 ч", callback_data="notify:interval:1"),
        types.InlineKeyboardButton("2 ч", callback_data="notify:interval:2"),
        types.InlineKeyboardButton("4 ч", callback_data="notify:interval:4"),
    )
    return keyboard


def forecast_keyboard(days: list[str]) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    for index, day in enumerate(days):
        keyboard.add(types.InlineKeyboardButton(forecast_day_label(day), callback_data=f"forecast:day:{index}"))
    keyboard.add(types.InlineKeyboardButton("← В меню", callback_data="forecast:back"))
    return keyboard


def forecast_day_label(day: str) -> str:
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    months = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
    try:
        value = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{weekdays[value.weekday()]} · {value.day} {months[value.month - 1]}"


def set_state(user_id: int, name: str, **extra) -> None:
    user_states[user_id] = {"name": name, **extra}


def clear_state(user_id: int) -> None:
    user_states.pop(user_id, None)


def remember_chat(user_id: int, chat_id: int) -> None:
    data = load_user(user_id)
    if data.get("chat_id") != chat_id:
        data["chat_id"] = chat_id
        data.setdefault("notifications", {"enabled": False, "interval_h": 2})
        save_user(user_id, data)


def action_name(text: str) -> str:
    for prefix in ("🌤️", "🌤", "🔎", "📅", "📍", "🔔", "⚖️", "🌫️", "🌿", "←"):
        text = text.replace(prefix, "")
    return " ".join(text.split()).strip()


def country_flag(country_code: str) -> str:
    code = country_code.upper()
    if len(code) != 2 or not code.isalpha():
        return "🌍"
    return "".join(chr(127397 + ord(letter)) for letter in code)


def location_title(location: dict, include_state: bool = False) -> str:
    name = str(location.get("name", "Неизвестный город"))
    state = str(location.get("state", ""))
    country = str(location.get("country", "")).upper()
    parts = [name]
    if include_state and state and state.casefold() != name.casefold():
        parts.append(state)
    place = ", ".join(parts)
    return f"{place} {country_flag(country)} {country}".strip()


def resolve_user_location(user_id: int) -> tuple[float, float, str] | None:
    user = load_user(user_id)
    lat = user.get("lat")
    lon = user.get("lon")
    if lat is not None and lon is not None:
        title = user.get("city", "вашей геолокации")
        if title in ("Моя геолокация", "вашей геолокации") or not user.get("country"):
            location = get_location_by_coordinates(float(lat), float(lon))
            if location:
                user["city"] = location["name"]
                user["country"] = location["country"]
                user["state"] = location["state"]
                save_user(user_id, user)
                title = location_title(location)
        elif user.get("country"):
            title = location_title(
                {
                    "name": title,
                    "country": user.get("country", ""),
                    "state": user.get("state", ""),
                }
            )
        return float(lat), float(lon), title
    return None


def save_city(
    user_id: int,
    city: str,
    lat: float,
    lon: float,
    country: str = "",
    state: str = "",
) -> None:
    data = load_user(user_id)
    data.update(
        {
            "city": city,
            "country": country,
            "state": state,
            "lat": lat,
            "lon": lon,
        }
    )
    data.setdefault("notifications", {"enabled": False, "interval_h": 2})
    save_user(user_id, data)


def format_number(value: object, digits: int = 1) -> str:
    try:
        number = round(float(value), digits)
    except (TypeError, ValueError):
        return "—"
    return str(int(number)) if number.is_integer() else str(number)


def weather_emoji(weather: dict) -> str:
    weather_items = weather.get("weather", [{}])
    condition_id = weather_items[0].get("id", 800)
    if 200 <= condition_id < 300:
        return "⛈️"
    if 300 <= condition_id < 600:
        return "🌧️"
    if 600 <= condition_id < 700:
        return "🌨️"
    if 700 <= condition_id < 800:
        return "🌫️"
    if condition_id == 800:
        return "☀️"
    return "⛅"


def is_raining(weather: dict) -> bool:
    weather_items = weather.get("weather", [{}])
    try:
        condition_id = int(weather_items[0].get("id", 800))
    except (TypeError, ValueError):
        condition_id = 800
    return 200 <= condition_id < 600 or bool(weather.get("rain"))


def current_rain_status(weather: dict) -> str:
    """Describe precipitation without exposing ambiguous raw humidity data."""
    if is_raining(weather):
        return "🌧️ <b>Дождь:</b> <b>идёт сейчас</b>"
    return "🌂 <b>Дождь:</b> <b>сейчас не ожидается</b>"


def format_current_weather(weather: dict, title: str = "Погода") -> str:
    if has_error(weather):
        return error_message(weather)

    main = weather.get("main", {})
    wind = weather.get("wind", {})
    weather_items = weather.get("weather", [{}])
    description = escape(str(weather_items[0].get("description", "Нет данных")).capitalize())
    temp = format_number(main.get("temp"))
    feels_like = format_number(main.get("feels_like"))
    pressure = format_number(main.get("pressure"), 0)
    wind_speed = format_number(wind.get("speed"))
    safe_title = escape(title)
    icon = weather_emoji(weather)

    return (
        f"{icon} <b>{safe_title}</b>\n"
        f"<i>{description}</i>\n\n"
        f"🌡️ <b>{temp}°C</b>  ·  ощущается как <b>{feels_like}°C</b>\n"
        f"{current_rain_status(weather)}\n"
        f"💨 Ветер  <b>{wind_speed} м/с</b>\n"
        f"📊 Давление  <b>{pressure} гПа</b>"
    )


def format_air_analysis(analysis: dict) -> str:
    lines = [
        "🌿 <b>Качество воздуха</b>",
        f"Статус  <b>{escape(str(analysis.get('status', 'Нет данных')))}</b>",
        f"<i>{escape(str(analysis.get('summary', '')))}</i>",
    ]

    details = analysis.get("details", {})
    for name, item in details.items():
        lines.append(
            f"{escape(name)}  <b>{format_number(item['value'])}</b> мкг/м³ · "
            f"{escape(str(item['status']))}"
        )
    return "\n".join(line for line in lines if line)


def grouped_forecast(forecast: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for item in forecast:
        date_text = item.get("dt_txt", "")
        day = date_text[:10] if date_text else "Без даты"
        days.setdefault(day, []).append(item)
    return days


def format_forecast_day(day: str, items: list[dict], title: str = "") -> str:
    heading = forecast_day_label(day)
    lines = [f"📅 <b>{escape(heading)}</b>"]
    if title:
        lines.append(escape(title))
    lines.append("")
    for item in items:
        time_text = item.get("dt_txt", "")[11:16]
        main = item.get("main", {})
        weather_items = item.get("weather", [{}])
        description = escape(str(weather_items[0].get("description", "нет описания")).capitalize())
        try:
            rain_probability = max(0, min(100, round(float(item.get("pop", 0)) * 100)))
        except (TypeError, ValueError):
            rain_probability = 0
        if rain_probability < 20:
            rain_text = "без дождя"
        elif rain_probability < 50:
            rain_text = f"дождь маловероятен · {rain_probability}%"
        elif rain_probability < 70:
            rain_text = f"возможен дождь · {rain_probability}%"
        else:
            rain_text = f"ожидается дождь · {rain_probability}%"
        lines.append(
            f"<b>{time_text}</b>  {weather_emoji(item)}  "
            f"<b>{format_number(main.get('temp'))}°C</b>  ·  {description}\n"
            f"　🌧️ {rain_text}"
        )
    return "\n".join(lines)


def ask_for_city(message: types.Message, state: str, prompt: str) -> None:
    set_state(message.from_user.id, state)
    bot.send_message(message.chat.id, prompt, reply_markup=back_menu())


def send_current_weather(chat_id: int, lat: float, lon: float, title: str) -> None:
    progress = bot.send_message(chat_id, "🌤 <b>Обновляю погоду…</b>")
    try:
        weather = get_current_weather(lat, lon)
        bot.edit_message_text(
            format_current_weather(weather, title),
            chat_id,
            progress.message_id,
            reply_markup=None,
        )
    except Exception:
        logger.exception("Current weather handler failed for chat_id=%s", chat_id)
        try:
            bot.edit_message_text(
                "🌤 <b>Не удалось обновить погоду</b>\n"
                "Сервис не ответил вовремя. Попробуйте ещё раз.",
                chat_id,
                progress.message_id,
                reply_markup=None,
            )
        except Exception:
            logger.exception("Could not update failed weather message for chat_id=%s", chat_id)


@bot.message_handler(commands=["start"])
def start(message: types.Message) -> None:
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "🌤 <b>Погода рядом и по всему миру</b>\n\n"
        "Точный прогноз, сравнение городов и качество воздуха.\n"
        "Выберите действие ниже.",
        reply_markup=main_menu(),
    )


@bot.message_handler(content_types=["location"])
def handle_location(message: types.Message) -> None:
    remember_chat(message.from_user.id, message.chat.id)
    if not message.location:
        bot.send_message(message.chat.id, "Отправьте location через кнопку геолокации.")
        return

    user_id = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    location = get_location_by_coordinates(lat, lon)
    city = location["name"] if location else "Моя геолокация"
    country = location["country"] if location else ""
    state = location["state"] if location else ""
    data = load_user(user_id)
    data.update(
        {
            "city": city,
            "country": country,
            "state": state,
            "lat": lat,
            "lon": lon,
        }
    )
    data.setdefault("notifications", {"enabled": False, "interval_h": 2})
    save_user(user_id, data)
    clear_state(user_id)
    title = location_title(location) if location else city
    bot.send_message(
        message.chat.id,
        f"📍 <b>Местоположение сохранено</b>\n{escape(title)}",
        reply_markup=main_menu(),
    )


@bot.message_handler(func=lambda message: True)
def handle_text(message: types.Message) -> None:
    user_id = message.from_user.id
    text = (message.text or "").strip()
    action = action_name(text)
    remember_chat(user_id, message.chat.id)

    maybe_send_due_notification(user_id, message.chat.id)

    if action == "Назад":
        clear_state(user_id)
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        return

    state = user_states.get(user_id, {})
    if state:
        handle_state_message(message, state, text)
        return

    if action in ("Сейчас", "Текущая погода"):
        saved = resolve_user_location(user_id)
        if saved:
            lat, lon, title = saved
            send_current_weather(message.chat.id, lat, lon, title)
        else:
            ask_for_city(
                message,
                "current_city",
                "🔎 <b>Найдём любой город мира</b>\n"
                "Введите город и страну, например: <i>Paris, FR</i>",
            )
    elif action == "Найти город":
        ask_for_city(
            message,
            "current_city",
            "🔎 <b>Погода в любом городе мира</b>\n"
            "Введите город и страну.\n"
            "Например: <i>Токио, JP</i> или <i>New York, US</i>",
        )
    elif action in ("Прогноз", "Прогноз на 5 дней"):
        saved = resolve_user_location(user_id)
        if saved:
            send_forecast_picker(message.chat.id, user_id, saved[0], saved[1], saved[2])
        else:
            ask_for_city(
                message,
                "forecast_city",
                "📅 <b>Прогноз на 5 дней</b>\nВведите город и страну, например: <i>Rome, IT</i>",
            )
    elif action in ("Моя геолокация", "Отправить местоположение", "Геолокация"):
        set_state(user_id, "location")
        bot.send_message(
            message.chat.id,
            "📍 <b>Ваше местоположение</b>\nНажмите кнопку ниже, чтобы отправить геолокацию.",
            reply_markup=location_keyboard(),
        )
    elif action in ("Сравнить", "Сравнить города"):
        ask_for_city(
            message,
            "compare_cities",
            "⚖️ <b>Сравнение городов</b>\n"
            "Введите два города через точку с запятой.\n\n"
            "Например: <i>Paris, FR; Tokyo, JP</i>",
        )
    elif action in ("Воздух", "Расширенные данные"):
        saved = resolve_user_location(user_id)
        if saved:
            send_extended_weather(message.chat.id, saved[0], saved[1], saved[2])
        else:
            ask_for_city(
                message,
                "extended_city",
                "🌿 <b>Погода и качество воздуха</b>\nВведите город и страну, например: <i>Berlin, DE</i>",
            )
    elif action == "Уведомления":
        show_notifications(message.chat.id, user_id)
    else:
        bot.send_message(message.chat.id, "Выберите действие в меню или используйте /start.", reply_markup=main_menu())


def handle_state_message(message: types.Message, state: dict, text: str) -> None:
    user_id = message.from_user.id
    name = state.get("name")

    if name == "location":
        bot.send_message(
            message.chat.id,
            "📍 Нажмите кнопку отправки местоположения ниже.",
            reply_markup=location_keyboard(),
        )
    elif name == "current_city":
        show_location_choices(message.chat.id, user_id, text, "current")
    elif name == "forecast_city":
        show_location_choices(message.chat.id, user_id, text, "forecast")
    elif name == "extended_city":
        show_location_choices(message.chat.id, user_id, text, "extended")
    elif name == "compare_cities":
        separator = ";" if ";" in text else None
        cities = [city.strip() for city in text.split(separator, 1)] if separator else []
        if len(cities) != 2 or not cities[0] or not cities[1]:
            bot.send_message(
                message.chat.id,
                "⚖️ <b>Разделите города точкой с запятой.</b>\n"
                "Например: <i>London, GB; Dubai, AE</i>",
                reply_markup=back_menu(),
            )
            return

        bot.send_message(message.chat.id, "⏳ Получаю данные о погоде в двух городах...")
        compare_cities(message.chat.id, cities[0], cities[1])
        clear_state(user_id)


def location_choice_keyboard(locations: list[dict]) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for index, location in enumerate(locations):
        keyboard.add(
            types.InlineKeyboardButton(
                location_title(location, include_state=True),
                callback_data=f"place:{index}",
            )
        )
    keyboard.add(types.InlineKeyboardButton("✕ Отмена", callback_data="place:cancel"))
    return keyboard


def show_location_choices(
    chat_id: int,
    user_id: int,
    query: str,
    purpose: str,
) -> None:
    locations = search_locations(query, limit=5)
    if locations and has_error(locations[0]):
        bot.send_message(chat_id, error_message(locations[0]), reply_markup=main_menu())
        clear_state(user_id)
        return
    if not locations:
        bot.send_message(
            chat_id,
            "🔎 <b>Город не найден</b>\nПроверьте написание или добавьте код страны, например: <i>Paris, FR</i>.",
            reply_markup=back_menu(),
        )
        return

    set_state(user_id, "location_choice", purpose=purpose, locations=locations)
    bot.send_message(
        chat_id,
        "🌍 <b>Уточните место</b>\nВыберите нужный город:",
        reply_markup=location_choice_keyboard(locations),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("place:"))
def location_choice_callback(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    state = user_states.get(user_id, {})
    if call.data == "place:cancel":
        clear_state(user_id)
        bot.answer_callback_query(call.id, "Отменено")
        bot.send_message(call.message.chat.id, "Главное меню", reply_markup=main_menu())
        return
    if state.get("name") != "location_choice":
        bot.answer_callback_query(call.id, "Поиск устарел. Выполните его снова.")
        return

    try:
        index = int(call.data.split(":", 1)[1])
        location = state["locations"][index]
    except (ValueError, IndexError, KeyError):
        bot.answer_callback_query(call.id, "Место не найдено.")
        return

    bot.answer_callback_query(call.id, "Место выбрано")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass

    title = location_title(location)
    save_city(
        user_id,
        location["name"],
        location["lat"],
        location["lon"],
        location["country"],
        location["state"],
    )
    purpose = state.get("purpose")
    clear_state(user_id)
    if purpose == "forecast":
        send_forecast_picker(call.message.chat.id, user_id, location["lat"], location["lon"], title)
    elif purpose == "extended":
        send_extended_weather(call.message.chat.id, location["lat"], location["lon"], title)
    else:
        send_current_weather(call.message.chat.id, location["lat"], location["lon"], title)


def send_extended_weather(chat_id: int, lat: float, lon: float, title: str) -> None:
    progress = bot.send_message(
        chat_id,
        "🌿 <b>Проверяю качество воздуха…</b>\nОбычно это занимает несколько секунд.",
    )
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weather-air") as executor:
            weather_future = executor.submit(
                get_current_weather,
                lat,
                lon,
                max_retries=0,
                timeout_s=5,
            )
            air_future = executor.submit(get_air_pollution, lat, lon)
            weather = weather_future.result()
            components = air_future.result()

        if has_error(weather):
            bot.edit_message_text(
                error_message(weather),
                chat_id,
                progress.message_id,
                reply_markup=None,
            )
            return

        analysis = analyze_air_pollution(components, extended=True)
        bot.edit_message_text(
            f"{format_current_weather(weather, title)}\n\n{format_air_analysis(analysis)}",
            chat_id,
            progress.message_id,
            reply_markup=None,
        )
    except Exception:
        logger.exception("Air quality handler failed for chat_id=%s", chat_id)
        try:
            bot.edit_message_text(
                "🌿 <b>Не удалось проверить качество воздуха</b>\n"
                "Сервис временно не ответил. Попробуйте ещё раз через минуту.",
                chat_id,
                progress.message_id,
                reply_markup=None,
            )
        except Exception:
            logger.exception("Could not update failed air-quality message for chat_id=%s", chat_id)


def send_forecast_picker(
    chat_id: int,
    user_id: int,
    lat: float,
    lon: float,
    title: str = "",
) -> None:
    progress = bot.send_message(chat_id, "📅 <b>Готовлю прогноз…</b>")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weather-forecast") as executor:
        forecast_future = executor.submit(get_forecast_5d3h, lat, lon)
        current_future = executor.submit(get_current_weather, lat, lon)
        forecast = forecast_future.result()
        current_weather = current_future.result()

    if forecast and has_error(forecast[0]):
        bot.edit_message_text(
            error_message(forecast[0]),
            chat_id,
            progress.message_id,
            reply_markup=None,
        )
        return

    days = grouped_forecast(forecast)
    day_names = list(days.keys())[:5]
    set_state(
        user_id,
        "forecast_picker",
        forecast_days=days,
        day_names=day_names,
        title=title,
    )
    if not has_error(current_weather):
        try:
            card = render_weather_card(forecast, current_weather, lat, lon, title)
            bot.send_photo(
                chat_id,
                card,
                caption=(
                    f"📅 <b>Прогноз для {escape(title)}</b>\n"
                    "Подробности доступны по кнопкам ниже."
                ),
                reply_markup=forecast_keyboard(day_names),
            )
            try:
                bot.delete_message(chat_id, progress.message_id)
            except Exception:
                logger.warning("Could not delete forecast progress message for chat_id=%s", chat_id)
            return
        except Exception:
            # The text version keeps the forecast available if Pillow/font
            # rendering is unavailable on a particular server.
            logger.exception("Weather card rendering failed for chat_id=%s", chat_id)

    bot.send_message(
        chat_id,
        f"📅 <b>Прогноз на 5 дней</b>\n{escape(title)}\n\nВыберите день:",
        reply_markup=forecast_keyboard(day_names),
    )
    try:
        bot.delete_message(chat_id, progress.message_id)
    except Exception:
        logger.warning("Could not delete forecast progress message for chat_id=%s", chat_id)


def compare_cities(chat_id: int, city_a: str, city_b: str) -> None:
    rows = []
    for query in (city_a, city_b):
        locations = search_locations(query, limit=1)
        if locations and has_error(locations[0]):
            bot.send_message(chat_id, error_message(locations[0]), reply_markup=main_menu())
            return
        if not locations:
            bot.send_message(
                chat_id,
                f"🔎 Город не найден: <b>{escape(query)}</b>",
                reply_markup=main_menu(),
            )
            return
        location = locations[0]
        weather = get_current_weather(location["lat"], location["lon"])
        if has_error(weather):
            bot.send_message(chat_id, error_message(weather), reply_markup=main_menu())
            return
        main = weather.get("main", {})
        wind = weather.get("wind", {})
        weather_items = weather.get("weather", [{}])
        rows.append(
            {
                "city": location_title(location),
                "temp": main.get("temp"),
                "rain": "идёт сейчас" if is_raining(weather) else "сейчас не ожидается",
                "wind": wind.get("speed"),
                "pressure": main.get("pressure"),
                "description": escape(
                    str(weather_items[0].get("description", "нет описания")).capitalize()
                ),
            }
        )

    first, second = rows
    diff_text = format_temperature_diff(first["city"], first["temp"], second["city"], second["temp"])
    first_city = escape(first["city"])
    second_city = escape(second["city"])
    text = (
        "⚖️ <b>Сравнение погоды</b>\n\n"
        f"<b>{first_city}</b>\nvs\n<b>{second_city}</b>\n\n"
        "🌡️ <b>Температура:</b>\n"
        f"{first_city}: <b>{format_number(first['temp'])}°C</b>\n"
        f"{second_city}: <b>{format_number(second['temp'])}°C</b>\n"
        f"{diff_text}\n\n"
        "🌧️ <b>Дождь:</b>\n"
        f"{first_city}: <b>{first['rain']}</b>\n"
        f"{second_city}: <b>{second['rain']}</b>\n\n"
        "💨 <b>Ветер:</b>\n"
        f"{first_city}: <b>{format_number(first['wind'])} м/с</b>\n"
        f"{second_city}: <b>{format_number(second['wind'])} м/с</b>\n\n"
        "📊 <b>Давление:</b>\n"
        f"{first_city}: <b>{format_number(first['pressure'], 0)} гПа</b>\n"
        f"{second_city}: <b>{format_number(second['pressure'], 0)} гПа</b>\n\n"
        "☁️ <b>Условия:</b>\n"
        f"{first_city}: <b>{first['description']}</b>\n"
        f"{second_city}: <b>{second['description']}</b>"
    )
    bot.send_message(chat_id, text, reply_markup=main_menu())


def format_temperature_diff(city_a: str, temp_a: object, city_b: str, temp_b: object) -> str:
    try:
        first_temp = float(temp_a)
        second_temp = float(temp_b)
    except (TypeError, ValueError):
        return ""

    diff = round(abs(first_temp - second_temp), 1)
    if diff == 0:
        return "🌡️ Температура одинаковая"
    warmer_city = city_a if first_temp > second_temp else city_b
    return f"🔥 В <b>{escape(warmer_city)}</b> теплее на <b>{format_number(diff)}°C</b>"


def show_notifications(chat_id: int, user_id: int) -> None:
    data = load_user(user_id)
    notifications = data.setdefault("notifications", {"enabled": False, "interval_h": 2})
    save_user(user_id, data)
    enabled = bool(notifications.get("enabled"))
    status = "Включены" if enabled else "Выключены"
    status_icon = "🟢" if enabled else "⚪"
    interval = notifications.get("interval_h", 2)
    bot.send_message(
        chat_id,
        "🔔 <b>Уведомления о погоде</b>\n\n"
        f"{status_icon} Статус  <b>{status}</b>\n"
        f"⏱ Интервал  <b>{interval} ч</b>\n\n"
        "Прогноз придёт для сохранённого города.",
        reply_markup=notification_keyboard(enabled),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("forecast:"))
def forecast_callback(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    state = user_states.get(user_id, {})

    if call.data == "forecast:back":
        clear_state(user_id)
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Главное меню", reply_markup=main_menu())
        return

    if state.get("name") != "forecast_picker":
        bot.answer_callback_query(call.id, "Прогноз устарел. Откройте его заново.")
        return

    index = int(call.data.split(":")[-1])
    day_names = state.get("day_names", [])
    days = state.get("forecast_days", {})
    if index >= len(day_names):
        bot.answer_callback_query(call.id, "День не найден.")
        return

    day = day_names[index]
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        format_forecast_day(day, days[day], state.get("title", "")),
        reply_markup=forecast_keyboard(day_names),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("notify:"))
def notification_callback(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    data = load_user(user_id)
    notifications = data.setdefault("notifications", {"enabled": False, "interval_h": 2})
    parts = call.data.split(":")

    if parts[1] == "toggle":
        notifications["enabled"] = not notifications.get("enabled", False)
    elif parts[1] == "interval":
        notifications["interval_h"] = int(parts[2])

    save_user(user_id, data)
    bot.answer_callback_query(call.id, "Готово")
    show_notifications(call.message.chat.id, user_id)


def maybe_send_due_notification(user_id: int, chat_id: int) -> None:
    data = load_user(user_id)
    notifications = data.get("notifications", {})
    if not notifications.get("enabled"):
        return

    location = resolve_user_location(user_id)
    if not location:
        return

    interval_s = int(notifications.get("interval_h", 2)) * 3600
    now = time.time()
    if now - notified_at[user_id] < interval_s:
        return

    notified_at[user_id] = now
    lat, lon, title = location
    bot.send_message(chat_id, f"Плановое обновление {datetime.now():%H:%M}\n\n{format_current_weather(get_current_weather(lat, lon), title)}")


def send_due_notification_for_user(user_id: int, data: dict) -> None:
    notifications = data.get("notifications", {})
    chat_id = data.get("chat_id")
    lat = data.get("lat")
    lon = data.get("lon")
    if not notifications.get("enabled") or not chat_id or lat is None or lon is None:
        return

    interval_s = int(notifications.get("interval_h", 2)) * 3600
    now = time.time()
    if now - notified_at[user_id] < interval_s:
        return

    notified_at[user_id] = now
    title = data.get("city", "вашей геолокации")
    text = f"Плановое обновление {datetime.now():%H:%M}\n\n{format_current_weather(get_current_weather(float(lat), float(lon)), title)}"
    try:
        bot.send_message(int(chat_id), text)
    except Exception:
        logger.exception("Scheduled notification failed for user_id=%s", user_id)


def notification_loop() -> None:
    while True:
        for raw_user_id, data in load_all_users().items():
            try:
                send_due_notification_for_user(int(raw_user_id), data)
            except (TypeError, ValueError):
                continue
        time.sleep(60)


def main() -> None:
    threading.Thread(target=notification_loop, daemon=True).start()
    logger.info("Weather bot started")
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)


if __name__ == "__main__":
    main()
