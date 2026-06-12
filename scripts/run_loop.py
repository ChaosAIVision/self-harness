#!/usr/bin/env python3
"""Run Self-Harness optimization loop until convergence."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import yaml
import config
from orchestrator import run_round

MAX_ROUNDS = 10
MIN_IMPROVEMENT = 0.005   # stop if delta_out < 0.5% for 2 consecutive rounds
DRY_STREAK_LIMIT = 2


def main():
    dry_streak = 0
    history = []

    for round_num in range(1, MAX_ROUNDS + 1):
        # Load current harness
        current_cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
        current_version = current_cfg["version"]
        prompt_file = current_cfg.get("system_prompt_file", "prompts/system.md")
        current_prompt = Path(prompt_file).read_text()

        print(f"\n{'#'*60}")
        print(f"  LOOP ROUND {round_num} / {MAX_ROUNDS}  —  harness: {current_version}")
        print(f"{'#'*60}")

        result = run_round(current_prompt, current_version)
        history.append(result)

        if not result.get("promoted"):
            dry_streak += 1
            print(f"\n  No improvement (dry streak: {dry_streak}/{DRY_STREAK_LIMIT})")
            if dry_streak >= DRY_STREAK_LIMIT:
                print(f"\n✓ Converged after {round_num} rounds — stopping loop.")
                break
        else:
            dry_streak = 0
            delta_out = result["final_out"] - result["baseline_out"]
            if delta_out < MIN_IMPROVEMENT:
                dry_streak += 1
                print(f"\n  Marginal improvement ({delta_out:+.1%}) — counting as dry")

    # Final summary
    print(f"\n{'='*60}")
    print("LOOP COMPLETE — HISTORY")
    print(f"{'='*60}")
    for i, r in enumerate(history, 1):
        if r.get("promoted"):
            print(f"  Round {i}: {r.get('new_version','?'):8s}  "
                  f"held_in {r['baseline_in']:.1%}→{r['final_in']:.1%}  "
                  f"held_out {r['baseline_out']:.1%}→{r['final_out']:.1%}")
        else:
            print(f"  Round {i}: no change  (baseline {r['baseline_in']:.1%} / {r['baseline_out']:.1%})")


if __name__ == "__main__":
    main()
