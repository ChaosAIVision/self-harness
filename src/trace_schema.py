from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class StepResult(BaseModel):
    id: int
    r: str  # "S" or "U"
    e: str
    g: Optional[str] = None


class TraceVerifier(BaseModel):
    passed: bool
    steps_matched: int
    steps_total: int
    agreement_rate: float
    mismatched_steps: list[int]


class TraceStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    wall_time_ms: int = 0


class Trace(BaseModel):
    task_id: str
    record_id: str
    source: str
    model: str
    harness_version: str
    status: str  # "ok" | "failed" | "parse_error"
    system_prompt_hash: str
    raw_output: str
    thinking: Optional[str] = None  # model's reasoning trace (kept as a scoring resource)
    parsed_steps: list[StepResult]
    ground_truth_steps: list[StepResult]
    verifier: Optional[TraceVerifier] = None
    stats: TraceStats = TraceStats()
    error: Optional[str] = None


class FailureSignature(BaseModel):
    step_id: int
    step_type: str  # "làm_dịu" | "làm_rõ" | "làm_hài_lòng"
    source_types: list[str]
    count: int
    pattern: str
    mechanism: str
    representative_cases: list[str]


class FailureBundle(BaseModel):
    harness_version: str
    total_calls: int
    total_steps: int
    overall_agreement: float
    clusters: list[FailureSignature]


class HarnessProposal(BaseModel):
    proposal_id: str
    target_failure: str
    editable_surface: str  # "system_prompt" | "policy_field"
    proposed_change: str
    insertion_point: str = "end of STEP RULES section"
    expected_gain: str
    risk: str


class ValidationResult(BaseModel):
    proposal_id: str
    harness_version: str
    delta_in: float
    delta_out: float
    agreement_in: float
    agreement_out: float
    baseline_in: float
    baseline_out: float
    accepted: bool
