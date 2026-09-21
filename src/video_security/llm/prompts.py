from __future__ import annotations

from typing import Any

PROMPT_VERSION = "2"

EVENT_TYPES: list[str] = [
    "intrusion", "loitering", "weaving", "near_miss",
    "plate_capture", "audio_distress", "suspicious_behavior",
    "hard_brake", "hard_corner", "impact", "none",
]

TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "event_type": {"type": "string", "enum": EVENT_TYPES},
        "description": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["relevant", "event_type", "confidence"],
}

DETAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **TRIAGE_SCHEMA["properties"],
        "evidence_rationale": {"type": "string"},
        "recommended_action": {
            "type": "string",
            "enum": ["escalate", "log_only", "none"],
        },
    },
    "required": ["relevant", "event_type", "confidence", "description"],
}


def wrap_transcript(window: str) -> str:
    if not window:
        return ""
    return "<transcript>\n" + window + "\n</transcript>"


def wrap_evidence(summary: str) -> str:
    if not summary:
        return ""
    return "<evidence>\n" + summary + "\n</evidence>"


_EVENT_TYPE_STR = "|".join(EVENT_TYPES)


def build_triage_prompt(
    event_type: str,
    detector_score: float,
    start_sec: float,
    transcript_window: str,
    evidence_summary: str = "",
) -> str:
    metadata = (
        f"Security camera keyframe at {start_sec:.1f}s. "
        f"Prefilter detected possible {event_type} with score {detector_score:.2f}. "
    )
    transcript = wrap_transcript(transcript_window)
    if transcript:
        metadata += "Untrusted audio transcript attached (may be irrelevant)."
    evidence = wrap_evidence(evidence_summary)
    if evidence:
        metadata += (
            "Deterministic detector evidence from the full clip attached "
            "(plates, tracks, faces, audio — may be unrelated to this event)."
        )
    schema_desc = (
        f'Output ONLY valid JSON matching: {{"relevant": boolean, '
        f'"event_type": "{_EVENT_TYPE_STR}", '
        f'"confidence": "low|medium|high", "description": string}}. '
    )
    parts = [metadata, schema_desc, "Is this a genuine security-relevant event?"]
    if transcript:
        parts.insert(1, transcript)
    if evidence:
        parts.insert(1, evidence)
    return " ".join(parts)


def build_detail_prompt(
    event_type: str,
    detector_score: float,
    start_sec: float,
    end_sec: float,
    transcript_window: str,
    tiled: bool = False,
    evidence_summary: str = "",
) -> str:
    metadata = (
        f"Security camera keyframes spanning {start_sec:.1f}s to {end_sec:.1f}s. "
        f"Prefilter detected possible {event_type} with score {detector_score:.2f}. "
    )
    if tiled:
        metadata += (
            "These tiles are zoomed crops of the keyframe with 15% overlap. "
        )
    transcript = wrap_transcript(transcript_window)
    if transcript:
        metadata += "Untrusted audio transcript attached (may be irrelevant)."
    evidence = wrap_evidence(evidence_summary)
    if evidence:
        metadata += (
            "Deterministic detector evidence from the full clip attached "
            "(plates, tracks, faces, audio — may be unrelated to this event)."
        )
    schema_desc = (
        f'Output ONLY valid JSON matching: {{"relevant": boolean, '
        f'"event_type": "{_EVENT_TYPE_STR}", '
        f'"description": string, "confidence": "low|medium|high", '
        f'"evidence_rationale": string citing visible elements, '
        f'"recommended_action": "escalate|log_only|none"}}. '
    )
    parts = [
        metadata,
        schema_desc,
        "Analyze all keyframes and determine if this is a genuine security event.",
    ]
    if transcript:
        parts.insert(1, transcript)
    if evidence:
        parts.insert(1, evidence)
    return " ".join(parts)