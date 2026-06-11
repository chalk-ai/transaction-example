#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["fastapi", "uvicorn[standard]", "python-dotenv", "chalkcompute>=2.1.1"]
# ///
"""Refund-abuse agent demo UI — the web front end.

This file is pure UI plumbing: a FastAPI server, an SSE event stream, and the
HTML/CSS/JS for the chat + investigation tree. The actual Chalk Compute
integration lives in two interchangeable client modules — chalk_client_chunked
(buffered `investigate_refund`) and chalk_client_generator (streaming
`investigate_refund_streaming`). The UI's "chunked"/"generator" toggle picks
which one; `_producer` is the bridge that calls it and turns the reply into UI
events.

Run:
  ./chalkcompute_agent_demo_ui.py 8123
  open http://localhost:8123
"""

import asyncio
import json
import os
import queue
import re
import threading
import time
import uuid
from urllib.parse import urlencode
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

import chalk_client_chunked    # buffered investigate_refund (returns full text)
import chalk_client_generator  # streaming investigate_refund_streaming (yields chunks)


# ── Reading the agent's text response ────────────────────────────────────────
# The client modules return the agent's flat "{trace}\n\n{verdict}" text.
# The rest is app logic: pull it apart into a verdict and the tool calls.

# The verdict keyword. Feature values in the trace never contain these words, so
# the first match marks the boundary between the trace and the decision.
_VERDICT_RE = re.compile(r"\b(APPROVE|DENY|ESCALATE)\b")

# A trace line is `  name(args) → result`, where result may span multiple lines.
# Leading spaces are 0–2: the agent indents every step two spaces but lstrip()s
# the whole blob, so the FIRST line loses its indent. Call lines are identified by
# the `name(...) →` shape, so flush-left result lines (`user.total_spend: …`) never
# match. Match each call's result up to the next call line or end-of-trace.
_STEP_RE = re.compile(r"^ {0,2}(\w+)\((.*?)\)\s*→\s*(.*?)(?=\n {0,2}\w+\(.*?\)\s*→|\Z)",
                      re.DOTALL | re.MULTILINE)
# key=value pairs in an args string; values may be quoted/bracketed (and contain
# commas), so match those before the bare-token case.
_ARG_RE = re.compile(r"(\w+)=('[^']*'|\"[^\"]*\"|\[[^\]]*\]|[^,]+)")

# Chalk windowed features come back as `name__<seconds>__` (e.g.
# count_withdrawals__2592000__). Humanize the window into a readable unit so the
# tree shows `count_withdrawals · 30d` instead of the raw second count.
_WINDOW_RE = re.compile(r"__(\d+)__")


def _window_label(seconds: str) -> str:
    s = int(seconds)
    for unit, suffix in ((31536000, "y"), (86400, "d"), (3600, "h"), (60, "m")):
        if s % unit == 0:
            return f"{s // unit}{suffix}"
    return f"{s}s"


def _humanize_windows(text: str) -> str:
    return _WINDOW_RE.sub(lambda m: f" · {_window_label(m.group(1))}", text)


# Render total_spend as currency: 48911.08000... -> $48,911.08
_SPEND_RE = re.compile(r"(total_spend:\s*)(\d+(?:\.\d+)?)")


def _format_spend(text: str) -> str:
    return _SPEND_RE.sub(lambda m: f"{m.group(1)}${float(m.group(2)):,.2f}", text)


def trace_block(raw: str) -> str:
    """The leading tool-call trace, i.e. everything before the verdict."""
    m = _VERDICT_RE.search(raw)
    return raw[:m.start()] if m else raw


def split_verdict(raw: str) -> tuple[str | None, str]:
    """Pull (APPROVE|DENY|ESCALATE, reasoning) out of the response.

    Keyed off the first verdict keyword rather than block splitting, because the
    model may put a blank line between the verdict and its reasoning.
    """
    m = _VERDICT_RE.search(raw)
    if not m:
        return None, raw.strip()
    reasoning = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", raw[m.end():]).strip(" :\n-")
    return m.group(1), reasoning or raw[m.start():].strip()


def parse_steps(raw: str) -> list[dict]:
    """Reconstruct the ordered tool calls from the trace."""
    steps = []
    for i, m in enumerate(_STEP_RE.finditer(trace_block(raw))):
        name, args, result = m.group(1), m.group(2), m.group(3)
        steps.append({
            "id": f"s{i}",
            "tool": name,
            "label": _step_label(name, args),
            "args": _parse_args(args),
            "result": _format_spend(_humanize_windows(result.strip())),
        })
    return steps


def _parse_args(args: str) -> dict:
    """Turn an args string like `user_id=1, features='a,b'` into a dict."""
    out: dict = {}
    for k, v in _ARG_RE.findall(args):
        v = v.strip().strip("'\"")
        out[k] = int(v) if v.lstrip("-").isdigit() else v
    return out


def _step_label(name: str, args: str) -> str:
    """Short node title — feature short-names for get_chalk_features."""
    if name == "get_chalk_features":
        feats = re.findall(r"\b\w+\.(\w+)", args)
        if feats:
            shown = ", ".join(feats[:3])
            return f"{shown} +{len(feats) - 3}" if len(feats) > 3 else shown
    return name.replace("_", " ")


# ── Console trace link (every call is traced server-side) ────────────────────
# We can't mint the per-span deep link client-side (operator/span ids are
# server-assigned), but we can deep-link to the scaling group's flame-graph view
# scoped to the call's time window — the user's run sits right at the top.

CONSOLE_BASE = os.environ.get("CHALK_CONSOLE_BASE", "https://chalk.ai").rstrip("/")
CONSOLE_PROJECT = os.environ.get("CHALK_CONSOLE_PROJECT", "cmpnck95f00090hs67kq4n6fb")
ENV_ID = os.environ.get("CHALK_ENVIRONMENT_ID", "clk8fc4d2e1")
_SG_FALLBACK = "investigate-refund"


def trace_url(agent, start_s: float, end_s: float) -> str:
    """Console flame-graph trace view for `agent`, windowed ±5min around the call."""
    vi = getattr(agent, "version_info", None)
    sg = (getattr(vi, "scaling_group_name", "") if vi else "") or _SG_FALLBACK
    qs = urlencode({
        "v": "remote-call-traces",
        "ds": int(start_s * 1000) - 300_000,
        "de": int(end_s * 1000) + 300_000,
        "scalingGroupTraceView": "flame-graph",
    })
    return (f"{CONSOLE_BASE}/projects/{CONSOLE_PROJECT}"
            f"/environments/{ENV_ID}/scaling-groups/{sg}?{qs}")


def _race_modes(user_id: int, reason: str, q: queue.Queue,
                first_chunk_timeout: float = 5.0,
                overall_timeout: float = 180.0):
    """Fire the streaming and chunked agents at once; pick whichever serves.

    Use the streaming result if it produces a first chunk within the timeout;
    otherwise fall back to the chunked result, which has been running in parallel
    the whole time (so the fallback isn't a fresh wait). Returns
    (raw_text, agent_handle, fell_back). Emits the fallback status + a `mode`
    event at the decision point so the UI flips the toggle to "b".
    """
    stream_chunks: list[str] = []
    s_first, s_done, s_err = threading.Event(), threading.Event(), {}
    c_box, c_done, c_err = {}, threading.Event(), {}

    def run_stream():
        try:
            for ch in chalk_client_generator.investigate(user_id, reason):
                stream_chunks.append(ch); s_first.set()
            s_done.set()
        except Exception as e:
            s_err["e"] = e; s_first.set(); s_done.set()

    def run_chunked():
        try:
            c_box["raw"] = chalk_client_chunked.investigate(user_id, reason)
        except Exception as e:
            c_err["e"] = e
        finally:
            c_done.set()

    threading.Thread(target=run_stream, daemon=True).start()
    threading.Thread(target=run_chunked, daemon=True).start()

    # streaming wins if it emits a real first chunk before the timeout
    if s_first.wait(first_chunk_timeout) and not s_err:
        s_done.wait(overall_timeout)
        if not s_err and s_done.is_set():
            return "".join(stream_chunks), chalk_client_generator.agent, False

    # else use the chunked result (already in flight since submit) — silently;
    # the only signal is the toggle quietly flipping s -> b via the `mode` event.
    q.put({"type": "mode", "value": "chunked"})
    c_done.wait(overall_timeout)
    if c_err or "raw" not in c_box:
        raise c_err.get("e", RuntimeError("chunked fallback failed"))
    return c_box["raw"], chalk_client_chunked.agent, True


def _producer(user_id: int, reason: str, q: queue.Queue, mode: str = "chunked") -> None:
    """Call the agent, then narrate the investigation.

    `mode` selects which deployed function the toggle picked:
      - "chunked"   -> chalk_client_chunked   (investigate_refund, buffered string)
      - "generator" -> chalk_client_generator (investigate_refund_streaming, yields)
    Generator can hang, so generator mode races both agents at once (_race_modes):
    streaming wins if its first chunk beats 8s, else we use the chunked result
    that was running in parallel — the demo never freezes. Either path drains to
    the full "{trace}\n\n{verdict}" text; the reveal is staggered for chunked,
    straight through for a live stream.
    """
    paced = mode == "chunked"

    def beat(secs: float) -> None:
        if paced:
            time.sleep(secs)

    try:
        q.put({"type": "status", "text": "Agent investigating…"})
        t0 = time.time()

        if mode == "generator":
            raw, agent, fell_back = _race_modes(user_id, reason, q)
            if fell_back:
                paced = True  # render the chunked fallback like a normal chunked run
        else:
            raw = chalk_client_chunked.investigate(user_id, reason)
            agent = chalk_client_chunked.agent

        url = trace_url(agent, t0, time.time())

        verdict, text = split_verdict(raw)
        steps = parse_steps(raw)

        # Right tree: reveal a node per tool call, paced.
        for s in steps:
            q.put({"type": "tree_node", "id": s["id"], "label": s["label"], "tool": s["tool"]})
            beat(0.5)
            q.put({"type": "tree_node_done", "id": s["id"], "result": s["result"]})
            beat(0.35)

        if verdict:
            beat(0.2)
            q.put({"type": "decision", "verdict": verdict, "text": text, "trace_url": url})
        else:
            q.put({"type": "question", "text": text, "trace_url": url})
    except Exception as e:
        q.put({"type": "error", "message": str(e)})
    finally:
        q.put(None)


def _producer_reply(q: queue.Queue) -> None:
    """investigate_refund is stateless — explain that follow-ups aren't supported."""
    q.put({"type": "question",
           "text": "investigate_refund runs statelessly on Chalk Compute and doesn't "
                   "carry conversation history — start a new investigation to run it again."})
    q.put(None)


async def _sse(q: queue.Queue):
    while True:
        try:
            event = q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue
        if event is None:
            yield "data: [DONE]\n\n"
            return
        yield f"data: {json.dumps(event)}\n\n"


app = FastAPI()


class InvestigateRequest(BaseModel):
    user_id: int
    reason: str
    mode: str = "chunked"   # "chunked" | "generator" (which deployed fn to call)


class ReplyRequest(BaseModel):
    message: str


@app.get("/")
async def index() -> HTMLResponse:
    # no-store so the browser never serves a stale page during iteration/demo
    return HTMLResponse(HTML, headers={"Cache-Control": "no-store"})


@app.post("/investigate")
async def investigate(req: InvestigateRequest) -> StreamingResponse:
    session_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    threading.Thread(target=_producer, args=(req.user_id, req.reason, q, req.mode), daemon=True).start()

    async def stream():
        yield f"data: {json.dumps({'type': 'session', 'id': session_id})}\n\n"
        async for chunk in _sse(q):
            yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/reply/{session_id}")
async def reply(session_id: str, req: ReplyRequest) -> StreamingResponse:
    q: queue.Queue = queue.Queue()
    threading.Thread(target=_producer_reply, args=(q,), daemon=True).start()
    return StreamingResponse(_sse(q), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ACME Corp. Refund Investigator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<!-- Load fonts without blocking render: fetch as a print sheet, then promote to all
     on load. A flaky/blocked font CDN can't stall the page — it falls back to system fonts. -->
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" media="print" onload="this.media='all'">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"></noscript>
<style>
  :root {
    /* Chalk console — light surfaces */
    --bg:          #f7f9f8;
    --surface:     #ffffff;
    --surface2:    #f4f6f5;
    --surface3:    #eef1f0;
    --border:      #e3e6e5;
    --border-strong:#cfd4d2;
    --text:        #173029;   /* black-green / type-primary */
    --text2:       #3c6e65;
    --muted:       #7a8a87;   /* type-secondary */
    --faint:       #a2a5a4;
    /* Chalk greens */
    --accent:      #16883e;   /* bright-green-700 — interactive */
    --accent-hi:   #2aa853;
    --accent-deep: #12654F;   /* primary green stroke */
    --accent-soft: #e5f4e9;   /* bright-green-50 */
    /* Semantic (CDS) */
    --green:       #16a34a;  --green-text:#166534; --green-bg:#dcfce7; --green-bd:#86efac;
    --red:         #e01c40;  --red-text:#991b1b;   --red-bg:#fee2e2;   --red-bd:#fca5a5;
    --amber:       #b45309;  --amber-text:#b45309; --amber-bg:#fef3c7; --amber-bd:#fcd34d;
    --blue:        #2563eb;  --blue-text:#1e40af;  --blue-bg:#e8f1fd;  --blue-bd:#bcd7f5;
    --tool-bg:     #f3f8f5;
    --tool-border: #d3e7db;
    /* Elevation (CDS) */
    --sh-s: 0 1px 2px 0 rgb(16 48 41 / .04);
    --sh-m: 0 2px 8px 0 rgb(16 48 41 / .08);
    --sh-l: 0 8px 24px 0 rgb(16 48 41 / .12);
    --dot:  #d3dbd8;
    --line: #cfd6d3;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text);
    height: 100vh; display: flex; flex-direction: column; overflow: hidden;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  }
  .mono { font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace; }

  /* ── Top-level split layout ── */
  .app-body { display: flex; flex: 1; overflow: hidden; }

  /* ── Left panel ── */
  .left-panel {
    width: 480px; flex-shrink: 0;
    display: flex; flex-direction: column;
    border-right: 1px solid var(--border); overflow: hidden;
    background: var(--surface);
  }

  /* ── Right panel ── */
  .right-panel {
    flex: 1; display: flex; flex-direction: column;
    background-color: var(--bg);
    background-image: radial-gradient(circle, var(--dot) 1px, transparent 1px);
    background-size: 22px 22px;
    overflow: hidden;
  }
  .right-header {
    padding: 16px 24px; border-bottom: 1px solid var(--border); flex-shrink: 0;
    font-size: 11px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--muted);
    display: flex; align-items: center; gap: 8px;
    background: var(--surface);
  }
  .right-header .dotmark { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .right-body {
    flex: 1; overflow: auto; position: relative;
    display: flex; align-items: flex-start; justify-content: safe center;
    padding: 56px 32px;
  }

  /* ── Tree pane: planning / idle hint ── */
  .tree-hint {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
    display: flex; flex-direction: column; align-items: center; gap: 16px;
    color: var(--muted); pointer-events: none; text-align: center; max-width: 320px;
  }
  .tree-hint-mark {
    width: 44px; height: 44px; border-radius: 11px; border: 1px dashed var(--border-strong);
    display: flex; align-items: center; justify-content: center; font-size: 20px; color: var(--faint);
  }
  .tree-hint-text { font-size: 13px; line-height: 1.5; }
  .tree-hint-text b { color: var(--text2); font-weight: 600; }
  .tree-planning .tree-hint-mark { border-style: solid; border-color: var(--accent); color: var(--accent); animation: spin 1.4s linear infinite; }
  .tree-planning .tree-hint-text { color: var(--text2); }

  /* ── Tree canvas ── */
  #treeCanvas {
    position: relative; flex-shrink: 0;
    display: none;
  }
  #treeCanvas.show { animation: canvasIn 0.5s cubic-bezier(.16,1,.3,1); }

  /* ── Tree nodes (shared base) ── */
  .tree-node {
    position: absolute; border-radius: 8px; padding: 11px 14px;
    font-size: 12px; line-height: 1.4;
    border: 1px solid var(--border);
    background: var(--surface);
    box-shadow: var(--sh-s);
    transition: background .3s, border-color .3s, opacity .3s, box-shadow .3s, transform .3s,
                left .35s cubic-bezier(.16,1,.3,1);
  }
  #tree-source, #tree-conclusion { overflow: hidden; }
  .tree-node-label {
    font-size: 9.5px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 5px;
  }
  .tree-node-title { font-size: 12px; font-weight: 600; color: var(--text); letter-spacing: -0.01em; }
  .tree-node-summary {
    font-size: 11px; color: var(--muted); margin-top: 5px;
    line-height: 1.4; word-break: break-word; overflow-wrap: break-word;
    white-space: pre-line;   /* keep each feature on its own line */
  }

  /* ── SOURCE node (position + size set by JS) — the claim, neutral/informational ── */
  #tree-source {
    background: var(--blue-bg); border-color: var(--blue-bd);
  }
  #tree-source .tree-node-label { color: var(--blue); }
  #tree-source .tree-node-title { color: var(--blue-text); font-size: 13px; }
  #tree-source .tree-node-title .mono { font-size: 12px; }

  /* ── H nodes (hypothesis) — left/top set by JS ── */
  .tree-hyp {
    width: 264px; min-height: 100px;
    opacity: 0.4; transform: scale(.97);
  }
  .tree-hyp.active { opacity: 1; transform: scale(1); }
  .tree-hyp.hyp-running {
    opacity: 1; transform: scale(1);
    background: var(--accent-soft); border-color: var(--accent);
    box-shadow: var(--sh-m); animation: glow 1.5s ease-in-out infinite;
  }
  .tree-hyp.hyp-done {
    opacity: 1; transform: scale(1);
    background: var(--surface); border-color: var(--border-strong); box-shadow: var(--sh-s);
  }
  .tree-hyp.hyp-alert {
    opacity: 1; transform: scale(1);
    background: var(--amber-bg); border-color: var(--amber-bd); box-shadow: var(--sh-s);
  }

  .hyp-icon {
    font-size: 15px; display: inline-flex; align-items: center; justify-content: center;
    width: 20px; height: 20px; margin-bottom: 5px; color: var(--faint);
  }
  .hyp-icon.spinning { animation: spin 1s linear infinite; color: var(--accent); }

  /* ── CONCLUSION node (left/top set by JS) — just the verdict ── */
  #tree-conclusion {
    width: 320px;
    display: none;
  }
  #tree-conclusion.show { display: block; animation: popIn 0.45s cubic-bezier(.16,1,.3,1); }
  #tree-conclusion.verdict-approve  { background: var(--green-bg); border-color: var(--green-bd); }
  #tree-conclusion.verdict-deny     { background: var(--red-bg);   border-color: var(--red-bd); }
  #tree-conclusion.verdict-escalate { background: var(--amber-bg); border-color: var(--amber-bd); }
  #tree-conclusion .conc-label {
    font-size: 14px; font-weight: 700; letter-spacing: .06em;
    display: flex; align-items: center; gap: 7px;
  }
  #tree-conclusion.verdict-approve  .conc-label { color: var(--green-text); }
  #tree-conclusion.verdict-deny     .conc-label { color: var(--red-text); }
  #tree-conclusion.verdict-escalate .conc-label { color: var(--amber-text); }

  /* ── SVG connector lines ── */
  .tree-svg {
    position: absolute; top: 0; left: 0;
    width: 100%; height: 100%; pointer-events: none; overflow: visible;
  }
  .tree-svg line, .tree-svg path {
    stroke: var(--line); stroke-width: 1.5; fill: none;
    transition: stroke .4s, stroke-width .4s;
  }
  .tree-svg .edge-active  { stroke: var(--accent); stroke-width: 2; }
  .tree-svg .edge-visited { stroke: var(--line); stroke-width: 1.5; }  /* same as the rails */
  .tree-svg .edge-done    { stroke: var(--green); stroke-width: 2; }
  .tree-svg .edge-alert   { stroke: var(--amber); stroke-width: 2; }
  .tree-svg .edge-deny    { stroke: var(--red);   stroke-width: 2; }

  /* ── Header ── */
  .header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 15px 24px; border-bottom: 1px solid var(--border); flex-shrink: 0;
    background: var(--surface);
  }
  .header-left { display: flex; align-items: center; gap: 11px; }
  .header-title { font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
  .header-tagline {
    display: flex; align-items: center; gap: 7px;
    margin-left: 3px; padding-left: 14px; border-left: 1px solid var(--border);
    font-size: 12px; font-weight: 400; color: var(--text2); letter-spacing: 0;
    font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
  }
  .header-tagline .dotmark { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .logo {
    width: 28px; height: 28px;
    background: linear-gradient(160deg, #177F65 0%, #12654F 100%);
    border-radius: 7px; display: flex; align-items: center; justify-content: center;
    font-size: 14px; font-weight: 700; color: #fff;
    box-shadow: var(--sh-s);
  }
  .header-right { display: flex; align-items: center; gap: 12px; }
  .new-inv-btn {
    background: transparent; color: var(--text2); border: 1px solid var(--border);
    border-radius: 8px; padding: 7px 14px; font-size: 13px; font-weight: 500;
    font-family: inherit; cursor: pointer; white-space: nowrap;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
  }
  .new-inv-btn:hover { background: var(--surface2); border-color: var(--border-strong); color: var(--text); }

  /* ── User selector ── */
  .user-sel { position: relative; flex-shrink: 0; }
  .user-sel-btn {
    display: flex; align-items: center; gap: 8px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 11px 14px;
    color: var(--text); font-size: 13px; font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
    cursor: pointer; white-space: nowrap; transition: border-color 0.15s, box-shadow 0.15s; user-select: none;
  }
  .user-sel-btn:hover:not(:disabled) { border-color: var(--border-strong); box-shadow: var(--sh-s); }
  .user-sel-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .user-sel-chevron { color: var(--muted); font-size: 10px; transition: transform 0.15s; }
  .user-sel-btn.open .user-sel-chevron { transform: rotate(180deg); }
  .user-dropdown {
    position: absolute; top: calc(100% + 8px); left: 0; right: 0;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 6px; min-width: 268px;
    box-shadow: 0 12px 28px rgb(16 48 41 / .16);
    display: flex; flex-direction: column; gap: 2px;
    z-index: 100; animation: rise 0.15s ease;
  }
  .user-option {
    display: flex; flex-direction: column; align-items: stretch; gap: 5px;
    padding: 10px 12px; border-radius: 7px; cursor: pointer; transition: background 0.1s; user-select: none;
  }
  .user-option:hover { background: var(--surface2); }
  .user-option-id { font-size: 13px; font-weight: 600; font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace; }
  .user-option-reason { font-size: 12.5px; color: var(--text2); }

  /* ── Chat ── */
  .chat { flex: 1; overflow-y: auto; padding: 28px 24px; display: flex; flex-direction: column; gap: 22px; }
  .chat::-webkit-scrollbar { width: 5px; }
  .chat::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 4px; }

  /* ── Fresh-start view (full-width centered hero + composer) ── */
  .start-view {
    flex: 1; overflow: auto;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 40px 24px;
    background-color: var(--bg);
    background-image: radial-gradient(circle, var(--dot) 1px, transparent 1px);
    background-size: 22px 22px;
  }
  .start-inner { width: 100%; max-width: 560px; display: flex; flex-direction: column; align-items: center; }
  .start-mark {
    width: 56px; height: 56px; border-radius: 14px;
    background: linear-gradient(160deg, #177F65 0%, #12654F 100%);
    display: flex; align-items: center; justify-content: center;
    font-size: 26px; color: #fff; box-shadow: var(--sh-m); margin-bottom: 20px;
    animation: rise .4s ease;
  }
  .start-title { font-size: 22px; font-weight: 600; color: var(--text); letter-spacing: -0.02em; margin-bottom: 9px; text-align: center; animation: rise .45s ease; }
  .start-sub { font-size: 14px; line-height: 1.55; color: var(--muted); text-align: center; max-width: 420px; margin-bottom: 28px; animation: rise .5s ease; }

  /* Composer card */
  .composer {
    width: 100%; background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px; box-shadow: var(--sh-l); padding: 18px;
    display: flex; flex-direction: column; gap: 12px; animation: rise .55s ease;
  }
  .composer-row { display: flex; gap: 10px; align-items: stretch; }
  .composer .user-sel { flex: 1; }
  .composer .user-sel-btn { width: 100%; height: 100%; justify-content: space-between; }
  .composer-go {
    background: linear-gradient(180deg, #177F65 0%, #12654F 100%); color: #fff; border: none;
    border-radius: 9px; padding: 12px; font-size: 14px; font-weight: 600; font-family: inherit;
    cursor: pointer; transition: filter .15s, box-shadow .15s; box-shadow: var(--sh-s);
  }
  .composer-go:hover:not(:disabled) { filter: brightness(1.08); box-shadow: var(--sh-m); }
  .composer-go:active:not(:disabled) { filter: brightness(.95); }
  .composer-go:disabled { opacity: .4; cursor: not-allowed; box-shadow: none; }

  /* Bubbles */
  .msg-user {
    align-self: flex-end; max-width: 420px;
    background: var(--accent-soft); border: 1px solid var(--green-bd);
    border-radius: 14px 14px 4px 14px; padding: 11px 15px; animation: rise 0.2s ease;
  }
  .msg-user-reason { font-size: 14px; line-height: 1.4; color: var(--text); }
  .msg-user-reason .mono { font-size: 13px; }
  .msg-user-claim  { font-size: 13px; line-height: 1.4; color: var(--text2); margin-top: 5px; }
  .msg-reply {
    align-self: flex-end; max-width: 420px;
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 14px 14px 4px 14px; padding: 11px 15px; font-size: 14px; line-height: 1.4; animation: rise 0.2s ease;
  }
  .msg-question {
    align-self: flex-start; max-width: 480px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px 14px 14px 4px; padding: 12px 16px; font-size: 14px; line-height: 1.55; animation: rise 0.2s ease;
    box-shadow: var(--sh-s);
  }
  .msg-agent { align-self: flex-start; max-width: 620px; width: 100%; display: flex; flex-direction: column; gap: 8px; animation: rise 0.2s ease; }

  /* ── Tool card ── */
  .tool-card {
    background: var(--tool-bg); border: 1px solid var(--tool-border);
    border-left: 3px solid var(--accent); border-radius: 8px;
    padding: 10px 14px; font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace; font-size: 12px; animation: rise 0.15s ease;
    word-break: break-word; overflow-wrap: anywhere;
  }
  .tool-header { display: flex; align-items: center; gap: 7px; color: var(--accent); margin-bottom: 3px; }
  .tool-fn   { font-weight: 600; }
  .tool-args { color: var(--muted); }
  .tool-result-row { margin-top: 7px; padding-top: 7px; border-top: 1px solid var(--tool-border); display: flex; align-items: center; gap: 7px; }
  .tool-result-row.pending { color: var(--muted); }
  .tool-result-row.done    { color: var(--green-text); }

  /* ── Agent status line ── */
  .msg-status {
    align-self: flex-start; display: flex; align-items: center; gap: 11px;
    padding: 4px 2px; font-size: 14px; color: var(--text2); animation: rise 0.2s ease;
  }
  .msg-status .thinking { padding: 0; }

  /* ── Thinking dots ── */
  .thinking { display: flex; align-items: center; gap: 5px; padding: 8px 2px; }
  .dot { width: 6px; height: 6px; background: var(--accent); border-radius: 50%; animation: pulse 1.2s ease-in-out infinite; }
  .dot:nth-child(2) { animation-delay: .2s; }
  .dot:nth-child(3) { animation-delay: .4s; }

  /* ── Verdict ── */
  .verdict-card { border-radius: 12px; padding: 16px 20px; border: 1px solid; animation: rise 0.25s ease; }
  .verdict-approve  { background: var(--green-bg); border-color: var(--green-bd); }
  .verdict-deny     { background: var(--red-bg);   border-color: var(--red-bd); }
  .verdict-escalate { background: var(--amber-bg); border-color: var(--amber-bd); }
  .verdict-label { font-size: 18px; font-weight: 700; letter-spacing: .04em; margin-bottom: 6px; }
  .verdict-approve  .verdict-label { color: var(--green-text); }
  .verdict-deny     .verdict-label { color: var(--red-text);   }
  .verdict-escalate .verdict-label { color: var(--amber-text); }
  .verdict-text { font-size: 13px; color: var(--text2); line-height: 1.55; }
  .trace-link { display: inline-flex; align-items: center; gap: 4px; margin-top: 12px;
    font-size: 12px; font-weight: 600; color: var(--accent); text-decoration: none;
    border-top: 1px solid var(--line); padding-top: 10px; width: 100%; }
  .trace-link:hover { text-decoration: underline; }

  /* ── Error ── */
  .error-card { background: var(--red-bg); border: 1px solid var(--red-bd); border-radius: 8px; padding: 10px 14px; font-size: 13px; color: var(--red-text); }

  /* ── Mode toggle (tiny, bottom-right): "s" streaming · "b" batch ── */
  .pacing-toggle {
    position: fixed; bottom: 12px; right: 14px; z-index: 200;
    display: flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; padding: 0;
    background: var(--surface); border: 1px solid var(--border); color: var(--faint);
    border-radius: 50%; font-size: 11px; font-weight: 600;
    font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
    cursor: pointer; opacity: 0.4; user-select: none;
    transition: opacity .15s, color .15s, border-color .15s;
  }
  .pacing-toggle:hover { opacity: 1; color: var(--text2); border-color: var(--border-strong); }

  /* ── Input bar ── */
  .input-bar { padding: 14px 24px; border-top: 1px solid var(--border); display: flex; gap: 10px; flex-shrink: 0; align-items: center; background: var(--surface); }
  .main-input { flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: 9px; padding: 11px 16px; color: var(--text); font-size: 14px; font-family: inherit; outline: none; transition: border-color 0.15s, box-shadow 0.15s; }
  .main-input::placeholder { color: var(--muted); }
  .main-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgb(22 136 62 / .12); }
  .main-input:disabled { opacity: 0.45; }
  .submit-btn {
    background: linear-gradient(180deg, #177F65 0%, #12654F 100%); color: #fff; border: none;
    border-radius: 9px; padding: 11px 22px; font-size: 14px; font-weight: 600; font-family: inherit;
    cursor: pointer; transition: filter 0.15s, box-shadow 0.15s; white-space: nowrap; box-shadow: var(--sh-s);
  }
  .submit-btn:hover:not(:disabled) { filter: brightness(1.08); box-shadow: var(--sh-m); }
  .submit-btn:active:not(:disabled) { filter: brightness(.95); }
  .submit-btn:disabled { opacity: .4; cursor: not-allowed; box-shadow: none; }

  @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes pulse { 0%,80%,100% { transform: scale(.55); opacity: .35; } 40% { transform: scale(1); opacity: 1; } }
  @keyframes spin  { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  @keyframes glow  { 0%,100% { box-shadow: 0 0 0 0 rgb(22 136 62 / 0); } 50% { box-shadow: 0 0 0 5px rgb(22 136 62 / .14); } }
  @keyframes canvasIn { from { opacity: 0; transform: translateY(10px) scale(.99); } to { opacity: 1; transform: none; } }
  @keyframes popIn { from { opacity: 0; transform: scale(.9); } 60% { transform: scale(1.02); } to { opacity: 1; transform: scale(1); } }
  @keyframes floaty { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; }
  }
</style>
</head>
<body>

<!-- ── Top header spanning full width ── -->
<div class="header">
  <div class="header-left">
    <div class="logo">A</div>
    <span class="header-title">ACME Corp. Refund Investigator</span>
    <span class="header-tagline"><span class="dotmark"></span>Built on the Chalk AI Data Platform</span>
  </div>
  <div class="header-right">
    <button id="newInvBtn" class="new-inv-btn" onclick="dismiss()" style="display:none">New investigation</button>
  </div>
</div>

<!-- ── Fresh-start view (full-width hero + composer) ── -->
<div class="start-view" id="startView">
  <div class="start-inner">
    <div class="start-mark">⬡</div>
    <div class="start-title">ACME Corp. Refund Investigator</div>
    <div class="start-sub">Pick a refund claim to investigate.</div>

    <div class="composer">
      <div class="composer-row">
        <div class="user-sel" id="userSel">
          <button class="user-sel-btn" id="userSelBtn" onclick="toggleDropdown()">
            <span id="userSelLabel">Select a claim</span>
            <span class="user-sel-chevron">▾</span>
          </button>
          <div class="user-dropdown" id="userDropdown" style="display:none">
            <div class="user-option" onclick="selectUser(1, 'My order never arrived', '10482')">
              <span class="user-option-id">#10482 · user_id=1</span>
              <span class="user-option-reason">“My order never arrived”</span>
            </div>
            <div class="user-option" onclick="selectUser(2, 'I received the wrong item', '10517')">
              <span class="user-option-id">#10517 · user_id=2</span>
              <span class="user-option-reason">“I received the wrong item”</span>
            </div>
            <div class="user-option" onclick="selectUser(9, 'Item arrived damaged', '10538')">
              <span class="user-option-id">#10538 · user_id=9</span>
              <span class="user-option-reason">“Item arrived damaged”</span>
            </div>
          </div>
        </div>
      </div>
      <button id="startBtn" class="composer-go" onclick="startInvestigation()" disabled>Investigate →</button>
    </div>
  </div>
</div>

<!-- ── Split body (hidden until investigation starts) ── -->
<div class="app-body" id="appBody" style="display:none">

  <!-- Left panel: chat -->
  <div class="left-panel">
    <div class="chat" id="chat"></div>

    <div class="input-bar">
      <input id="mainInput" class="main-input" type="text"
             placeholder="Ask a follow-up question…"
             disabled
             onkeydown="if(event.key==='Enter')sendReply()">
      <button id="submitBtn"  class="submit-btn"  onclick="sendReply()" disabled>Send →</button>
    </div>
  </div><!-- /left-panel -->

  <!-- Right panel: hypothesis tree -->
  <div class="right-panel">
    <div class="right-header"><span class="dotmark"></span>Investigation Tree</div>
    <div class="right-body">

      <!-- Idle / planning hint (absolute-centered) -->
      <div class="tree-hint" id="treeHint">
        <div class="tree-hint-mark" id="treeHintMark">⬡</div>
        <div class="tree-hint-text" id="treeHintText">The agent's <b>hypothesis tree</b> renders here as it investigates.</div>
      </div>

      <!-- Tree canvas — built dynamically from the agent's plan -->
      <div id="treeCanvas"></div>

    </div><!-- /right-body -->
  </div><!-- /right-panel -->

</div><!-- /app-body -->

<!-- Tiny mode toggle: "s" = streaming (generator) · "b" = batch (chunked) -->
<button id="pacingToggle" class="pacing-toggle" onclick="toggleClientMode()"><span id="pacingLabel">b</span></button>

<script>
let selectedUser   = null;
let selectedReason = null;
let selectedOrder  = null;
let currentUserId  = null;
let currentReason  = null;
let currentOrder   = null;
let sessionId      = null;
let mode           = 'idle';
let activeAgentMsg = null;
let activeThinking = null;
let activeStatus   = null;
let useGenerator   = true;  // default to streaming ("s"); reset to "s" each new investigation

// ── Client toggle: chunked (investigate_refund) vs generator (…_streaming) ─────
function toggleClientMode() {
  useGenerator = !useGenerator;
  updateModeLabel();
}
function updateModeLabel() {
  document.getElementById('pacingLabel').textContent = useGenerator ? 's' : 'b';
  document.getElementById('pacingToggle').title = useGenerator
    ? 'streaming — generator (investigate_refund_streaming); click for batch'
    : 'batch — chunked (investigate_refund); click for streaming';
}

function traceLinkHtml(url) {
  if (!url) return '';
  return `<a class="trace-link" href="${esc(url)}" target="_blank" rel="noopener">` +
         `View the function trace ↗</a>`;
}

// ── Dropdown ──────────────────────────────────────────────────────────────────

function toggleDropdown() {
  const btn  = document.getElementById('userSelBtn');
  const menu = document.getElementById('userDropdown');
  const open = menu.style.display !== 'none';
  menu.style.display = open ? 'none' : 'flex';
  btn.classList.toggle('open', !open);
}

// Pick one of the canned refund claims. The mocked reason rides along with the
// selection (shown in the dropdown itself) — no separate input needed.
function selectUser(id, reason, order) {
  selectedUser = id;
  selectedReason = reason;
  selectedOrder = order;

  document.getElementById('userSelLabel').textContent = `#${order} · user_id=${id}`;
  document.getElementById('userSelBtn').className = `user-sel-btn`;
  document.getElementById('userDropdown').style.display = 'none';
  document.getElementById('startBtn').disabled = false;
}

document.addEventListener('click', e => {
  const sel = document.getElementById('userSel');
  if (sel && !sel.contains(e.target)) {
    document.getElementById('userDropdown').style.display = 'none';
    document.getElementById('userSelBtn').classList.remove('open');
  }
});

// ── Mode ──────────────────────────────────────────────────────────────────────

// Follow-up bar only (idle is handled by the start view).
function setMode(m) {
  mode = m;
  const input   = document.getElementById('mainInput');
  const submit  = document.getElementById('submitBtn');

  if (m === 'thinking') {
    input.disabled  = true; submit.disabled = true;
  } else if (m === 'reply') {
    input.disabled    = false; input.placeholder = 'Reply to agent…'; input.value = '';
    submit.disabled   = false;
    input.focus();
  } else if (m === 'done') {
    input.disabled    = false;
    input.placeholder = 'Ask a follow-up question…';
    input.value       = '';
    submit.disabled   = false;
    input.focus();
  }
}

// ── Actions ───────────────────────────────────────────────────────────────────

function startInvestigation() {
  if (!selectedUser) return;
  const reason = selectedReason;

  currentUserId = selectedUser;
  currentReason = reason;
  currentOrder  = selectedOrder;

  // Swap from the fresh-start view to the split investigation view.
  document.getElementById('startView').style.display = 'none';
  document.getElementById('appBody').style.display   = 'flex';
  document.getElementById('chat').innerHTML          = '';
  document.getElementById('newInvBtn').style.display = 'inline-block';
  resetTree();
  showTreeHint('planning');
  setMode('thinking');

  appendUserBubble(selectedUser, reason, selectedOrder);
  activeAgentMsg = appendAgentMsg();
  scrollBottom();

  fetch('/investigate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user_id: selectedUser, reason, mode: useGenerator ? 'generator' : 'chunked'}),
  }).then(res => streamEvents(res)).catch(() => setMode('done'));
}

function sendReply() {
  const input = document.getElementById('mainInput');
  const text  = input.value.trim();
  if (!text || !sessionId) return;

  setMode('thinking');
  appendReplyBubble(text);
  activeAgentMsg = appendAgentMsg();
  scrollBottom();

  fetch(`/reply/${sessionId}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: text}),
  }).then(res => streamEvents(res)).catch(() => setMode('reply'));
}

function dismiss() {
  sessionId = null;
  activeAgentMsg = null; activeThinking = null;
  currentUserId = null; currentReason = null; currentOrder = null;

  resetTree();
  document.getElementById('chat').innerHTML = '';

  // Back to the fresh-start view.
  document.getElementById('newInvBtn').style.display = 'none';
  document.getElementById('appBody').style.display   = 'none';
  document.getElementById('startView').style.display = 'flex';

  // Reset composer.
  selectedUser = null; selectedReason = null; selectedOrder = null;
  document.getElementById('userSelLabel').textContent = 'Select a claim';
  document.getElementById('startBtn').disabled = true;
  useGenerator = true; updateModeLabel();  // each new investigation starts on "s"
  mode = 'idle';
}

// ── SSE stream ────────────────────────────────────────────────────────────────

function streamEvents(res) {
  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  (function read() {
    reader.read().then(({done, value}) => {
      if (done) { setMode('done'); return; }
      buf += decoder.decode(value, {stream: true});
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (raw === '[DONE]') { if (mode === 'thinking') setMode('done'); endTreeIfNoPlan(); return; }
        try { handleEvent(JSON.parse(raw)); } catch (err) { console.error('handleEvent failed', err, raw); }
      }
      scrollBottom();
      read();
    });
  })();
}

// ── Event handler ─────────────────────────────────────────────────────────────

function handleEvent(ev) {
  if (ev.type === 'session') {
    sessionId = ev.id;

  } else if (ev.type === 'status') {
    setStatus(ev.text);

  } else if (ev.type === 'mode') {
    // server fell back (or switched) modes — reflect it on the toggle (this run only)
    useGenerator = ev.value === 'generator';
    updateModeLabel();

  } else if (ev.type === 'tree_node') {
    ensureTreeScaffold();
    addTreeNode({id: ev.id, label: ev.label, tool: ev.tool});
    updateTreeHyp(ev.id, 'running');

  } else if (ev.type === 'tree_node_done') {
    updateTreeHyp(ev.id, 'done', ev.result || '');

  } else if (ev.type === 'question') {
    finalizeStatus('Investigation complete');
    const bubble = document.createElement('div');
    bubble.className = 'msg-question'; bubble.textContent = ev.text;
    activeAgentMsg.appendChild(bubble);
    endTreeIfNoPlan();
    setMode('reply');

  } else if (ev.type === 'decision') {
    finalizeStatus('Investigation complete');
    const v = ev.verdict.toLowerCase();

    const card = document.createElement('div');
    card.className = `verdict-card verdict-${v}`;
    card.innerHTML = `<div class="verdict-label">${esc(ev.verdict)}</div>` +
                     `<div class="verdict-text">${esc(ev.text)}</div>` +
                     traceLinkHtml(ev.trace_url);
    activeAgentMsg.appendChild(card);

    renderTreeConclusion(ev.verdict);
    endTreeIfNoPlan();
    setMode('done');

  } else if (ev.type === 'error') {
    finalizeStatus();
    const card = document.createElement('div');
    card.className = 'error-card'; card.textContent = '⚠ ' + ev.message;
    activeAgentMsg.appendChild(card);
    endTreeIfNoPlan();
    setMode('done');
  }

  scrollBottom();
}

// Left chat: a single status line the agent updates (preparing → executing …),
// with animated dots. The detailed work shows in the tree on the right.
function setStatus(text) {
  if (activeThinking) { activeThinking.remove(); activeThinking = null; }
  if (!activeStatus) {
    activeStatus = document.createElement('div');
    activeStatus.className = 'msg-status';
    const t = document.createElement('span'); t.className = 'status-text';
    activeStatus.appendChild(t);
    activeStatus.appendChild(mkThinking());
    activeAgentMsg.appendChild(activeStatus);
  }
  activeStatus.querySelector('.status-text').textContent = text;
}

// Settle the status line instead of removing it: stop the dots, leave it in place
// (optionally with a final label) so the trail of what the agent did stays visible.
function finalizeStatus(doneText) {
  if (activeThinking) { activeThinking.remove(); activeThinking = null; }
  if (activeStatus) {
    const dots = activeStatus.querySelector('.thinking');
    if (dots) dots.remove();
    if (doneText) activeStatus.querySelector('.status-text').textContent = doneText;
    activeStatus = null;  // keep the element; just stop tracking it
  }
}

// ── DOM helpers ───────────────────────────────────────────────────────────────

function appendUserBubble(userId, reason, order) {
  const el = document.createElement('div');
  el.className = 'msg-user';
  el.innerHTML = `<div class="msg-user-reason">Please investigate ` +
                 `<span class="mono">Order #${esc(order)} · user_id=${esc(userId)}</span></div>` +
                 `<div class="msg-user-claim">“${esc(reason)}”</div>`;
  document.getElementById('chat').appendChild(el);
}

function appendReplyBubble(text) {
  const el = document.createElement('div');
  el.className = 'msg-reply'; el.textContent = text;
  document.getElementById('chat').appendChild(el);
}

function appendAgentMsg() {
  const el = document.createElement('div');
  el.className = 'msg-agent';
  const t = mkThinking(); el.appendChild(t); activeThinking = t;
  activeStatus = null;
  document.getElementById('chat').appendChild(el);
  return el;
}

function mkThinking() {
  const el = document.createElement('div'); el.className = 'thinking';
  el.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
  return el;
}

function scrollBottom() { const c = document.getElementById('chat'); c.scrollTop = c.scrollHeight; }

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Tree helpers (dynamic, N nodes from the agent's plan) ──────────────────────

const NODE_W = 264, NODE_GAP = 20, SRC_W = 300, SRC_H = 64, CONC_W = 320;
const ROW_TOP = 118;   // top y of the hypothesis row
const FAN_Y   = 90;    // y of the horizontal fan rail
const PAD     = 10;    // bottom padding of the canvas

let treeSteps = [];    // [{id,label,tool}, ...]
let edgeCls   = {};    // edge id → highlight class, persisted across redraws
let layout    = null;  // {n, centerX, cx:[...]}
let treeReady = false; // scaffold (source + conclusion + svg) built?

function showTreeHint(state) {
  const hint = document.getElementById('treeHint');
  const mark = document.getElementById('treeHintMark');
  const text = document.getElementById('treeHintText');
  hint.style.display = 'flex';
  if (state === 'planning') {
    hint.classList.add('tree-planning');
    mark.textContent = '↻';
    text.style.display = 'none';   // just the spinner — we're not only waiting on a plan
  } else {
    hint.classList.remove('tree-planning');
    mark.textContent = '⬡';
    text.style.display = '';
    text.innerHTML   = "The agent's <b>hypothesis tree</b> renders here as it investigates.";
  }
}

function hideTreeHint() {
  const hint = document.getElementById('treeHint');
  hint.style.display = 'none';
  hint.classList.remove('tree-planning');
}

// When a verdict/question/error lands but the agent never emitted a plan
// (the deployed function returns a flat verdict, not a step-by-step plan), no
// tree was built — so the planning spinner would otherwise spin forever. The
// trace link lives in the verdict card, so just clear the hint here.
function endTreeIfNoPlan() {
  if (treeSteps.length > 0) return;
  hideTreeHint();
}

// Build the fixed scaffold once (source + hidden conclusion + svg). Hypothesis
// nodes are appended live as the agent issues tool calls.
function ensureTreeScaffold() {
  if (treeReady) return;
  treeReady = true;
  treeSteps = [];
  edgeCls   = {};
  hideTreeHint();

  const canvas = document.getElementById('treeCanvas');
  const reason = currentReason ? `"${esc(currentReason)}"` : '';
  canvas.innerHTML =
      '<svg class="tree-svg" id="treeSvg" xmlns="http://www.w3.org/2000/svg"></svg>'
    + `<div class="tree-node" id="tree-source" style="top:0;width:${SRC_W}px;">`
    +   `<div class="tree-node-label">Source · Order #${esc(currentOrder)}</div>`
    +   `<div class="tree-node-title"><span class="mono">user_id=${esc(currentUserId)}</span> · ${reason}</div>`
    + `</div>`
    + `<div class="tree-node" id="tree-conclusion" style="width:${CONC_W}px;">`
    +   `<div class="conc-label" id="tree-conc-label">—</div>`
    + `</div>`;

  canvas.style.display = 'block';
  canvas.classList.remove('show'); void canvas.offsetWidth; canvas.classList.add('show');
}

// Append one hypothesis node and re-center the row (existing nodes slide).
function addTreeNode(step) {
  if (treeSteps.some(s => s.id === step.id)) return;
  treeSteps.push(step);

  const node = document.createElement('div');
  node.className = 'tree-node tree-hyp';
  node.id = `tree-${step.id}`;
  node.style.top = ROW_TOP + 'px';
  node.innerHTML =
      `<div class="hyp-icon">○</div>`
    + `<div class="tree-node-title">${esc(step.label)}</div>`
    + `<div class="tree-node-summary" id="tree-${esc(step.id)}-summary"></div>`;
  document.getElementById('treeCanvas').appendChild(node);

  relayoutTree();
}

// Recompute the row geometry and reposition source / nodes / conclusion.
function relayoutTree() {
  const n = treeSteps.length;
  if (!n) return;
  const rowW    = n * NODE_W + (n - 1) * NODE_GAP;
  const centerX = rowW / 2;
  const cx      = treeSteps.map((_, i) => i * (NODE_W + NODE_GAP) + NODE_W / 2);
  layout = { n, centerX, cx };

  document.getElementById('treeCanvas').style.width = rowW + 'px';
  const src = document.getElementById('tree-source');
  if (src) src.style.left = (centerX - SRC_W / 2) + 'px';
  treeSteps.forEach((s, i) => {
    const el = document.getElementById(`tree-${s.id}`);
    if (el) el.style.left = (i * (NODE_W + NODE_GAP)) + 'px';
  });
  const conc = document.getElementById('tree-conclusion');
  if (conc) conc.style.left = (centerX - CONC_W / 2) + 'px';

  drawEdges();
}

function svgLine(id, x1, y1, x2, y2) {
  const idAttr = id ? ` id="${id}"` : '';
  const cls    = id && edgeCls[id] ? ` class="${edgeCls[id]}"` : '';
  return `<line${idAttr}${cls} x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
}

// Recompute geometry from measured node heights and (re)draw all connectors.
function drawEdges() {
  if (!layout) return;
  const { n, centerX, cx } = layout;

  const bottoms = treeSteps.map(s => {
    const el = document.getElementById(`tree-${s.id}`);
    return el ? el.offsetTop + el.offsetHeight : ROW_TOP + 100;
  });
  const maxBottom  = Math.max.apply(null, bottoms.concat([ROW_TOP + 100]));
  const collectorY = maxBottom + 24;
  const concTop    = collectorY + 34;

  const conc = document.getElementById('tree-conclusion');
  if (conc) conc.style.top = concTop + 'px';

  const concVisible = conc && conc.classList.contains('show');
  const totalH = (concVisible ? concTop + conc.offsetHeight : collectorY) + PAD;
  document.getElementById('treeCanvas').style.height = totalH + 'px';

  const first = cx[0], last = cx[n - 1];
  let s = '';
  s += svgLine(null, centerX, SRC_H, centerX, FAN_Y);   // source → fan junction
  s += svgLine(null, first, FAN_Y, last, FAN_Y);         // fan rail
  treeSteps.forEach((st, i) => { s += svgLine(`e-in-${st.id}`,  cx[i], FAN_Y,        cx[i], ROW_TOP);    });
  treeSteps.forEach((st, i) => { s += svgLine(`e-out-${st.id}`, cx[i], bottoms[i],   cx[i], collectorY); });
  s += svgLine(null, first, collectorY, last, collectorY); // collector rail
  s += svgLine('e-conc', centerX, collectorY, centerX, concTop);
  document.getElementById('treeSvg').innerHTML = s;
}

function setEdge(id, cls) {
  if (cls) edgeCls[id] = cls; else delete edgeCls[id];
  const e = document.getElementById(id);
  if (!e) return;
  e.classList.remove('edge-active','edge-visited','edge-done','edge-alert','edge-deny');
  if (cls) e.classList.add(cls);
}

function updateTreeHyp(id, status, summary) {
  const node = document.getElementById(`tree-${id}`);
  if (!node) return;
  const icon  = node.querySelector('.hyp-icon');
  const sumEl = document.getElementById(`tree-${id}-summary`);

  node.classList.remove('hyp-running','hyp-done','hyp-alert');

  if (status === 'running') {
    node.classList.add('hyp-running');
    icon.textContent = '↻'; icon.className = 'hyp-icon spinning';
    setEdge(`e-in-${id}`, 'edge-visited');  // keep connectors gray even while running
  } else if (status === 'done') {
    // "done" = lookup completed, not "passed" — keep it neutral so green doesn't
    // imply a clean verdict. The verdict colour lives on the conclusion node only.
    node.classList.add('hyp-done');
    icon.textContent = '✓'; icon.className = 'hyp-icon'; icon.style.color = 'var(--muted)';
    setEdge(`e-in-${id}`, 'edge-visited');
    setEdge(`e-out-${id}`, 'edge-visited');
  } else if (status === 'alert') {
    node.classList.add('hyp-alert');
    icon.textContent = '⚠'; icon.className = 'hyp-icon'; icon.style.color = 'var(--amber)';
    setEdge(`e-in-${id}`, 'edge-alert');
    setEdge(`e-out-${id}`, 'edge-alert');
  }

  if (sumEl && summary) {
    sumEl.textContent = summary.length > 220 ? summary.slice(0, 217) + '…' : summary;
  }
  drawEdges();
}

function renderTreeConclusion(verdict) {
  const v    = verdict.toLowerCase();
  const node = document.getElementById('tree-conclusion');
  if (!node) return;
  node.className = `tree-node verdict-${v}`;
  node.style.display = 'block';
  void node.offsetWidth; node.classList.add('show');

  // The right-hand node is just the verdict; the reasoning lives in the chat card.
  const glyph = v === 'approve' ? '✓' : v === 'deny' ? '✕' : '⚠';
  document.getElementById('tree-conc-label').innerHTML =
    `<span>${glyph}</span><span>${esc(verdict)}</span>`;

  setEdge('e-conc', 'edge-visited');  // keep all connectors uniform gray; colour lives on the node
  drawEdges();
}

function resetTree() {
  treeSteps = []; edgeCls = {}; layout = null; treeReady = false;
  const canvas = document.getElementById('treeCanvas');
  canvas.style.display = 'none'; canvas.classList.remove('show');
  canvas.innerHTML = ''; canvas.style.height = ''; canvas.style.width = '';
  showTreeHint('idle');
}

// Reflect the saved client-mode preference on the toggle at load.
updateModeLabel();
</script>
</body>
</html>"""


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"\n  Refund Intelligence UI → http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
