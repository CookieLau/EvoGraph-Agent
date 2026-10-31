"""Inline copy of the dirty payload (used only if dirty_payload.py is unavailable)."""
import json

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


def get_raw_payload_str() -> str:
    return json.dumps(CONFIDENTIAL_PORTFOLIO_DATA, indent=2)
