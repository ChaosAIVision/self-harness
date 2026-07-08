"""Track per-step agreement trends across harness versions."""
from __future__ import annotations
import json
from pathlib import Path
from trace_schema import Trace
import config

TRENDS_DIR = config.DATA_DIR / "trends"


def _step_agreement(traces: list[Trace], step_id: int) -> float:
    total = matched = 0
    for t in traces:
        gt_map = {s.id: s.r for s in t.ground_truth_steps}
        pred_map = {s.id: s.r for s in t.parsed_steps}
        if step_id in gt_map and step_id in pred_map:
            total += 1
            if gt_map[step_id] == pred_map[step_id]:
                matched += 1
    return matched / total if total > 0 else 0.0


def compute_version_snapshot(traces: list[Trace], harness_version: str) -> dict:
    step_types = {"làm_dịu": [1, 4, 7], "làm_rõ": [2, 5, 8], "làm_hài_lòng": [3, 6, 9]}
    by_step = {str(sid): _step_agreement(traces, sid) for sid in range(1, 10)}
    by_type = {}
    for stype, sids in step_types.items():
        rates = [by_step[str(sid)] for sid in sids]
        by_type[stype] = sum(rates) / len(rates) if rates else 0.0
    total_steps = sum(t.verifier.steps_total for t in traces if t.verifier)
    total_matched = sum(t.verifier.steps_matched for t in traces if t.verifier)
    return {
        "harness_version": harness_version,
        "overall": total_matched / total_steps if total_steps else 0.0,
        "by_step": by_step,
        "by_type": by_type,
        "total_calls": len(traces),
        "total_steps": total_steps,
    }


def save_snapshot(snapshot: dict) -> Path:
    TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    path = TRENDS_DIR / f"{snapshot['harness_version']}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_all_snapshots() -> list[dict]:
    if not TRENDS_DIR.exists():
        return []
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(TRENDS_DIR.glob("*.json"))]


def build_trend_context() -> str:
    """Context string showing per-step trends for miner."""
    snapshots = load_all_snapshots()
    if len(snapshots) < 2:
        return ""
    lines = ["PER-STEP AGREEMENT TRENDS:"]
    lines.append(f"  {'Step':<8} " + "  ".join(f"{s['harness_version']:>8}" for s in snapshots))
    step_labels = {1: "dịu", 2: "rõ", 3: "hl", 4: "dịu", 5: "rõ", 6: "hl", 7: "dịu", 8: "rõ", 9: "hl"}
    for sid in range(1, 10):
        values = [s["by_step"].get(str(sid), 0.0) for s in snapshots]
        trend = ""
        if len(values) >= 2:
            delta = values[-1] - values[-2]
            if delta > 0.05:
                trend = " ↑"
            elif delta < -0.05:
                trend = " ↓ REGRESSION"
            elif values[-1] < 0.5:
                trend = " ⚠ STAGNANT"
        formatted = "  ".join(f"{v:>7.0%}" for v in values)
        lines.append(f"  {sid} ({step_labels[sid]:<3}) {formatted}{trend}")
    lines.append("")
    return "\n".join(lines)
