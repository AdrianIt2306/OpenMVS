# console_watch.py
import socket, time, re, datetime, os, logging
from logging.handlers import RotatingFileHandler

HOST, PORT = "127.0.0.1", 5002
re_submit = re.compile(r"\$HASP100\s+(\S+)\s+JOB\s+\((JOB\d+)\)\s+SUBMITTED")
re_ended  = re.compile(r"\$HASP395\s+(\S+)\s+ENDED")

# Logger setup: console + rotating file
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "console_watch.log")

# PID directory (centralized)
PIDDIR = os.environ.get("BRIDGE_PIDDIR", "/app/pids")
os.makedirs(PIDDIR, exist_ok=True)
PID_FILE = os.path.join(PIDDIR, "console_watch.pid")

logger = logging.getLogger("console_watch")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)

    def write_pid():
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            logger.exception("Failed to write pid file %s", PID_FILE)

    def remove_pid():
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except Exception:
            logger.exception("Failed to remove pid file %s", PID_FILE)

def iter_lines(sock):
    buf = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            # al cerrar la conexión, si queda fragmento, devolverlo como última línea
            if buf:
                yield buf.decode("ascii", "ignore").rstrip("\r\n")
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("ascii", "ignore").rstrip("\r")

def run_watch_loop():
    logger.info("[watch] conectando a %s:%s ...", HOST, PORT)
    while True:
        try:
            s = socket.socket()
            s.connect((HOST, PORT))
            logger.info("conectado a %s:%s", HOST, PORT)
            for line in iter_lines(s):
                # DEBUG: ver todo lo que emite la hardcopy
                logger.info("[CONS] %s", line)
                if m := re_submit.search(line):
                    jobname, jobid = m.group(1), m.group(2)
                    logger.info("SUBMITTED %s -> %s", jobname, jobid)
                if m := re_ended.search(line):
                    jobname = m.group(1)
                    logger.info("ENDED %s", jobname)
            s.close()
            logger.info("[watch] EOF (cerró 030E). Reintentando...")
            time.sleep(0.3)
        except ConnectionRefusedError:
            logger.warning("[watch] REFUSED (030E no está escuchando). Reintentando...")
            time.sleep(0.7)
        except OSError as e:
            logger.warning("[watch] WARN: %s. Reintentando...", e)
            time.sleep(1.0)

if __name__ == '__main__':
    write_pid()
    try:
        run_watch_loop()
    finally:
        remove_pid()
