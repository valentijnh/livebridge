#!/usr/bin/env bash
# LiveBridge installer wrapper for macOS (and Linux, for an MCP-only machine).
#
# Finds a suitable Python 3 (3.10+ preferred, 3.9 is enough for the installer itself -- it looks
# for a 3.10+ interpreter for the MCP server on its own) and runs installers/install.py with every
# argument passed through unchanged.
#
#   ./installers/install.sh                      # Live + Claude on this Mac
#   ./installers/install.sh --network            # Live also reachable from the LAN
#   ./installers/install.sh --pair 192.168.1.20 --token TOKEN
#   ./installers/install.sh --dry-run
#   ./installers/install.sh --uninstall [--dry-run]   # runs installers/uninstall.py instead
#
# Set LIVEBRIDGE_PYTHON=/path/to/python3 to force an interpreter.
# Compatible with the bash 3.2 that ships with macOS.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/install.py"
if [ "${1:-}" = "--uninstall" ]; then
    shift
    TARGET="$SCRIPT_DIR/uninstall.py"
fi

# Prints "major minor" for a python executable, or nothing if it does not run.
py_version() {
    "$1" -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null || true
}

PYTHON=""
BEST_MINOR=-1
pick() {
    candidate="$1"
    [ -n "$candidate" ] || return 0
    command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ] || return 0
    version="$(py_version "$candidate")"
    [ -n "$version" ] || return 0
    major="${version% *}"
    minor="${version#* }"
    [ "$major" = "3" ] || return 0
    [ "$minor" -ge 9 ] || return 0
    if [ "$minor" -gt "$BEST_MINOR" ]; then
        PYTHON="$candidate"
        BEST_MINOR="$minor"
    fi
}

if [ -n "${LIVEBRIDGE_PYTHON:-}" ]; then
    pick "$LIVEBRIDGE_PYTHON"
    if [ -z "$PYTHON" ]; then
        echo "LIVEBRIDGE_PYTHON=$LIVEBRIDGE_PYTHON is not a working Python 3.9+." >&2
        exit 1
    fi
else
    for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        pick "$candidate"
    done
fi

if [ -z "$PYTHON" ]; then
    cat >&2 <<'MSG'
LiveBridge needs Python 3 (3.10 or newer recommended) and none was found.

Install one of:
  - https://www.python.org/downloads/macos/   (official installer)
  - brew install python                       (Homebrew)
  - uv: curl -LsSf https://astral.sh/uv/install.sh | sh, then re-run with --uv

Then run this script again.
MSG
    exit 1
fi

if [ "$BEST_MINOR" -lt 10 ]; then
    echo "Note: $PYTHON is Python 3.$BEST_MINOR; the installer will look for Python 3.10+ for the MCP server." >&2
fi

exec "$PYTHON" "$TARGET" "$@"
