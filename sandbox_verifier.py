"""Daytona sandbox as the execution verifier (RL "step" function).

Runs LLM-generated ETL code inside an offline Daytona sandbox, then scores the
output against the ground truth. Falls back to a local subprocess if Daytona
is unavailable or USE_LOCAL_FALLBACK=true.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import warnings
from typing import Any, Dict, Optional

from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

MARKER = "ASSERTION_PASSED_GRAPH_COMMITTED"
OUTPUT_LIMIT = 6000
APP_LABEL = {"app": "evograph"}


def _score(output: str, exit_code: int) -> Dict[str, Any]:
    """Use the ground-truth checker; fall back to a marker check if it is missing."""
    try:
        from ground_truth_checker import check_cleaned_output

        return check_cleaned_output(output, exit_code)
    except Exception as e:  # checker missing or crashed: never lose the sandbox result
        ok = exit_code == 0 and MARKER in output
        return {
            "passed": ok,
            "reward": 1.0 if ok else 0.0,
            "failures": [] if ok else [f"exit_code={exit_code} or marker missing"],
            "checks": [{"name": "marker", "ok": ok, "detail": f"fallback marker check ({type(e).__name__})"}],
            "cleaned": None,
        }


class DaytonaSandboxVerifier:
    def __init__(self, payload_str: str, network_block_all: bool = True):
        self.payload_str = payload_str
        self.network_block_all = network_block_all
        self.use_fallback = os.getenv("USE_LOCAL_FALLBACK", "false").lower() == "true"
        self.daytona = None
        self.sandbox = None
        self._last_error: Optional[str] = None

        if not self.use_fallback:
            api_key = os.getenv("DAYTONA_API_KEY", "")
            if not api_key:
                self.use_fallback = True
                self._last_error = "DAYTONA_API_KEY missing"
            else:
                try:
                    from daytona import Daytona, DaytonaConfig

                    self.daytona = Daytona(
                        DaytonaConfig(
                            api_key=api_key,
                            api_url=os.getenv("DAYTONA_API_URL", "https://app.daytona.io/api"),
                        )
                    )
                except Exception as e:  # SDK import/config failure
                    self.use_fallback = True
                    self._last_error = f"Daytona init failed: {type(e).__name__}: {e}"

    # ---------- labels ----------
    def _engine_label(self) -> str:
        if self.sandbox is None:
            return "Local Fallback (subprocess)"
        net = "offline" if self.network_block_all else "online"
        return f"Daytona Sandbox ({self.sandbox.id[:8]}), {net}"

    # ---------- sandbox lifecycle ----------
    def _create_sandbox(self) -> None:
        from daytona import CreateSandboxFromSnapshotParams

        params = CreateSandboxFromSnapshotParams(
            language="python",
            network_block_all=self.network_block_all,
            auto_stop_interval=30,
            labels=dict(APP_LABEL),
        )
        self.sandbox = self.daytona.create(params, timeout=120)

    def _upload_payload(self) -> None:
        self.sandbox.fs.upload_file(self.payload_str.encode("utf-8"), "payload.json")

    def warmup(self) -> Dict[str, Any]:
        start = time.time()
        if not self.use_fallback and self.sandbox is None:
            for attempt in range(2):
                try:
                    self._create_sandbox()
                    self._upload_payload()
                    break
                except Exception as e:
                    self._last_error = f"{type(e).__name__}: {e}"
                    self._safe_delete()
                    if attempt == 1:
                        self.use_fallback = True
        return {
            "engine": self._engine_label(),
            "sandbox_id": self.sandbox.id if self.sandbox else None,
            "latency_ms": int((time.time() - start) * 1000),
        }

    def _safe_delete(self) -> None:
        if self.sandbox is not None and self.daytona is not None:
            try:
                self.daytona.delete(self.sandbox)
            except Exception:
                pass
        self.sandbox = None

    def destroy(self) -> None:
        """Delete only the sandbox this verifier created."""
        self._safe_delete()

    def cleanup_stale(self) -> int:
        """Delete leftover sandboxes labelled app=evograph (never the current one)."""
        if self.daytona is None:
            return 0
        current = self.sandbox.id if self.sandbox else None
        removed = 0
        try:
            for sb in self.daytona.list():
                labels = getattr(sb, "labels", None) or {}
                if labels.get("app") == APP_LABEL["app"] and sb.id != current:
                    try:
                        self.daytona.delete(sb)
                        removed += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return removed

    # ---------- execution ----------
    def _run_daytona(self, code: str):
        if self.sandbox is None:
            self._create_sandbox()
        self._upload_payload()  # fresh payload every run
        resp = self.sandbox.process.code_run(code, timeout=60)
        return int(resp.exit_code), resp.result or ""

    def _run_local(self, code: str):
        with tempfile.TemporaryDirectory(prefix="evograph_") as tmp:
            with open(os.path.join(tmp, "payload.json"), "w", encoding="utf-8") as f:
                f.write(self.payload_str)
            script = os.path.join(tmp, "etl_script.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(code)
            try:
                proc = subprocess.run(
                    [sys.executable, script],
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                return proc.returncode, out.strip()
            except subprocess.TimeoutExpired:
                return 124, "TimeoutExpired: script exceeded 20s in local fallback"

    def verify_etl_script(self, python_code: str) -> Dict[str, Any]:
        start = time.time()
        exit_code, output = None, ""

        if not self.use_fallback:
            for attempt in range(2):
                try:
                    exit_code, output = self._run_daytona(python_code)
                    break
                except Exception as e:
                    self._last_error = f"{type(e).__name__}: {e}"
                    self._safe_delete()  # retry on a brand-new sandbox
            if exit_code is None:
                self.use_fallback = True

        if exit_code is None:
            exit_code, output = self._run_local(python_code)

        latency_ms = int((time.time() - start) * 1000)
        score = _score(output, exit_code)
        if len(output) > OUTPUT_LIMIT:
            output = "...[truncated]...\n" + output[-OUTPUT_LIMIT:]

        return {
            "passed": bool(score.get("passed")),
            "reward": float(score.get("reward", 0.0)),
            "exit_code": exit_code,
            "output": output,
            "latency_ms": latency_ms,
            "engine": self._engine_label(),
            "sandbox_id": self.sandbox.id if self.sandbox else None,
            "checks": score.get("checks", []),
            "failures": score.get("failures", []),
            "cleaned": score.get("cleaned"),
        }
