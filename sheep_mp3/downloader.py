"""Link routing + download orchestration.

- YouTube / SoundCloud  -> yt-dlp Python API (fine-grained progress)
- Spotify               -> spotDL Python API (metadata -> YouTube match -> tag)

One job row per pasted URL. Playlists/albums/sets show aggregate "x/N: title"
progress on that single row.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp
from yt_dlp.postprocessor import MetadataParserPP

from . import bins, config
from .events import bus

# ---- link detection --------------------------------------------------------

SPOTIFY_RE = re.compile(r"(open\.spotify\.com|spotify:)", re.I)
SOUNDCLOUD_RE = re.compile(r"(soundcloud\.com|on\.soundcloud\.com|snd\.sc)", re.I)
YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be|music\.youtube\.com)", re.I)

ACTIVE = {"queued", "resolving", "downloading", "converting"}


def detect_source(url: str) -> str | None:
    if SPOTIFY_RE.search(url):
        return "spotify"
    if SOUNDCLOUD_RE.search(url):
        return "soundcloud"
    if YOUTUBE_RE.search(url):
        return "youtube"
    return None


def _js_runtimes() -> dict | None:
    """Point yt-dlp at our deno so YouTube's player challenges can be solved."""
    deno = bins.deno_exe()
    return {"deno": {"path": deno}} if deno else None


# ---- Spotify engine (lazy singleton; spotDL holds a Spotify client singleton) ----

class _SpotifyEngine:
    _instance: "_SpotifyEngine | None" = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        from spotdl import Spotdl
        from spotdl.utils.config import DEFAULT_CONFIG

        self.dl_lock = threading.Lock()  # serialize Spotify downloads (shared rate limit + settings)
        self._spotdl = Spotdl(
            client_id=DEFAULT_CONFIG["client_id"],
            client_secret=DEFAULT_CONFIG["client_secret"],
            headless=True,
            downloader_settings={
                "output": str(config.output_dir() / "{artists} - {title}.{output-ext}"),
                "format": "mp3",
                "bitrate": "320k",
                "ffmpeg": bins.ffmpeg_exe() or "ffmpeg",
                "threads": 4,
                "simple_tui": True,
                "log_level": "CRITICAL",
                "print_errors": False,
                "yt_dlp_args": self._yt_dlp_args(),
            },
        )

    @staticmethod
    def _yt_dlp_args() -> str | None:
        """Extra CLI-style args for spotDL's internal yt-dlp (Spotify -> YouTube)."""
        deno = bins.deno_exe()
        return f'--js-runtimes "deno:{deno}"' if deno else None

    @classmethod
    def get(cls) -> "_SpotifyEngine":
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def configure(self, fmt: str) -> None:
        s = self._spotdl.downloader.settings
        s["output"] = str(config.output_dir() / "{artists} - {title}.{output-ext}")
        s["format"] = "wav" if fmt == "wav" else "mp3"
        s["bitrate"] = "disable" if fmt == "wav" else "320k"
        s["ffmpeg"] = bins.ffmpeg_exe() or "ffmpeg"
        s["yt_dlp_args"] = self._yt_dlp_args()

    def search(self, url: str):
        return self._spotdl.search([url])

    def download(self, song):
        return self._spotdl.download(song)


# ---- download manager ------------------------------------------------------

class DownloadManager:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl")
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.last_activity = time.time()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(j) for j in self.jobs.values()]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self.jobs.values() if j["status"] in ACTIVE)

    def _emit(self, job: dict, **changes) -> None:
        with self._lock:
            job.update(changes)
            payload = dict(job)
        self.last_activity = time.time()
        payload["type"] = "job"
        bus.publish(payload)

    def submit(self, urls: list[str], fmt: str) -> list[dict]:
        created: list[dict] = []
        for raw in urls:
            url = (raw or "").strip()
            if not url:
                continue
            source = detect_source(url)
            job = {
                "id": uuid.uuid4().hex[:12],
                "url": url,
                "source": source or "unknown",
                "title": url,
                "status": "queued",
                "percent": 0,
                "detail": "Waiting in the pen…",
                "file": None,
                "error": None,
            }
            with self._lock:
                self.jobs[job["id"]] = job
            created.append(job)

            if source is None:
                self._emit(job, status="error",
                           error="Not a YouTube, Spotify, or SoundCloud link",
                           detail="Unrecognized link")
                continue
            self._emit(job)  # broadcast the queued row
            self.executor.submit(self._run, job["id"], fmt)
        return created

    def _run(self, job_id: str, fmt: str) -> None:
        job = self.jobs[job_id]
        try:
            if not bins.have_ffmpeg():
                raise RuntimeError("ffmpeg is missing — run `sheep-mp3 setup` in Terminal")
            if job["source"] == "spotify":
                self._download_spotify(job, fmt)
            else:
                self._download_ytdlp(job, fmt)
        except Exception as exc:  # noqa: BLE001 - surface any failure on the row
            self._emit(job, status="error", error=_friendly_error(exc), detail="Failed")

    # ---- yt-dlp (YouTube + SoundCloud) ----

    def _download_ytdlp(self, job: dict, fmt: str) -> None:
        out_dir = config.output_dir()
        self._emit(job, status="resolving", detail="Reading link…")

        flat_opts = {
            "quiet": True, "no_warnings": True,
            "extract_flat": "in_playlist", "skip_download": True,
        }
        flat_opts.update(self._common_opts())
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(job["url"], download=False)
        if not info:
            raise RuntimeError("Couldn't read that link")

        entries = [e for e in (info.get("entries") or []) if e]
        is_playlist = bool(entries)
        total = max(1, len(entries)) if is_playlist else 1
        title = info.get("title") or job["url"]
        self._emit(job, title=title, detail=f"{total} track(s)")

        state = {"done": 0, "failed": 0}

        def hook(d: dict) -> None:
            status = d.get("status")
            if status == "downloading":
                tb = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                db = d.get("downloaded_bytes") or 0
                frac = (db / tb) if tb else 0.0
                cur_title = (d.get("info_dict") or {}).get("title") or ""
                pct = int((state["done"] + frac) / total * 100)
                detail = (f"{state['done'] + 1}/{total}: {cur_title}"
                          if total > 1 else cur_title)
                self._emit(job, status="downloading", percent=min(99, pct), detail=detail)
            elif status == "finished":
                state["done"] += 1
                pct = int(state["done"] / total * 100)
                self._emit(job, status="converting", percent=min(99, pct), detail="Converting…")
            elif status == "error":
                state["failed"] += 1

        opts = self._ytdlp_opts(fmt, out_dir, hook)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([job["url"]])

        if state["done"] == 0:
            raise RuntimeError("Nothing could be downloaded from that link")
        saved = state["done"]
        note = f"{saved} file(s) saved" + (f", {total - saved} skipped" if saved < total else "")
        self._emit(job, status="done", percent=100, detail=note, file=str(out_dir))

    def _common_opts(self) -> dict:
        """Options shared by the flat probe and the real download."""
        opts: dict = {}
        js = _js_runtimes()
        if js:
            opts["js_runtimes"] = js
        settings = config.load_settings()
        if settings.get("use_cookies"):
            opts["cookiesfrombrowser"] = (settings.get("cookies_browser", "chrome"),)
        return opts

    def _ytdlp_opts(self, fmt: str, out_dir: Path, hook) -> dict:
        is_wav = fmt == "wav"
        pps = [
            # Clean up tags for DJ libraries: split "Artist - Title" video titles,
            # guarantee an artist tag (fall back to the channel), and drop YouTube's
            # auto-generated " - Topic" suffix from channel names.
            {"key": "MetadataParser", "when": "pre_process", "actions": [
                (MetadataParserPP.Actions.INTERPRET, "%(title)s",
                 r"(?P<meta_artist>.+?) - (?P<meta_title>.+)"),
                (MetadataParserPP.Actions.INTERPRET, "%(meta_artist,artist,uploader)s",
                 r"(?P<meta_artist>.+)"),
                (MetadataParserPP.Actions.REPLACE, "meta_artist", r"\s*-\s*Topic$", ""),
            ]},
            {"key": "FFmpegExtractAudio",
             "preferredcodec": "wav" if is_wav else "mp3",
             "preferredquality": "0" if is_wav else "320"},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ]
        if not is_wav:
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

        opts = {
            "format": "bestaudio/best",
            # File name mirrors the cleaned tags: "Artist - Title.mp3".
            "outtmpl": str(out_dir / "%(meta_artist,uploader)s - %(meta_title,title)s.%(ext)s"),
            "postprocessors": pps,
            "writethumbnail": not is_wav,
            "allow_playlist_files": False,   # no stray playlist cover .jpg next to the tracks
            "quiet": True, "no_warnings": True, "noprogress": True,
            "ignoreerrors": True,
            "progress_hooks": [hook],
            "concurrent_fragment_downloads": 4,
            "retries": 5,
            "fragment_retries": 5,
            "windowsfilenames": False,
        }
        opts.update(self._common_opts())
        ff = bins.ffmpeg_dir()
        if ff:
            opts["ffmpeg_location"] = ff
        return opts

    # ---- spotDL (Spotify) ----

    def _download_spotify(self, job: dict, fmt: str) -> None:
        self._emit(job, status="resolving", detail="Asking Spotify…")
        engine = _SpotifyEngine.get()
        with engine.dl_lock:
            engine.configure(fmt)
            songs = engine.search(job["url"])
            if not songs:
                raise RuntimeError("Spotify returned nothing for that link")
            total = len(songs)
            if total > 1:
                self._emit(job, title=f"Spotify ({total} tracks)", detail=f"{total} track(s)")
            else:
                s = songs[0]
                self._emit(job, title=f"{s.artist} - {s.name}")

            done = 0
            saved = 0
            for song in songs:
                label = f"{song.artist} - {song.name}"
                self._emit(job, status="downloading",
                           percent=int(done / total * 100),
                           detail=(f"{done + 1}/{total}: {label}" if total > 1 else label))
                try:
                    _, path = engine.download(song)
                    if path:
                        saved += 1
                except Exception:
                    pass  # one bad match shouldn't kill the whole playlist
                done += 1
                self._emit(job, percent=min(99, int(done / total * 100)))

        if saved == 0:
            raise RuntimeError("Couldn't find a YouTube match for that Spotify track")
        note = f"{saved} track(s) saved" + (f", {total - saved} skipped" if saved < total else "")
        self._emit(job, status="done", percent=100, detail=note, file=str(config.output_dir()))


def _friendly_error(exc: Exception) -> str:
    """Turn yt-dlp's noisy exceptions into one readable line."""
    msg = str(exc).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)          # ANSI colours
    msg = re.sub(r"\[[\w:]+\]\s*[\w-]*:?\s*", "", msg, count=1)  # "[youtube] id: "
    low = msg.lower()
    if "sign in to confirm" in low or "bot" in low:
        return "YouTube wants a sign-in. Turn on browser cookies in Settings and retry."
    if "drm" in low:
        return "That track is DRM-protected (SoundCloud Go+ / Premium only) — can't be downloaded."
    if "private" in low:
        return "That track/playlist is private."
    if "unavailable" in low or "removed" in low:
        return "That track is unavailable."
    if "no such file" in low and "ffmpeg" in low:
        return "ffmpeg is missing — run `sheep-mp3 setup` in Terminal"
    return (msg.splitlines()[0] if msg else "Download failed")[:200]


manager = DownloadManager()
