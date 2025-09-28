import socket, time, datetime, os

HOST = "127.0.0.1"   # Hercules escucha aquí
PORT = 5000
OUTDIR = "spool_out"
os.makedirs(OUTDIR, exist_ok=True)


class JobLogExtractor:
    """Extrae bloques de JOB LOG identificados por la línea
    'J E S 2   J O B   L O G' y los escribe en ficheros separados.

    Funciona en modo streaming: se le pasan trozos de bytes con feed(),
    y él detecta el inicio (línea con el patrón) y escribe todo lo que
    venga debajo hasta que aparece otro patrón o se cierra la conexión.
    """

    PATTERN = b"J E S 2   J O B   L O G"

    def __init__(self, outdir=OUTDIR):
        self.buf = bytearray()
        self.recording = False
        self.current_f = None
        self.counter = 0
        self.outdir = outdir

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
                # Buscar patrón
                idx = self.buf.find(self.PATTERN)
                if idx == -1:
                    # No hay patrón: descartamos prefijos largos (evitar memoria infinita)
                    max_keep = len(self.PATTERN) + 200
                    if len(self.buf) > max_keep:
                        # conservar solo la cola necesaria para detectar un patrón partido
                        self.buf = self.buf[-max_keep:]
                    break
                # Encontrado: necesitamos identificar fin de línea y comenzar a grabar
                # Buscar el fin de línea (LF). Si no está completo, esperar más datos.
                after = idx + len(self.PATTERN)
                eol_idx = None
                for nl in (b"\r\n", b"\n", b"\r"):
                    pos = self.buf.find(nl, after)
                    if pos != -1:
                        eol_idx = pos + len(nl)
                        break
                if eol_idx is None:
                    # No hay fin de línea aún: esperar más datos
                    # Pero si el patrón está al final y no hay contenido debajo aún, salir
                    # Mantener buffer tal cual
                    break
                # Crear archivo para este JOB LOG
                path = self._new_file_path()
                self.current_f = open(path, "wb")
                self.recording = True
                # Escribir cualquier contenido que ya esté después de la línea del patrón
                if eol_idx < len(self.buf):
                    self.current_f.write(self.buf[eol_idx:])
                    # limpiar el buffer completo porque ya todo lo que había quedó escrito
                    self.buf = bytearray()
                else:
                    # No hay contenido después aún
                    self.buf = bytearray()
                # continue para poder detectar inmediatamente si otro patrón aparece más adelante
                continue
            else:
                # Ya estamos grabando: comprobar si aparece un nuevo patrón en el buffer
                idx = self.buf.find(self.PATTERN)
                if idx == -1:
                    # No hay patrón: todo el buffer pertenece al bloque actual
                    if self.buf:
                        self.current_f.write(self.buf)
                        self.buf = bytearray()
                    break
                else:
                    # Nuevo patrón encontrado: escribir hasta antes del patrón, cerrar archivo
                    if idx > 0:
                        self.current_f.write(self.buf[:idx])
                    self.current_f.close()
                    self.current_f = None
                    self.recording = False
                    # dejamos en el buffer el resto desde el patrón para procesarlo en la siguiente iteración
                    self.buf = self.buf[idx:]
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


def recv_one_spool():
    s = socket.socket()
    s.connect((HOST, PORT))  # Cliente conecta al listener de Hercules
    # Archivo con el spool completo (por compatibilidad)
    fname = datetime.datetime.now().strftime(f"{OUTDIR}/spool_%Y%m%d.bin")
    print(f"[INFO] Conectado, guardando spool completo en {fname}")

    extractor = JobLogExtractor(outdir=OUTDIR)

    try:
        with open(fname, "wb") as f:
            while True:
                data = s.recv(65536)
                if not data:  # Hercules cierra al terminar un spool
                    break
                # Guardar spool completo
                f.write(data)
                # Alimentar extractor para que cree archivos por cada JOB LOG
                try:
                    extractor.feed(data)
                except Exception as e:
                    # No queremos que un fallo en el extractor detenga el guardado del spool
                    print(f"[ERROR] extractor: {e}")
    finally:
        # cerrar extractor para volcar cualquier resto pendiente
        extractor.close()
        s.close()
    print(f"[OK] Spool recibido y guardado en {fname}")


print(f"Conectando a Hercules en {HOST}:{PORT} ...")
while True:
    try:
        recv_one_spool()
        time.sleep(0.2)  # espera breve antes del próximo spool
    except ConnectionRefusedError:
        time.sleep(0.5)  # no hay spool aún, reintenta
    except Exception as e:
        print("[WARN]", e)
        time.sleep(1)
