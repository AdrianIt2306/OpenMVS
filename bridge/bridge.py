import socket

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 5000))
srv.listen(1)
print("Esperando conexión de Hercules en 127.0.0.1:5000...")
conn, addr = srv.accept()
print("Conectado desde:", addr)

with open("salida_1403.txt", "wb") as f:
    while True:
        data = conn.recv(4096)
        if not data:
            break
        f.write(data)

conn.close()
srv.close()
print("Listo: salida escrita en salida_1403.txt")