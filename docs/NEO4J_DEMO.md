# Neo4j Aura 现场演示清单（约 30–40 秒）

> 全部查询都是**只读 MATCH**，现场随便点，不会改库。写库只由 `graph_store.py` 完成。
> 数据模型（见 CONTRACT.md）：`(:Entity {id,name,tier,exposure,currency,status})`，tier 同时作为 label（如 `:Tier4_RawMaterial`）；
> 关系上有 `weight` 属性（关系类型可能是 `RISK_LINK` 或 `SUPPLIES_*` / `FABRICATES_CHIPS`，下面查询**不写死关系类型**，两种都兼容）；
> 演化谱系：`:EvoRun` / `:Epoch` / `:PromptVersion`。

---

## 0. 上台前准备（提前 5 分钟做完）

1. 打开 https://console.neo4j.io → 选实例 **8b48f46c** → 点 **Query**（Neo4j 新版 Query 工具）。
   用户名 `neo4j`，密码在本机 `.env` 的 `NEO4J_PASSWORD`（**不要投屏 .env**，提前登录好）。
2. 先跑一次 Demo（或回放）让 `graph_store.py` 把清洗后的图写进去，然后在 Query 里执行 §1 确认有 6 个点 5 条边。
3. 把 §1–§4 的查询存进左侧 **Saved Cypher**（收藏夹），现场直接点，不要现场打字。
4. 让图好看：
   - **Aura Query**：结果图右侧/上方的 label 图例里，点 `Portfolio_Holding`、`Tier4_RawMaterial` 等 label → 改颜色、caption 选 `name`；关系 caption 选 `weight`。颜色照 `assets/neo4j_style.grass` 的配色（持仓绿 `#00CC96`、晶圆厂蓝 `#636EFA`、设备紫 `#AB63FA`、组件橙 `#FFA15A`、原材料红 `#EF553B`、PromptVersion 金 `#FECB52`）。
   - **Neo4j Browser**（备选，支持 GraSS 文件）：打开 https://browser.neo4j.io ，连接 URL 填 `.env` 里的 `NEO4J_URI`，登录后输入 `:style`，把 `assets/neo4j_style.grass` **拖进样式面板**即可一次性套用全部配色。
   - 浏览器缩放 125%，结果面板最大化，点 "Fit to screen"。
5. **Explore（Bloom）**备选：console → 实例 → **Explore** → 搜索框输入 `Entity` → 右侧 Legend 里对每个 tier label 设颜色和 caption=name；用 "Rule-based" 让 `status = 'SANCTION_OR_FORCE_MAJEURE'` 的点变大变红。

---

## 1. 全景图（开场 5 秒）

```cypher
// 整张供应链风险传染图
MATCH p = (a:Entity)-[r]->(b:Entity)
RETURN p
```

预期：6 个 Entity、5 条带 weight 的边，从红色的 Spruce Pine 石英 → Zeiss → ASML → TSMC → NVDA / AAPL。

---

## 2. 哇点 ①：最短传染链（shortestPath）

```cypher
// 原材料断供 → 核心持仓 NVDA 的最短传染链 + 传染系数
MATCH (src:Entity {id: 'RAW:QUARTZ_X'}), (dst:Entity {id: 'HOLDING:NVDA'})
MATCH p = shortestPath((src)-[*..6]->(dst))
RETURN p,
       [n IN nodes(p) | n.name] AS chain,
       round(reduce(s = 1.0, x IN relationships(p) | s * x.weight), 4) AS contagion
```

预期：4 跳链路，`contagion = 0.8565`（0.98 × 0.95 × 0.92 × 1.0）。
台词："一个四级原材料的断供，经过 4 跳，把 85.6% 的冲击传到了我们 1250 万美元的 NVDA 持仓。"

---

## 3. 哇点 ②：What-if —— 如果 Zeiss 断供会怎样？

```cypher
// 假设 SUP:ZEISS 停产：下游哪些持仓受冲击、冲击多大
MATCH (z:Entity {id: 'SUP:ZEISS'})
MATCH p = (z)-[*1..6]->(h:Entity)
WHERE h.tier = 'Portfolio_Holding' AND all(n IN nodes(p) WHERE n:Entity)
WITH h, max(reduce(s = 1.0, x IN relationships(p) | s * x.weight)) AS hit
WITH h, hit, coalesce(h.exposure, h.exposure_usd, 0.0) AS exposure
RETURN h.name AS holding,
       round(hit, 4) AS contagion,
       exposure,
       round(hit * exposure) AS at_risk_usd
ORDER BY at_risk_usd DESC
```

预期：NVDA `0.874` → **$10,925,000**；AAPL `0.7691` → **$7,537,376**；合计约 **$18.46M**。
想要图而不是表：把最后的 `RETURN ...` 换成 `MATCH p2 = (z)-[*1..6]->(h) RETURN p2`。

---

## 4. 哇点 ③：持仓风险 Top 榜（按 at_risk 排序）

```cypher
// 所有"四级原材料 → 持仓"的传染路径，按受影响金额排序
MATCH p = (r:Entity {tier: 'Tier4_RawMaterial'})-[*1..6]->(h:Entity {tier: 'Portfolio_Holding'})
WHERE all(n IN nodes(p) WHERE n:Entity)
WITH h, p, reduce(s = 1.0, x IN relationships(p) | s * x.weight) AS contagion
WITH h, p, contagion, coalesce(h.exposure, h.exposure_usd, 0.0) AS exposure
RETURN h.name AS holding,
       [n IN nodes(p) | n.id] AS chain,
       round(contagion, 4) AS contagion,
       exposure,
       round(contagion * exposure) AS at_risk_usd
ORDER BY at_risk_usd DESC
```

预期：
| holding | contagion | at_risk_usd |
|---|---|---|
| NVIDIA Corp's Core | 0.8565 | 10,706,500 |
| Apple Inc. | 0.7537 | 7,386,628 |

（若 tier 只存在 label 上、没有存成属性，把 `{tier: '...'}` 改成 `(r:Tier4_RawMaterial)` / `(h:Portfolio_Holding)`。）

---

## 5. 加分：单点故障（卡脖子节点）

```cypher
// 哪个中间节点一断，暴露的持仓最多
MATCH p = (r:Entity {tier: 'Tier4_RawMaterial'})-[*1..6]->(h:Entity {tier: 'Portfolio_Holding'})
WHERE all(n IN nodes(p) WHERE n:Entity)
UNWIND nodes(p)[1..-1] AS choke
RETURN choke.name AS choke_point, choke.tier AS tier,
       count(DISTINCT h) AS holdings_exposed
ORDER BY holdings_exposed DESC, tier
```

预期：TSMC、ASML、Zeiss 各自都暴露 2 个持仓 —— "整条链没有冗余供应商"。

---

## 6. Agent 自我进化谱系（证明"进化过程本身也进了图"）

```cypher
// 本次 Run 的 Epoch 与 Prompt 版本谱系（关系类型不写死）
MATCH (run:EvoRun)
WITH run ORDER BY coalesce(run.started_at, run.ts, run.run_id) DESC LIMIT 1
MATCH p = (run)-[*1..3]-(x)
WHERE x:Epoch OR x:PromptVersion
RETURN p
```

```cypher
// 表格版：每个 Epoch 的结果
MATCH (e:Epoch)
RETURN e.run_id AS run, e.epoch AS epoch, e.passed AS passed, e.reward AS reward,
       left(coalesce(e.error_summary, ''), 80) AS error
ORDER BY run DESC, epoch
```

台词："红色的 Epoch 1 失败，金色的 Prompt v2 是 Daytona 报错反推出来的，Epoch 2 通过后才允许写入这张风险图。"

---

## 演示节奏（Neo4j 段，约 35 秒）

1. 点 §1 全景（5s）→ 2. 点 §2 最短传染链，念 0.8565（10s）→ 3. 点 §3 What-if，念 "$18.46M"（10s）→ 4. 点 §6 进化谱系收尾（10s）。
如果网络卡：直接切回 Streamlit 右栏，那里展示的是同一份 `contagion_paths()` 结果。
