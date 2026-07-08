"""Human-in-the-Loop feedback collection, storage, and integration."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from feedback_schema import (
    StepFeedback, TraceFeedback, FeedbackBatch,
    GroundTruthPatch, PreferencePair,
)
from trace_schema import Trace
import config


FEEDBACK_DIR = config.DATA_DIR / "feedback"
PREFERENCE_DIR = config.DATA_DIR / "preference_pairs"

_STEP_TYPE = {
    1: "làm_dịu", 2: "làm_rõ", 3: "làm_hài_lòng",
    4: "làm_dịu", 5: "làm_rõ", 6: "làm_hài_lòng",
    7: "làm_dịu", 8: "làm_rõ", 9: "làm_hài_lòng",
}


def save_feedback_batch(batch: FeedbackBatch) -> Path:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = FEEDBACK_DIR / f"{batch.session_id}.json"
    path.write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_all_feedback(harness_version: str | None = None) -> list[TraceFeedback]:
    if not FEEDBACK_DIR.exists():
        return []
    all_fb = []
    for f in sorted(FEEDBACK_DIR.glob("*.json")):
        try:
            batch = FeedbackBatch.model_validate_json(f.read_text())
            for tf in batch.feedbacks:
                if harness_version is None or tf.harness_version == harness_version:
                    all_fb.append(tf)
        except Exception:
            continue
    return all_fb


def save_preference_pairs(pairs: list[PreferencePair], harness_version: str) -> Path:
    PREFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = PREFERENCE_DIR / f"{harness_version}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for p in pairs:
            f.write(p.model_dump_json() + "\n")
    return path


def extract_disagreements(traces: list[Trace]) -> list[dict]:
    disagreements = []
    for t in traces:
        if not t.verifier or not t.verifier.mismatched_steps:
            continue
        gt_map = {s.id: s for s in t.ground_truth_steps}
        pred_map = {s.id: s for s in t.parsed_steps}
        for step_id in t.verifier.mismatched_steps:
            gt = gt_map.get(step_id)
            pred = pred_map.get(step_id)
            if gt and pred:
                disagreements.append({
                    "task_id": t.task_id, "record_id": t.record_id,
                    "source": t.source, "harness_version": t.harness_version,
                    "step_id": step_id,
                    "model_said": pred.r, "model_evidence": pred.e[:200],
                    "ground_truth": gt.r, "gt_evidence": gt.e[:200],
                    "model_thinking": (t.thinking or "")[:800],
                })
    return disagreements


def export_for_review(traces: list[Trace], output_path: Path) -> int:
    disagreements = extract_disagreements(traces)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(disagreements, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(disagreements)


def cli_review(traces: list[Trace], harness_version: str, reviewer: str = "default") -> FeedbackBatch:
    """Interactive CLI: [enter]=agree  s=override S  u=override U  a=ambiguous  w=GT wrong  n=note  q=quit"""
    disagreements = extract_disagreements(traces)
    if not disagreements:
        print("No disagreements to review!")
        return FeedbackBatch(
            session_id=f"review_{harness_version}_{datetime.now().strftime('%Y%m%d_%H%M')}",
            harness_version=harness_version, reviewer=reviewer, feedbacks=[],
        )

    print(f"\n{'='*60}")
    print(f"  HUMAN REVIEW — {len(disagreements)} disagreements")
    print(f"  Commands: [enter]=agree  s/u=override  a=ambiguous  w=GT wrong  n=note  q=quit")
    print(f"{'='*60}\n")

    by_task: dict[str, list[dict]] = {}
    for d in disagreements:
        by_task.setdefault(d["task_id"], []).append(d)

    feedbacks: list[TraceFeedback] = []
    for task_id, steps in by_task.items():
        print(f"\n--- Call: {task_id} ({steps[0]['source']}) ---")
        step_feedbacks = []
        for i, d in enumerate(steps):
            print(f"\n  Step {d['step_id']} ({_STEP_TYPE.get(d['step_id'], '?')}):")
            print(f"    Model: {d['model_said']}  |  GT: {d['ground_truth']}")
            print(f"    Model evidence: {d['model_evidence'][:120]}")
            print(f"    GT evidence:    {d['gt_evidence'][:120]}")

            override, is_ambiguous, gt_is_wrong, reasoning = None, False, False, ""
            while True:
                choice = input(f"    [{i+1}/{len(steps)}] > ").strip().lower()
                if choice == "":
                    break
                elif choice == "s":
                    override = "S"; break
                elif choice == "u":
                    override = "U"; break
                elif choice == "a":
                    is_ambiguous = True; break
                elif choice == "w":
                    gt_is_wrong = True; override = d["model_said"]
                    reasoning = input("    Why is GT wrong? > ").strip(); break
                elif choice == "n":
                    reasoning = input("    Note: > ").strip()
                elif choice == "q":
                    batch = FeedbackBatch(
                        session_id=f"review_{harness_version}_{datetime.now().strftime('%Y%m%d_%H%M')}",
                        harness_version=harness_version, reviewer=reviewer, feedbacks=feedbacks,
                    )
                    save_feedback_batch(batch)
                    return batch

            step_feedbacks.append(StepFeedback(
                step_id=d["step_id"], model_said=d["model_said"],
                ground_truth=d["ground_truth"], human_override=override,
                reasoning_note=reasoning, is_ambiguous=is_ambiguous, gt_is_wrong=gt_is_wrong,
            ))

        feedbacks.append(TraceFeedback(
            task_id=task_id, record_id=steps[0]["record_id"],
            source=steps[0]["source"], harness_version=harness_version,
            reviewer=reviewer, timestamp=datetime.now().isoformat(), steps=step_feedbacks,
        ))

    batch = FeedbackBatch(
        session_id=f"review_{harness_version}_{datetime.now().strftime('%Y%m%d_%H%M')}",
        harness_version=harness_version, reviewer=reviewer, feedbacks=feedbacks,
    )
    save_feedback_batch(batch)
    print(f"\n✓ Saved {len(feedbacks)} trace feedbacks")
    return batch


def generate_preference_pairs(
    traces: list[Trace],
    feedback: list[TraceFeedback],
    harness_version: str,
) -> list[PreferencePair]:
    """Generate DPO preference pairs from human feedback + disagreements."""
    pairs = []
    fb_map = {tf.task_id: tf for tf in feedback}

    for trace in traces:
        if not trace.verifier or not trace.verifier.mismatched_steps:
            continue
        tf = fb_map.get(trace.task_id)

        if not tf:
            chosen = json.dumps([s.model_dump() for s in trace.ground_truth_steps], ensure_ascii=False)
            rejected = json.dumps([s.model_dump() for s in trace.parsed_steps], ensure_ascii=False)
            pairs.append(PreferencePair(
                task_id=trace.task_id, record_id=trace.record_id,
                chosen=chosen, rejected=rejected,
                source="gt_vs_model", harness_version=harness_version,
            ))
            continue

        corrected_steps = []
        for gt_step in trace.ground_truth_steps:
            sf = next((s for s in tf.steps if s.step_id == gt_step.id), None)
            if sf and sf.human_override:
                corrected_steps.append({"id": gt_step.id, "r": sf.human_override, "e": gt_step.e})
            elif sf and sf.gt_is_wrong:
                corrected_steps.append({"id": gt_step.id, "r": sf.model_said, "e": gt_step.e})
            else:
                corrected_steps.append({"id": gt_step.id, "r": gt_step.r, "e": gt_step.e})

        chosen = json.dumps(corrected_steps, ensure_ascii=False)
        rejected = json.dumps([s.model_dump() for s in trace.parsed_steps], ensure_ascii=False)
        if chosen != rejected:
            source = (
                "human_corrected_gt"
                if any(s.human_override or s.gt_is_wrong for s in tf.steps)
                else "gt_vs_model"
            )
            pairs.append(PreferencePair(
                task_id=trace.task_id, record_id=trace.record_id,
                chosen=chosen, rejected=rejected,
                source=source, harness_version=harness_version,
            ))

    if pairs:
        save_preference_pairs(pairs, harness_version)
    return pairs


def build_feedback_context_for_miner(harness_version: str) -> str:
    """Human reasoning notes for the miner."""
    feedbacks = load_all_feedback(harness_version)
    if not feedbacks:
        return ""

    lines = ["HUMAN REVIEWER FEEDBACK (from QC experts):"]
    notes_by_type: dict[str, list[str]] = {}
    ambiguous_count = gt_wrong_count = 0

    for tf in feedbacks:
        for sf in tf.steps:
            step_type = _STEP_TYPE.get(sf.step_id, "unknown")
            if sf.reasoning_note:
                notes_by_type.setdefault(step_type, []).append(
                    f"step {sf.step_id} ({tf.task_id}): {sf.reasoning_note}"
                )
            if sf.is_ambiguous:
                ambiguous_count += 1
            if sf.gt_is_wrong:
                gt_wrong_count += 1

    for step_type, notes in notes_by_type.items():
        lines.append(f"\n  [{step_type}] Human notes:")
        for note in notes[:5]:
            lines.append(f"    • {note}")

    if ambiguous_count:
        lines.append(f"\n  {ambiguous_count} steps flagged as AMBIGUOUS")
    if gt_wrong_count:
        lines.append(f"  {gt_wrong_count} ground truth labels marked INCORRECT")
    lines.append("\nUse these human insights to understand ROOT CAUSE of failures.\n")
    return "\n".join(lines)


def compute_adjusted_agreement(traces: list[Trace], harness_version: str) -> float:
    """Agreement using human overrides as reference when available."""
    feedbacks = load_all_feedback(harness_version)
    fb_map: dict[str, dict[int, StepFeedback]] = {}
    for tf in feedbacks:
        fb_map[tf.task_id] = {sf.step_id: sf for sf in tf.steps}

    total = matched = 0
    for t in traces:
        if not t.verifier:
            continue
        step_fb = fb_map.get(t.task_id, {})
        gt_map = {s.id: s.r for s in t.ground_truth_steps}
        for pred_step in t.parsed_steps:
            if pred_step.id not in gt_map:
                continue
            total += 1
            sf = step_fb.get(pred_step.id)
            if sf and sf.human_override:
                reference = sf.human_override
            elif sf and sf.gt_is_wrong:
                reference = sf.model_said
            else:
                reference = gt_map[pred_step.id]
            if pred_step.r == reference:
                matched += 1
    return matched / total if total > 0 else 0.0
