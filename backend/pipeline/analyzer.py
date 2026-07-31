"""Step 3 — ask Claude (AWS Bedrock) to pick the best short-form clips."""
from __future__ import annotations

import json
import re

import boto3

import config


class AnalysisError(RuntimeError):
    pass


SYSTEM = """You are a world-class short-form video editor. You have grown multiple
YouTube and Instagram channels by turning long videos into viral Shorts/Reels.
You are given the transcript of a video with timestamps. Select the segments
that will perform best as standalone vertical clips."""

PROMPT_TEMPLATE = """Video title: {title}
Video duration: {duration:.0f} seconds

Transcript (each line is "[start - end] text" in seconds):
{transcript}

Select up to {max_clips} clips. Rules:
- Each clip must be {min_s:.0f}-{max_s:.0f} seconds long.
- A clip MUST start at the beginning of a sentence and end at the end of a
  sentence — never cut mid-thought. Use the segment timestamps to snap.
- Prioritize: strong hooks, surprising claims, complete self-contained ideas,
  actionable tips, emotional or funny moments.
- Do not overlap clips. Spread across the video if quality allows.
- Titles: short, curiosity-driven, no clickbait lies, max 60 characters.

For each clip also write ready-to-post metadata:
- post_title: YouTube Shorts title, <=90 chars, curiosity-driven, may include ONE emoji
- description: 2-3 sentences for the post, plain text, ending with a soft CTA
  to the full video
- hashtags: 4-6 lowercase hashtags, mix of broad (#devops) and specific

Respond with ONLY a JSON array, no markdown, no commentary:
[
  {{
    "start": <number, seconds>,
    "end": <number, seconds>,
    "title": "<clip title>",
    "hook": "<the first spoken line of the clip>",
    "reason": "<one sentence: why this will perform>",
    "post_title": "<shorts title>",
    "description": "<post description>",
    "hashtags": ["#tag1", "#tag2"]
  }}
]"""


def _format_transcript(segments: list[dict]) -> str:
    return "\n".join(
        f"[{s['start']:.1f} - {s['end']:.1f}] {s['text']}" for s in segments
    )


def _extract_json(text: str):
    text = text.strip()
    # Strip markdown code fences if the model added them
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise AnalysisError(f"Model did not return JSON. Got: {text[:300]}")
    return json.loads(m.group(0))


def _converse(client, messages):
    try:
        resp = client.converse(
            modelId=config.BEDROCK_MODEL_ID,
            system=[{"text": SYSTEM}],
            messages=messages,
            inferenceConfig={"maxTokens": 4000, "temperature": 0.4},
        )
    except Exception as e:
        name = type(e).__name__
        msg = str(e)
        if "AccessDenied" in name or "AccessDenied" in msg:
            raise AnalysisError(
                "Bedrock access denied. Check: (1) your IAM role has "
                "AmazonBedrockFullAccess, (2) you submitted the one-time "
                "Anthropic use-case form in the Bedrock console playground, "
                f"(3) model '{config.BEDROCK_MODEL_ID}' exists in region "
                f"'{config.AWS_REGION}'. Note: first invocation auto-subscribes "
                "via Marketplace and can take ~2 minutes — try again shortly."
            )
        if "ValidationException" in name or "on-demand throughput" in msg:
            raise AnalysisError(
                f"Model ID '{config.BEDROCK_MODEL_ID}' was rejected. Use a "
                "cross-region inference profile ID (global./us./apac. prefix), "
                "not the base model ID. Set BEDROCK_MODEL_ID accordingly."
            )
        if "ThrottlingException" in name or "Throttling" in msg:
            raise AnalysisError("Bedrock throttled the request. Wait a minute and retry.")
        raise AnalysisError(f"Bedrock call failed ({name}): {msg[:300]}")
    return "".join(
        block.get("text", "") for block in resp["output"]["message"]["content"]
    )


def find_clips(title: str, duration: float, segments: list[dict], max_clips: int) -> list[dict]:
    client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)

    prompt = PROMPT_TEMPLATE.format(
        title=title,
        duration=duration,
        transcript=_format_transcript(segments),
        max_clips=max_clips,
        min_s=config.CLIP_MIN_SECONDS,
        max_s=config.CLIP_MAX_SECONDS,
    )

    messages = [{"role": "user", "content": [{"text": prompt}]}]
    text = _converse(client, messages)

    try:
        clips = _extract_json(text)
    except (AnalysisError, json.JSONDecodeError):
        # One corrective retry: feed back the bad output, demand pure JSON
        messages += [
            {"role": "assistant", "content": [{"text": text}]},
            {"role": "user", "content": [{"text":
                "That was not valid JSON. Respond again with ONLY the JSON "
                "array, no markdown fences, no commentary."}]},
        ]
        text = _converse(client, messages)
        clips = _extract_json(text)

    # Validate & sanitize
    cleaned = []
    for c in clips:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        start = max(0.0, start)
        end = min(duration or end, end)
        length = end - start
        if length < config.CLIP_MIN_SECONDS - 3 or length > config.CLIP_MAX_SECONDS + 10:
            continue
        tags = c.get("hashtags") or []
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip()[:40] for t in tags if str(t).strip()][:6]
        cleaned.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "title": str(c.get("title", "Clip"))[:80],
                "hook": str(c.get("hook", ""))[:200],
                "reason": str(c.get("reason", ""))[:300],
                "post_title": str(c.get("post_title", c.get("title", "Clip")))[:120],
                "description": str(c.get("description", ""))[:500],
                "hashtags": tags,
            }
        )
    cleaned.sort(key=lambda c: c["start"])
    # Enforce non-overlap: drop any clip overlapping >20% with the last kept one
    non_overlapping = []
    for c in cleaned:
        if non_overlapping:
            prev = non_overlapping[-1]
            overlap = min(prev["end"], c["end"]) - max(prev["start"], c["start"])
            if overlap > 0.2 * (c["end"] - c["start"]):
                continue
        non_overlapping.append(c)
    if not non_overlapping:
        raise AnalysisError("No valid clips found in the model response.")
    return non_overlapping[:max_clips]
