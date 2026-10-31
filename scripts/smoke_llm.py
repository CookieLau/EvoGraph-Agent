"""Smoke test for llm_client: health() + one tiny forward call. Never prints secrets.
Run: .venv/bin/python scripts/smoke_llm.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_client  # noqa: E402


def main() -> int:
    h = llm_client.health()
    print("health:", json.dumps(h, ensure_ascii=False, indent=2))
    t0 = time.time()
    try:
        text = llm_client.chat(
            "forward",
            [{"role": "system", "content": "You are a Python developer. Output only code, no markdown."},
             {"role": "user", "content": "Write a 5-line Python function parse_money(s) that turns '$12,500,000' into 12500000.0."}],
            temperature=0.1, timeout=180,
        )
    except Exception as e:
        print(f"chat FAILED after {time.time() - t0:.1f}s: {e}")
        print("last_call:", llm_client.get_last_call_info().get("display"))
        return 1
    info = llm_client.get_last_call_info()
    print(f"chat OK via {info.get('display')} in {time.time() - t0:.1f}s (fallback_used={info.get('fallback_used')}, tokens={info.get('completion_tokens')})")
    print("---\n" + text.strip()[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
