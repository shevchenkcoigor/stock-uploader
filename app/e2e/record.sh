#!/usr/bin/env bash
# Runs the window tests while recording the X display into artifacts/run.mp4. Linux only: CI starts it under
# xvfb-run and uploads the video when the run failed.
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p artifacts

ffmpeg -loglevel error -y -f x11grab -video_size 1280x800 -framerate 10 -i "$DISPLAY" \
  -pix_fmt yuv420p artifacts/run.mp4 &
recorder=$!

npm test
status=$?

# SIGINT lets ffmpeg finish the file; a killed recorder leaves a video that does not play.
kill -INT "$recorder" 2>/dev/null
wait "$recorder"
exit "$status"
