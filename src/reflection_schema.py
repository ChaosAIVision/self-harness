"""Schema for Reflexion / Episodic Memory records."""
from __future__ import annotations
from pydantic import BaseModel


class ProposalOutcome(BaseModel):
    """What happened when a specific proposal was tried."""
    proposal_id: str
    target_failure: str
    proposed_change_summary: str  # short summary, not full text
    accepted: bool
    delta_in: float
    delta_out: float


class FailureStatus(BaseModel):
    """Tracks a failure pattern across rounds."""
    pattern: str
    step_type: str
    first_seen: str          # harness version where first detected
    still_present: bool      # still failing after this round?
    attempts: list[str]      # proposal_ids that tried to fix this
    count_before: int        # count in miner before this round
    count_after: int | None = None  # count in miner after (None if fixed)


class Reflection(BaseModel):
    """One round's episodic memory record."""
    round_id: str            # e.g. "v0.1.0_to_v0.2.0"
    from_version: str
    to_version: str | None   # None if no promotion happened
    baseline_in: float
    baseline_out: float
    final_in: float | None = None
    final_out: float | None = None

    proposals_tried: list[ProposalOutcome]
    failure_statuses: list[FailureStatus]

    # LLM-generated natural language summary
    what_worked: str         # which edits helped and why
    what_failed: str         # which edits hurt or had no effect and why
    what_persists: str       # failures that remain despite attempts
    lesson: str              # strategic insight for next round
    avoid_next: str          # explicit "do NOT try this again" guidance
