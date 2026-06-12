"""Apply harness proposals to produce candidate system prompts."""
from __future__ import annotations
import json
from openai import OpenAI
from trace_schema import HarnessProposal
import config


ALLOWED_SURFACES = {"system_prompt"}


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

INSERTION CONTEXT: {proposal.insertion_point if hasattr(proposal, 'insertion_point') else 'at the end of STEP RULES section'}

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

    return new_prompt


def merge_proposals(proposals: list[HarnessProposal], base_prompt: str) -> dict[str, str]:
    """Apply each proposal independently, return map proposal_id -> candidate_prompt."""
    candidates = {}
    for p in proposals:
        result = apply_proposal(p, base_prompt)
        if result:
            candidates[p.proposal_id] = result
        else:
            print(f"[patcher] Skipped proposal {p.proposal_id}")
    return candidates
