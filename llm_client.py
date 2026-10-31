"""OpenAI-compatible LLM client: Nosana (Ollama) primary, optional fallback endpoint.

Public API (see CONTRACT.md):
    chat(role, messages, temperature=0.1, json_mode=False, timeout=120) -> str
    model_name(role) -> str
    health() -> dict
    last_call_info / get_last_call_info()  -> which endpoint + model served the last call

.env is re-read on every call, so model names can be changed without restarting the app.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI
import openai

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_PLACEHOLDER_MODELS = {"", "tbd", "none", "null", "auto"}
_DEFAULT_FALLBACK_MODELS = {"forward": "gpt-4o-mini", "backward": "gpt-4o"}

# Updated in place after every chat() call (safe to `from llm_client import last_call_info`).
last_call_info: Dict[str, Any] = {}
_tls = threading.local()

# base_url -> (timestamp, [model ids]) ; short TTL so a freshly pulled model shows up quickly
_models_cache: Dict[str, Any] = {}
_MODELS_TTL_S = 30.0


# ---------------------------------------------------------------- helpers
def _reload_env() -> None:
    load_dotenv(_ENV_PATH, override=True)


def _norm_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and urlparse(url).path in ("", "/"):
        url += "/v1"  # Ollama's OpenAI-compatible API lives under /v1
    return url


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def _provider_label(url: str, model: str = "") -> str:
    h = (_host(url) or "").lower()
    # Local SSH relay (scripts/nosana_relay.py, port 11435) -> Ollama on the Nosana RTX 5090
    if ":11435" in (url or "") or "qwen3.8" in (model or "").lower():
        return f"Nosana (RTX 5090, {model or 'qwen3.8:27b'})"
    if "nos.ci" in h or "nosana" in h:
        return "Nosana"
    if "openai.com" in h:
        return "OpenAI"
    if "anthropic" in h:
        return "Anthropic"
    if h in ("localhost", "127.0.0.1", "0.0.0.0"):
        return "Agent API stand-in" if ":8787" in (url or "") else "Local"
    return h or "unknown"


def _short_host(url: str) -> str:
    h = _host(url) or ""
    return (h[:14] + "…" + h[-16:]) if len(h) > 34 else h


def _endpoints(role: str) -> List[Dict[str, str]]:
    """Ordered list of endpoints to try for this role."""
    role = "backward" if role == "backward" else "forward"
    eps: List[Dict[str, str]] = []

    base = _norm_base_url(os.getenv("LLM_BASE_URL", ""))
    if base:
        eps.append({
            "name": "primary",
            "base_url": base,
            "api_key": os.getenv("LLM_API_KEY", "") or "ollama",
            "model": os.getenv("FORWARD_MODEL" if role == "forward" else "BACKWARD_MODEL", "")
                     or os.getenv("FORWARD_MODEL", ""),
        })

    fb_key = os.getenv("FALLBACK_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    fb_base = os.getenv("FALLBACK_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "")
    if fb_key or fb_base:
        fb_model = (os.getenv("FALLBACK_FORWARD_MODEL" if role == "forward" else "FALLBACK_BACKWARD_MODEL", "")
                    or os.getenv("FALLBACK_MODEL", "")
                    or _DEFAULT_FALLBACK_MODELS[role])
        eps.append({
            "name": "fallback",
            "base_url": _norm_base_url(fb_base or "https://api.openai.com/v1"),
            "api_key": fb_key or "none",
            "model": fb_model,
        })
    if os.getenv("LLM_PREFER", "").strip().lower() == "fallback":
        eps = [e for e in eps if e["name"] == "fallback"] or eps
    return eps


def _client(ep: Dict[str, str], timeout: float) -> OpenAI:
    return OpenAI(api_key=ep["api_key"], base_url=ep["base_url"], timeout=timeout, max_retries=0)


def _list_models(ep: Dict[str, str], timeout: float = 5) -> List[str]:
    now = time.time()
    cached = _models_cache.get(ep["base_url"])
    if cached and now - cached[0] < _MODELS_TTL_S:
        return cached[1]
    ids = [m.id for m in _client(ep, timeout).models.list().data]
    _models_cache[ep["base_url"]] = (now, ids)
    return ids


def _resolve_model(ep: Dict[str, str]) -> str:
    m = (ep.get("model") or "").strip()
    if m.lower() not in _PLACEHOLDER_MODELS:
        return m
    try:
        ids = _list_models(ep)
    except Exception as e:  # e.g. Nosana Ollama read-only FS 500
        raise RuntimeError(
            f"Model not set (FORWARD_MODEL/BACKWARD_MODEL is '{m or 'empty'}') and auto-discovery via "
            f"{_short_host(ep['base_url'])}/v1/models failed: {type(e).__name__}: {_err_text(e)}"
        ) from None
    if not ids:
        raise RuntimeError(f"No models served at {_short_host(ep['base_url'])}; pull one or set FORWARD_MODEL in .env")
    return ids[0]


def _err_text(e: Exception, limit: int = 300) -> str:
    s = str(e).replace("\n", " ")
    return s[:limit]


def _strip_think(text: str) -> str:
    """Remove reasoning blocks from qwen3 / deepseek-r1 style output."""
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    # Only a closing tag (template ate the opening one): drop everything up to the last </think>
    if re.search(r"</think>", text, flags=re.I):
        text = re.split(r"</think>", text, flags=re.I)[-1]
    # Unclosed <think> (truncated output): drop the dangling reasoning
    text = re.sub(r"<think>.*\Z", "", text, flags=re.S | re.I)
    return text.strip()


def _with_no_think(messages: list, model: str) -> list:
    """qwen3 models: append ' /no_think' to the last user message to disable thinking."""
    if "qwen3" not in (model or "").lower():
        return messages
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            if "/no_think" not in m["content"]:
                m["content"] = m["content"].rstrip() + " /no_think"
            break
    return msgs


def _is_response_format_rejection(e: Exception) -> bool:
    if isinstance(e, (openai.BadRequestError, openai.UnprocessableEntityError)):
        return True
    msg = str(e).lower()
    return "response_format" in msg or "json_object" in msg


def _set_last(info: Dict[str, Any]) -> None:
    _tls.info = dict(info)
    last_call_info.clear()
    last_call_info.update(info)


def get_last_call_info() -> Dict[str, Any]:
    """Info about the last chat() call in this thread (falls back to the global one)."""
    return dict(getattr(_tls, "info", None) or last_call_info)


# ---------------------------------------------------------------- public API
def model_name(role: str) -> str:
    """Model that chat(role) would use on the first available endpoint (no network unless model is TBD)."""
    _reload_env()
    errors = []
    for ep in _endpoints(role):
        try:
            return _resolve_model(ep)
        except Exception as e:
            errors.append(_err_text(e))
    return "unknown" if not errors else f"unresolved ({errors[0][:80]})"


def chat(role: str, messages: list, temperature: float = 0.1, json_mode: bool = False, timeout: int = 180) -> str:
    """Chat completion with automatic fallback. Returns the assistant text (may be '')."""
    _reload_env()
    eps = _endpoints(role)
    if not eps:
        raise RuntimeError("No LLM endpoint configured: set LLM_BASE_URL (Nosana) or OPENAI_API_KEY in .env")

    errors: List[str] = []
    for i, ep in enumerate(eps):
        t0 = time.time()
        try:
            model = _resolve_model(ep)
            client = _client(ep, timeout)
            kwargs: Dict[str, Any] = dict(model=model, messages=_with_no_think(messages, model),
                                          temperature=temperature)
            json_native = False
            if json_mode:
                try:
                    resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                    json_native = True
                except Exception as e:
                    if not _is_response_format_rejection(e):
                        raise
                    resp = client.chat.completions.create(**kwargs)
            else:
                resp = client.chat.completions.create(**kwargs)

            text = _strip_think((resp.choices[0].message.content or "") if resp.choices else "")
            provider = _provider_label(ep["base_url"], model)
            usage = getattr(resp, "usage", None)
            served = getattr(resp, "model", "") or ""
            display = provider if provider.startswith("Nosana (") else f"{provider}, {model}"
            if provider == "Agent API stand-in":
                m = re.search(r"claude-[a-z]+", served)
                display = f"Agent API stand-in, {m.group(0) if m else model}"
            _set_last({
                "role": role,
                "endpoint": ep["name"],
                "provider": provider,
                "host": _short_host(ep["base_url"]),
                "model": model,
                "display": display,
                "served_model": served,
                "latency_ms": int((time.time() - t0) * 1000),
                "fallback_used": i > 0,
                "primary_error": errors[0] if errors else None,
                "json_mode_native": json_native,
                "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
                "no_think": "qwen3" in model.lower(),
            })
            return text
        except Exception as e:
            errors.append(f"{ep['name']}@{_short_host(ep['base_url'])}: {type(e).__name__}: {_err_text(e)}")
            continue

    _set_last({"role": role, "endpoint": None, "provider": None, "model": None, "display": "LLM unavailable",
               "fallback_used": len(eps) > 1, "primary_error": errors[0] if errors else None})
    raise RuntimeError("All LLM endpoints failed -> " + " | ".join(errors))


def health() -> dict:
    """{"ok", "base_url_host", "models", "error", ...extras}. Never raises."""
    out: Dict[str, Any] = {"ok": False, "base_url_host": "", "models": [], "error": None,
                           "provider": None, "forward_model": None, "backward_model": None,
                           "primary_ok": False, "fallback": None}
    try:
        _reload_env()
        eps_f = _endpoints("forward")
        if not eps_f:
            out["error"] = "No LLM endpoint configured (LLM_BASE_URL / OPENAI_API_KEY)"
            return out
        primary = eps_f[0]
        out["base_url_host"] = _host(primary["base_url"])
        out["provider"] = _provider_label(primary["base_url"], primary.get("model", ""))
        try:
            out["models"] = _list_models(primary, timeout=10)
            out["primary_ok"] = True
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {_err_text(e)}"
        if out["primary_ok"]:
            for role in ("forward", "backward"):
                try:
                    ep = _endpoints(role)[0]
                    m = _resolve_model(ep)
                    out[f"{role}_model"] = m
                    if out["models"] and m not in out["models"] and m.split(":")[0] not in [x.split(":")[0] for x in out["models"]]:
                        out["error"] = (out["error"] or "") + f" {role} model '{m}' not in served models;"
                except Exception as e:
                    out["error"] = f"{type(e).__name__}: {_err_text(e)}"
        if len(eps_f) > 1:
            fb = eps_f[1]
            out["fallback"] = {"provider": _provider_label(fb["base_url"]), "base_url_host": _host(fb["base_url"]),
                               "model": fb["model"]}
        out["ok"] = bool(out["primary_ok"] or out["fallback"])
        if out["error"]:
            out["error"] = out["error"].strip()
    except Exception as e:  # absolute safety net
        out["error"] = f"health() internal error: {type(e).__name__}: {_err_text(e)}"
    return out
