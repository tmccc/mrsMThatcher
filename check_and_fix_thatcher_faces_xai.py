#!/usr/bin/env python3
"""
Assess Margaret Thatcher facial likeness in an approved generated-image corpus,
edit only images whose Thatcher likeness is materially inaccurate, verify that
the edit improved identity without materially changing the composition, and
write accepted corrected PNGs into a subdirectory using the original filename.

The source images are NEVER modified.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image, UnidentifiedImageError

API_BASE = "https://api.x.ai/v1"
RESPONSES_URL = f"{API_BASE}/responses"
IMAGE_EDITS_URL = f"{API_BASE}/images/edits"
DEFAULT_ANALYSIS_MODEL = "grok-4.5"
DEFAULT_EDIT_MODEL = "grok-imagine-image-quality"
DEFAULT_OUTPUT_SUBDIR = "grok_face_corrected"
DEFAULT_REJECTED_SUBDIR = "grok_face_rejected_candidates"
DEFAULT_MANIFEST_NAME = "grok_face_correction_manifest.json"
LOG = logging.getLogger("thatcher-face-correction")

ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thatcher_present": {"type": "boolean"},
        "thatcher_identification_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "target_person_description": {"type": "string", "maxLength": 800},
        "target_location": {"type": "string", "maxLength": 400},
        "identification_evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "other_women_present": {"type": "boolean"},
        "people_not_to_edit": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "face_visible_enough_to_judge": {"type": "boolean"},
        "likeness": {"type": "string", "enum": ["excellent", "good", "weak", "poor", "not_applicable"]},
        "needs_edit": {"type": "boolean"},
        "edit_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "problems": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "rationale": {"type": "string", "maxLength": 1600},
        "edit_focus": {"type": "string", "maxLength": 800},
    },
    "required": [
        "thatcher_present",
        "thatcher_identification_confidence",
        "target_person_description",
        "target_location",
        "identification_evidence",
        "other_women_present",
        "people_not_to_edit",
        "face_visible_enough_to_judge",
        "likeness",
        "needs_edit",
        "edit_confidence",
        "problems",
        "rationale",
        "edit_focus",
    ],
    "additionalProperties": False,
}

STRICT_LIKENESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "face_visible_enough_to_judge": {"type": "boolean"},
        "likeness": {
            "type": "string",
            "enum": [
                "highly_accurate",
                "recognisable_but_inaccurate",
                "weak_likeness",
                "poor_likeness",
                "not_judgeable",
            ],
        },
        "needs_edit": {"type": "boolean"},
        "edit_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "face_only_reasoning": {"type": "string", "maxLength": 1800},
        "problems": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "edit_focus": {"type": "string", "maxLength": 900},
    },
    "required": [
        "face_visible_enough_to_judge",
        "likeness",
        "needs_edit",
        "edit_confidence",
        "face_only_reasoning",
        "problems",
        "edit_focus",
    ],
    "additionalProperties": False,
}

VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_target_person_was_edited": {"type": "boolean"},
        "target_person_only_was_edited": {"type": "boolean"},
        "other_people_unchanged": {"type": "boolean"},
        "thatcher_identity_improved": {"type": "boolean"},
        "composition_preserved": {"type": "boolean"},
        "unrelated_material_changes": {"type": "boolean"},
        "facial_accuracy_comparison": {"type": "string", "enum": ["better", "same", "worse", "not_judgeable"]},
        "accept_edit": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "problems": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "rationale": {"type": "string", "maxLength": 1600},
    },
    "required": [
        "same_target_person_was_edited",
        "target_person_only_was_edited",
        "other_people_unchanged",
        "thatcher_identity_improved",
        "composition_preserved",
        "unrelated_material_changes",
        "facial_accuracy_comparison",
        "accept_edit",
        "confidence",
        "problems",
        "rationale",
    ],
    "additionalProperties": False,
}

ASSESSMENT_PROMPT = """
You are reviewing one already human-approved editorial image from a Margaret Thatcher quotation account.

Your FIRST and MOST IMPORTANT task is to determine whether Margaret Thatcher is actually intended to appear in
this image, and if so, which exact person she is.

CRITICAL ANTI-MISIDENTIFICATION RULES:

- Do NOT assume that any woman is Margaret Thatcher.
- Do NOT assume that any blonde woman, older woman, politician, leader, central female figure, woman in blue,
  woman with a handbag, or woman with a bouffant hairstyle is Margaret Thatcher.
- Some images may contain women who are NOT Margaret Thatcher.
- Some images may contain several women.
- Some images may contain no Margaret Thatcher at all.
- A woman must never be converted into Margaret Thatcher merely because this corpus belongs to a Thatcher account.
- False negatives are strongly preferable to turning an unrelated woman into Margaret Thatcher.

Only set thatcher_present=true when there is strong contextual and/or visual evidence that one specific person is
intended to depict Margaret Thatcher. If uncertain, set thatcher_present=false or give a low identification
confidence.

If thatcher_present=true:

1. Identify exactly ONE target person intended to be Margaret Thatcher.
2. Describe that person unambiguously using location, clothing, pose and surrounding objects.
3. State where that person is in the image.
4. List the evidence for identifying that person as Thatcher.
5. Explicitly describe any other women or female-presenting people who must NOT be edited.

The target description must be precise enough for a separate image editor to alter the correct person and nobody
else. Examples of useful descriptions:
- "the blonde woman in the blue suit standing in the centre foreground"
- "the older woman at the lectern on the far right"

Never use vague descriptions such as "the woman" when more than one person is present.

Only AFTER identifying the Thatcher target with high confidence should you assess physical likeness.

Judge whether that exact target is recognisably and plausibly Margaret Thatcher in facial structure and visible
physical appearance. Pay particular attention, where visible, to:
- face shape and proportions;
- eyes and brow;
- nose;
- mouth and smile;
- jaw and chin;
- hairstyle and hairline;
- age and overall recognisable identity.

Do NOT judge whether the scene is sensible, realistic, tasteful, politically neutral, historically plausible,
physically possible, bizarre, surreal, absurd, humorous or grotesque. The image has already passed a human
editorial review and its composition must be treated as intentional.

Be conservative about requesting an edit. Set needs_edit=true only when ALL are true:
1. thatcher_present=true;
2. one exact target person has been identified;
3. thatcher_identification_confidence is high;
4. enough of that target's face is visible to judge identity;
5. the likeness is materially weak or poor, not merely imperfect or stylised;
6. correcting the target face would meaningfully improve recognisability.

Set needs_edit=false when:
- Thatcher is not clearly present;
- identity intent is ambiguous;
- the target is tiny, turned away, silhouetted, obscured or not judgeable;
- the image is stylised or caricatured but still recognisably Thatcher;
- the likeness is good enough for normal social-media viewing.

The edit_focus field must describe ONLY the physical identity correction required for the exact target person.
It must not suggest changing composition, objects, pose, clothing, background, text, lighting, camera angle,
crop, style, or any bizarre or surreal elements.

Return only the requested structured result.
""".strip()

STRICT_LIKENESS_PROMPT_TEMPLATE = """
You are performing a SECOND-PASS, FACE-ONLY likeness audit of one exact person already identified with high
confidence as the intended Margaret Thatcher subject in a human-approved editorial image.

The identity-detection stage is already complete. Do NOT choose a different person and do NOT reconsider whether
some other woman is Margaret Thatcher.

THE EXACT PERSON TO ASSESS:
{target_person_description}

TARGET LOCATION:
{target_location}

Your task is now completely different from identity detection: judge how accurately THIS EXACT PERSON'S FACE
resembles the real Margaret Thatcher.

CRITICAL SEPARATION OF IDENTITY INTENT FROM FACIAL ACCURACY:

- Do not confuse "clearly intended to be Margaret Thatcher" with "has an accurate Margaret Thatcher face".
- A figure can be unmistakably intended as Thatcher because of hair, pearls, clothing, setting or pose while the
  face itself is generic, distorted or unlike her.
- Once the exact target person has been located, STOP using non-facial context as evidence of likeness.

FOR THE LIKENESS JUDGEMENT, IGNORE:
- hairstyle and hair colour;
- pearls and jewellery;
- blue clothing or any costume;
- handbags;
- podiums, lecterns and microphones;
- political settings;
- flags, props and scenery;
- pose and gesture;
- the fact that this image comes from a Margaret Thatcher corpus.

Mentally ask:

IF THIS FACE WERE SHOWN ALONE, WITHOUT HAIR, CLOTHING, JEWELLERY, POSE OR POLITICAL CONTEXT, WOULD IT GENUINELY
RESEMBLE MARGARET THATCHER?

Assess facial structure and visible identity cues, including where visible:
- overall face shape and proportions;
- forehead and brow structure;
- eye shape, spacing and expression;
- nose shape and proportions;
- mouth, lips and smile;
- cheeks;
- jaw and chin;
- age and characteristic facial identity.

Use this strict scale:

highly_accurate:
    The face itself strongly and specifically resembles Margaret Thatcher. Context is not needed to carry the
    identity. Minor generative imperfections are acceptable.

recognisable_but_inaccurate:
    The intended identity can be recognised, but the face itself is noticeably generic, softened, idealised,
    distorted, or wrong in important features. A correction would materially improve accuracy.

weak_likeness:
    Without hair, clothes and context, most viewers would probably not identify the face as Margaret Thatcher.
    The face needs correction.

poor_likeness:
    The face looks like a different person or a generic unrelated woman, despite the surrounding Thatcher cues.
    The face clearly needs correction.

not_judgeable:
    The exact target face is too small, obscured, turned away, heavily stylised or otherwise impossible to assess
    reliably. Do not request an edit merely because it cannot be judged.

Set needs_edit=true for:
- recognisable_but_inaccurate;
- weak_likeness;
- poor_likeness.

Set needs_edit=false only for:
- highly_accurate;
- not_judgeable.

Do not be conservative merely because editing costs money. Do not be generous merely because the overall image is
attractive or the target is obviously meant to be Thatcher. This is a strict facial-accuracy audit.

The problems and edit_focus fields must discuss ONLY the exact target person's facial likeness. They must not
suggest changing hair, clothing, jewellery, pose, composition, background, objects, text, lighting, camera angle,
crop, style or any bizarre/surreal content.

Return only the requested structured result.
""".strip()

VERIFICATION_PROMPT_TEMPLATE = """
Compare two images.

IMAGE 1 is the original human-approved editorial image.
IMAGE 2 is an AI-edited candidate intended only to correct Margaret Thatcher's physical likeness.

The exact intended Thatcher target from the original assessment is:
{target_person_description}

Target location:
{target_location}

People who were explicitly NOT to be edited:
{people_not_to_edit}

The original composition is intentional no matter how bizarre, absurd, surreal, implausible or strange it appears.

Your first task is to verify that IMAGE 2 edited the SAME exact target person and did not convert or alter another
woman or another person instead.

Accept the edit only if ALL are true:
1. the same exact target person identified above was edited;
2. only that target person's physical likeness was materially edited;
3. all other people, especially people listed above, remain materially unchanged;
4. Margaret Thatcher's recognisable physical identity is genuinely improved;
5. the composition is materially preserved;
6. there are no material unrelated changes.

Treat these as rejection reasons:
- another woman or another person was changed into Thatcher;
- the wrong person was edited;
- more than one person's identity or face was materially altered;
- any protected person listed above was materially altered;
- objects or people were added, removed, replaced or substantially redesigned;
- pose, clothing or body position changed materially;
- background or text changed;
- flags, symbols or props changed;
- lighting, camera angle, crop, framing or style changed materially;
- bizarre details were rationalised, cleaned up or normalised.

Minor generation noise not noticeable at normal social-media size is not material.

accept_edit must be true only when:
- same_target_person_was_edited=true
- target_person_only_was_edited=true
- other_people_unchanged=true
- thatcher_identity_improved=true
- composition_preserved=true
- unrelated_material_changes=false
- facial_accuracy_comparison="better"

Return only the requested structured result.
""".strip()

EDIT_PROMPT_TEMPLATE = """
Edit IMAGE 1 only.

There is exactly ONE person in IMAGE 1 who may be edited.

THE ONLY EDIT TARGET:
{target_person_description}

TARGET LOCATION:
{target_location}

This exact person is intended to be Margaret Thatcher. Correct ONLY this person's physical appearance and facial
identity so that this exact person is recognisably and accurately Margaret Thatcher.

DO NOT edit any other person. In particular, the following people must remain unchanged:
{people_not_to_edit}

Specific identity problems identified by the reviewer:
{problems}

Identity correction focus:
{edit_focus}

ABSOLUTE TARGETING RULES:

- Do not choose a different woman or different person as Margaret Thatcher.
- Do not turn another woman into Margaret Thatcher.
- Do not modify the identity of any person other than the exact target described above.
- If the target cannot be isolated confidently, preserve the image rather than changing another person.

ABSOLUTE PRESERVATION REQUIREMENT:

Preserve IMAGE 1's composition as exactly as possible. Do NOT rationalise, simplify, improve, censor, modernise,
clean up or reinterpret the scene. However crazy, bizarre, surreal, absurd, impossible or grotesque the image
seems, leave those aspects alone.

Preserve every object, every other person, every pose and gesture, body position, clothing and costume,
background, text, flags and symbols, props, lighting, camera angle, crop and framing, aspect ratio, artistic style,
and strange or surreal details.

Change only the physical likeness of the exact Thatcher target described above. Do not turn the image into a new
composition.

{reference_instruction}
""".strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Assess and selectively correct Margaret Thatcher likeness.")
    p.add_argument("--input-dir", type=Path, default=Path.cwd())
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--rejected-dir", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--pattern", default="tg_*.png")
    p.add_argument("--analysis-model", default=DEFAULT_ANALYSIS_MODEL)
    p.add_argument("--edit-model", default=DEFAULT_EDIT_MODEL)
    p.add_argument("--reference-image", action="append", type=Path, default=[])
    p.add_argument("--analyse-only", action="store_true")
    p.add_argument("--strict-likeness-reassess", action="store_true",
                   help="Reuse existing identity decisions and run a strict face-only likeness second pass.")
    p.add_argument("--min-thatcher-confidence", type=float, default=0.90,
                   help="Minimum identification confidence required before any edit. Default: 0.90")
    p.add_argument("--min-edit-confidence", type=float, default=0.80,
                   help="Minimum confidence that a likeness correction is genuinely needed. Default: 0.80")
    p.add_argument("--limit", type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--timeout", type=float, default=360.0)
    p.add_argument("--max-retries", type=int, default=5)
    p.add_argument("--sleep", type=float, default=1.0)
    p.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return p.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    args.input_dir = args.input_dir.expanduser().resolve()
    if not args.input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {args.input_dir}")
    args.output_dir = args.output_dir.expanduser().resolve() if args.output_dir else args.input_dir / DEFAULT_OUTPUT_SUBDIR
    args.rejected_dir = args.rejected_dir.expanduser().resolve() if args.rejected_dir else args.input_dir / DEFAULT_REJECTED_SUBDIR
    args.manifest = args.manifest.expanduser().resolve() if args.manifest else args.input_dir / DEFAULT_MANIFEST_NAME
    if len(args.reference_image) > 2:
        raise SystemExit("At most two --reference-image arguments are supported.")
    refs = []
    for ref in args.reference_image:
        ref = ref.expanduser().resolve()
        if not ref.is_file():
            raise SystemExit(f"Reference image does not exist: {ref}")
        refs.append(ref)
    args.reference_image = refs
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.max_retries < 1:
        raise SystemExit("--max-retries must be at least 1")
    if not 0.0 <= args.min_thatcher_confidence <= 1.0:
        raise SystemExit("--min-thatcher-confidence must be between 0 and 1")
    if not 0.0 <= args.min_edit_confidence <= 1.0:
        raise SystemExit("--min-edit-confidence must be between 0 and 1")
    if not os.environ.get("XAI_API_KEY"):
        raise SystemExit("XAI_API_KEY is not set.")


def mime_type_for(path: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "application/octet-stream")


def data_uri(path: Path) -> str:
    return f"data:{mime_type_for(path)};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def data_uri_from_bytes(data: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "created_at": utc_now(), "updated_at": utc_now(), "items": {}, "run_history": []}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema_version") != 1 or not isinstance(raw.get("items"), dict):
        raise SystemExit(f"Unsupported or malformed manifest: {path}")
    raw.setdefault("run_history", [])
    return raw


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    atomic_write_json(path, manifest)


def response_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if isinstance(item, dict) and item.get("type") == "message":
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
    raise ValueError("No output_text found in xAI Responses API payload")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
        "Content-Type": "application/json",
        "User-Agent": "MrsMThatcher-ThatcherFaceCorrection/1.0",
    })
    return s


def post_json_with_retries(session: requests.Session, url: str, payload: dict[str, Any], *, timeout: float, max_retries: int, sleep_seconds: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(url, json=payload, timeout=timeout)
            if response.status_code < 400:
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("xAI response is not a JSON object")
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                return result
            body = response.text[:2000]
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise RuntimeError(f"Transient HTTP {response.status_code}: {body}")
            raise RuntimeError(f"Non-retryable HTTP {response.status_code}: {body}")
        except (requests.Timeout, requests.ConnectionError, RuntimeError, ValueError) as exc:
            last_error = exc
            transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or "Transient HTTP" in str(exc)
            if not transient or attempt >= max_retries:
                break
            delay = min(60.0, (2 ** (attempt - 1)) + random.uniform(0, 1))
            LOG.warning("API attempt %d/%d failed transiently: %s; sleeping %.1fs", attempt, max_retries, exc, delay)
            time.sleep(delay)
    raise RuntimeError(f"xAI API request failed after retries: {last_error}") from last_error


def structured_vision_call(session: requests.Session, *, model: str, images: list[tuple[str, str]], prompt: str, schema_name: str, schema: dict[str, Any], timeout: float, max_retries: int, sleep_seconds: float) -> tuple[dict[str, Any], dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "input_image", "image_url": image_url, "detail": detail}
        for image_url, detail in images
    ]
    content.append({"type": "input_text", "text": prompt})
    payload = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        "store": False,
    }
    raw = post_json_with_retries(session, RESPONSES_URL, payload, timeout=timeout, max_retries=max_retries, sleep_seconds=sleep_seconds)
    parsed = json.loads(response_text(raw))
    if not isinstance(parsed, dict):
        raise ValueError("Structured xAI response is not a JSON object")
    return parsed, raw


def assess_image(session: requests.Session, path: Path, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    return structured_vision_call(
        session, model=args.analysis_model, images=[(data_uri(path), "high")], prompt=ASSESSMENT_PROMPT,
        schema_name="thatcher_likeness_assessment", schema=ASSESSMENT_SCHEMA,
        timeout=args.timeout, max_retries=args.max_retries, sleep_seconds=args.sleep,
    )


def strict_likeness_assess(
    session: requests.Session,
    path: Path,
    identity_assessment: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = STRICT_LIKENESS_PROMPT_TEMPLATE.format(
        target_person_description=identity_assessment.get("target_person_description") or "UNSPECIFIED TARGET",
        target_location=identity_assessment.get("target_location") or "UNSPECIFIED LOCATION",
    )
    return structured_vision_call(
        session,
        model=args.analysis_model,
        images=[(data_uri(path), "high")],
        prompt=prompt,
        schema_name="strict_thatcher_face_likeness_assessment",
        schema=STRICT_LIKENESS_SCHEMA,
        timeout=args.timeout,
        max_retries=args.max_retries,
        sleep_seconds=args.sleep,
    )


def effective_assessment(
    identity_assessment: dict[str, Any],
    strict_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if strict_result is None:
        return dict(identity_assessment)
    merged = dict(identity_assessment)
    merged.update({
        "face_visible_enough_to_judge": strict_result["face_visible_enough_to_judge"],
        "likeness": strict_result["likeness"],
        "needs_edit": strict_result["needs_edit"],
        "edit_confidence": strict_result["edit_confidence"],
        "problems": strict_result["problems"],
        "edit_focus": strict_result["edit_focus"],
    })
    return merged


def format_people_not_to_edit(assessment: dict[str, Any]) -> str:
    people = assessment.get("people_not_to_edit") or []
    if not people:
        return "- No other specific women or people were identified, but all non-target people must remain unchanged."
    return "\n".join(f"- {person}" for person in people)


def make_edit_prompt(assessment: dict[str, Any], has_references: bool) -> str:
    problems = assessment.get("problems") or []
    problems_text = "\n".join(f"- {item}" for item in problems) or "- General weak facial identity"
    if has_references:
        reference_instruction = (
            "IMAGE 2 and, if supplied, IMAGE 3 are genuine Margaret Thatcher identity references. "
            "Use them ONLY to improve the exact target person's Thatcher identity in IMAGE 1. Do not copy their "
            "backgrounds, clothing, pose, expression, crop, lighting or composition into IMAGE 1. Do not add "
            "the reference people as extra subjects and do not apply their identity to any non-target person."
        )
    else:
        reference_instruction = (
            "Use your knowledge of Margaret Thatcher's real physical appearance only to correct the exact target "
            "person. Do not apply Thatcher's identity to any other person in the image."
        )
    return EDIT_PROMPT_TEMPLATE.format(
        target_person_description=assessment.get("target_person_description") or "UNSPECIFIED TARGET",
        target_location=assessment.get("target_location") or "UNSPECIFIED LOCATION",
        people_not_to_edit=format_people_not_to_edit(assessment),
        problems=problems_text,
        edit_focus=assessment.get("edit_focus") or "Improve recognisable Margaret Thatcher identity only.",
        reference_instruction=reference_instruction,
    )


def edit_image(session: requests.Session, path: Path, assessment: dict[str, Any], args: argparse.Namespace) -> tuple[bytes, str, dict[str, Any]]:
    image_urls = [data_uri(path)] + [data_uri(ref) for ref in args.reference_image]
    prompt = make_edit_prompt(assessment, bool(args.reference_image))
    if len(image_urls) == 1:
        image_payload: dict[str, Any] = {"image": {"type": "image_url", "url": image_urls[0]}}
    else:
        image_payload = {"images": [{"type": "image_url", "url": url} for url in image_urls]}
    payload = {"model": args.edit_model, "prompt": prompt, **image_payload}
    raw = post_json_with_retries(session, IMAGE_EDITS_URL, payload, timeout=args.timeout, max_retries=args.max_retries, sleep_seconds=args.sleep)
    data = raw.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ValueError("Image edit response has no data[0] object")
    item = data[0]
    if isinstance(item.get("url"), str):
        download = requests.get(item["url"], timeout=args.timeout)
        download.raise_for_status()
        return download.content, download.headers.get("Content-Type", item.get("mime_type", "image/jpeg")), raw
    if isinstance(item.get("b64_json"), str):
        return base64.b64decode(item["b64_json"]), item.get("mime_type", "image/png"), raw
    raise ValueError("Image edit response has neither url nor b64_json")


def convert_to_png_bytes(image_bytes: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except UnidentifiedImageError as exc:
        raise ValueError("Edited response is not a decodable image") from exc


def verify_edit(
    session: requests.Session,
    original_path: Path,
    candidate_png: bytes,
    assessment: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = VERIFICATION_PROMPT_TEMPLATE.format(
        target_person_description=assessment.get("target_person_description") or "UNSPECIFIED TARGET",
        target_location=assessment.get("target_location") or "UNSPECIFIED LOCATION",
        people_not_to_edit=format_people_not_to_edit(assessment),
    )
    return structured_vision_call(
        session,
        model=args.analysis_model,
        images=[(data_uri(original_path), "high"), (data_uri_from_bytes(candidate_png, "image/png"), "high")],
        prompt=prompt,
        schema_name="thatcher_edit_verification",
        schema=VERIFICATION_SCHEMA,
        timeout=args.timeout,
        max_retries=args.max_retries,
        sleep_seconds=args.sleep,
    )


def strict_verification_accepts(result: dict[str, Any]) -> bool:
    return (
        result.get("accept_edit") is True
        and result.get("same_target_person_was_edited") is True
        and result.get("target_person_only_was_edited") is True
        and result.get("other_people_unchanged") is True
        and result.get("thatcher_identity_improved") is True
        and result.get("composition_preserved") is True
        and result.get("unrelated_material_changes") is False
        and result.get("facial_accuracy_comparison") == "better"
    )


def terminal_status(status: str) -> bool:
    return status in {
        "original_accepted",
        "strict_original_accepted",
        "strict_not_judgeable",
        "skipped_no_thatcher",
        "skipped_ambiguous_thatcher_identity",
        "skipped_inconsistent_assessment",
        "skipped_not_judgeable",
        "edit_accepted",
        "edit_rejected",
    }


def should_process(existing: dict[str, Any] | None, args: argparse.Namespace) -> bool:
    if args.strict_likeness_reassess:
        if existing is None:
            return False
        identity = existing.get("assessment")
        if not isinstance(identity, dict):
            return False
        if identity.get("thatcher_present") is not True:
            return False
        if float(identity.get("thatcher_identification_confidence", 0.0)) < args.min_thatcher_confidence:
            return False
        if not str(identity.get("target_person_description") or "").strip():
            return False
        if not str(identity.get("target_location") or "").strip():
            return False
        if identity.get("other_women_present") is True and not (identity.get("people_not_to_edit") or []):
            return False
        if args.force:
            return True
        if args.retry_failed:
            return str(existing.get("status", "")).endswith("_failed")
        strict_result = existing.get("strict_likeness_assessment")
        if isinstance(strict_result, dict):
            # After analyse-only, allow a later non-analyse-only run to edit saved failures without paying for reassessment.
            return (not args.analyse_only) and existing.get("status") == "strict_analysis_only_needs_edit"
        return True

    if args.force:
        return True
    if existing is None:
        return not args.retry_failed
    status = str(existing.get("status", ""))
    if args.retry_failed:
        return status.endswith("_failed")
    return not terminal_status(status) and not status.endswith("_failed")


def concise_usage(raw: dict[str, Any]) -> Any:
    return raw.get("usage") if isinstance(raw.get("usage"), dict) else None


def process_one_initial(session: requests.Session, source_path: Path, manifest: dict[str, Any], args: argparse.Namespace) -> None:
    item = manifest["items"].setdefault(source_path.name, {})
    item.update({"source_path": str(source_path), "started_at": utc_now(), "status": "processing", "error": None})
    save_manifest(args.manifest, manifest)
    LOG.info("Assessing %s", source_path.name)
    try:
        assessment, assessment_raw = assess_image(session, source_path, args)
        item.update({
            "assessment": assessment,
            "assessment_model": args.analysis_model,
            "assessment_usage": concise_usage(assessment_raw),
            "assessed_at": utc_now(),
        })
        save_manifest(args.manifest, manifest)
    except Exception as exc:
        item.update({"status": "assessment_failed", "error": str(exc), "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.exception("Assessment failed for %s", source_path.name)
        return

    if not assessment["thatcher_present"]:
        item.update({"status": "skipped_no_thatcher", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info("%s: no Thatcher depicted; leaving original unchanged", source_path.name)
        return
    identification_confidence = float(assessment["thatcher_identification_confidence"])
    if identification_confidence < args.min_thatcher_confidence:
        item.update({"status": "skipped_ambiguous_thatcher_identity", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning(
            "%s: Thatcher identification confidence %.2f is below %.2f; refusing to edit",
            source_path.name,
            identification_confidence,
            args.min_thatcher_confidence,
        )
        return
    if not str(assessment.get("target_person_description") or "").strip():
        item.update({"status": "skipped_ambiguous_thatcher_identity", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning("%s: Thatcher target description is empty; refusing to edit", source_path.name)
        return
    if not str(assessment.get("target_location") or "").strip():
        item.update({"status": "skipped_ambiguous_thatcher_identity", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning("%s: Thatcher target location is empty; refusing to edit", source_path.name)
        return
    if assessment.get("other_women_present") is True and not (assessment.get("people_not_to_edit") or []):
        item.update({"status": "skipped_ambiguous_thatcher_identity", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning(
            "%s: other women are present but no protected non-target people were described; refusing to edit",
            source_path.name,
        )
        return
    if not assessment["face_visible_enough_to_judge"]:
        item.update({"status": "skipped_not_judgeable", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info("%s: Thatcher face not judgeable; leaving original unchanged", source_path.name)
        return
    if assessment["needs_edit"] and assessment["likeness"] not in {"weak", "poor"}:
        item.update({"status": "skipped_inconsistent_assessment", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning(
            "%s: assessment requested an edit despite likeness=%s; refusing to edit",
            source_path.name,
            assessment["likeness"],
        )
        return
    edit_confidence = float(assessment["edit_confidence"])
    if assessment["needs_edit"] and edit_confidence < args.min_edit_confidence:
        item.update({"status": "skipped_inconsistent_assessment", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning(
            "%s: edit confidence %.2f is below %.2f; refusing to edit",
            source_path.name,
            edit_confidence,
            args.min_edit_confidence,
        )
        return
    if not assessment["needs_edit"]:
        item.update({"status": "original_accepted", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info(
            "%s: original accepted, target=%r likeness=%s identification_confidence=%.2f edit_confidence=%.2f",
            source_path.name,
            assessment["target_person_description"],
            assessment["likeness"],
            identification_confidence,
            edit_confidence,
        )
        return
    if args.analyse_only:
        item.update({"status": "analysis_only_needs_edit", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info("%s: needs edit; analyse-only mode, not editing", source_path.name)
        return

    LOG.info(
        "%s: target=%r likeness=%s needs edit; calling %s",
        source_path.name,
        assessment["target_person_description"],
        assessment["likeness"],
        args.edit_model,
    )
    try:
        edited_bytes, edited_mime, edit_raw = edit_image(session, source_path, assessment, args)
        candidate_png = convert_to_png_bytes(edited_bytes)
        item.update({
            "edit_model": args.edit_model,
            "edit_response_mime_type": edited_mime,
            "edit_usage": concise_usage(edit_raw),
            "edited_at": utc_now(),
        })
        save_manifest(args.manifest, manifest)
    except Exception as exc:
        item.update({"status": "edit_failed", "error": str(exc), "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.exception("Edit failed for %s", source_path.name)
        return

    LOG.info("Verifying edited candidate for %s", source_path.name)
    try:
        verification, verification_raw = verify_edit(session, source_path, candidate_png, assessment, args)
        item.update({
            "verification": verification,
            "verification_model": args.analysis_model,
            "verification_usage": concise_usage(verification_raw),
            "verified_at": utc_now(),
        })
        save_manifest(args.manifest, manifest)
    except Exception as exc:
        item.update({"status": "verification_failed", "error": str(exc), "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.exception("Verification failed for %s", source_path.name)
        return

    if strict_verification_accepts(verification):
        output_path = args.output_dir / source_path.name
        atomic_write_bytes(output_path, candidate_png)
        item.update({"status": "edit_accepted", "output_path": str(output_path)})
        LOG.info("ACCEPTED corrected image: %s", output_path)
    else:
        rejected_path = args.rejected_dir / source_path.name
        atomic_write_bytes(rejected_path, candidate_png)
        item.update({"status": "edit_rejected", "rejected_candidate_path": str(rejected_path)})
        LOG.warning("REJECTED edited candidate for %s; preserved at %s", source_path.name, rejected_path)
    item["finished_at"] = utc_now()
    save_manifest(args.manifest, manifest)


def process_one_strict(
    session: requests.Session,
    source_path: Path,
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    item = manifest["items"][source_path.name]
    identity = item.get("assessment")
    if not isinstance(identity, dict):
        LOG.warning("%s: no reusable identity assessment; skipping", source_path.name)
        return

    # The pending selector already enforces these gates; repeat them defensively.
    if identity.get("thatcher_present") is not True:
        LOG.warning("%s: existing identity assessment says no Thatcher; skipping", source_path.name)
        return
    identification_confidence = float(identity.get("thatcher_identification_confidence", 0.0))
    if identification_confidence < args.min_thatcher_confidence:
        LOG.warning("%s: existing identity confidence below threshold; skipping", source_path.name)
        return

    item.update({"source_path": str(source_path), "started_at": utc_now(), "status": "strict_processing", "error": None})
    save_manifest(args.manifest, manifest)

    strict_result = item.get("strict_likeness_assessment")
    if not isinstance(strict_result, dict) or args.force:
        LOG.info(
            "Strict face-only reassessment %s target=%r",
            source_path.name,
            identity.get("target_person_description"),
        )
        try:
            strict_result, strict_raw = strict_likeness_assess(session, source_path, identity, args)
            item.update({
                "strict_likeness_assessment": strict_result,
                "strict_likeness_model": args.analysis_model,
                "strict_likeness_usage": concise_usage(strict_raw),
                "strict_likeness_assessed_at": utc_now(),
            })
            save_manifest(args.manifest, manifest)
        except Exception as exc:
            item.update({"status": "strict_likeness_failed", "error": str(exc), "finished_at": utc_now()})
            save_manifest(args.manifest, manifest)
            LOG.exception("Strict likeness reassessment failed for %s", source_path.name)
            return
    else:
        LOG.info("%s: reusing saved strict likeness assessment", source_path.name)

    likeness = strict_result["likeness"]
    needs_edit = strict_result["needs_edit"] is True
    edit_confidence = float(strict_result["edit_confidence"])

    expected_needs_edit = likeness in {
        "recognisable_but_inaccurate",
        "weak_likeness",
        "poor_likeness",
    }
    if needs_edit != expected_needs_edit:
        item.update({"status": "strict_inconsistent_assessment", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning(
            "%s: strict result inconsistent: likeness=%s needs_edit=%s; refusing to edit",
            source_path.name,
            likeness,
            needs_edit,
        )
        return

    if likeness == "not_judgeable" or not strict_result["face_visible_enough_to_judge"]:
        item.update({"status": "strict_not_judgeable", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info("%s: strict face-only audit says not judgeable; leaving original unchanged", source_path.name)
        return

    if not needs_edit:
        item.update({"status": "strict_original_accepted", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info(
            "%s: strict original accepted, target=%r likeness=%s confidence=%.2f",
            source_path.name,
            identity.get("target_person_description"),
            likeness,
            edit_confidence,
        )
        return

    if edit_confidence < args.min_edit_confidence:
        item.update({"status": "strict_low_edit_confidence", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.warning(
            "%s: strict audit wants edit but confidence %.2f is below %.2f; refusing to edit",
            source_path.name,
            edit_confidence,
            args.min_edit_confidence,
        )
        return

    if args.analyse_only:
        item.update({"status": "strict_analysis_only_needs_edit", "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.info(
            "%s: strict audit needs edit, likeness=%s confidence=%.2f; analyse-only mode",
            source_path.name,
            likeness,
            edit_confidence,
        )
        return

    assessment = effective_assessment(identity, strict_result)
    LOG.info(
        "%s: strict audit target=%r likeness=%s needs edit; calling %s",
        source_path.name,
        identity.get("target_person_description"),
        likeness,
        args.edit_model,
    )
    try:
        edited_bytes, edited_mime, edit_raw = edit_image(session, source_path, assessment, args)
        candidate_png = convert_to_png_bytes(edited_bytes)
        item.update({
            "edit_model": args.edit_model,
            "edit_response_mime_type": edited_mime,
            "edit_usage": concise_usage(edit_raw),
            "edited_at": utc_now(),
        })
        save_manifest(args.manifest, manifest)
    except Exception as exc:
        item.update({"status": "edit_failed", "error": str(exc), "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.exception("Edit failed for %s", source_path.name)
        return

    LOG.info("Verifying edited candidate for %s", source_path.name)
    try:
        verification, verification_raw = verify_edit(session, source_path, candidate_png, assessment, args)
        item.update({
            "verification": verification,
            "verification_model": args.analysis_model,
            "verification_usage": concise_usage(verification_raw),
            "verified_at": utc_now(),
        })
        save_manifest(args.manifest, manifest)
    except Exception as exc:
        item.update({"status": "verification_failed", "error": str(exc), "finished_at": utc_now()})
        save_manifest(args.manifest, manifest)
        LOG.exception("Verification failed for %s", source_path.name)
        return

    if strict_verification_accepts(verification):
        output_path = args.output_dir / source_path.name
        atomic_write_bytes(output_path, candidate_png)
        item.update({"status": "edit_accepted", "output_path": str(output_path)})
        LOG.info("ACCEPTED corrected image: %s", output_path)
    else:
        rejected_path = args.rejected_dir / source_path.name
        atomic_write_bytes(rejected_path, candidate_png)
        item.update({"status": "edit_rejected", "rejected_candidate_path": str(rejected_path)})
        LOG.warning("REJECTED edited candidate for %s; preserved at %s", source_path.name, rejected_path)
    item["finished_at"] = utc_now()
    save_manifest(args.manifest, manifest)


def summarise(manifest: dict[str, Any], source_names: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, item in manifest.get("items", {}).items():
        if name in source_names:
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    validate_args(args)
    sources = sorted(path.resolve() for path in args.input_dir.glob(args.pattern) if path.is_file())
    if not sources:
        LOG.error("No source images found: %s/%s", args.input_dir, args.pattern)
        return 2
    manifest = load_manifest(args.manifest)
    source_names = {path.name for path in sources}
    run_record = {
        "started_at": utc_now(), "input_dir": str(args.input_dir), "output_dir": str(args.output_dir),
        "rejected_dir": str(args.rejected_dir), "pattern": args.pattern, "analysis_model": args.analysis_model,
        "edit_model": args.edit_model, "analyse_only": args.analyse_only,
        "strict_likeness_reassess": args.strict_likeness_reassess,
        "min_thatcher_confidence": args.min_thatcher_confidence,
        "min_edit_confidence": args.min_edit_confidence,
        "reference_images": [str(path) for path in args.reference_image], "source_count": len(sources),
    }
    manifest["run_history"].append(run_record)
    save_manifest(args.manifest, manifest)
    pending = [path for path in sources if should_process(manifest["items"].get(path.name), args)]
    if args.limit is not None:
        pending = pending[:args.limit]
    LOG.info("Source images: %d", len(sources))
    LOG.info("Images selected for this run: %d", len(pending))
    LOG.info("Output directory: %s", args.output_dir)
    LOG.info("Manifest: %s", args.manifest)
    if args.strict_likeness_reassess:
        LOG.info("Strict likeness reassessment mode: reusing existing identity decisions")
    if args.analyse_only:
        LOG.info("Analyse-only mode: image editing is disabled")
    if args.reference_image:
        LOG.info("Using %d real Thatcher reference image(s)", len(args.reference_image))
    session = make_session()
    for index, source_path in enumerate(pending, start=1):
        LOG.info("[%d/%d] %s", index, len(pending), source_path.name)
        if args.strict_likeness_reassess:
            process_one_strict(session, source_path, manifest, args)
        else:
            process_one_initial(session, source_path, manifest, args)
    run_record["finished_at"] = utc_now()
    run_record["summary"] = summarise(manifest, source_names)
    save_manifest(args.manifest, manifest)
    print("\nFinal corpus status\n===================")
    for status, count in summarise(manifest, source_names).items():
        print(f"{status:28s} {count:3d}")
    print(f"\nAccepted corrected images: {args.output_dir}")
    print(f"Rejected edit candidates:  {args.rejected_dir}")
    print(f"Manifest:                  {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
