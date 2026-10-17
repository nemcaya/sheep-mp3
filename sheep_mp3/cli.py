"""`sheep-mp3` command-line entry point.

    sheep-mp3            start the local server (if needed) and open the dashboard
    sheep-mp3 stop       shut the background server down
    sheep-mp3 setup      download ffmpeg / ffprobe / deno (done once, automatically)
    sheep-mp3 update     refresh yt-dlp & friends (YouTube changes often)
    sheep-mp3 status     show versions, paths and whether the server is running
    sheep-mp3 serve      run the server in the foreground (Ctrl+C to stop)

The default command spawns the server as a *detached* background process and
exits right away, so the Terminal window can be closed. The dashboard's Quit
button, `sheep-mp3 stop`, or a few idle hours shut it down again.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, bins, config

SHEEP = "🐑"
INSTALL_ONE_LINER = "curl -fsSL https://raw.githubusercontent.com/Slover-69/sheep-mp3/main/install.sh | bash"


# ---- helpers -----------------------------------------------------------------

def _say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def server_up() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        return sock.connect_ex((config.HOST, config.PORT)) == 0
    finally:
        sock.close()


def _wait(predicate, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return predicate()


def _progress(name: str, got: int, total: int) -> None:
    mb = got / 1_000_000
    if total:
        pct = int(got / total * 100)
        sys.stdout.write(f"\r  ↓ {name:<8} {pct:3d}%  ({mb:5.1f} MB)")
    else:
        sys.stdout.write(f"\r  ↓ {name:<8} {mb:5.1f} MB")
    sys.stdout.flush()
    if total and got >= total:
        sys.stdout.write("\n")


def ensure_binaries(force: bool = False) -> None:
    need = bins.missing() if not force else ["ffmpeg", "ffprobe", "deno"]
    if not need:
        return
    _say(f"{SHEEP} First-time setup: fetching {', '.join(need)} (one-off, ~130 MB)…")
    try:
        bins.ensure_all(_progress, force=force)
    except Exception as exc:
        _say(f"⚠️  Couldn't download {', '.join(need)}: {exc}")
        _say("   Check your internet connection and run `sheep-mp3 setup` again.")
        raise SystemExit(1)
    _say(f"✓ Tools ready in {config.bin_dir()}")


def _read_pid() -> int | None:
    try:
        return int(config.pid_file().read_text().strip())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---- commands ----------------------------------------------------------------

def cmd_open(_args) -> int:
    ensure_binaries()
    if not server_up():
        _spawn_server()
        if not _wait(server_up, 30):
            _say("❌ The server didn't start. Last lines of the log:")
            try:
                for line in config.log_file().read_text().splitlines()[-15:]:
                    print("     " + line)
            except Exception:
                pass
            return 1
    webbrowser.open(config.URL)
    _say(f"{SHEEP} Sheep MP3 is open at {config.URL}")
    _say("   You can close this window. Stop it later with:  sheep-mp3 stop")
    return 0


def _spawn_server() -> None:
    log = open(config.log_file(), "a")
    log.write(f"\n--- sheep-mp3 {__version__} starting {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log.flush()
    subprocess.Popen(
        [sys.executable, "-m", "sheep_mp3.cli", "serve", "--no-browser"],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        start_new_session=True, close_fds=True,
    )


def cmd_serve(args) -> int:
    ensure_binaries()
    if server_up():
        _say(f"Already running at {config.URL}")
        if not args.no_browser:
            webbrowser.open(config.URL)
        return 0
    import uvicorn
    from .app import app

    config.pid_file().write_text(str(os.getpid()))
    if not args.no_browser:
        import threading

        def _open_when_up():
            if _wait(server_up, 30):
                webbrowser.open(config.URL)
        threading.Thread(target=_open_when_up, daemon=True).start()
        _say(f"{SHEEP} Serving Sheep MP3 at {config.URL}  (Ctrl+C to stop)")
    try:
        uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
    finally:
        try:
            config.pid_file().unlink()
        except Exception:
            pass
    return 0


def cmd_stop(_args) -> int:
    if not server_up():
        _say("Sheep MP3 isn't running.")
        return 0
    try:
        req = urllib.request.Request(f"{config.URL}/api/quit", method="POST")
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass
    if _wait(lambda: not server_up(), 5):
        _say(f"{SHEEP} Sheep MP3 stopped.")
        return 0
    pid = _read_pid()
    if pid and _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        if _wait(lambda: not server_up(), 5):
            _say(f"{SHEEP} Sheep MP3 stopped.")
            return 0
    _say("⚠️  Couldn't stop the server on port %d." % config.PORT)
    return 1


def cmd_setup(args) -> int:
    ensure_binaries(force=args.force)
    if not args.force and not bins.missing():
        _say(f"✓ ffmpeg, ffprobe and deno are ready.")
    return 0


def cmd_update(_args) -> int:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv).exists():
        _say("uv isn't installed, so I can't update in place. Re-run the installer:")
        _say("   " + INSTALL_ONE_LINER)
        return 1
    was_running = server_up()
    if was_running:
        cmd_stop(None)
    _say(f"{SHEEP} Updating yt-dlp, spotDL and friends…")
    # only-managed: never probe /usr/bin/python3 (Xcode CLT stub on a fresh Mac)
    env = {**os.environ, "UV_PYTHON_PREFERENCE": "only-managed", "UV_PYTHON_DOWNLOADS": "automatic"}
    rc = subprocess.call([uv, "tool", "upgrade", "sheep-mp3"], env=env)
    if rc != 0:
        _say("⚠️  Update failed. Full refresh:")
        _say("   " + INSTALL_ONE_LINER)
        return rc
    subprocess.call([uv, "tool", "run", "--from", "sheep-mp3", "sheep-mp3", "setup"], env=env)
    _say("✓ Updated. For a brand-new version of Sheep MP3 itself, re-run the installer:")
    _say("   " + INSTALL_ONE_LINER)
    if was_running:
        cmd_open(None)
    return 0


def cmd_status(_args) -> int:
    import yt_dlp
    try:
        from spotdl import __version__ as spotdl_version
    except Exception:
        spotdl_version = "missing"
    rows = [
        ("Sheep MP3", __version__),
        ("Python", sys.version.split()[0] + f"  ({sys.executable})"),
        ("yt-dlp", yt_dlp.version.__version__),
        ("spotDL", spotdl_version),
        ("ffmpeg", bins.ffmpeg_exe() or "MISSING — run `sheep-mp3 setup`"),
        ("ffprobe", bins.ffprobe_exe() or "MISSING — run `sheep-mp3 setup`"),
        ("deno", bins.deno_exe() or "MISSING — run `sheep-mp3 setup` (YouTube needs it)"),
        ("Server", f"running at {config.URL}" if server_up() else "not running"),
        ("Saves to", config.load_settings()["output_dir"]),
        ("Settings", str(config.app_support() / "settings.json")),
        ("Log", str(config.log_file())),
    ]
    for k, v in rows:
        print(f"  {k:<10} {v}")
    return 0


# ---- entry -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sheep-mp3",
        description="🐑 Sheep MP3 — paste YouTube / SoundCloud / Spotify links, get tagged MP3s.",
    )
    p.add_argument("--version", action="version", version=f"sheep-mp3 {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("open", help="start the server if needed and open the dashboard (default)")
    s = sub.add_parser("serve", help="run the server in the foreground (Ctrl+C to stop)")
    s.add_argument("--no-browser", action="store_true", help="don't open the dashboard")
    sub.add_parser("stop", help="shut the background server down")
    s = sub.add_parser("setup", help="download ffmpeg / ffprobe / deno into the app folder")
    s.add_argument("--force", action="store_true", help="re-download even if present")
    sub.add_parser("update", help="upgrade yt-dlp, spotDL and friends")
    sub.add_parser("status", help="show versions, paths and server state")

    args = p.parse_args(argv)
    handler = {
        None: cmd_open, "open": cmd_open, "serve": cmd_serve, "stop": cmd_stop,
        "setup": cmd_setup, "update": cmd_update, "status": cmd_status,
    }[args.cmd]
    try:
        return handler(args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
