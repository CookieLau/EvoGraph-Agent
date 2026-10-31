"""Evolution loop: LLM writes ETL code -> Daytona sandbox runs it -> textual gradient fixes the prompt -> Neo4j.

run_evolution() is a generator of events (see CONTRACT.md "Event stream"). app.py renders them live,
and the same list of events is saved as a replay file for the stage fallback.

CLI:
    python orchestrator.py --epochs 3 --record replay/golden_run.json [--no-neo4j]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from typing import Any, Dict, Iterator, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # dotenv is optional for the loop itself
    pass

SOURCE_TIER = "Tier4_RawMaterial"
HOLDING_TIER = "Portfolio_Holding"


# ---------------------------------------------------------------- helpers

def _err_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def _engine_hint() -> str:
    if os.getenv("USE_LOCAL_FALLBACK", "false").lower() == "true" or not os.getenv("DAYTONA_API_KEY"):
        return "local"
    return "daytona"


def _model_name(role: str, engine: Any) -> Optional[str]:
    # Prefer what the engine actually used (no network call); ScriptedEngine reports "scripted".
    meta = getattr(engine, f"last_{role}_meta", None)
    if isinstance(meta, dict):
        v = meta.get("model")
        if isinstance(v, str) and v and v != "TBD" and not v.startswith("unresolved"):
            return v
    for attr in (f"{role}_model", f"{role}_model_name"):
        v = getattr(engine, attr, None)
        if isinstance(v, str) and v:
            return v
    try:
        from llm_client import model_name

        return model_name(role)
    except Exception:
        return None


def _graph_memory_on() -> bool:
    return os.getenv("GRAPH_MEMORY", "1").strip().lower() not in ("0", "false", "off", "no")


def _recall_lessons(store: Any, run_id: str, timeout_s: float = 3.0) -> List[Dict[str, Any]]:
    """Graph memory lookup with a hard timeout. Any problem -> [] (never breaks the loop)."""
    if store is None or not _graph_memory_on() or not hasattr(store, "recall_lessons"):
        return []
    try:
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(store.recall_lessons, exclude_run_id=run_id)
        try:
            lessons = fut.result(timeout=timeout_s)
        finally:
            pool.shutdown(wait=False)
        return [l for l in (lessons or []) if isinstance(l, dict)]
    except Exception:
        return []


def _lesson_line(lesson: Dict[str, Any]) -> str:
    err = " ".join(str(lesson.get("error_summary") or "").split())[:300]
    grad = " ".join(str(lesson.get("gradient") or "").split())[:220]
    return f"Lesson from graph memory: {err} -> {grad}"


def _current_prompt(engine: Any) -> str:
    param = getattr(engine, "system_prompt_param", None)
    data = getattr(param, "data", None) if param is not None else None
    return data if isinstance(data, str) else ""


def _error_summary(res: Dict[str, Any]) -> str:
    """Short one-line reason for a failed epoch (first failures, else last traceback line)."""
    failures = res.get("failures") or []
    if failures:
        return "; ".join(str(f) for f in failures[:3])[:400]
    output = (res.get("output") or "").strip()
    if output:
        return output.splitlines()[-1][:400]
    return f"exit code {res.get('exit_code')}"


def _failed_result(message: str) -> Dict[str, Any]:
    return {
        "passed": False, "reward": 0.0, "exit_code": -1, "output": message, "latency_ms": 0,
        "engine": "Verifier (Error)", "sandbox_id": None, "checks": [], "failures": [message], "cleaned": None,
    }


def local_contagion_paths(cleaned: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Same shape as GraphStore.contagion_paths(), computed in memory (used when Neo4j is off or down)."""
    nodes = {n.get("id"): n for n in (cleaned or {}).get("nodes", []) if isinstance(n, dict)}
    out_edges: Dict[str, List[Dict[str, Any]]] = {}
    for e in (cleaned or {}).get("edges", []):
        if isinstance(e, dict):
            out_edges.setdefault(e.get("src"), []).append(e)

    paths: List[Dict[str, Any]] = []

    def walk(node_id: str, path: List[str], score: float) -> None:
        if len(path) > 7:
            return
        node = nodes.get(node_id, {})
        if len(path) > 1 and node.get("tier") == HOLDING_TIER:
            exposure = float(node.get("exposure") or 0.0)
            paths.append({
                "path": list(path),
                "names": [nodes.get(i, {}).get("name", i) for i in path],
                "contagion": round(score, 6),
                "holding": node_id,
                "exposure": exposure,
                "at_risk": round(exposure * score, 2),
            })
            return
        for e in out_edges.get(node_id, []):
            nxt = e.get("dst")
            if nxt in path:
                continue
            try:
                w = float(e.get("weight") or 0.0)
            except (TypeError, ValueError):
                w = 0.0
            walk(nxt, path + [nxt], score * w)

    for nid, n in nodes.items():
        if n.get("tier") == SOURCE_TIER:
            walk(nid, [nid], 1.0)
    paths.sort(key=lambda p: p["at_risk"], reverse=True)
    return paths


# ---------------------------------------------------------------- main loop

def run_evolution(
    max_epochs: int = 3,
    use_neo4j: bool = True,
    engine: Any = None,
    verifier: Any = None,
    store: Any = None,
    payload_str: Optional[str] = None,
) -> Iterator[dict]:
    """Yield CONTRACT events. Dependencies can be injected (tests / replay); otherwise built lazily."""
    t0 = time.time()
    run_id = f"run-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"

    def ev(etype: str, **kw: Any) -> dict:
        d = {"type": etype}
        d.update(kw)
        d["ts"] = round(time.time() - t0, 3)
        return d

    yield ev("start", run_id=run_id, engine_hint=_engine_hint(), max_epochs=max_epochs, use_neo4j=use_neo4j)

    epochs_run = 0
    passed_res: Optional[Dict[str, Any]] = None
    try:
        # ---- payload
        if payload_str is None:
            try:
                from dirty_payload import get_raw_payload_str

                payload_str = get_raw_payload_str()
            except Exception as e:
                yield ev("error", stage="forward", message=f"Cannot load payload: {_err_text(e)}")
                yield ev("done", success=False, epochs=0, run_id=run_id)
                return

        # ---- sandbox verifier + warmup
        if verifier is None:
            try:
                from sandbox_verifier import DaytonaSandboxVerifier

                verifier = DaytonaSandboxVerifier(payload_str, network_block_all=True)
            except Exception as e:
                yield ev("error", stage="sandbox", message=f"Verifier init failed: {_err_text(e)}")
                yield ev("done", success=False, epochs=0, run_id=run_id)
                return
        try:
            w = verifier.warmup() or {}
            yield ev("warmup", engine=w.get("engine"), sandbox_id=w.get("sandbox_id"), latency_ms=w.get("latency_ms"))
        except Exception as e:
            yield ev("error", stage="sandbox", message=f"Sandbox warmup failed (will retry on first run): {_err_text(e)}")

        # ---- engine
        if engine is None:
            try:
                from evo_engine import EvoGraphEngine

                engine = EvoGraphEngine()
            except Exception as e:
                yield ev("error", stage="forward", message=f"Engine init failed: {_err_text(e)}")
                yield ev("done", success=False, epochs=0, run_id=run_id)
                return

        # ---- graph store (optional; failures only disable Neo4j)
        if use_neo4j and store is None:
            try:
                from graph_store import GraphStore

                store = GraphStore()
                if hasattr(store, "ping") and not store.ping():
                    raise RuntimeError("Neo4j ping returned False")
            except Exception as e:
                yield ev("error", stage="neo4j", message=f"Neo4j unavailable, continuing without it: {_err_text(e)}")
                store = None
        if not use_neo4j:
            store = None

        def record(epoch: int, prompt: str, passed: bool, reward: float, summary: str, grad: Optional[str]):
            if store is None:
                return None
            try:
                store.record_epoch(run_id, epoch, prompt, passed, float(reward or 0.0), summary, grad)
                return None
            except Exception as e:
                return ev("error", stage="neo4j", message=f"record_epoch({epoch}) failed: {_err_text(e)}")

        # ---- epochs
        for epoch in range(1, max_epochs + 1):
            epochs_run = epoch
            prompt = _current_prompt(engine)

            # forward pass
            t = time.time()
            try:
                code = engine.forward_generate_code(payload_str)
            except Exception as e:
                yield ev("error", stage="forward", epoch=epoch, message=_err_text(e))
                err = record(epoch, prompt, False, 0.0, f"forward error: {_err_text(e)}"[:400], None)
                if err:
                    yield err
                continue  # transient LLM failure: try again next epoch with the same prompt
            yield ev("forward", epoch=epoch, prompt=prompt, code=code,
                     model=_model_name("forward", engine), latency_ms=int((time.time() - t) * 1000))

            # sandbox run
            try:
                res = verifier.verify_etl_script(code)
            except Exception as e:
                yield ev("error", stage="sandbox", epoch=epoch, message=_err_text(e))
                res = _failed_result(f"Verifier exception: {_err_text(e)}")
            yield ev("sandbox", epoch=epoch, result=res)

            if res.get("passed"):
                err = record(epoch, prompt, True, res.get("reward", 1.0), "passed", None)
                if err:
                    yield err
                passed_res = res
                yield ev("passed", epoch=epoch)
                break

            # backward pass (skip on the last epoch: nothing would use the new prompt)
            grad_text: Optional[str] = None
            if epoch < max_epochs:
                # graph memory: recall past fixed failures from Neo4j (copy; checker failures untouched)
                bwd_failures = list(res.get("failures") or [])
                lessons = _recall_lessons(store, run_id)
                if lessons:
                    yield ev("memory", epoch=epoch, lessons=lessons, source="neo4j")
                    bwd_failures.extend(_lesson_line(l) for l in lessons)
                t = time.time()
                try:
                    g = engine.backward_textual_gradient(code, res.get("output") or "", bwd_failures)
                    grad_text = (g or {}).get("textual_gradient")
                    yield ev("gradient", epoch=epoch, textual_gradient=grad_text,
                             updated_prompt=(g or {}).get("updated_prompt") or _current_prompt(engine),
                             model=_model_name("backward", engine), latency_ms=int((time.time() - t) * 1000))
                except Exception as e:
                    yield ev("error", stage="gradient", epoch=epoch, message=_err_text(e))
            err = record(epoch, prompt, False, res.get("reward", 0.0), _error_summary(res), grad_text)
            if err:
                yield err

        # ---- commit verified data (never the raw payload)
        if passed_res is not None:
            cleaned = passed_res.get("cleaned")
            if not cleaned:
                yield ev("error", stage="neo4j", message="Sandbox passed but no cleaned graph JSON was returned")
            elif store is not None:
                t = time.time()
                committed = False
                try:
                    stats = store.upsert_graph(cleaned, run_id)
                    committed = True
                    yield ev("neo4j_commit", stats=stats, latency_ms=int((time.time() - t) * 1000))
                except Exception as e:
                    yield ev("error", stage="neo4j", message=f"upsert_graph failed: {_err_text(e)}")
                if committed:
                    try:
                        yield ev("contagion", paths=store.contagion_paths(), source="neo4j")
                    except Exception as e:
                        yield ev("error", stage="neo4j", message=f"contagion query failed: {_err_text(e)}")
                        yield ev("contagion", paths=local_contagion_paths(cleaned), source="local")
                    try:
                        yield ev("graph", graph=store.fetch_graph_for_viz(), source="neo4j")
                    except Exception as e:
                        yield ev("error", stage="neo4j", message=f"fetch_graph_for_viz failed: {_err_text(e)}")
                        yield ev("graph", graph=cleaned, source="sandbox")
                else:
                    yield ev("contagion", paths=local_contagion_paths(cleaned), source="local")
                    yield ev("graph", graph=cleaned, source="sandbox")
            else:
                yield ev("contagion", paths=local_contagion_paths(cleaned), source="local")
                yield ev("graph", graph=cleaned, source="sandbox")

        yield ev("done", success=passed_res is not None, epochs=epochs_run, run_id=run_id)
    except Exception as e:  # last-resort guard so the UI always gets a terminal event
        yield ev("error", stage="orchestrator", message=_err_text(e), traceback=traceback.format_exc()[-2000:])
        yield ev("done", success=passed_res is not None, epochs=epochs_run, run_id=run_id)
    finally:
        if verifier is not None:
            try:
                verifier.destroy()
            except Exception:
                pass
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception:
                pass


# ---------------------------------------------------------------- replay

def save_replay(events: list, path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def load_replay(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("events", [])
    return list(data)


def iter_replay(events: list, speed: float = 1.0, max_gap: float = 2.5) -> Iterator[dict]:
    """Re-emit recorded events with their original pacing (gaps capped at max_gap seconds / speed)."""
    prev = None
    for e in events:
        ts = e.get("ts")
        if prev is not None and isinstance(ts, (int, float)) and speed > 0:
            time.sleep(min(max(ts - prev, 0.0), max_gap) / speed)
        if isinstance(ts, (int, float)):
            prev = ts
        yield e


# ---------------------------------------------------------------- CLI

def _brief(e: dict) -> str:
    t = e.get("type")
    head = f"[{e.get('ts', 0):7.2f}s] {t:<12}"
    if t == "start":
        return f"{head} run_id={e.get('run_id')} engine_hint={e.get('engine_hint')}"
    if t == "warmup":
        return f"{head} {e.get('engine')} ({e.get('latency_ms')} ms)"
    if t == "forward":
        return f"{head} epoch {e.get('epoch')} model={e.get('model')} code={len(e.get('code') or '')} chars ({e.get('latency_ms')} ms)"
    if t == "sandbox":
        r = e.get("result") or {}
        status = "PASS" if r.get("passed") else "FAIL"
        return (f"{head} epoch {e.get('epoch')} {status} exit={r.get('exit_code')} reward={r.get('reward')} "
                f"{r.get('engine')} ({r.get('latency_ms')} ms) | {_error_summary(r) if not r.get('passed') else 'ok'}")
    if t == "gradient":
        g = (e.get("textual_gradient") or "").replace("\n", " ")
        return f"{head} epoch {e.get('epoch')} ({e.get('latency_ms')} ms) {g[:160]}"
    if t == "passed":
        return f"{head} epoch {e.get('epoch')}"
    if t == "neo4j_commit":
        return f"{head} {e.get('stats')} ({e.get('latency_ms')} ms)"
    if t == "contagion":
        ps = e.get("paths") or []
        top = "; ".join(f"{p.get('holding')} c={p.get('contagion')} at_risk={p.get('at_risk')}" for p in ps[:3])
        return f"{head} {len(ps)} paths [{e.get('source')}] {top}"
    if t == "graph":
        g = e.get("graph") or {}
        return f"{head} {len(g.get('nodes', []))} nodes / {len(g.get('edges', []))} edges [{e.get('source')}]"
    if t == "done":
        return f"{head} success={e.get('success')} epochs={e.get('epochs')}"
    if t == "error":
        return f"{head} stage={e.get('stage')} {e.get('message')}"
    return f"{head} {json.dumps(e, default=str)[:200]}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the EvoGraph self-evolution loop")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--record", default=None, help="save the event stream as a replay JSON file")
    ap.add_argument("--no-neo4j", action="store_true", help="skip Neo4j (contagion computed locally)")
    args = ap.parse_args(argv)

    events: List[dict] = []
    success = False
    try:
        for e in run_evolution(max_epochs=args.epochs, use_neo4j=not args.no_neo4j):
            events.append(e)
            print(_brief(e), flush=True)
            if e.get("type") == "done":
                success = bool(e.get("success"))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
    finally:
        if args.record and events:
            save_replay(events, args.record)
            print(f"replay saved -> {args.record} ({len(events)} events)")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
