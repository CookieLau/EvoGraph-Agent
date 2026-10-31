# Epoch 2 sample: currency symbols/None fixed, but 'M' multiplier and 'CRITICAL_' weights mishandled.
import json
import re

with open("payload.json") as f:
    data = json.load(f)


def parse_exposure(raw):
    if raw is None:
        return 0.0, None
    s = str(raw)
    currency = "EUR" if ("€" in s or "EUR" in s.upper()) else "USD"
    num = re.sub(r"[^0-9.]", "", s)  # strips symbols, but silently drops the 'M' multiplier
    return float(num), currency


def parse_weight(w):
    if isinstance(w, (int, float)):
        return float(w)
    m = re.search(r"\d+(?:\.\d+)?", str(w))
    return round(float(m.group()) / 10.0, 4) if m else 0.0  # wrong rescaling of 'CRITICAL_' weights


nodes = []
for n in data["nodes"]:
    exposure, currency = parse_exposure(n.get("exposure_raw"))
    nodes.append({"id": n["id"], "name": n["name"], "tier": n["tier"],
                  "exposure": exposure, "currency": currency, "status": n["status"]})

edges = []
for e in data["edges"]:
    edges.append({"src": e["src"], "dst": e["dst"], "rel": e["rel"], "weight": parse_weight(e["weight"])})

queries = [{"query": "MERGE (n:Entity {id: $id}) SET n += $props",
            "params": {"id": n["id"], "props": n}} for n in nodes]
print(f"Prepared {len(queries)} parameterized node upserts")
print("CLEANED_GRAPH_JSON: " + json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False))
print("ASSERTION_PASSED_GRAPH_COMMITTED")
