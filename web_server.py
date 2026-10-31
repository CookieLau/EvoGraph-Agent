"""EvoGraph-Quant web dashboard server (stdlib only, replaces the Streamlit app).

    .venv/bin/python web_server.py            # http://localhost:8600

Routes
  GET  /                      web/index.html
  GET  /api/config            which providers are configured (no secret values)
  GET  /api/replays           replay/*.json names, golden first
  GET  /api/replay?name=...   recorded event list
  GET  /api/payload           the dirty confidential feed (shown quarantined in the UI)
  GET  /api/run?mode=live|scripted&epochs=3   Server-Sent Events from orchestrator.run_evolution()
  POST /api/fanout            Daytona best-of-K fan-out (6 offline sandboxes)
  POST /api/reset             clear the Neo4j demo labels
"""
from __future__ import annotations

import glob
import importlib.util
import json
import mimetypes
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPLAY_DIR = os.path.join(HERE, "replay")
REPLAY_PRIORITY = ["golden_run.json", "scripted_run.json", "mock_run.json"]
STATIC = {"/brand/": os.path.join(HERE, "brand"), "/assets/": os.path.join(HERE, "assets"), "/web/": os.path.join(HERE, "web")}
SCRIPTED_NOTE = "LLM steps pre-authored; Daytona and Neo4j executed live"
FANOUT_SCRIPTS = ["reference/naive_etl.py", "reference/partial_etl.py", "reference/robust_etl.py"]

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(HERE, ".env"))
except Exception:
    pass


def list_replays():
    files = [os.path.basename(p) for p in glob.glob(os.path.join(REPLAY_DIR, "*.json"))]
    ordered = [f for f in REPLAY_PRIORITY if f in files]
    return ordered + sorted([f for f in files if f not in ordered], reverse=True)


def load_scripted_engine():
    spec = importlib.util.spec_from_file_location("evograph_e2e_scripted", os.path.join(HERE, "scripts", "e2e_scripted.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.ScriptedEngine()


def enrich(ev: dict, engine) -> dict:
    """Attach the endpoint actually used (Nosana vs labelled fallback) so the UI stays honest."""
    meta = None
    if ev.get("type") == "forward":
        meta = getattr(engine, "last_forward_meta", None)
    elif ev.get("type") == "gradient":
        meta = getattr(engine, "last_backward_meta", None)
    if isinstance(meta, dict):
        for k in ("display", "fallback_used", "provider", "endpoint"):
            if meta.get(k) is not None and k not in ev:
                ev[k] = meta.get(k)
    return ev


class Handler(BaseHTTPRequestHandler):
    server_version = "EvoGraph/1.0"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: str):
        if not os.path.isfile(path):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(HERE, "web", "index.html"))
            for prefix, root in STATIC.items():
                if p.startswith(prefix):
                    full = os.path.realpath(os.path.join(root, p[len(prefix):]))
                    if not full.startswith(os.path.realpath(root)):
                        return self._send(403, b"forbidden", "text/plain")
                    return self._file(full)
            if p == "/api/config":
                fm = os.getenv("FORWARD_MODEL") or ""
                return self._json({
                    "daytona": bool(os.getenv("DAYTONA_API_KEY")) and os.getenv("USE_LOCAL_FALLBACK", "false").lower() != "true",
                    "forward_model": fm if fm.upper() != "TBD" else "",
                    "backward_model": os.getenv("BACKWARD_MODEL") or "",
                    "neo4j_host": (os.getenv("NEO4J_URI", "") or "").split("//")[-1][:40],
                    "fanout": os.path.exists(os.path.join(HERE, "parallel_sandbox.py")),
                })
            if p == "/api/replays":
                return self._json(list_replays())
            if p == "/api/replay":
                name = os.path.basename(q.get("name", ""))
                with open(os.path.join(REPLAY_DIR, name), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = data.get("events", [])
                return self._json(data)
            if p == "/api/payload":
                from dirty_payload import CONFIDENTIAL_PORTFOLIO_DATA, get_raw_payload_str

                return self._json({"raw": get_raw_payload_str(), "nodes": CONFIDENTIAL_PORTFOLIO_DATA["nodes"],
                                   "edges": CONFIDENTIAL_PORTFOLIO_DATA["edges"]})
            if p == "/api/run":
                return self._run_sse(q.get("mode", "scripted"), int(q.get("epochs", "3") or 3))
            return self._send(404, b"not found", "text/plain")
        except Exception as ex:  # noqa
            return self._json({"error": "%s: %s" % (type(ex).__name__, ex)}, 500)

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/fanout":
                import parallel_sandbox as ps
                from dirty_payload import get_raw_payload_str

                codes, labels = [], []
                for rel in FANOUT_SCRIPTS:
                    with open(os.path.join(HERE, rel), "r", encoding="utf-8") as f:
                        code = f.read()
                    for _ in range(2):
                        codes.append(code)
                        labels.append(os.path.basename(rel).replace("_etl.py", ""))
                fo = ps.run_best_of_k(codes, get_raw_payload_str())
                fo["labels"] = labels
                for r in fo.get("results") or []:
                    r.pop("cleaned", None)
                    r["output"] = (r.get("output") or "")[-600:]
                return self._json(fo)
            if p == "/api/reset":
                from graph_store import GraphStore

                GraphStore().reset_demo()
                return self._json({"ok": True})
            return self._send(404, b"not found", "text/plain")
        except Exception as ex:  # noqa
            return self._json({"error": "%s: %s" % (type(ex).__name__, ex)}, 500)

    # ------------------------------------------------------------------ SSE
    def _run_sse(self, mode: str, epochs: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        events = []

        def emit(ev: dict):
            events.append(ev)
            self.wfile.write(("data: %s\n\n" % json.dumps(ev, ensure_ascii=False, default=str)).encode("utf-8"))
            self.wfile.flush()

        try:
            from orchestrator import run_evolution

            if mode == "live":
                from evo_engine import EvoGraphEngine

                engine = EvoGraphEngine()
            else:
                engine = load_scripted_engine()
            try:
                from graph_store import GraphStore

                GraphStore().reset_demo()
            except Exception as ex:  # noqa
                emit({"type": "error", "stage": "neo4j", "message": "reset_demo failed: %s" % ex})
            for ev in run_evolution(max_epochs=max(2, min(epochs, 4)), use_neo4j=True, engine=engine):
                if ev.get("type") == "start" and mode != "live":
                    ev.setdefault("scripted", True)
                    ev.setdefault("note", SCRIPTED_NOTE)
                emit(enrich(ev, engine))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as ex:  # noqa
            try:
                emit({"type": "error", "stage": "orchestrator", "message": "%s: %s" % (type(ex).__name__, ex),
                      "traceback": traceback.format_exc()[-1500:]})
                emit({"type": "done", "success": False, "epochs": 0})
            except Exception:
                return
        finally:
            if events:
                try:
                    os.makedirs(REPLAY_DIR, exist_ok=True)
                    with open(os.path.join(REPLAY_DIR, "live_%s.json" % time.strftime("%H%M%S")), "w") as f:
                        json.dump(events, f, indent=2, ensure_ascii=False, default=str)
                except Exception:
                    pass


def main():
    port = int(os.getenv("WEB_PORT", "8600"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("EvoGraph-Quant dashboard on http://localhost:%d" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
