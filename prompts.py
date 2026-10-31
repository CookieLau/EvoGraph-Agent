"""Prompt texts for the EvoGraph-Quant evolution loop.

- NAIVE_INITIAL_PROMPT is used as-is as a system message (NOT a .format() template).
- FORWARD_USER_TEMPLATE and BACKWARD_TEMPLATE ARE .format() templates (literal braces are doubled).
"""
from __future__ import annotations


def _esc(text: str) -> str:
    """Escape literal braces so a block can be embedded in a .format() template."""
    return text.replace("{", "{{").replace("}", "}}")


# Shared sandbox protocol: identical in the naive prompt and in every evolved prompt.
PROTOCOL_BLOCK = """SANDBOX PROTOCOL (mandatory):
- Load the data with: data = json.load(open("payload.json"))   (the file is in the current directory)
- Use only the Python standard library (json, re). No network, no pip installs, no neo4j driver.
- Never hardcode the data in the script.
- Build cleaned = {"nodes": [...], "edges": [...]}:
    node fields: id, name, tier, exposure (float), currency ("USD", "EUR" or None), status
    edge fields: src, dst, rel, weight (float)
- Print exactly one line: print("CLEANED_GRAPH_JSON: " + json.dumps(cleaned))
- The very last line printed must be: ASSERTION_PASSED_GRAPH_COMMITTED"""

# Rules the optimizer must add to the evolved prompt.
PARSING_RULES = """PARSING RULES (the feed is dirty):
1. exposure_raw can be "$12,500,000", "8500000 USD", "€4.2M", "1.5M EUR" or null.
   - null/None -> exposure 0.0 and currency None.
   - currency: "$" or "USD" -> "USD"; "€" or "EUR" -> "EUR".
   - remove currency symbols/codes, spaces and thousands commas; suffix M -> x1,000,000; suffix K -> x1,000.
   - Examples: "€4.2M" -> 4200000.0 EUR; "$12,500,000" -> 12500000.0 USD; "8500000 USD" -> 8500000.0 USD.
2. weight can be a float (0.95) or a string like "CRITICAL_0.98": strip the "CRITICAL_" prefix, then float().
3. Keep every name exactly as given, including apostrophes (e.g. "NVIDIA Corp's Core"). Never strip or replace quotes.
4. Cypher must be parameterized, e.g. "MERGE (n:Entity {id: $id}) SET n += $props" with a params dict.
   Never put values into Cypher strings with f-strings or concatenation.
5. Do not silently swallow errors: if a value cannot be parsed, raise ValueError showing the raw value."""

CODE_ONLY_LINE = "Output ONLY raw Python code. No markdown fences, no explanations."


# Epoch 1: deliberately naive. float() on "$12,500,000" (first node) raises ValueError.
# The f-string example below is valid Python (Cypher map braces are doubled).
NAIVE_INITIAL_PROMPT = (
    """You are a Python developer on a quant desk. Write ONE standalone Python 3 script that loads a
portfolio / supply-chain JSON feed and prepares it for a Neo4j risk graph.

"""
    + PROTOCOL_BLOCK
    + """

PROCESSING STEPS (keep it simple, the feed is already clean and trusted):
1. For every node, read the exposure directly:
       exposure = float(node["exposure_raw"])
2. For every edge, read the weight directly:
       weight = float(edge["weight"])
3. Build Cypher CREATE statements with plain f-strings and collect them in a list called statements, e.g.
       cypher = f"CREATE (n:Entity {{id: '{node['id']}', name: '{node['name']}', exposure: {exposure}}})"
4. Set currency to "USD" for every node.
5. Do not use try/except. The data is trusted, so fail fast.

"""
    + CODE_ONLY_LINE
)


FORWARD_USER_TEMPLATE = """The confidential feed is stored in the file payload.json in the current directory.
Your script must read it with json.load(open("payload.json")). Do NOT copy or hardcode the data below into
the script. It is shown only so you can see the structure.

### FEED STRUCTURE (sample)
{payload}

### SCHEMA NOTES
{schema_hint}

Write the complete standalone Python script now, following the system instructions exactly.
""" + CODE_ONLY_LINE


BACKWARD_TEMPLATE = (
    """You are the Textual Gradient Optimizer (AdalFlow TGD backward engine).
A code-generation agent wrote a Python ETL script from a SYSTEM PROMPT. The script ran inside an isolated,
offline Daytona sandbox and FAILED. Find why, then rewrite the SYSTEM PROMPT so the next script passes.

### CURRENT SYSTEM PROMPT
{prompt}

### GENERATED SCRIPT
{code}

### DAYTONA SANDBOX OUTPUT (exit code + traceback = loss signal)
{feedback}

### VERIFIER FAILURES (ground-truth checks that failed)
{failures}

### DATA SCHEMA (no raw values)
{schema_hint}

### YOUR TASK
1. "textual_gradient": 3 to 5 short bullet lines. Each line = root cause seen in the output -> concrete fix.
   Quote the exact error, e.g. ValueError: could not convert string to float: '$12,500,000'.
2. "updated_prompt": the COMPLETE new system prompt (not a diff). It MUST:
   a) contain this block word for word:
"""
    + _esc(PROTOCOL_BLOCK)
    + """
   b) contain these rules word for word:
"""
    + _esc(PARSING_RULES)
    + """
   c) drop every instruction that caused the failure (e.g. "float(node['exposure_raw']) directly",
      "Do not use try/except", "currency USD for every node", f-string Cypher).
   d) end with: """
    + CODE_ONLY_LINE
    + """

### OUTPUT FORMAT
Return ONLY one JSON object. No markdown, no text before or after. Use \\n for line breaks inside strings.
Example:
{{"textual_gradient": "- ValueError on '$12,500,000': float() on a currency string -> normalize currency first\\n- TypeError on None exposure -> map None to 0.0, currency None\\n- 'CRITICAL_0.98' weight -> strip prefix before float()", "updated_prompt": "You are a Python developer ... (full prompt)"}}
"""
)


# Hand-written evolved prompt: fallback if the backward model returns unusable output.
REFERENCE_EVOLVED_PROMPT = (
    """You are a careful Python data engineer on a quant desk. Write ONE standalone Python 3 script that loads a
dirty portfolio / supply-chain JSON feed, normalizes it, and prepares it for a Neo4j risk graph.

"""
    + PROTOCOL_BLOCK
    + "\n\n"
    + PARSING_RULES
    + "\n\n"
    + CODE_ONLY_LINE
)


def ensure_protocol(prompt: str) -> str:
    """Re-append the sandbox protocol if an evolved prompt dropped it."""
    if "CLEANED_GRAPH_JSON" in prompt and "payload.json" in prompt and "ASSERTION_PASSED_GRAPH_COMMITTED" in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + PROTOCOL_BLOCK + "\n\n" + CODE_ONLY_LINE
