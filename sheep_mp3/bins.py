"""Resolve (and, if needed, fetch) the external binaries Sheep MP3 relies on.

* ffmpeg / ffprobe — audio extraction, tagging, cover art.
* deno            — JavaScript runtime that yt-dlp needs to solve YouTube's
                    player challenges (without it YouTube downloads degrade or fail).

Resolution order for each binary:
  $SHEEPMP3_BIN/<name>  ->  ~/Library/Application Support/SheepMP3/bin/<name>
  ->  PATH  ->  the usual Homebrew locations.

`ensure_all()` downloads whatever is missing into the app-support bin dir, so a
fresh Mac with no Homebrew / Xcode works after `sheep-mp3 setup`.
"""
from __future__ import annotations

import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import config

USER_AGENT = "sheep-mp3 (https://github.com/Slover-69/sheep-mp3)"
MIN_DENO = (2, 3, 0)

_FALLBACK_DIRS = [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def _asset_urls() -> dict[str, tuple[str, str | None]]:
    """name -> (url, member-inside-zip or None for a bare binary)."""
    arch = _arch()
    ff = "https://github.com/eugeneware/ffmpeg-static/releases/latest/download"
    deno_triple = "aarch64-apple-darwin" if arch == "arm64" else "x86_64-apple-darwin"
    return {
        "ffmpeg": (f"{ff}/ffmpeg-darwin-{arch}", None),
        "ffprobe": (f"{ff}/ffprobe-darwin-{arch}", None),
        "deno": (f"https://github.com/denoland/deno/releases/latest/download/deno-{deno_triple}.zip", "deno"),
    }


# ---- lookup ----------------------------------------------------------------

def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("SHEEPMP3_BIN")
    if env:
        dirs.append(Path(env))
    dirs.append(config.bin_dir())
    return dirs


def find_binary(name: str) -> str | None:
    for d in _candidate_dirs():
        cand = d / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    for d in _FALLBACK_DIRS:
        cand = d / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def ffmpeg_exe() -> str | None:
    return find_binary("ffmpeg")


def ffprobe_exe() -> str | None:
    return find_binary("ffprobe")


def ffmpeg_dir() -> str | None:
    exe = ffmpeg_exe()
    return str(Path(exe).parent) if exe else None


def have_ffmpeg() -> bool:
    return bool(ffmpeg_exe() and ffprobe_exe())


def _deno_version(path: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"deno (\d+)\.(\d+)\.(\d+)", out)
        return tuple(int(x) for x in m.groups()) if m else None
    except Exception:
        return None


def deno_exe() -> str | None:
    """A deno binary new enough for yt-dlp, or None."""
    for d in _candidate_dirs():
        cand = d / "deno"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    for cand in filter(None, [shutil.which("deno"), *[str(d / "deno") for d in _FALLBACK_DIRS]]):
        if Path(cand).is_file():
            ver = _deno_version(cand)
            if ver and ver >= MIN_DENO:
                return cand
    return None


def have_deno() -> bool:
    return deno_exe() is not None


def missing() -> list[str]:
    out = []
    if not ffmpeg_exe():
        out.append("ffmpeg")
    if not ffprobe_exe():
        out.append("ffprobe")
    if not deno_exe():
        out.append("deno")
    return out


# ---- fetching --------------------------------------------------------------

Progress = Callable[[str, int, int], None]  # (name, downloaded_bytes, total_bytes)


def _download(url: str, name: str, progress: Progress | None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        got = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if progress:
                progress(name, got, total)
    return buf.getvalue()


def fetch(name: str, progress: Progress | None = None) -> Path:
    """Download one binary into the app-support bin dir and return its path."""
    url, member = _asset_urls()[name]
    data = _download(url, name, progress)
    if member:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            data = zf.read(member)
    dest = config.bin_dir() / name
    tmp = Path(tempfile.mkstemp(dir=str(config.bin_dir()), prefix=f".{name}-")[1])
    tmp.write_bytes(data)
    tmp.chmod(0o755)
    tmp.replace(dest)
    return dest


def ensure_all(progress: Progress | None = None, force: bool = False) -> list[str]:
    """Fetch every missing binary. Returns the names that were downloaded."""
    if sys.platform != "darwin":
        raise RuntimeError("Sheep MP3's automatic binary setup only supports macOS. "
                           "Install ffmpeg + deno yourself and put them on PATH.")
    names = ["ffmpeg", "ffprobe", "deno"] if force else missing()
    for name in names:
        fetch(name, progress)
    return names
