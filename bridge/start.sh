#!/bin/bash
# Inicia MVS, espera a que ss reporte LISTEN en el puerto 5000 y después levanta los bridges.

set -euo pipefail

LOGDIR=/app/logs
mkdir -p "$LOGDIR"

STARTUP_LOG="$LOGDIR/startup.log"

# Centralized PID directory for all helper PID files
PIDDIR=${PIDDIR:-/app/pids}
mkdir -p "$PIDDIR"

log() {
	echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$STARTUP_LOG"
}

# Cleanup function: attempt orderly shutdown of helper processes listed in pid files,
# wait briefly for them to exit, escalate to KILL if necessary, then remove pid files.
cleanup() {
	log "Cleanup: terminating helpers (if any) and removing pid files"
	# Remove or truncate console_watch log on shutdown so next run starts fresh
	if [ -f "$LOGDIR/console_watch.log" ]; then
		log "Removing $LOGDIR/console_watch.log as part of cleanup"
		rm -f "$LOGDIR/console_watch.log" || true
	fi
	# iterate over pid files in pid dir and logs dir
	for pidf in "$PIDDIR"/*.pid "$LOGDIR"/*.pid; do
		[ -e "$pidf" ] || continue
		pid=$(cat "$pidf" 2>/dev/null || true)
		if [ -z "$pid" ]; then
			log "Empty pid in $pidf; removing"
			rm -f "$pidf" || true
			continue
		fi
		if ps -p "$pid" > /dev/null 2>&1; then
			log "Attempting TERM -> pid=$pid (from $pidf)"
			kill -TERM "$pid" 2>/dev/null || true
			# wait up to 5 seconds for graceful shutdown
			waited=0
			while ps -p "$pid" > /dev/null 2>&1 && [ $waited -lt 5 ]; do
				sleep 1
				waited=$((waited+1))
			done
			if ps -p "$pid" > /dev/null 2>&1; then
				log "PID $pid did not exit after TERM; sending KILL"
				kill -KILL "$pid" 2>/dev/null || true
			else
				log "PID $pid exited after TERM"
			fi
		else
			log "No running process for pid file $pidf (pid $pid)"
		fi
		rm -f "$pidf" || true
		log "Removed pid file $pidf"
	done
	# ensure no stray pid files or ready files remain
	rm -f "$PIDDIR"/*.pid "$LOGDIR"/*.pid /app/*.ready "$LOGDIR"/*.ready || true
}

# Ensure cleanup runs on exit or termination
trap cleanup EXIT TERM INT

log "Starting MVS in background..."
	# Ensure any previous console_watch log is removed before (re)starting MVS
	if [ -f "$LOGDIR/console_watch.log" ]; then
		log "Removing existing $LOGDIR/console_watch.log before starting MVS"
		rm -f "$LOGDIR/console_watch.log" || true
	fi
	/tk5-/mvs &
	MVS_PID=$!
	echo $MVS_PID > "$PIDDIR/mvs.pid"
	log "MVS pid=$MVS_PID"

HOST=127.0.0.1
PORT=5000
TIMEOUT=240
INTERVAL=0.5

log "Waiting up to ${TIMEOUT}s for MVS to listen on ${HOST}:${PORT} (using ss)..."
deadline=$((SECONDS + TIMEOUT))
tries=0
while [ $SECONDS -lt $deadline ]; do
	tries=$((tries+1))
	if ss -ltn | tee -a "$STARTUP_LOG" | grep -q ":${PORT} \| ${HOST}:${PORT}\b"; then
		log "MVS is listening on ${HOST}:${PORT} (after ${tries} checks)"
		break
	fi
	sleep $INTERVAL
done

# ss_tries not recorded separately; diagnostics are in $STARTUP_LOG

if [ $SECONDS -ge $deadline ]; then
	log "Warning: timeout waiting for MVS on ${HOST}:${PORT} after ${tries} tries. Dumping ss -ltn output for diagnosis." 
	ss -ltn >> "$STARTUP_LOG" 2>&1 || true
	log "Starting bridges anyway (timeout)."
fi

if command -v python3 >/dev/null 2>&1; then
	PY=python3
elif command -v python >/dev/null 2>&1; then
	PY=python
else
	echo "No python interpreter found to start bridge" >&2
	exit 1
fi

HELPER_START_DELAY=${HELPER_START_DELAY:-1}
CW_INIT_LINE=${CW_INIT_LINE:-"MVS038J MVS 3.8j TK5 system initialization complete"}
CW_INIT_TIMEOUT=${CW_INIT_TIMEOUT:-240}
CW_POLL_INTERVAL=${CW_POLL_INTERVAL:-1}

if [ -f /app/console_watch.py ]; then
	log "Starting console_watch.py first..."
	# ensure console_watch.log is fresh for this run
	: > "$LOGDIR/console_watch.log" 2>/dev/null || true
	"$PY" /app/console_watch.py >> "$LOGDIR/console_watch.log" 2>&1 &
	CW_PID=$!
	echo $CW_PID > "$PIDDIR/console_watch.pid"
	log "console_watch pid=$CW_PID"
	log "Waiting for MVS init line in console_watch.log before starting console_bridge"
	# wait until console_watch.log contains the expected MVS init line (or timeout)
	deadline=$((SECONDS + CW_INIT_TIMEOUT))
	found=0
	while [ $SECONDS -lt $deadline ]; do
		if grep -Fq "$CW_INIT_LINE" "$LOGDIR/console_watch.log" 2>/dev/null; then
			log "Detected MVS init line in console_watch.log"
			found=1
			break
		fi
		sleep "$CW_POLL_INTERVAL"
	done
	if [ $found -eq 0 ]; then
		log "Timeout (${CW_INIT_TIMEOUT}s) waiting for MVS init line in console_watch.log; proceeding to start console_bridge anyway"
	fi
fi

log "Starting console_bridge.py..."
"$PY" /app/console_bridge.py >> "$LOGDIR/console_bridge.log" 2>&1 &
CB_PID=$!
echo $CB_PID > "$PIDDIR/console_bridge.pid"
log "console_bridge pid=$CB_PID"

HELPER_READY_TIMEOUT=${HELPER_READY_TIMEOUT:-30}
if [ -f /app/console_bridge.ready ]; then
	log "console_bridge ready file already present"
else
	log "Waiting up to ${HELPER_READY_TIMEOUT}s for console_bridge.ready"
	waited=0
	while [ $waited -lt $HELPER_READY_TIMEOUT ]; do
		if [ -f /app/console_bridge.ready ]; then
			log "Detected console_bridge.ready after ${waited}s"
			break
		fi
		sleep 1
		waited=$((waited+1))
	done
	if [ $waited -ge $HELPER_READY_TIMEOUT ]; then
		log "Timeout waiting for console_bridge.ready (${HELPER_READY_TIMEOUT}s); proceeding anyway"
	fi
fi

log "All helpers started. Waiting for MVS (pid ${MVS_PID}) to exit..."
wait $MVS_PID
# Give helpers a short grace period to finish writing logs / close sockets
# Configurable via environment variable STARTUP_GRACE_SECONDS (default: 2)
STARTUP_GRACE_SECONDS=${STARTUP_GRACE_SECONDS:-2}
log "MVS exited; waiting ${STARTUP_GRACE_SECONDS}s for helpers to finish before cleanup..."
sleep ${STARTUP_GRACE_SECONDS}
log "MVS (pid ${MVS_PID}) exited. Cleaning up."
log "Startup log available at $STARTUP_LOG"