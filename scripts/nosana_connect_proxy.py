"""HTTP CONNECT helper used as ssh ProxyCommand for the Nosana node (replaces socat)."""
import socket, sys, threading, os
host, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection(("node.k8s.prd.nos.ci", 5002), timeout=20)
s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
buf = b""
while b"\r\n\r\n" not in buf:
    c = s.recv(1)
    if not c: sys.exit("proxy closed")
    buf += c
if b" 200" not in buf.split(b"\r\n")[0]: sys.exit("proxy refused: " + buf.decode(errors="replace"))
s.settimeout(None)
def up():
    while True:
        d = os.read(0, 65536)
        if not d: break
        s.sendall(d)
    s.shutdown(socket.SHUT_WR)
threading.Thread(target=up, daemon=True).start()
while True:
    d = s.recv(65536)
    if not d: break
    os.write(1, d)
