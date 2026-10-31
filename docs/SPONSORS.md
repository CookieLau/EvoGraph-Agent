# EvoGraph-Quant × Daytona × Nosana × Neo4j

EvoGraph-Quant is a closed learning loop, and each sponsor sits on a part of it that cannot be removed:

```
 Nosana GPU (generate)  ->  Daytona offline sandbox (execute = loss)  ->  ground-truth reward
        ^                                                                        |
        |               textual gradient on Nosana (rewrite the prompt)  <-------+
        |                                                                        |
        +------------------  Neo4j Aura (verified graph + contagion + lineage) <-+  (only when reward = 1.0)
```

Every number below was measured tonight and matches `README.md`, `docs/PITCH.md`, `docs/PROMPT_LAB.md` and `replay/golden_run.json`.

---

## Daytona: the execution verifier, which supplies the loss signal

**What we use (API level)**

| Capability | Where |
|---|---|
| `Daytona(DaytonaConfig(api_key, api_url))` client | `sandbox_verifier.py:63`, `parallel_sandbox.py:56` |
| `CreateSandboxFromSnapshotParams(language="python", network_block_all=True, auto_stop_interval, labels)`: a fully offline sandbox | `sandbox_verifier.py:83-85`, `parallel_sandbox.py:97-99`, `scripts/generalize.py:59` |
| `sandbox.fs.upload_file(payload, "payload.json")`: the confidential feed goes in as a file, never into the prompt or the code | `sandbox_verifier.py:92`, `parallel_sandbox.py:106`, `scripts/generalize.py:31` |
| `sandbox.process.code_run(code, timeout=60)`: runs LLM-written code; `exit_code` + `result` (traceback) become the loss | `sandbox_verifier.py:149`, `scripts/generalize.py:33` |
| One sandbox warmed up and reused across epochs (`warmup`), with a fresh payload upload per run | `sandbox_verifier.py:94`, `sandbox_verifier.py:172` |
| `Daytona.delete(sb)` in `finally`, plus label-scoped `cleanup_stale()` | `sandbox_verifier.py:116`, `sandbox_verifier.py:125-136`, `orchestrator.py:303` |
| Fan-out: K candidates in K sandboxes in parallel (`ThreadPoolExecutor`), best-of-K | `parallel_sandbox.py:150` (`verify_many`), `parallel_sandbox.py:161`, `parallel_sandbox.py:191` (`run_best_of_k`) |

**Why it has to be Daytona.** The loop runs code written by an LLM against confidential data. Without an isolated, network-blocked machine, you either cannot execute that code safely, or you execute it somewhere it could exfiltrate the payload. With no execution there is no traceback, so no loss, so no gradient, so no evolution. `network_block_all=True` is what lets us say *the generated code physically cannot leak the feed*. Sub-second creation is what makes a per-epoch sandbox affordable, and parallel creation is what makes best-of-K search possible.

**Measured tonight**
- Sandbox create ≈ **0.5 s**; warm-up including payload upload ≈ **2 s**; `code_run` ≈ **0.4–1.6 s**.
- Golden run: Epoch 1 `exit 1`, `ValueError: could not convert string to float: '1.5M'` → reward 0.0. Epoch 2 `exit 0` → reward 1.0.
- Fan-out: **6 offline sandboxes in 4.9 s** versus **21.0 s** serial (**4.3×**); best candidate robust#1, reward 1.0.
- The sandbox really is offline: from inside it, Aura `:7687` and `pypi.org` both fail DNS (`gaierror`) when `network_block_all=True`.
- Generalisation on a new EV-battery feed ran in a fresh offline sandbox: 0.0, then **0.946** after one more rule.

**15 seconds on stage**
> "That red box is a Daytona sandbox with networking switched off. The model's code runs there and only there. Its crash, exit code 1 and a real traceback, is our loss function. Daytona spins one up in half a second, so we can afford one per epoch, and six at once for best-of-K: 4.9 seconds instead of 21."

---

## Nosana: private inference for both the forward and the backward pass

**What we use (API level)**

| Capability | Where |
|---|---|
| A Nosana GPU job (RTX 5090, 32 GB) serving `qwen3.8:27b` through Ollama, OpenAI-compatible `/v1/chat/completions` | `.env` `LLM_BASE_URL`, `FORWARD_MODEL`, `BACKWARD_MODEL` |
| The Nosana job SSH channel. The job's public Ollama endpoint failed (read-only model mount), so a second Ollama runs inside the same container on `127.0.0.1:11435` and requests are bridged over the session | `scripts/nosana_relay.py:5-9`, `scripts/nosana_relay.py:25`, `scripts/nosana_relay.py:36` |
| Forward pass: Nosana writes the ETL script | `evo_engine.py:330` (`forward_generate_code`) via `llm_client.py:200` (`chat`) |
| Backward pass: Nosana writes the textual gradient and the new prompt in JSON mode, seeing only the schema hint, code and traceback, never the raw payload | `evo_engine.py:367-379` |
| qwen3 handling: `/no_think` and `<think>` stripping | `llm_client.py:147-164` |
| The provider label says "Nosana (RTX 5090 · qwen3.8:27b)" so the UI never mislabels where inference ran | `llm_client.py:58-60` |
| Prompt lab on Nosana: 5 runs per prompt, scored by the ground-truth checker | `scripts/nosana_prompt_lab.py`, `docs/PROMPT_LAB.md` |

**Why it has to be Nosana.** The whole premise is that a quant desk cannot send positions and supplier intelligence to a hosted closed model. Nosana gives us an open-weight model on rented GPU compute that we control, so both the code generation *and* the self-critique stay inside the data boundary. Without it, the forward pass or the optimizer, or both, would have to call an external API, and the "air-gapped" claim collapses.

**Measured tonight**
- Forward: a **101-line script in 5.4 s** (836 tokens). Backward: a **JSON prompt rewrite in 5.1 s**. Two concurrent calls queue (5.4 s + 5.8 s) and never deadlock.
- Golden run: Epoch 1 forward 4.8 s, gradient 8.3 s, Epoch 2 forward 10.7 s; **35 s end to end** including Daytona and Neo4j.
- Prompt lab: NAIVE prompt **0/5** (every run `ValueError ... '1.5M'`). After one real Nosana backward pass (8.9 s, JSON parsed directly), the evolved prompt passes **5/5**, reward 1.0.
- A full live run through the Streamlit app: **32.4–33.9 s**, 2 epochs, 0 exceptions.

**15 seconds on stage**
> "The model you just saw is running on a Nosana RTX 5090: qwen3.8, open weights, about five seconds per call. It writes the code *and* it critiques itself. The confidential feed never touches a hosted model API. It stays between the Nosana node and our machine."

---

## Neo4j: the verified risk graph, contagion maths and the agent's audit trail

**What we use (API level)**

| Capability | Where |
|---|---|
| Uniqueness constraints and indexes (`CREATE CONSTRAINT … IF NOT EXISTS`) | `graph_store.py:39-40` |
| Parameterised batch writes: `UNWIND $rows AS row MERGE (n:Entity {id: row.id})`. This is why `NVIDIA Corp's Core` cannot inject Cypher | `graph_store.py:47-48`, `graph_store.py:62-65`, `graph_store.py:166` (`upsert_graph`) |
| Tier labels and relationship types from a whitelist (no string-built Cypher) | `graph_store.py:65`, `graph_store.py:71` |
| Multi-hop contagion in one query: variable-length paths + `reduce(c = 1.0, r IN relationships(p) \| c * r.weight)`, then exposure × contagion | `graph_store.py:71-72`, `graph_store.py:210` (`contagion_paths`) |
| The agent's own lineage: `(:EvoRun)-[:HAS_EPOCH]->(:Epoch)-[:USED_PROMPT]->(:PromptVersion)-[:EVOLVED_INTO {via_gradient}]->(:PromptVersion)` | `graph_store.py:83-99`, `graph_store.py:225` (`record_epoch`), `graph_store.py:263` (`fetch_lineage`) |
| The graph shown in the UI is read back from Aura (`fetch_graph_for_viz`), not drawn from the payload | `graph_store.py:249-258` |
| Ready-to-paste Aura console queries: contagion, single point of failure, what-if, lineage | `queries.cypher`, `docs/NEO4J_DEMO.md` |
| Only `reward = 1.0` data is committed (orchestrator gate) | `orchestrator.py:273-280` |

**Why it has to be Neo4j.** The risk we care about is multi-hop: quartz mine → optics → lithography → foundry → holding. In a table that is a recursive join. In Neo4j it is one path pattern with `reduce()`, computed where the data lives. The same graph also stores *why* the agent behaves the way it does (every prompt version and the gradient that produced it), which is the audit trail a compliance team asks for. Without Neo4j, contagion would be an in-memory script (our fallback exists only for outages), and there would be no queryable lineage.

**Measured tonight**
- Commit: **6 nodes, 5 relationships** in ≈ **0.6–1.4 s**; contagion query **156–241 ms**.
- Contagion computed in Cypher: NVDA 0.98 × 0.95 × 0.92 × 1.0 = **0.85652 → $10,706,500**; AAPL × 0.88 = **0.7537 → $7,386,628**; total ≈ **$18.1M**.
- Single-point-of-failure query: ZEISS, ASML and TSMC each sit on both holdings' paths, **$22.3M** of exposure behind them.
- Aura holds clean data only: relationship-type whitelist; checked read-only tonight.

**15 seconds on stage**
> "Only verified rows reach Neo4j, through parameterised MERGE. This multiplication, 0.98 × 0.95 × 0.92, happens inside Neo4j, in Cypher: one query, $18.1M at risk, four hops from a sanctioned quarry. And the agent's own evolution is stored in the same graph, so you can audit which prompt produced which data."

---

## Integration depth scorecard

| Sponsor | Capabilities used | Position in the loop | Evidence |
|---|---|---|---|
| **Daytona** | 7: client config, offline create (`network_block_all`), `fs.upload_file`, `process.code_run`, warm reuse, delete + label cleanup, parallel fan-out | **Execute = loss.** Every epoch's reward comes from a Daytona run; without it the loop has no signal | `sandbox_verifier.py`, `parallel_sandbox.py`, `scripts/smoke_daytona.py`, `scripts/demo_parallel.py`, `scripts/generalize.py`, `replay/golden_run.json` |
| **Nosana** | 6: GPU job (RTX 5090), Ollama OpenAI API, job SSH channel relay, forward pass, backward pass (JSON), measured prompt lab | **Generate + optimise.** Both the policy and the optimizer run on Nosana | `llm_client.py`, `evo_engine.py`, `scripts/nosana_relay.py`, `scripts/nosana_prompt_lab.py`, `docs/PROMPT_LAB.md` |
| **Neo4j** | 7: constraints/indexes, `UNWIND … MERGE`, label/type whitelist, variable-length paths + `reduce()`, lineage graph, read-back for the UI, Aura console query pack | **Commit + reason + remember.** The only sink for verified data, and the home of the contagion maths and the audit trail | `graph_store.py`, `queries.cypher`, `docs/NEO4J_DEMO.md`, `scripts/smoke_neo4j.py` |

---

## Roadmap deeper

**Daytona**
- **Snapshots**: bake a "verified ETL runtime" snapshot (pinned libs plus the checker) so every epoch starts from an identical, audited image.
- **Volumes**: mount large vendor feeds read-only instead of uploading per run; keep passing scripts in a versioned volume as a reusable ETL library.
- **Preview links**: host the EvoGraph console itself inside a Daytona sandbox and share it via a signed preview URL with the risk team.

**Nosana**
- **Job-definition deploys**: ship the Ollama/vLLM job JSON with the repo and deploy it from the CLI as part of `setup.sh`, so the inference node is reproducible and exposes the port directly (no relay).
- **Multi-GPU population evolution**: one Nosana job per candidate prompt, feeding Daytona fan-out. Best-of-K at the model level as well as at the program level.

**Neo4j**
- **GraphRAG memory, shipping tonight**: before each backward pass, retrieve past "error → fix" pairs from the lineage graph (`Epoch.error_summary` → `EVOLVED_INTO.via_gradient`) and give them to the optimizer, so a new feed's `HIGH_` prefix is solved with what the agent already learned about `CRITICAL_`.
- **Live contagion monitoring**: rerun the `reduce()` query when a supplier's status flips to `WARNING`, and push the at-risk delta to the desk.
