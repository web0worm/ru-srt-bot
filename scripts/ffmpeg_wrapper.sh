#!/bin/bash
# FFmpeg auto-restart wrapper
# Usage: ffmpeg_wrapper.sh "/path/to/log" <ffmpeg args...>
LOG_FILE="$1"
shift

FFMPEG_PID=0

cleanup() {
    echo "[$(date)] Received signal, stopping ffmpeg (pid=$FFMPEG_PID)..." >> "$LOG_FILE"
    if [ $FFMPEG_PID -ne 0 ]; then
        kill $FFMPEG_PID 2>/dev/null
        wait $FFMPEG_PID 2>/dev/null
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT SIGHUP

while true; do
    echo "[$(date)] Starting ffmpeg..." >> "$LOG_FILE"
    ffmpeg -nostdin "$@" >> "$LOG_FILE" 2>&1 &
    FFMPEG_PID=$!
    wait $FFMPEG_PID
    EXIT_CODE=$?
    FFMPEG_PID=0
    echo "[$(date)] ffmpeg exited with code $EXIT_CODE, restarting in 0.5s..." >> "$LOG_FILE"
    sleep 0.5
done
