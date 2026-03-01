#!/bin/bash
# Kill all ffmpeg processes on a given port
PORT=$1
if [ -z "$PORT" ]; then
    echo "Usage: kill_port.sh <port>"
    exit 1
fi
for i in 1 2 3; do
    pids=$(pgrep -f "ffmpeg.*$PORT" 2>/dev/null)
    [ -z "$pids" ] && exit 0
    kill -9 $pids 2>/dev/null
    sleep 0.3
done
