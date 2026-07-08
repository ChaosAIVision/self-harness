#!/usr/bin/env python3
"""CLI tool for human review of model disagreements between pipeline rounds.

Usage:
    python scripts/review.py                    # review latest version
    python scripts/review.py --version v0.2.0   # review specific version
    python scripts/review.py --reviewer alice    # tag reviewer
    python scripts/review.py --export-only       # export only, no interactive
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import yaml
import config
from trace_schema import Trace
from feedback_collector import cli_review, export_for_review, load_all_feedback


def _current_version() -> str:
    cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
    return cfg["version"]


def _load_traces(version: str) -> list[Trace]:
    path = config.DATA_DIR / "runs" / version / "traces.json"
    if not path.exists():
        print(f"[review] No traces found for version {version} at {path}")
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Trace.model_validate(t) for t in raw]


def main():
    parser = argparse.ArgumentParser(description="Human review of model disagreements")
    parser.add_argument("--version", default=None, help="Harness version to review (default: current)")
    parser.add_argument("--reviewer", default="default", help="Reviewer name/tag")
    parser.add_argument("--export-only", action="store_true", help="Only export disagreements, skip interactive review")
    args = parser.parse_args()

    version = args.version or _current_version()
    print(f"\n{'='*60}")
    print(f"  SELF-HARNESS REVIEW — version: {version}")
    print(f"  Reviewer: {args.reviewer}")
    print(f"{'='*60}\n")

    traces = _load_traces(version)
    if not traces:
        print("No traces to review. Run a pipeline round first.")
        sys.exit(1)

    # Export disagreements to review queue
    review_path = config.DATA_DIR / "review_queue" / f"{version}.json"
    n_exported = export_for_review(traces, review_path)
    print(f"Exported {n_exported} disagreements to {review_path}")

    if n_exported == 0:
        print("No disagreements found — model agrees with ground truth on all evaluated steps.")
        sys.exit(0)

    # Show existing feedback stats
    existing_feedback = load_all_feedback(version)
    if existing_feedback:
        reviewed_count = sum(len(tf.steps) for tf in existing_feedback)
        print(f"Already reviewed: {reviewed_count} steps in {len(existing_feedback)} traces")

    if args.export_only:
        print(f"\nExport-only mode. Review queue saved to: {review_path}")
        sys.exit(0)

    # Interactive review
    batch = cli_review(traces, version, reviewer=args.reviewer)
    print(f"\n✓ Review session complete.")
    print(f"  Session ID: {batch.session_id}")
    print(f"  Traces reviewed: {len(batch.feedbacks)}")
    total_steps = sum(len(tf.steps) for tf in batch.feedbacks)
    print(f"  Steps annotated: {total_steps}")


if __name__ == "__main__":
    main()
