#!/bin/bash
# Meeting Scribe launcher — sets itself up on first run, then starts the app.
set -u
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
GUI="${1:-cli}"

# Where writable state lives. Inside an .app bundle the code directory is
# read-only (Gatekeeper translocates unsigned apps to a read-only mount), so
# the venv, config, database, and logs all go to Application Support instead.
if [[ "$APP_DIR" == *"/Contents/Resources/"* || "${2:-}" == "--bundled" ]]; then
  SUPPORT_DIR="$HOME/Library/Application Support/MeetingScribe"
  VENV="$SUPPORT_DIR/.venv"
  DATA_DIR="$SUPPORT_DIR/data"
  export SCRIBE_DATA_DIR="$DATA_DIR"
else
  VENV="$APP_DIR/.venv"
  DATA_DIR="$APP_DIR/data"
fi
LOG_FILE="$DATA_DIR/scribe.log"
mkdir -p "$DATA_DIR"

say()    { echo "● $1"; }
notify() { if [ "$GUI" = "gui" ]; then osascript -e "display notification \"$1\" with title \"Meeting Scribe\"" >/dev/null 2>&1 || true; fi; }
fail() {
  echo "✗ $1"
  if [ "$GUI" = "gui" ]; then
    osascript -e "display dialog \"$1\" with title \"Meeting Scribe\" buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1 || true
  fi
  exit 1
}

# ---- 1. Python 3.9+ ----
if ! command -v python3 >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  fail "Python 3 is needed (one-time). macOS should have just shown an 'Install developer tools' popup — click Install, wait for it to finish, then open Meeting Scribe again."
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
  fail "Your Python 3 is too old (3.9+ needed). Install the latest Python from python.org, then open Meeting Scribe again."
fi

# ---- 2. Private environment + components ----
REQ_SUM="$(/usr/bin/openssl md5 -r requirements.txt 2>/dev/null | cut -d' ' -f1)"
MARK="$VENV/.deps-${REQ_SUM:-x}"

if [ ! -x "$VENV/bin/python" ]; then
  say "First-time setup — preparing Meeting Scribe (takes a minute or two)…"
  notify "First-time setup — takes a minute or two"
  python3 -m venv "$VENV" 2>/dev/null || { rm -rf "$VENV"; python3 -m venv "$VENV"; } \
    || fail "Could not create the app's Python environment. See $LOG_FILE."
fi
if [ ! -f "$MARK" ]; then
  say "Downloading components (Flask, audio tools)…"
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip >/dev/null 2>&1 || true
  if ! "$VENV/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt; then
    fail "Could not download the app's components. Check your internet connection and try again."
  fi
  rm -f "$VENV"/.deps-* 2>/dev/null || true
  touch "$MARK"
  say "Setup complete."
fi

# ---- 3. Start (keeps running in the background) ----
say "Starting Meeting Scribe…"
notify "Starting Meeting Scribe…"
nohup "$VENV/bin/python" "$APP_DIR/run.py" >> "$LOG_FILE" 2>&1 &

# Wait briefly and sanity-check
STARTED=""
for i in $(seq 1 20); do
  sleep 0.5
  if tail -n 5 "$LOG_FILE" 2>/dev/null | grep -q "Meeting Scribe"; then STARTED="yes"; break; fi
done
if tail -n 30 "$LOG_FILE" 2>/dev/null | grep -q "Traceback"; then
  fail "Meeting Scribe hit an error while starting. Open $LOG_FILE and send it to whoever set this up."
fi

say "Meeting Scribe is running — your browser will open in a moment."
say "It keeps running in the background; quit it anytime from the app's Settings page."
say "You can close this window."
exit 0
