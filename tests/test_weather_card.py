from datetime import datetime, timedelta, timezone

from PIL import Image

from weather_card import HEIGHT, WIDTH, render_weather_card


def test_render_weather_card_returns_valid_png() -> None:
    tz = timezone(timedelta(hours=3))
    base = datetime(2026, 10, 17, tzinfo=tz)
    forecast = []
    for day in range(2):
        for hour in (0, 6, 12, 18):
            moment = base + timedelta(days=day, hours=hour)
            forecast.append(
                {
                    "dt": int(moment.timestamp()),
                    "main": {"temp": 12, "feels_like": 10, "humidity": 70, "pressure": 1005},
                    "weather": [{"id": 801}],
                    "wind": {"speed": 4, "deg": 120},
                    "clouds": {"all": 45},
                    "visibility": 9000,
                    "pop": 0.2,
                }
            )

    current = {
        "timezone": 10800,
        "main": {"temp": 13, "feels_like": 11, "pressure": 1011},
        "weather": [{"id": 801}],
        "wind": {"speed": 4, "deg": 120},
    }
    result = render_weather_card(forecast, current, 55.7558, 37.6173, "Москва · RU")
    image = Image.open(result)
    assert image.format == "PNG"
    assert image.size == (WIDTH, HEIGHT)

