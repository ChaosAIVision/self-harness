"""Apply harness proposals to produce candidate system prompts."""
from __future__ import annotations
import json
import difflib
from openai import OpenAI
from trace_schema import HarnessProposal
import config


ALLOWED_SURFACES = {"system_prompt"}
MAX_DIFF_RATIO = 0.30


def _diff_ratio(old: str, new: str) -> float:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    return 1.0 - matcher.ratio()


def apply_proposal(proposal: HarnessProposal, current_prompt: str) -> str | None:
    """Apply a single proposal to the current system prompt. Returns new prompt or None on rejection."""
    if proposal.editable_surface not in ALLOWED_SURFACES:
        print(f"[patcher] REJECT: surface '{proposal.editable_surface}' not allowed")
        return None

    change = proposal.proposed_change.strip()
    if not change:
        print(f"[patcher] REJECT: empty proposed_change")
        return None

    # Use LLM to apply the patch precisely
    client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    apply_prompt = f"""You are applying a targeted edit to a system prompt.
DO NOT rewrite or restructure the prompt. Only add the specified text at the appropriate location.
Keep all existing rules intact.

CURRENT SYSTEM PROMPT:
{current_prompt}

EDIT TO APPLY:
{change}

INSERTION POINT: {proposal.insertion_point}

Output ONLY the modified system prompt text, nothing else."""

    resp = client.chat.completions.create(
        model=config.MODEL_NAME,
        messages=[{"role": "user", "content": apply_prompt}],
        temperature=0,
    )
    new_prompt = (resp.choices[0].message.content or "").strip()

    # Sanity check: new prompt must be longer than old (we only add, not remove)
    if len(new_prompt) < len(current_prompt) * 0.9:
        print(f"[patcher] REJECT: new prompt too short (likely destructive edit)")
        return None

    # Diff ratio check: reject if > 30% of lines changed (too disruptive)
    ratio = _diff_ratio(current_prompt, new_prompt)
    if ratio > MAX_DIFF_RATIO:
        print(f"[patcher] REJECT {proposal.proposal_id}: diff ratio {ratio:.0%} > {MAX_DIFF_RATIO:.0%}")
        return None

    return new_prompt


def _apply_combo(proposal_a: HarnessProposal, proposal_b: HarnessProposal, base_prompt: str) -> str | None:
    """Merge top-2 proposals into a single combined candidate."""
    client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    apply_prompt = f"""You are applying two targeted edits to a system prompt.
DO NOT rewrite or restructure the prompt. Only add the specified texts at the appropriate locations.
Keep all existing rules intact.

CURRENT SYSTEM PROMPT:
{base_prompt}

EDIT 1 TO APPLY:
{proposal_a.proposed_change.strip()}
INSERTION POINT 1: {proposal_a.insertion_point}

EDIT 2 TO APPLY:
{proposal_b.proposed_change.strip()}
INSERTION POINT 2: {proposal_b.insertion_point}

Output ONLY the modified system prompt text with BOTH edits applied, nothing else."""

    resp = client.chat.completions.create(
        model=config.MODEL_NAME,
        messages=[{"role": "user", "content": apply_prompt}],
        temperature=0,
    )
    new_prompt = (resp.choices[0].message.content or "").strip()

    if len(new_prompt) < len(base_prompt) * 0.9:
        print(f"[patcher] REJECT combo: new prompt too short")
        return None

    ratio = _diff_ratio(base_prompt, new_prompt)
    if ratio > MAX_DIFF_RATIO:
        print(f"[patcher] REJECT combo: diff ratio {ratio:.0%} > {MAX_DIFF_RATIO:.0%}")
        return None

    return new_prompt


def merge_proposals(proposals: list[HarnessProposal], base_prompt: str) -> dict[str, str]:
    """Apply each proposal independently, plus a combo of the first two. Returns map proposal_id -> candidate_prompt."""
    candidates = {}
    successful = []

    for p in proposals:
        result = apply_proposal(p, base_prompt)
        if result:
            candidates[p.proposal_id] = result
            successful.append(p)
        else:
            print(f"[patcher] Skipped proposal {p.proposal_id}")

    # Generate combo candidate from top-2 successful proposals
    if len(successful) >= 2:
        combo_id = f"combo_{successful[0].proposal_id.replace('p_', '')}_{successful[1].proposal_id.replace('p_', '')}"
        combo_result = _apply_combo(successful[0], successful[1], base_prompt)
        if combo_result:
            candidates[combo_id] = combo_result
            print(f"[patcher] Generated combo candidate: {combo_id}")

    return candidates
