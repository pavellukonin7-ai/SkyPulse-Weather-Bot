import json
from pathlib import Path
from threading import Lock


DATA_FILE = Path(__file__).with_name("User_Data.json")
_LOCK = Lock()


def _read_all() -> dict:
    if not DATA_FILE.exists():
        DATA_FILE.write_text("{}", encoding="utf-8")

    try:
        text = DATA_FILE.read_text(encoding="utf-8").strip()
        return json.loads(text or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(data: dict) -> None:
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_all_users() -> dict:
    with _LOCK:
        return _read_all()


def load_user(user_id: int) -> dict:
    with _LOCK:
        users = _read_all()
        return users.get(str(user_id), {}).copy()


def save_user(user_id: int, data: dict) -> None:
    with _LOCK:
        users = _read_all()
        users[str(user_id)] = data
        _write_all(users)
