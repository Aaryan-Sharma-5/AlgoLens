<div align="center">

# 🔎 AlgoLens

### Not a linter. A pedagogue.

Deterministic **AST contract verification** + **live execution-trace animation** + **Socratic feedback** for algorithmic patterns.
No test runner. No Big-O guessing. No LLM in the detection path.

<br>

![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js%2016-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)
![Groq](https://img.shields.io/badge/LLM-Groq%20llama--3.3--70b-F55036?style=for-the-badge&logo=groq&logoColor=white)

![Frontend on Vercel](https://img.shields.io/badge/Frontend-Vercel-000000?style=flat-square&logo=vercel&logoColor=white)
![Backend on Render](https://img.shields.io/badge/Backend-Render-46E3B7?style=flat-square&logo=render&logoColor=white)
![Track](https://img.shields.io/badge/EdTech_3.0-Track_2_Assessment_%26_Feedback-C97832?style=flat-square)
![Status](https://img.shields.io/badge/status-demo_ready-22c55e?style=flat-square)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

**[\[▶ Live Demo\]](https://algo-lens-lac.vercel.app)**  ·  built for the EdTech 3.0 Hackathon

</div>

> **Patterns:** Sliding Window · Two Pointers · Binary Search&nbsp;&nbsp;|&nbsp;&nbsp;**Language:** Python only

---

## Table of contents

- [What it does](#what-it-does)
- [Detected violations](#detected-violations)
- [Architecture](#architecture)
- [Run it locally](#run-it-locally)
- [Test submissions](#test-submissions)
- [Deployment](#deployment)
- [Scaling notes](#scaling-notes)
- [Constraints](#constraints)
- [Known limitations](#known-limitations)

---

## What it does

Paste a Python function, pick a pattern, hit **Grade**:

1. **AST check** — flags pattern-contract violations as inline editor decorations, instantly and deterministically (zero LLM involvement).
2. **Adversarial generation** — one structured Groq call produces a worst-case input *targeted at the detected violation type*, plus the explanation and the Socratic questions.
3. **Sandboxed execution** — runs the submission once on that adversarial input in a memory- and time-capped child process, capturing every loop iteration.
4. **Live trace + Socratic reasoning** — animates the pointers/array against the executing source line, streams a plain-English explanation, then gates a set of Socratic questions behind the animation so reflection follows the failure.

## Detected violations

| Violation | Severity | Meaning |
|---|---|---|
| `implicit_slice_loop` | critical | `arr[i:j]` inside a loop — an implicit second loop |
| `non_halving_search` | critical | binary-search bounds move by ±1, never the midpoint — O(N), not O(log N) |
| `missing_iteration_structure` | critical | no loop at all — a static constant, not an algorithm |
| `nested_loop` | major | nested loop where a single pass was promised |
| `sort_in_loop` | major | sort inside a loop — O(N² log N) |
| `linear_membership_check` | minor | `x in list` inside a loop — hidden O(N) lookup |

---

## Architecture

A single `POST /grade` request drives four autonomous stages and streams each result back over Server-Sent Events the moment it is ready — the browser starts reacting at ~50 ms, long before the pipeline finishes.

```
                          POST /grade
                              │
        ┌─────────────────────┴─────────────────────┐
        │            backend pipeline                │
        │                                            │
        │  1  checker.py      AST contract check ────┼──▶  event: violations
        │       │             (deterministic, no LLM)│         (~50 ms)
        │       ▼                                     │
        │  2  llm_layer.py    one structured Groq call│
        │       │             → explanation           │
        │       │             → adversarial input      │
        │       │             → socratic questions     │
        │       ▼                                     │
        │  3  instrumenter.py instrument + sandboxed ─┼──▶  event: adversarial_trace
        │       │             run on adversarial input│         (~800 ms)
        │       ▼                                     │
        │  4  llm_layer.py    stream explanation ─────┼──▶  event: explanation (tokens)
        │                     release socratic ───────┼──▶  event: socratic
        │                                            │──▶  event: done
        └─────────────────────┬─────────────────────┘
                              │  SSE
                              ▼
                    frontend (Next.js + Monaco)
        violations → editor decorations · trace → Framer-Motion animator
        explanation → streamed panel · socratic → gated reflection
```

**Why this order:** detection runs first and never touches the LLM, so violations appear instantly and reproducibly. The structured LLM call (stage 2) front-loads the adversarial input and Socratic questions so they're ready before execution — the second LLM call only re-streams the explanation token by token alongside the animation. Sandboxed execution (stage 3) runs *once*, on the adversarial input, so the trace the student watches is the worst case the AST predicted.

| Module | Responsibility |
|---|---|
| **`backend/checker.py`** | `ast.NodeVisitor` classes emit `{type, lineno, severity, label}`. A pattern-presence guard rejects loopless submissions outright. |
| **`backend/instrumenter.py`** | Rewrites the AST to record each loop iteration — *prepended* into the loop body so `continue`/`break`/`return` can't skip capture — resolves pointer names dynamically, then runs the code in a `multiprocessing` child with `RLIMIT_AS` (128 MB) and a 3 s timeout. Frame count is capped; partial traces flush on timeout for the infinite-loop demo. |
| **`backend/llm_layer.py`** | One structured Groq (`llama-3.3-70b-versatile`) call returns explanation + adversarial input + Socratic questions; a second call re-streams only the explanation. The LLM never sees execution state — only structured violation metadata. |
| **`backend/main.py`** | The typed SSE pipeline, with keep-alive heartbeats during the LLM/sandbox stages so proxies don't drop the connection. |
| **`frontend/`** | Next.js 16 + Monaco. Editor decorations, a Framer-Motion trace animation synced to the executing source line, a terminal-style pipeline log, and progressive Socratic disclosure. |

Properties that hold **by construction:** detection is reproducible and LLM-free; adversarial generation is violation-type-constrained, not random; execution is isolated and resource-capped; and the four stages are genuinely autonomous — not a chatbot wrapper.

---

## Run it locally

You need a free [Groq API key](https://console.groq.com/keys).

### Option A — two processes (recommended for development)

**Backend** (Python 3.11 or 3.12):

```bash
cd backend
python -m venv venv && source venv/Scripts/activate   # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key_here" > .env
cd ..
uvicorn backend.main:app --reload --port 8000          # run from the repo root
```

**Frontend** (Node ≥ 20.9):

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**. The dev server proxies `/api/*` to the backend,
so only port 3000 is user-facing.

### Option B — one command (Docker)

```bash
cp .env.example .env          # set GROQ_API_KEY
docker compose up --build
```

Frontend at http://localhost:3000, backend health at http://localhost:8000/health.

---

## Test submissions

The AI pipeline only runs when the AST finds a violation. A correct-but-trivial solution legitimately returns *"No anti-patterns detected"* — AlgoLens verifies efficiency structure, not correctness. To see the full pipeline, paste these (select the matching pattern first).

**Sliding Window — slice + nested loop (richest demo, 2 violations):**
```python
def solve(arr, k):
    best = 0
    for i in range(len(arr)):
        for j in range(i, len(arr)):
            best = max(best, sum(arr[i:j]))
    return best
```

**Two Pointers — O(N²) instead of one pass:**
```python
def solve(arr, target):
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            if arr[i] + arr[j] == target:
                return [i, j]
    return []
```

**Binary Search — a linear scan in disguise (`non_halving_search`):**
```python
def solve(arr, target):
    lo = 0
    hi = len(arr) - 1
    while lo <= hi:
        if arr[lo] == target:
            return lo
        lo += 1
    return -1
```

**Pattern-presence guard — rejected, not passed:**
```python
def solve(arr, k):
    return 0
```

**Clean (any pattern's default template)** → green "No anti-patterns detected".

---

## Deployment

Frontend → **Vercel**, backend → **Render**. The backend cannot run on Vercel: its sandbox needs real processes (`multiprocessing` + `RLIMIT_AS`).

### 1. Backend on Render

- Push this repo, then Render → **New + → Blueprint** and select it. The included [`render.yaml`](render.yaml) provisions a Python web service.
- Set **`GROQ_API_KEY`** in the dashboard (it is not in the blueprint).
- Note the service URL, e.g. `https://algolens-backend.onrender.com`.
- **Keep-alive:** Render free tier sleeps after 15 min (≈50 s cold start). Add a free [cron-job.org](https://cron-job.org) job hitting `/health` every 14 minutes.

### 2. Frontend on Vercel

- Vercel → **New Project** → import the repo.
- Set **Root Directory** to `frontend` (Next.js is auto-detected).
- Add env var **`NEXT_PUBLIC_API_URL`** = your Render URL (no trailing slash).
- Deploy. The browser then streams SSE directly from Render (the backend's CORS is open), avoiding any proxy buffering.

---

## Scaling notes

Each grade is a stateless pure function — no database, no session state. The sandbox isolates execution per request in its own process. To scale, run more uvicorn workers or place the backend behind a load balancer; nothing is shared between requests.

## Constraints

- Python submissions only. Source ≤ 50 KB, input arrays ≤ 10,000 elements.
- Child process: 128 MB (`RLIMIT_AS`, Linux), 3 s timeout, captured-frame cap.
- Sandbox builtins are whitelisted and dunder-traversal escapes are blocked at the AST level.

## Known limitations

- Pure-comprehension and recursive solutions have no `For`/`While` statement, so the presence guard treats them as non-iterative (a scoped MVP decision).
- The presence/anti-pattern checks verify efficiency structure, not correctness.