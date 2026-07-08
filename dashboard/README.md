# Self-Harness Dashboard

A FastAPI backend + zero-build single-page frontend for the Self-Harness pipeline.
It reads pipeline artifacts directly from `data/` and `configs/` (no database) and
can launch pipeline rounds with a live SSE log stream.

## Pages

- **Bench** (`#/`, the landing page) — the prompt-evaluation workbench. Paste a
  system prompt, load call records (JSONL), hit **RUN**, and watch the model score
  every call live. Each run produces a **verdict matrix** (calls × 9 steps; filled
  = model agrees with ground truth, hollow = diverges), per-type agreement bars, and
  a disagreement drill-down with transcript evidence. Every run is saved to
  `data/playground/runs/` and joins the **history rail** so you compare run-over-run
  and reload any prompt back into the editor.
- **Pipeline** (`#/pipeline`) — current version, overall agreement, trend line chart, per-step
  heatmap (click a cell → round detail), latest reflection.
- **Round Detail** (`#/round/<version>`) — per-step agreement, mined failures,
  proposals, validation results, reflection, and the prompt diff vs the previous version.
- **Memory** (`#/memory`) — episodic-memory reflections and the failure-pattern tracker.
- **Review** (`#/review`) — card-by-card human review of disagreements with transcript
  context; annotations are saved to `data/feedback/` and feed the pipeline.
- **Runner** (`#/runner`) — configure & start pipeline rounds, watch the live log (SSE).

## Setup

The dashboard depends on `fastapi`, `uvicorn` (plus the pipeline's `openai`,
`pydantic`, `pyyaml`, `python-dotenv`). A pinned virtualenv is the reliable path
on this machine, because the system interpreters have conflicting global packages:

```bash
python3 -m venv .venv                       # from repo root; use a 3.11+ interpreter
.venv/bin/python -m pip install -r requirements.txt
```

Model config is read from the repo-root `.env` (`MODEL_NAME`, `API_KEY`, `BASE_URL`).

## Run

```bash
.venv/bin/python -m uvicorn dashboard.server:app --host 127.0.0.1 --port 8000
# then open http://127.0.0.1:8000
```

## Inputs — what to pass in

Every runnable surface takes its inputs over the API; nothing is hard-coded. The
UI mirrors these exactly (the Runner has a built-in "What input does the pipeline
need?" panel).

### The call-record format (used by Bench **and** Runner data)

Data is **JSONL** (one JSON object per line) or a JSON array. Each record:

```json
{
  "callID":   "C-117557096",        // unique id → becomes task_id
  "recordID": "71166431",
  "source":   "RTN",
  "input":    "Module: xulytuchoi Transcript section: [customer] … [telesales] …",
  "output":   "{\"round1\":{\"step1\":{\"id\":1,\"r\":\"U\",\"e\":\"…\"}, …}}"
}
```

- `input` — the transcript section the model scores.
- `output` — the **ground truth**: 9 steps (`id` 1–9, `r` = `"S"` or `"U"`, `e` = evidence),
  either round-nested (`round1/round2/round3` → `step1..step9`) or a flat list. This is
  what agreement is measured against. Records missing any required field are skipped
  (with a warning), so a bad line never aborts a run.

### Bench — `POST /api/bench/run`

```json
{ "prompt": "<system prompt>", "data": "<jsonl>", "sample": 5, "label": "my run" }
```
`sample` (optional) caps to the first N records; `label` names the run in history.
Runs the prompt against the data and returns the verdict matrix — no promotion, no
harness mutation.

### Runner — `POST /api/run/start` (full self-improvement loop, promotes real versions)

```json
{
  "prompt": "<starting system prompt>",   // becomes base_version's prompt
  "data": "<jsonl records>",              // written as the benchmark + split
  "held_in": 14,        // records for mining + edit selection (0 = auto ~2/3)
  "held_out": 7,        // records for regression check        (0 = auto rest)
  "base_version": "v0.1.0",               // label for the starting prompt
  "max_rounds": 3,                        // how many optimize rounds to try
  "min_improvement": 0.005,               // held_out gain below this = "dry"
  "dry_streak": 2                         // stop after this many dry rounds
}
```
Omit `prompt`/`data` to run on the on-disk harness + benchmark instead. Providing them
overwrites the real harness: the prompt is written to `configs/harness/versions/<base_version>.md`,
the data to `data/benchmark/`, and promotions create real `vX.Y.0` versions + update
`current.yaml`. Results appear on **Pipeline / Memory / Review** (pick the version in
each page's selector). Watch progress on `GET /api/run/logs/{run_id}` (SSE).

### Review — `POST /api/review/submit`

```json
{ "reviewer": "alice", "harness_version": "v0.2.0",
  "items": [ { "task_id": "C-117557096", "step_id": 1, "action": "gt_wrong",
               "reasoning_note": "telesale did acknowledge", "model_said": "S", "ground_truth": "U" } ] }
```
`action` ∈ `agree | override_s | override_u | ambiguous | gt_wrong`.

## API (all JSON, UTF-8)

| Endpoint | Purpose |
|---|---|
| `GET /api/overview` | cards + trend + latest reflection + review counts |
| `GET /api/trends` | per-step / per-type / overall series across versions |
| `GET /api/rounds` | all reflection records |
| `GET /api/rounds/{version}` | traces summary, failures, proposals, validation, reflection |
| `GET /api/prompts/{version}` | prompt text + unified diff vs previous version |
| `GET /api/review/queue` | disagreement counts (total / reviewed / pending / by type) |
| `GET /api/review/items` | paged review items (+ transcript, existing feedback) |
| `POST /api/review/submit` | save human feedback batch |
| `GET /api/review/stats` | reviewer + override statistics |
| `GET /api/reflections` | episodic memory records |
| `GET /api/versions` | harness versions that have a run (for the selectors) |
| `GET /api/bench/defaults` | starter prompt + sample data + model name |
| `POST /api/bench/parse` | validate pasted/uploaded data (count + warnings) |
| `POST /api/bench/run` | run a prompt against records in a background thread |
| `GET /api/bench/status` | current bench run state |
| `GET /api/bench/stream/{run_id}` | SSE stream of per-record progress |
| `GET /api/bench/result` | full result of the last/current run |
| `GET /api/bench/runs` / `GET /api/bench/runs/{id}` | run history + detail |
| `POST /api/run/start` | start pipeline rounds in a background thread |
| `GET /api/run/status` | current run state |
| `POST /api/run/stop` | graceful stop after the current round |
| `GET /api/run/logs/{run_id}` | SSE stream of pipeline log lines |

## Notes

- **Graceful empty state.** Trend snapshots and the review queue are computed
  on the fly from `data/runs/*/traces.json` when the pipeline hasn't written
  `data/trends/` or `data/review_queue/` yet, so the dashboard is useful immediately.
- **Runner side effects.** Starting a run executes real pipeline rounds: it calls
  the model on every benchmark record and can promote a new harness version. Expect
  a few minutes and new files under `data/`.
- **Reset the harness** to the committed sample lineage after experimenting:
  ```bash
  git checkout -- configs/harness/ data/
  git clean -fd configs/harness/versions data/reflections data/trends \
                data/review_queue data/benchmark data/runs/v0.1.0
  ```
