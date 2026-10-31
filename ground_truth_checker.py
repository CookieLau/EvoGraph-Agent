"""Host-side ground-truth checker for sandbox ETL output.

The sandbox script's own "ASSERTION_PASSED" marker is not trusted: we parse its
CLEANED_GRAPH_JSON line and compare every node/edge with GROUND_TRUTH.
Failure messages are short sentences: they go to the backward optimizer and the UI.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional

MARKER = "ASSERTION_PASSED_GRAPH_COMMITTED"
JSON_PREFIX = "CLEANED_GRAPH_JSON:"

EXPOSURE_REL_TOL = 0.01
WEIGHT_ABS_TOL = 0.005

try:  # single source of truth when the data module is ready
    from dirty_payload import GROUND_TRUTH  # type: ignore
except Exception:  # fallback copy, identical to CONTRACT.md
    GROUND_TRUTH = {
        "nodes": {
            "HOLDING:NVDA": {"name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure": 12500000.0, "currency": "USD", "status": "ACTIVE"},
            "HOLDING:AAPL": {"name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure": 9800000.0, "currency": "USD", "status": "ACTIVE"},
            "SUP:TSMC": {"name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure": 8500000.0, "currency": "USD", "status": "ACTIVE"},
            "SUP:ASML": {"name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure": 4200000.0, "currency": "EUR", "status": "ACTIVE"},
            "SUP:ZEISS": {"name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure": 1500000.0, "currency": "EUR", "status": "WARNING"},
            "RAW:QUARTZ_X": {"name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure": 0.0, "currency": None, "status": "SANCTION_OR_FORCE_MAJEURE"},
        },
        "edges": {
            "RAW:QUARTZ_X->SUP:ZEISS": {"rel": "SUPPLIES_MATERIAL", "weight": 0.98},
            "SUP:ZEISS->SUP:ASML": {"rel": "SUPPLIES_OPTICS", "weight": 0.95},
            "SUP:ASML->SUP:TSMC": {"rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92},
            "SUP:TSMC->HOLDING:NVDA": {"rel": "FABRICATES_CHIPS", "weight": 1.0},
            "SUP:TSMC->HOLDING:AAPL": {"rel": "FABRICATES_CHIPS", "weight": 0.88},
        },
    }

_NULL_CURRENCY = {"", "none", "null", "n/a", "na"}


# ---------------------------------------------------------------- parsing
def extract_cleaned_json(output: str) -> Optional[dict]:
    """Return the dict after the last CLEANED_GRAPH_JSON: prefix, or None."""
    if not output or JSON_PREFIX not in output:
        return None
    decoder = json.JSONDecoder()
    # try the last occurrence first (scripts sometimes print a draft first)
    idx = output.rfind(JSON_PREFIX)
    while idx != -1:
        rest = output[idx + len(JSON_PREFIX):].lstrip()
        try:
            obj, _ = decoder.raw_decode(rest)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        idx = output.rfind(JSON_PREFIX, 0, idx)
    return None


def _last_error_line(output: str) -> str:
    lines = [l.strip() for l in (output or "").splitlines() if l.strip()]
    for line in reversed(lines):
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b.*", line):
            return line[:240]
    return lines[-1][:240] if lines else "(no output)"


def _to_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("_", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _as_list(items: Any, kind: str) -> List[dict]:
    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]
    if isinstance(items, dict):  # tolerate {"id": {...}} shape
        out = []
        for k, v in items.items():
            if isinstance(v, dict):
                v = dict(v)
                if kind == "node":
                    v.setdefault("id", k)
                elif "->" in str(k):
                    s, d = str(k).split("->", 1)
                    v.setdefault("src", s)
                    v.setdefault("dst", d)
                out.append(v)
        return out
    return []


def _get(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


# ---------------------------------------------------------------- field checks
def _check_exposure(nid: str, got: Any, exp: float) -> Optional[str]:
    val = _to_float(got)
    if val is None:
        if got is None and exp == 0.0:
            return f"{nid} exposure is null != 0.0 (null exposure must default to 0.0)"
        return f"{nid} exposure {got!r} is not a number (expected {exp})"
    if exp == 0.0:
        return None if abs(val) < 1e-6 else f"{nid} exposure {val} != 0.0"
    if abs(val - exp) <= EXPOSURE_REL_TOL * abs(exp):
        return None
    hint = ""
    if val != 0 and abs(val * 1e6 - exp) <= EXPOSURE_REL_TOL * exp:
        hint = " (missing 'M' multiplier?)"
    elif abs(val - exp * 1e6) <= EXPOSURE_REL_TOL * exp * 1e6:
        hint = " (million multiplier applied twice?)"
    elif val == 0:
        hint = " (value dropped / defaulted to 0?)"
    return f"{nid} exposure {val:g} != {exp:g}{hint}"


def _check_name(nid: str, got: Any, exp: str) -> Optional[str]:
    if got == exp:
        return None
    if not isinstance(got, str):
        return f"{nid} name missing (expected {exp!r})"
    if "'" in exp and "'" not in got:
        if "\\'" in got or "''" in got or "&#39;" in got:
            return f"{nid} name was escaped ({got!r}); store the raw string {exp!r} and use parameterized Cypher"
        return f"{nid} name lost apostrophe ({got!r} != {exp!r})"
    if "\\" in got:
        return f"{nid} name contains escape characters ({got!r} != {exp!r})"
    return f"{nid} name {got!r} != {exp!r}"


def _check_currency(nid: str, got: Any, exp: Optional[str]) -> Optional[str]:
    if exp is None:
        if got is None or (isinstance(got, str) and got.strip().lower() in _NULL_CURRENCY):
            return None
        return f"{nid} currency {got!r} != null (no exposure → no currency)"
    if isinstance(got, str) and got.strip().upper() == exp:
        return None
    return f"{nid} currency {got!r} != {exp!r}"


def _check_weight(eid: str, got: Any, exp: float) -> Optional[str]:
    val = _to_float(got)
    if val is None:
        hint = " (strip 'CRITICAL_' prefix before float())" if isinstance(got, str) and "CRITICAL" in got.upper() else ""
        return f"edge {eid} weight {got!r} is not a number{hint}"
    if abs(val - exp) <= WEIGHT_ABS_TOL:
        return None
    return f"edge {eid} weight {val:g} != {exp:g}"


# ---------------------------------------------------------------- main entry
def check_cleaned_output(output: str, exit_code: int, truth: Optional[dict] = None) -> Dict[str, Any]:
    """truth: same shape as GROUND_TRUTH (defaults to it) — lets us score other scenarios."""
    output = output or ""
    gt = truth if truth else GROUND_TRUTH
    checks: List[Dict[str, Any]] = []
    failures: List[str] = []

    def add(name: str, errs: List[str], ok_detail: str = "ok") -> None:
        ok = not errs
        checks.append({"name": name, "ok": ok, "detail": ok_detail if ok else "; ".join(errs)})
        failures.extend(errs)

    # 1. exit code
    if exit_code == 0:
        add("exit_code", [], "exit 0")
    else:
        add("exit_code", [f"Script crashed (exit {exit_code}): {_last_error_line(output)}"])

    # 2. marker
    has_marker = MARKER in output
    add("marker", [] if has_marker else [f"{MARKER} marker not printed"], "marker printed")

    # 3. JSON
    cleaned = extract_cleaned_json(output)
    if cleaned is None:
        if JSON_PREFIX in output:
            msg = "CLEANED_GRAPH_JSON line present but not valid single-line JSON"
        elif has_marker:
            msg = "marker printed but CLEANED_GRAPH_JSON missing (do not print the marker without the JSON)"
        else:
            msg = "CLEANED_GRAPH_JSON line missing"
        add("cleaned_json", [msg])
    else:
        add("cleaned_json", [], "parsed")

    gt_nodes = gt["nodes"]
    gt_edges = gt["edges"]
    nodes = _as_list(cleaned.get("nodes"), "node") if cleaned else []
    edges = _as_list(cleaned.get("edges"), "edge") if cleaned else []
    node_by_id = {str(_get(n, "id")): n for n in nodes if _get(n, "id") is not None}
    edge_by_id = {}
    for e in edges:
        s, d = _get(e, "src", "source", "from"), _get(e, "dst", "target", "to")
        if s is not None and d is not None:
            edge_by_id[f"{s}->{d}"] = e

    # 4. nodes
    for nid, exp in gt_nodes.items():
        if cleaned is None:
            checks.append({"name": f"node:{nid}", "ok": False, "detail": "no cleaned JSON"})
            continue
        n = node_by_id.get(nid)
        if n is None:
            add(f"node:{nid}", [f"node {nid} missing from cleaned graph"])
            continue
        errs = []
        for err in (
            _check_name(nid, n.get("name"), exp["name"]),
            _check_exposure(nid, _get(n, "exposure", "exposure_amount", "exposure_value"), exp["exposure"]),
            _check_currency(nid, n.get("currency"), exp["currency"]),
            None if str(n.get("tier", "")).strip() == exp["tier"] else f"{nid} tier {n.get('tier')!r} != {exp['tier']!r}",
            None if str(n.get("status", "")).strip() == exp["status"] else f"{nid} status {n.get('status')!r} != {exp['status']!r}",
        ):
            if err:
                errs.append(err)
        add(f"node:{nid}", errs)

    # 5. edges
    for eid, exp in gt_edges.items():
        if cleaned is None:
            checks.append({"name": f"edge:{eid}", "ok": False, "detail": "no cleaned JSON"})
            continue
        e = edge_by_id.get(eid)
        if e is None:
            add(f"edge:{eid}", [f"edge {eid} missing from cleaned graph"])
            continue
        errs = []
        w = _check_weight(eid, e.get("weight"), exp["weight"])
        if w:
            errs.append(w)
        if str(e.get("rel", "")).strip() != exp["rel"]:
            errs.append(f"edge {eid} rel {e.get('rel')!r} != {exp['rel']!r}")
        add(f"edge:{eid}", errs)

    # 6. no unexpected entities
    if cleaned is not None:
        extra = [f"node {k}" for k in node_by_id if k not in gt_nodes] + [f"edge {k}" for k in edge_by_id if k not in gt_edges]
        add("no_extra_entities", [f"unexpected {x} in cleaned graph" for x in extra[:5]], "no extras")

    n_ok = sum(1 for c in checks if c["ok"])
    reward = round(n_ok / len(checks), 3) if checks else 0.0
    passed = exit_code == 0 and has_marker and cleaned is not None and all(c["ok"] for c in checks)
    if passed:
        reward = 1.0
        # Hand Neo4j a canonical shape (floats, list of src/dst edges), not whatever the script printed.
        cleaned = _normalize(nodes, edges)
    return {"passed": passed, "reward": reward, "failures": failures, "checks": checks, "cleaned": cleaned}


def _normalize(nodes: List[dict], edges: List[dict]) -> dict:
    out_nodes = []
    for n in nodes:
        nid = _get(n, "id")
        if nid is None:
            continue
        cur = n.get("currency")
        if cur is None or (isinstance(cur, str) and cur.strip().lower() in _NULL_CURRENCY):
            cur = None
        else:
            cur = str(cur).strip().upper()
        exp = _to_float(_get(n, "exposure", "exposure_amount", "exposure_value"))
        out_nodes.append({"id": str(nid), "name": n.get("name"), "tier": str(n.get("tier", "")).strip(),
                          "exposure": exp if exp is not None else 0.0, "currency": cur,
                          "status": str(n.get("status", "")).strip()})
    out_edges = []
    for e in edges:
        s, d = _get(e, "src", "source", "from"), _get(e, "dst", "target", "to")
        if s is None or d is None:
            continue
        w = _to_float(e.get("weight"))
        edge = {"src": str(s), "dst": str(d), "rel": str(e.get("rel", "")).strip(), "weight": w if w is not None else 0.0}
        if e.get("note") is not None:
            edge["note"] = e.get("note")
        out_edges.append(edge)
    return {"nodes": out_nodes, "edges": out_edges}


if __name__ == "__main__":  # quick manual check: python ground_truth_checker.py < output.txt
    import sys

    print(json.dumps(check_cleaned_output(sys.stdin.read(), 0), indent=2, ensure_ascii=False))
