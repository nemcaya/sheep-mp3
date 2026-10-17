"""App configuration, settings persistence, and resource path resolution."""
from __future__ import annotations

import json
from pathlib import Path

APP_NAME = "SheepMP3"
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"

COOKIE_BROWSERS = ("chrome", "safari", "firefox", "brave", "edge", "chromium")

DEFAULTS = {
    "output_dir": str(Path.home() / "Downloads" / "Sheep MP3"),
    "format": "mp3",           # "mp3" | "wav"
    "use_cookies": False,      # hand yt-dlp browser cookies if YouTube blocks a download
    "cookies_browser": "chrome",
}


def app_support() -> Path:
    p = Path.home() / "Library" / "Application Support" / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def bin_dir() -> Path:
    """Where `sheep-mp3 setup` drops ffmpeg / ffprobe / deno."""
    p = app_support() / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return app_support() / "server.log"


def pid_file() -> Path:
    return app_support() / "server.pid"


def _settings_file() -> Path:
    return app_support() / "settings.json"


def load_settings() -> dict:
    data = dict(DEFAULTS)
    f = _settings_file()
    if f.exists():
        try:
            data.update(json.loads(f.read_text()))
        except Exception:
            pass
    if data.get("cookies_browser") not in COOKIE_BROWSERS:
        data["cookies_browser"] = DEFAULTS["cookies_browser"]
    return data


def save_settings(patch: dict) -> dict:
    data = load_settings()
    for key in DEFAULTS:
        if key in patch and patch[key] is not None:
            data[key] = patch[key]
    if data.get("cookies_browser") not in COOKIE_BROWSERS:
        data["cookies_browser"] = DEFAULTS["cookies_browser"]
    _settings_file().write_text(json.dumps(data, indent=2))
    return data


def output_dir() -> Path:
    p = Path(load_settings()["output_dir"]).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def frontend_dir() -> Path:
    """The web UI ships inside the package."""
    return Path(__file__).resolve().parent / "frontend"
