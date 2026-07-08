"""Orchestrate one full Self-Harness optimization round."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from runner import run_task
from failure_miner import mine_failures
from proposer import generate_proposals
from patcher import merge_proposals
from validator import validate_proposal, _compute_agreement
from promoter import promote
from reflector import generate_reflection
from trend_tracker import compute_version_snapshot, save_snapshot
from feedback_collector import export_for_review, generate_preference_pairs, load_all_feedback


def _load_data() -> list[dict]:
    records = []
    with open(config.BENCHMARK_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_split() -> tuple[list[int], list[int]]:
    import yaml
    held_in_ids = yaml.safe_load(config.HELD_IN_IDS_FILE.read_text())["ids"]
    held_out_ids = yaml.safe_load(config.HELD_OUT_IDS_FILE.read_text())["ids"]
    return held_in_ids, held_out_ids


def run_round(current_prompt: str, current_version: str) -> dict:
    print(f"\n{'='*60}")
    print(f"SELF-HARNESS ROUND — harness version: {current_version}")
    print(f"{'='*60}")

    all_records = _load_data()
    held_in_ids, held_out_ids = _load_split()

    held_in = [r for i, r in enumerate(all_records) if i in held_in_ids]
    held_out = [r for i, r in enumerate(all_records) if i in held_out_ids]

    # Step 1: Run baseline evaluation on held_in
    print(f"\n[1] Running baseline on {len(held_in)} held_in records...")
    baseline_traces_in = [run_task(r, current_prompt, current_version) for r in held_in]
    baseline_in = _compute_agreement(baseline_traces_in)

    print(f"[1] Running baseline on {len(held_out)} held_out records...")
    baseline_traces_out = [run_task(r, current_prompt, current_version) for r in held_out]
    baseline_out = _compute_agreement(baseline_traces_out)

    print(f"    Baseline held_in: {baseline_in:.1%}  held_out: {baseline_out:.1%}")

    # Save traces
    run_dir = config.DATA_DIR / "runs" / current_version
    run_dir.mkdir(parents=True, exist_ok=True)
    all_traces = baseline_traces_in + baseline_traces_out
    traces_file = run_dir / "traces.json"
    traces_file.write_text(
        json.dumps([t.model_dump() for t in all_traces], indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Step 2: Mine failures
    print(f"\n[2] Mining failures from held_in traces...")
    bundle = mine_failures(baseline_traces_in, current_version)
    print(f"    Found {len(bundle.clusters)} failure clusters")
    for c in bundle.clusters:
        print(f"    - [{c.step_type}] {c.pattern} (count={c.count})")

    bundle_file = config.DATA_DIR / "mined_failures" / f"{current_version}.json"
    bundle_file.parent.mkdir(parents=True, exist_ok=True)
    bundle_file.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    # Step 3: Generate proposals
    print(f"\n[3] Generating harness proposals...")
    proposals = generate_proposals(bundle, current_prompt)
    print(f"    Generated {len(proposals)} proposals")
    for p in proposals:
        print(f"    - {p.proposal_id}: {p.target_failure}")

    proposals_file = config.DATA_DIR / "proposals" / f"{current_version}.json"
    proposals_file.parent.mkdir(parents=True, exist_ok=True)
    proposals_file.write_text(
        json.dumps([p.model_dump() for p in proposals], indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    if not proposals:
        print("    No proposals generated. Round complete with no changes.")
        # Still generate reflection and snapshot even with no proposals
        print(f"\n[7] Generating reflection...")
        reflection = generate_reflection(
            from_version=current_version,
            to_version=None,
            bundle=bundle,
            proposals_file=proposals_file,
            validation_results=[],
            baseline_in=baseline_in,
            baseline_out=baseline_out,
            final_in=None,
            final_out=None,
        )
        print(f"\n[8] Saving trend snapshot and exporting review queue...")
        snapshot = compute_version_snapshot(all_traces, current_version)
        save_snapshot(snapshot)
        review_path = config.DATA_DIR / "review_queue" / f"{current_version}.json"
        n_exported = export_for_review(all_traces, review_path)
        print(f"    {n_exported} disagreements exported for review.")
        return {"promoted": False, "baseline_in": baseline_in, "baseline_out": baseline_out}

    # Step 4: Apply patches
    print(f"\n[4] Applying proposal patches...")
    candidates = merge_proposals(proposals, current_prompt)
    print(f"    {len(candidates)} candidate prompts ready")

    # Step 5: Validate each candidate
    print(f"\n[5] Validating candidates...")
    next_ver = current_version + "_cand"
    results = []
    for pid, cprompt in candidates.items():
        result = validate_proposal(
            pid, cprompt, next_ver,
            held_in, held_out,
            baseline_in, baseline_out,
        )
        results.append(result)
        status = "ACCEPT" if result.accepted else "REJECT"
        print(f"    {pid}: {status} delta_in={result.delta_in:+.1%} delta_out={result.delta_out:+.1%}")

    # Step 6: Promote
    print(f"\n[6] Promoting best accepted candidate...")
    promo = promote(results, candidates, current_version)

    if promo:
        new_version, new_prompt = promo
        print(f"\n✓ Round complete. New harness: {new_version}")
        final_in = max(r.agreement_in for r in results if r.accepted)
        final_out = max(r.agreement_out for r in results if r.accepted)

        # Step 7: Generate reflection
        print(f"\n[7] Generating reflection...")
        reflection = generate_reflection(
            from_version=current_version,
            to_version=new_version,
            bundle=bundle,
            proposals_file=proposals_file,
            validation_results=results,
            baseline_in=baseline_in,
            baseline_out=baseline_out,
            final_in=final_in,
            final_out=final_out,
        )
        print(f"    Lesson: {reflection.lesson[:100]}...")

        # Step 8: Save trend snapshot + export disagreements for review
        print(f"\n[8] Saving trend snapshot and exporting review queue...")
        snapshot = compute_version_snapshot(all_traces, current_version)
        save_snapshot(snapshot)
        review_path = config.DATA_DIR / "review_queue" / f"{current_version}.json"
        n_exported = export_for_review(all_traces, review_path)
        print(f"    {n_exported} disagreements exported for review.")
        # Generate preference pairs from any existing feedback
        existing_feedback = load_all_feedback(current_version)
        if existing_feedback:
            pairs = generate_preference_pairs(all_traces, existing_feedback, current_version)
            print(f"    Generated {len(pairs)} DPO preference pairs.")

        return {
            "promoted": True,
            "new_version": new_version,
            "new_prompt": new_prompt,
            "baseline_in": baseline_in,
            "baseline_out": baseline_out,
            "final_in": final_in,
            "final_out": final_out,
        }
    else:
        print(f"\n✗ No improvement found. Current harness unchanged.")

        # Step 7: Generate reflection even when not promoted
        print(f"\n[7] Generating reflection...")
        reflection = generate_reflection(
            from_version=current_version,
            to_version=None,
            bundle=bundle,
            proposals_file=proposals_file,
            validation_results=results,
            baseline_in=baseline_in,
            baseline_out=baseline_out,
            final_in=None,
            final_out=None,
        )
        print(f"    Lesson: {reflection.lesson[:100]}...")

        # Step 8: Save trend snapshot + export disagreements for review
        print(f"\n[8] Saving trend snapshot and exporting review queue...")
        snapshot = compute_version_snapshot(all_traces, current_version)
        save_snapshot(snapshot)
        review_path = config.DATA_DIR / "review_queue" / f"{current_version}.json"
        n_exported = export_for_review(all_traces, review_path)
        print(f"    {n_exported} disagreements exported for review.")
        existing_feedback = load_all_feedback(current_version)
        if existing_feedback:
            pairs = generate_preference_pairs(all_traces, existing_feedback, current_version)
            print(f"    Generated {len(pairs)} DPO preference pairs.")

        return {"promoted": False, "baseline_in": baseline_in, "baseline_out": baseline_out}
