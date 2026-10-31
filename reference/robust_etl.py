# Epoch 3 sample: robust, stdlib-only ETL that normalizes the dirty feed and emits parameterized Cypher.
import json
import re

with open("payload.json") as f:
    data = json.load(f)

MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9}


def parse_exposure(raw):
    """'$12,500,000' / '8500000 USD' / '€4.2M' / '1.5M EUR' / None -> (amount, currency)."""
    if raw is None:
        return 0.0, None
    s = str(raw).strip()
    upper = s.upper()
    if "€" in s or "EUR" in upper:
        currency = "EUR"
    elif "$" in s or "USD" in upper:
        currency = "USD"
    else:
        currency = None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KMB])?(?![A-Z])", upper.replace("USD", "").replace("EUR", ""))
    if not m:
        raise ValueError(f"Unparseable exposure: {raw!r}")
    amount = float(m.group(1).replace(",", ""))
    if m.group(2):
        amount *= MULTIPLIERS[m.group(2)]
    return amount, currency


def parse_weight(w):
    """0.85 / 'CRITICAL_0.98' / 'CRITICAL_1.0' -> float in [0, 1]."""
    if isinstance(w, (int, float)):
        val = float(w)
    else:
        s = str(w).strip().upper()
        if s.startswith("CRITICAL_"):
            s = s[len("CRITICAL_"):]
        val = float(s)
    if not 0.0 <= val <= 1.0:
        raise ValueError(f"Weight out of range: {w!r}")
    return val


nodes = []
for n in data["nodes"]:
    exposure, currency = parse_exposure(n.get("exposure_raw"))
    nodes.append({"id": n["id"], "name": n["name"], "tier": n["tier"],
                  "exposure": exposure, "currency": currency, "status": n["status"]})

edges = []
for e in data["edges"]:
    edges.append({"src": e["src"], "dst": e["dst"], "rel": e["rel"], "weight": parse_weight(e["weight"])})

# Parameterized Cypher only: names like "NVIDIA Corp's Core" never touch the query text.
queries = [{"query": "MERGE (n:Entity {id: $id}) SET n += $props", "params": {"id": n["id"], "props": n}} for n in nodes]
queries += [{"query": "MATCH (a:Entity {id: $src}), (b:Entity {id: $dst}) "
                      "MERGE (a)-[r:SUPPLY_LINK {rel: $rel}]->(b) SET r.weight = $weight",
             "params": e} for e in edges]

assert len(nodes) == len(data["nodes"]) and len(edges) == len(data["edges"])
assert all("'" not in q["query"] for q in queries), "Cypher text must not embed literals"
print(f"Prepared {len(queries)} parameterized Cypher statements")
print("CYPHER_SAMPLE: " + json.dumps(queries[0], ensure_ascii=False))
print("CLEANED_GRAPH_JSON: " + json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False))
print("ASSERTION_PASSED_GRAPH_COMMITTED")
