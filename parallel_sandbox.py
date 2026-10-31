"""Best-of-K parallel verification: run K candidate ETL scripts in K offline
Daytona sandboxes at the same time and pick the highest reward.

Each candidate gets its own fresh sandbox (network_block_all=True), its own
copy of payload.json, and is scored by ground_truth_checker. Sandboxes are
always deleted in `finally`. If Daytona fails for a candidate, that candidate
falls back to a local subprocess (engine label says "Local Fallback").
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

MARKER = "ASSERTION_PASSED_GRAPH_COMMITTED"
OUTPUT_LIMIT = 6000
PAR_LABEL = {"app": "evograph-par"}


def _score(output: str, exit_code: int) -> Dict[str, Any]:
    try:
        from ground_truth_checker import check_cleaned_output

        return check_cleaned_output(output, exit_code)
    except ImportError:
        ok = exit_code == 0 and MARKER in output
        return {
            "passed": ok,
            "reward": 1.0 if ok else 0.0,
            "failures": [] if ok else [f"exit_code={exit_code} or marker missing"],
            "checks": [{"name": "marker", "ok": ok, "detail": "fallback marker check"}],
            "cleaned": None,
        }


def _make_client():
    if os.getenv("USE_LOCAL_FALLBACK", "false").lower() == "true":
        return None
    api_key = os.getenv("DAYTONA_API_KEY", "")
    if not api_key:
        return None
    try:
        from daytona import Daytona, DaytonaConfig

        return Daytona(
            DaytonaConfig(
                api_key=api_key,
                api_url=os.getenv("DAYTONA_API_URL", "https://app.daytona.io/api"),
            )
        )
    except Exception:
        return None


def _run_local(code: str, payload_str: str):
    with tempfile.TemporaryDirectory(prefix="evograph_par_") as tmp:
        with open(os.path.join(tmp, "payload.json"), "w", encoding="utf-8") as f:
            f.write(payload_str)
        script = os.path.join(tmp, "etl_script.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = subprocess.run(
                [sys.executable, script], cwd=tmp, capture_output=True, text=True, timeout=20
            )
            out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            return proc.returncode, out.strip()
        except subprocess.TimeoutExpired:
            return 124, "TimeoutExpired: script exceeded 20s in local fallback"


def _run_one(index: int, code: str, payload_str: str, client, network_block_all: bool) -> Dict[str, Any]:
    t0 = time.time()
    exit_code: Optional[int] = None
    output = ""
    engine = "Local Fallback (subprocess)"
    sandbox_id = None
    error = None
    exec_ms = 0

    if client is not None:
        from daytona import CreateSandboxFromSnapshotParams

        sb = None
        try:
            sb = client.create(
                CreateSandboxFromSnapshotParams(
                    language="python",
                    network_block_all=network_block_all,
                    auto_stop_interval=15,
                    labels=dict(PAR_LABEL),
                ),
                timeout=120,
            )
            sandbox_id = sb.id
            sb.fs.upload_file(payload_str.encode("utf-8"), "payload.json")
            te = time.time()
            resp = sb.process.code_run(code, timeout=60)
            exec_ms = int((time.time() - te) * 1000)
            exit_code, output = int(resp.exit_code), resp.result or ""
            net = "offline" if network_block_all else "online"
            engine = f"Daytona Sandbox ({sb.id[:8]}), {net}"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            exit_code = None
        finally:
            if sb is not None:
                try:
                    client.delete(sb)
                except Exception:
                    pass

    if exit_code is None:
        te = time.time()
        exit_code, output = _run_local(code, payload_str)
        exec_ms = int((time.time() - te) * 1000)
        engine = "Local Fallback (subprocess)"

    wall_ms = int((time.time() - t0) * 1000)
    score = _score(output, exit_code)
    if len(output) > OUTPUT_LIMIT:
        output = "...[truncated]...\n" + output[-OUTPUT_LIMIT:]
    return {
        "index": index,
        "passed": bool(score.get("passed")),
        "reward": float(score.get("reward", 0.0)),
        "exit_code": exit_code,
        "output": output,
        "latency_ms": exec_ms,      # code_run time only
        "wall_ms": wall_ms,         # create + upload + run + score (+ delete excluded)
        "engine": engine,
        "sandbox_id": sandbox_id,
        "checks": score.get("checks", []),
        "failures": score.get("failures", []),
        "cleaned": score.get("cleaned"),
        "error": error,
    }


def verify_many(
    codes: List[str],
    payload_str: str,
    max_workers: Optional[int] = None,
    network_block_all: bool = True,
) -> List[Dict[str, Any]]:
    """Run each candidate in its own sandbox, in parallel. Results keep input order."""
    if not codes:
        return []
    client = _make_client()
    workers = max_workers or min(len(codes), 10)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_one, i, c, payload_str, client, network_block_all)
            for i, c in enumerate(codes)
        ]
        return [f.result() for f in futures]


def best_of(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Highest reward wins; ties go to the faster candidate."""
    if not results:
        return None
    return sorted(results, key=lambda r: (-r["reward"], r["wall_ms"]))[0]


def summary(results: List[Dict[str, Any]], total_wall_ms: int) -> Dict[str, Any]:
    serial_ms = sum(r["wall_ms"] for r in results)
    best = best_of(results)
    return {
        "k": len(results),
        "parallel_wall_ms": total_wall_ms,
        "serial_sum_ms": serial_ms,
        "speedup": round(serial_ms / total_wall_ms, 2) if total_wall_ms else None,
        "best_index": best["index"] if best else None,
        "best_reward": best["reward"] if best else None,
        "passed_count": sum(1 for r in results if r["passed"]),
        "engines": sorted({r["engine"].split(" (")[0] for r in results}),
    }


def run_best_of_k(codes: List[str], payload_str: str, max_workers: Optional[int] = None) -> Dict[str, Any]:
    """Convenience: verify_many + summary + best in one call."""
    t0 = time.time()
    results = verify_many(codes, payload_str, max_workers=max_workers)
    total = int((time.time() - t0) * 1000)
    return {"results": results, "best": best_of(results), "summary": summary(results, total)}
