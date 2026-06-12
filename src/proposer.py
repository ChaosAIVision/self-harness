"""Propose minimal harness edits based on failure clusters."""
from __future__ import annotations
import json
from openai import OpenAI
from trace_schema import FailureBundle, HarnessProposal
import config


def _build_proposal_prompt(bundle: FailureBundle, current_system_prompt: str) -> str:
    clusters_text = json.dumps(
        [c.model_dump() for c in bundle.clusters],
        ensure_ascii=False, indent=2
    )
    return f"""You are a harness optimizer for a QC AI scoring telesales calls.
The current system prompt is shown below. The AI scores 9 steps (3 rounds × 3 steps each).
Current agreement rate: {bundle.overall_agreement:.1%}

CURRENT SYSTEM PROMPT (first 1500 chars):
{current_system_prompt[:1500]}

FAILURE CLUSTERS:
{clusters_text}

Generate 2-3 targeted proposals to improve the system prompt.
Each proposal must:
1. Address exactly one failure cluster
2. Be a MINIMAL change (add/clarify one rule, not rewrite everything)
3. NOT change benchmark data, verifier logic, or scoring structure
4. Be a concrete text addition or replacement to the system prompt

Output JSON only:
{{
  "proposals": [
    {{
      "proposal_id": "p_001",
      "target_failure": "brief name of the failure cluster",
      "editable_surface": "system_prompt",
      "proposed_change": "EXACT text to add/modify in the system prompt",
      "insertion_point": "after which section to insert (e.g. 'after STEP RULES section')",
      "expected_gain": "what specific improvement is expected",
      "risk": "potential regression risk"
    }}
  ]
}}"""


def generate_proposals(bundle: FailureBundle, current_system_prompt: str) -> list[HarnessProposal]:
    if not bundle.clusters:
        return []

    client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    prompt = _build_proposal_prompt(bundle, current_system_prompt)

    resp = client.chat.completions.create(
        model=config.MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = resp.choices[0].message.content or ""
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()

    proposals = []
    try:
        data = json.loads(raw)
        for p in data.get("proposals", []):
            proposals.append(HarnessProposal(
                proposal_id=p.get("proposal_id", "p_000"),
                target_failure=p.get("target_failure", ""),
                editable_surface=p.get("editable_surface", "system_prompt"),
                proposed_change=p.get("proposed_change", ""),
                expected_gain=p.get("expected_gain", ""),
                risk=p.get("risk", ""),
            ))
    except Exception as e:
        print(f"[proposer] parse error: {e}\nRaw: {raw[:200]}")

    return proposals
