#!/usr/bin/env python3
"""Run one complete Self-Harness optimization round."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import config
from orchestrator import run_round


def main():
    # Load current harness
    import yaml
    current_cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
    current_version = current_cfg.get("version", "v0.1.0")
    prompt_file = current_cfg.get("system_prompt_file", "prompts/system.md")
    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = config.ROOT / prompt_path
    if not prompt_path.exists():
        prompt_path = config.HARNESS_DIR / "versions" / f"{current_version}.md"
    current_prompt = prompt_path.read_text(encoding="utf-8")

    result = run_round(current_prompt, current_version)

    print("\n" + "="*60)
    print("ROUND SUMMARY")
    print("="*60)
    print(f"Baseline held_in:  {result['baseline_in']:.1%}")
    print(f"Baseline held_out: {result['baseline_out']:.1%}")
    if result.get("promoted"):
        print(f"New version:       {result['new_version']}")
        print(f"Final held_in:     {result['final_in']:.1%}  (Δ{result['final_in']-result['baseline_in']:+.1%})")
        print(f"Final held_out:    {result['final_out']:.1%}  (Δ{result['final_out']-result['baseline_out']:+.1%})")
    else:
        print("No improvement found — harness unchanged.")

    # Save summary
    summary_file = config.DATA_DIR / "reports" / f"round_{current_version}.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nSummary saved → {summary_file}")


if __name__ == "__main__":
    main()
