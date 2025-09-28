#!/bin/bash
# Inicia MVS, espera a que ss reporte LISTEN en el puerto 5000 y después levanta los bridges.

set -euo pipefail

LOGDIR=/app/logs
mkdir -p "$LOGDIR"

echo "Starting MVS in background..."
/tk5-/mvs &
MVS_PID=$!
echo $MVS_PID > /app/mvs.pid

HOST=127.0.0.1
PORT=5000
TIMEOUT=240
INTERVAL=0.5

echo "Waiting up to ${TIMEOUT}s for MVS to listen on ${HOST}:${PORT} (using ss)..."
deadline=$((SECONDS + TIMEOUT))
while [ $SECONDS -lt $deadline ]; do
	if ss -ltn | grep -q ":${PORT} \| ${HOST}:${PORT}\b"; then
		echo "MVS is listening on ${HOST}:${PORT}"
		break
	fi
	sleep $INTERVAL
done

if [ $SECONDS -ge $deadline ]; then
	echo "Warning: timeout waiting for MVS on ${HOST}:${PORT}. Starting bridges anyway." >&2
fi

if command -v python3 >/dev/null 2>&1; then
	PY=python3
elif command -v python >/dev/null 2>&1; then
	PY=python
else
	echo "No python interpreter found to start bridge" >&2
	exit 1
fi

echo "Starting console_bridge.py..."
"$PY" /app/console_bridge.py >> "$LOGDIR/console_bridge.log" 2>&1 &
echo $! > /app/console_bridge.pid

if [ -f /app/console_watch.py ]; then
	echo "Starting console_watch.py..."
	"$PY" /app/console_watch.py >> "$LOGDIR/console_watch.log" 2>&1 &
	echo $! > /app/console_watch.pid
fi

echo "All helpers started. Waiting for MVS (pid ${MVS_PID}) to exit..."
wait $MVS_PID