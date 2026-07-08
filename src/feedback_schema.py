"""Schema for Human-in-the-Loop feedback."""
from __future__ import annotations
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class StepFeedback(BaseModel):
    step_id: int
    model_said: str                     # "S" or "U"
    ground_truth: str                   # "S" or "U"
    human_override: Optional[str] = None  # "S"/"U" or None = agree with GT
    reasoning_note: str = ""            # WHY — injected into miner context
    is_ambiguous: bool = False
    gt_is_wrong: bool = False


class TraceFeedback(BaseModel):
    task_id: str
    record_id: str
    source: str
    harness_version: str
    reviewer: str = "default"
    timestamp: str = ""
    steps: list[StepFeedback]
    overall_difficulty: int = 3         # 1-5
    general_note: str = ""


class FeedbackBatch(BaseModel):
    session_id: str
    harness_version: str
    reviewer: str
    feedbacks: list[TraceFeedback]
    created_at: str = ""

    def model_post_init(self, __context):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class GroundTruthPatch(BaseModel):
    task_id: str
    record_id: str
    step_id: int
    old_gt: str
    new_gt: str
    reasoning: str
    reviewer: str
    timestamp: str = ""


class PreferencePair(BaseModel):
    """For future DPO training. chosen = correct output, rejected = wrong output."""
    task_id: str
    record_id: str
    chosen: str       # full output JSON
    rejected: str     # full output JSON
    source: str       # "human_vs_model" | "gt_vs_model" | "human_corrected_gt"
    harness_version: str
