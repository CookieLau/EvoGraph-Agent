// EvoGraph-Quant — demo queries for the Neo4j Aura console (Query tab).
// Paste one at a time. All read-only.

// 1) Whole verified supply-chain risk graph (switch result view to "Graph")
MATCH (a:Entity)-[r]->(b:Entity)
RETURN a, r, b;

// 2) Multi-hop contagion paths: raw-material shock -> portfolio holdings
//    (contagion = product of dependency weights along the path)
MATCH p = (src:Tier4_RawMaterial)-[*1..8]->(h:Portfolio_Holding)
RETURN p,
       reduce(c = 1.0, r IN relationships(p) | c * r.weight) AS contagion;

// 3) Exposure-at-risk leaderboard (table view)
//    Expected: NVDA 0.85652 -> $10,706,500 | AAPL 0.75374 -> $7,386,628
MATCH p = (src:Tier4_RawMaterial)-[*1..8]->(h:Portfolio_Holding)
WITH h, [n IN nodes(p) | n.name] AS route,
     reduce(c = 1.0, r IN relationships(p) | c * r.weight) AS contagion
RETURN h.id AS holding, h.name AS name, route,
       round(contagion * 100000) / 100000.0 AS contagion,
       round(h.exposure * contagion) AS usd_at_risk
ORDER BY usd_at_risk DESC;

// 4) Single points of failure: suppliers every holding path goes through
MATCH p = (:Tier4_RawMaterial)-[*1..8]->(h:Portfolio_Holding)
UNWIND nodes(p)[1..-1] AS mid
WITH mid, count(DISTINCT h) AS holdings_hit, sum(h.exposure) AS exposure_behind
RETURN mid.id AS supplier, mid.name AS name, mid.status AS status,
       holdings_hit, exposure_behind
ORDER BY holdings_hit DESC, exposure_behind DESC;

// 5) Agent self-evolution lineage: run -> epochs -> prompt versions (Graph view)
MATCH (run:EvoRun)-[:HAS_EPOCH]->(e:Epoch)-[:USED_PROMPT]->(pv:PromptVersion)
OPTIONAL MATCH evo = (pv)-[:EVOLVED_INTO]->(:PromptVersion)
RETURN run, e, pv, evo
ORDER BY e.n;

// 5b) Same lineage as a table: what failed, what the textual gradient said
MATCH (run:EvoRun)-[:HAS_EPOCH]->(e:Epoch)
WITH run, e ORDER BY run.created_at DESC, e.n
RETURN run.run_id AS run_id, e.n AS epoch, e.passed AS passed, e.reward AS reward,
       left(e.error_summary, 120) AS error, left(e.gradient, 160) AS textual_gradient;
