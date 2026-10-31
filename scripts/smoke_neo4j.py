"""Smoke test: GraphStore against the real Neo4j Aura instance.

Run:  .venv/bin/python scripts/smoke_neo4j.py
Steps: reset -> upsert cleaned graph -> contagion (asserts expected numbers)
       -> record 2 epochs -> fetch graph + lineage.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_store import GraphStore  # noqa: E402

GT_NODES = {
    "HOLDING:NVDA": {"name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure": 12500000.0, "currency": "USD", "status": "ACTIVE"},
    "HOLDING:AAPL": {"name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure": 9800000.0, "currency": "USD", "status": "ACTIVE"},
    "SUP:TSMC": {"name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure": 8500000.0, "currency": "USD", "status": "ACTIVE"},
    "SUP:ASML": {"name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure": 4200000.0, "currency": "EUR", "status": "ACTIVE"},
    "SUP:ZEISS": {"name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure": 1500000.0, "currency": "EUR", "status": "WARNING"},
    "RAW:QUARTZ_X": {"name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure": 0.0, "currency": None, "status": "SANCTION_OR_FORCE_MAJEURE"},
}
GT_EDGES = {
    "RAW:QUARTZ_X->SUP:ZEISS": {"rel": "SUPPLIES_MATERIAL", "weight": 0.98},
    "SUP:ZEISS->SUP:ASML": {"rel": "SUPPLIES_OPTICS", "weight": 0.95},
    "SUP:ASML->SUP:TSMC": {"rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92},
    "SUP:TSMC->HOLDING:NVDA": {"rel": "FABRICATES_CHIPS", "weight": 1.0},
    "SUP:TSMC->HOLDING:AAPL": {"rel": "FABRICATES_CHIPS", "weight": 0.88},
}


def cleaned_from_gt() -> dict:
    nodes = [dict(id=k, **v) for k, v in GT_NODES.items()]
    edges = []
    for key, v in GT_EDGES.items():
        src, dst = key.split("->")
        edges.append({"src": src, "dst": dst, "rel": v["rel"], "weight": v["weight"]})
    return {"nodes": nodes, "edges": edges}


def main() -> int:
    t0 = time.time()
    run_id = "smoke-" + time.strftime("%H%M%S")
    with GraphStore() as gs:
        assert gs.ping(), "Neo4j ping failed"
        print(f"ping ok ({time.time() - t0:.1f}s)")

        gs.reset_demo()
        t = time.time()
        stats = gs.upsert_graph(cleaned_from_gt(), run_id)
        print(f"upsert {stats} in {(time.time() - t) * 1000:.0f} ms")
        assert stats == {"nodes": 6, "rels": 5}, stats

        t = time.time()
        paths = gs.contagion_paths()
        print(f"contagion query in {(time.time() - t) * 1000:.0f} ms")
        for p in paths:
            print(f"  {' -> '.join(p['path'])}  contagion={p['contagion']:.5f}  at_risk=${p['at_risk']:,.0f}")
        by_holding = {p["holding"]: p for p in paths}
        assert len(paths) == 2, paths
        assert abs(by_holding["HOLDING:NVDA"]["at_risk"] - 10706500) < 1, by_holding["HOLDING:NVDA"]
        assert abs(by_holding["HOLDING:AAPL"]["at_risk"] - 7386628) < 1, by_holding["HOLDING:AAPL"]
        assert paths[0]["holding"] == "HOLDING:NVDA"
        assert "NVIDIA Corp's Core" in by_holding["HOLDING:NVDA"]["names"]  # apostrophe survives

        gs.record_epoch(run_id, 1, "NAIVE PROMPT v1", False, 0.0,
                        "ValueError: could not convert string to float: '$12,500,000'",
                        "Use a regex currency normalizer; strip CRITICAL_ prefixes.")
        gs.record_epoch(run_id, 2, "EVOLVED PROMPT v2", True, 1.0, "", None)
        lineage = gs.fetch_lineage(run_id)
        print("lineage:", [(r["epoch"], r["passed"], r["prompt_version"]) for r in lineage])
        assert [r["epoch"] for r in lineage] == [1, 2]
        evo = gs._run(
            "MATCH (a:PromptVersion {run_id:$r, v:1})-[ev:EVOLVED_INTO]->(b:PromptVersion {run_id:$r, v:2}) "
            "RETURN ev.via_gradient AS g", r=run_id)
        assert evo and evo[0]["g"], evo
        print("EVOLVED_INTO v1->v2 via_gradient ok")

        g = gs.fetch_graph_for_viz()
        print(f"fetch_graph_for_viz: {len(g['nodes'])} nodes, {len(g['edges'])} edges")
        assert len(g["nodes"]) == 6 and len(g["edges"]) == 5

    print(f"SMOKE_NEO4J_OK total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
