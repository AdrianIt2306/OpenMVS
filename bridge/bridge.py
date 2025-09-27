import socket
import threading

HOST = "0.0.0.0"
PORT = 5000

def handle(conn, addr):
    try:
        data = b""
        # lee hasta \n o cierre de socket
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        msg = data.decode("utf-8", errors="ignore").strip()
        print(f"[bridge] <- {addr} : {msg}")

        # aquí harías la consulta real; por ahora solo eco “OK: …”
        reply = f"OK: {msg}\n"
        conn.sendall(reply.encode("utf-8"))
        print(f"[bridge] -> {addr} : {reply.strip()}")
    finally:
        conn.close()

def main():
        print(f"[bridge] listening on {HOST}:{PORT}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen(5)
            while True:
                conn, addr = s.accept()
                threading.Thread(target=handle, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()
