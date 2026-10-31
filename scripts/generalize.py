"""Generalization check: run the evolved ETL script on an unseen dirty feed in a real Daytona sandbox.

Usage:
    .venv/bin/python scripts/generalize.py                 # runs reference/robust_etl.py (epoch-3 script, unchanged)
    .venv/bin/python scripts/generalize.py --code gen.py   # also runs an LLM-generated script
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig  # noqa: E402

from ground_truth_checker import check_cleaned_output  # noqa: E402

SCENARIO = os.path.join(ROOT, "data", "scenario_ev_battery.json")
TRUTH = os.path.join(ROOT, "data", "scenario_ev_battery_truth.json")


def run_one(sb, label: str, code: str, payload: bytes, truth: dict) -> dict:
    sb.fs.upload_file(payload, "payload.json")
    t = time.time()
    r = sb.process.code_run(code, timeout=60)
    ms = int((time.time() - t) * 1000)
    res = check_cleaned_output(r.result or "", r.exit_code, truth=truth)
    ok_n = sum(1 for c in res["checks"] if c["ok"])
    print(f"\n=== {label}: exit={r.exit_code} passed={res['passed']} reward={res['reward']} "
          f"({ok_n}/{len(res['checks'])} checks) {ms}ms")
    for c in res["checks"]:
        print(f"  {'✅' if c['ok'] else '❌'} {c['name']}: {c['detail'][:160]}")
    return {"label": label, "exit_code": r.exit_code, "passed": res["passed"], "reward": res["reward"],
            "ok": ok_n, "total": len(res["checks"]), "failures": res["failures"], "latency_ms": ms}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="extra script to run (e.g. LLM-generated)")
    ap.add_argument("--json-out", help="write summary JSON here")
    args = ap.parse_args()

    payload = open(SCENARIO, "rb").read()
    truth = json.load(open(TRUTH))
    jobs = [("robust_etl.py (epoch-3 script, unchanged)", open(os.path.join(ROOT, "reference", "robust_etl.py")).read())]
    if args.code:
        jobs.append((os.path.basename(args.code), open(args.code).read()))

    d = Daytona(DaytonaConfig(api_key=os.getenv("DAYTONA_API_KEY"), api_url=os.getenv("DAYTONA_API_URL")))
    t = time.time()
    sb = d.create(CreateSandboxFromSnapshotParams(language="python", network_block_all=True,
                                                  auto_stop_interval=30, labels={"app": "evograph-gen"}), timeout=120)
    print(f"sandbox {sb.id[:8]} (offline) ready in {int((time.time() - t) * 1000)}ms — scenario: EV battery, "
          f"{len(truth['nodes'])} nodes / {len(truth['edges'])} edges")
    results = []
    try:
        for label, code in jobs:
            results.append(run_one(sb, label, code, payload, truth))
    finally:
        d.delete(sb)
        print(f"\nsandbox {sb.id[:8]} deleted")
    if args.json_out:
        json.dump(results, open(args.json_out, "w"), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
