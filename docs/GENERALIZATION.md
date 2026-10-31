# Generalization: does the evolved script transfer to an unseen feed?

Real Daytona runs (offline sandbox, `network_block_all=True`), scored host-side against `data/scenario_ev_battery_truth.json` (15 nodes / 18 edges, EV battery chain). Re-run with `.venv/bin/python scripts/generalize.py [--code FILE]`.

| Script | Exit | Checks passed | Reward |
|---|---|---|---|
| `reference/robust_etl.py`, the epoch-3 winner, unchanged | 1 | 0 / 36 | **0.0**: `ValueError: could not convert string to float: 'HIGH_0.8'` (a severity prefix it has never seen) |
| Same script plus one epoch-4 rule (strip *any* `XXX_` prefix) | 0 | 35 / 37 | **0.946**: only `¥` → `JPY` is missing (PANASONIC, SUMITOMO) |

- The epoch-3 script copes with every `$ / € / USD / EUR / M / K / null / CRITICAL_` variant it was evolved on, plus new forms such as `$0.75M`, `2,400,000 USD`, `1.2K` and `" 0.66 "`. It still crashes on the first unseen dirt pattern.
- That crash is the point. The sandbox traceback becomes the next **loss signal**. One more online epoch adds a general prefix rule and lifts reward from 0.0 to 0.946. The two remaining checker failures (`currency None != 'JPY'`) are precise enough for the following epoch to add the `¥` mapping.
- Takeaway for the judges: the system does not claim a one-shot universal parser. It claims **online self-evolution**: every new dirty feed is verified in an air-gapped Daytona sandbox, and every failure is turned into a prompt update, with no human in the loop.
