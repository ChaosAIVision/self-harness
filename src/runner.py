"""Run model on a task record and produce a Trace."""
from __future__ import annotations
import json
import re
import time
import hashlib
from openai import OpenAI
from trace_schema import Trace, TraceVerifier, TraceStats, StepResult
import config

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 8]  # seconds


def _client() -> OpenAI:
    return OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)


def _extract_thinking(message, content: str) -> str | None:
    """The model's reasoning trace, kept as a scoring resource. Prefer a
    reasoning-model's dedicated field; else lift any <think>…</think> block, else
    the prose preamble that sits before the JSON answer."""
    for attr in ("reasoning_content", "reasoning"):
        v = getattr(message, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if not content:
        return None
    tags = re.findall(r"<think(?:ing)?>(.*?)</think(?:ing)?>", content, flags=re.DOTALL | re.IGNORECASE)
    if tags:
        return "\n".join(t.strip() for t in tags).strip()
    # No tag/field: treat text before the first JSON span as the reasoning.
    for span in _iter_json_spans(content):
        head = content.split(span, 1)[0].strip()
        return head or None
    return None


def _to_steps(parsed) -> list[dict]:
    """Turn a parsed JSON value into a flat list of step dicts."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        # Round-structured: {"round1": {"step1": {...}, ...}, "round2": {...}, ...}
        steps = []
        for rk in ["round1", "round2", "round3"]:
            rd = parsed.get(rk)
            if isinstance(rd, dict):
                # Steps may be named step1-step9 regardless of round key
                for sv in sorted(rd.values(), key=lambda x: x.get("id", 0) if isinstance(x, dict) else 0):
                    if isinstance(sv, dict) and "id" in sv:
                        steps.append(sv)
        if steps:
            return steps
        if isinstance(parsed.get("steps"), list):
            return parsed["steps"]
        if "id" in parsed and "r" in parsed:
            return [parsed]
    return []


def _iter_json_spans(s: str):
    """Yield every balanced [...] / {...} substring, respecting JSON strings and
    escapes so brackets inside quoted prose (e.g. "[telesales]") don't confuse
    the matcher."""
    i, n = 0, len(s)
    while i < n:
        if s[i] in "[{":
            depth = 0
            in_str = False
            esc = False
            j = i
            while j < n:
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                    if depth == 0:
                        yield s[i:j + 1]
                        break
                j += 1
            i = j + 1
        else:
            i += 1


def _normalize_output(raw: str) -> list[dict]:
    """Extract the step list from model output. Tolerates a reasoning/thinking
    preamble, <think> tags, markdown code fences, and stray brackets in the
    prose — the actual answer (array or round-object) trails the reasoning."""
    if not raw:
        return []
    text = raw.strip()
    # Drop inline reasoning blocks some models emit before the answer.
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    # Prefer a fenced code block's content (answers are usually fenced); if there
    # are several, the final answer is the last one.
    fences = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    search = fences[-1].strip() if fences else text
    # Fast path: the whole thing is clean JSON (model output or ground truth).
    try:
        steps = _to_steps(json.loads(search))
        if steps:
            return steps
    except json.JSONDecodeError:
        pass
    # Fallback: scan for balanced JSON spans, keep the LAST that yields steps.
    best: list[dict] = []
    for span in _iter_json_spans(search):
        try:
            steps = _to_steps(json.loads(span))
        except json.JSONDecodeError:
            continue
        if steps:
            best = steps
    return best


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
    thinking = None
    error = None
    usage = None

    try:
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.chat.completions.create(
                    model=config.MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": enriched_input},
                    ],
                    temperature=0,
                )
                message = resp.choices[0].message
                raw_output = message.content or ""
                thinking = _extract_thinking(message, raw_output)
                usage = resp.usage
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF[attempt])
                else:
                    status = "failed"
                    error = f"All {MAX_RETRIES} attempts failed. Last: {e}"
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
        thinking=thinking,
        parsed_steps=parsed_steps,
        ground_truth_steps=ground_truth_steps,
        verifier=verifier,
        stats=stats,
        error=error,
    )
