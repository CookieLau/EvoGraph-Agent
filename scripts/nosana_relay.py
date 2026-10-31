"""Local OpenAI-compatible relay to the Ollama running on our Nosana GPU node.

Why: the node's public Ollama (PID 1) can't read its read-only model mount, and the
node allows neither SSH exec nor port forwarding. So we keep one interactive SSH
session open, run a second Ollama inside the container on 127.0.0.1:11435, and pass
each HTTP request through the shell (base64 in, base64 out via bash /dev/tcp).

Run:  .venv/bin/python scripts/nosana_relay.py      (listens on 127.0.0.1:11435)
Then: LLM_BASE_URL=http://127.0.0.1:11435/v1
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LISTEN_PORT = int(os.getenv("RELAY_PORT", "11435"))
SSH_HOST = os.getenv(
    "NOSANA_SSH_HOST", "nosana@CP6fNnuJvjiu6scK6apNrbtWUaZg66H9RwTWoUn7jYAx-0-ssh.node.k8s.prd.nos.ci")
SSH_KEY = os.path.expanduser(os.getenv("NOSANA_SSH_KEY", "~/.ssh/id_ed25519"))
PROXY = os.path.join(HERE, "nosana_connect_proxy.py")
CHUNK = 900
MARK = re.compile(rb"@@BEGIN@@([A-Za-z0-9+/=]*)@@END@@")

# Remote helpers. Markers are split ("BEG""IN") so the shell's own echo never matches.
REMOTE_INIT = r"""
export PS1='' PS2=''; stty -echo; bind 'set enable-bracketed-paste off' 2>/dev/null
if ! OLLAMA_HOST=127.0.0.1:11435 ollama list </dev/null >/dev/null 2>&1; then mkdir -p /tmp/om; ln -sfn /root/.ollama/models/blobs /tmp/om/blobs; cp -rn /root/.ollama/models/manifests /tmp/om/; (OLLAMA_MODELS=/tmp/om OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NOPRUNE=1 OLLAMA_KEEP_ALIVE=3h setsid nohup ollama serve >/tmp/ollama2.log 2>&1 </dev/null &); sleep 6; fi
ohttp(){ exec 3<>/dev/tcp/127.0.0.1/11435 || { echo "@@BEG""IN@@@@E""ND@@"; return; }; if [ "$1" = GET ]; then printf 'GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n' "$2" >&3; else L=$(stat -c %s /tmp/r.body); { printf 'POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' "$2" "$L"; cat /tmp/r.body; } >&3; fi; R=$(base64 -w0 <&3); exec 3>&-; echo "@@BEG""IN@@${R}@@E""ND@@"; }
echo "@@BEG""IN@@cmVhZHk=@@E""ND@@"
"""


class NosanaShell:
    """One long-lived interactive SSH session; requests are serialized."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.buf = b""
        self.cond = threading.Condition()

    def _reader(self, proc):
        while True:
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                break
            with self.cond:
                self.buf += data
                self.cond.notify_all()

    def _start(self):
        cmd = ["ssh", "-S", "none", "-tt", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=25",
               "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=8",
               "-o", "ProxyCommand=%s %s %%h %%p" % (sys.executable, PROXY), "-i", SSH_KEY, SSH_HOST]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, bufsize=0)
        self.buf = b""
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        time.sleep(2.0)
        self._send(REMOTE_INIT)
        if self._wait(60) is None:
            raise RuntimeError("Nosana shell did not become ready")

    def _send(self, text):
        for line in text.strip("\n").split("\n"):
            self.proc.stdin.write(line.encode() + b"\n")
        self.proc.stdin.flush()

    def _wait(self, timeout):
        deadline = time.time() + timeout
        with self.cond:
            while True:
                m = MARK.search(self.buf)
                if m:
                    self.buf = self.buf[m.end():]
                    return base64.b64decode(m.group(1))
                left = deadline - time.time()
                if left <= 0 or (self.proc and self.proc.poll() is not None):
                    return None
                self.cond.wait(min(left, 1.0))

    def request(self, method, path, body=b"", timeout=300):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        self._start()
                    lines = []
                    if method == "POST":
                        b64 = base64.b64encode(body).decode()
                        lines.append(": > /tmp/r.b64")
                        for i in range(0, len(b64), CHUNK):
                            lines.append("printf %%s '%s' >> /tmp/r.b64" % b64[i:i + CHUNK])
                        lines.append("base64 -d /tmp/r.b64 > /tmp/r.body")
                    lines.append("ohttp %s '%s'" % (method, path))
                    self._send("\n".join(lines))
                    raw = self._wait(timeout)
                    if raw is None:
                        raise RuntimeError("timeout or session died")
                    return parse_http(raw)
                except Exception as e:  # restart the session once
                    print("[relay] attempt %d failed: %s" % (attempt, e), flush=True)
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
                    self.proc = None
            raise RuntimeError("Nosana relay failed twice")


def parse_http(raw):
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split()[1]) if lines and len(lines[0].split()) > 1 else 502
    headers = {}
    for h in lines[1:]:
        k, _, v = h.partition(b":")
        headers[k.strip().lower().decode()] = v.strip().decode()
    if "chunked" in headers.get("transfer-encoding", ""):
        out, rest = b"", body
        while rest:
            size_line, _, rest = rest.partition(b"\r\n")
            size = int(size_line.split(b";")[0] or b"0", 16)
            if size == 0:
                break
            out, rest = out + rest[:size], rest[size + 2:]
        body = out
    return status, body


SHELL = NosanaShell()


def openai_to_ollama(req):
    body = {"model": req.get("model"), "messages": req.get("messages", []), "stream": False, "think": False,
            "options": {}}
    if "temperature" in req:
        body["options"]["temperature"] = req["temperature"]
    if req.get("max_tokens"):
        body["options"]["num_predict"] = req["max_tokens"]
    if (req.get("response_format") or {}).get("type") == "json_object":
        body["format"] = "json"
    return body


def ollama_to_openai(res, model):
    content = (res.get("message") or {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    pt, ct = res.get("prompt_eval_count", 0), res.get("eval_count", 0)
    return {"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
            "model": res.get("model", model),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": res.get("done_reason", "stop")}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, status, obj):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            status, body = SHELL.request("GET", "/api/tags")
            if self.path.rstrip("/").endswith("/models"):
                tags = json.loads(body or b"{}").get("models", [])
                return self._reply(200, {"object": "list", "data": [
                    {"id": m["name"], "object": "model", "owned_by": "nosana"} for m in tags]})
            return self._reply(status, body)
        except Exception as e:
            return self._reply(502, {"error": {"message": str(e)}})

    def do_POST(self):
        t0 = time.time()
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        try:
            req = json.loads(raw or b"{}")
            if self.path.rstrip("/").endswith("/chat/completions"):
                status, body = SHELL.request("POST", "/api/chat", json.dumps(openai_to_ollama(req)).encode())
                if status != 200:
                    return self._reply(status, body)
                out = ollama_to_openai(json.loads(body), req.get("model"))
            else:  # native Ollama passthrough (/api/chat, /api/generate, ...)
                status, out = SHELL.request("POST", self.path, raw)
                return self._reply(status, out)
            print("[relay] %s %s %.1fs tokens=%s" % (self.path, req.get("model"), time.time() - t0,
                                                     out["usage"]["completion_tokens"]), flush=True)
            return self._reply(200, out)
        except Exception as e:
            return self._reply(502, {"error": {"message": "nosana relay: %s" % e}})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("[relay] connecting to Nosana node ...", flush=True)
    SHELL.request("GET", "/api/tags")
    print("[relay] ready on http://127.0.0.1:%d/v1" % LISTEN_PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
