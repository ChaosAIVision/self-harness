"""Run model on a task record and produce a Trace."""
from __future__ import annotations
import json
import time
import hashlib
from openai import OpenAI
from trace_schema import Trace, TraceVerifier, TraceStats, StepResult
import config


def _client() -> OpenAI:
    return OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)


def _normalize_output(raw: str) -> list[dict]:
    """Normalize model output to flat list of 9 step dicts."""
    raw = raw.strip()
    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find JSON array or object in text
        import re
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except Exception:
                return []
        else:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group())
                except Exception:
                    return []
            else:
                return []

    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        # Round-structured: {"round1": {"step1": {...}, ...}, "round2": {"step4": {...}, ...}, ...}
        steps = []
        for rk in ["round1", "round2", "round3"]:
            if rk in parsed:
                rd = parsed[rk]
                if isinstance(rd, dict):
                    # Steps may be named step1-step9 regardless of round key
                    for sv in sorted(rd.values(), key=lambda x: x.get("id", 0) if isinstance(x, dict) else 0):
                        if isinstance(sv, dict) and "id" in sv:
                            steps.append(sv)
        return steps
    return []


def _normalize_ground_truth(raw_output: str) -> list[StepResult]:
    """Parse ground truth output into StepResult list."""
    steps_raw = _normalize_output(raw_output)
    results = []
    for s in steps_raw:
        if isinstance(s, dict) and "id" in s and "r" in s:
            results.append(StepResult(
                id=s["id"],
                r=s.get("r", "U"),
                e=s.get("e", ""),
                g=s.get("g"),
            ))
    return sorted(results, key=lambda x: x.id)


def _verify(parsed: list[StepResult], truth: list[StepResult]) -> TraceVerifier:
    truth_map = {s.id: s.r for s in truth}
    matched = 0
    mismatched = []
    for step in parsed:
        if step.id in truth_map:
            if step.r == truth_map[step.id]:
                matched += 1
            else:
                mismatched.append(step.id)
    total = len(truth)
    return TraceVerifier(
        passed=matched == total,
        steps_matched=matched,
        steps_total=total,
        agreement_rate=matched / total if total > 0 else 0.0,
        mismatched_steps=mismatched,
    )


def run_task(record: dict, system_prompt: str, harness_version: str) -> Trace:
    client = _client()
    task_id = record["callID"]
    record_id = record["recordID"]
    source = record["source"]
    user_input = record["input"]
    ground_truth_steps = _normalize_ground_truth(record["output"])

    sp_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:8]
    enriched_input = user_input
    t0 = time.time()
    status = "ok"
    raw_output = ""
    error = None
    usage = None

    try:
        resp = client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": enriched_input},
            ],
            temperature=0,
        )
        raw_output = resp.choices[0].message.content or ""
        usage = resp.usage
    except Exception as e:
        status = "failed"
        error = str(e)

    wall_ms = int((time.time() - t0) * 1000)
    parsed_steps = []
    if status == "ok":
        steps_raw = _normalize_output(raw_output)
        if not steps_raw:
            status = "parse_error"
            error = "could not parse output as step list"
        else:
            for s in steps_raw:
                if isinstance(s, dict) and "id" in s and "r" in s:
                    parsed_steps.append(StepResult(
                        id=s["id"],
                        r=s.get("r", "U"),
                        e=s.get("e", ""),
                        g=s.get("g"),
                    ))

    verifier = None
    if parsed_steps and ground_truth_steps:
        verifier = _verify(parsed_steps, ground_truth_steps)

    stats = TraceStats(
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        wall_time_ms=wall_ms,
    )

    return Trace(
        task_id=task_id,
        record_id=record_id,
        source=source,
        model=config.MODEL_NAME,
        harness_version=harness_version,
        status=status,
        system_prompt_hash=sp_hash,
        raw_output=raw_output,
        parsed_steps=parsed_steps,
        ground_truth_steps=ground_truth_steps,
        verifier=verifier,
        stats=stats,
        error=error,
    )
