"""Generate a local preview without Telegram or API keys."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from weather_card import render_weather_card


def _sample_item(moment: datetime, temp: float, condition: int, humidity: int, wind: float, degree: int, pop: float) -> dict:
    return {
        "dt": int(moment.timestamp()),
        "main": {
            "temp": temp,
            "feels_like": temp - 2,
            "humidity": humidity,
            "pressure": 1004 + humidity // 12,
        },
        "weather": [{"id": condition, "description": "переменная облачность"}],
        "wind": {"speed": wind, "deg": degree},
        "clouds": {"all": min(100, humidity)},
        "visibility": 10000 - humidity * 20,
        "pop": pop,
    }


def main() -> None:
    tz = timezone(timedelta(hours=3))
    base = datetime.now(tz=tz).replace(hour=0, minute=0, second=0, microsecond=0)
    forecast = []
    conditions = (500, 500, 801, 801, 802, 800, 800, 801)
    for day in range(2):
        for index, hour in enumerate(range(0, 24, 3)):
            forecast.append(
                _sample_item(
                    base + timedelta(days=day, hours=hour),
                    temp=8 + day + min(index, 5) * 1.4,
                    condition=conditions[index],
                    humidity=78 - min(index, 6) * 6,
                    wind=3 + index * 0.4,
                    degree=45 + index * 35,
                    pop=max(0.05, 0.72 - index * 0.09),
                )
            )

    current = {
        "timezone": 10800,
        "main": {"temp": 13, "feels_like": 11, "pressure": 1011},
        "weather": [{"id": 801, "description": "небольшая облачность"}],
        "wind": {"speed": 4.2, "deg": 225},
    }
    image = render_weather_card(forecast, current, 55.7558, 37.6173, "Москва · RU")
    output = Path(__file__).with_name("weather_card_preview.png")
    output.write_bytes(image.getvalue())
    print(output)


if __name__ == "__main__":
    main()

