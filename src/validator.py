"""Validate candidate harness against held_in and held_out splits."""
from __future__ import annotations
from trace_schema import Trace, ValidationResult
from runner import run_task


def _compute_agreement(traces: list[Trace]) -> float:
    total = sum(t.verifier.steps_total for t in traces if t.verifier)
    matched = sum(t.verifier.steps_matched for t in traces if t.verifier)
    return matched / total if total > 0 else 0.0


def validate_proposal(
    proposal_id: str,
    candidate_prompt: str,
    harness_version: str,
    held_in: list[dict],
    held_out: list[dict],
    baseline_in: float,
    baseline_out: float,
) -> ValidationResult:
    print(f"  [validator] Running candidate {proposal_id} on {len(held_in)} held_in tasks...")
    traces_in = [run_task(r, candidate_prompt, harness_version) for r in held_in]
    agreement_in = _compute_agreement(traces_in)

    print(f"  [validator] Running candidate {proposal_id} on {len(held_out)} held_out tasks...")
    traces_out = [run_task(r, candidate_prompt, harness_version) for r in held_out]
    agreement_out = _compute_agreement(traces_out)

    delta_in = agreement_in - baseline_in
    delta_out = agreement_out - baseline_out
    accepted = delta_in >= 0 and delta_out >= 0 and max(delta_in, delta_out) > 0

    return ValidationResult(
        proposal_id=proposal_id,
        harness_version=harness_version,
        delta_in=delta_in,
        delta_out=delta_out,
        agreement_in=agreement_in,
        agreement_out=agreement_out,
        baseline_in=baseline_in,
        baseline_out=baseline_out,
        accepted=accepted,
    )
