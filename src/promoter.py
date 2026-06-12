"""Promote accepted harness proposals and manage versioning."""
from __future__ import annotations
import json
from pathlib import Path
from trace_schema import ValidationResult
import config


def promote(
    results: list[ValidationResult],
    candidates: dict[str, str],
    current_version: str,
) -> tuple[str, str] | None:
    """Pick best accepted candidate, save it, return (new_version, new_prompt) or None."""
    accepted = [r for r in results if r.accepted]
    if not accepted:
        print("[promoter] No candidates accepted.")
        return None

    # Pick the one with best combined improvement (held_out weighted higher)
    best = max(accepted, key=lambda r: r.delta_out * 2 + r.delta_in)
    print(f"[promoter] Promoting proposal {best.proposal_id}: "
          f"delta_in={best.delta_in:+.1%} delta_out={best.delta_out:+.1%}")

    new_prompt = candidates[best.proposal_id]
    # Version bump
    parts = current_version.lstrip("v").split(".")
    minor = int(parts[1]) + 1 if len(parts) > 1 else 1
    new_version = f"v{parts[0]}.{minor}.0"

    # Save to versions directory
    versions_dir = config.HARNESS_DIR / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_file = versions_dir / f"{new_version}.md"
    version_file.write_text(new_prompt, encoding="utf-8")

    # Save as current
    current_file = config.HARNESS_DIR / "current.yaml"
    current_data = {
        "version": new_version,
        "based_on": current_version,
        "promoted_proposal": best.proposal_id,
        "delta_in": best.delta_in,
        "delta_out": best.delta_out,
        "system_prompt_file": str(version_file),
    }
    import yaml
    current_file.write_text(yaml.dump(current_data, allow_unicode=True), encoding="utf-8")

    # Save validation results
    report_file = config.DATA_DIR / "reports" / f"validation_{new_version}.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps([r.model_dump() for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"[promoter] Saved harness {new_version} → {version_file}")
    return new_version, new_prompt
