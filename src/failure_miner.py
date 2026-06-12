"""Mine failure patterns from a set of traces."""
from __future__ import annotations
import json
import collections
from openai import OpenAI
from trace_schema import Trace, FailureSignature, FailureBundle
import config


_STEP_TYPE = {
    1: "làm_dịu", 2: "làm_rõ", 3: "làm_hài_lòng",
    4: "làm_dịu", 5: "làm_rõ", 6: "làm_hài_lòng",
    7: "làm_dịu", 8: "làm_rõ", 9: "làm_hài_lòng",
}


def _build_mining_prompt(traces: list[Trace]) -> str:
    failures_text = []
    for t in traces:
        if not t.verifier:
            continue
        for step_id in t.verifier.mismatched_steps:
            gt = next((s for s in t.ground_truth_steps if s.id == step_id), None)
            pred = next((s for s in t.parsed_steps if s.id == step_id), None)
            if gt and pred:
                failures_text.append(
                    f"call={t.task_id} source={t.source} step={step_id} "
                    f"type={_STEP_TYPE.get(step_id,'?')} "
                    f"model_said={pred.r} truth={gt.r} "
                    f"truth_evidence={gt.e[:120]}"
                )

    if not failures_text:
        return ""

    lines = "\n".join(failures_text[:80])
    return f"""You are analyzing failures of a QC AI that scores telesales calls.
Below are mismatches where the model's S/U judgment differed from ground truth.

FAILURES:
{lines}

Task: Identify 3-5 distinct failure patterns. For each cluster output JSON:
{{
  "clusters": [
    {{
      "step_type": "làm_dịu|làm_rõ|làm_hài_lòng",
      "frequent_step_ids": [1,4,7],
      "pattern": "short description of what the model gets wrong",
      "mechanism": "operational root cause: what harness rule/instruction is missing or unclear",
      "representative_cases": ["call=... step=..."],
      "count": N
    }}
  ]
}}
Output JSON only. No extra text."""


def mine_failures(traces: list[Trace], harness_version: str) -> FailureBundle:
    total_steps = sum(t.verifier.steps_total for t in traces if t.verifier)
    total_matched = sum(t.verifier.steps_matched for t in traces if t.verifier)
    overall = total_matched / total_steps if total_steps > 0 else 0.0

    prompt = _build_mining_prompt(traces)
    clusters: list[FailureSignature] = []

    if prompt:
        client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
        resp = client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        try:
            data = json.loads(raw)
            for i, c in enumerate(data.get("clusters", [])):
                sig = FailureSignature(
                    step_id=c.get("frequent_step_ids", [0])[0],
                    step_type=c.get("step_type", "unknown"),
                    source_types=[],
                    count=c.get("count", 0),
                    pattern=c.get("pattern", ""),
                    mechanism=c.get("mechanism", ""),
                    representative_cases=c.get("representative_cases", [])[:3],
                )
                clusters.append(sig)
        except Exception:
            # Fallback: rule-based clustering
            step_counts = collections.Counter()
            for t in traces:
                if t.verifier:
                    for sid in t.verifier.mismatched_steps:
                        step_counts[sid] += 1
            for sid, cnt in step_counts.most_common(3):
                clusters.append(FailureSignature(
                    step_id=sid,
                    step_type=_STEP_TYPE.get(sid, "unknown"),
                    source_types=[],
                    count=cnt,
                    pattern=f"Model misclassifies step {sid} ({_STEP_TYPE.get(sid,'?')})",
                    mechanism=f"Harness rule for step {sid} needs clarification",
                    representative_cases=[],
                ))

    return FailureBundle(
        harness_version=harness_version,
        total_calls=len(traces),
        total_steps=total_steps,
        overall_agreement=overall,
        clusters=clusters,
    )
