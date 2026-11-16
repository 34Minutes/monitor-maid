#!/usr/bin/env bash
# Build Monitor Maid and replace /Applications/Monitor Maid.app.
#
# The bundle does not contain the database or grok-build.json. Those stay in
# ~/Library/Application Support/MonitorMaid/ and this script never deletes that
# folder. It removes only the old .app, then copies dist/Monitor Maid.app over it.
#
# Usage: scripts/build-deploy-local.sh [--no-smoke]
#   --no-smoke   install without the offscreen launch check

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
cd "$(repo_root)"

smoke=1
case "${1:-}" in
  "") ;;
  --no-smoke) smoke=0 ;;
  -h|--help)
    echo "Usage: scripts/build-deploy-local.sh [--no-smoke]"
    exit 0
    ;;
  *)
    echo "Usage: scripts/build-deploy-local.sh [--no-smoke]" >&2
    exit 2
    ;;
esac
if [[ $# -gt 1 ]]; then
  echo "Usage: scripts/build-deploy-local.sh [--no-smoke]" >&2
  exit 2
fi

pyinstaller="$PWD/.venv/bin/pyinstaller"
app_name="Monitor Maid.app"
built="$PWD/dist/$app_name"
installed="/Applications/$app_name"
binary="Contents/MacOS/Monitor Maid"
data_dir="$HOME/Library/Application Support/MonitorMaid"
# pgrep is regex. The brackets keep this script's own command line from matching.
app_pattern='/Applications/Monitor Maid[.]app/Contents/MacOS/Monitor Maid'

if [[ ! -x "$pyinstaller" ]]; then
  echo "Missing $pyinstaller." >&2
  echo "From the repo: python3 -m venv .venv && .venv/bin/pip install -r requirements-build.txt" >&2
  exit 1
fi

app_is_running() {
  pgrep -f "$app_pattern" >/dev/null
}

quit_running_app() {
  if ! app_is_running; then
    return 0
  fi
  echo "Quitting Monitor Maid. This uses a normal Quit. A force-quit marks the session crashed."
  # AppleScript can return -128 while the window is still closing. Wait below.
  osascript -e 'tell application "Monitor Maid" to quit' || true
  local i
  for i in $(seq 1 45); do
    if ! app_is_running; then
      return 0
    fi
    sleep 1
  done
  echo "Monitor Maid is still running. Quit it from the app, then run this again." >&2
  exit 1
}

# One line: mtime, size, and sha256. "absent" when the file is not there.
stamp_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    printf '%s %s\n' "$(stat -f '%m %z' "$file")" "$(shasum -a 256 "$file" | awk '{print $1}')"
  else
    printf 'absent\n'
  fi
}

stamp_data() {
  printf 'db %s\n' "$(stamp_file "$data_dir/monitor-maid.db")"
  printf 'log %s\n' "$(stamp_file "$data_dir/monitor-maid.log")"
  printf 'config %s\n' "$(stamp_file "$data_dir/grok-build.json")"
}

smoke_installed_app() {
  local smoke_dir log pid i found=0
  smoke_dir=$(mktemp -d /tmp/monitor-maid-smoke.XXXXXX)
  log="$smoke_dir/monitor-maid.log"
  echo "Smoke-checking the installed app. Its data goes to $smoke_dir."
  # QT_PLUGIN_PATH from another Qt app (PySide) aborts this PyQt6 build.
  env -u QT_PLUGIN_PATH \
    MONITOR_MAID_DATA_DIR="$smoke_dir" \
    QT_QPA_PLATFORM=offscreen \
    "$installed/$binary" >"$smoke_dir/stdout.txt" 2>&1 &
  pid=$!
  for i in $(seq 1 60); do
    if [[ -f "$log" ]] && grep -q "Host commands are using the login-shell environment." "$log"; then
      found=1
      break
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  kill -TERM "$pid" 2>/dev/null || true
  for i in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "Smoke process $pid did not exit after SIGTERM. It was left running." >&2
    echo "Smoke files: $smoke_dir" >&2
    exit 1
  fi
  wait "$pid" || true
  if [[ "$found" -ne 1 ]]; then
    echo "Smoke did not log the login-shell environment line." >&2
    echo "Smoke files: $smoke_dir" >&2
    exit 1
  fi
  rm -rf "$smoke_dir"
  echo "Smoke passed."
}

echo "Building $built"
"$pyinstaller" --noconfirm --clean monitor-maid.spec

if [[ ! -x "$built/$binary" ]]; then
  echo "Build finished without $built/$binary." >&2
  exit 1
fi

quit_running_app

before=$(stamp_data)

echo "Replacing $installed"
rm -rf "$installed"
ditto "$built" "$installed"

cmp "$built/$binary" "$installed/$binary"
codesign --verify --deep --strict "$installed"
echo "Installed binary matches the build, and the signature check passed."

if [[ "$smoke" -eq 1 ]]; then
  smoke_installed_app
else
  echo "Skipped the smoke check."
fi

after=$(stamp_data)
if [[ "$before" != "$after" ]]; then
  echo "Application Support changed during install:" >&2
  echo "before:" >&2
  printf '%s\n' "$before" >&2
  echo "after:" >&2
  printf '%s\n' "$after" >&2
  exit 1
fi

echo "Left in place: $data_dir"
echo "  monitor-maid.db, monitor-maid.log, and grok-build.json were not replaced."
echo "Open Monitor Maid from /Applications to load this build."
