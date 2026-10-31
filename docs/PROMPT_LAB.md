# Prompt Lab: does Epoch 1 always fail and Epoch 2 always pass?

Run at 17:47 on 2026-10-31. Everything ran on the real stack:
- **Model**: `qwen3.8:27b` on the Nosana RTX 5090, reached through `scripts/nosana_relay.py`, thinking off. Every run confirmed `model=qwen3.8:27b`, so the Claude fallback on port 8787 was never used.
- **Sandbox**: one Daytona sandbox (`b60e5301`) with `network_block_all=True` (offline), deleted afterwards.
- **Scoring**: `ground_truth_checker` through `DaytonaSandboxVerifier.verify_etl_script`.
- **Neo4j**: nothing was written.

Reproduce with `.venv/bin/python scripts/nosana_prompt_lab.py 5`. Raw rows, including the full evolved prompt and gradient text, are in `docs/prompt_lab_results.json`.

## Results

| Prompt | Pass | Avg reward | Avg forward (LLM) | Avg sandbox |
|---|---|---|---|---|
| `NAIVE_INITIAL_PROMPT` | **0 / 5** | 0.00 | 5.2 s | 1.41 s |
| Evolved by one live backward pass | **5 / 5** | 1.00 | 9.4 s | 1.39 s |
| `REFERENCE_EVOLVED_PROMPT` (hand-written fallback) | **5 / 5** | 1.00 | 9.6 s | 1.37 s |

The backward pass took 8.9 s. It returned strategy `llm`, which means the model's JSON parsed and needed no rule patch, and produced a 1,798-character prompt.

**Takeaway**: the loop is deterministic enough for a live demo. One backward pass turns 0/5 into 5/5. Expect about 2 epochs in roughly 30 s of model plus sandbox time.

## What the audience will actually see

- **The Epoch 1 error is the same in all 5 runs**:
  `ValueError: could not convert string to float: '1.5M'` (exit code 1)
  - It is **not** `'$12,500,000'`. Even under the naive prompt, qwen3.8 strips `$`, thousands commas and `USD` on its own. It then crashes on the magnitude suffix in ZEISS's `"1.5M EUR"`.
  - The pitch line in `docs/PITCH.md` (0:40–1:05) should quote `'1.5M'`.
  - A stronger line to say on stage: "even a 27B model half-cleans the data and still crashes, and without a sandbox you only find out in production."
- **Gradient text**, first bullets as the model wrote them:
  > - ValueError: could not convert string to float: '1.5M': The prompt instructed to read exposure directly, but the data contains magnitude suffixes (M/K) and currency codes that must be stripped and multiplied before float conversion.
  > - Currency detection failure: The prompt forced 'USD' for all nodes, ignoring the '€' or 'EUR' indicators …
  > - Weight parsing risk: … 'CRITICAL_' …
- **Epoch 2**: exit code 0, every ground-truth check passes, reward 1.0.

## Latency budget for the 3-minute demo

| Step | Time |
|---|---|
| Sandbox warmup (create + upload payload) | about 1.9 s |
| Epoch 1 forward + sandbox | about 5 s + 1.4 s |
| Backward (JSON prompt rewrite) | about 9 s |
| Epoch 2 forward + sandbox | about 9.5 s + 1.4 s |
| **Total, excluding Neo4j** | **about 28 s** |

When two clients hit the relay at once, requests queue and run one at a time, so latency roughly doubles. Close any other Nosana callers, such as replay recording, before going on stage.

## Caveats

- The relay depends on one interactive SSH session to the Nosana node. It reconnects once automatically. If it dies, restart it with `.venv/bin/python scripts/nosana_relay.py`, which takes about 15 s to come up.
- The Nosana public endpoint (`…nos.ci/api/*`) still returns 500 because of the read-only model mount in PID 1. Only the relay path works.
- N = 5 per group is small. Treat it as "stable in practice", not as a statistical result.
