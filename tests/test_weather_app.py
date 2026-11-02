import requests

import weather_app


def test_air_pollution_fails_fast(monkeypatch) -> None:
    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise requests.Timeout("test timeout")

    monkeypatch.setenv("OW_API_KEY", "test-key")
    monkeypatch.setattr(weather_app.requests, "get", timeout)

    result = weather_app.get_air_pollution(55.75, 37.61)

    assert result.get("error")
    assert calls == [5]


def test_current_weather_has_short_timeout(monkeypatch) -> None:
    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise requests.Timeout("test timeout")

    monkeypatch.setenv("OW_API_KEY", "test-key")
    monkeypatch.setattr(weather_app.requests, "get", timeout)

    result = weather_app.get_current_weather(55.75, 37.61)

    assert result.get("error")
    assert calls == [7]
