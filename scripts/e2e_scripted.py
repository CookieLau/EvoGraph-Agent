"""End-to-end run with a scripted (pre-authored) LLM, but REAL Daytona sandbox + REAL Neo4j Aura.

Use when the Nosana LLM endpoint is unavailable. Writes replay/scripted_run.json.

    .venv/bin/python scripts/e2e_scripted.py [--epochs 3] [--out replay/scripted_run.json] [--no-save]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import warnings  # noqa: E402

warnings.filterwarnings("ignore")

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

from adalflow.optim.parameter import Parameter  # noqa: E402

from prompts import NAIVE_INITIAL_PROMPT  # noqa: E402

SCRIPTS = ["reference/naive_etl.py", "reference/partial_etl.py", "reference/robust_etl.py"]
NOTE = "sandbox + Neo4j are real; LLM steps pre-authored"


def _load_scripted_gradients() -> List[Dict[str, str]]:
    """Pull the hand-written gradients / prompt versions from the reference replay (if present)."""
    path = os.path.join(ROOT, "replay", "scripted_run.json")
    grads: List[Dict[str, str]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            events = json.load(f)
        for e in events:
            if e.get("type") == "gradient":
                grads.append({"textual_gradient": e.get("textual_gradient") or "",
                              "updated_prompt": e.get("updated_prompt") or ""})
    except Exception:
        pass
    return grads


FALLBACK_GRADIENTS = [
    {"textual_gradient": "Loss signal: Daytona exit_code=1, ValueError on float('$12,500,000'). "
                         "Root cause: prompt forces float() on dirty currency strings. "
                         "Fix: normalize $, EUR, commas, None before float().",
     "updated_prompt": NAIVE_INITIAL_PROMPT + "\n\nRobustness rules: strip currency symbols/commas, "
                       "treat None as 0.0, strip CRITICAL_ prefixes, use parameterized Cypher."},
    {"textual_gradient": "Loss signal: exit 0 but ground-truth checker rejected exposures/weights. "
                         "Root cause: 'M' multiplier and CRITICAL_ prefix not handled.",
     "updated_prompt": NAIVE_INITIAL_PROMPT + "\n\nRobustness rules: apply M/K multipliers, "
                       "strip CRITICAL_ before float(), keep names byte-exact."},
]


class ScriptedEngine:
    """Same surface as EvoGraphEngine, but forward/backward are pre-authored."""

    forward_model = "scripted"
    backward_model = "scripted"

    def __init__(self) -> None:
        self.system_prompt_param = Parameter(
            data=NAIVE_INITIAL_PROMPT,
            role_desc="System Prompt for Financial Graph ETL & Contagion Script Generation",
            requires_opt=True,
        )
        self.prompt_versions: List[str] = [NAIVE_INITIAL_PROMPT]
        self._codes = []
        for p in SCRIPTS:
            with open(os.path.join(ROOT, p), "r", encoding="utf-8") as f:
                self._codes.append(f.read())
        self._grads = _load_scripted_gradients() or FALLBACK_GRADIENTS
        self._fi = 0
        self._bi = 0
        self.last_forward_meta: Dict[str, Any] = {}
        self.last_backward_meta: Dict[str, Any] = {}

    def forward_generate_code(self, payload_str: str) -> str:
        t = time.time()
        code = self._codes[min(self._fi, len(self._codes) - 1)]
        self._fi += 1
        self.last_forward_meta = {"model": "scripted", "endpoint": "scripted",
                                  "latency_ms": int((time.time() - t) * 1000)}
        return code

    def backward_textual_gradient(self, failed_code: str, sandbox_output: str, failures: list) -> dict:
        t = time.time()
        g = dict(self._grads[min(self._bi, len(self._grads) - 1)])
        self._bi += 1
        new_prompt = g.get("updated_prompt") or self.system_prompt_param.data
        self.system_prompt_param.update_value(new_prompt)
        self.prompt_versions.append(new_prompt)
        self.last_backward_meta = {"model": "scripted", "endpoint": "scripted",
                                   "latency_ms": int((time.time() - t) * 1000)}
        return {"textual_gradient": g.get("textual_gradient") or "", "updated_prompt": new_prompt}


def _brief(e: dict) -> str:
    t = e.get("type")
    ts = e.get("ts")
    if t == "sandbox":
        r = e.get("result") or {}
        tail = (r.get("output") or "").strip().splitlines()[-1:] or [""]
        return (f"[{ts:>6}s] sandbox  ep{e.get('epoch')} exit={r.get('exit_code')} passed={r.get('passed')} "
                f"reward={r.get('reward')} {r.get('latency_ms')}ms {r.get('engine')} | {tail[0][:90]}")
    if t == "forward":
        return f"[{ts:>6}s] forward  ep{e.get('epoch')} model={e.get('model')} code={len(e.get('code') or '')}ch"
    if t == "gradient":
        return f"[{ts:>6}s] gradient ep{e.get('epoch')} {(e.get('textual_gradient') or '')[:80]!r}"
    if t == "neo4j_commit":
        return f"[{ts:>6}s] neo4j_commit {e.get('stats')} {e.get('latency_ms')}ms"
    if t == "contagion":
        ps = e.get("paths") or []
        return f"[{ts:>6}s] contagion source={e.get('source')} " + "; ".join(
            f"{p.get('holding')} c={p.get('contagion'):.5f} at_risk=${p.get('at_risk'):,.0f}" for p in ps[:3])
    if t == "graph":
        g = e.get("graph") or {}
        return (f"[{ts:>6}s] graph source={e.get('source')} nodes={len(g.get('nodes') or [])} "
                f"edges={len(g.get('edges') or [])}")
    if t == "warmup":
        return f"[{ts:>6}s] warmup {e.get('engine')} {e.get('latency_ms')}ms"
    if t == "error":
        return f"[{ts:>6}s] ERROR {e.get('stage')}: {(e.get('message') or '')[:160]}"
    return f"[{ts:>6}s] {t} " + json.dumps({k: v for k, v in e.items() if k not in ('type', 'ts')}, default=str)[:160]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--out", default="replay/scripted_run.json")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--no-neo4j", action="store_true", help="skip Neo4j (contagion computed locally)")
    args = ap.parse_args(argv)

    from graph_store import GraphStore
    from orchestrator import run_evolution, save_replay
    from sandbox_verifier import DaytonaSandboxVerifier
    from dirty_payload import get_raw_payload_str

    engine = ScriptedEngine()  # load gradients BEFORE we overwrite the replay file

    t = time.time()
    store = None
    if not args.no_neo4j:
        try:
            store = GraphStore()
            store.reset_demo()
            print(f"[reset_demo] {int((time.time() - t) * 1000)}ms")
        except Exception as ex:
            print(f"[neo4j] unavailable, continuing without it: {type(ex).__name__}: {str(ex)[:160]}")
            store = None

    payload = get_raw_payload_str()
    verifier = DaytonaSandboxVerifier(payload, network_block_all=True)

    events: List[dict] = []
    t0 = time.time()
    for e in run_evolution(max_epochs=args.epochs, use_neo4j=store is not None, engine=engine,
                           verifier=verifier, store=store, payload_str=payload):
        if e.get("type") == "start":
            e["scripted"] = True
            e["note"] = NOTE
        events.append(e)
        print(_brief(e), flush=True)
    print(f"[total] {time.time() - t0:.1f}s, sandbox after destroy: {getattr(verifier, 'sandbox', None)}")

    if not args.no_save:
        save_replay(events, args.out)
        print(f"[saved] {args.out} ({len(events)} events)")
    done = [e for e in events if e.get("type") == "done"]
    return 0 if done and done[-1].get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
