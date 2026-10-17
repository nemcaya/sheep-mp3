#!/bin/bash
# 🐑 Sheep MP3 — one-time installer for macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/nemcaya/sheep-mp3/main/install.sh | bash
#
# What it does (all inside your home folder, no sudo, no Homebrew, no Xcode):
#   1. installs `uv` (tiny Python tool manager) into ~/.local/bin if missing
#   2. downloads Sheep MP3 and installs the `sheep-mp3` command with its own Python
#   3. makes sure ~/.local/bin is on your PATH
#   4. fetches ffmpeg + deno into ~/Library/Application Support/SheepMP3/bin
#   5. opens the dashboard in your browser
#
# Re-running it later upgrades everything. Uninstall:  uv tool uninstall sheep-mp3
set -euo pipefail

TARBALL="${SHEEP_MP3_TARBALL:-https://github.com/nemcaya/sheep-mp3/archive/refs/heads/main.tar.gz}"
PY_VERSION="${SHEEP_MP3_PYTHON:-3.12}"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/Library/Application Support/SheepMP3"
SRC_DIR="$APP_DIR/src"

say()  { printf "  %s\n" "$*"; }
step() { printf "\n  🐑  %s\n" "$*"; }
die()  { printf "\n  ❌  %s\n\n" "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "Sheep MP3's installer is for macOS only."
command -v curl >/dev/null || die "curl is missing (it ships with macOS — something is very wrong)."

printf "\n  🐑  Sheep MP3 installer\n  ─────────────────────────\n"

# ---- 1. uv ------------------------------------------------------------------
export PATH="$BIN_DIR:$PATH"
# Never let uv probe the system python3: on a Mac without Xcode Command Line
# Tools, /usr/bin/python3 is Apple's stub that pops the "install developer
# tools" dialog. uv downloads its own Python instead.
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_DOWNLOADS=automatic
if ! command -v uv >/dev/null 2>&1; then
  step "Installing uv (Python tool manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 UV_INSTALL_DIR="$BIN_DIR" sh >/dev/null
  command -v uv >/dev/null 2>&1 || die "uv didn't install. Try again, or see https://docs.astral.sh/uv/"
fi
say "uv $(uv --version | awk '{print $2}') ready"

# ---- 2. source --------------------------------------------------------------
# If this script sits inside a checkout (pyproject.toml next to it), install from
# there; otherwise download the latest tarball from GitHub.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ] && grep -q '^name = "sheep-mp3"' "$SCRIPT_DIR/pyproject.toml"; then
  SRC="$SCRIPT_DIR"
  step "Installing from local checkout: $SRC"
else
  step "Downloading Sheep MP3…"
  mkdir -p "$APP_DIR"
  TMP_SRC="$APP_DIR/src.new"
  mkdir -p "$TMP_SRC"
  curl -fsSL "$TARBALL" | tar -xz --strip-components=1 -C "$TMP_SRC" \
    || die "Download failed. Check your internet connection and try again."
  [ -f "$TMP_SRC/pyproject.toml" ] || die "Downloaded archive looks wrong (no pyproject.toml)."
  # swap in atomically-ish: keep the previous copy around until the new one is in place
  if [ -d "$SRC_DIR" ]; then mv "$SRC_DIR" "$SRC_DIR.old"; fi
  mv "$TMP_SRC" "$SRC_DIR"
  [ -d "$SRC_DIR.old" ] && /bin/rm -rf "$SRC_DIR.old"
  SRC="$SRC_DIR"
fi

# ---- 3. install the command ----------------------------------------------------
step "Installing the sheep-mp3 command (Python $PY_VERSION + yt-dlp + spotDL)…"
say "This takes a minute the first time."
uv tool install --python "$PY_VERSION" --python-preference only-managed --force --reinstall "$SRC" 2>&1 | grep -v "^Resolved\|^Prepared\|^Uninstalled\|^Installed [0-9]" | sed 's/^/     /' || true
command -v sheep-mp3 >/dev/null 2>&1 || die "sheep-mp3 didn't install. Scroll up for the error and try again."

# ---- 4. PATH ----------------------------------------------------------------
added_path=0
for rc in "$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.bash_profile"; do
  case "$rc" in
    */.bash_profile) [ -f "$rc" ] || continue ;;   # only touch bash if it's in use
  esac
  if ! grep -qs 'local/bin' "$rc" 2>/dev/null; then
    printf '\n# Sheep MP3 / uv tools\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
    added_path=1
  fi
done
[ "$added_path" = 1 ] && say "Added ~/.local/bin to your PATH (takes effect in new Terminal windows)."

# ---- 5. tools + launch ------------------------------------------------------
step "Fetching ffmpeg + deno (one-off)…"
sheep-mp3 setup

step "Opening Sheep MP3…"
if [ "${SHEEP_MP3_NO_LAUNCH:-0}" != 1 ]; then
  sheep-mp3 || true
fi

cat <<DONE

  ✅  All set!

      Open it any time:   open Terminal, type   sheep-mp3   and press Return
      Stop it:            sheep-mp3 stop
      Keep it fresh:      sheep-mp3 update        (YouTube changes often)
      Tracks land in:     ~/Downloads/Sheep MP3

DONE
