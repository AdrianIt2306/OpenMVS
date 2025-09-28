import socket, time, datetime, os

HOST = "127.0.0.1"   # Hercules escucha aquí
PORT = 5000
OUTDIR = "spool_out"
os.makedirs(OUTDIR, exist_ok=True)

def recv_one_spool():
    s = socket.socket()
    s.connect((HOST, PORT))  # Cliente conecta al listener de Hercules
    fname = datetime.datetime.now().strftime(f"{OUTDIR}/spool_%Y%m%d.txt")
    with open(fname, "wb") as f:
        while True:
            data = s.recv(4096)
            if not data:  # Hercules cierra al terminar un spool
                break
            f.write(data)
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
