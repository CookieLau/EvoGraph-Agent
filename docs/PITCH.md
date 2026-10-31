# EvoGraph-Quant — 3-Minute Demo Pitch

> One-liner: **A private, self-evolving ETL agent. An open model on a Nosana GPU writes the cleaning code, an offline Daytona sandbox runs it, the agent learns from its own crash, and only verified data reaches the Neo4j risk graph.**

Numbers to memorise (all measured tonight):
- **Model**: `qwen3.8:27b` on a **Nosana RTX 5090 (32 GB)**, thinking off. Writing a 101-line script takes **5.4 s**; rewriting the prompt (JSON) takes **5.1 s**.
- **Daytona**: an offline sandbox (`network_block_all=True`) warms up in about **2 s** and runs a script in **1.6–2.5 s**. **Fan-out**: 6 sandboxes run in parallel in **4.9 s**, versus **21.0 s** run one after another (**4.3× faster**).
- **Golden run** (`replay/golden_run.json`, real Nosana + Daytona + Neo4j): **35 s** end to end. Epoch 1 exits 1 with `ValueError: '1.5M'` (reward 0.0). The gradient takes 8.3 s. Epoch 2 reaches reward 1.0. 6 nodes and 5 relationships are committed.
- **Contagion** is the product of the edge weights along a path:
  - NVDA 0.98 × 0.95 × 0.92 × 1.0 = **0.857** → **$10,706,500** at risk
  - AAPL × 0.88 = **0.754** → **$7,386,628** at risk
  - **Total ≈ $18.1M**, all traced back to one quartz mine in Spruce Pine, NC.
- **Prompt lab** (`docs/PROMPT_LAB.md`, Nosana `qwen3.8:27b` + real offline Daytona): the NAIVE prompt passes **0/5**. All 5 runs exit 1 with `ValueError: could not convert string to float: '1.5M'`. One real backward pass (8.9 s, its JSON parsed on the first try) produces a prompt that passes **5/5** with reward 1.0. The demo loop takes about **28 s** before Neo4j.
- **Generalisation**: the final script from the scripted run gets **0.0** on a new EV-battery feed (new `HIGH_` prefix) and **0.946** after one more rule (`docs/GENERALIZATION.md`).

---

## 0. Pre-flight (5 minutes before going on stage)

```bash
cd EvoGraph-Agent
curl -s http://127.0.0.1:11435/v1/models | head -c 200   # Nosana relay up? (owner: daytona-8c)
#   if not: .venv/bin/python scripts/nosana_relay.py &      # first connect ~15 s
curl -s http://127.0.0.1:8787/v1/models | head -c 200    # stand-in LLM up (fallback only)
.venv/bin/python scripts/smoke_llm.py                    # display must say "Nosana (RTX 5090 · qwen3.8:27b)"
```
- Tab 0: the launch video `video/EvoGraph-Quant_launch.mp4`. Tab 1: the demo page (http://localhost:8600, served by web_server.py). Mode = **Live on Nosana**, max epochs = 3, press **Reset the Neo4j demo graph** once.
- Tab 2: `slides/index.html` on the sponsor summary slide. Tab 3 (optional): the Neo4j Aura console, Query tab, with the lineage query (bottom of §1) pasted in.
- Have **Replay → `golden_run.json`** one click away in the sidebar.
- **Stop every other Nosana call** before going on stage: the prompt lab, other sessions, test scripts. The single GPU queues requests, so a parallel call **doubles the latency** in front of the judges.

---

## 1. Script (3:00 = 70 s launch video + 110 s live, timed against the real 35-second run)

Every beat names the sponsor doing the work. Say the product name and point at it.

| Time | Say | Do / where to look |
|---|---|---|
| **0:00–1:10** Launch video | *(Optional, one line over the opening frame)* "Seventy seconds on how it works, then we run it live." Otherwise let the video carry it: pain → contagion → the **Nosana → Daytona → Neo4j** loop → evidence. | Tab 0: `video/EvoGraph-Quant_launch.mp4` full screen (or `video/index.html`). No audio track: you talk, or stay silent. |
| **1:10–1:20** Launch | "Now live. The model is a 27B open model on a **Nosana** RTX 5090. The code runs in an offline **Daytona** sandbox. Only verified data goes into **Neo4j**. One button, hands off." | Switch to Tab 1 (the app). Click **Run evolution**. |
| **1:20–1:23** *(live t+0–3 s)* Warm-up | "**Daytona** just created an offline sandbox, `network_block_all=True`, in about two seconds. The confidential feed went in as a file, and the generated code physically can't reach the network." | The warm-up line shows the sandbox id and that it is offline. |
| **1:23–1:29** *(t+3–9)* Epoch 1 forward | "**Nosana** is writing epoch 1 right now: qwen3.8 on an RTX 5090, from a deliberately naive prompt. About five seconds, on rented private compute, not a hosted API." | Epoch 1 card: the generated code appears. Nosana badge: "Nosana, RTX 5090, qwen3.8:27b". |
| **1:29–1:32** *(t+9–12)* Epoch 1 fails | "That red box is a **Daytona** sandbox with networking switched off. Exit code 1: `ValueError: could not convert string to float: '1.5M'`. The model cleaned half the data and still crashed on `'1.5M EUR'`. That traceback is real, straight out of Daytona. It's our loss function." *(5 of 5 lab runs fail the same way. If the screen shows another value, read that one.)* | Sandbox output tab: the traceback; every check failing; reward 0.0. |
| **1:32–1:40** *(t+12–20)* Gradient | "The traceback goes back to **Nosana**, the same GPU. It writes a textual gradient and rewrites its own prompt. It sees the schema, the code and the error, never the confidential values." | The gradient box. |
| **1:40–1:51** *(t+20–31)* Epoch 2 forward | "While **Nosana** writes epoch 2, look at the prompt diff: `M`/`K` multipliers, nulls to 0, strip `CRITICAL_`, parameterised Cypher. Nobody typed that. It came from a Daytona crash." | Prompt diff tab (v1 → v2). |
| **1:51–1:53** *(t+31–33)* Epoch 2 passes | "Back in the **Daytona** sandbox: exit 0. We don't trust the script's own 'PASSED': the host checks every node and edge against ground truth. Reward 1.0." | Epoch 2 card: every check passing. |
| **1:53–1:55** *(t+33–35)* Commit | "Only now does anything touch **Neo4j**, through parameterised `UNWIND … MERGE`, so the apostrophe in `NVIDIA Corp's Core` can't inject anything." | The Neo4j badge shows 6 nodes and 5 relationships. |
| **1:55–2:20** Contagion | "This multiplication happens inside **Neo4j**, in Cypher: one path query with `reduce()`. Sanctioned quartz mine → Zeiss → ASML → TSMC → NVIDIA: 0.98 × 0.95 × 0.92 × 1.0 = 0.857, **$10.7M at risk**. Apple **$7.4M**. **$18.1M**, four hops from anything a PM looks at. Neo4j also stores the agent's own lineage: every prompt version and the gradient that produced it." | Right panel: the graph read back from Aura, contagion path in red, the at-risk table with the full dollar amounts. |
| **2:20–2:40** Daytona fan-out | "**Daytona** is built for parallel agents. Six candidate scripts, six offline sandboxes, at once: **4.9 seconds** versus **21** one by one. We keep the best reward. That's best-of-K search over programs." | Click **Fan out to 6 sandboxes**: a grid of 6 rewards (0.0 / 0.733 / 1.0) and parallel vs serial time. |
| **2:40–3:00** Close: three sponsors, one line each | "**Nosana**: the model and its self-critique stay on private GPU compute. **Daytona**: every line of AI-written code runs offline, and its crash is the reward. **Neo4j**: only verified data enters the graph, where contagion is computed and every evolution step is audited. That's EvoGraph-Quant." | Stay on the green final state. Hit the slides' sponsor page if there's a pause. |

If the live run needs a third epoch (about 10 s more), cut the fan-out to one sentence: "Daytona can run six of these at once: 4.9 seconds versus 21." Not-overfit (EV feed, 0 → 0.946) moves to Q&A.

### Video, demo and slides: running order

1. **Before going on stage**, open three tabs or windows in this order:
   - Tab 0: `video/EvoGraph-Quant_launch.mp4` (QuickTime full screen), or `video/index.html`.
   - Tab 1: the app at http://localhost:8600, mode **Live on Nosana**, "Reset the Neo4j demo graph" pressed once.
   - Tab 2: `slides/index.html`, parked on the sponsor summary slide. Tab 3: the Aura console with the lineage query pasted (optional).
2. **0:00–1:10**: play the launch video. It explains the why and the how, so the live part only has to prove it.
3. **1:10–2:40**: Live demo in the app (the table above). Name the sponsor at every beat.
4. **2:40–3:00**: close with the three one-liners.
5. **Q&A**: go to the slides. Use the **Daytona / Nosana / Neo4j pages** and the scorecard (`docs/SPONSORS.md`) for "how deeply did you use our product?" questions, and the evidence slide (0/5 → 5/5) for "is it luck?".
6. If the Wi-Fi dies: the video and slides are local files; switch the app to **Replay → golden_run.json** (real Nosana + Daytona + Neo4j, recorded tonight) and say so.

### Cypher for the Aura tab (also in `queries.cypher`)
```cypher
// Agent evolution lineage
MATCH p = (r:EvoRun)-[*1..3]-(x)
WHERE x:Epoch OR x:PromptVersion
RETURN p LIMIT 50;

// Contagion (backup if the UI panel misbehaves)
MATCH p = (r:Entity {tier:'Tier4_RawMaterial'})-[*]->(h:Entity {tier:'Portfolio_Holding'})
WITH p, h, reduce(s = 1.0, x IN relationships(p) | s * x.weight) AS contagion
RETURN [n IN nodes(p) | n.name] AS path, contagion, h.exposure * contagion AS at_risk
ORDER BY at_risk DESC;
```

---

## 2. Judging criteria: what to say

**Completeness**
- "It's measured, not anecdotal. The naive prompt passes 0/5 on Nosana. After one real backward pass, the evolved prompt passes 5/5, reward 1.0."
- "It runs end to end, live: Nosana generates the code, Daytona executes it offline, the host checks it against ground truth, a textual gradient rewrites the prompt, the next epoch passes, and Neo4j gets the commit and runs the contagion query. 35 seconds."
- "Replay is a stage safety net, and the UI labels it. `golden_run.json` is a real Nosana + Daytona + Neo4j run, recorded earlier tonight."

**Innovation**
- "The sandbox is the reward function. Real stack traces become prompt updates through a textual-gradient optimizer. It's reinforcement learning at the prompt level, with no fine-tuning and no human labels. Daytona fan-out turns it into best-of-K search."
- "The agent's own learning history lives in the same graph as the business data, so it can be audited."

**Real-life pain**
- "Supply-chain contagion is how the 2021 chip shortage and the 2024 Spruce Pine hurricane hit portfolios. The data is messy and confidential, which is exactly where cloud agents are banned and private models break."

**Sponsor usage** (full API-level detail with file:line in `docs/SPONSORS.md`)
- **Daytona**: "Every script runs in a Daytona sandbox with `network_block_all=True`. The payload goes in through `fs.upload_file`, runs through `process.code_run`, and the exit code plus traceback become the loss. `parallel_sandbox.py` fans out six sandboxes at once: 4.9 s versus 21 s serial."
- **Nosana**: "`qwen3.8:27b` runs on a Nosana RTX 5090. It does both the forward pass (code) and the backward pass (gradient), about 5 seconds each. Confidential data only passes through the Nosana node and our machine."
- **Neo4j**: "Aura takes parameterised `UNWIND … MERGE` writes of verified data only, computes multi-hop contagion with one Cypher `reduce`, and stores the agent's lineage of runs, epochs and prompt versions."

---

## 3. Likely judge questions

1. **"Why not just hand-write the cleaning code?"**
   "For one feed, you would. A desk has hundreds of vendor feeds, and their formats drift every quarter. Here the agent repairs its own instructions from the crash, and every fix is checked in a sandbox before it touches the graph."

2. **"Did it just pass by luck?"**
   "No. We measured it, with Nosana `qwen3.8:27b` and real offline Daytona. The naive prompt failed 5 out of 5 times, every time with the same `ValueError` on `'1.5M'`. We ran one real backward pass: 8.9 seconds, and its JSON parsed directly. The evolved prompt then passed 5 out of 5, all with reward 1.0. The failure is systematic and the fix is systematic. It's in `docs/PROMPT_LAB.md`."

3. **"Isn't it overfitting to this one payload?"**
   "What gets updated is general rules, like 'normalise currency strings with M/K suffixes', not values. The optimizer never sees the raw numbers. We tested on a second feed: the old script crashed on a new prefix (reward 0), and one more learned rule took it to 0.946. That's the point of online evolution."

4. **"Can the reward be hacked? The script prints its own PASSED marker."**
   "The marker alone earns nothing. The host parses `CLEANED_GRAPH_JSON` and checks every node and edge against ground truth: exposure within 1%, weights within 0.005, names byte-exact. Nothing reaches Neo4j until that check passes."

5. **"Is one traceback really a 'gradient'?"**
   "It's a textual gradient in the TextGrad / AdalFlow sense, not a numeric derivative. It's a natural-language critique of a parameter, the system prompt, conditioned on a loss signal, the sandbox failure. One sample per step is noisy, which is why we verify every step in the sandbox instead of trusting it."

6. **"Why not use Nosana's public endpoint directly?"**
   "Tonight the job's public Ollama endpoint was broken: its model directory was mounted read-only, and that Ollama version fails trying to write a new manifest folder. The GPU itself was fine. So we started a second Ollama inside the same container and reached it through the SSH channel Nosana gives every job. `scripts/nosana_relay.py` bridges HTTP to that channel on `127.0.0.1:11435`. Same RTX 5090, same model, about 5 s per call, and the data still only touches the Nosana node and our machine. In production you'd just expose the job port."

7. **"Is a 27B model really a 'small' model?"**
   "Size isn't the point. What matters is that the model is open-weight and runs on compute we rent, inside the same security boundary as the data. The loop doesn't depend on the model: swapping it is one line in `.env`. A weaker model would probably need more epochs, and the sandbox and checker would still catch every mistake."

8. **"What exactly runs where?"**
   "Generated code runs only inside an offline Daytona sandbox. The model runs on Nosana. Neo4j writes happen on our machine after verification, because the sandbox can't reach the network by design. The optimizer only sees the schema, the code and the traceback."

9. **"How does this scale?"**
   "Daytona fan-out is already here: K candidates in K sandboxes, keep the best. Nosana scales the model, Neo4j's contagion query works on the full supplier graph, and the lineage tells compliance which prompt version produced which data."

---

## 4. If something breaks on stage

Decide which mode you're in before the demo, and **say it out loud** if it isn't Live (Nosana).

| Symptom | Action | What to say (truthfully) |
|---|---|---|
| Nosana slow (over 20 s per call) or venue Wi-Fi bad | Sidebar → **Replay** → `replay/golden_run.json` | "I'll replay a run we recorded earlier tonight. It's real Nosana, Daytona and Neo4j output, just not generated this second." |
| Nosana relay dropped | `llm_client` automatically falls back to the stand-in at `127.0.0.1:8787` (`agent_api_server.py`, a hosted Claude model). The badge then reads **"Agent API stand-in"**. Or restart the relay with `.venv/bin/python scripts/nosana_relay.py` (about 15 s). | "The Nosana tunnel dropped, so this step is served by a stand-in hosted model. The badge says so. In the real setup that's the Nosana GPU you saw." **Never call the stand-in Nosana.** |
| Any LLM unavailable | Sidebar → **Live (real sandbox + scripted LLM)** | "The model text here is pre-written, but the Daytona sandbox runs and the Neo4j writes are happening live right now." |
| Daytona API timeout | The verifier falls back automatically; the badge shows **"Local Fallback"** | "Venue Wi-Fi is struggling, so this run is in a local isolated subprocess. Same code, same checks." |
| Neo4j write fails | The orchestrator computes contagion in memory and shows the table; then run the backup Cypher in Aura | "Aura is unreachable right now. These numbers come from the same computation." |
| Epoch 2 also fails | Let it run to epoch 3 (max epochs = 3) | "A second failure, so the agent learns again. That's the loop working." |

**Honesty rules:**
- Never present Replay as Live. The UI shows the mode.
- `golden_run.json`: everything is real (Nosana `qwen3.8:27b`, Daytona, Neo4j), recorded earlier tonight.
- `scripted_run.json`: 3 epochs (reward 0.0 / 0.733 / 1.0). The **sandbox runs and Neo4j writes are real**; the **LLM text was written ahead of time**.
- If the stand-in at 8787 served any step, say so. It's a hosted model, not private compute.

---

## 中文要点（台上速记）

- **一句话**：私有的自进化 ETL Agent。Nosana GPU 上的开源模型写清洗代码，断网的 Daytona sandbox 执行，Agent 从自己的报错里学习，只有验证通过的数据才写进 Neo4j。
- **整体顺序**：0:00–1:10 放发布视频 → 1:10–2:40 Live demo，每一步都点出是哪家赞助商在干活（Nosana 写代码和梯度、Daytona 断网执行、Neo4j 写入和算传染）→ 2:40–3:00 三家赞助商各一句收尾 → 问答时用幻灯片的赞助商页和 `docs/SPONSORS.md` 的评分表。
- **台上首选 Live (Nosana)**：一轮约 10 秒加 sandbox 时间，全程约 35 秒。节奏：1:10 点 Run evolution → +3 秒 sandbox 就绪 → +9 秒 Epoch 1 代码生成 → +12 秒报错（报错原文照屏幕念）→ +20 秒梯度 → +31 秒 Epoch 2 代码（等待时讲 prompt diff）→ +33 秒通过 → +35 秒写入 Neo4j。
- **Epoch 1 报错的说法**：`ValueError: could not convert string to float: '1.5M'`。"27B 模型自作聪明洗了一半数据（去掉了 $、逗号和 USD），还是崩在 '1.5M EUR' 上；没有 sandbox，这种错只能在生产环境里才发现。"
- **评委问"会不会只是碰巧"**：PROMPT_LAB 实测，NAIVE prompt 0/5 通过，5 次都是 exit 1、报 '1.5M'；一次真实 backward（8.9 秒，JSON 直接解析成功）进化出的 prompt 5/5 通过，reward 1.0；演示流程约 28 秒，不算 Neo4j。
- **上台前提醒**：关掉其他所有 Nosana 调用（PROMPT_LAB、别的会话、测试脚本），否则请求排队，延迟翻倍。
- **要背的数**：qwen3.8:27b 跑在 Nosana RTX 5090 上，每次调用约 5 秒；Daytona 并行 6 个 sandbox 用 4.9 秒，串行要 21 秒（4.3 倍）；NVDA $10.7M + AAPL $7.4M = **$18.1M**；换 EV 电池新数据 0 分，加一条规则后 0.946。
- **新增问答**：为什么不用 Nosana 公网端点？公网 Ollama 的模型目录被只读挂载，导致服务报错，GPU 本身没问题；我们通过 Nosana 给每个 job 提供的 SSH 通道连到同一块 GPU，数据只经过 Nosana 节点和本机。27B 算小模型吗？关键不在大小，而在开源、在自己租的算力上、和数据处于同一安全边界；整套流程和模型无关，换模型只改 `.env` 一行。
- **翻车预案**：网络不好 → Replay 播 golden_run（全部真实，只是预先录的）；Nosana 中转断了 → 自动切到 8787 的 Claude 顶替服务，**必须说明不是 Nosana**；完全没有 LLM → 用 "real sandbox + scripted LLM" 模式，sandbox 和 Neo4j 仍是现场真实执行。
