from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import io
import re

# Configurable directories (match bridge defaults)
OUTDIR = Path(os.getenv("BRIDGE_OUTDIR", "/app/spool"))
LOGDIR = Path(os.getenv("BRIDGE_LOGDIR", "/app/logs"))
PIDDIR = Path(os.getenv("BRIDGE_PIDDIR", "/app/pids"))
READY_FILE = Path(os.getenv("BRIDGE_READYFILE", str(PIDDIR / "console_bridge.ready")))

app = FastAPI(title="OpenMVS Bridge API", version="0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ensure_dir(d: Path):
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def list_dir_files(d: Path, pattern: str = "*") -> List[Dict[str, Any]]:
    ensure_dir(d)
    files = [p for p in d.glob(pattern) if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files:
        # derive job id/name from the filename stem (without extension)
        job_id = None
        job_name = None
        try:
            stem = p.stem
            if "-" in stem:
                left, right = stem.split("-", 1)
                if re.match(r"^JOB\d+$", left):
                    job_id = left
                    job_name = right.split('.')[0].strip()
            else:
                if re.match(r"^JOB\d+$", stem):
                    job_id = stem
        except Exception:
            job_id = None
            job_name = None

        out.append({
            "file-name": p.name,
            "job-name": job_name,
            "job-id": job_id,
            "path": str(p),
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })
    return out


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "bridge_ready": READY_FILE.exists(),
        "spool_dir_exists": OUTDIR.exists(),
        "log_dir_exists": LOGDIR.exists(),
        "pid_dir_exists": PIDDIR.exists(),
    }


@app.get("/spools")
async def list_spools(job_name: Optional[str] = None, job_id: Optional[str] = None):
    """List spools; optional query params:
    - job_name: returns entries whose job-name matches (case-insensitive)
    - job_id: returns entries whose job-id matches (case-insensitive)
    If both provided, both filters are applied.
    """
    items = list_dir_files(OUTDIR, "JOB*")
    if job_name:
        jn = job_name.strip().lower()
        items = [i for i in items if i.get("job-name") and i.get("job-name").strip().lower() == jn]
    if job_id:
        jid = job_id.strip().lower()
        items = [i for i in items if i.get("job-id") and i.get("job-id").strip().lower() == jid]
    return items


@app.get("/spools/{name}")
async def get_spool(name: str):
    p = OUTDIR / name
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Spool not found")
    return FileResponse(path=str(p), media_type="application/octet-stream", filename=p.name)


@app.get("/joblogs")
async def list_joblogs(job_name: Optional[str] = None, job_id: Optional[str] = None):
    """List joblogs; supports same optional filters as /spools.
    """
    items = list_dir_files(OUTDIR, "joblog_*")
    if job_name:
        jn = job_name.strip().lower()
        items = [i for i in items if i.get("job-name") and i.get("job-name").strip().lower() == jn]
    if job_id:
        jid = job_id.strip().lower()
        items = [i for i in items if i.get("job-id") and i.get("job-id").strip().lower() == jid]
    return items


@app.get("/joblogs/{name}")
async def get_joblog(name: str):
    p = OUTDIR / name
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Joblog not found")
    # Serve as text when possible
    return FileResponse(path=str(p), media_type="text/plain; charset=utf-8", filename=p.name)


def tail_lines(path: Path, lines: int = 200) -> str:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    # Read backwards in binary to avoid encoding issues
    with path.open("rb") as f:
        avg_line_size = 200
        to_read = lines * avg_line_size
        try:
            f.seek(0, 2)
            file_size = f.tell()
            if to_read < file_size:
                f.seek(-to_read, 2)
            else:
                f.seek(0)
            data = f.read()
        except OSError:
            f.seek(0)
            data = f.read()
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = data.decode("latin-1", errors="replace")
    arr = text.splitlines()
    return "\n".join(arr[-lines:])


@app.get('/stream/watch')
async def stream_watch(request: Request):
    """Stream new lines from console_watch.log as Server-Sent Events (SSE).
    Keep the connection open and yield new lines as they are appended.
    """
    log_path = LOGDIR / 'console_watch.log'
    ensure_dir(LOGDIR)
    # If file doesn't exist yet, create an empty one
    if not log_path.exists():
        log_path.write_text("")

    async def event_generator():
        with log_path.open('r', encoding='utf-8', errors='replace') as f:
            # seek to end
            f.seek(0, 2)
            while True:
                if await request.is_disconnected():
                    break
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    # no new line, wait a bit
                    import asyncio
                    await asyncio.sleep(0.5)

    # Return a StreamingResponse with the SSE media type. This avoids
    # relying on EventSourceResponse from starlette which may not be present
    # in all versions. The generator yields properly formatted SSE events.
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get('/joblogs/{name}/meta')
async def joblog_meta(name: str, head_lines: int = 8):
    p = OUTDIR / name
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail='Joblog not found')
    stat = p.stat()
    # attempt to read first few lines for metadata (try utf-8 then fallback)
    try:
        with p.open('r', encoding='utf-8') as f:
            first = [next(f).rstrip('\n') for _ in range(head_lines)]
    except Exception:
        try:
            with p.open('r', encoding='cp037', errors='replace') as f:
                first = [next(f).rstrip('\n') for _ in range(head_lines)]
        except Exception:
            first = []
    # parse filename for job id and job name (e.g. JOB00002-ECHO.txt)
    job_id = None
    job_name = None
    try:
        base = p.stem  # filename without suffix
        # split on first dash
        if "-" in base:
            left, right = base.split("-", 1)
            # validate left side matches JOB + digits
            if re.match(r"^JOB\d+$", left):
                job_id = left
                job_name = right
        else:
            if re.match(r"^JOB\d+$", base):
                job_id = base
    except Exception:
        job_id = None
        job_name = None
    # defensive cleanup: remove any accidental extension or trailing dots/whitespace
    if job_id:
        job_id = job_id.split('.')[0].strip()
    if job_name:
        # job_name may include dots if the original stem had multiple dots; keep left part
        job_name = job_name.split('.')[0].strip()

    return {
        'name': p.name,
        'size': stat.st_size,
        'mtime': stat.st_mtime,
        'head_lines': first,
        'job_id': job_id,
        'job_name': job_name,
    }


@app.get("/logs/{logname}")
async def get_log_tail(logname: str, lines: int = 200):
    p = LOGDIR / logname
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Log not found")
    try:
        text = tail_lines(p, lines)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Log not found")
    return JSONResponse({"name": p.name, "lines": lines, "content": text})


@app.get("/pids")
async def list_pids():
    ensure_dir(PIDDIR)
    out = []
    for p in sorted(PIDDIR.glob("*.pid")):
        try:
            content = p.read_text(errors="replace").strip()
        except Exception:
            content = None
        out.append({"name": p.name, "content": content})
    return out


@app.get("/ready")
async def ready():
    return {"ready": READY_FILE.exists(), "ready_path": str(READY_FILE)}


# Optional: simple search for a substring within raw dump (first N bytes)
@app.get("/raw/search")
async def raw_search(q: str, limit_bytes: int = 65536):
    p = LOGDIR / "console_bridge-raw.bin"
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="raw dump not found")
    try:
        with p.open("rb") as f:
            data = f.read(limit_bytes)
    except Exception:
        raise HTTPException(status_code=500, detail="failed reading raw dump")
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = data.decode("latin-1", errors="replace")
    found = q in text
    return {"query": q, "found": found}


if __name__ == "__main__":
    import uvicorn
    # When running this file directly, pass the app object to uvicorn.run()
    # so uvicorn doesn't attempt to import a package named 'bridge' when the
    # PYTHONPATH or package layout differs.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("API_PORT", "8000")), log_level="info")
