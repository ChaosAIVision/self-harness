#!/usr/bin/env python3
"""Run evaluation with the promoted harness on all 21 records."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import config
from runner import run_task


def _compute_agreement(traces):
    total = sum(t.verifier.steps_total for t in traces if t.verifier)
    matched = sum(t.verifier.steps_matched for t in traces if t.verifier)
    return matched / total if total > 0 else 0.0


def main():
    import yaml
    current_cfg = yaml.safe_load((config.HARNESS_DIR / "current.yaml").read_text())
    version = current_cfg["version"]
    prompt_file = current_cfg["system_prompt_file"]
    system_prompt = Path(prompt_file).read_text()

    records = []
    with open(config.BENCHMARK_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Running optimized harness {version} on {len(records)} records")
    print(f"Model: {config.MODEL_NAME}\n")

    traces = []
    for i, rec in enumerate(records):
        print(f"  [{i+1}/{len(records)}] {rec['callID']} (source={rec['source']})...", end="", flush=True)
        t = run_task(rec, system_prompt, version)
        traces.append(t)
        if t.verifier:
            print(f" agreement={t.verifier.agreement_rate:.0%} ({t.verifier.steps_matched}/{t.verifier.steps_total})")
        else:
            print(f" status={t.status}")

    out_dir = config.DATA_DIR / "runs" / version
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "traces.json"
    out_file.write_text(
        json.dumps([t.model_dump() for t in traces], indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    overall = _compute_agreement(traces)
    print(f"\nOptimized overall agreement: {overall:.1%}")
    print(f"Saved traces → {out_file}")


if __name__ == "__main__":
    main()
