"""Smoke test: warmup -> failing script -> correct script -> destroy (real Daytona)."""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sandbox_verifier import DaytonaSandboxVerifier  # noqa: E402

try:
    from dirty_payload import get_raw_payload_str
    PAYLOAD = get_raw_payload_str()
except Exception:
    PAYLOAD = json.dumps({"nodes": [{"id": "HOLDING:NVDA", "name": "NVIDIA Corp's Core", "exposure_raw": "$12,500,000"}], "edges": []})

GOOD = {
    "nodes": [
        {"id": "HOLDING:NVDA", "name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure": 12500000.0, "currency": "USD", "status": "ACTIVE"},
        {"id": "HOLDING:AAPL", "name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure": 9800000.0, "currency": "USD", "status": "ACTIVE"},
        {"id": "SUP:TSMC", "name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure": 8500000.0, "currency": "USD", "status": "ACTIVE"},
        {"id": "SUP:ASML", "name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure": 4200000.0, "currency": "EUR", "status": "ACTIVE"},
        {"id": "SUP:ZEISS", "name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure": 1500000.0, "currency": "EUR", "status": "WARNING"},
        {"id": "RAW:QUARTZ_X", "name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure": 0.0, "currency": None, "status": "SANCTION_OR_FORCE_MAJEURE"},
    ],
    "edges": [
        {"src": "RAW:QUARTZ_X", "dst": "SUP:ZEISS", "rel": "SUPPLIES_MATERIAL", "weight": 0.98},
        {"src": "SUP:ZEISS", "dst": "SUP:ASML", "rel": "SUPPLIES_OPTICS", "weight": 0.95},
        {"src": "SUP:ASML", "dst": "SUP:TSMC", "rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92},
        {"src": "SUP:TSMC", "dst": "HOLDING:NVDA", "rel": "FABRICATES_CHIPS", "weight": 1.0},
        {"src": "SUP:TSMC", "dst": "HOLDING:AAPL", "rel": "FABRICATES_CHIPS", "weight": 0.88},
    ],
}

FAIL_CODE = """import json
data = json.load(open('payload.json'))
total = sum(float(n['exposure_raw']) for n in data['nodes'])
print('ASSERTION_PASSED_GRAPH_COMMITTED')
"""

GOOD_CODE = (
    "import json\n"
    "data = json.load(open('payload.json'))\n"
    f"cleaned = json.loads({json.dumps(json.dumps(GOOD))})\n"
    "print('CLEANED_GRAPH_JSON: ' + json.dumps(cleaned))\n"
    "print('ASSERTION_PASSED_GRAPH_COMMITTED')\n"
)


def show(tag, r):
    tail = r["output"].strip().splitlines()[-1:] if r["output"].strip() else [""]
    print(f"[{tag}] passed={r['passed']} reward={r['reward']:.2f} exit={r['exit_code']} "
          f"{r['latency_ms']}ms engine={r['engine']} | last: {tail[0][:110]}")
    if r["failures"]:
        print(f"    failures: {r['failures'][:3]}")


def main():
    t0 = time.time()
    v = DaytonaSandboxVerifier(PAYLOAD)
    w = v.warmup()
    print(f"[warmup] engine={w['engine']} {w['latency_ms']}ms")
    try:
        show("fail-run", v.verify_etl_script(FAIL_CODE))
        show("good-run", v.verify_etl_script(GOOD_CODE))
    finally:
        t = time.time()
        v.destroy()
        print(f"[destroy] {int((time.time() - t) * 1000)}ms | total {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
