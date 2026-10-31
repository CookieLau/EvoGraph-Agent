"""Self-check for the EV battery scenario.

Usage:
  .venv/bin/python data/_verify.py                    # verify scenario vs truth
  .venv/bin/python data/_verify.py --write-contagion  # (re)compute expected_contagion from truth edges, then verify
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SCENARIO = HERE / "scenario_ev_battery.json"
TRUTH = HERE / "scenario_ev_battery_truth.json"

SYMBOLS = {"$": "USD", "€": "EUR", "¥": "JPY"}
CODES = ("USD", "EUR", "JPY")
MULT = {"K": 1e3, "M": 1e6, "B": 1e9}


def parse_exposure(raw) -> tuple:
    """Reference normalizer -> (amount: float, currency: Optional[str])."""
    if raw is None:
        return 0.0, None
    s = str(raw).strip()
    if not s:
        return 0.0, None
    currency: Optional[str] = None
    for sym, code in SYMBOLS.items():
        if sym in s:
            currency = code
            s = s.replace(sym, "")
    m_code = re.search(r"\b(USD|EUR|JPY)\b", s, flags=re.IGNORECASE)
    if m_code:
        currency = m_code.group(1).upper()
        s = s[: m_code.start()] + s[m_code.end():]
    s = s.replace(",", "").strip()
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([KMB])?", s, flags=re.IGNORECASE)
    if not m:
        raise ValueError(f"unparseable exposure: {raw!r}")
    amount = float(m.group(1))
    if m.group(2):
        amount *= MULT[m.group(2).upper()]
    return amount, currency


def parse_weight(raw) -> float:
    s = str(raw).strip()
    s = re.sub(r"^[A-Za-z]+_", "", s)  # CRITICAL_ / HIGH_ / any LABEL_ prefix
    return float(s)


def clean(scenario: dict) -> dict:
    nodes = {}
    for n in scenario["nodes"]:
        amt, cur = parse_exposure(n["exposure_raw"])
        nodes[n["id"]] = {"name": n["name"], "tier": n["tier"], "exposure": amt,
                          "currency": cur, "status": n["status"]}
    edges = {}
    for e in scenario["edges"]:
        edges[f"{e['src']}->{e['dst']}"] = {"rel": e["rel"], "weight": parse_weight(e["weight"])}
    return {"nodes": nodes, "edges": edges}


def contagion_paths(truth: dict) -> list:
    """All simple paths Tier4_RawMaterial -> Portfolio_Holding; contagion = product of weights."""
    nodes, adj = truth["nodes"], {}
    for key, e in truth["edges"].items():
        src, dst = key.split("->")
        adj.setdefault(src, []).append((dst, e["weight"]))
    out = []

    def dfs(cur, path, prod):
        if len(path) > 1 and nodes[cur]["tier"] == "Portfolio_Holding":
            h = nodes[cur]
            out.append({"path": list(path), "names": [nodes[p]["name"] for p in path],
                        "contagion": round(prod, 6), "holding": cur,
                        "exposure": h["exposure"], "currency": h["currency"],
                        "at_risk": round(h["exposure"] * prod, 2)})
            return
        for nxt, w in adj.get(cur, []):
            if nxt not in path:
                dfs(nxt, path + [nxt], prod * w)

    for nid, n in nodes.items():
        if n["tier"] == "Tier4_RawMaterial":
            dfs(nid, [nid], 1.0)
    out.sort(key=lambda p: (-p["contagion"], p["holding"]))
    return out


def main() -> int:
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))

    if "--write-contagion" in sys.argv:
        truth["expected_contagion"] = contagion_paths(truth)
        TRUTH.write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {len(truth['expected_contagion'])} expected_contagion paths")

    errors = []
    sc_nodes = {n["id"] for n in scenario["nodes"]}
    sc_edges = {f"{e['src']}->{e['dst']}" for e in scenario["edges"]}
    if sc_nodes != set(truth["nodes"]):
        errors.append(f"node id mismatch: {sc_nodes ^ set(truth['nodes'])}")
    if sc_edges != set(truth["edges"]):
        errors.append(f"edge id mismatch: {sc_edges ^ set(truth['edges'])}")
    for key in sc_edges:
        for end in key.split("->"):
            if end not in sc_nodes:
                errors.append(f"edge {key} references unknown node {end}")

    cleaned = clean(scenario)
    for nid, t in truth["nodes"].items():
        c = cleaned["nodes"].get(nid)
        if c is None:
            continue
        for f in ("name", "tier", "status", "currency"):
            if c[f] != t[f]:
                errors.append(f"{nid}.{f}: parsed {c[f]!r} != truth {t[f]!r}")
        if not math.isclose(c["exposure"], t["exposure"], rel_tol=1e-9, abs_tol=1e-6):
            errors.append(f"{nid}.exposure: parsed {c['exposure']} != truth {t['exposure']}")
    for key, t in truth["edges"].items():
        c = cleaned["edges"].get(key)
        if c is None:
            continue
        if c["rel"] != t["rel"]:
            errors.append(f"{key}.rel mismatch")
        if abs(c["weight"] - t["weight"]) > 1e-9:
            errors.append(f"{key}.weight: parsed {c['weight']} != truth {t['weight']}")

    expected = contagion_paths(truth)
    if json.dumps(expected, sort_keys=True) != json.dumps(truth.get("expected_contagion", []), sort_keys=True):
        errors.append("expected_contagion in truth file is stale; rerun with --write-contagion")
    if len(expected) < 2:
        errors.append("need at least 2 Tier4 -> holding paths")
    hops = [len(p["path"]) - 1 for p in expected]

    if errors:
        print("FAIL:\n  " + "\n  ".join(errors))
        return 1
    print(f"OK: {len(sc_nodes)} nodes, {len(sc_edges)} edges, {len(expected)} contagion paths "
          f"({min(hops)}-{max(hops)} hops); top: {' -> '.join(expected[0]['path'])} = {expected[0]['contagion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
