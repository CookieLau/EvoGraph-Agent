"""Run naive/partial/robust reference scripts in a real Daytona sandbox and write replay/scripted_run.json.

Usage: .venv/bin/python reference/build_scripted_replay.py
Sandbox results are real Daytona executions; the LLM forward/backward text is pre-authored.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

try:
    from dirty_payload import GROUND_TRUTH, get_raw_payload_str
except Exception:
    from reference.payload_data import GROUND_TRUTH, get_raw_payload_str
try:
    from ground_truth_checker import check_cleaned_output
except Exception:
    from reference.mini_checker import check_cleaned_output
try:
    from prompts import NAIVE_INITIAL_PROMPT
except Exception:
    NAIVE_INITIAL_PROMPT = (
        "You are a Python developer. Write a Python script that loads payload.json and prepares it for Neo4j.\n"
        "1. Parse `exposure_raw` directly with `float(node['exposure_raw'])` to compute total exposure.\n"
        "2. Build Cypher CREATE statements with f-strings, e.g. "
        "f\"CREATE (n:Entity {{id: '{node['id']}', name: '{node['name']}'}})\".\n"
        "3. Parse edge `weight` with `float(edge['weight'])` to compute contagion risk.\n"
        "4. Print 'CLEANED_GRAPH_JSON: ' + one-line JSON, then 'ASSERTION_PASSED_GRAPH_COMMITTED'.\n"
        "Output ONLY raw Python code."
    )

PROMPT_V2 = NAIVE_INITIAL_PROMPT + """

[Optimizer patch v2 — learned from Daytona traceback, epoch 1]
- NEVER call float() directly on `exposure_raw`. Strip currency symbols ('$', '€'), currency codes ('USD', 'EUR') and thousands separators with a regex before converting.
- `exposure_raw` may be null: map None to exposure 0.0 and currency None.
- Record the currency of every node in a `currency` field ('USD' / 'EUR' / None).
- Use parameterized Cypher (`MERGE (n:Entity {id: $id}) SET n += $props`) — never interpolate names such as "NVIDIA Corp's Core" into query text.
- Output nodes as {id,name,tier,exposure,currency,status} and edges as {src,dst,rel,weight}."""

PROMPT_V3 = PROMPT_V2 + """

[Optimizer patch v3 — learned from ground-truth assertion diff, epoch 2]
- Honor magnitude suffixes: 'K' = 1e3, 'M' = 1e6, 'B' = 1e9 ('€4.2M' -> 4200000.0, '1.5M EUR' -> 1500000.0). Do not strip letters before reading the suffix.
- Edge weights may be strings with a severity prefix: strip a leading 'CRITICAL_' and parse the remainder as-is ('CRITICAL_0.98' -> 0.98, 'CRITICAL_1.0' -> 1.0). Never rescale weights.
- Assert every weight is within [0, 1] and every node/edge from the payload is present before printing the marker."""

GRADIENT_1 = (
    "Loss signal: Daytona sandbox exit_code=1, ValueError: could not convert string to float: '$12,500,000' "
    "(raised on the very first node, HOLDING:NVDA).\n"
    "Root cause 1 — the prompt instructs `float(node['exposure_raw'])`, but exposure_raw is heterogeneous: "
    "'$12,500,000', '8500000 USD', '€4.2M', '1.5M EUR' and null. Any of these crashes float() (ValueError / TypeError).\n"
    "Root cause 2 (latent, masked by the crash) — f-string Cypher interpolation would break on names containing "
    "apostrophes ('NVIDIA Corp's Core', 'Carl Zeiss SMT's Optics') → Cypher injection / syntax error.\n"
    "Root cause 3 (latent) — `float(edge['weight'])` will fail on 'CRITICAL_0.98'.\n"
    "Gradient direction: replace raw float() with a currency-aware normaliser, handle null, switch to parameterized Cypher."
)
GRADIENT_2 = (
    "Loss signal: script exited 0 but the ground-truth assertion rejected 4 fields (reward 0.73).\n"
    "Diff: SUP:ASML exposure 4.2 vs expected 4,200,000; SUP:ZEISS 1.5 vs 1,500,000 — the regex `[^0-9.]` strips the "
    "'M' magnitude suffix, silently shrinking EUR exposures by 10^6.\n"
    "Diff: RAW:QUARTZ_X->SUP:ZEISS weight 0.098 vs 0.98 and SUP:TSMC->HOLDING:NVDA 0.1 vs 1.0 — 'CRITICAL_' weights "
    "were rescaled /10 instead of having the prefix stripped.\n"
    "Silent data corruption is worse than a crash: the contagion score for NVDA would be under-reported ~10x.\n"
    "Gradient direction: parse K/M/B suffixes before stripping letters; strip 'CRITICAL_' and never rescale; add range asserts."
)

SCRIPTS = [
    ("naive_etl.py", NAIVE_INITIAL_PROMPT, GRADIENT_1, PROMPT_V2),
    ("partial_etl.py", PROMPT_V2, GRADIENT_2, PROMPT_V3),
    ("robust_etl.py", PROMPT_V3, None, None),
]


def compute_contagion() -> list:
    nodes, edges = GROUND_TRUTH["nodes"], GROUND_TRUTH["edges"]
    out_edges = {}
    for key, e in edges.items():
        src, dst = key.split("->")
        out_edges.setdefault(src, []).append((dst, e["weight"]))
    results = []

    def walk(cur, path, prod):
        if nodes[cur]["tier"] == "Portfolio_Holding" and len(path) > 1:
            exp = nodes[cur]["exposure"]
            results.append({"path": list(path), "names": [nodes[p]["name"] for p in path],
                            "contagion": round(prod, 6), "holding": cur, "exposure": exp,
                            "at_risk": round(exp * prod, 2)})
        for dst, w in out_edges.get(cur, []):
            if dst not in path:
                walk(dst, path + [dst], prod * w)

    for nid, n in nodes.items():
        if n["tier"] == "Tier4_RawMaterial":
            walk(nid, [nid], 1.0)
    return sorted(results, key=lambda r: -r["at_risk"])


def graph_for_viz() -> dict:
    return {
        "nodes": [dict(id=k, **v) for k, v in GROUND_TRUTH["nodes"].items()],
        "edges": [dict(src=k.split("->")[0], dst=k.split("->")[1], **v) for k, v in GROUND_TRUTH["edges"].items()],
    }


def main() -> None:
    from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig

    d = Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"],
                              api_url=os.getenv("DAYTONA_API_URL", "https://app.daytona.io/api")))
    run_id = "scripted-" + uuid.uuid4().hex[:8]
    t0 = time.time()
    events = [{"type": "start", "run_id": run_id, "ts": t0, "engine_hint": "daytona", "scripted": True,
               "note": "sandbox results are real Daytona executions; LLM steps pre-authored"}]
    tw = time.time()
    sb = d.create(CreateSandboxFromSnapshotParams(language="python", network_block_all=True,
                                                  auto_stop_interval=15, labels={"app": "evograph-ref"}), timeout=180)
    sb.fs.upload_file(get_raw_payload_str().encode("utf-8"), "payload.json")
    engine = f"Daytona Sandbox ({sb.id[:8]})"
    events.append({"type": "warmup", "engine": engine, "sandbox_id": sb.id, "latency_ms": int((time.time() - tw) * 1000),
                   "network_block_all": True})
    passed_epoch = None
    try:
        for epoch, (fname, prompt, gradient, next_prompt) in enumerate(SCRIPTS, start=1):
            code = open(os.path.join(ROOT, "reference", fname)).read()
            events.append({"type": "forward", "epoch": epoch, "prompt": prompt, "code": code,
                           "model": "scripted (reference/" + fname + ")", "latency_ms": 0})
            ts = time.time()
            resp = sb.process.code_run(code)
            latency = int((time.time() - ts) * 1000)
            output = resp.result or ""
            chk = check_cleaned_output(output, resp.exit_code)
            result = {"passed": chk["passed"], "reward": chk["reward"], "exit_code": resp.exit_code, "output": output,
                      "latency_ms": latency, "engine": engine, "sandbox_id": sb.id, "checks": chk["checks"],
                      "failures": chk["failures"], "cleaned": chk["cleaned"]}
            events.append({"type": "sandbox", "epoch": epoch, "result": result})
            print(f"epoch {epoch} {fname}: exit={resp.exit_code} passed={chk['passed']} reward={chk['reward']} {latency}ms")
            if chk["passed"]:
                passed_epoch = epoch
                events.append({"type": "passed", "epoch": epoch})
                break
            events.append({"type": "gradient", "epoch": epoch, "textual_gradient": gradient,
                           "updated_prompt": next_prompt, "model": "scripted", "latency_ms": 0})
    finally:
        d.delete(sb)
    if passed_epoch:
        n_nodes, n_rels = len(GROUND_TRUTH["nodes"]), len(GROUND_TRUTH["edges"])
        events.append({"type": "neo4j_commit", "stats": {"nodes": n_nodes, "rels": n_rels}, "latency_ms": 0, "scripted": True})
        events.append({"type": "contagion", "paths": compute_contagion()})
        events.append({"type": "graph", "graph": graph_for_viz()})
    events.append({"type": "done", "success": bool(passed_epoch), "epochs": passed_epoch or len(SCRIPTS)})
    os.makedirs(os.path.join(ROOT, "replay"), exist_ok=True)
    out = os.path.join(ROOT, "replay", "scripted_run.json")
    with open(out, "w") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)
    print(f"wrote {out} ({len(events)} events), sandbox {sb.id[:8]} deleted")


if __name__ == "__main__":
    main()
