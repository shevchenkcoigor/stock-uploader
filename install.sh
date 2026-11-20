#!/bin/sh
# Builds the desktop app on macOS or Linux. Usage: ./install.sh [--zip] [--no-smoke]
set -e
cd "$(dirname "$0")"

for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
        exec "$candidate" packaging/build.py "$@"
    fi
done
echo "Python 3.12 or newer is required. Install it from https://www.python.org/downloads/" >&2
exit 1
