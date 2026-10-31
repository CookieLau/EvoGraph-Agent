"""Demo: 6 candidate ETL scripts -> 6 offline Daytona sandboxes in parallel -> best-of-K.

Usage:  .venv/bin/python scripts/demo_parallel.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dirty_payload import get_raw_payload_str  # noqa: E402
from parallel_sandbox import run_best_of_k  # noqa: E402


def main() -> None:
    names = ["naive_etl.py", "partial_etl.py", "robust_etl.py"]
    labels, codes = [], []
    for n in names:
        with open(os.path.join(ROOT, "reference", n), encoding="utf-8") as f:
            src = f.read()
        for copy in (1, 2):
            labels.append(f"{n}#{copy}")
            codes.append(src)

    print(f">> Launching {len(codes)} candidates in {len(codes)} offline Daytona sandboxes ...")
    t0 = time.time()
    out = run_best_of_k(codes, get_raw_payload_str())
    print(f">> done in {time.time() - t0:.2f}s\n")

    for r in out["results"]:
        tail = (r["failures"][0] if r["failures"] else "all checks passed")[:80]
        print(
            f"  [{r['index']}] {labels[r['index']]:<18} reward={r['reward']:.3f} "
            f"exit={r['exit_code']} wall={r['wall_ms']:>5}ms run={r['latency_ms']:>4}ms "
            f"{r['engine']:<36} | {tail}"
        )
    s = out["summary"]
    print(
        f"\n>> parallel wall {s['parallel_wall_ms']}ms vs serial sum {s['serial_sum_ms']}ms "
        f"(speedup x{s['speedup']}); passed {s['passed_count']}/{s['k']}; engines={s['engines']}"
    )
    b = out["best"]
    print(f">> BEST candidate: [{b['index']}] {labels[b['index']]} reward={b['reward']:.3f}")


if __name__ == "__main__":
    main()
