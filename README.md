README — console_bridge mini-implementation

Overview
--------
This folder contains a small, robust Python-based bridge used to capture printer output
from a Hercules TK5 instance (unit-record device) and convert it into spool files and
separate job-log files. The implementation is tuned for running inside a container and
is intended to be resilient to startup order, EBCDIC-encoded streams, and multiple
objects in a single spool.

Main responsibilities
- Start/stop lifecycle and PID management (via `start.sh` and a centralized `/app/pids`).
- Connect as a TCP client to Hercules (default 127.0.0.1:5000).
- Save each raw spool to `OUTDIR` (default `/app/spool`) as `spool_YYYYMMDD.bin`.
- Extract logical objects delimited by explicit START/END markers and write them as
  `joblog_YYYYMMDD_HHMMSS_NNN.txt` (UTF-8) or binary for ASCII streams.
- Support EBCDIC streams: try common codepages (cp037, cp1047, cp500) and extract
  textual joblogs after decoding.
- Provide logs and a raw binary dump for debugging (`/app/logs/console_bridge.log` and
  `/app/logs/console_bridge-raw.bin`).

Files and roles
---------------
- `start.sh`
  - Orchestrates container startup: launch MVS, wait until `ss -ltn` shows the listener,
    start `console_watch.py` and wait for the MVS initialization line in `console_watch.log`.
  - Creates and uses a centralized PID directory (`/app/pids` by default).
  - Starts `console_bridge.py` only after the MVS init line is detected (or a timeout).
  - Implements a cleanup trap to gracefully terminate helper processes and remove pid/ready
    files.

- `tk5.cnf`
  - A sample TK5 configuration file is included (`tk5.cnf`) so you can configure unit-record
    devices (printers) to point at the Python socket listener ports used by the bridge
    (for example `127.0.0.1:5000` for the printer socket or other ports for auxiliary
    consoles). Edit the device entries in `tk5.cnf` to match your container/host networking
    if you want Hercules to send printer output directly to the bridge.

- `console_bridge.py`
  - Main bridge client that connects to Hercules and receives printer output.
  - Writes a full spool binary file (`spool_YYYYMMDD.bin`) for each spool session.
  - Appends every received chunk to `/app/logs/console_bridge-raw.bin` for offline analysis.
  - JobLogExtractor class:
    - Binary mode: looks for the binary START marker `b"****A  START"`, then writes data
      until the END marker `b"****A   END"` is found. Each object is saved as
      `joblog_*.txt` (or .txt containing decoded text).
    - Text mode: tries decoding incoming chunks with common EBCDIC codepages; if the
      decoded stream contains the textual START marker `"****A  START"` it will keep a
      decoded buffer and extract objects delimited by START/END into UTF-8 files.
  - PID handling: writes its own PID into `/app/pids/console_bridge.pid` by default.
  - Configurable socket and buffer sizes (via environment variables) and forcible
    flush after each write to reduce the chance of truncated spools.

- `console_watch.py`
  - Lightweight watcher that connects to a different Hercules console port (default
    127.0.0.1:5002) and logs printer lines into `bridge/logs/console_watch.log`.
  - Writes `/app/pids/console_watch.pid` on start and removes it on exit.
  - `start.sh` relies on `console_watch.log` to detect the MVS init message:
    "MVS038J MVS 3.8j TK5 system initialization complete".

Key protocol
------------
- The extractor uses explicit START/END markers (exact bytes/text):
  - START (bytes):    b"****A  START"
  - END (bytes):      b"****A   END"
  - START (text):     "****A  START"
  - END (text):       "****A   END"

Behavioral contract (short)
- Input: TCP stream from Hercules listener (127.0.0.1:5000 by default).
- Output:
  - `/app/spool/spool_YYYYMMDD.bin` — full raw spool per session.
  - `/app/spool/joblog_YYYYMMDD_HHMMSS_NNN.txt` — extracted objects (UTF-8), one file per
    START..END pair.
- Error modes:
  - If the START/END pair is split across recv() calls, the extractor maintains an internal
    buffer so a fragmented marker is still detected.
  - If stream is EBCDIC, the implementation attempts decoding with cp037/cp1047/cp500 and
    extracts textual joblogs from the decoded stream.

Configuration (environment variables)
-------------------------------------
- `BRIDGE_HOST` (default 127.0.0.1)
- `BRIDGE_PORT` (default 5000)
- `BRIDGE_OUTDIR` (default `/app/spool`)
- `BRIDGE_LOGDIR` (default `/app/logs`)
- `BRIDGE_PIDDIR` (default `/app/pids`)
- `BRIDGE_PIDFILE` (default `$BRIDGE_PIDDIR/console_bridge.pid`)
- `BRIDGE_READYFILE` (default `/app/pids/console_bridge.ready`)
- `BRIDGE_RECV_SIZE` (default 65536) — number of bytes passed to `socket.recv()` per call.
- `BRIDGE_SO_RCVBUF` (default 2 * BRIDGE_RECV_SIZE) — attempted kernel socket receive buffer size.
- `CW_INIT_LINE` (start.sh) — the init line console_watch looks for (defaults to the MVS init message).
- `CW_INIT_TIMEOUT`, `CW_POLL_INTERVAL` configure how long start.sh waits for the init line.

How it handles multiple objects in-series
---------------------------------------
- The extractor writes to a new joblog file as soon as it detects a START marker and closes
  that file when it finds the corresponding END marker. After closing, it continues
  processing the remainder of the buffer, so it can handle multiple START..END objects
  that arrive back-to-back in the same connection/spool.

Troubleshooting
---------------
- If no `joblog_` files appear:
  - Check `/app/logs/console_bridge-raw.bin` with a hex dump (`xxd`) to confirm if data is
    ASCII or EBCDIC.
  - If EBCDIC, ensure the START/END markers appear in the decoded text. If not, adjust
    the marker strings to match your device output.
- If spools are truncated:
  - Increase `BRIDGE_RECV_SIZE` (e.g. 131072 or 262144) and `BRIDGE_SO_RCVBUF` accordingly.
  - Ensure the container is not being killed abruptly; graceful shutdown gives time for
    flush/close operations.

Quick test steps
----------------
1) Start the system (from repository root):
```pwsh
docker compose down
docker compose build
docker compose up -d
```

2) Submit a test job in Hercules that generates printer output containing the markers
   `****A  START` and `****A   END` (either as ASCII or in your EBCDIC codepage).

3) Inspect outputs:
```pwsh
docker compose exec <service> ls -l /app/spool
docker compose exec <service> tail -n 200 /app/logs/console_bridge.log
docker compose exec <service> head -c 512 /app/logs/console_bridge-raw.bin | xxd
```

ASCII block diagram
-------------------
Legend: [process] -> communication/interaction

[Docker container entrypoint start.sh]
        |
        +-- starts -> [MVS process] (background)
        |
        +-- starts -> [console_watch.py] -> writes to -> /app/logs/console_watch.log
        |                         (start.sh waits for init line here)
        |
        +-- starts -> [console_bridge.py] (client)
                      |
                      +-- connects to Hercules listener (127.0.0.1:5000)
                      |
                      +-- receives bytes -> appends to /app/logs/console_bridge-raw.bin
                      |
                      +-- writes full spool -> /app/spool/spool_YYYYMMDD.bin
                      |
                      +-- runs JobLogExtractor:
                           - binary mode: detect b"****A  START" ... b"****A   END" -> write joblog file
                           - text mode: try cp037/cp1047/cp500 decode -> detect START/END -> write UTF-8 joblog file

Notes
-----
- Marker strings are exact and space-sensitive. If the real output has different spacing
  or extra characters, adapt the `START_PATTERN` / `END_PATTERN` values in
  `console_bridge.py` accordingly.
- The design favors safety and debuggability: raw dumps, rotating logs, pid files, and
  explicit readiness checks.

If you want
-----------
- I can: add detection logs that print which encoding (if any) matched, write an
  additional binary copy of each joblog (EBCDIC raw), or make joblog filenames include
  extracted metadata from the job header.
- Tell me which of those you prefer and I will implement it.


API (FastAPI)
-------------
There is a small FastAPI app included under `bridge/api` that exposes the outputs
produced by `console_bridge.py` and `console_watch.py` so you can query spools,
joblogs, logs and status over HTTP. The API is launched inside the TK5 container
after `console_bridge` signals readiness (see `start.sh` behavior).

Quick start (inside the container or in an environment with access to /app):

1. Install deps (if not already present in the image):

```pwsh
pip install -r /app/bridge/api/requirements.txt
```

2. Run the API (default port 8000):

```pwsh
python /app/bridge/api/app.py
```

3. Example endpoints:
- GET /health — basic status and whether the bridge ready-file exists
- GET /spools — list spool files
- GET /spools/{name} — download a spool file
- GET /joblogs — list extracted joblogs
- GET /joblogs/{name} — download an extracted joblog
- GET /logs/{logname}?lines=200 — tail a log file (console_bridge.log, console_watch.log)
- GET /pids — list pid files under `/app/pids`
- GET /ready — check for the bridge ready file
- GET /raw/search?q=****A — simple search in the first chunk of the raw dump
- GET /stream/watch — Server-Sent Events stream of new lines appended to `console_watch.log`
- GET /joblogs/{name}/meta — metadata for a joblog (size, mtime, first lines)

Configuration:
- The API reads the same environment variables used by the bridge to locate
  directories: `BRIDGE_OUTDIR`, `BRIDGE_LOGDIR`, `BRIDGE_PIDDIR`, `BRIDGE_READYFILE`.
- To change port: set `API_PORT` environment variable before launching.

Notes:
- The API is intentionally small and read-only. It does not modify bridge files.
- The API is started by `start.sh` after `console_bridge.ready` appears (or after the configured timeout).
