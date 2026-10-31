"""EvoGraph-Quant demo dashboard (Streamlit).

Consumes the event stream defined in CONTRACT.md, either live from
orchestrator.run_evolution() or from a replay/*.json file.
"""
from __future__ import annotations

import base64
import difflib
import glob
import html
import importlib.util
import json
import os
import time
import traceback
from typing import Dict, List, Optional

import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

try:
    from streamlit_agraph import Config, Edge, Node, _agraph  # _agraph lets us pass a key
except Exception:  # pragma: no cover
    _agraph = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY_DIR = os.path.join(HERE, "replay")
ASSETS = os.path.join(HERE, "assets")
REPLAY_PRIORITY = ["golden_run.json", "scripted_run.json", "mock_run.json"]

MODE_LIVE = "Live run on the Nosana LLM"
MODE_SCRIPTED = "Live sandbox and Neo4j, scripted LLM"
MODE_REPLAY = "Replay a recorded run"
SCRIPTED_NOTE = "LLM steps pre-authored; Daytona and Neo4j executed live"

# Ink palette, hex twins of the CSS tokens for the graph canvas (vis.js does not read CSS variables)
INK = {  # hex twins of brand/tokens.css (Ink) for the graph canvas; vis.js does not read CSS variables
    "ink": "#0D0D0D", "ink2": "#292929", "muted": "#525252", "rule": "#DBDBD8",
    "paper": "#FEFDFB", "paper2": "#F6F5F2", "paper3": "#ECEBE7", "accent": "#00746D", "risk": "#B9402C",
}


def asset(*names: str) -> Optional[str]:
    for n in names:
        p = os.path.join(ASSETS, n)
        if os.path.exists(p):
            return p
    return None


st.set_page_config(page_title="EvoGraph-Quant", page_icon=asset("favicon.png"), layout="wide")


@st.cache_data(show_spinner=False)
def b64_file(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")

# ----------------------------------------------------------------------------- styling
CSS_BODY = """
/* Hallmark · theme: ink (dealt 3/3, seed self-evolving-financial-risk-graph-dbb2-91dc53b1-2026-10-31-r0) · palette and faces: brand/tokens.css (by request, shared with slides and video) · genre: editorial · redesign: full · surface: app screen (Streamlit) · signature: the at-risk figure in the accent · v2.0.0 */
/* Hallmark · pre-emit critique: P4 H4 E4 S4 R4 V4 · weakest: S, a framework dashboard leaves little room for a signature; the exposure-at-risk figure is set large in the risk colour */
/* Hallmark · waive 22 · ink, greys and tier nodes at zero chroma · a risk desk notice is printed in plain black, teal marks verified and red marks risk */
:root {
  --space-2xs: 2px; --space-xs: 4px; --space-sm: 8px; --space-md: 14px; --space-lg: 24px;
  --text-xs: 0.78rem; --text-sm: 0.88rem; --text-md: 1rem; --text-lg: 1.06rem;
  --radius-pill: 999px; --dur-micro: 120ms;
}
html, body { overflow-x: clip; scrollbar-color: var(--rule) var(--paper); }
html, body, .stApp, .stMarkdown, .stText, button, input, textarea, select, label, p, li,
[data-testid="stSidebar"], [data-testid="stWidgetLabel"], [data-baseweb="tab"] { font-family: var(--font-body); }
.stApp { background: var(--paper); color: var(--ink-2); }
[data-testid="stHeader"] { background: var(--paper); }
[data-testid="stSidebar"] { background: var(--paper-2); border-right: var(--hair) solid var(--rule); }
h1, h2, h3, h4 { font-family: var(--font-display); font-weight: var(--display-weight); color: var(--ink);
  letter-spacing: var(--tracking-display); line-height: 1.08; overflow-wrap: break-word; min-width: 0; text-wrap: balance; }
h2 { font-size: 1.5rem; }
code, pre, kbd, .stCode, [data-testid="stCode"] * { font-family: var(--font-mono); }
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1440px; margin: 0 auto; }
a { color: var(--ink); font-weight: 600; } a:hover { text-decoration-color: var(--ok); }
::selection { background: var(--accent-soft); color: var(--ink); }
:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; transition: none; }
button:focus-visible, [role="tab"]:focus-visible, [role="radio"]:focus-visible, input:focus-visible,
summary:focus-visible, [data-baseweb="select"]:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.stButton > button { border-radius: var(--radius); font-weight: 600; border: var(--hair) solid var(--rule);
  background: var(--paper-2); color: var(--ink); transition: border-color var(--dur-micro) ease; }
.stButton > button:hover { border-color: var(--ink); color: var(--ink); }
.stButton > button:active { background: var(--rule); border-color: var(--ink); }
.stButton > button[kind="primary"] { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.stButton > button[kind="primary"]:hover { background: var(--ink-2); border-color: var(--ink-2); color: var(--paper); }
.stButton > button[kind="primary"]:active { background: var(--ink-2); }
.stButton > button:disabled, .stButton > button[aria-disabled="true"] { background: var(--paper-2); color: var(--muted); border-color: var(--rule); cursor: not-allowed; }
[data-baseweb="tab-highlight"] { background: var(--ink); }
[data-baseweb="tab"][aria-selected="true"] { color: var(--ink); }
[data-testid="stExpander"] details { border: var(--hair) solid var(--rule); border-radius: var(--radius); background: var(--paper); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important; scroll-behavior: auto !important; }
}
.eg-hero { border-bottom: var(--hair) solid var(--ink); padding-bottom: var(--space-lg); margin-bottom: var(--space-md); }
.eg-hero h1 { font-size: clamp(2.2rem, 3.4vw, 3.3rem); line-height: 1.02; margin: 0 0 var(--space-sm) 0; padding: 0; }
.eg-lede { font-size: var(--text-lg); line-height: 1.55; color: var(--ink-2); max-width: 62ch; margin: 0; }
.eg-sub { color: var(--muted); font-size: var(--text-sm); margin: var(--space-2xs) 0 var(--space-md) 0; line-height: 1.5; }
.eg-tag { display: inline-flex; align-items: center; padding: var(--space-2xs) var(--space-sm); border-radius: var(--radius-pill);
  font-size: var(--text-xs); font-weight: 600; background: var(--paper-2); color: var(--ink-2); white-space: nowrap; }
.eg-tag.in { background: var(--ok-soft); color: var(--ok); }
.eg-tag.fail { background: var(--fail-soft); color: var(--fail); }
.eg-tag.out { background: transparent; box-shadow: inset 0 0 0 var(--hair) var(--rule); color: var(--muted); }
.eg-tag.on { background: var(--ink); color: var(--paper); }
.eg-ruled { border-top: var(--hair) solid var(--ink); margin: 0 0 var(--space-lg) 0; }
.eg-row { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr) minmax(0, 1.6fr) auto; gap: var(--space-md);
  align-items: baseline; padding: var(--space-sm) 0; border-bottom: var(--hair) solid var(--rule); font-size: var(--text-sm); }
.eg-row .n { font-weight: 600; color: var(--ink); }
.eg-row .v { color: var(--ink-2); overflow-wrap: anywhere; }
.eg-row .d { color: var(--muted); overflow-wrap: anywhere; }
.eg-steps { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: var(--space-sm); margin: var(--space-xs) 0 var(--space-lg) 0; }
.eg-step { border-top: var(--hair) solid var(--rule); padding-top: var(--space-xs); font-size: var(--text-xs); color: var(--muted); min-width: 0; }
.eg-step .num { display: block; font-family: var(--font-display); font-weight: var(--display-weight); font-size: 1.35rem;
  letter-spacing: var(--tracking-display); color: var(--muted); line-height: 1.1; }
.eg-step.done { border-top-color: var(--ok); color: var(--ink-2); } .eg-step.done .num { color: var(--ok); }
.eg-step.active { border-top: 2px solid var(--ink); color: var(--ink); } .eg-step.active .num { color: var(--ink); }
.eg-step.fail { border-top-color: var(--fail); color: var(--ink-2); } .eg-step.fail .num { color: var(--fail); }
.eg-step .s { display: block; margin-top: var(--space-2xs); }
.eg-epoch-h { display: flex; flex-wrap: wrap; gap: var(--space-sm); align-items: baseline; border-top: var(--hair) solid var(--ink);
  padding-top: var(--space-sm); margin-top: var(--space-md); }
.eg-epoch-h .t { font-family: var(--font-display); font-weight: var(--display-weight); font-size: 1.35rem; color: var(--ink); }
.eg-diff { font-family: var(--font-mono); font-size: var(--text-xs); line-height: 1.45; background: var(--paper-2);
  border: var(--hair) solid var(--rule); border-radius: var(--radius); padding: var(--space-sm); max-height: 360px; overflow: auto; white-space: pre-wrap; }
.eg-diff .add { background: var(--ok-soft); color: var(--ok); display: block; }
.eg-diff .del { background: var(--fail-soft); color: var(--fail); display: block; text-decoration: line-through; }
.eg-diff .ctx { color: var(--muted); display: block; }
.eg-check { display: grid; grid-template-columns: auto minmax(0, 0.9fr) minmax(0, 2fr); gap: var(--space-sm); align-items: baseline;
  font-size: var(--text-sm); padding: var(--space-xs) 0; border-bottom: var(--hair) solid var(--rule); }
.eg-check .n { font-weight: 600; color: var(--ink); } .eg-check .dd { color: var(--muted); overflow-wrap: anywhere; }
.eg-fail-line { font-family: var(--font-mono); font-size: var(--text-xs); color: var(--fail); padding: var(--space-xs) 0;
  border-bottom: var(--hair) solid var(--rule); overflow-wrap: anywhere; }
.eg-well { background: var(--paper-2); border: var(--hair) solid var(--rule); border-radius: var(--radius);
  padding: var(--space-md); color: var(--ink-2); font-size: var(--text-sm); line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.eg-note { border: var(--hair) solid var(--rule); border-radius: var(--radius); padding: var(--space-sm) var(--space-md);
  color: var(--ink-2); font-size: var(--text-sm); margin: var(--space-sm) 0; background: var(--paper); }
.eg-note.in { background: var(--ok-soft); color: var(--ok); border-color: var(--ok-soft); }
.eg-note.fail { background: var(--fail-soft); color: var(--fail); border-color: var(--fail-soft); }
.eg-big { background: var(--paper-2); border: var(--hair) solid var(--rule); border-radius: var(--radius);
  padding: var(--space-md) var(--space-lg); margin-bottom: var(--space-md); }
.eg-big .k { font-size: var(--text-sm); color: var(--ink-2); }
.eg-big .v { font-family: var(--font-display); font-size: clamp(2.6rem, 4vw, 3.6rem); font-weight: var(--display-weight);
  color: var(--accent); line-height: 1.0; margin: var(--space-xs) 0; }
.eg-big .d { font-size: var(--text-sm); color: var(--muted); }
.eg-path { padding: var(--space-sm) 0; border-bottom: var(--hair) solid var(--rule); font-size: var(--text-sm); color: var(--ink-2); }
.eg-path .fig { font-family: var(--font-mono); color: var(--ink); }
.eg-flag { margin: 0 0 var(--space-md) 0; }
.eg-graph-cap { display: flex; gap: var(--space-sm); align-items: baseline; font-size: var(--text-sm); color: var(--muted); margin: var(--space-xs) 0; }
.eg-fan { width: 100%; border-collapse: collapse; font-size: var(--text-sm); border-top: var(--hair) solid var(--ink); }
.eg-fan th { text-align: left; font-weight: 400; color: var(--muted); padding: var(--space-xs) var(--space-sm) var(--space-xs) 0; border-bottom: var(--hair) solid var(--rule); }
.eg-fan td { padding: var(--space-sm) var(--space-sm) var(--space-sm) 0; border-bottom: var(--hair) solid var(--rule); color: var(--ink-2); }
.eg-fan td.fig { font-family: var(--font-mono); color: var(--ink); }
.eg-fan tr.best td { font-weight: 600; color: var(--ink); }
.eg-memory { border-top: var(--hair) solid var(--rule); margin: var(--space-xs) 0 var(--space-sm) 0; }
.eg-memory .h { font-size: var(--text-sm); font-weight: 600; color: var(--ink); padding: var(--space-xs) 0; }
.eg-memory .l { font-size: var(--text-xs); color: var(--ink-2); padding: var(--space-xs) 0; border-bottom: var(--hair) solid var(--rule); overflow-wrap: anywhere; }
.eg-memory .g { color: var(--muted); display: block; }
.eg-wordmark { font-family: var(--font-display); font-weight: var(--display-weight); font-size: 1.4rem; color: var(--ink); margin: 0 0 var(--space-sm) 0; }
"""

# Brand tokens are the single source shared with slides/ and video/; read them live, fall back to a copy.
TOKENS_FALLBACK = """
@import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap");
:root {
  --paper: #F4F1EA; --paper-2: #EBE6DA; --ink: #1C1B19; --ink-2: #4A4740; --muted: #8A857A; --rule: #D6CFC0;
  --accent: #C2410C; --accent-soft: #F3D9C9; --ok: #2F6B4F; --ok-soft: #D5E6DA; --fail: #A61B1B; --fail-soft: #F2D4D0; --warn: #A16207;
  --node-holding: #2F6B4F; --node-t1: #3F5E8C; --node-t2: #6B4E8A; --node-t3: #A16207; --node-t4: #C2410C;
  --font-display: "Fraunces", Georgia, "Times New Roman", serif;
  --font-body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --radius: 10px; --hair: 1.5px;
}
"""


def brand_tokens_css() -> str:
    try:
        with open(os.path.join(HERE, "brand", "tokens.css"), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return TOKENS_FALLBACK


def _hallmark_first(tokens: str, body: str) -> str:
    """Keep the Hallmark stamp as the first lines of the injected CSS, then the brand @import and tokens."""
    lines = body.strip().splitlines()
    stamp, rest = lines[:3], lines[3:]
    imports = [l for l in tokens.splitlines() if l.strip().startswith("@import")]
    tok_rest = "\n".join(l for l in tokens.splitlines() if not l.strip().startswith("@import"))
    return "\n".join(stamp + imports) + "\n" + tok_rest + "\n" + "\n".join(rest)


CSS = "<style>\n" + _hallmark_first(brand_tokens_css(), CSS_BODY) + "\n</style>"

st.markdown(CSS, unsafe_allow_html=True)

TIER_COLORS = {  # brand/tokens.css --node-*: holdings black, tiers grey, the sanctioned raw material in risk red
    "Portfolio_Holding": "#0D0D0D",
    "Tier1_Foundry": "#333333",
    "Tier2_Equipment": "#525252",
    "Tier3_Component": "#747474",
    "Tier4_RawMaterial": "#B9402C",
}
TIER_LEVEL = {"Tier4_RawMaterial": 0, "Tier3_Component": 1, "Tier2_Equipment": 2, "Tier1_Foundry": 3, "Portfolio_Holding": 4}

STEPS = [
    ("forward", "Forward pass on the LLM"),
    ("sandbox", "Daytona sandbox"),
    ("verify", "Ground-truth verifier"),
    ("gradient", "Textual gradient"),
    ("prompt", "Prompt update"),
    ("neo4j", "Neo4j commit"),
    ("contagion", "Multi-hop contagion"),
]


def note(text: str, kind: str = "") -> None:
    """A plain notice on the paper; 'in' is the ok colour, 'fail' the fail colour."""
    st.markdown('<div class="eg-note %s">%s</div>' % (kind, text), unsafe_allow_html=True)


def tag(text: str, kind: str = "") -> str:
    return '<span class="eg-tag %s">%s</span>' % (kind, esc(text))


# ----------------------------------------------------------------------------- helpers
def fmt_money(x: Optional[float], cur: Optional[str] = "USD") -> str:
    if x is None:
        return "n/a"
    sym = {"USD": "$", "EUR": "€"}.get((cur or "").upper(), "$" if cur else "")
    x = float(x)
    if abs(x) >= 1e9:
        return "%s%.2fB" % (sym, x / 1e9)
    if abs(x) >= 1e6:
        return "%s%.1fM" % (sym, x / 1e6)
    if abs(x) >= 1e3:
        return "%s%.1fK" % (sym, x / 1e3)
    return "%s%.0f" % (sym, x)


def fmt_full(x: Optional[float], cur: Optional[str] = "USD") -> str:
    """Whole-unit amount with thousands separators, e.g. $10,706,500."""
    if x is None:
        return "n/a"
    sym = {"USD": "$", "EUR": "\u20ac"}.get((cur or "").upper(), "$" if cur else "")
    return sym + "{:,.0f}".format(round(float(x)))


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def list_replays() -> List[str]:
    files = [os.path.basename(p) for p in glob.glob(os.path.join(REPLAY_DIR, "*.json"))]
    ordered = [f for f in REPLAY_PRIORITY if f in files]
    ordered += sorted([f for f in files if f not in ordered], reverse=True)
    return ordered


def load_events(name: str) -> List[dict]:
    with open(os.path.join(REPLAY_DIR, name)) as f:
        data = json.load(f)
    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    return list(data)


def derive(events: List[dict]) -> dict:
    s = {
        "epochs": {}, "warmup": None, "neo4j": None, "contagion": None, "graph": None,
        "done": None, "errors": [], "passed_epoch": None, "last": None, "start": None,
        "fwd_model": None, "bwd_model": None, "engine": None,
        "fwd_display": None, "fwd_fallback": False, "scripted": False, "note": None,
    }
    for ev in events:
        t = ev.get("type")
        s["last"] = ev
        ep = ev.get("epoch")
        if ep is not None:
            s["epochs"].setdefault(ep, {})
        if t == "start":
            s["start"] = ev
            s["scripted"] = bool(ev.get("scripted"))
            s["note"] = ev.get("note")
        elif t == "warmup":
            s["warmup"] = ev
            s["engine"] = ev.get("engine")
        elif t == "forward":
            s["epochs"][ep]["forward"] = ev
            s["fwd_model"] = ev.get("model") or s["fwd_model"]
            s["fwd_display"] = ev.get("display") or s["fwd_display"]
            s["fwd_fallback"] = bool(ev.get("fallback_used")) or s["fwd_fallback"]
        elif t == "sandbox":
            s["epochs"][ep]["sandbox"] = ev.get("result", {})
            s["engine"] = ev.get("result", {}).get("engine") or s["engine"]
        elif t == "gradient":
            s["epochs"][ep]["gradient"] = ev
            s["bwd_model"] = ev.get("model") or s["bwd_model"]
        elif t == "memory":
            s["epochs"][ep]["memory"] = ev
        elif t == "passed":
            s["passed_epoch"] = ep
        elif t == "neo4j_commit":
            s["neo4j"] = ev
        elif t == "contagion":
            s["contagion"] = ev.get("paths") or []
        elif t == "graph":
            s["graph"] = ev.get("graph")
        elif t == "done":
            s["done"] = ev
        elif t == "error":
            s["errors"].append(ev)
    return s


def step_states(s: dict, running: bool) -> Dict[str, str]:
    st_ = {k: "" for k, _ in STEPS}
    if not s["epochs"] and not s["last"]:
        return st_
    last_ep = max(s["epochs"]) if s["epochs"] else None
    cur = s["epochs"].get(last_ep, {}) if last_ep is not None else {}
    if cur.get("forward"):
        st_["forward"] = "done"
    if cur.get("sandbox") is not None:
        ok = bool(cur["sandbox"].get("passed"))
        st_["sandbox"] = "done" if cur["sandbox"].get("exit_code") == 0 else "fail"
        st_["verify"] = "done" if ok else "fail"
    if cur.get("gradient"):
        st_["gradient"] = "done"
        st_["prompt"] = "done"
    if s["neo4j"]:
        st_["neo4j"] = "done"
    if s["contagion"] is not None:
        st_["contagion"] = "done"
    # failures from earlier epochs that were later fixed -> show as done
    if s["passed_epoch"] is not None:
        st_["sandbox"] = st_["verify"] = "done"
    for e in s["errors"]:
        stage = e.get("stage")
        if stage in st_:
            st_[stage] = "fail"
    if running:
        last = (s["last"] or {}).get("type")
        nxt = {
            "start": "forward", "warmup": "forward", "forward": "sandbox", "sandbox": None,
            "gradient": "forward", "passed": "neo4j", "neo4j_commit": "contagion",
        }.get(last)
        if last == "sandbox":
            nxt = "neo4j" if cur.get("sandbox", {}).get("passed") else "gradient"
        if last == "gradient":
            # next epoch starts: reset per-epoch steps for clarity
            for k in ("forward", "sandbox", "verify"):
                st_[k] = ""
        if nxt:
            st_[nxt] = "active"
    return st_


# ----------------------------------------------------------------------------- render blocks
def nosana_badge(s: dict, running: bool, run_kind: str):
    """Return (value, detail, state) — never claims Nosana is live unless a live call went there."""
    fwd = s["fwd_model"]
    bwd = s["bwd_model"] or "none yet"
    if s["scripted"] or (fwd or "").lower() == "scripted":
        return "Scripted", "LLM steps pre-authored, not a live model call", "idle"
    if fwd is None:
        env_m = os.getenv("FORWARD_MODEL") or ""
        if env_m and env_m.upper() != "TBD":
            return esc(env_m), "Configured, not called yet", "active" if running else "idle"
        return "Endpoint not verified", "Set FORWARD_MODEL in .env", "active" if running else "idle"
    is_nosana_model = "qwen3.8" in str(fwd).lower()
    if run_kind != "live":
        if is_nosana_model and not s["fwd_fallback"]:
            return "Nosana, RTX 5090, %s" % esc(fwd), "Recorded from a live run; optimizer %s" % esc(bwd), "ok"
        return esc(fwd), "Recorded run; optimizer %s" % esc(bwd), "idle"
    disp = s["fwd_display"] or fwd
    if s["fwd_fallback"] or ("nosana" not in str(disp).lower() and not is_nosana_model):
        return esc(disp), "Not Nosana: fallback endpoint; optimizer %s" % esc(bwd), "fail"
    if is_nosana_model and "nosana" not in str(disp).lower():
        disp = "Nosana, RTX 5090, %s" % fwd
    return esc(disp), "Live; optimizer %s" % esc(bwd), "ok"


def render_flag(s: dict, run_kind: str, run_label: Optional[str]):
    if run_kind == "mock":
        st.markdown('<div class="eg-flag">%s</div>' % tag("UI mock: fabricated events for a layout preview (%s)" % (run_label or ""), "out"),
                    unsafe_allow_html=True)
    elif s["scripted"]:
        pre = "Replay of a scripted run: " if run_kind == "replay" else "Scripted LLM: "
        st.markdown('<div class="eg-flag">%s</div>' % tag(pre + (s["note"] or SCRIPTED_NOTE), "out"),
                    unsafe_allow_html=True)
    elif run_kind == "replay" and s["start"]:
        st.markdown('<div class="eg-flag">%s</div>' % tag("Replay of a recorded live run (%s)" % (run_label or ""), "on"),
                    unsafe_allow_html=True)


def render_header(s: dict, running: bool, run_kind: str = "live"):
    def dot(state):  # a state is a word in a tag, never a coloured dot
        return ""

    state_tag = {"ok": ("in", "in"), "active": ("running", "on"), "idle": ("standby", "out"), "fail": ("check", "fail")}

    eng = s["engine"] or os.getenv("DAYTONA_API_KEY") and "Daytona Cloud (ready)" or "not configured"
    any_sb = any(e.get("sandbox") is not None for e in s["epochs"].values())
    d_state = "ok" if any_sb else ("active" if running else "idle")
    if eng and "Fallback" in str(eng):
        d_state = "fail"
    runs = sum(1 for e in s["epochs"].values() if e.get("sandbox") is not None)

    n_val, n_det, n_state = nosana_badge(s, running, run_kind)

    if s["neo4j"]:
        st4 = s["neo4j"].get("stats", {})
        neo_v, neo_d, neo_state = "Committed", "%s nodes and %s relationships in Aura" % (st4.get("nodes"), st4.get("rels")), "ok"
    else:
        neo_v, neo_d, neo_state = "Awaiting verified data", (os.getenv("NEO4J_URI", "") or "").split("//")[-1][:34] or "Aura", "idle"

    versions = 1 + sum(1 for e in s["epochs"].values() if e.get("gradient"))
    a_state = "ok" if versions > 1 else ("active" if running else "idle")

    rows = [
        ("Daytona execution verifier", d_state, ("%d sandbox runs" % runs if runs else "Standby"), esc(eng)),
        ("Nosana private GPU model", n_state, n_val, n_det),
        ("Neo4j contagion graph", neo_state, esc(neo_v), esc(neo_d)),
        ("AdalFlow prompt parameter", a_state, "Prompt v%d" % versions, "Textual-gradient updates: %d" % (versions - 1)),
    ]
    h = '<div class="eg-ruled">'
    for name, state, v, d in rows:
        word, kind = state_tag.get(state, ("standby", "out"))
        h += ('<div class="eg-row"><span class="n">%s</span><span class="v">%s</span><span class="d">%s</span>%s</div>'
              % (name, v, d, tag(word, kind)))
    h += "</div>"
    st.markdown(h, unsafe_allow_html=True)


def render_stepper(s: dict, running: bool):
    states = step_states(s, running)
    words = {"done": "done", "active": "running", "fail": "failed", "": ""}
    parts = []
    for i, (k, label) in enumerate(STEPS):
        stt = states.get(k, "")
        parts.append('<div class="eg-step %s"><span class="num">%d</span>%s<span class="s">%s</span></div>'
                     % (stt, i + 1, esc(label), words.get(stt, "")))
    if s["epochs"]:
        st.markdown('<div class="eg-sub">Epoch %d of the loop</div>' % max(s["epochs"]), unsafe_allow_html=True)
    st.markdown('<div class="eg-steps">%s</div>' % "".join(parts), unsafe_allow_html=True)


def diff_html(old: str, new: str) -> str:
    out = []
    for line in difflib.ndiff((old or "").splitlines(), (new or "").splitlines()):
        tag, body = line[:2], esc(line[2:]) or "&nbsp;"
        if tag == "+ ":
            out.append('<span class="add">+ %s</span>' % body)
        elif tag == "- ":
            out.append('<span class="del">- %s</span>' % body)
        elif tag == "  ":
            out.append('<span class="ctx">  %s</span>' % body)
    return '<div class="eg-diff">%s</div>' % "".join(out)


def render_epoch(ep: int, e: dict, running: bool):
    fwd = e.get("forward") or {}
    sb = e.get("sandbox")
    grad = e.get("gradient")
    with st.container():
        if sb is None:
            pill = tag("Running" if running else "Pending", "on" if running else "out")
        elif sb.get("passed"):
            pill = tag("Passed, reward %.2f" % float(sb.get("reward", 1.0)), "in")
        else:
            pill = tag("Failed, reward %.2f, exit %s" % (float(sb.get("reward", 0.0)), sb.get("exit_code")), "fail")
        meta = []
        if fwd.get("model"):
            meta.append("Model %s took %.1f s." % (esc(fwd["model"]), (fwd.get("latency_ms") or 0) / 1000.0))
        if sb:
            meta.append("Sandbox %s ran in %d ms." % (esc(sb.get("engine", "")), int(sb.get("latency_ms") or 0)))
        st.markdown(
            '<div class="eg-epoch-h"><span class="t">Epoch %d</span>%s</div><div class="eg-sub">%s</div>' % (ep, pill, " ".join(meta)),
            unsafe_allow_html=True,
        )
        mem = e.get("memory") or {}
        lessons = mem.get("lessons") or []
        if lessons:
            h = '<div class="eg-memory"><div class="h">Recalled from Neo4j</div>'
            for les in lessons[:3]:
                fixed = les.get("fixed_in_epoch")
                h += ('<div class="l">%s%s<span class="g">%s</span></div>'
                      % (esc(str(les.get("error_summary") or "")[:120]),
                         (" Fixed in epoch %s." % esc(fixed)) if fixed is not None else "",
                         esc(str(les.get("gradient") or "")[:120])))
            st.markdown(h + "</div>", unsafe_allow_html=True)
        tabs = st.tabs(["Sandbox output", "Verifier", "Textual gradient", "Prompt diff", "Generated code"])
        with tabs[0]:
            if sb is None:
                st.caption("Waiting for Daytona sandbox execution…")
            else:
                out = sb.get("output") or ""
                if len(out) > 4000:
                    out = out[:1500] + "\n…\n" + out[-2000:]
                st.code(out or "(no output)", language="text")
        with tabs[1]:
            if sb is None:
                st.caption("Verifier runs after the sandbox returns.")
            else:
                checks = sb.get("checks") or []
                if checks:
                    rows = "".join(
                        '<div class="eg-check">%s<span class="n">%s</span><span class="dd">%s</span></div>'
                        % (tag("pass", "in") if c.get("ok") else tag("fail", "fail"), esc(c.get("name")), esc(c.get("detail")))
                        for c in checks
                    )
                    st.markdown(rows, unsafe_allow_html=True)
                fails = sb.get("failures") or []
                if fails:
                    st.markdown("".join('<div class="eg-fail-line">%s</div>' % esc(str(f)[:300]) for f in fails),
                                unsafe_allow_html=True)
                if not checks and not fails:
                    st.caption("No detailed checks reported.")
        with tabs[2]:
            if grad:
                st.markdown('<div class="eg-well">%s</div>' % esc(grad.get("textual_gradient") or "(empty gradient)"),
                            unsafe_allow_html=True)
                if grad.get("model"):
                    st.caption("Backward engine %s took %.1f s." % (grad["model"], (grad.get("latency_ms") or 0) / 1000.0))
            elif sb is not None and sb.get("passed"):
                note("No gradient needed: the loss is zero, so the prompt parameter stays as it is.", "in")
            else:
                st.caption("The gradient is computed from the sandbox traceback when an epoch fails.")
        with tabs[3]:
            if grad:
                st.caption("Prompt v%d to v%d. Teal lines were added by the optimizer; struck red lines were removed." % (ep, ep + 1))
                st.markdown(diff_html(fwd.get("prompt", ""), grad.get("updated_prompt", "")), unsafe_allow_html=True)
            else:
                st.code(fwd.get("prompt") or "(prompt not yet available)", language="markdown")
        with tabs[4]:
            st.code(fwd.get("code") or "(waiting for forward pass)", language="python")


def holdings_at_risk(paths: List[dict]) -> List[dict]:
    best: Dict[str, dict] = {}
    for p in paths or []:
        h = p.get("holding") or (p.get("path") or [None])[-1]
        if h is None:
            continue
        if h not in best or float(p.get("at_risk") or 0) > float(best[h].get("at_risk") or 0):
            best[h] = p
    return sorted(best.values(), key=lambda p: -float(p.get("at_risk") or 0))


SHORT_NAMES = {"RAW:QUARTZ_X": "Quartz", "SUP:ZEISS": "Zeiss", "SUP:ASML": "ASML",
               "SUP:TSMC": "TSMC", "HOLDING:NVDA": "NVIDIA", "HOLDING:AAPL": "Apple"}


def short_name(n: dict) -> str:
    """Compact node label so the 5-hop chain fits the right column; full names stay in the table."""
    return SHORT_NAMES.get(n.get("id", ""), str(n.get("name", n.get("id", "")))[:14])


def raw_feed_graph() -> Optional[dict]:
    try:
        from dirty_payload import CONFIDENTIAL_PORTFOLIO_DATA as raw
    except Exception:
        return None
    return {"nodes": raw.get("nodes", []), "edges": raw.get("edges", [])}


def render_raw_graph(key: str):
    """Grey, unverified view of the dirty feed — shown before anything is committed."""
    g = raw_feed_graph()
    if not g or _agraph is None:
        return
    st.markdown('<div class="eg-graph-cap">%s<span>Raw feed, quarantined. Nothing here is in Neo4j yet.</span></div>'
                % tag("unverified", "out"), unsafe_allow_html=True)
    nodes, edges = [], []
    for n in g["nodes"]:
        tier = n.get("tier", "")
        raw_exp = n.get("exposure_raw")
        nodes.append(Node(
            id=n["id"], label="%s\n%s" % (short_name(n), raw_exp if raw_exp is not None else "null"),
            size=22, color={"background": INK["paper2"], "border": INK["rule"]}, level=TIER_LEVEL.get(tier, 2),
            title="Unverified: %s, exposure_raw=%r" % (n.get("id"), raw_exp),
            font={"color": INK["muted"], "size": 12, "strokeWidth": 4, "strokeColor": INK["paper"], "face": "Helvetica Neue, Helvetica, Arial, sans-serif"},
            shape="dot", borderWidth=1,
        ))
    for e in g["edges"]:
        edges.append(Edge(
            source=e["src"], target=e["dst"], label="%s" % e.get("weight"),
            color=INK["rule"], width=1.2, dashes=True,
            font={"color": INK["muted"], "size": 10, "strokeWidth": 0, "align": "top", "face": "IBM Plex Mono"},
            smooth={"type": "curvedCW", "roundness": 0.12},
        ))
    cfg = Config(width=540, height=360, directed=True, physics=False, hierarchical=True,
                 direction="LR", sortMethod="directed", levelSeparation=100, nodeSpacing=110)
    data = {"nodes": [x.to_dict() for x in nodes], "edges": [x.to_dict() for x in edges]}
    _agraph(data=json.dumps(data), config=json.dumps(cfg.__dict__), key=key)


def render_graph(s: dict, key: str):
    g = s["graph"]
    if not g:
        return
    if _agraph is None:
        st.error("streamlit-agraph not available")
        return
    st.markdown('<div class="eg-graph-cap">%s<span>Read back from Neo4j. The heavy red line is the contagion path.</span></div>'
                % tag("verified", "in"), unsafe_allow_html=True)
    red_edges = set()
    red_nodes = set()
    for p in s["contagion"] or []:
        ids = p.get("path") or []
        red_nodes.update(ids)
        for a, b in zip(ids, ids[1:]):
            red_edges.add((a, b))
    nodes = []
    for n in g.get("nodes", []):
        tier = n.get("tier", "")
        label = "%s\n%s" % (short_name(n), fmt_money(n.get("exposure"), n.get("currency")) if n.get("currency") else "Force majeure")
        is_key = tier in ("Portfolio_Holding", "Tier4_RawMaterial")
        nodes.append(Node(
            id=n["id"], label=label, size=28 if is_key else 20, color=TIER_COLORS.get(tier, INK["muted"]),
            title="%s, %s, %s" % (n.get("id"), tier, n.get("status")), level=TIER_LEVEL.get(tier, 2),
            font={"color": INK["ink"], "size": 13, "strokeWidth": 5, "strokeColor": INK["paper"], "face": "Helvetica Neue, Helvetica, Arial, sans-serif"},
            borderWidth=3 if n["id"] in red_nodes else 1,
            shape="star" if tier == "Tier4_RawMaterial" else ("diamond" if tier == "Portfolio_Holding" else "dot"),
        ))
    edges = []
    for e in g.get("edges", []):
        hot = (e.get("src"), e.get("dst")) in red_edges
        edges.append(Edge(
            source=e["src"], target=e["dst"], label="%.2f" % float(e.get("weight") or 0),
            color=INK["risk"] if hot else INK["rule"], width=3.5 if hot else 1.2,
            font={"color": INK["risk"] if hot else INK["muted"], "size": 10, "strokeWidth": 4, "strokeColor": INK["paper"],
                  "align": "top", "face": "IBM Plex Mono"},
            dashes=False, smooth={"type": "curvedCW", "roundness": 0.12},
        ))
    cfg = Config(width=540, height=420, directed=True, physics=False, hierarchical=True,
                 direction="LR", sortMethod="directed", levelSeparation=100, nodeSpacing=120)
    data = {"nodes": [x.to_dict() for x in nodes], "edges": [x.to_dict() for x in edges]}
    _agraph(data=json.dumps(data), config=json.dumps(cfg.__dict__), key=key)


def render_right_top(s: dict, running: bool):
    st.markdown("## Risk contagion in Neo4j")
    paths = s["contagion"]
    if paths is None:
        if s["passed_epoch"] is not None:
            note("The sandbox verification passed. Committing the verified graph to Neo4j.")
        else:
            note("The dirty feed is quarantined. Nothing reaches Neo4j until the Daytona sandbox and the ground-truth verifier pass.")
        return
    hold = holdings_at_risk(paths)
    total = sum(float(p.get("at_risk") or 0) for p in hold)
    src = paths[0]["names"][0] if paths and paths[0].get("names") else "the upstream shock"
    st.markdown(
        '<div class="eg-big"><div class="k">Portfolio exposure at risk</div><div class="v">%s</div>'
        '<div class="d">%s in full. %d holdings are reachable from %s, under sanction or force majeure. Computed in Cypher.</div></div>'
        % (fmt_money(total), fmt_full(total), len(hold), esc(src)),
        unsafe_allow_html=True,
    )


def render_right_details(s: dict):
    paths = s["contagion"]
    if paths is None:
        return
    hold = holdings_at_risk(paths)
    h = '<div class="eg-ruled">'
    for p in hold[:3]:
        chain = " to ".join(esc(x) for x in (p.get("names") or p.get("path") or []))
        h += ('<div class="eg-path">%s<br/>Contagion <span class="fig">%.5f</span> times exposure <span class="fig">%s</span> '
              'puts <span class="fig">%s</span> at risk.</div>'
              % (chain, float(p.get("contagion") or 0), fmt_full(p.get("exposure")), fmt_full(p.get("at_risk"))))
    st.markdown(h + "</div>", unsafe_allow_html=True)
    rows = [{
        "Holding": p.get("holding"),
        "Hops": max(0, len(p.get("path") or []) - 1),
        "Contagion": round(float(p.get("contagion") or 0), 4),
        "Exposure": fmt_full(p.get("exposure")),
        "At risk": fmt_full(p.get("at_risk")),
    } for p in hold]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    with st.expander("Cypher used for contagion"):
        st.code(
            "MATCH p=(r:Entity {tier:'Tier4_RawMaterial'})-[*]->(h:Entity {tier:'Portfolio_Holding'})\n"
            "WITH p, h, reduce(s=1.0, x IN relationships(p) | s * x.weight) AS contagion\n"
            "RETURN [n IN nodes(p) | n.id] AS path, h.id AS holding, contagion,\n"
            "       h.exposure AS exposure, h.exposure * contagion AS at_risk\n"
            "ORDER BY at_risk DESC",
            language="cypher",
        )


def render_left(s: dict, running: bool):
    render_stepper(s, running)
    for e in s["errors"]:
        note("%s error: %s" % (esc(e.get("stage", "run")), esc(e.get("message"))), "fail")
    if s["warmup"]:
        st.caption("Sandbox pre-warmed: %s in %s ms." % (s["warmup"].get("engine"), s["warmup"].get("latency_ms")))
    for ep in sorted(s["epochs"]):
        render_epoch(ep, s["epochs"][ep], running)
    if s["done"]:
        if s["done"].get("success"):
            note("The loop converged in %s epochs with no human edits." % esc(s["done"].get("epochs")), "in")
        else:
            note("The run ended without converging after %s epochs." % esc(s["done"].get("epochs")), "fail")


# ----------------------------------------------------------------------------- page
_hero_l, _hero_r = st.columns([3, 1], gap="large", vertical_alignment="bottom")
with _hero_l:
    st.markdown(
        '<div class="eg-hero"><h1>Self-correcting ETL for confidential risk data</h1>'
        '<p class="eg-lede">A private model on Nosana writes the ETL, an offline Daytona sandbox runs it, '
        "the traceback rewrites the prompt, and only verified rows reach the Neo4j contagion graph.</p></div>",
        unsafe_allow_html=True,
    )
with _hero_r:
    run_btn = st.button("Run evolution", type="primary", width="stretch")
_arch = asset("architecture_light.svg", "architecture.svg")

for _k, _v in (("events", []), ("live_error", None), ("run_kind", "live"), ("run_label", None), ("fanout", None)):
    if _k not in st.session_state:
        st.session_state[_k] = _v


def load_scripted_engine_cls():
    """Load ScriptedEngine from scripts/e2e_scripted.py by file path (scripts/ is not a package)."""
    path = os.path.join(HERE, "scripts", "e2e_scripted.py")
    if not os.path.exists(path):
        raise RuntimeError("scripts/e2e_scripted.py not found yet.")
    spec = importlib.util.spec_from_file_location("evograph_e2e_scripted", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not hasattr(mod, "ScriptedEngine"):
        raise RuntimeError("scripts/e2e_scripted.py has no ScriptedEngine.")
    return mod.ScriptedEngine


HAS_FANOUT = os.path.exists(os.path.join(HERE, "parallel_sandbox.py"))
FANOUT_SCRIPTS = ["reference/naive_etl.py", "reference/partial_etl.py", "reference/robust_etl.py"]

with st.sidebar:
    st.markdown('<div class="eg-wordmark">EvoGraph-Quant</div>', unsafe_allow_html=True)
    mode = st.radio(
        "Mode", [MODE_REPLAY, MODE_SCRIPTED, MODE_LIVE], index=0,
        help="Live run: real Nosana LLM, Daytona and Neo4j. "
             "Scripted LLM: pre-authored LLM steps with a real Daytona sandbox and real Neo4j. "
             "Replay: a recorded run.",
    )
    if mode == MODE_SCRIPTED:
        st.caption(SCRIPTED_NOTE)
    replays = list_replays()
    replay_file = None
    speed = 1.0
    if mode == MODE_REPLAY:
        if replays:
            replay_file = st.selectbox("Replay file", replays, index=0)
        else:
            st.warning("No replay/*.json found")
        speed = st.select_slider("Playback speed", options=[0.5, 1.0, 1.5, 2.0, 3.0, 5.0], value=1.5)
    max_epochs = st.slider("Max self-evolution epochs", 2, 4, 3)
    record = False
    if mode != MODE_REPLAY:
        record = st.checkbox("Record this run to replay/live_<time>.json", value=True)
    st.divider()
    fan_btn = False
    if HAS_FANOUT:
        fan_btn = st.button("Fan out to 6 Daytona sandboxes", width="stretch",
                            help="Run 6 candidate ETL scripts in 6 isolated offline Daytona sandboxes in parallel (best-of-K).")
    if st.button("Reset the Neo4j demo graph", width="stretch"):
        try:
            from graph_store import GraphStore

            GraphStore().reset_demo()
            st.caption("Neo4j demo labels cleared.")
        except Exception as ex:  # noqa
            st.caption("Reset failed: %s" % ex)
    if st.button("Clear dashboard", width="stretch"):
        st.session_state.events = []
        st.session_state.live_error = None
        st.session_state.fanout = None
        st.rerun()
    try:
        from dirty_payload import get_raw_payload_str

        with st.expander("Confidential dirty feed"):
            st.code(get_raw_payload_str(), language="json")
            st.caption("Traps: apostrophes in names (Cypher injection), mixed currency strings and a null, 'CRITICAL_' weight prefixes.")
    except Exception:
        pass
    if _arch:
        with st.expander("Architecture"):
            st.markdown('<img src="data:image/svg+xml;base64,%s" style="width:100%%;border-radius:12px;" alt="EvoGraph-Quant architecture"/>'
                        % b64_file(_arch), unsafe_allow_html=True)

flag_ph = st.empty()
header_ph = st.empty()
col_left, col_right = st.columns([1.2, 1], gap="large")
with col_left:
    st.markdown("## The self-evolution loop")
    left_ph = st.empty()
with col_right:
    right_ph = st.empty()
    graph_ph = st.empty()
    details_ph = st.empty()
fan_ph = st.empty()


def paint(events: List[dict], running: bool, graph_key: Optional[str], run_kind: str, run_label: Optional[str],
          raw_key: Optional[str] = None):
    s = derive(events)
    with flag_ph.container():
        render_flag(s, run_kind, run_label)
    with header_ph.container():
        render_header(s, running, run_kind)
    with left_ph.container():
        render_left(s, running)
    with right_ph.container():
        render_right_top(s, running)
    if graph_key and s["graph"]:
        with graph_ph.container():
            render_graph(s, graph_key)
    elif raw_key and not s["graph"]:
        with graph_ph.container():
            render_raw_graph(raw_key)
    with details_ph.container():
        render_right_details(s)
    return s


def render_fanout(fo: dict):
    st.markdown("## Daytona fan-out, best of K isolated sandboxes")
    if fo.get("error"):
        note("Fan-out failed: %s" % esc(fo["error"]), "fail")
        return
    summ = fo.get("summary") or {}
    best = fo.get("best") or {}
    st.markdown(
        '<div class="eg-sub">%s candidates ran in parallel in %s ms of wall clock, against %s ms run one after another '
        '(%sx faster). %s passed. The best is candidate %s with reward %.2f. Engines: %s.</div>'
        % (summ.get("k"), summ.get("parallel_wall_ms"), summ.get("serial_sum_ms"), summ.get("speedup"),
           summ.get("passed_count"), (best.get("index", 0) or 0) + 1 if best else "none",
           float(best.get("reward") or 0), esc(", ".join(summ.get("engines") or []))),
        unsafe_allow_html=True,
    )
    results = fo.get("results") or []
    labels = fo.get("labels") or []
    h = ('<table class="eg-fan"><thead><tr><th>Candidate</th><th>Script</th><th>Reward</th><th>State</th>'
         "<th>Exit</th><th>Wall ms</th><th>Engine</th></tr></thead><tbody>")
    for i, r in enumerate(results):
        ok = bool(r.get("passed"))
        is_best = bool(best) and r.get("index") == best.get("index")
        h += ('<tr class="%s"><td>%d%s</td><td>%s</td><td class="fig">%.2f</td><td>%s</td><td class="fig">%s</td>'
              '<td class="fig">%s</td><td>%s</td></tr>'
              % ("best" if is_best else "", i + 1, " (best)" if is_best else "", esc(labels[i] if i < len(labels) else ""),
                 float(r.get("reward") or 0), tag("passed", "in") if ok else tag("failed", "fail"),
                 esc(r.get("exit_code")), esc(r.get("wall_ms")), esc(r.get("engine", ""))))
    st.markdown(h + "</tbody></table>", unsafe_allow_html=True)


DELAYS = {"memory": 1.0, "start": 0.3, "warmup": 0.8, "forward": 2.2, "sandbox": 1.6, "gradient": 2.6, "passed": 0.6,
          "neo4j_commit": 1.2, "contagion": 1.0, "graph": 0.6, "done": 0.2, "error": 0.5}


def _enrich(ev: dict, engine) -> dict:
    """Attach the real endpoint used (Nosana vs fallback) so the badge is honest."""
    if engine is None:
        return ev
    meta = None
    if ev.get("type") == "forward":
        meta = getattr(engine, "last_forward_meta", None)
    elif ev.get("type") == "gradient":
        meta = getattr(engine, "last_backward_meta", None)
    if isinstance(meta, dict):
        for k in ("display", "fallback_used", "provider", "endpoint"):
            if meta.get(k) is not None and k not in ev:
                ev[k] = meta.get(k)
    return ev


if fan_btn:
    fo: dict = {}
    try:
        import parallel_sandbox as ps
        from dirty_payload import get_raw_payload_str

        codes, labels = [], []
        for rel in FANOUT_SCRIPTS:
            with open(os.path.join(HERE, rel), "r", encoding="utf-8") as f:
                code = f.read()
            for _ in range(2):
                codes.append(code)
                labels.append(os.path.basename(rel).replace("_etl.py", ""))
        with st.spinner("Starting 6 offline Daytona sandboxes in parallel"):
            if hasattr(ps, "run_best_of_k"):
                fo = ps.run_best_of_k(codes, get_raw_payload_str())
            else:
                t0 = time.time()
                res = ps.verify_many(codes, get_raw_payload_str())
                total = int((time.time() - t0) * 1000)
                fo = {"results": res, "best": ps.best_of(res), "summary": ps.summary(res, total)}
        fo["labels"] = labels
    except Exception as ex:
        fo = {"error": "%s" % ex}
    st.session_state.fanout = fo

if run_btn:
    st.session_state.events = []
    st.session_state.live_error = None
    events: List[dict] = []
    graph_drawn = False
    run_kind = {MODE_REPLAY: "replay", MODE_SCRIPTED: "scripted", MODE_LIVE: "live"}[mode]
    run_label = replay_file if mode == MODE_REPLAY else mode
    if mode == MODE_REPLAY and replay_file and "mock" in replay_file:
        run_kind = "mock"
    st.session_state.run_kind, st.session_state.run_label = run_kind, run_label
    try:
        if mode == MODE_REPLAY:
            if not replay_file:
                raise RuntimeError("No replay file available.")
            source = load_events(replay_file)
            for i, ev in enumerate(source):
                events.append(ev)
                key = None
                if ev.get("type") == "graph" and not graph_drawn:
                    key, graph_drawn = "graph_stream", True
                paint(events, True, key, run_kind, run_label, raw_key="raw_stream" if i == 0 else None)
                time.sleep(DELAYS.get(ev.get("type"), 0.5) / max(speed, 0.1))
        else:
            try:
                from orchestrator import run_evolution
            except Exception as ex:
                raise RuntimeError("orchestrator.run_evolution not importable (%s). Switch to Replay mode." % ex)
            engine = None
            if mode == MODE_SCRIPTED:
                engine = load_scripted_engine_cls()()
            else:
                try:
                    from evo_engine import EvoGraphEngine

                    engine = EvoGraphEngine()
                except Exception as ex:
                    raise RuntimeError("EvoGraphEngine init failed (%s). Try the scripted mode or Replay." % ex)
            try:
                from graph_store import GraphStore

                GraphStore().reset_demo()
            except Exception as ex:
                events.append({"type": "error", "stage": "neo4j", "message": "reset_demo failed: %s" % ex})
            first = True
            for ev in run_evolution(max_epochs=max_epochs, use_neo4j=True, engine=engine):
                if ev.get("type") == "start" and mode == MODE_SCRIPTED:
                    ev.setdefault("scripted", True)
                    ev.setdefault("note", SCRIPTED_NOTE)
                events.append(_enrich(ev, engine))
                key = None
                if ev.get("type") == "graph" and not graph_drawn:
                    key, graph_drawn = "graph_stream", True
                paint(events, True, key, run_kind, run_label, raw_key="raw_stream" if first else None)
                first = False
            if record and events:
                path = os.path.join(REPLAY_DIR, "live_%s.json" % time.strftime("%H%M%S"))
                try:
                    os.makedirs(REPLAY_DIR, exist_ok=True)
                    with open(path, "w") as f:
                        json.dump(events, f, indent=2, ensure_ascii=False, default=str)
                except Exception:
                    pass
    except Exception as ex:
        st.session_state.live_error = "%s\n\n%s" % (ex, traceback.format_exc()[-1500:])
    st.session_state.events = events
    st.rerun()

if st.session_state.live_error:
    note("Run failed: " + esc(st.session_state.live_error.split("\n\n")[0]), "fail")
    with st.expander("Traceback"):
        st.code(st.session_state.live_error, language="text")
    if mode != MODE_REPLAY:
        st.caption("Switch the sidebar to Replay a recorded run for a guaranteed demo.")

paint(st.session_state.events, False, "graph_final", st.session_state.run_kind, st.session_state.run_label,
      raw_key="raw_final")
if not st.session_state.events:
    with left_ph.container():
        render_stepper(derive([]), False)
        note("Press Run evolution to start an offline Daytona sandbox and the self-evolution loop.")
if st.session_state.fanout:
    with fan_ph.container():
        render_fanout(st.session_state.fanout)
