"""Generate, store, and load Reflexion records (episodic memory)."""
from __future__ import annotations
import json
from pathlib import Path
from openai import OpenAI
from reflection_schema import Reflection, ProposalOutcome, FailureStatus
from trace_schema import FailureBundle, ValidationResult
import config


REFLECTIONS_DIR = config.DATA_DIR / "reflections"


# ── Generate ────────────────────────────────────────────────────────

def _build_reflect_prompt(
    from_version: str,
    bundle: FailureBundle,
    proposals_file: Path,
    validation_results: list[ValidationResult],
    prev_reflections: list[Reflection],
) -> str:
    """Build prompt asking the LLM to write a structured reflection."""

    prev_text = ""
    if prev_reflections:
        prev_text = "PREVIOUS ROUND REFLECTIONS (for continuity):\n"
        for r in prev_reflections[-3:]:  # last 3 rounds max
            prev_text += (
                f"  Round {r.round_id}:\n"
                f"    worked: {r.what_worked}\n"
                f"    failed: {r.what_failed}\n"
                f"    persists: {r.what_persists}\n"
                f"    lesson: {r.lesson}\n\n"
            )

    failures_text = json.dumps(
        [c.model_dump() for c in bundle.clusters],
        ensure_ascii=False, indent=2,
    )

    proposals_raw = json.loads(proposals_file.read_text()) if proposals_file.exists() else []
    outcomes_text = ""
    for vr in validation_results:
        p_data = next((p for p in proposals_raw if p["proposal_id"] == vr.proposal_id), {})
        outcomes_text += (
            f"  {vr.proposal_id}: "
            f"{'ACCEPTED' if vr.accepted else 'REJECTED'} "
            f"delta_in={vr.delta_in:+.1%} delta_out={vr.delta_out:+.1%}\n"
            f"    target: {p_data.get('target_failure', '?')}\n"
            f"    edit: {p_data.get('proposed_change', '?')[:150]}\n\n"
        )

    return f"""You are the reflection engine of a self-improving QC pipeline.
A round just completed. Analyze what happened and produce a reflection record.

{prev_text}
THIS ROUND ({from_version}):
Baseline agreement: held_in={bundle.overall_agreement:.1%}

FAILURE CLUSTERS FOUND:
{failures_text}

PROPOSAL OUTCOMES:
{outcomes_text}

Write a JSON reflection with these exact fields:
{{
  "what_worked": "which proposals improved scores and WHY they helped (1-2 sentences)",
  "what_failed": "which proposals were rejected and WHY they hurt or had no effect (1-2 sentences)",
  "what_persists": "failure patterns that remain despite this round's attempts — be specific about WHAT exact cases still fail and WHY the current approach doesn't fix them (2-3 sentences)",
  "lesson": "strategic insight: what TYPE of edit should next round try that is DIFFERENT from what was already tried (1-2 sentences)",
  "avoid_next": "explicit list of approaches that should NOT be retried because they already failed or are redundant with existing rules (1-2 sentences)"
}}

CRITICAL: 'what_persists' and 'lesson' are the most important fields. Be specific
about WHY previous edits didn't fully solve the pattern, and WHAT different approach
would work. Don't just say "needs more rules" — say what KIND of rule, checking WHAT
signal, that the current approach misses.

Output JSON only."""


def generate_reflection(
    from_version: str,
    to_version: str | None,
    bundle: FailureBundle,
    proposals_file: Path,
    validation_results: list[ValidationResult],
    baseline_in: float,
    baseline_out: float,
    final_in: float | None,
    final_out: float | None,
) -> Reflection:
    """Generate a reflection record after a completed round."""

    prev_reflections = load_all_reflections()

    proposals_raw = json.loads(proposals_file.read_text()) if proposals_file.exists() else []
    proposal_outcomes = []
    for vr in validation_results:
        p_data = next((p for p in proposals_raw if p["proposal_id"] == vr.proposal_id), {})
        proposal_outcomes.append(ProposalOutcome(
            proposal_id=vr.proposal_id,
            target_failure=p_data.get("target_failure", ""),
            proposed_change_summary=p_data.get("proposed_change", "")[:200],
            accepted=vr.accepted,
            delta_in=vr.delta_in,
            delta_out=vr.delta_out,
        ))

    failure_statuses = []
    prev_patterns = {}
    for pr in prev_reflections:
        for fs in pr.failure_statuses:
            prev_patterns[fs.pattern[:50]] = fs

    for cluster in bundle.clusters:
        match_key = cluster.pattern[:50]
        prev = prev_patterns.get(match_key)
        attempts = prev.attempts.copy() if prev else []
        for po in proposal_outcomes:
            if cluster.step_type in po.target_failure.lower() or cluster.pattern[:30] in po.target_failure:
                attempts.append(po.proposal_id)

        failure_statuses.append(FailureStatus(
            pattern=cluster.pattern,
            step_type=cluster.step_type,
            first_seen=prev.first_seen if prev else from_version,
            still_present=True,
            attempts=attempts,
            count_before=cluster.count,
        ))

    prompt = _build_reflect_prompt(
        from_version, bundle, proposals_file,
        validation_results, prev_reflections,
    )

    client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    resp = client.chat.completions.create(
        model=config.MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()

    try:
        llm_fields = json.loads(raw)
    except Exception:
        llm_fields = {
            "what_worked": "Parse error — could not generate reflection",
            "what_failed": "", "what_persists": "", "lesson": "", "avoid_next": "",
        }

    round_id = f"{from_version}_to_{to_version or 'no_change'}"
    reflection = Reflection(
        round_id=round_id,
        from_version=from_version,
        to_version=to_version,
        baseline_in=baseline_in,
        baseline_out=baseline_out,
        final_in=final_in,
        final_out=final_out,
        proposals_tried=proposal_outcomes,
        failure_statuses=failure_statuses,
        what_worked=llm_fields.get("what_worked", ""),
        what_failed=llm_fields.get("what_failed", ""),
        what_persists=llm_fields.get("what_persists", ""),
        lesson=llm_fields.get("lesson", ""),
        avoid_next=llm_fields.get("avoid_next", ""),
    )

    save_reflection(reflection)
    return reflection


# ── Storage ─────────────────────────────────────────────────────────

def save_reflection(reflection: Reflection) -> Path:
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = REFLECTIONS_DIR / f"{reflection.round_id}.json"
    path.write_text(reflection.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_all_reflections() -> list[Reflection]:
    if not REFLECTIONS_DIR.exists():
        return []
    files = sorted(REFLECTIONS_DIR.glob("*.json"))
    reflections = []
    for f in files:
        try:
            reflections.append(Reflection.model_validate_json(f.read_text()))
        except Exception:
            continue
    return reflections


# ── Context builders ────────────────────────────────────────────────

def build_miner_context(max_rounds: int = 5) -> str:
    """Context for failure_miner — what's already known from previous rounds."""
    reflections = load_all_reflections()
    if not reflections:
        return ""

    recent = reflections[-max_rounds:]
    lines = ["EPISODIC MEMORY — Previous round results (most recent last):"]
    lines.append("You MUST read this before analyzing failures.\n")

    for r in recent:
        final_in_str = f"{r.final_in:.1%}" if r.final_in is not None else "N/A"
        lines.append(f"--- Round {r.round_id} (agreement: {r.baseline_in:.1%} → {final_in_str} held_in) ---")
        if r.what_persists:
            lines.append(f"  STILL FAILING: {r.what_persists}")
        for po in r.proposals_tried:
            status = "✓ WORKED" if po.accepted else "✗ FAILED"
            lines.append(f"  {status}: {po.target_failure} (delta_out={po.delta_out:+.1%})")
        if r.lesson:
            lines.append(f"  LESSON: {r.lesson}")
        lines.append("")

    lines.append("INSTRUCTION: Do NOT re-report failure patterns listed above as already fixed.")
    lines.append("Focus on: 1) NEW failures  2) PERSISTENT failures (explain what's different now)  3) REGRESSIONS\n")
    return "\n".join(lines)


def build_proposer_context(max_rounds: int = 5) -> str:
    """Context for proposer — what edits were already tried."""
    reflections = load_all_reflections()
    if not reflections:
        return ""

    recent = reflections[-max_rounds:]
    lines = ["EPISODIC MEMORY — Previous proposals and their outcomes:"]
    lines.append("You MUST read this before generating new proposals.\n")

    tried_by_target: dict[str, list[str]] = {}
    for r in recent:
        for po in r.proposals_tried:
            key = po.target_failure
            entry = f"{'✓' if po.accepted else '✗'} {po.proposed_change_summary[:120]}... (delta_out={po.delta_out:+.1%})"
            tried_by_target.setdefault(key, []).append(entry)

    for target, attempts in tried_by_target.items():
        lines.append(f"Target: {target}")
        for a in attempts:
            lines.append(f"  {a}")
        lines.append("")

    all_lessons = [r.lesson for r in recent if r.lesson]
    all_avoids = [r.avoid_next for r in recent if r.avoid_next]

    if all_lessons:
        lines.append("ACCUMULATED LESSONS:")
        for lesson in all_lessons[-3:]:
            lines.append(f"  • {lesson}")
        lines.append("")

    if all_avoids:
        lines.append("DO NOT RETRY:")
        for avoid in all_avoids[-3:]:
            lines.append(f"  ✗ {avoid}")
        lines.append("")

    lines.append("INSTRUCTION: Each proposal must be MEANINGFULLY DIFFERENT from previous attempts.")
    lines.append("Vary the strategy — if text-matching failed, try structural/context-aware approach.\n")
    return "\n".join(lines)
