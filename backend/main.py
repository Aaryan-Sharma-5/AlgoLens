"""FastAPI app for AlgoLens — the /grade SSE pipeline.

POST /grade runs the four-stage pipeline as typed Server-Sent Events:

    violations        → AST contract result (deterministic, instant)
    adversarial_trace → single sandboxed run on the LLM's adversarial input
    explanation       → explanation tokens streamed one by one
    socratic          → Socratic follow-up questions
    done              → stream complete

Stage 1 is pure and always emitted first. Stages 2-5 (LLM + sandbox) degrade
gracefully — any failure emits a typed `error` event then `done`, never tearing
the stream (the violations event is already on the wire by then).
"""

import asyncio
import json
from typing import AsyncGenerator

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# Load backend/.env regardless of the directory uvicorn is launched from, so
# GROQ_API_KEY is present before llm_layer's lazy client first reads it.
load_dotenv(Path(__file__).parent / ".env")

from .checker import check_contract
from .instrumenter import sandboxed_run
from .llm_layer import llm_generate, stream_explanation_from_string
from .models import GradeRequest

app = FastAPI(title="AlgoLens", version="1.0.0")

# Permissive CORS: harmless behind the Next.js /api proxy, and lets the backend
# be hit directly during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mandatory on Render — prevents nginx from buffering the SSE stream.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


# Emit an SSE comment this often while a slow stage (LLM call, sandbox run) is in
# flight. Comment lines (`:` prefix) are ignored by the EventSource parser — they
# never reach onmessage — but they keep the browser and any intermediary proxy
# (nginx on Render) from silently dropping an otherwise-idle connection.
_HEARTBEAT_INTERVAL = 5.0


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _heartbeat_until(task: "asyncio.Future") -> AsyncGenerator[str, None]:
    """Yield SSE keepalive comments until `task` finishes.

    `asyncio.wait` never cancels the task on timeout and never consumes its
    result/exception, so there is no `shield`+`wait_for` log noise on failure.
    When the task is done (success OR exception) the loop exits; the caller then
    retrieves it via `task.result()`, which re-raises into the existing handling.
    """
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_INTERVAL)
        if not done:
            yield ": keepalive\n\n"


async def _grade_events(req: GradeRequest) -> AsyncGenerator[str, None]:
    # Stage 1: AST contract — deterministic, no LLM. Drives Monaco decorations.
    try:
        violations = check_contract(req.source, req.pattern)
    except SyntaxError as e:
        yield _sse("error", {"error": "syntax_error", "message": str(e)})
        yield _sse("done", {})
        return
    except ValueError as e:
        # Unknown pattern.
        yield _sse("error", {"error": "invalid_pattern", "message": str(e)})
        yield _sse("done", {})
        return

    yield _sse("violations", violations)

    if not violations:
        yield _sse("done", {"status": "clean", "message": "No violations found."})
        return

    # Pattern-presence rejection: a loopless submission has nothing to instrument,
    # animate, or generate an adversarial input for. Surface the violation (Monaco
    # decoration is already on the wire) and stop before the LLM/sandbox stages.
    if any(v["type"] == "missing_iteration_structure" for v in violations):
        yield _sse(
            "done",
            {
                "status": "no_iteration",
                "message": "No iteration structure found — this code does not implement the selected pattern.",
            },
        )
        return

    # Stage 2: one structured LLM call. A shape failure (ValueError "llm_schema")
    # is distinguished from an unreachable/erroring API so the frontend can show
    # a specific banner for each.
    try:
        llm_task = asyncio.ensure_future(llm_generate(req.source, violations))
        async for hb in _heartbeat_until(llm_task):
            yield hb
        llm_response = llm_task.result()
    except ValueError as e:
        yield _sse("error", {"error": "llm_schema", "message": str(e)})
        yield _sse("done", {})
        return
    except Exception as e:  # noqa: BLE001 — network/auth/etc.
        yield _sse("error", {"error": "llm_unavailable", "message": str(e)})
        yield _sse("done", {})
        return

    # Stages 3-5: sandbox + explanation stream + socratic.
    try:
        adversarial = llm_response["adversarial_input"]

        # Stage 3: single sandboxed execution. sandboxed_run is BLOCKING (it
        # joins a child process for up to ~timeout seconds), so run it in a
        # thread to keep the event loop responsive — otherwise an infinite-loop
        # submission would freeze FastAPI for the whole timeout window.
        loop = asyncio.get_running_loop()
        adv_task = asyncio.ensure_future(
            loop.run_in_executor(
                None,
                sandboxed_run,
                req.source,
                adversarial["array"],
                adversarial["target"],
            )
        )
        async for hb in _heartbeat_until(adv_task):
            yield hb
        adv_result = adv_task.result()
        yield _sse("adversarial_trace", adv_result)

        # Stage 4: stream the explanation produced in stage 2 (no second LLM call).
        async for token in stream_explanation_from_string(llm_response["explanation"]):
            yield _sse("explanation", {"text": token})

        # Stage 5: Socratic questions (pre-generated in stage 2).
        yield _sse("socratic", llm_response["socratic"])
    except Exception as e:  # noqa: BLE001 — surface any pipeline failure to the client
        yield _sse("error", {"error": "pipeline_error", "message": str(e)})

    yield _sse("done", {})


@app.post("/grade")
async def grade(req: GradeRequest) -> StreamingResponse:
    return StreamingResponse(
        _grade_events(req),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
