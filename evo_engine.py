"""EvoGraph evolution engine: forward pass (prompt -> ETL code) and backward pass
(sandbox traceback -> textual gradient -> updated prompt), see CONTRACT.md."""
from __future__ import annotations

import ast
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from adalflow.optim.parameter import Parameter

try:  # prompts.py is written by another agent; fall back to built-ins if missing
    import prompts as _prompts
except Exception:  # pragma: no cover
    _prompts = None


# ---------------------------------------------------------------------------
# Default prompt text (only used if prompts.py is missing an attribute)
# ---------------------------------------------------------------------------
_DEFAULT_NAIVE_PROMPT = """You are a Python developer. Write a standalone Python script that loads the financial
supply-chain JSON from 'payload.json' (current directory) and prepares it for a Neo4j graph.
Requirements:
1. Parse `exposure_raw` directly with `float(node['exposure_raw'])`.
2. Parse edge `weight` directly with `float(edge['weight'])`.
3. Build Cypher strings with f-strings, e.g. f"CREATE (n:Entity {{id: '{{node['id']}}'}})".
4. Print one line `CLEANED_GRAPH_JSON: <single-line json>` with keys nodes[id,name,tier,exposure,currency,status]
   and edges[src,dst,rel,weight], then print `ASSERTION_PASSED_GRAPH_COMMITTED` as the last line.
Output ONLY raw Python code."""

_DEFAULT_FORWARD_USER_TEMPLATE = """Raw confidential payload (also available as payload.json in the working directory):
{payload}

Schema notes:
{schema_hint}

Write the standalone Python script now. Output ONLY Python code."""

_DEFAULT_BACKWARD_TEMPLATE = """You are the Backward Textual Gradient Engine (prompt optimizer).
A coding agent's script FAILED inside an isolated, offline Daytona sandbox.

[Current system prompt]
{prompt}

[Payload schema (no raw values)]
{schema_hint}

[Generated script]
{code}

[Sandbox output / traceback (loss signal)]
{feedback}

[Verifier failures]
{failures}

Return ONLY a JSON object with two keys:
"textual_gradient": concise root-cause diagnosis of why the script failed;
"updated_prompt": the full rewritten system prompt that fixes those root causes."""


def _p(name: str, default: str) -> str:
    val = getattr(_prompts, name, None) if _prompts is not None else None
    return val if isinstance(val, str) and val.strip() else default


def _naive_prompt() -> str:
    return _p("NAIVE_INITIAL_PROMPT", _DEFAULT_NAIVE_PROMPT)


NAIVE_INITIAL_PROMPT = _naive_prompt()

OUTPUT_MARKERS = ("CLEANED_GRAPH_JSON", "ASSERTION_PASSED_GRAPH_COMMITTED")

ROBUSTNESS_RULES = """## Robustness Rules (learned from sandbox failures — these OVERRIDE any conflicting instruction above)
1. Load the payload with json.load(open('payload.json')). Use only the Python standard library (json, re). No network, no neo4j driver.
2. NEVER call float() directly on raw fields. Normalize `exposure_raw` with a regex parser: strip '$', '€', ',', spaces and
   currency words (USD, EUR); apply suffix multipliers K=1e3, M=1e6, B=1e9 (e.g. '€4.2M' -> 4200000.0, '1.5M EUR' -> 1500000.0).
   currency = 'USD' if '$' or 'USD' appears, 'EUR' if '€' or 'EUR' appears. None/empty -> exposure 0.0 and currency None.
3. Normalize edge `weight`: numbers -> float(weight); strings like 'CRITICAL_0.98' -> extract the number with
   re.search(r'[-+]?\\d*\\.?\\d+', weight) and float() it.
4. Keep every name byte-exact, including apostrophes (e.g. "NVIDIA Corp's Core"). Never build Cypher by string
   interpolation; if Cypher is built at all, use parameters ($props) only.
5. Print exactly one line: CLEANED_GRAPH_JSON: <json.dumps(...) on a single line> with
   {"nodes":[{"id","name","tier","exposure","currency","status"}],"edges":[{"src","dst","rel","weight"}]}.
6. Print ASSERTION_PASSED_GRAPH_COMMITTED as the very last line, only after all validation succeeds.
   Let real errors raise (non-zero exit); never swallow exceptions with a bare except.
7. Output ONLY raw Python code, no markdown fences, no explanations."""

PROTOCOL_REMINDER = """## Output protocol (mandatory)
- Read 'payload.json' from the current directory; standard library only; no network.
- Print one line `CLEANED_GRAPH_JSON: <single-line json>` with nodes[id,name,tier,exposure,currency,status] and edges[src,dst,rel,weight].
- Print `ASSERTION_PASSED_GRAPH_COMMITTED` as the very last line. Output ONLY raw Python code."""


def _schema_hint() -> str:
    try:
        import dirty_payload

        fn = getattr(dirty_payload, "get_schema_hint", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    return (
        "nodes[]: id:str, name:str (may contain apostrophes), tier:str, exposure_raw: str|null "
        "(mixed formats: '$12,500,000', '8500000 USD', '€4.2M', '1.5M EUR', null), status:str\n"
        "edges[]: src:str, dst:str, rel:str, weight: float | str with prefix (e.g. 'CRITICAL_0.98'), note:str"
    )


def _fill(template: str, **kw: str) -> str:
    """str.format, but tolerant of literal braces in the template."""
    try:
        return template.format(**kw)
    except (KeyError, ValueError, IndexError):
        out = template
        for k, v in kw.items():
            out = out.replace("{" + k + "}", v)
        return out


def _endpoint_host() -> str:
    try:
        return urlparse(os.getenv("LLM_BASE_URL", "")).hostname or "unknown"
    except Exception:
        return "unknown"


def _llm():
    import llm_client  # imported lazily so tests can monkeypatch / inject it

    return llm_client


def _model_name(role: str) -> str:
    try:
        return _llm().model_name(role)
    except Exception:
        return os.getenv("FORWARD_MODEL" if role == "forward" else "BACKWARD_MODEL", "unknown")


def _call_info() -> Dict[str, Any]:
    """Which endpoint/model actually served the last call (llm_client may fall back)."""
    try:
        fn = getattr(_llm(), "get_last_call_info", None)
        info = fn() if callable(fn) else {}
        return {k: info.get(k) for k in ("model", "host", "provider", "display", "fallback_used") if info.get(k) is not None}
    except Exception:
        return {}


def _tail(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else "...[truncated]...\n" + text[-n:]


# ---------------------------------------------------------------------------
# Robust parsing helpers
# ---------------------------------------------------------------------------
_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n([\s\S]*?)```")
_CODE_START_RE = re.compile(
    r"""^\s*(import\s|from\s+\S+\s+import\s|\#|def\s|class\s|@|if\s|for\s|while\s|try:|with\s|print\(|
        async\s|raise\s|assert\s|return\s|\"\"\"|'''|[A-Za-z_][A-Za-z0-9_\.]*(\[[^\]]*\])?\s*(=|\+=|\())""",
    re.VERBOSE,
)


def _strip_think(text: str) -> str:
    """Remove qwen3-style reasoning: full <think>..</think> blocks, an orphan '</think>' prefix
    (opening tag eaten by the chat template), and an unclosed trailing <think>."""
    text = _THINK_RE.sub("", text or "")
    low = text.lower()
    if "</think>" in low:  # orphan closing tag: everything before it is reasoning
        text = text[low.rindex("</think>") + len("</think>"):]
        low = text.lower()
    if "<think>" in low:  # unclosed reasoning block
        text = text[: low.index("<think>")]
    return text.strip()


def _is_prose_line(line: str) -> bool:
    s = line.rstrip()
    if not s.strip():
        return False
    if s[:1].isspace() or _CODE_START_RE.match(s):
        return False
    if re.match(r"^(\d+[\.\)]|[-*•])\s+\S", s):
        return True
    words = s.split()
    return len(words) >= 3 and not re.search(r"[=\(\)\[\]\{\}]", s)


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def extract_code(raw: str) -> str:
    """Pull runnable Python out of an LLM reply (fences, <think> blocks, prose before/after)."""
    text = _strip_think(raw)
    blocks = [(lang.lower(), body) for lang, body in _FENCE_RE.findall(text)]
    if blocks:
        py = [b for lang, b in blocks if lang in ("python", "py", "python3", "")] or [b for _, b in blocks]
        return max(py, key=len).strip()
    if "```" in text:  # unclosed fence: keep what follows the opening fence
        after = text.split("```", 1)[1]
        text = after.split("\n", 1)[1] if "\n" in after else ""
    lines = text.replace("\r\n", "\n").split("\n")
    start = 0
    while start < len(lines) and (not lines[start].strip() or not _CODE_START_RE.match(lines[start])):
        start += 1
    if start == len(lines):  # nothing looks like code; return as-is so the sandbox reports the error
        return text.strip()
    end = len(lines)
    while end > start and (not lines[end - 1].strip() or _is_prose_line(lines[end - 1])):
        end -= 1
    code = "\n".join(lines[start:end]).strip()
    # If heuristics broke a parsable reply, prefer the plain stripped original.
    if not _parses(code) and _parses(text):
        return text.strip()
    return code


def _json_candidates(text: str) -> List[str]:
    cands = [text]
    for _, body in _FENCE_RE.findall(text):
        cands.append(body)
    if "{" in text and "}" in text:
        cands.append(text[text.index("{"): text.rindex("}") + 1])
    return cands


def _repair_json(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r",\s*([}\]])", r"\1", s)


def _regex_key(text: str, key: str) -> Optional[str]:
    m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"(?=\s*(,|\}|$))' % key, text, re.DOTALL)
    if m:
        try:
            return json.loads('"' + m.group(1) + '"', strict=False)
        except Exception:
            return m.group(1)
    # Unescaped quotes inside a long value: take up to the last quote before the closing brace.
    m = re.search(r'"%s"\s*:\s*"' % key, text)
    if m:
        rest = text[m.end():]
        stop = rest.rfind('"')
        if stop > 0:
            val = rest[:stop]
            nxt = re.search(r'"\s*,\s*"[A-Za-z_]+"\s*:', val)  # don't swallow a following key
            if nxt:
                val = val[: nxt.start()]
            return val.replace("\\n", "\n").replace('\\"', '"')
    return None


def parse_gradient_json(raw: str) -> Dict[str, Any]:
    """Best-effort parse of {"textual_gradient", "updated_prompt"} from a small model's reply."""
    text = _strip_think(raw)
    for cand in _json_candidates(text):
        for variant in (cand, _repair_json(cand)):
            try:
                obj = json.loads(variant, strict=False)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
            try:
                obj = ast.literal_eval(variant)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    out: Dict[str, Any] = {}
    for key in ("textual_gradient", "updated_prompt"):
        val = _regex_key(text, key)
        if val:
            out[key] = val
    return out


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return "\n".join("- " + _as_text(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class EvoGraphEngine:
    def __init__(self, initial_prompt: Optional[str] = None):
        self.initial_prompt = initial_prompt or _naive_prompt()
        self.system_prompt_param = Parameter(
            data=self.initial_prompt,
            role_desc="System prompt for the financial graph ETL + contagion script generator",
            requires_opt=True,
            name="etl_system_prompt",
        )
        self.prompt_versions: List[str] = [self.initial_prompt]
        self.gradient_history: List[Dict[str, Any]] = []
        self.last_forward_meta: Dict[str, Any] = {}
        self.last_backward_meta: Dict[str, Any] = {}
        self._backward_count = 0

    @property
    def current_prompt(self) -> str:
        return self.system_prompt_param.data

    def reset(self) -> None:
        self.__init__(self.initial_prompt)

    # -- forward ------------------------------------------------------------
    def forward_generate_code(self, payload_str: str) -> str:
        user = _fill(
            _p("FORWARD_USER_TEMPLATE", _DEFAULT_FORWARD_USER_TEMPLATE),
            payload=payload_str,
            schema_hint=_schema_hint(),
        )
        messages = [
            {"role": "system", "content": self.system_prompt_param.data},
            {"role": "user", "content": user},
        ]
        t0 = time.time()
        raw = _llm().chat("forward", messages, temperature=0.1, json_mode=False)
        code = extract_code(raw)
        info = _call_info()
        self.last_forward_meta = {
            "model": info.get("model") or _model_name("forward"),
            "latency_ms": int((time.time() - t0) * 1000),
            "endpoint": _endpoint_host(),
            "raw_chars": len(raw or ""),
            "code_chars": len(code),
            "prompt_version": len(self.prompt_versions),
        }
        if info:
            self.last_forward_meta.update(info)
            self.last_forward_meta["endpoint"] = info.get("host") or self.last_forward_meta["endpoint"]
        return code

    # -- backward -----------------------------------------------------------
    def _rule_patch(self, base_prompt: str, failures: List[str], sandbox_output: str) -> str:
        obs = "\n".join("- " + str(f)[:300] for f in failures[:12]) or "- (no structured failures reported)"
        tail = _tail(sandbox_output, 600).strip()
        patch = (
            f"\n\n{ROBUSTNESS_RULES}\n\n## Observed sandbox failures (epoch {self._backward_count})\n{obs}"
            + (f"\nLast sandbox output:\n{tail}" if tail else "")
        )
        return base_prompt.rstrip() + patch

    def backward_textual_gradient(
        self, failed_code: str, sandbox_output: str, failures: Optional[List[str]] = None
    ) -> Dict[str, str]:
        failures = [str(f) for f in (failures or [])]
        self._backward_count += 1
        current = self.system_prompt_param.data
        user = _fill(
            _p("BACKWARD_TEMPLATE", _DEFAULT_BACKWARD_TEMPLATE),
            prompt=current,
            code=_tail(failed_code, 6000),
            feedback=_tail(sandbox_output, 3000),
            failures="\n".join("- " + f for f in failures) or "- (none reported)",
            schema_hint=_schema_hint(),  # schema only — raw payload never leaves the private boundary
        )
        messages = [
            {"role": "system", "content": "You are a prompt optimizer. Respond with ONLY a valid JSON object."},
            {"role": "user", "content": user},
        ]
        t0 = time.time()
        raw, err = "", None
        try:
            raw = _llm().chat("backward", messages, temperature=0.2, json_mode=True)
        except Exception as e:  # LLM down: still evolve deterministically
            err = f"{type(e).__name__}: {e}"
        parsed = parse_gradient_json(raw) if raw else {}

        gradient = _as_text(parsed.get("textual_gradient"))
        updated = _as_text(parsed.get("updated_prompt"))
        if not gradient:
            gradient = "Sandbox loss signal: " + ("; ".join(failures[:6]) or _tail(sandbox_output, 300).strip() or "script failed")

        if len(updated) >= 120:
            strategy = "llm"
            if not all(m in updated for m in OUTPUT_MARKERS):
                updated = updated.rstrip() + "\n\n" + PROTOCOL_REMINDER
                strategy = "llm+protocol"
            if self._backward_count >= 2 and "Robustness Rules" not in updated:
                # Second failure in a row: guarantee convergence by pinning the learned rules.
                updated = self._rule_patch(updated, failures, sandbox_output)
                strategy = "llm+rules"
        else:
            updated = self._rule_patch(current, failures, sandbox_output)
            strategy = "rules_fallback"

        self.system_prompt_param.update_value(updated)
        self.prompt_versions.append(updated)
        info = _call_info() if not err else {}
        # LLM down -> don't hit the network again just to label the model
        self.last_backward_meta = {
            "model": info.get("model") or (os.getenv("BACKWARD_MODEL", "unknown") if err else _model_name("backward")),
            "latency_ms": int((time.time() - t0) * 1000),
            "endpoint": _endpoint_host(),
            "strategy": strategy,
            "parsed_keys": sorted(parsed.keys()),
            "llm_error": err,
            "prompt_version": len(self.prompt_versions),
        }
        if info:
            self.last_backward_meta.update(info)
            self.last_backward_meta["endpoint"] = info.get("host") or self.last_backward_meta["endpoint"]
        result = {"textual_gradient": gradient, "updated_prompt": updated}
        self.gradient_history.append({**result, **self.last_backward_meta})
        return result


# ---------------------------------------------------------------------------
# Self-test with a fake llm_client (no network)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import types

    calls: List[Dict[str, Any]] = []
    replies: List[str] = []

    fake = types.ModuleType("llm_client")

    def _chat(role, messages, temperature=0.1, json_mode=False, timeout=120):
        calls.append({"role": role, "messages": messages, "json_mode": json_mode})
        return replies.pop(0)

    fake.chat = _chat
    fake.model_name = lambda role: f"fake-{role}"
    sys.modules["llm_client"] = fake

    payload = json.dumps({"nodes": [{"id": "HOLDING:NVDA", "name": "NVIDIA Corp's Core", "exposure_raw": "$12,500,000"}]})
    eng = EvoGraphEngine()

    # forward: fences + prose before/after
    replies.append("Here is the script:\n```python\nimport json\nprint('hi')\n```\nThis script prints hi to stdout.")
    assert eng.forward_generate_code(payload) == "import json\nprint('hi')", "fenced"
    assert calls[-1]["messages"][0]["content"] == eng.current_prompt
    # forward: no fences, prose around, <think> block
    replies.append("<think>let me plan...</think>\nSure! Below is the code you asked for.\nimport json\ndata = json.load(open('payload.json'))\nprint(data)\n\nThis code loads the payload and prints it.\n1. It reads the file")
    assert eng.forward_generate_code(payload) == "import json\ndata = json.load(open('payload.json'))\nprint(data)", "prose"
    # forward: orphan </think> prefix (qwen3 template ate the opening tag), with a decoy fence in the reasoning
    replies.append("Okay, I should use ```python\nimport os\n``` maybe...\n</think>\n\n```python\nimport json\nx = 2\n```")
    assert eng.forward_generate_code(payload) == "import json\nx = 2", "orphan think"
    # forward: unclosed fence
    replies.append("```python\nimport re\nx = 1\n")
    assert eng.forward_generate_code(payload) == "import re\nx = 1", "unclosed"
    assert eng.last_forward_meta["model"] == "fake-forward" and "latency_ms" in eng.last_forward_meta

    fails = ["exit_code=1", "ValueError: could not convert string to float: '$12,500,000'"]
    out = "Traceback (most recent call last):\n  File \"<stdin>\", line 5\nValueError: could not convert string to float"

    # backward 1: ```json + trailing comma + valid prompt -> llm (markers present)
    good_prompt = "You are a careful ETL engineer. " * 5 + "Print CLEANED_GRAPH_JSON: then ASSERTION_PASSED_GRAPH_COMMITTED."
    replies.append("Here you go:\n```json\n{\"textual_gradient\": \"float() on '$12,500,000'\", \"updated_prompt\": %s,}\n```" % json.dumps(good_prompt))
    r = eng.backward_textual_gradient("x=float('$1')", out, fails)
    assert eng.last_backward_meta["strategy"] == "llm", eng.last_backward_meta
    assert r["updated_prompt"] == good_prompt and eng.current_prompt == good_prompt
    bmsg = calls[-1]["messages"][1]["content"]
    assert calls[-1]["json_mode"] and payload not in bmsg, "raw payload must not reach backward pass"

    # backward 2: only gradient key -> rules_fallback, prompt grows
    replies.append('{"textual_gradient": "weights have CRITICAL_ prefix"}')
    before = eng.current_prompt
    r = eng.backward_textual_gradient("w=float('CRITICAL_0.98')", out, fails)
    assert eng.last_backward_meta["strategy"] == "rules_fallback"
    assert r["textual_gradient"] == "weights have CRITICAL_ prefix"
    assert eng.current_prompt.startswith(before) and "Robustness Rules" in eng.current_prompt

    # backward 3: garbage -> rules_fallback with synthesized gradient
    eng2 = EvoGraphEngine()
    replies.append("I think the problem is the currency strings, you should fix them.")
    r = eng2.backward_textual_gradient("code", out, fails)
    assert eng2.last_backward_meta["strategy"] == "rules_fallback" and r["textual_gradient"].startswith("Sandbox loss signal")

    # backward 4: python-dict style (single quotes) + prompt missing markers -> llm+protocol
    eng3 = EvoGraphEngine()
    replies.append("{'textual_gradient': 'nulls', 'updated_prompt': '%s'}" % ("Parse money robustly with regex. " * 6))
    eng3.backward_textual_gradient("code", out, fails)
    assert eng3.last_backward_meta["strategy"] == "llm+protocol" and "ASSERTION_PASSED_GRAPH_COMMITTED" in eng3.current_prompt
    # backward 5: second failure in a row -> llm+rules
    replies.append(json.dumps({"textual_gradient": "still wrong", "updated_prompt": good_prompt}))
    eng3.backward_textual_gradient("code", out, fails)
    assert eng3.last_backward_meta["strategy"] == "llm+rules" and "Robustness Rules" in eng3.current_prompt

    # backward 6: unescaped quotes inside updated_prompt value
    eng4 = EvoGraphEngine()
    long_val = 'Keep names like "NVIDIA Corp\'s Core" exact. ' * 4 + "Print CLEANED_GRAPH_JSON and ASSERTION_PASSED_GRAPH_COMMITTED."
    replies.append('{"textual_gradient": "quotes", "updated_prompt": "%s"}' % long_val)
    eng4.backward_textual_gradient("code", out, fails)
    assert eng4.last_backward_meta["strategy"] == "llm", eng4.last_backward_meta
    assert "NVIDIA Corp's Core" in eng4.current_prompt

    # backward 6b: <think> block containing braces, then JSON
    eng6 = EvoGraphEngine()
    replies.append('<think>maybe {"a": 1} is wrong</think>\n' + json.dumps({"textual_gradient": "think ok", "updated_prompt": good_prompt}))
    r = eng6.backward_textual_gradient("code", out, fails)
    assert r["textual_gradient"] == "think ok" and eng6.last_backward_meta["strategy"] == "llm"
    # backward 6c: orphan </think> prefix
    replies.append('reasoning {"x": 2} ...</think>' + json.dumps({"textual_gradient": "orphan ok", "updated_prompt": good_prompt}))
    r = eng6.backward_textual_gradient("code", out, fails)
    assert r["textual_gradient"] == "orphan ok"

    # backward 7: LLM raises -> still evolves
    eng5 = EvoGraphEngine()
    fake.chat = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("nosana timeout"))
    eng5.backward_textual_gradient("code", out, fails)
    assert eng5.last_backward_meta["strategy"] == "rules_fallback" and eng5.last_backward_meta["llm_error"]

    assert len(eng.prompt_versions) == 3 and eng.prompt_versions[-1] == eng.system_prompt_param.data
    print("evo_engine self-test: ALL PASSED")
