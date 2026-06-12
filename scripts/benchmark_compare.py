#!/usr/bin/env python3
"""Compare baseline vs self-harness model performance."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import config


def load_traces(version: str) -> list[dict]:
    trace_file = config.DATA_DIR / "runs" / version / "traces.json"
    if not trace_file.exists():
        return []
    return json.loads(trace_file.read_text())


def compute_stats(traces: list[dict]) -> dict:
    if not traces:
        return {}
    total_steps = sum(t["verifier"]["steps_total"] for t in traces if t.get("verifier"))
    matched_steps = sum(t["verifier"]["steps_matched"] for t in traces if t.get("verifier"))
    by_step = {}
    for t in traces:
        if not t.get("verifier"):
            continue
        for step_id in range(1, 10):
            gt_step = next((s for s in t["ground_truth_steps"] if s["id"] == step_id), None)
            pred_step = next((s for s in t["parsed_steps"] if s["id"] == step_id), None)
            if gt_step and pred_step:
                if step_id not in by_step:
                    by_step[step_id] = {"matched": 0, "total": 0}
                by_step[step_id]["total"] += 1
                if gt_step["r"] == pred_step["r"]:
                    by_step[step_id]["matched"] += 1

    by_source = {}
    for t in traces:
        if not t.get("verifier"):
            continue
        src = t["source"]
        if src not in by_source:
            by_source[src] = {"matched": 0, "total": 0}
        by_source[src]["total"] += t["verifier"]["steps_total"]
        by_source[src]["matched"] += t["verifier"]["steps_matched"]

    return {
        "overall_agreement": matched_steps / total_steps if total_steps else 0,
        "total_calls": len(traces),
        "total_steps": total_steps,
        "by_step": {
            k: v["matched"] / v["total"] if v["total"] else 0
            for k, v in by_step.items()
        },
        "by_source": {
            k: v["matched"] / v["total"] if v["total"] else 0
            for k, v in by_source.items()
        },
    }


def print_comparison(base_stats: dict, optimized_stats: dict, base_ver: str, opt_ver: str):
    print("\n" + "="*70)
    print(f"  BENCHMARK COMPARISON: {base_ver}  vs  {opt_ver}")
    print("="*70)

    b = base_stats.get("overall_agreement", 0)
    o = optimized_stats.get("overall_agreement", 0)
    delta = o - b
    print(f"\n{'Metric':<30} {'Base':>10} {'Optimized':>12} {'Delta':>10}")
    print("-"*65)
    print(f"{'Overall Agreement':<30} {b:>9.1%} {o:>11.1%} {delta:>+9.1%}")

    print(f"\n{'Step Agreement by Step ID':<30} {'Base':>10} {'Optimized':>12} {'Delta':>10}")
    print("-"*65)
    step_names = {
        1: "Step 1 (Làm dịu R1)", 2: "Step 2 (Làm rõ R1)", 3: "Step 3 (Làm hài lòng R1)",
        4: "Step 4 (Làm dịu R2)", 5: "Step 5 (Làm rõ R2)", 6: "Step 6 (Làm hài lòng R2)",
        7: "Step 7 (Làm dịu R3)", 8: "Step 8 (Làm rõ R3)", 9: "Step 9 (Làm hài lòng R3)",
    }
    b_steps = base_stats.get("by_step", {})
    o_steps = optimized_stats.get("by_step", {})
    for sid in range(1, 10):
        bv = b_steps.get(sid, 0)
        ov = o_steps.get(sid, 0)
        d = ov - bv
        flag = " ↑" if d > 0.02 else (" ↓" if d < -0.02 else "")
        print(f"  {step_names[sid]:<28} {bv:>9.1%} {ov:>11.1%} {d:>+9.1%}{flag}")

    print(f"\n{'Agreement by Source':<30} {'Base':>10} {'Optimized':>12} {'Delta':>10}")
    print("-"*65)
    all_sources = set(list(base_stats.get("by_source", {}).keys()) + list(optimized_stats.get("by_source", {}).keys()))
    for src in sorted(all_sources):
        bv = base_stats.get("by_source", {}).get(src, 0)
        ov = optimized_stats.get("by_source", {}).get(src, 0)
        d = ov - bv
        print(f"  {src:<28} {bv:>9.1%} {ov:>11.1%} {d:>+9.1%}")

    print("\n" + "="*70)
    if delta > 0:
        print(f"  Self-Harness IMPROVED agreement by {delta:+.1%}")
    elif delta == 0:
        print(f"  Self-Harness: No change in agreement")
    else:
        print(f"  Self-Harness DECREASED agreement by {delta:+.1%} (regression!)")
    print("="*70 + "\n")


def main():
    # Find all available run versions
    runs_dir = config.DATA_DIR / "runs"
    versions = sorted([d.name for d in runs_dir.iterdir() if d.is_dir() and (d / "traces.json").exists()])

    if not versions:
        print("No trace files found. Run run_baseline.py and run_round.py first.")
        sys.exit(1)

    print(f"Available versions: {versions}")

    base_ver = "v0.1.0"
    base_traces = load_traces(base_ver)

    if not base_traces:
        print(f"No traces found for {base_ver}. Run run_baseline.py first.")
        sys.exit(1)

    base_stats = compute_stats(base_traces)

    # Find the latest optimized version
    opt_versions = [v for v in versions if v != base_ver]
    if not opt_versions:
        print(f"\nOnly baseline found. Showing baseline stats:\n")
        print(f"Overall Agreement: {base_stats['overall_agreement']:.1%}")
        print(f"By Step: {base_stats['by_step']}")
        sys.exit(0)

    opt_ver = opt_versions[-1]  # latest
    opt_traces = load_traces(opt_ver)
    opt_stats = compute_stats(opt_traces)

    print_comparison(base_stats, opt_stats, base_ver, opt_ver)

    # Save comparison report
    report = {
        "base_version": base_ver,
        "optimized_version": opt_ver,
        "base_stats": base_stats,
        "optimized_stats": opt_stats,
        "overall_delta": opt_stats.get("overall_agreement", 0) - base_stats.get("overall_agreement", 0),
    }
    report_file = config.DATA_DIR / "reports" / "comparison.json"
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report saved → {report_file}")


if __name__ == "__main__":
    main()
