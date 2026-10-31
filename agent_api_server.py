"""Local OpenAI-compatible stand-in LLM API backed by Claude Code headless mode.

Used only while the Nosana endpoint is down. llm_client tries Nosana first and
falls back here (FALLBACK_BASE_URL=http://127.0.0.1:8787/v1).

Run:  nohup .venv/bin/python agent_api_server.py > agent_api.log 2>&1 & disown
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

HOST = os.getenv("AGENT_API_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT_API_PORT", "8787"))
CLAUDE_BIN = os.getenv("CLAUDE_BIN") or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
TIMEOUT_S = int(os.getenv("AGENT_API_TIMEOUT", "150"))

# request model name -> claude CLI model alias
MODEL_MAP = {
    "agent-forward": "haiku",    # plays the small in-house model
    "agent-backward": "sonnet",  # plays the optimizer model
}
DEFAULT_MODEL = "agent-forward"

# empty working dir so the CLI never picks up project context (CLAUDE.md etc.)
WORK_DIR = tempfile.mkdtemp(prefix="agent_api_cwd_")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _build_prompt(messages: List[Dict[str, Any]], json_mode: bool) -> (str, str):
    system_parts, convo = [], []
    for m in messages:
        role = m.get("role", "user")
        text = _content_to_text(m.get("content"))
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            convo.append(f"[assistant]\n{text}")
        else:
            convo.append(text if len(messages) <= 2 else f"[user]\n{text}")
    prompt = "\n\n".join(convo).strip()
    if json_mode:
        prompt += "\n\nReturn ONLY a JSON object."
    system = "\n\n".join(system_parts).strip() or "You are a helpful assistant."
    return system, prompt


def _call_claude(model_alias: str, system: str, prompt: str) -> Dict[str, Any]:
    cmd = [
        CLAUDE_BIN, "-p",
        "--model", model_alias,
        "--output-format", "json",
        "--system-prompt", system,
        "--tools", "",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        timeout=TIMEOUT_S, cwd=WORK_DIR,
    )
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except Exception:
        raise RuntimeError(f"claude CLI returned non-JSON (exit {proc.returncode}): {(out or proc.stderr)[-300:]}")
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        raise RuntimeError(f"claude CLI error: {str(data.get('result', data))[:300]}")
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentAPI/0.1"

    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send(200, {"object": "list", "data": [
                {"id": k, "object": "model", "owned_by": f"claude-{v}"} for k, v in MODEL_MAP.items()]})
        elif self.path.rstrip("/") in ("", "/health", "/v1"):
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return
        t0 = time.time()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
            model = req.get("model") or DEFAULT_MODEL
            alias = MODEL_MAP.get(model, "haiku" if "forward" in model else "sonnet")
            json_mode = (req.get("response_format") or {}).get("type") == "json_object"
            system, prompt = _build_prompt(req.get("messages") or [], json_mode)
            data = _call_claude(alias, system, prompt)
            text = data.get("result", "") or ""
            usage = data.get("usage") or {}
            in_tok = int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
            out_tok = int(usage.get("output_tokens", 0))
            resp = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": f"{model} (claude-{alias})",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok, "total_tokens": in_tok + out_tok},
            }
            self._send(200, resp)
            _log(f"OK model={model}->{alias} json={json_mode} prompt_chars={len(prompt)} out_tokens={out_tok} {time.time()-t0:.1f}s")
        except subprocess.TimeoutExpired:
            _log(f"TIMEOUT after {time.time()-t0:.1f}s")
            self._send(504, {"error": {"message": f"agent backend timed out after {TIMEOUT_S}s", "type": "timeout"}})
        except Exception as e:  # noqa: BLE001
            _log(f"ERROR {type(e).__name__}: {str(e)[:200]} {time.time()-t0:.1f}s")
            self._send(500, {"error": {"message": f"{type(e).__name__}: {str(e)[:300]}", "type": "api_error"}})


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    _log(f"Agent API stand-in listening on http://{HOST}:{PORT}/v1 (models: {MODEL_MAP})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
