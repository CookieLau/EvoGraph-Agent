from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ground_truth_checker import GROUND_TRUTH, MARKER, check_cleaned_output, extract_cleaned_json  # noqa: E402


def _good_graph() -> dict:
    nodes = [dict(id=k, **v) for k, v in GROUND_TRUTH["nodes"].items()]
    edges = []
    for k, v in GROUND_TRUTH["edges"].items():
        s, d = k.split("->")
        edges.append({"src": s, "dst": d, "rel": v["rel"], "weight": v["weight"]})
    return {"nodes": nodes, "edges": edges}


def _out(graph: dict, marker: bool = True, noise: bool = True) -> str:
    lines = []
    if noise:
        lines.append("Loading payload.json ... 6 nodes, 5 edges")
    lines.append("CLEANED_GRAPH_JSON: " + json.dumps(graph))
    if noise:
        lines.append("total exposure computed")
    if marker:
        lines.append(MARKER)
    return "\n".join(lines)


def _node(graph: dict, nid: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == nid)


def test_perfect_output_passes():
    r = check_cleaned_output(_out(_good_graph()), 0)
    assert r["passed"] is True
    assert r["reward"] == 1.0
    assert r["failures"] == []
    assert r["cleaned"]["nodes"][0]["id"]


def test_tolerates_string_numbers_and_lowercase_currency():
    g = _good_graph()
    _node(g, "SUP:TSMC")["exposure"] = "8,500,000"
    _node(g, "SUP:ASML")["currency"] = "eur"
    g["edges"][0]["weight"] = "0.98"
    assert check_cleaned_output(_out(g), 0)["passed"] is True


def test_missing_m_multiplier_fails():
    g = _good_graph()
    _node(g, "SUP:ASML")["exposure"] = 4.2
    r = check_cleaned_output(_out(g), 0)
    assert r["passed"] is False
    assert 0 < r["reward"] < 1
    assert any("SUP:ASML" in f and "'M' multiplier" in f for f in r["failures"])


def test_lost_apostrophe_fails():
    g = _good_graph()
    _node(g, "HOLDING:NVDA")["name"] = "NVIDIA Corps Core"
    r = check_cleaned_output(_out(g), 0)
    assert r["passed"] is False
    assert any("HOLDING:NVDA" in f and "apostrophe" in f for f in r["failures"])


def test_marker_only_fails():
    r = check_cleaned_output("all good!\n" + MARKER, 0)
    assert r["passed"] is False
    assert r["cleaned"] is None
    assert any("marker printed but CLEANED_GRAPH_JSON missing" in f for f in r["failures"])


def test_crash_fails_with_traceback_last_line():
    out = (
        "Traceback (most recent call last):\n"
        '  File "<stdin>", line 12, in <module>\n'
        "ValueError: could not convert string to float: '$12,500,000'"
    )
    r = check_cleaned_output(out, 1)
    assert r["passed"] is False
    assert r["reward"] < 0.2
    assert any("ValueError: could not convert string to float: '$12,500,000'" in f for f in r["failures"])


def test_nonzero_exit_fails_even_with_perfect_json():
    r = check_cleaned_output(_out(_good_graph()), 1)
    assert r["passed"] is False


def test_critical_weight_hint_and_null_exposure():
    g = _good_graph()
    g["edges"][0]["weight"] = "CRITICAL_0.98"
    _node(g, "RAW:QUARTZ_X")["exposure"] = None
    r = check_cleaned_output(_out(g), 0)
    assert r["passed"] is False
    assert any("CRITICAL_" in f for f in r["failures"])
    assert any("RAW:QUARTZ_X" in f and "null" in f for f in r["failures"])


def test_extract_uses_last_json_line():
    g = _good_graph()
    bad = copy.deepcopy(g)
    bad["nodes"] = []
    out = "CLEANED_GRAPH_JSON: " + json.dumps(bad) + "\nCLEANED_GRAPH_JSON: " + json.dumps(g) + "\n" + MARKER
    assert len(extract_cleaned_json(out)["nodes"]) == 6
    assert extract_cleaned_json("no json here") is None
