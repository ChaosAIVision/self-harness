"""FastAPI backend for the Self-Harness dashboard.

Reads pipeline artifacts straight from data/ + configs/ (no database) and
exposes them over a small JSON API, plus a background pipeline runner with a
live SSE log stream.

Run:
    python dashboard/server.py
    # or: uvicorn dashboard.server:app --reload --port 8000
"""
from __future__ import annotations

import io
import json
import sys
import time
import difflib
import threading
import contextlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))  # so `import bench` resolves

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import config
from trace_schema import Trace
from trend_tracker import compute_version_snapshot, load_all_snapshots, save_snapshot
from reflector import load_all_reflections
from feedback_collector import (
    extract_disagreements, export_for_review, load_all_feedback,
    save_feedback_batch, generate_preference_pairs,
)
from feedback_schema import StepFeedback, TraceFeedback, FeedbackBatch
import bench

app = FastAPI(title="Self-Harness Dashboard")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def _revalidate_static(request, call_next):
    """Force the browser to revalidate /static assets each load (cheap 304 when
    unchanged) so an edited app.js/style.css is never served stale from cache."""
    resp = await call_next(request)
    if request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp

STATIC_DIR = Path(__file__).parent / "static"

STEP_TYPE = {
    1: "làm_dịu", 2: "làm_rõ", 3: "làm_hài_lòng",
    4: "làm_dịu", 5: "làm_rõ", 6: "làm_hài_lòng",
    7: "làm_dịu", 8: "làm_rõ", 9: "làm_hài_lòng",
}

# ── Pipeline runtime state ──────────────────────────────────────────
pipeline_state: dict = {
    "running": False,
    "run_id": None,
    "logs": [],
    "current_step": None,
    "current_round": 0,
    "max_rounds": 0,
    "stop_requested": False,
    "summary": None,
}


# ── Helpers ─────────────────────────────────────────────────────────
def _current_version() -> str:
    try:
        cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
        return cfg.get("version", "unknown")
    except Exception:
        return "unknown"


def _load_records() -> list[dict]:
    records = []
    if not config.BENCHMARK_PATH.exists():
        return records
    for line in config.BENCHMARK_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _load_traces(version: str) -> list[Trace]:
    path = config.DATA_DIR / "runs" / version / "traces.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Trace.model_validate(t) for t in raw]
    except Exception:
        return []


def _run_versions() -> list[str]:
    """Versions that actually have a traces.json, in natural order."""
    runs_dir = config.DATA_DIR / "runs"
    if not runs_dir.exists():
        return []
    versions = [d.name for d in runs_dir.iterdir() if (d / "traces.json").exists()]
    return sorted(versions)


def _get_snapshots() -> list[dict]:
    """Saved trend snapshots, or computed on the fly from existing runs."""
    snaps = load_all_snapshots()
    if snaps:
        return snaps
    snaps = []
    for v in _run_versions():
        traces = _load_traces(v)
        if traces:
            snaps.append(compute_version_snapshot(traces, v))
    return snaps


def _review_items(version: str) -> list[dict]:
    """Load the review queue for a version, generating it on the fly if absent."""
    path = config.DATA_DIR / "review_queue" / f"{version}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    traces = _load_traces(version)
    return extract_disagreements(traces)


def _transcript_for(task_id: str, records: list[dict]) -> str:
    for r in records:
        if r.get("callID") == task_id or r.get("recordID") == task_id:
            return r.get("input", "")
    return ""


def _log(step: str, message: str) -> None:
    pipeline_state["logs"].append({
        "step": step,
        "message": message,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })


class _LogStream(io.TextIOBase):
    """Captures pipeline stdout line-by-line into pipeline_state logs."""
    def __init__(self):
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                step = pipeline_state.get("current_step") or "pipeline"
                _log(step, line)
        return len(s)

    def flush(self):
        pass


# ── Input setup (prompt + data + split) ─────────────────────────────
def _setup_run_inputs(prompt: str, data_text: str, held_in_n: int | None,
                      held_out_n: int | None, base_version: str) -> dict:
    """Write the provided prompt + dataset into the real harness so the pipeline
    runs on THEM. Returns a small summary. Mutates config paths at runtime."""
    summary: dict = {}

    # 1) Dataset → benchmark file + held_in/held_out split
    if data_text and data_text.strip():
        records, warnings = bench.parse_records(data_text)
        if not records:
            raise ValueError("No valid records in data (each record needs at least: input + label).")
        bench_dir = config.DATA_DIR / "benchmark"
        bench_dir.mkdir(parents=True, exist_ok=True)
        bpath = bench_dir / "current.jsonl"
        bpath.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )
        n = len(records)
        hi = held_in_n if held_in_n and held_in_n > 0 else max(1, round(n * 2 / 3))
        hi = min(hi, n)
        ho = held_out_n if held_out_n and held_out_n > 0 else (n - hi)
        ho = min(ho, max(0, n - hi))
        held_in_ids = list(range(0, hi))
        held_out_ids = list(range(hi, hi + ho)) if ho > 0 else list(range(0, hi))
        (bench_dir / "held_in.yaml").write_text(yaml.dump({"ids": held_in_ids}), encoding="utf-8")
        (bench_dir / "held_out.yaml").write_text(yaml.dump({"ids": held_out_ids}), encoding="utf-8")
        # Redirect config to the uploaded dataset for this process.
        config.BENCHMARK_PATH = bpath
        config.HELD_IN_IDS_FILE = bench_dir / "held_in.yaml"
        config.HELD_OUT_IDS_FILE = bench_dir / "held_out.yaml"
        summary.update(records=n, held_in=len(held_in_ids), held_out=len(held_out_ids), warnings=warnings)

    # 2) Prompt → base harness version + current.yaml
    if prompt and prompt.strip():
        versions_dir = config.HARNESS_DIR / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        vf = versions_dir / f"{base_version}.md"
        vf.write_text(prompt, encoding="utf-8")
        (config.HARNESS_DIR / "current.yaml").write_text(
            yaml.dump({"version": base_version, "system_prompt_file": str(vf)}, allow_unicode=True),
            encoding="utf-8",
        )
        summary.update(base_version=base_version, prompt_chars=len(prompt))

    return summary


# ── Pipeline background runner ──────────────────────────────────────
def _run_pipeline_bg(run_id: str, max_rounds: int, min_improvement: float, dry_limit: int):
    from orchestrator import run_round  # imported lazily so read-only API stays cheap

    stream = _LogStream()
    dry = 0
    try:
        with contextlib.redirect_stdout(stream):
            for rnd in range(1, max_rounds + 1):
                if pipeline_state["stop_requested"]:
                    _log("stopped", f"Stop requested — halting before round {rnd}.")
                    break
                pipeline_state["current_round"] = rnd
                pipeline_state["current_step"] = f"round {rnd}"

                current_cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
                current_version = current_cfg["version"]
                prompt_file = current_cfg.get("system_prompt_file", "prompts/system.md")
                prompt_path = Path(prompt_file)
                if not prompt_path.is_absolute():
                    prompt_path = config.ROOT / prompt_path
                if not prompt_path.exists():
                    prompt_path = config.HARNESS_DIR / "versions" / f"{current_version}.md"
                current_prompt = prompt_path.read_text(encoding="utf-8")

                _log("round", f"Round {rnd}/{max_rounds} — harness {current_version}")
                result = run_round(current_prompt, current_version)
                pipeline_state["summary"] = result

                if not result.get("promoted"):
                    dry += 1
                    _log("round", f"Round {rnd}: no promotion (dry streak {dry}/{dry_limit}).")
                    if dry >= dry_limit:
                        _log("round", f"Converged — stopping after {rnd} rounds.")
                        break
                else:
                    delta_out = result.get("final_out", 0) - result.get("baseline_out", 0)
                    _log("round", f"Round {rnd}: {current_version} → {result['new_version']} "
                                  f"(+{delta_out:.1%} held_out)")
                    if delta_out < min_improvement:
                        dry += 1
                        _log("round", f"Marginal (<{min_improvement:.1%}) — counts as dry ({dry}/{dry_limit}).")
                        if dry >= dry_limit:
                            _log("round", f"Converged — stopping after {rnd} rounds.")
                            break
                    else:
                        dry = 0
    except Exception as e:  # noqa: BLE001
        _log("error", f"Pipeline crashed: {type(e).__name__}: {e}")
    finally:
        pipeline_state["running"] = False
        pipeline_state["current_step"] = None
        _log("done", "Pipeline finished.")


# ── Pipeline endpoints ──────────────────────────────────────────────
@app.post("/api/run/start")
async def start_run(body: dict | None = None):
    """Start the self-improvement pipeline.

    Body (all optional — omit prompt/data to run on the on-disk harness):
      prompt: str            starting system prompt (becomes base_version)
      data: str              JSONL/JSON-array of records
                             (each: input + label; id/source auto-filled)
      held_in: int           # of records for mining/selection  (default ~2/3)
      held_out: int          # of records for regression check   (default rest)
      base_version: str      version label for the starting prompt (default "v0.1.0")
      max_rounds: int        max optimization rounds             (default 3)
      min_improvement: float held_out gain below this counts as dry (default 0.005)
      dry_streak: int        consecutive dry rounds before stop  (default 2)
    """
    if pipeline_state["running"]:
        return JSONResponse({"error": "Pipeline already running"}, status_code=409)
    body = body or {}
    max_rounds = int(body.get("max_rounds", 3))
    min_improvement = float(body.get("min_improvement", 0.005))
    dry_limit = int(body.get("dry_streak", 2))
    base_version = (body.get("base_version") or "v0.1.0").strip()

    try:
        setup = _setup_run_inputs(
            body.get("prompt", ""), body.get("data", ""),
            body.get("held_in"), body.get("held_out"), base_version,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=400)

    run_id = f"run_{int(time.time())}"
    pipeline_state.update(
        running=True, run_id=run_id, logs=[], current_step="starting",
        current_round=0, max_rounds=max_rounds, stop_requested=False, summary=None,
    )
    if setup:
        parts = []
        if "records" in setup:
            parts.append(f"data={setup['records']} records (held_in {setup['held_in']} / held_out {setup['held_out']})")
        if "base_version" in setup:
            parts.append(f"prompt={setup['prompt_chars']} chars → {setup['base_version']}")
        _log("start", "Inputs: " + "; ".join(parts))
        for w in setup.get("warnings", [])[:3]:
            _log("start", f"⚠ {w}")
    _log("start", f"Pipeline started (run_id={run_id}, max_rounds={max_rounds}, "
                  f"min_improvement={min_improvement:.1%}, dry_streak={dry_limit})")
    threading.Thread(target=_run_pipeline_bg,
                     args=(run_id, max_rounds, min_improvement, dry_limit), daemon=True).start()
    return {"run_id": run_id, "status": "started", "setup": setup}


@app.get("/api/run/status")
async def run_status():
    return {
        "running": pipeline_state["running"],
        "run_id": pipeline_state["run_id"],
        "current_round": pipeline_state["current_round"],
        "max_rounds": pipeline_state["max_rounds"],
        "current_step": pipeline_state["current_step"],
        "log_count": len(pipeline_state["logs"]),
        "summary": pipeline_state["summary"],
    }


@app.post("/api/run/stop")
async def stop_run():
    pipeline_state["stop_requested"] = True
    return {"stopping": True, "note": "Will halt after the current round."}


@app.get("/api/run/logs/{run_id}")
async def run_logs(run_id: str):
    async def event_generator():
        import asyncio
        last_idx = 0
        while True:
            logs = pipeline_state.get("logs", [])
            while last_idx < len(logs):
                yield f"data: {json.dumps(logs[last_idx], ensure_ascii=False)}\n\n"
                last_idx += 1
            if not pipeline_state.get("running") and last_idx >= len(logs):
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            await asyncio.sleep(0.4)
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Data endpoints ──────────────────────────────────────────────────
@app.get("/api/overview")
async def overview():
    version = _current_version()
    snapshots = _get_snapshots()
    reflections = load_all_reflections()
    items = _review_items(version)
    feedback = load_all_feedback(version)
    reviewed_ids = {(tf.task_id, sf.step_id) for tf in feedback for sf in tf.steps}
    pending = [i for i in items if (i.get("task_id"), i.get("step_id")) not in reviewed_ids]
    return {
        "current_version": version,
        "overall_agreement": snapshots[-1]["overall"] if snapshots else 0,
        "total_rounds": len(snapshots),
        "total_calls_evaluated": snapshots[-1]["total_calls"] if snapshots else 0,
        "pending_reviews": len(pending),
        "reviewed": len(items) - len(pending),
        "trend": snapshots,
        "latest_reflection": reflections[-1].model_dump() if reflections else None,
    }


@app.get("/api/versions")
async def versions():
    """All harness versions that have a run, newest first, + the current one."""
    vs = _run_versions()
    cur = _current_version()
    if cur not in vs:
        vs.append(cur)
    return {"current": cur, "versions": sorted(set(vs), reverse=True)}


@app.get("/api/trends")
async def trends():
    snapshots = _get_snapshots()
    versions = [s["harness_version"] for s in snapshots]
    by_step = {str(sid): [s["by_step"].get(str(sid), 0) for s in snapshots] for sid in range(1, 10)}
    by_type = {}
    for stype in ["làm_dịu", "làm_rõ", "làm_hài_lòng"]:
        by_type[stype] = [s["by_type"].get(stype, 0) for s in snapshots]
    overall = [s["overall"] for s in snapshots]
    return {"versions": versions, "by_step": by_step, "by_type": by_type, "overall": overall}


@app.get("/api/rounds")
async def rounds():
    return [r.model_dump() for r in load_all_reflections()]


@app.get("/api/rounds/{version}")
async def round_detail(version: str):
    traces = _load_traces(version)
    by_step = {}
    passed = failed = 0
    for t in traces:
        if t.verifier and t.verifier.passed:
            passed += 1
        else:
            failed += 1
    for sid in range(1, 10):
        matched = total = 0
        for t in traces:
            gt = {s.id: s.r for s in t.ground_truth_steps}
            pred = {s.id: s.r for s in t.parsed_steps}
            if sid in gt and sid in pred:
                total += 1
                if gt[sid] == pred[sid]:
                    matched += 1
        by_step[str(sid)] = {
            "matched": matched, "total": total,
            "rate": matched / total if total else 0.0,
            "step_type": STEP_TYPE[sid],
        }

    failures_path = config.DATA_DIR / "mined_failures" / f"{version}.json"
    proposals_path = config.DATA_DIR / "proposals" / f"{version}.json"
    failures = json.loads(failures_path.read_text(encoding="utf-8")) if failures_path.exists() else {}
    proposals = json.loads(proposals_path.read_text(encoding="utf-8")) if proposals_path.exists() else []

    # validation_<next>.json is keyed by the promoted version, so scan for a match
    validation = []
    reports_dir = config.DATA_DIR / "reports"
    if reports_dir.exists():
        for vf in sorted(reports_dir.glob("validation_*.json")):
            try:
                data = json.loads(vf.read_text(encoding="utf-8"))
                rows = data if isinstance(data, list) else data.get("results", [])
                if rows and any(r.get("harness_version", "").startswith(version) for r in rows):
                    validation = rows
                    break
            except Exception:
                continue

    reflections = load_all_reflections()
    reflection = next((r for r in reflections if r.from_version == version), None)

    return {
        "version": version,
        "traces_summary": {
            "total": len(traces), "passed": passed, "failed": failed, "by_step": by_step,
        },
        "failures": failures,
        "proposals": proposals,
        "validation": validation,
        "reflection": reflection.model_dump() if reflection else None,
    }


@app.get("/api/prompts/{version}")
async def prompt_diff(version: str):
    version_path = config.HARNESS_DIR / "versions" / f"{version}.md"
    if not version_path.exists():
        return JSONResponse({"error": f"Version {version} not found"}, status_code=404)
    current = version_path.read_text(encoding="utf-8")

    versions = sorted(p.stem for p in (config.HARNESS_DIR / "versions").glob("*.md"))
    diff = ""
    prev_version = None
    if version in versions:
        idx = versions.index(version)
        if idx > 0:
            prev_version = versions[idx - 1]
            prev_text = (config.HARNESS_DIR / "versions" / f"{prev_version}.md").read_text(encoding="utf-8")
            diff = "".join(difflib.unified_diff(
                prev_text.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile=prev_version, tofile=version,
            ))
    return {"version": version, "previous_version": prev_version,
            "system_prompt": current, "diff": diff}


# ── Review endpoints ────────────────────────────────────────────────
@app.get("/api/review/queue")
async def review_queue(version: str | None = None):
    version = version or _current_version()
    items = _review_items(version)
    feedback = load_all_feedback(version)
    reviewed_ids = {(tf.task_id, sf.step_id) for tf in feedback for sf in tf.steps}
    pending = [i for i in items if (i.get("task_id"), i.get("step_id")) not in reviewed_ids]
    by_type: dict[str, int] = {}
    for i in items:
        st = STEP_TYPE.get(i.get("step_id"), "unknown")
        by_type[st] = by_type.get(st, 0) + 1
    return {
        "version": version,
        "total_disagreements": len(items),
        "reviewed": len(items) - len(pending),
        "pending": len(pending),
        "by_step_type": by_type,
    }


@app.get("/api/review/items")
async def review_items(version: str | None = None, step_type: str | None = None,
                       source: str | None = None, pending_only: bool = False,
                       page: int = 1, limit: int = 10):
    version = version or _current_version()
    items = _review_items(version)
    records = _load_records()

    feedback = load_all_feedback(version)
    fb_map = {(tf.task_id, sf.step_id): sf for tf in feedback for sf in tf.steps}

    enriched = []
    for i in items:
        sid = i.get("step_id")
        if step_type and STEP_TYPE.get(sid) != step_type:
            continue
        if source and i.get("source") != source:
            continue
        existing = fb_map.get((i.get("task_id"), sid))
        if pending_only and existing is not None:
            continue
        item = dict(i)
        item["step_type"] = STEP_TYPE.get(sid, "unknown")
        item["transcript_excerpt"] = _transcript_for(i.get("task_id"), records)[:1200]
        item["existing_feedback"] = existing.model_dump() if existing else None
        enriched.append(item)

    total = len(enriched)
    start = (page - 1) * limit
    return {"items": enriched[start:start + limit], "total": total, "page": page}


@app.post("/api/review/submit")
async def submit_review(data: dict):
    reviewer = data.get("reviewer", "default")
    version = data.get("harness_version") or _current_version()

    by_task: dict[str, list] = {}
    for item in data.get("items", []):
        by_task.setdefault(item["task_id"], []).append(item)

    feedbacks = []
    for task_id, items in by_task.items():
        steps = []
        for item in items:
            action = item.get("action", "agree")
            override, is_ambiguous, gt_is_wrong = None, False, False
            if action == "override_s":
                override = "S"
            elif action == "override_u":
                override = "U"
            elif action == "ambiguous":
                is_ambiguous = True
            elif action == "gt_wrong":
                gt_is_wrong = True
                override = item.get("model_said", "S")
            steps.append(StepFeedback(
                step_id=item["step_id"],
                model_said=item.get("model_said", "?"),
                ground_truth=item.get("ground_truth", "?"),
                human_override=override,
                reasoning_note=item.get("reasoning_note", ""),
                is_ambiguous=is_ambiguous,
                gt_is_wrong=gt_is_wrong,
            ))
        feedbacks.append(TraceFeedback(
            task_id=task_id,
            record_id=items[0].get("record_id", ""),
            source=items[0].get("source", ""),
            harness_version=version,
            reviewer=reviewer,
            timestamp=datetime.now().isoformat(),
            steps=steps,
        ))

    batch = FeedbackBatch(
        session_id=f"ui_review_{version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        harness_version=version, reviewer=reviewer, feedbacks=feedbacks,
    )
    save_feedback_batch(batch)
    return {"saved": True, "session_id": batch.session_id,
            "items_saved": len(data.get("items", []))}


@app.get("/api/review/stats")
async def review_stats():
    feedbacks = load_all_feedback()
    total = overrides = ambiguous = gt_wrong = 0
    by_reviewer: dict[str, int] = {}
    for tf in feedbacks:
        by_reviewer[tf.reviewer] = by_reviewer.get(tf.reviewer, 0) + len(tf.steps)
        for sf in tf.steps:
            total += 1
            if sf.human_override:
                overrides += 1
            if sf.is_ambiguous:
                ambiguous += 1
            if sf.gt_is_wrong:
                gt_wrong += 1
    pref_dir = config.DATA_DIR / "preference_pairs"
    pairs = 0
    if pref_dir.exists():
        for f in pref_dir.glob("*.jsonl"):
            pairs += sum(1 for _ in f.open(encoding="utf-8"))
    return {
        "total_reviewed": total, "overrides": overrides, "ambiguous": ambiguous,
        "gt_wrong": gt_wrong, "by_reviewer": by_reviewer,
        "preference_pairs_generated": pairs,
    }


@app.get("/api/reflections")
async def reflections():
    return [r.model_dump() for r in load_all_reflections()]


# ── Bench (prompt eval workbench) ───────────────────────────────────
@app.get("/api/bench/defaults")
async def bench_defaults():
    """Starter prompt + sample data so the bench is usable on first load."""
    return {
        "prompt": bench.load_current_prompt(),
        "sample_data": bench.load_sample_records(),
        "current_version": _current_version(),
        "model": config.MODEL_NAME,
    }


def _config_public() -> dict:
    """Connection config safe to send to the browser (never the raw key)."""
    return {
        "model_name": config.MODEL_NAME,
        "base_url": config.BASE_URL,
        "has_key": bool(config.API_KEY),
    }


def _persist_env(updates: dict) -> None:
    """Update-or-append the given KEY=value pairs in .env, preserving the rest."""
    envp = ROOT / ".env"
    lines = envp.read_text(encoding="utf-8").splitlines() if envp.exists() else []
    seen: set[str] = set()
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(ln)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    envp.write_text("\n".join(out) + "\n", encoding="utf-8")


@app.get("/api/config")
async def get_config():
    return _config_public()


@app.post("/api/config")
async def set_config(data: dict):
    """Set the model connection at runtime. Applies in-process immediately
    (runner._client reads config on each call) and persists to .env by default.
    An empty api_key means 'keep the current one'."""
    updates: dict[str, str] = {}
    model = (data.get("model_name") or "").strip()
    base = (data.get("base_url") or "").strip()
    key = (data.get("api_key") or "").strip().rstrip(",")
    if model:
        config.MODEL_NAME = model; updates["MODEL_NAME"] = model
    # base_url may legitimately be cleared back to empty
    if "base_url" in data:
        config.BASE_URL = base; updates["BASE_URL"] = base
    if key:
        config.API_KEY = key; updates["API_KEY"] = key
    persisted = False
    err = None
    if data.get("persist", True) and updates:
        try:
            _persist_env(updates)
            persisted = True
        except Exception as e:  # noqa: BLE001
            err = str(e)
    return {"ok": True, "persisted": persisted, "error": err, **_config_public()}


@app.post("/api/bench/parse")
async def bench_parse(data: dict):
    """Validate uploaded/pasted data without running."""
    records, warnings = bench.parse_records(data.get("data", ""))
    preview = [{"task_id": r["callID"], "source": r.get("source", "")} for r in records[:50]]
    return {"count": len(records), "warnings": warnings, "preview": preview}


@app.post("/api/bench/run")
async def bench_run(data: dict):
    if bench.bench_state["running"]:
        return JSONResponse({"error": "A bench run is already in progress"}, status_code=409)
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)
    records, warnings = bench.parse_records(data.get("data", ""))
    if not records:
        return JSONResponse({"error": "No valid records", "warnings": warnings}, status_code=400)
    sample = data.get("sample")
    if isinstance(sample, int) and sample > 0:
        records = records[:sample]
    label = (data.get("label") or "").strip() or f"run of {len(records)} records"
    run_id = bench.start_run(prompt, records, label)
    return {"run_id": run_id, "status": "started", "n_records": len(records),
            "steps": bench.bench_state.get("steps", []), "warnings": warnings}


@app.get("/api/bench/status")
async def bench_status():
    s = bench.bench_state
    return {
        "running": s["running"], "run_id": s["run_id"], "label": s["label"],
        "total": s["total"], "done": s["done"], "error": s["error"],
        "has_result": s["result"] is not None, "steps": s.get("steps", []),
    }


@app.get("/api/bench/stream/{run_id}")
async def bench_stream(run_id: str):
    async def gen():
        import asyncio
        last = 0
        while True:
            events = bench.bench_state.get("events", [])
            while last < len(events):
                yield f"data: {json.dumps(events[last], ensure_ascii=False)}\n\n"
                last += 1
            if not bench.bench_state.get("running") and last >= len(events):
                yield f"data: {json.dumps({'kind': 'closed'})}\n\n"
                break
            await asyncio.sleep(0.3)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/bench/result")
async def bench_result():
    return bench.bench_state.get("result") or JSONResponse({"error": "No result yet"}, status_code=404)


@app.get("/api/bench/runs")
async def bench_runs():
    return bench.list_runs()


@app.get("/api/bench/runs/{run_id}")
async def bench_run_detail(run_id: str):
    r = bench.get_run(run_id)
    if not r:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return r


# ── Static frontend ─────────────────────────────────────────────────
@app.get("/")
async def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse({"error": "Frontend not built"}, status_code=404)
    html = idx.read_text(encoding="utf-8")

    # Cache-bust asset URLs by file mtime so an edited app.js/style.css is always
    # fetched fresh — a stale JS/CSS is never served from the browser cache.
    def _ver(name: str) -> int:
        p = STATIC_DIR / name
        return int(p.stat().st_mtime) if p.exists() else 0

    html = html.replace("/static/app.js", f"/static/app.js?v={_ver('app.js')}")
    html = html.replace("/static/style.css", f"/static/style.css?v={_ver('style.css')}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
