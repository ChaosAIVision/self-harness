"""Propose minimal harness edits based on failure clusters."""
from __future__ import annotations
import json
import re
from openai import OpenAI
from trace_schema import FailureBundle, HarnessProposal
from reflector import build_proposer_context
import config


def _build_proposal_prompt(bundle: FailureBundle, current_system_prompt: str) -> str:
    clusters_text = json.dumps(
        [c.model_dump() for c in bundle.clusters],
        ensure_ascii=False, indent=2
    )
    memory_context = build_proposer_context()
    return f"""You are a harness optimizer for a QC AI scoring telesales calls.
The current system prompt is shown below. The AI scores 9 steps (3 rounds × 3 steps each).
Current agreement rate: {bundle.overall_agreement:.1%}

{memory_context}

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
5. Be MEANINGFULLY DIFFERENT from any previous attempts listed in the episodic memory above

IMPORTANT: Output ONLY valid JSON, no explanation text before or after.
{{\n  "proposals": [\n    {{\n      "proposal_id": "p_001",\n      "target_failure": "brief name of the failure cluster",\n      "editable_surface": "system_prompt",\n      "proposed_change": "EXACT text to add/modify in the system prompt",\n      "insertion_point": "after which section to insert (e.g. 'after STEP RULES section')",\n      "expected_gain": "what specific improvement is expected",\n      "risk": "potential regression risk"\n    }}\n  ]\n}}"""


def _extract_json(raw: str) -> str:
    """Strip markdown fences and extract the first JSON object from raw text."""
    # Remove ```json ... ``` or ``` ... ``` fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())
    raw = raw.strip()

    # If model prepended explanation text, find the first { ... } block
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        return match.group(0)
    return raw


def _safe_parse(raw: str) -> dict:
    """Try json.loads, then fall back to sanitizing control characters."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Replace literal newlines/tabs inside JSON string values with escaped versions.
    # Strategy: parse char-by-char, replace bare control chars inside strings.
    sanitized = []
    in_string = False
    escape_next = False
    for ch in raw:
        if escape_next:
            sanitized.append(ch)
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            sanitized.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            sanitized.append(ch)
            continue
        if in_string and ch == "\n":
            sanitized.append("\\n")
        elif in_string and ch == "\r":
            sanitized.append("\\r")
        elif in_string and ch == "\t":
            sanitized.append("\\t")
        else:
            sanitized.append(ch)

    return json.loads("".join(sanitized))


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
    raw = _extract_json(raw)

    proposals = []
    try:
        data = _safe_parse(raw)
        for p in data.get("proposals", []):
            proposals.append(HarnessProposal(
                proposal_id=p.get("proposal_id", "p_000"),
                target_failure=p.get("target_failure", ""),
                editable_surface=p.get("editable_surface", "system_prompt"),
                proposed_change=p.get("proposed_change", ""),
                insertion_point=p.get("insertion_point", "end of STEP RULES section"),
                expected_gain=p.get("expected_gain", ""),
                risk=p.get("risk", ""),
            ))
    except Exception as e:
        print(f"[proposer] parse error: {e}\nRaw: {raw[:200]}")

    return proposals
