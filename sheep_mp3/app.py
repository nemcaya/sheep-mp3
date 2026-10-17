"""FastAPI app: job submission, settings, progress WebSocket, static frontend."""
from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, bins, config, events
from .downloader import manager

# Shut the background server down after this long with no dashboard open and
# nothing downloading, so a forgotten server doesn't linger for days.
IDLE_HOURS = float(os.environ.get("SHEEPMP3_IDLE_HOURS", "6"))


async def _idle_watchdog() -> None:
    while True:
        await asyncio.sleep(60)
        if IDLE_HOURS <= 0:
            continue
        idle_for = time.time() - manager.last_activity
        if (events.bus.subscriber_count() == 0 and manager.active_count() == 0
                and idle_for > IDLE_HOURS * 3600):
            os._exit(0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    events.bus.bind_loop(asyncio.get_running_loop())
    task = asyncio.create_task(_idle_watchdog())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Sheep MP3", version=__version__, lifespan=lifespan)


class JobRequest(BaseModel):
    urls: list[str]
    format: str | None = None


class SettingsPatch(BaseModel):
    output_dir: str | None = None
    format: str | None = None
    use_cookies: bool | None = None
    cookies_browser: str | None = None


@app.post("/api/jobs")
async def create_jobs(req: JobRequest):
    fmt = (req.format or config.load_settings()["format"]).lower()
    if fmt not in ("mp3", "wav"):
        fmt = "mp3"
    jobs = manager.submit(req.urls, fmt)
    return {"jobs": [{"id": j["id"], "title": j["title"],
                      "source": j["source"], "status": j["status"]} for j in jobs]}


@app.get("/api/jobs")
async def list_jobs():
    return {"jobs": manager.snapshot()}


@app.get("/api/settings")
async def get_settings():
    data = config.load_settings()
    data["cookie_browsers"] = list(config.COOKIE_BROWSERS)
    return data


@app.post("/api/settings")
async def set_settings(patch: SettingsPatch):
    data = {k: v for k, v in patch.model_dump().items() if v is not None}
    return config.save_settings(data)


@app.get("/api/version")
async def version():
    import yt_dlp
    try:
        from spotdl import __version__ as spotdl_version
    except Exception:
        spotdl_version = None
    return {
        "app": __version__,
        "yt_dlp": yt_dlp.version.__version__,
        "spotdl": spotdl_version,
        "ffmpeg": bins.have_ffmpeg(),
        "deno": bins.have_deno(),
    }


@app.post("/api/reveal")
async def reveal():
    out = str(config.output_dir())
    try:
        subprocess.Popen(["open", out])
    except Exception:
        pass
    return {"ok": True, "path": out}


@app.post("/api/quit")
async def quit_app():
    """Stop the background server (dashboard Quit button / `sheep-mp3 stop`)."""
    def _die():
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q = events.bus.subscribe()
    manager.last_activity = time.time()
    try:
        await websocket.send_json({"type": "snapshot", "jobs": manager.snapshot()})
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        events.bus.unsubscribe(q)
        manager.last_activity = time.time()


# Mounted last so /api and /ws take precedence over the catch-all static handler.
app.mount("/", StaticFiles(directory=str(config.frontend_dir()), html=True), name="static")
