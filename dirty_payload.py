"""Confidential dirty portfolio / supply-chain feed + ground truth for the verifier.

The payload deliberately contains three traps that break naive ETL code:
  1. Apostrophes in entity names / notes  -> breaks f-string Cypher literals
  2. Mixed currency strings and nulls     -> float() raises ValueError / TypeError
  3. Prefixed weights ("CRITICAL_0.98")    -> float() raises ValueError
"""
from __future__ import annotations

import json
from typing import Dict, List

CONFIDENTIAL_PORTFOLIO_DATA = {
    "report_id": "Q4_ALPHA_DESK_INTERNAL_ONLY",
    "desk": "Systematic Equity & Credit Contagion",
    "nodes": [
        {"id": "HOLDING:NVDA", "name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure_raw": "$12,500,000", "status": "ACTIVE"},
        {"id": "HOLDING:AAPL", "name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure_raw": "$9,800,000", "status": "ACTIVE"},
        {"id": "SUP:TSMC", "name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure_raw": "8500000 USD", "status": "ACTIVE"},
        {"id": "SUP:ASML", "name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure_raw": "€4.2M", "status": "ACTIVE"},
        {"id": "SUP:ZEISS", "name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure_raw": "1.5M EUR", "status": "WARNING"},
        {"id": "RAW:QUARTZ_X", "name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure_raw": None, "status": "SANCTION_OR_FORCE_MAJEURE"},
    ],
    "edges": [
        {"src": "RAW:QUARTZ_X", "dst": "SUP:ZEISS", "rel": "SUPPLIES_MATERIAL", "weight": "CRITICAL_0.98", "note": "Sole source for 'Ultra-Pure' crucible"},
        {"src": "SUP:ZEISS", "dst": "SUP:ASML", "rel": "SUPPLIES_OPTICS", "weight": 0.95, "note": "High-NA EUV Mirrors"},
        {"src": "SUP:ASML", "dst": "SUP:TSMC", "rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92, "note": "EUV Fleet Maintenance"},
        {"src": "SUP:TSMC", "dst": "HOLDING:NVDA", "rel": "FABRICATES_CHIPS", "weight": "CRITICAL_1.0", "note": "CoWoS-L & Blackwell Wafer's"},
        {"src": "SUP:TSMC", "dst": "HOLDING:AAPL", "rel": "FABRICATES_CHIPS", "weight": 0.88, "note": "3nm N3E Node"},
    ],
}


def get_raw_payload_str() -> str:
    """Full confidential payload (only ever sent to the private forward model / sandbox)."""
    return json.dumps(CONFIDENTIAL_PORTFOLIO_DATA, indent=2, ensure_ascii=False)


def get_schema_hint() -> str:
    """Schema + kinds of dirt, with NO real values or names (safe for the optimizer pass)."""
    return """Payload file: payload.json (UTF-8 JSON object)
Top level: report_id (str), desk (str), nodes (list), edges (list)
nodes[]: id (str, unique, e.g. "<PREFIX>:<TICKER>"), name (str), tier (str enum), exposure_raw (str | null), status (str enum)
edges[]: src (str, node id), dst (str, node id), rel (str, UPPER_SNAKE relationship type), weight (float | str), note (str)

Kinds of dirt present (values withheld):
- name / note strings may contain single quotes (apostrophes) -> must never be interpolated into Cypher string literals
- exposure_raw is heterogeneous: currency symbol prefix ("$" / "€") with thousands separators, OR plain number with a
  trailing ISO code ("USD" / "EUR"), OR an "M" (million) magnitude suffix with optional currency code, OR null
- weight is either a float OR a string with an upper-case label prefix and underscore before the number (e.g. "<LABEL>_<float>")

Expected cleaned output per node: id, name (unchanged, byte-exact), tier, exposure (float, native currency amount,
null -> 0.0), currency ("USD" | "EUR" | null), status. Per edge: src, dst, rel, weight (float in [0, 1])."""


GROUND_TRUTH = {
    "nodes": {
        "HOLDING:NVDA": {"name": "NVIDIA Corp's Core", "tier": "Portfolio_Holding", "exposure": 12500000.0, "currency": "USD", "status": "ACTIVE"},
        "HOLDING:AAPL": {"name": "Apple Inc.", "tier": "Portfolio_Holding", "exposure": 9800000.0, "currency": "USD", "status": "ACTIVE"},
        "SUP:TSMC": {"name": "Taiwan Semi (TSMC)", "tier": "Tier1_Foundry", "exposure": 8500000.0, "currency": "USD", "status": "ACTIVE"},
        "SUP:ASML": {"name": "ASML Holding N.V.", "tier": "Tier2_Equipment", "exposure": 4200000.0, "currency": "EUR", "status": "ACTIVE"},
        "SUP:ZEISS": {"name": "Carl Zeiss SMT's Optics", "tier": "Tier3_Component", "exposure": 1500000.0, "currency": "EUR", "status": "WARNING"},
        "RAW:QUARTZ_X": {"name": "Spruce Pine High-Purity Quartz", "tier": "Tier4_RawMaterial", "exposure": 0.0, "currency": None, "status": "SANCTION_OR_FORCE_MAJEURE"},
    },
    "edges": {
        "RAW:QUARTZ_X->SUP:ZEISS": {"rel": "SUPPLIES_MATERIAL", "weight": 0.98},
        "SUP:ZEISS->SUP:ASML": {"rel": "SUPPLIES_OPTICS", "weight": 0.95},
        "SUP:ASML->SUP:TSMC": {"rel": "SUPPLIES_LITHOGRAPHY", "weight": 0.92},
        "SUP:TSMC->HOLDING:NVDA": {"rel": "FABRICATES_CHIPS", "weight": 1.0},
        "SUP:TSMC->HOLDING:AAPL": {"rel": "FABRICATES_CHIPS", "weight": 0.88},
    },
}

# Contagion from the Tier-4 raw-material shock to each holding = product of edge weights along the path.
EXPECTED_CONTAGION = {
    "HOLDING:NVDA": {
        "path": ["RAW:QUARTZ_X", "SUP:ZEISS", "SUP:ASML", "SUP:TSMC", "HOLDING:NVDA"],
        "contagion": 0.85652,
        "exposure": 12500000.0,
        "at_risk": 10706500.0,
    },
    "HOLDING:AAPL": {
        "path": ["RAW:QUARTZ_X", "SUP:ZEISS", "SUP:ASML", "SUP:TSMC", "HOLDING:AAPL"],
        "contagion": 0.7537376,
        "exposure": 9800000.0,
        "at_risk": 7386628.48,
    },
}
EXPECTED_TOTAL_AT_RISK = 18093128.48

TRAPS: List[Dict[str, object]] = [
    {
        "id": "TRAP_1_CYPHER_QUOTE_INJECTION",
        "title": "Single-quote Cypher injection",
        "description": "Entity names and notes contain apostrophes (\"NVIDIA Corp's Core\", \"Carl Zeiss SMT's Optics\", "
                       "\"'Ultra-Pure'\", \"Wafer's\"). String-interpolated Cypher literals break or become injectable.",
        "fields": ["nodes[].name", "edges[].note"],
        "affected_ids": ["HOLDING:NVDA", "SUP:ZEISS", "RAW:QUARTZ_X->SUP:ZEISS", "SUP:TSMC->HOLDING:NVDA"],
        "exception": "CypherSyntaxError (on a real Neo4j) / SyntaxError from the script's own literal check",
        "fix": "Parameterized Cypher ($props) - never f-string user data into queries; keep names byte-exact.",
    },
    {
        "id": "TRAP_2_DIRTY_CURRENCY_AND_NULL",
        "title": "Heterogeneous currency strings + null exposure",
        "description": "exposure_raw mixes \"$12,500,000\", \"8500000 USD\", \"€4.2M\", \"1.5M EUR\" and null. "
                       "float() on these raises immediately.",
        "fields": ["nodes[].exposure_raw"],
        "affected_ids": ["HOLDING:NVDA", "HOLDING:AAPL", "SUP:TSMC", "SUP:ASML", "SUP:ZEISS", "RAW:QUARTZ_X"],
        "exception": "ValueError: could not convert string to float: '$12,500,000'  |  TypeError: float() argument must be a string or a real number, not 'NoneType'",
        "fix": "Regex normalizer: strip symbols/commas/ISO codes, apply 'M' x1e6 multiplier, detect currency, null -> 0.0.",
    },
    {
        "id": "TRAP_3_PREFIXED_WEIGHT",
        "title": "Label-prefixed dependency weights",
        "description": "Edge weight mixes floats (0.95) with strings like \"CRITICAL_0.98\" / \"CRITICAL_1.0\".",
        "fields": ["edges[].weight"],
        "affected_ids": ["RAW:QUARTZ_X->SUP:ZEISS", "SUP:TSMC->HOLDING:NVDA"],
        "exception": "ValueError: could not convert string to float: 'CRITICAL_0.98'",
        "fix": "Strip the '<LABEL>_' prefix before float(); validate 0 <= weight <= 1.",
    },
]


if __name__ == "__main__":
    print(get_raw_payload_str())
