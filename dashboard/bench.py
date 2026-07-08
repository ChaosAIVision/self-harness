"""Prompt-evaluation workbench: run a pasted system prompt against uploaded
call records, score agreement vs ground truth, and keep per-run history.

Reuses the pipeline's real evaluation path (runner.run_task) so a Bench run is
identical to what the pipeline does per record — just driven interactively.
"""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime
from pathlib import Path

import config
from runner import run_task, _normalize_ground_truth
from trace_schema import Trace

BENCH_DIR = config.DATA_DIR / "playground"
RUNS_DIR = BENCH_DIR / "runs"

STEP_TYPE = {
    1: "làm_dịu", 2: "làm_rõ", 3: "làm_hài_lòng",
    4: "làm_dịu", 5: "làm_rõ", 6: "làm_hài_lòng",
    7: "làm_dịu", 8: "làm_rõ", 9: "làm_hài_lòng",
}
# Minimal intake contract: a record needs only `input` + `label` (the ground
# truth). Everything else is optional and auto-filled to this canonical shape,
# so the same intake serves any task — you paste INPUT + LABEL and nothing else.
# `label` is handed straight to the scorer; retarget a different task by swapping
# the scorer (runner._normalize_ground_truth / _verify), not the intake.
CANONICAL_FIELDS = ("callID", "recordID", "source", "input", "output")

# Live state for the currently-running (or last) bench run.
bench_state: dict = {
    "running": False,
    "run_id": None,
    "label": None,
    "total": 0,
    "done": 0,
    "events": [],       # per-record progress events for SSE
    "result": None,      # final result dict once complete
    "error": None,
    "steps": [],         # [{id, type}] inferred from the data (any step count)
}
_lock = threading.Lock()


# ── Data parsing ────────────────────────────────────────────────────
def parse_records(text: str) -> tuple[list[dict], list[str]]:
    """Parse JSONL or a JSON array into records. Returns (records, warnings)."""
    text = (text or "").strip()
    warnings: list[str] = []
    records: list[dict] = []
    if not text:
        return records, ["No data provided."]

    # Try a whole-document JSON array first, else line-by-line JSONL.
    parsed_array = None
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            parsed_array = obj
        elif isinstance(obj, dict):
            parsed_array = [obj]
    except Exception:
        parsed_array = None

    raw_items = parsed_array if parsed_array is not None else []
    if parsed_array is None:
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw_items.append(json.loads(line))
            except Exception:
                warnings.append(f"Line {i}: not valid JSON — skipped.")

    for i, r in enumerate(raw_items, 1):
        rec, warn = _coerce_record(r, i)
        if rec is None:
            warnings.append(warn)
            continue
        records.append(rec)
    return records, warnings


def _coerce_record(r: dict, i: int) -> tuple[dict | None, str | None]:
    """Normalize one raw item to CANONICAL_FIELDS.

    Minimal contract is just {input, label}; label is the ground truth and maps
    to `output`. Missing id/source/recordID are auto-filled. Records already in
    the legacy {callID, recordID, source, input, output} shape pass through
    unchanged. `label` may be a plain string or a JSON object (stringified here,
    so hand-authored labels need no escaping). Extra fields are preserved.
    """
    if not isinstance(r, dict):
        return None, f"Record {i}: not a JSON object — skipped."
    user_input = r.get("input")
    if user_input is None:
        return None, f"Record {i}: missing 'input' — skipped."
    gt = r.get("output")
    if gt is None:
        gt = r.get("label")
    if gt is None:
        return None, f"Record {i}: missing 'label' (ground truth) — skipped."
    if not isinstance(gt, str):
        gt = json.dumps(gt, ensure_ascii=False)
    if not isinstance(user_input, str):
        user_input = json.dumps(user_input, ensure_ascii=False)
    rid = r.get("id")
    call_id = r.get("callID") or rid or f"rec-{i}"
    rec = dict(r)  # keep any extra fields the caller passed
    rec.update({
        "callID": call_id,
        "recordID": r.get("recordID") or rid or call_id,
        "source": r.get("source") or "manual",
        "input": user_input,
        "output": gt,
    })
    return rec, None


def load_sample_records() -> str:
    if config.BENCHMARK_PATH.exists():
        return config.BENCHMARK_PATH.read_text(encoding="utf-8")
    return ""


def load_current_prompt() -> str:
    """Best-effort load of the active harness prompt for a starting point."""
    try:
        import yaml
        cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
        version = cfg.get("version", "")
        pf = cfg.get("system_prompt_file", "")
        p = Path(pf)
        if not p.is_absolute():
            p = config.ROOT / p
        if not p.exists():
            p = config.HARNESS_DIR / "versions" / f"{version}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    fallback = config.PROMPTS_DIR / "system.md"
    return fallback.read_text(encoding="utf-8") if fallback.exists() else ""


# ── Scoring ─────────────────────────────────────────────────────────
def _infer_step_ids(records: list[dict]) -> list[int]:
    """Union of ground-truth step ids across records — lets the scorer adapt to
    3-step, 4-step or 9-step tasks. Falls back to 1–9 if nothing parses yet."""
    ids: set[int] = set()
    for r in records:
        for s in _normalize_ground_truth(r.get("output", "") or ""):
            ids.add(s.id)
    return sorted(ids) or list(range(1, 10))


def _steps_meta(step_ids: list[int]) -> list[dict]:
    """[{id, type}] for the UI. type is the XLTC label when known, else ''."""
    return [{"id": sid, "type": STEP_TYPE.get(sid, "")} for sid in step_ids]


def _build_result(run_id: str, label: str, prompt: str, traces: list[Trace]) -> dict:
    # Step set is data-driven (any count), not hardcoded to 9.
    step_ids = sorted({s.id for t in traces for s in t.ground_truth_steps})
    if not step_ids:
        step_ids = sorted({s.id for t in traces for s in t.parsed_steps}) or list(range(1, 10))
    by_step = {sid: {"matched": 0, "total": 0} for sid in step_ids}
    matrix = []
    disagreements = []
    total_matched = total_steps = 0

    for t in traces:
        gt = {s.id: s.r for s in t.ground_truth_steps}
        pred = {s.id: s.r for s in t.parsed_steps}
        cells = []
        for sid in step_ids:
            g, m = gt.get(sid), pred.get(sid)
            if g is None:
                cells.append({"id": sid, "model": None, "gt": None, "state": "na"})
                continue
            by_step[sid]["total"] += 1
            total_steps += 1
            if m is None:
                cells.append({"id": sid, "model": None, "gt": g, "state": "missing"})
                continue
            agree = (m == g)
            if agree:
                by_step[sid]["matched"] += 1
                total_matched += 1
            cells.append({"id": sid, "model": m, "gt": g, "state": "agree" if agree else "diverge"})
            if not agree:
                gs = next((s for s in t.ground_truth_steps if s.id == sid), None)
                ps = next((s for s in t.parsed_steps if s.id == sid), None)
                disagreements.append({
                    "task_id": t.task_id, "step_id": sid, "step_type": STEP_TYPE.get(sid, ""),
                    "model": m, "gt": g,
                    "model_evidence": (ps.e[:240] if ps else ""),
                    "gt_evidence": (gs.e[:240] if gs else ""),
                    "model_thinking": (t.thinking or "")[:800],
                })
        matrix.append({
            "task_id": t.task_id, "source": t.source, "status": t.status,
            "agreement": (t.verifier.agreement_rate if t.verifier else 0.0),
            "cells": cells,
        })

    by_step_out = {
        str(sid): {
            "matched": v["matched"], "total": v["total"],
            "rate": (v["matched"] / v["total"] if v["total"] else 0.0),
            "step_type": STEP_TYPE.get(sid, ""),
        } for sid, v in by_step.items()
    }
    # by_type is XLTC-specific decoration: only emitted for steps whose id carries
    # a known type. Generic tasks get an empty map and the UI drops the type bars.
    groups: dict[str, list[str]] = {}
    for sid in step_ids:
        stype = STEP_TYPE.get(sid)
        if stype:
            groups.setdefault(stype, []).append(str(sid))
    by_type = {
        stype: (sum(by_step_out[s]["rate"] for s in sids) / len(sids) if sids else 0.0)
        for stype, sids in groups.items()
    }

    return {
        "run_id": run_id,
        "label": label,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": config.MODEL_NAME,
        "n_records": len(traces),
        "n_failed": sum(1 for t in traces if t.status != "ok"),
        "overall": (total_matched / total_steps if total_steps else 0.0),
        "step_ids": step_ids,
        "steps": _steps_meta(step_ids),
        "by_step": by_step_out,
        "by_type": by_type,
        "matrix": matrix,
        "disagreements": disagreements,
        "prompt": prompt,
        "prompt_chars": len(prompt),
    }


def _save_run(result: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{result['run_id']}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Run driver ──────────────────────────────────────────────────────
def _event(kind: str, **data) -> None:
    ev = {"kind": kind, "ts": datetime.now().strftime("%H:%M:%S"), **data}
    with _lock:
        bench_state["events"].append(ev)


def _run_bench_bg(run_id: str, label: str, prompt: str, records: list[dict]) -> None:
    traces: list[Trace] = []
    try:
        _event("start", total=len(records), label=label)
        for i, rec in enumerate(records, 1):
            try:
                t = run_task(rec, prompt, "bench")
            except Exception as e:  # noqa: BLE001 — never let one record kill the run
                _event("record_error", index=i, task_id=rec.get("callID", "?"), error=str(e)[:200])
                continue
            traces.append(t)
            rate = t.verifier.agreement_rate if t.verifier else 0.0
            with _lock:
                bench_state["done"] = i
            _event("record", index=i, total=len(records), task_id=t.task_id,
                   status=t.status, agreement=rate,
                   running_overall=_running_overall(traces))
        result = _build_result(run_id, label, prompt, traces)
        _save_run(result)
        with _lock:
            bench_state["result"] = result
        _event("done", overall=result["overall"], n_records=result["n_records"])
    except Exception as e:  # noqa: BLE001
        with _lock:
            bench_state["error"] = str(e)
        _event("error", error=str(e)[:300])
    finally:
        with _lock:
            bench_state["running"] = False


def _running_overall(traces: list[Trace]) -> float:
    m = sum(t.verifier.steps_matched for t in traces if t.verifier)
    tot = sum(t.verifier.steps_total for t in traces if t.verifier)
    return m / tot if tot else 0.0


def start_run(prompt: str, records: list[dict], label: str) -> str:
    run_id = f"bench_{int(time.time())}"
    steps = _steps_meta(_infer_step_ids(records))
    with _lock:
        bench_state.update(
            running=True, run_id=run_id, label=label,
            total=len(records), done=0, events=[], result=None, error=None,
            steps=steps,
        )
    threading.Thread(target=_run_bench_bg, args=(run_id, label, prompt, records), daemon=True).start()
    return run_id


# ── History ─────────────────────────────────────────────────────────
def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for f in sorted(RUNS_DIR.glob("bench_*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            runs.append({
                "run_id": d["run_id"], "label": d.get("label", ""),
                "created_at": d.get("created_at", ""), "overall": d.get("overall", 0),
                "n_records": d.get("n_records", 0), "model": d.get("model", ""),
                "by_type": d.get("by_type", {}),
            })
        except Exception:
            continue
    return runs


def get_run(run_id: str) -> dict | None:
    f = RUNS_DIR / f"{run_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
