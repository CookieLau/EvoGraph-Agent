"""Prompt lab: how reliably does each prompt fail / pass on the real stack?

Real Nosana model (via scripts/nosana_relay.py) + real Daytona sandbox + ground_truth_checker.
Groups (N runs each): naive prompt, prompt evolved live by one backward pass, hand-written reference prompt.
Writes raw results to docs/prompt_lab_results.json (nothing is written to Neo4j).

Run: .venv/bin/python scripts/nosana_prompt_lab.py [N]
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from dirty_payload import get_raw_payload_str  # noqa: E402
from evo_engine import EvoGraphEngine  # noqa: E402
from prompts import NAIVE_INITIAL_PROMPT, REFERENCE_EVOLVED_PROMPT  # noqa: E402
from sandbox_verifier import DaytonaSandboxVerifier  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
PAYLOAD = get_raw_payload_str()


def last_error(output: str) -> str:
    lines = [l for l in (output or "").strip().splitlines() if l.strip()]
    for l in reversed(lines):
        if "Error" in l or "Exception" in l:
            return l.strip()[:160]
    return (lines[-1].strip()[:160] if lines else "")


def run_once(engine, verifier, group, i):
    t0 = time.time()
    code = engine.forward_generate_code(PAYLOAD)
    fwd = dict(engine.last_forward_meta)
    res = verifier.verify_etl_script(code)
    row = {
        "group": group, "run": i, "passed": res["passed"], "reward": round(res["reward"], 3),
        "exit_code": res["exit_code"], "error": "" if res["passed"] else last_error(res["output"]),
        "failures": res["failures"][:6], "engine": res["engine"], "model": fwd.get("model"),
        "forward_s": round(fwd.get("latency_ms", 0) / 1000, 1), "sandbox_s": round(res["latency_ms"] / 1000, 2),
        "total_s": round(time.time() - t0, 1), "code_lines": len(code.splitlines()),
    }
    print("[%s #%d] passed=%s reward=%.2f exit=%s fwd=%.1fs sb=%.2fs model=%s | %s" % (
        group, i, row["passed"], row["reward"], row["exit_code"], row["forward_s"], row["sandbox_s"],
        row["model"], row["error"] or (row["failures"][:1] or [""])[0]), flush=True)
    return row, code, res


def main():
    verifier = DaytonaSandboxVerifier(PAYLOAD, network_block_all=True)
    print("warmup:", verifier.warmup(), flush=True)
    rows, evolved = [], None
    try:
        # 1) naive prompt
        naive_fail = None
        for i in range(1, N + 1):
            eng = EvoGraphEngine(NAIVE_INITIAL_PROMPT)
            row, code, res = run_once(eng, verifier, "naive", i)
            rows.append(row)
            if not res["passed"] and naive_fail is None:
                naive_fail = (code, res)

        # 2) one live backward pass from a real naive failure -> evolved prompt
        if naive_fail:
            eng = EvoGraphEngine(NAIVE_INITIAL_PROMPT)
            t0 = time.time()
            g = eng.backward_textual_gradient(naive_fail[0], naive_fail[1]["output"], naive_fail[1]["failures"])
            evolved = g["updated_prompt"]
            meta = eng.last_backward_meta
            print("backward: %.1fs strategy=%s model=%s prompt_chars=%d" % (
                time.time() - t0, meta.get("strategy"), meta.get("model"), len(evolved)), flush=True)
            print("gradient:", g["textual_gradient"][:500].replace("\n", " | "), flush=True)
            rows.append({"group": "backward", "latency_s": round(time.time() - t0, 1),
                         "strategy": meta.get("strategy"), "model": meta.get("model"),
                         "gradient": g["textual_gradient"], "evolved_prompt": evolved})
            for i in range(1, N + 1):
                row, _, _ = run_once(EvoGraphEngine(evolved), verifier, "evolved_live", i)
                rows.append(row)

        # 3) hand-written reference prompt (fallback used by the engine)
        for i in range(1, N + 1):
            row, _, _ = run_once(EvoGraphEngine(REFERENCE_EVOLVED_PROMPT), verifier, "reference", i)
            rows.append(row)
    finally:
        verifier.destroy()
        out = os.path.join(ROOT, "docs", "prompt_lab_results.json")
        json.dump(rows, open(out, "w"), indent=2, ensure_ascii=False)
        print("saved", out, flush=True)

    for g in ("naive", "evolved_live", "reference"):
        rs = [r for r in rows if r.get("group") == g]
        if rs:
            print("SUMMARY %-13s pass %d/%d  avg_reward %.2f  avg_forward %.1fs  avg_sandbox %.2fs" % (
                g, sum(r["passed"] for r in rs), len(rs), sum(r["reward"] for r in rs) / len(rs),
                sum(r["forward_s"] for r in rs) / len(rs), sum(r["sandbox_s"] for r in rs) / len(rs)))


if __name__ == "__main__":
    main()
