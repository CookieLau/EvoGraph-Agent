"""Neo4j graph store for EvoGraph-Quant.

The host (not the sandbox) commits verified, cleaned data to Neo4j Aura.
Every write uses parameterized Cypher + UNWIND batches. Labels and relationship
types come only from fixed whitelists below, never from the data itself.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

# data value -> fixed label / relationship type (whitelists, prevents injection)
TIER_LABELS = {
    "Portfolio_Holding": "Portfolio_Holding",
    "Tier1_Foundry": "Tier1_Foundry",
    "Tier2_Equipment": "Tier2_Equipment",
    "Tier3_Component": "Tier3_Component",
    "Tier4_RawMaterial": "Tier4_RawMaterial",
}
REL_TYPES = {
    "SUPPLIES_MATERIAL": "SUPPLIES_MATERIAL",
    "SUPPLIES_OPTICS": "SUPPLIES_OPTICS",
    "SUPPLIES_LITHOGRAPHY": "SUPPLIES_LITHOGRAPHY",
    "FABRICATES_CHIPS": "FABRICATES_CHIPS",
}
DEFAULT_REL_TYPE = "SUPPLIES"  # anything not whitelisted; original kept in r.rel
# Read queries only follow relationship types this module writes (ignores other writers' data).
OWN_REL_TYPES = sorted(set(REL_TYPES.values()) | {DEFAULT_REL_TYPE})

SOURCE_TIER = "Tier4_RawMaterial"
HOLDING_TIER = "Portfolio_Holding"

SCHEMA_QUERIES = [
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT evorun_id IF NOT EXISTS FOR (r:EvoRun) REQUIRE r.run_id IS UNIQUE",
    "CREATE INDEX entity_tier IF NOT EXISTS FOR (n:Entity) ON (n.tier)",
    "CREATE INDEX epoch_run IF NOT EXISTS FOR (e:Epoch) ON (e.run_id, e.n)",
    "CREATE INDEX prompt_run IF NOT EXISTS FOR (p:PromptVersion) ON (p.run_id, p.v)",
]

UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:Entity {id: row.id})
SET n.name = row.name, n.tier = row.tier, n.exposure = row.exposure,
    n.currency = row.currency, n.status = row.status, n.run_id = $run_id,
    n.updated_at = datetime()
"""

# {label} / {rel_type} are filled ONLY from the whitelists above.
SET_TIER_LABEL = """
UNWIND $ids AS id
MATCH (n:Entity {id: id})
SET n:%s
"""

UPSERT_EDGES = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.src})
MATCH (b:Entity {id: row.dst})
MERGE (a)-[r:%s]->(b)
SET r.rel = row.rel, r.weight = row.weight, r.run_id = $run_id
"""

CONTAGION_QUERY = """
MATCH p = (src:Entity {tier: $source_tier})-[*1..8]->(h:Entity {tier: $holding_tier})
WHERE all(r IN relationships(p) WHERE type(r) IN $rel_types AND r.weight IS NOT NULL)
WITH p, h, reduce(c = 1.0, r IN relationships(p) | c * r.weight) AS contagion
RETURN [n IN nodes(p) | n.id]   AS path,
       [n IN nodes(p) | n.name] AS names,
       contagion,
       h.id AS holding,
       coalesce(h.exposure, 0.0) AS exposure,
       coalesce(h.exposure, 0.0) * contagion AS at_risk
ORDER BY at_risk DESC
"""

RECORD_EPOCH = """
MERGE (run:EvoRun {run_id: $run_id})
  ON CREATE SET run.created_at = datetime()
MERGE (e:Epoch {run_id: $run_id, n: $epoch})
SET e.passed = $passed, e.reward = $reward, e.error_summary = $error_summary,
    e.gradient = $gradient, e.ts = datetime()
MERGE (run)-[:HAS_EPOCH]->(e)
MERGE (pv:PromptVersion {run_id: $run_id, v: $epoch})
SET pv.text = $prompt, pv.chars = size($prompt)
MERGE (e)-[:USED_PROMPT]->(pv)
"""

LINK_PROMPT_VERSIONS = """
MATCH (prev:PromptVersion {run_id: $run_id, v: $prev_v})
MATCH (cur:PromptVersion {run_id: $run_id, v: $cur_v})
OPTIONAL MATCH (pe:Epoch {run_id: $run_id, n: $prev_v})
MERGE (prev)-[ev:EVOLVED_INTO]->(cur)
SET ev.via_gradient = pe.gradient
"""


# Graph memory (GraphRAG-style recall over the agent's own lineage): failed epochs whose run
# was later fixed, newest first. Read-only and parameterized.
RECALL_LESSONS = """
MATCH (run:EvoRun)-[:HAS_EPOCH]->(f:Epoch)
WHERE f.passed = false
  AND ($exclude_run_id IS NULL OR run.run_id <> $exclude_run_id)
  AND coalesce(f.gradient, '') <> ''
MATCH (run)-[:HAS_EPOCH]->(ok:Epoch)
WHERE ok.passed = true AND ok.n > f.n
WITH run, f, min(ok.n) AS fixed_in_epoch
RETURN run.run_id AS run_id, f.n AS epoch, fixed_in_epoch,
       left(coalesce(f.error_summary, ''), 300) AS error_summary,
       left(coalesce(f.gradient, ''), 300) AS gradient,
       toString(f.ts) AS ts
ORDER BY f.ts DESC
LIMIT $limit
"""


def memory_enabled() -> bool:
    """GRAPH_MEMORY=0/false/off disables graph memory; default on."""
    return os.getenv("GRAPH_MEMORY", "1").strip().lower() not in ("0", "false", "off", "no")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class GraphStore:
    def __init__(self) -> None:
        self.uri = os.getenv("NEO4J_URI", "")
        self.user = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME") or "neo4j"
        self.password = os.getenv("NEO4J_PASSWORD", "")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        if not self.uri:
            raise RuntimeError("NEO4J_URI is not set")
        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password),
            connection_timeout=10,
            max_transaction_retry_time=15,
        )
        self._schema_ready = False

    # ---- helpers -------------------------------------------------------
    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        records, _, _ = self.driver.execute_query(query, params, database_=self.database)
        return [r.data() for r in records]

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        for q in SCHEMA_QUERIES:
            self._run(q)
        self._schema_ready = True

    def close(self) -> None:
        try:
            self.driver.close()
        except Exception:
            pass

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- contract API --------------------------------------------------
    def ping(self) -> bool:
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    def reset_demo(self, keep_memory: Optional[bool] = None) -> None:
        """Clear the demo graph. With graph memory on (default), the agent's lineage
        (:EvoRun/:Epoch/:PromptVersion) is kept so later runs can recall past fixes;
        pass keep_memory=False (or GRAPH_MEMORY=0) for a full wipe."""
        if keep_memory is None:
            keep_memory = memory_enabled()
        if keep_memory:
            self._run("MATCH (n:Entity) DETACH DELETE n")
        else:
            self._run(
                "MATCH (n) WHERE n:Entity OR n:EvoRun OR n:Epoch OR n:PromptVersion "
                "DETACH DELETE n"
            )

    def recall_lessons(self, limit: int = 3, exclude_run_id: Optional[str] = None) -> list:
        """Graph memory: past failed epochs that were later fixed, newest first.
        Returns [] on any error (never breaks the loop)."""
        try:
            rows = self._run(RECALL_LESSONS, exclude_run_id=exclude_run_id, limit=int(limit) * 4)
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        seen = set()
        for r in rows:
            key = ((r.get("error_summary") or "").strip(), (r.get("gradient") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "run_id": r.get("run_id"),
                "epoch": r.get("epoch"),
                "fixed_in_epoch": r.get("fixed_in_epoch"),
                "error_summary": key[0],
                "gradient": key[1],
                "ts": r.get("ts"),
            })
            if len(out) >= int(limit):
                break
        return out

    def upsert_graph(self, cleaned: dict, run_id: str) -> dict:
        self.ensure_schema()
        nodes = cleaned.get("nodes") or []
        edges = cleaned.get("edges") or []

        node_rows = [
            {
                "id": str(n.get("id")),
                "name": n.get("name"),
                "tier": n.get("tier"),
                "exposure": _to_float(n.get("exposure")),
                "currency": n.get("currency"),
                "status": n.get("status"),
            }
            for n in nodes
            if n.get("id") is not None
        ]
        self._run(UPSERT_NODES, rows=node_rows, run_id=run_id)

        by_label: Dict[str, List[str]] = {}
        for row in node_rows:
            label = TIER_LABELS.get(row["tier"] or "")
            if label:
                by_label.setdefault(label, []).append(row["id"])
        for label, ids in by_label.items():
            self._run(SET_TIER_LABEL % label, ids=ids)

        by_type: Dict[str, List[dict]] = {}
        for e in edges:
            if e.get("src") is None or e.get("dst") is None:
                continue
            rel = str(e.get("rel") or "")
            rtype = REL_TYPES.get(rel, DEFAULT_REL_TYPE)
            by_type.setdefault(rtype, []).append(
                {"src": str(e["src"]), "dst": str(e["dst"]), "rel": rel,
                 "weight": _to_float(e.get("weight"))}
            )
        rel_count = 0
        for rtype, rows in by_type.items():
            self._run(UPSERT_EDGES % rtype, rows=rows, run_id=run_id)
            rel_count += len(rows)

        return {"nodes": len(node_rows), "rels": rel_count}

    def contagion_paths(self) -> list:
        rows = self._run(CONTAGION_QUERY, source_tier=SOURCE_TIER, holding_tier=HOLDING_TIER,
                         rel_types=OWN_REL_TYPES)
        return [
            {
                "path": r["path"],
                "names": r["names"],
                "contagion": float(r["contagion"]),
                "holding": r["holding"],
                "exposure": float(r["exposure"]),
                "at_risk": float(r["at_risk"]),
            }
            for r in rows
        ]

    def record_epoch(
        self,
        run_id: str,
        epoch: int,
        prompt: str,
        passed: bool,
        reward: float,
        error_summary: str,
        gradient: Optional[str],
    ) -> None:
        self.ensure_schema()
        self._run(
            RECORD_EPOCH,
            run_id=run_id,
            epoch=int(epoch),
            prompt=prompt or "",
            passed=bool(passed),
            reward=float(reward or 0.0),
            error_summary=(error_summary or "")[:4000],
            gradient=gradient,
        )
        if int(epoch) > 1:
            self._run(LINK_PROMPT_VERSIONS, run_id=run_id, prev_v=int(epoch) - 1, cur_v=int(epoch))

    def fetch_graph_for_viz(self) -> dict:
        nodes = self._run(
            "MATCH (n:Entity) RETURN n.id AS id, n.name AS name, n.tier AS tier, "
            "n.exposure AS exposure, n.currency AS currency, n.status AS status ORDER BY n.id"
        )
        edges = self._run(
            "MATCH (a:Entity)-[r]->(b:Entity) WHERE type(r) IN $rel_types "
            "RETURN a.id AS src, b.id AS dst, "
            "coalesce(r.rel, type(r)) AS rel, r.weight AS weight ORDER BY src, dst",
            rel_types=OWN_REL_TYPES,
        )
        return {"nodes": nodes, "edges": edges}

    # ---- extra (optional for UI) ----------------------------------------
    def fetch_lineage(self, run_id: str) -> list:
        """Epochs of one run with their prompt version, oldest first."""
        return self._run(
            "MATCH (:EvoRun {run_id: $run_id})-[:HAS_EPOCH]->(e:Epoch)-[:USED_PROMPT]->(pv:PromptVersion) "
            "RETURN e.n AS epoch, e.passed AS passed, e.reward AS reward, "
            "e.error_summary AS error_summary, e.gradient AS gradient, pv.v AS prompt_version, "
            "pv.chars AS prompt_chars ORDER BY e.n",
            run_id=run_id,
        )
