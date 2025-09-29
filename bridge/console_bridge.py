import socket, time, datetime, os, logging, re
from logging.handlers import RotatingFileHandler

HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")   # Hercules escucha aquí
PORT = int(os.environ.get("BRIDGE_PORT", "5000"))
OUTDIR = os.environ.get("BRIDGE_OUTDIR", "/app/spool")
LOGDIR = os.environ.get("BRIDGE_LOGDIR", "/app/logs")
READY_FILE = os.environ.get("BRIDGE_READYFILE", "/app/pids/console_bridge.ready")
PIDDIR = os.environ.get("BRIDGE_PIDDIR", "/app/pids")
PID_FILE = os.environ.get("BRIDGE_PIDFILE", os.path.join(PIDDIR, "console_bridge.pid"))

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
    """
    Extrae bloques de JOB LOG usando expresiones regulares para los marcadores
    de inicio y fin. Extrae el JOBNAME del marcador de inicio y el JOBID del
    cuerpo del log para nombrar los ficheros de salida.
    """

    # Expresiones regulares para detectar inicio y fin de un job
    # Manejan espacios variables (\\s+)
    START_PATTERN_RE = re.compile(b"\\*\\*\\*\\*A\\s+START\\s+JOB\\s+\\d+\\s+([A-Z0-9#@$]+)")
    END_PATTERN_RE = re.compile(b"\\*\\*\\*\\*A\\s+END")
    # Expresión regular para extraer el JOBID de líneas como 'JES2.JOB00001...'
    JOBID_PATTERN_RE = re.compile(b"JES2\\.(JOB\\d+)")

    def __init__(self, outdir=OUTDIR):
        self.buf = bytearray()
        self.recording = False
        self.current_f = None
        self.outdir = outdir
        
        # Variables para almacenar la información del job actual
        self.jobname = None
        self.jobid = None

    def _reset_state(self):
        """Resetea el estado para el siguiente job."""
        if self.current_f:
            try:
                self.current_f.close()
            except IOError as e:
                logger.error("Error closing joblog file: %s", e)
        
        self.recording = False
        self.current_f = None
        self.jobname = None
        self.jobid = None

    def feed(self, chunk: bytes):
        """Alimenta el extractor con nuevos bytes del stream."""
        if not chunk:
            return
        self.buf.extend(chunk)

        while True:
            if not self.recording:
                match = self.START_PATTERN_RE.search(self.buf)
                if not match:
                    # Si no hay inicio, descartamos el buffer para no consumir memoria infinita
                    # Dejamos una pequeña cola por si el patrón llega cortado
                    if len(self.buf) > 200:
                        self.buf = self.buf[-200:]
                    break
                
                # Encontramos un inicio de job
                self.recording = True
                # Capturamos el Job Name del grupo 1 de la regex
                self.jobname = match.group(1).decode('ascii', errors='ignore')
                logger.info("Detected START for jobname: %s", self.jobname)

                # Eliminamos del buffer todo hasta el final del match
                self.buf = self.buf[match.end():]
                # Continuamos el bucle para procesar el resto del buffer
                continue

            else: # Estamos grabando (self.recording es True)
                # Primero, buscamos el JOBID si aún no lo tenemos
                if self.jobname and not self.jobid:
                    id_match = self.JOBID_PATTERN_RE.search(self.buf)
                    if id_match:
                        # JOBID encontrado
                        self.jobid = id_match.group(1).decode('ascii', errors='ignore')
                        logger.info("Extracted JOBID: %s for jobname: %s", self.jobid, self.jobname)
                        
                        # Ahora que tenemos ambos, creamos el archivo
                        filename = f"{self.jobid}-{self.jobname}.txt"
                        path = os.path.join(self.outdir, filename)
                        
                        try:
                            self.current_f = open(path, "wb")
                            logger.info("Creating spool file: %s", path)
                            # Escribimos lo que teníamos en el buffer hasta ahora
                            self.current_f.write(self.buf)
                            self.buf = bytearray()
                        except IOError as e:
                            logger.error("Failed to create joblog file %s: %s", path, e)
                            self._reset_state() # Resetear si falla la creación del archivo
                        
                # Si ya tenemos un archivo abierto, comprobamos si llega el final del job
                end_match = self.END_PATTERN_RE.search(self.buf)
                if not end_match:
                    # Aún no hay fin. Si el archivo ya está abierto, escribimos el buffer.
                    if self.current_f:
                        self.current_f.write(self.buf)
                        self.buf = bytearray()
                    break # Salimos y esperamos más datos
                else:
                    # Encontramos el final del job
                    logger.info("Detected END for job: %s-%s", self.jobid, self.jobname)
                    # Escribimos los datos hasta justo antes del marcador de fin
                    if self.current_f:
                        self.current_f.write(self.buf[:end_match.start()])
                    
                    # Guardamos el resto del buffer para la siguiente iteración
                    self.buf = self.buf[end_match.end():]
                    
                    # Reseteamos estado para el próximo job
                    self._reset_state()
                    # Volvemos a empezar el bucle por si hay otro job en el buffer
                    continue

    def close(self):
        """Cierra cualquier fichero pendiente al finalizar la conexión."""
        logger.info("Connection closed. Closing any pending job files.")
        if self.recording and self.current_f:
            # Si quedaba algo en el buffer, lo escribimos
            if self.buf:
                self.current_f.write(self.buf)
            self.buf = bytearray()
        self._reset_state()


def recv_one_spool():
    s = socket.socket()
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
                data = s.recv(65536)
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
