# EvoGraph-Quant — shared build contract (read this first)

Deadline: code freeze 18:15, demo 18:30 (3 min). Ship working code first, polish later.

## Environment (verified 17:22)
- Repo root: `/Users/yiruixu/Agents2026/daytona/EvoGraph-Agent`
- Local Python: **3.9** → run with `.venv/bin/python` (all deps installed from requirements.lock.txt).
  Code must be 3.9-compatible: `from __future__ import annotations`, `Optional[...]`, no `match`, no `X | None` at runtime.
- Secrets live in `.env` (load with `python-dotenv`). **Never print or log secret values.**
- **Daytona** works: `from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams, CodeRunParams`.
  create ≈0.5s, code_run ≈0.4–1.2s, delete ≈0.3s. Sandbox Python = **3.14**, has numpy/pandas/requests.
  `network_block_all=True` works (sandbox fully offline). Aura is NOT reachable from a sandbox → Neo4j is written by the host.
  `sandbox.fs.upload_file(bytes, "payload.json")` then `open('payload.json')` in code_run works (relative path = sandbox workdir).
  ExecuteResponse has `.exit_code`, `.result` (stdout+traceback). Use `Daytona.delete(sb)`.
  **Do not touch sandbox `90079513…` — it is not ours. Only delete sandboxes you created.**
- **Neo4j Aura** works from the host (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` in .env). DB was empty at 17:20.
- **Nosana** LLM: Ollama at `LLM_BASE_URL` (OpenAI-compatible `/v1`). Being fixed (it returned a read-only FS 500). Model names
  will be written to `.env` as `FORWARD_MODEL` / `BACKWARD_MODEL`. Code must also accept `OPENAI_API_KEY` fallback if present.

## File ownership (edit ONLY your files)
| File | Owner |
|---|---|
| `dirty_payload.py` | data agent |
| `ground_truth_checker.py`, `tests/test_checker.py` | checker agent |
| `sandbox_verifier.py`, `scripts/smoke_daytona.py` | daytona agent |
| `llm_client.py`, `scripts/smoke_llm.py` | llm agent |
| `prompts.py` | prompt agent |
| `evo_engine.py` | engine agent |
| `graph_store.py`, `scripts/smoke_neo4j.py`, `queries.cypher` | neo4j agent |
| `orchestrator.py` | orchestrator agent |
| `app.py`, `replay/mock_run.json`, `.streamlit/config.toml` | frontend agent |
| `reference/*`, `replay/scripted_run.json` | reference-solution agent |
| `assets/*` | assets / diagram agents |
| `docs/PITCH.md` | pitch agent |
| `README.md` | readme agent |
| `.env` LLM lines, `scripts/nosana_*`, `docs/PROMPT_LAB.md` | **other session `daytona-8c`** (owns Nosana fix + prompt lab) |
| `data/*` | data-scale agent |
| `docs/NEO4J_DEMO.md`, `assets/neo4j_style.grass` | neo4j-console agent |

Neo4j Aura is shared: only `graph_store.py` writes to it. Nobody else runs reset/MERGE against Aura.
Nosana model is likely qwen3 → outputs may contain `<think>...</think>`; strip before parsing.

## Data + ground truth (dirty_payload.py)
`CONFIDENTIAL_PORTFOLIO_DATA` exactly as in handover.md §3 file 1. Also export:
- `get_raw_payload_str() -> str` (json.dumps indent=2)
- `get_schema_hint() -> str` — field names/types + the kinds of dirt, NO raw values (used by the backward/optimizer pass)
- `GROUND_TRUTH` =
```python
{"nodes": {
  "HOLDING:NVDA": {"name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure": 12500000.0, "currency": "USD", "status": "ACTIVE"},
  "HOLDING:AAPL": {"name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure": 9800000.0, "currency": "USD", "status": "ACTIVE"},
  "SUP:TSMC":     {"name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure": 8500000.0, "currency": "USD", "status": "ACTIVE"},
  "SUP:ASML":     {"name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure": 4200000.0, "currency": "EUR", "status": "ACTIVE"},
  "SUP:ZEISS":    {"name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure": 1500000.0, "currency": "EUR", "status": "WARNING"},
  "RAW:QUARTZ_X": {"name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure": 0.0, "currency": None, "status": "SANCTION_OR_FORCE_MAJEURE"}},
 "edges": {
  "RAW:QUARTZ_X->SUP:ZEISS": {"rel": "SUPPLIES_MATERIAL", "weight": 0.98},
  "SUP:ZEISS->SUP:ASML":     {"rel": "SUPPLIES_OPTICS", "weight": 0.95},
  "SUP:ASML->SUP:TSMC":      {"rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92},
  "SUP:TSMC->HOLDING:NVDA":  {"rel": "FABRICATES_CHIPS", "weight": 1.0},
  "SUP:TSMC->HOLDING:AAPL":  {"rel": "FABRICATES_CHIPS", "weight": 0.88}}}
```
Expected contagion (product of weights): NVDA 0.85652 → $10,706,500 at risk; AAPL 0.7537376 → $7,386,628.

## Sandbox script protocol (what LLM-generated code must do)
- Reads the payload from `payload.json` in the current working directory. Stdlib only (`json`, `re`). No network.
- Prints one line: `CLEANED_GRAPH_JSON: {"nodes":[{"id","name","tier","exposure","currency","status"}],"edges":[{"src","dst","rel","weight"}]}` (single-line JSON)
- Then prints `ASSERTION_PASSED_GRAPH_COMMITTED` as the last line. Any error → non-zero exit.

## Module interfaces
```python
# ground_truth_checker.py
def extract_cleaned_json(output: str) -> Optional[dict]
def check_cleaned_output(output: str, exit_code: int) -> dict
#   -> {"passed": bool, "reward": float 0..1 (partial credit), "failures": [str], "checks": [{"name": str, "ok": bool, "detail": str}], "cleaned": dict|None}
#   passed only if exit_code==0, marker present, JSON parses, every node/edge matches GROUND_TRUTH
#   (exposure rel tol 1%, weight abs tol 0.005, names byte-exact incl. apostrophes, currency case-insensitive).

# sandbox_verifier.py
class DaytonaSandboxVerifier:
    def __init__(self, payload_str: str, network_block_all: bool = True): ...
    def warmup(self) -> dict            # {"engine": str, "sandbox_id": str|None, "latency_ms": int}
    def verify_etl_script(self, python_code: str) -> dict
    #   -> {"passed","reward","exit_code","output","latency_ms","engine","sandbox_id","checks","failures","cleaned"}
    def destroy(self) -> None
# Local subprocess fallback if USE_LOCAL_FALLBACK=true or Daytona errors (engine label must say "Local Fallback").

# llm_client.py
def chat(role: str, messages: list, temperature: float = 0.1, json_mode: bool = False, timeout: int = 120) -> str   # role in {"forward","backward"}
def model_name(role: str) -> str
def health() -> dict   # {"ok": bool, "base_url_host": str, "models": [str], "error": str|None}

# prompts.py
NAIVE_INITIAL_PROMPT: str        # deliberately naive → epoch 1 must fail (float() on dirty strings). Braces escaped correctly.
FORWARD_USER_TEMPLATE: str       # .format(payload=..., schema_hint=...)
BACKWARD_TEMPLATE: str           # .format(prompt=..., code=..., feedback=..., failures=..., schema_hint=...) → asks for JSON {"textual_gradient","updated_prompt"}

# evo_engine.py
class EvoGraphEngine:
    system_prompt_param  # adalflow.optim.parameter.Parameter
    prompt_versions: list   # [str]
    def forward_generate_code(self, payload_str: str) -> str          # strips ``` fences
    def backward_textual_gradient(self, failed_code: str, sandbox_output: str, failures: list) -> dict  # {"textual_gradient","updated_prompt"}; robust JSON parsing for small models

# graph_store.py
class GraphStore:
    def __init__(self): ...            # reads env
    def ping(self) -> bool
    def reset_demo(self) -> None       # deletes only demo labels (:Entity, :EvoRun, :Epoch, :PromptVersion)
    def upsert_graph(self, cleaned: dict, run_id: str) -> dict          # parameterized MERGE only, returns {"nodes": n, "rels": m}
    def contagion_paths(self) -> list  # [{"path":[ids], "names":[...], "contagion": float, "holding": id, "exposure": float, "at_risk": float}] sorted by at_risk desc
    def record_epoch(self, run_id: str, epoch: int, prompt: str, passed: bool, reward: float, error_summary: str, gradient: Optional[str]) -> None
    def fetch_graph_for_viz(self) -> dict   # {"nodes":[...], "edges":[...]} same shape as cleaned JSON

# orchestrator.py
def run_evolution(max_epochs: int = 3, use_neo4j: bool = True) -> Iterator[dict]   # yields events below
def save_replay(events: list, path: str) -> None
def load_replay(path: str) -> list
```

## Event stream (orchestrator → app.py, also the replay file format = JSON list of these)
```json
{"type":"start","run_id":"...","ts":0.0,"engine_hint":"daytona|local"}
{"type":"warmup","engine":"Daytona Sandbox (95fe7c59)","sandbox_id":"...","latency_ms":480}
{"type":"forward","epoch":1,"prompt":"...","code":"...","model":"...","latency_ms":9000}
{"type":"sandbox","epoch":1,"result":{...verify_etl_script dict...}}
{"type":"gradient","epoch":1,"textual_gradient":"...","updated_prompt":"...","model":"...","latency_ms":8000}
{"type":"passed","epoch":2}
{"type":"neo4j_commit","stats":{"nodes":6,"rels":5},"latency_ms":900}
{"type":"contagion","paths":[...contagion_paths()...]}
{"type":"graph","graph":{...fetch_graph_for_viz()...}}
{"type":"done","success":true,"epochs":2}
{"type":"error","stage":"forward|sandbox|gradient|neo4j","message":"..."}
```
