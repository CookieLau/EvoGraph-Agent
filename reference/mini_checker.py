"""Minimal CONTRACT-compatible checker (used only if ground_truth_checker.py is unavailable)."""
from __future__ import annotations
import json
from typing import Optional

try:
    from dirty_payload import GROUND_TRUTH  # type: ignore
except Exception:  # pragma: no cover
    from reference.payload_data import GROUND_TRUTH

MARKER = "ASSERTION_PASSED_GRAPH_COMMITTED"
PREFIX = "CLEANED_GRAPH_JSON:"


def extract_cleaned_json(output: str) -> Optional[dict]:
    for line in (output or "").splitlines():
        if line.strip().startswith(PREFIX):
            try:
                return json.loads(line.strip()[len(PREFIX):].strip())
            except Exception:
                return None
    return None


def check_cleaned_output(output: str, exit_code: int) -> dict:
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("exit_code_zero", exit_code == 0, f"exit_code={exit_code}")
    add("marker_present", MARKER in (output or ""), MARKER)
    cleaned = extract_cleaned_json(output)
    add("cleaned_json_parses", cleaned is not None, PREFIX)
    nodes = {n.get("id"): n for n in (cleaned or {}).get("nodes", []) if isinstance(n, dict)}
    edges = {f"{e.get('src')}->{e.get('dst')}": e for e in (cleaned or {}).get("edges", []) if isinstance(e, dict)}
    for nid, exp in GROUND_TRUTH["nodes"].items():
        got = nodes.get(nid)
        if got is None:
            add(f"node:{nid}", False, "missing"); continue
        probs = []
        if got.get("name") != exp["name"]:
            probs.append(f"name {got.get('name')!r} != {exp['name']!r}")
        try:
            ge = float(got.get("exposure"))
            tol = max(abs(exp["exposure"]) * 0.01, 1e-6)
            if abs(ge - exp["exposure"]) > tol:
                probs.append(f"exposure {ge:,.2f} != {exp['exposure']:,.2f}")
        except Exception:
            probs.append(f"exposure {got.get('exposure')!r} not numeric")
        gc = got.get("currency")
        if (gc.upper() if isinstance(gc, str) else gc) != exp["currency"]:
            probs.append(f"currency {gc!r} != {exp['currency']!r}")
        add(f"node:{nid}", not probs, "; ".join(probs) or "ok")
    for key, exp in GROUND_TRUTH["edges"].items():
        got = edges.get(key)
        if got is None:
            add(f"edge:{key}", False, "missing"); continue
        try:
            ok = abs(float(got.get("weight")) - exp["weight"]) <= 0.005
            add(f"edge:{key}", ok, "ok" if ok else f"weight {got.get('weight')!r} != {exp['weight']}")
        except Exception:
            add(f"edge:{key}", False, f"weight {got.get('weight')!r} not numeric")
    reward = round(sum(c["ok"] for c in checks) / len(checks), 3)
    failures = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    return {"passed": not failures, "reward": 1.0 if not failures else reward, "failures": failures,
            "checks": checks, "cleaned": cleaned}
