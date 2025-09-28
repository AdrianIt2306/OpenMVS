import socket, time, datetime, os, logging
from logging.handlers import RotatingFileHandler

HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")   # Hercules escucha aquí
PORT = int(os.environ.get("BRIDGE_PORT", "5000"))
OUTDIR = os.environ.get("BRIDGE_OUTDIR", "/app/spool")
LOGDIR = os.environ.get("BRIDGE_LOGDIR", "/app/logs")
READY_FILE = os.environ.get("BRIDGE_READYFILE", "/app/pids/console_bridge.ready")
PIDDIR = os.environ.get("BRIDGE_PIDDIR", "/app/pids")
PID_FILE = os.environ.get("BRIDGE_PIDFILE", os.path.join(PIDDIR, "console_bridge.pid"))
# Configurable recv size (bytes) for socket; defaults to 64KiB
RECV_SIZE = int(os.environ.get("BRIDGE_RECV_SIZE", "131072"))
# When possible, set kernel socket receive buffer to twice the application buffer
RECV_SOCKBUF = int(os.environ.get("BRIDGE_SO_RCVBUF", str(RECV_SIZE * 2)))

os.makedirs(PIDDIR, exist_ok=True)

os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(LOGDIR, exist_ok=True)

# logger
logger = logging.getLogger("console_bridge")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(os.path.join(LOGDIR, "console_bridge.log"), maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)

def write_pid():
    try:
        # Ensure pid directory exists (in case start.sh didn't create it)
        pd = os.path.dirname(PID_FILE)
        if pd and not os.path.isdir(pd):
            os.makedirs(pd, exist_ok=True)
        with open(PID_FILE, "w") as pf:
            pf.write(str(os.getpid()))
    except Exception:
        logger.exception("Failed to write pid file %s", PID_FILE)

def write_ready():
    try:
        with open(READY_FILE, "w") as f:
            f.write(datetime.datetime.utcnow().isoformat() + "Z\n")
    except Exception:
        logger.exception("Failed to write ready file %s", READY_FILE)


class JobLogExtractor:
    """Extrae bloques de JOB LOG identificados por la línea
    'J E S 2   J O B   L O G' y los escribe en ficheros separados.

    Funciona en modo streaming: se le pasan trozos de bytes con feed(),
    y él detecta el inicio (línea con el patrón) y escribe todo lo que
    venga debajo hasta que aparece otro patrón o se cierra la conexión.
    """

    # New protocol markers (start/end) as requested
    START_PATTERN = b"****A  START"
    END_PATTERN = b"****A   END"
    START_PATTERN_STR = "****A  START"
    END_PATTERN_STR = "****A   END"

    def __init__(self, outdir=OUTDIR):
        self.buf = bytearray()
        self.recording = False
        self.current_f = None
        self.counter = 0
        self.outdir = outdir
        # text-mode buffers for decoded streams (EBCDIC -> UTF-8)
        self.text_buf = ""
        self.text_recording = False
        self.text_current_f = None

    def _new_file_path(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.counter += 1
        return os.path.join(self.outdir, f"joblog_{ts}_{self.counter:03d}.txt")

    def feed(self, chunk: bytes):
        """Feed new bytes; writes to current file(s) when appropriate."""
        if not chunk:
            return
        self.buf.extend(chunk)

        # Procesado en bucle para manejar múltiples apariciones en el buffer
        while True:
            if not self.recording:
                # Buscar START pattern en bytes
                idx = self.buf.find(self.START_PATTERN)
                if idx == -1:
                    # No hay START: descartamos prefijos largos (evitar memoria infinita)
                    max_keep = len(self.START_PATTERN) + 200
                    if len(self.buf) > max_keep:
                        # conservar solo la cola necesaria para detectar un patrón partido
                        self.buf = self.buf[-max_keep:]
                    break
                # Encontrado START: necesitamos identificar fin de la línea del START y comenzar a grabar
                after = idx + len(self.START_PATTERN)
                eol_idx = None
                for nl in (b"\r\n", b"\n", b"\r"):
                    pos = self.buf.find(nl, after)
                    if pos != -1:
                        eol_idx = pos + len(nl)
                        break
                if eol_idx is None:
                    # No hay fin de línea aún: esperar más datos
                    break
                # Crear archivo para este JOB LOG
                path = self._new_file_path()
                self.current_f = open(path, "wb")
                self.recording = True
                # Escribir cualquier contenido que ya esté después de la línea del START
                if eol_idx < len(self.buf):
                    self.current_f.write(self.buf[eol_idx:])
                    # limpiar el buffer completo porque ya todo lo que había quedó escrito
                    self.buf = bytearray()
                else:
                    # No hay contenido después aún
                    self.buf = bytearray()
                continue
            else:
                # Ya estamos grabando: comprobar si aparece el END_PATTERN en el buffer
                idx = self.buf.find(self.END_PATTERN)
                if idx == -1:
                    # No hay END: todo el buffer pertenece al bloque actual
                    if self.buf:
                        self.current_f.write(self.buf)
                        self.buf = bytearray()
                    break
                else:
                    # END encontrado: escribir hasta antes del END, cerrar archivo
                    if idx > 0:
                        self.current_f.write(self.buf[:idx])
                    self.current_f.close()
                    self.current_f = None
                    self.recording = False
                    # dejamos en el buffer el resto desde el END (posible siguiente START)
                    self.buf = self.buf[idx + len(self.END_PATTERN):]
                    # continue para detectar inmediatamente el siguiente inicio
                    continue

    def close(self):
        # Llamar al cierre de conexión: volcar buffer pendiente y cerrar fichero si estaba grabando
        if self.recording and self.current_f:
            if self.buf:
                self.current_f.write(self.buf)
            self.current_f.close()
            self.current_f = None
            self.recording = False
            self.buf = bytearray()
        # cerrar cualquier archivo de texto pendiente
        if self.text_recording and self.text_current_f:
            if self.text_buf:
                try:
                    self.text_current_f.write(self.text_buf)
                except Exception:
                    pass
            try:
                self.text_current_f.close()
            except Exception:
                pass
            self.text_current_f = None
            self.text_recording = False
            self.text_buf = ""

    def feed_text(self, text: str):
        """Feed decoded textual data (UTF-8 string). This finds the textual PATTERN and
        writes joblog files as UTF-8 text."""
        if not text:
            return
        self.text_buf += text
        while True:
            if not self.text_recording:
                idx = self.text_buf.find(self.START_PATTERN_STR)
                if idx == -1:
                    # keep only tail
                    max_keep = len(self.START_PATTERN_STR) + 500
                    if len(self.text_buf) > max_keep:
                        self.text_buf = self.text_buf[-max_keep:]
                    break
                # found pattern: look for end of line
                after = idx + len(self.START_PATTERN_STR)
                eol_idx = None
                for nl in ("\r\n", "\n", "\r"):
                    pos = self.text_buf.find(nl, after)
                    if pos != -1:
                        eol_idx = pos + len(nl)
                        break
                if eol_idx is None:
                    break
                # create file
                path = self._new_file_path()
                try:
                    self.text_current_f = open(path, "w", encoding="utf-8", errors="replace")
                except Exception:
                    self.text_current_f = None
                self.text_recording = True
                # write any content after the START line
                if eol_idx < len(self.text_buf):
                    if self.text_current_f:
                        try:
                            self.text_current_f.write(self.text_buf[eol_idx:])
                        except Exception:
                            pass
                    self.text_buf = ""
                else:
                    self.text_buf = ""
                continue
            else:
                # already recording: check for END marker to finish this object
                idx = self.text_buf.find(self.END_PATTERN_STR)
                if idx == -1:
                    # No END yet: write whole buffer to current file
                    if self.text_buf and self.text_current_f:
                        try:
                            self.text_current_f.write(self.text_buf)
                        except Exception:
                            pass
                        self.text_buf = ""
                    break
                else:
                    # END found: write up to it, close file, and keep remainder
                    if idx > 0 and self.text_current_f:
                        try:
                            self.text_current_f.write(self.text_buf[:idx])
                        except Exception:
                            pass
                    if self.text_current_f:
                        try:
                            self.text_current_f.close()
                        except Exception:
                            pass
                    self.text_current_f = None
                    self.text_recording = False
                    # leave remainder after the END marker for further processing
                    self.text_buf = self.text_buf[idx + len(self.END_PATTERN_STR):]
                    # continue loop to detect next START in the remainder
                    continue


def recv_one_spool():
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_SOCKBUF)
        logger.debug("Set socket SO_RCVBUF=%d", RECV_SOCKBUF)
    except Exception:
        logger.debug("Failed to set SO_RCVBUF, continuing with defaults")
    logger.info("Attempting connect to %s:%s", HOST, PORT)
    s.connect((HOST, PORT))  # Cliente conecta al listener de Hercules
    # Archivo con el spool completo (por compatibilidad)
    fname = datetime.datetime.now().strftime(f"{OUTDIR}/spool_%Y%m%d.bin")
    logger.info("Conectado; guardando spool completo en %s", fname)

    extractor = JobLogExtractor(outdir=OUTDIR)

    ready_written = False

    try:
        raw_path = os.path.join(LOGDIR, "console_bridge-raw.bin")
        with open(fname, "wb") as f, open(raw_path, "ab") as rawf:
            chunk_no = 0
            bytes_written = 0
            while True:
                data = s.recv(RECV_SIZE)
                if not data:  # Hercules cierra al terminar un spool
                    logger.info("recv returned 0 bytes (connection closed)")
                    break
                chunk_no += 1
                logger.info("Received chunk %d: %d bytes", chunk_no, len(data))
                # write ready file on first real data
                if not ready_written and len(data) > 0:
                    write_ready()
                    ready_written = True
                # Guardar spool completo
                f.write(data)
                try:
                    f.flush()
                except Exception:
                    logger.debug("Failed to flush spool file")
                bytes_written += len(data)
                # Also append raw bytes to a dedicated debug file
                try:
                    rawf.write(data)
                    rawf.flush()
                except Exception:
                    logger.exception("failed to write raw dump")
                # Alimentar extractor para que cree archivos por cada JOB LOG
                try:
                    # First try binary search (ASCII-compatible)
                    extractor.feed(data)
                except Exception:
                    logger.exception("extractor error (binary)")
                # If no joblog files were produced in binary mode, try decoding as EBCDIC
                try:
                    # cheap check: if the textual pattern exists when decoding with common EBCDIC codepages
                    for enc in ("cp037", "cp1047", "cp500"):
                        try:
                            decoded = data.decode(enc, errors="ignore")
                        except Exception:
                            decoded = None
                        if decoded and extractor.START_PATTERN_STR in decoded:
                            try:
                                extractor.feed_text(decoded)
                            except Exception:
                                logger.exception("extractor error (text)")
                            break
                except Exception:
                    logger.exception("extractor error (ebcdic attempts)")
            logger.info("Total bytes written to spool: %d", bytes_written)
            # if nothing was written, remove empty file
            if bytes_written == 0:
                try:
                    f.close()
                    os.remove(fname)
                    logger.info("Removed empty spool file %s", fname)
                except Exception:
                    logger.exception("Failed to remove empty spool %s", fname)
    finally:
        # cerrar extractor para volcar cualquier resto pendiente
        extractor.close()
        s.close()
    logger.info("Spool recibido y guardado en %s", fname)


def main():
    write_pid()
    logger.info("Starting console_bridge main loop connecting to %s:%s", HOST, PORT)
    while True:
        try:
            recv_one_spool()
            time.sleep(0.2)  # espera breve antes del próximo spool
        except ConnectionRefusedError:
            logger.debug("Connection refused; retrying in 0.5s")
            time.sleep(0.5)  # no hay spool aún, reintenta
        except Exception:
            logger.exception("Unhandled exception in main loop; sleeping 1s then retrying")
            time.sleep(1)

if __name__ == '__main__':
    main()
