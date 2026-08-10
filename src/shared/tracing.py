"""Tracing — one place that knows about MLflow.

Everything else calls `traced(...)` / `set_session(...)` / `record(...)` and never
imports mlflow. If mlflow isn't installed, or HEARTBUG_TRACING != "1", every call
here is a no-op, so tracing can never break a call. Swapping MLflow for another
backend (OTel, Langfuse) means changing ONLY this file.

Enable with:
    HEARTBUG_TRACING=1
    MLFLOW_TRACKING_URI=http://host.docker.internal:5000   # from a container
    MLFLOW_EXPERIMENT=heartbug-callcentre

Run the MLflow server with:
    uvx --python 3.12 --from mlflow mlflow server \                                        
  --host 0.0.0.0 --port 5001 \
  --backend-store-uri sqlite:///mlflow.db \
  --allowed-hosts '*' \
  --cors-allowed-origins '*'

Note: MLflow 3.14+ refuses the old file store ("./mlruns"), so the local default
here is sqlite. Point MLFLOW_TRACKING_URI at a real server for anything shared.
"""
from __future__ import annotations
 
import functools
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Optional
 
log = logging.getLogger("heartbug")
 
_WANT = os.getenv("HEARTBUG_TRACING", "0") == "1"
_mlflow = None
_init_done = False
_init_lock = threading.Lock()
 
 
def _ensure_init() -> None:
    """Import + configure mlflow ON FIRST USE, never at module import. mlflow can be
    slow or even hang on import (plugin/registry discovery), and doing that on the
    host's startup thread would block uvicorn from ever serving. Deferring it here
    means: (a) import cost is paid by the first traced call, not startup, and (b) if
    it fails, tracing just stays off and the request proceeds normally."""
    global _mlflow, _init_done
    if _init_done or not _WANT:
        return
    with _init_lock:
        if _init_done:
            return
        _init_done = True                       # set first: a failure must not retry-loop
        try:
            import mlflow as m                   # deferred — may be slow, never at startup
            m.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
            m.set_experiment(os.getenv("MLFLOW_EXPERIMENT", "heartbug-callcentre"))
            _mlflow = m
            log.info(f"TRACING on → {os.getenv('MLFLOW_TRACKING_URI', 'sqlite:///mlflow.db')}")
        except Exception as e:                   # missing dep, unreachable server, etc.
            _mlflow = None
            log.warning(f"TRACING disabled ({e})") 
 
def enabled() -> bool:
    """True only once mlflow has finished initialising in the background. NEVER blocks
    — if the warm-up thread hasn't finished (or tracing is off/failed), returns False
    and the caller no-ops. This guarantees no request ever waits on mlflow."""
    return _mlflow is not None
 
 
# Warm mlflow in the BACKGROUND at import (mlflow takes ~15s to import). This keeps
# it off the host's startup thread — uvicorn serves immediately — while the import
# finishes in parallel, so by the time the first call arrives tracing is usually
# ready. If it isn't yet, that call simply no-ops (enabled() returns False) rather
# than blocking. Daemon thread so it never holds up shutdown.
if _WANT:
    threading.Thread(target=_ensure_init, name="tracing-init", daemon=True).start()
 
 
# ── span types (mirrors mlflow's, kept as plain strings so callers stay clean) ──
CHAIN, LLM, TOOL, PARSER, AGENT = "CHAIN", "LLM", "TOOL", "PARSER", "AGENT"
 
 
def traced(name: str, span_type: str = CHAIN):
    """Decorate a sync or async function so it becomes a span.
 
    IMPORTANT: `enabled()` is checked at CALL time, not decoration time. MLflow warms
    up in a background thread, so at import (when decorators run) `enabled()` is still
    False — checking it there would hand back the bare function forever, and once
    MLflow finished warming, `set_session`/`update_current_trace` would fire with no
    active span ("No active trace found"). Checking per-call means the span appears as
    soon as tracing is ready."""
    def decorator(fn):
        if _is_async(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                if not enabled():
                    return await fn(*args, **kwargs)
                with _mlflow.start_span(name=name, span_type=span_type) as span:
                    out = await fn(*args, **kwargs)
                    _safe_output(span, out)
                    return out
            return awrapper
 
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled():
                return fn(*args, **kwargs)
            with _mlflow.start_span(name=name, span_type=span_type) as span:
                out = fn(*args, **kwargs)
                _safe_output(span, out)
                return out
        return wrapper
    return decorator
 
 
@contextmanager
def span(name: str, span_type: str = CHAIN, **attributes):
    """Inline span for a block of work: `with span("slot_check", have=...): ...`"""
    if not enabled():
        yield _NullSpan()
        return
    with _mlflow.start_span(name=name, span_type=span_type) as s:
        for k, v in attributes.items():
            _set(s, k, v)
        yield _SpanHandle(s)
 
 
def set_session(call_id: str, **metadata) -> None:
    """Group every trace of one phone call under a session id, and stamp per-trace
    metadata (role, clinic, verified…).
 
    We set the session id TWO ways on purpose:
      * `session_id=` — MLflow 3.x's first-class session field.
      * `mlflow.trace.session` in metadata — the key the trace-list "Session" COLUMN
        actually reads. Empirically this is what populated the column before; the
        native param alone left the column blank. Setting both = column shows AND the
        proper session field is populated.
    The role/clinic/verified fields ride along in metadata for per-trace inspection.
    """
    if not enabled():
        return
    try:
        meta = {"mlflow.trace.session": call_id}
        meta.update({k: str(v) for k, v in metadata.items() if v is not None})
        _mlflow.update_current_trace(session_id=call_id, metadata=meta)
    except Exception:
        # older MLflow without the session_id kwarg → fall back to metadata only
        try:
            _mlflow.update_current_trace(metadata=meta)
        except Exception:
            pass
 
 
def record_usage(usage, model: str = "") -> None:
    """Record token usage from an OpenAI response on the current span AND roll it up
    onto the trace, so MLflow's Token Usage / Cost dashboards populate.
 
    MLflow reads the standardised `mlflow.chat.tokenUsage` span attribute for its
    per-trace and aggregate token panels; setting it on each LLM span is what makes
    the empty dashboard fill in. We also stash raw counts as plain attributes so
    they're visible in the span table even without the standard key."""
    if not enabled() or usage is None:
        return
    try:
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        tt = getattr(usage, "total_tokens", pt + ct) or (pt + ct)
        # cached input tokens — OpenAI reports how many of the prompt tokens were
        # served from the prompt cache (billed at ~10%). Non-zero here = caching is
        # working. Nested under prompt_tokens_details on the usage object.
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        span = _mlflow.get_current_active_span()
        if span is not None:
            # the standard key MLflow's dashboards read
            span.set_attribute("mlflow.chat.tokenUsage", {
                "input_tokens": pt, "output_tokens": ct, "total_tokens": tt})
            # plain mirrors for the span table / ad-hoc queries
            span.set_attribute("llm.input_tokens", pt)
            span.set_attribute("llm.output_tokens", ct)
            span.set_attribute("llm.total_tokens", tt)
            # cache visibility: raw count + hit rate. cache_hit_rate > 0 in the span
            # table means prompt caching is working for this call.
            span.set_attribute("llm.cached_tokens", cached)
            span.set_attribute("llm.cache_hit_rate", round(cached / pt, 3) if pt else 0.0)

            if model:
                span.set_attribute("mlflow.chat.model", model)
    except Exception:
        pass 
 
def record(**attributes) -> None:
    """Attach attributes to the CURRENT span (whatever it is)."""
    if not enabled():
        return
    try:
        s = _mlflow.get_current_active_span()
        if s is not None:
            for k, v in attributes.items():
                _set(s, k, v)
    except Exception:
        pass
 
 
# ── internals ──────────────────────────────────────────────────────────────────
def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)
 
 
def _set(span_obj, key: str, value: Any) -> None:
    try:
        span_obj.set_attribute(key, value if _simple(value) else _short(value))
    except Exception:
        pass
 
 
def _simple(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) or v is None
 
 
def _short(v: Any, limit: int = 2000) -> str:
    text = str(v)
    return text if len(text) <= limit else text[:limit] + "…"
 
 
def _safe_output(span_obj, out: Any) -> None:
    try:
        span_obj.set_outputs(out if isinstance(out, (dict, list, str)) else _short(out))
    except Exception:
        pass
 
 
class _NullSpan:
    def set(self, **_: Any) -> None: ...
 
 
class _SpanHandle:
    def __init__(self, s) -> None:
        self._s = s
 
    def set(self, **attributes: Any) -> None:
        for k, v in attributes.items():
            _set(self._s, k, v)