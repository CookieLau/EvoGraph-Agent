# AGENTS.md: run EvoGraph-Quant on a fresh machine

This guide is for AI coding agents, and humans, who have just cloned the repo and need to get the whole flow running.
Every command and every expected output below was verified on 2026-10-31 on a clean copy with **no `.env` keys** (macOS, Python 3.9.6).
`README.md` covers the pitch and the architecture. This file only covers how to run it.

## What the flow does (30-second version)

1. A small LLM writes a Python ETL script for a dirty financial supply-chain feed (`dirty_payload.py`).
2. The script runs in an **offline Daytona sandbox**. `ground_truth_checker.py` scores its output against the ground truth, giving a reward from 0 to 1.
3. If the script fails, a backward LLM pass rewrites the system prompt (an AdalFlow `Parameter`) using the sandbox traceback, then the loop runs again.
4. Once a script passes, the **host** writes the cleaned graph to **Neo4j** and runs a multi-hop contagion query.

**What success looks like**:
- An early epoch fails (exit 1, reward 0.0) and a later epoch passes (exit 0, reward 1.0).
- Contagion at risk: **NVDA $10,706,500** (factor 0.85652) and **AAPL $7,386,628** (factor 0.75374).

## 0. Setup (about 1–3 minutes, mostly pip)

```bash
git clone https://github.com/CookieLau/EvoGraph-Agent && cd EvoGraph-Agent
./setup.sh                 # picks python3.12/3.11/3.13/3.10/python3, builds .venv, copies .env.example -> .env
source .venv/bin/activate
```

- **Python 3.10 or newer is recommended.**
  - 3.9 works, and `requirements.lock.txt` is pinned for 3.9.
  - On 3.9, the Daytona SDK reports *every* API error as `TypeError: unsupported operand type(s) for |`, which hides real auth and quota errors.
- On any Python other than 3.9, `setup.sh` resolves `requirements.txt` fresh and **rewrites `requirements.lock.txt`**. Do not commit that change.
- The `.env` created from `.env.example` has **empty values**. Leave unknown values empty: placeholder strings make Neo4j retry for about 18 s and make the LLM return 401.
- `llm_client.py` loads `.env` with `override=True`, so **put keys in `.env`, not in shell `export`s**.

## 1. Zero-credential path: the full loop in about a minute

With no keys, every component degrades to a clearly labelled local mode:
- **Daytona** becomes `Local Fallback (subprocess)`.
- **Neo4j** is skipped, and contagion is computed locally (`source=local`).
- **The LLM** is replaced by pre-written steps ("scripted" mode).

| Command | Time | Expected output |
|---|---|---|
| `python -m pytest tests -q` | 0.2 s | `9 passed` |
| `python scripts/smoke_daytona.py` | < 1 s | engine `Local Fallback (subprocess)`; naive script exits 1 with `ValueError: could not convert string to float: '$12,500,000'`; good script reward 1.00 |
| `python scripts/e2e_scripted.py --no-neo4j --no-save` | 1 s | 3 epochs, reward **0.0 → 0.733 → 1.0**; `contagion source=local HOLDING:NVDA … at_risk=$10,706,500; HOLDING:AAPL … $7,386,628` |
| `python scripts/demo_parallel.py` | < 1 s | 6 candidates, `passed 2/6`, best = robust script, reward 1.000 |
| `python web_server.py` | — | dashboard at **http://localhost:8600** (stdlib server, no Streamlit needed) |

- Always pass `--no-save` to `e2e_scripted.py`. Without it, the script overwrites `replay/scripted_run.json`, the file that scripted mode reads its gradient text from.
- With no LLM configured, `python orchestrator.py --epochs 3 --no-neo4j` exits 1 with a clean message: `No LLM endpoint configured: set LLM_BASE_URL (Nosana) or OPENAI_API_KEY in .env`. That is expected.

### Dashboard (port 8600)

There are three modes:
- **Replay**: plays `replay/golden_run.json`, a recorded *real* run on Nosana, Daytona and Neo4j.
- **Scripted**: runs live, about 2 s locally.
- **Live**: needs an LLM.

The **fan-out** button runs 6 candidate scripts in parallel.

For headless agents, the same flow is available over HTTP:

```bash
curl -s localhost:8600/api/config                             # which services are configured (no secret values)
curl -s localhost:8600/api/replays                            # ["golden_run.json", "scripted_run.json", "mock_run.json"]
curl -sN "localhost:8600/api/run?mode=scripted&epochs=3"      # Server-Sent Events: start, warmup, forward, sandbox, gradient, …, passed, contagion, graph, done
curl -sN "localhost:8600/api/run?mode=live&epochs=3"          # same, with real LLM calls
curl -s -X POST localhost:8600/api/fanout                     # best-of-6 sandbox fan-out
```

The older Streamlit UI also works: `streamlit run app.py` (port 8501). To drive it headless with `streamlit.testing.v1.AppTest`, run with `PYTHONPATH=.`.

## 2. Full live path (credentials supplied)

Add this minimal block to `.env`:

```ini
DAYTONA_API_KEY="dtn_..."                      # app.daytona.io -> API keys; DAYTONA_API_URL defaults to https://app.daytona.io/api
LLM_BASE_URL="https://api.openai.com/v1"      # any OpenAI-compatible endpoint
LLM_API_KEY="sk-..."
FORWARD_MODEL="gpt-4o-mini"
BACKWARD_MODEL="gpt-4o-mini"                  # or gpt-4o
NEO4J_URI="neo4j+s://xxxx.databases.neo4j.io"  # Neo4j Aura free tier is fine
NEO4J_USER="neo4j"
NEO4J_PASSWORD="..."
```

Then check each service and run the loop:

```bash
python scripts/smoke_daytona.py      # engine must read "Daytona Sandbox (xxxxxxxx) · offline"; "Local Fallback" means the key did not work
python scripts/smoke_llm.py          # /v1 health, model list, one completion
python scripts/smoke_neo4j.py        # connectivity, upsert, contagion round-trip
python scripts/e2e_scripted.py --no-save          # real Daytona + Neo4j, pre-written LLM steps (about 8 s)
python orchestrator.py --epochs 3                 # full live loop; --no-neo4j to skip Neo4j; --record replay/my_run.json to save a replay
python scripts/generalize.py         # final prompt vs an unseen EV-battery feed (data/); needs a Daytona key, no local fallback
python web_server.py                 # dashboard, mode = live
```

**LLM choice**
- Any OpenAI-compatible endpoint works. `llm_client` sends only `model`, `messages`, `temperature`, plus `response_format=json_object` for the backward pass.
  - OpenAI: use `gpt-4o-mini`, `gpt-4.1-mini` or `gpt-4o`. Avoid o-series and gpt-5 reasoning models: they reject `temperature`, and the JSON mode used by the backward pass fails.
  - Local Ollama: `LLM_BASE_URL="http://127.0.0.1:11434/v1"`, `LLM_API_KEY="ollama"`, model e.g. `qwen2.5-coder:7b`.
  - Nosana: deploy an Ollama or vLLM job and set `LLM_BASE_URL` to its URL plus `/v1`.
- `qwen3*` models automatically get `/no_think` appended, and any `<think>` blocks are stripped.
- A second endpoint can be set with `FALLBACK_*`, or with `OPENAI_API_KEY` alone. It is used only if the primary endpoint fails, and the UI labels it as a fallback.

**What the services do**
- **Daytona**: every run creates its own sandbox with `network_block_all=True`, uploads `payload.json`, runs the script, then deletes the sandbox. Sandboxes are labelled `app=evograph` or `app=evograph-par`. Never delete sandboxes you did not create.
- **Neo4j**: written **from the host**, because the sandbox is offline. The demo uses only the labels `:Entity`, `:EvoRun`, `:Epoch` and `:PromptVersion`.
  - The UI's reset clears `:Entity` only. Graph memory (`:EvoRun`, `:Epoch`, `:PromptVersion`) is kept by default; set `GRAPH_MEMORY=0` for a full wipe.

**Reference numbers from our run** (`qwen3.8:27b` on a Nosana RTX 5090, real Daytona and Aura):
- About 30 s end to end.
- Epoch 1 fails with `ValueError: could not convert string to float: '1.5M'`; epoch 2 passes.
- Pass rates were 0/5 for the naive prompt and 5/5 after one backward pass (`docs/PROMPT_LAB.md`).

## 3. Do NOT run these on a fresh machine

These only work on the team's demo laptop:

| File | Why |
|---|---|
| `scripts/nosana_relay.py`, `scripts/nosana_connect_proxy.py` | SSH relay to *our* Nosana job's Ollama. It needs our SSH key, and the node is gone after the event. Point `LLM_BASE_URL` at your own endpoint instead. |
| `agent_api_server.py` | Local LLM stand-in that shells out to the Claude Code CLI. Demo-laptop only. |
| `scripts/nosana_prompt_lab.py` | Prompt experiment that needs a working LLM and Daytona key. Its results are already in `docs/PROMPT_LAB.md`. |
| `CONTRACT.md` | Internal build contract from the hackathon. Its absolute paths are historical and not needed to run anything. |

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every run stalls about 18 s; log shows `Transaction failed and will be retried` | `NEO4J_URI` points at an unreachable server (e.g. the old `bolt://localhost:7687` placeholder) | Empty it, or use a real Aura URI |
| `401 … Incorrect API key provided: your_…` | Placeholder key in `.env` | Empty it, or put in the real key |
| LLM key exported in the shell is ignored | `llm_client` loads `.env` with `override=True` | Put the key in `.env` |
| `TypeError: unsupported operand type(s) for \|` from `daytona/_utils/errors.py` | Python 3.9 masking a real Daytona API error | Check `DAYTONA_API_KEY`; use Python 3.10+ |
| `DaytonaAuthenticationError` in `scripts/generalize.py` | This script has no local fallback | Supply a Daytona key, or skip it |
| `No module named 'orchestrator'` under AppTest | AppTest does not add the repo root to `sys.path` | `PYTHONPATH=. python …` |
| New `replay/live_*.json` files appear | Streamlit's "Record this run" checkbox is on by default | Harmless; they are gitignored |
| Dashboard LLM card shows "Not Nosana: fallback endpoint" | You are on a non-Nosana endpoint | Expected; the label is honest |

## 5. Code map

| File | Role |
|---|---|
| `dirty_payload.py` | Dirty feed (3 traps: currency strings and null, `CRITICAL_` weights, apostrophes) and `GROUND_TRUTH` |
| `prompts.py` | Naive initial prompt, forward/backward templates, reference evolved prompt |
| `evo_engine.py` | Forward (generate code) and backward (textual gradient), with robust JSON parsing for small models |
| `sandbox_verifier.py` | Daytona sandbox runner with a labelled local-subprocess fallback |
| `ground_truth_checker.py` | Reward 0..1 and per-check failures; the anti-reward-hacking gate |
| `graph_store.py`, `queries.cypher` | Parameterized `MERGE`, contagion query, agent lineage and graph memory |
| `llm_client.py` | OpenAI-compatible client: primary (Nosana) and fallback endpoints |
| `orchestrator.py` | The loop as an event stream (`run_evolution`), replay save/load, CLI |
| `web_server.py` + `web/index.html` | Current dashboard (port 8600) |
| `app.py` | Older Streamlit dashboard (port 8501) |
| `parallel_sandbox.py`, `scripts/demo_parallel.py` | Best-of-K fan-out across sandboxes |
| `reference/` | Hand-written naive, partial and robust ETL scripts used by scripted mode and fan-out |
| `replay/` | `golden_run.json` (real live run), `scripted_run.json`, `mock_run.json` |

## 6. Known gaps (post-hackathon TODO)

- **Fallback wording.**
  - When contagion is computed locally (`source=local`) or the engine is `Local Fallback`, some headings still say "in Neo4j / Computed in Cypher" (`app.py` ~646/659).
  - `SCRIPTED_NOTE` (`app.py:41`) says "Daytona and Neo4j executed live".
  - Check `web/index.html` (port 8600) for the same problem. All of this copy should branch on `ev["source"]` and the engine.
- `scripts/smoke_daytona.py` exits 0 on Local Fallback and does not print why. `scripts/demo_parallel.py` announces "Daytona sandboxes" even when it runs on the fallback.
- `scripts/generalize.py` has no local fallback.
- `llm_client.py` uses `load_dotenv(override=True)`. This is intentional, for hot-swapping the endpoint during the demo, but it means shell exports are ignored.
- Commit a Python 3.11 lock file, so that 3.10+ installs are pinned too.
- The Streamlit "Record" checkbox should probably default to off.
