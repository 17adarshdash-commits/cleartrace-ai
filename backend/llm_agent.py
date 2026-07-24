"""
The Agent.

This is the ONLY module that makes a runtime model call, and it's used
only for violations that require judgment rather than a formula:
  - missing alt text (needs to actually look at the image)
  - missing form labels (needs to deduce field purpose from DOM context)

IMPORTANT: this calls the Responses API, not the older Chat Completions
API. OpenAI deprecated Chat Completions support for Codex models (full
removal was slated for February 2026), so Codex-family models need the
Responses API's request/response shape, which differs from what you
may be used to. Before you build the demo around this: confirm the
exact model slug you have access to (e.g. a current "-codex" variant —
check your account's live model list, don't assume the exact string)
and set MODEL_NAME accordingly.

Every call returns the model's own reasoning verbatim in "reasoning" —
that field is what should stream into your live demo dashboard. Don't
hand-write status strings to display instead; that's staging autonomy
you didn't show.
"""

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

MODEL_API_URL = os.environ.get("MODEL_API_URL", "https://api.openai.com/v1/responses")
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "REPLACE_ME")  # confirm the real -codex model slug before building further

MAX_RETRIES = 3


ALT_TEXT_SYSTEM_PROMPT = """You are an accessibility engineer fixing a specific WCAG violation \
on a real webpage. You will be shown the actual image, plus the surrounding HTML context and the \
axe-core violation detail. Decide the correct fix and respond with ONLY a JSON object, no prose \
outside it, shaped exactly like:
{"attribute": "alt", "value": "<the alt text you wrote>", "reasoning": "<your brief reasoning, 1-2 sentences>"}
Alt text must describe the image's actual informational content, not a generic placeholder. If the \
image shows a sequence of steps or stages, name each distinct step/stage individually — do not \
just say "a multi-step process" or summarize only the first and last steps."""

LABEL_SYSTEM_PROMPT = """You are an accessibility engineer fixing a missing form label. You will \
be given the input element's HTML and its surrounding DOM context (headings, hint text, sibling \
elements) — the element itself has no name/id hint, so you must deduce its purpose from context. \
Respond with ONLY a JSON object, shaped exactly like:
{"attribute": "aria-label", "value": "<label text>", "reasoning": "<your brief reasoning, 1-2 sentences>"}"""


def _call_model(system_prompt: str, user_content: str, image_base64: str | None = None,
                 image_mime: str = "image/svg+xml") -> dict:
    if not MODEL_API_KEY:
        raise RuntimeError(
            "MODEL_API_KEY is not set. Set it in your .env before running a real audit — "
            "see backend/.env.example."
        )
    full_prompt = (
        f"{system_prompt}\n\nRespond with ONLY the JSON object described above — "
        f"no markdown code fences, no prose before or after it.\n\n{user_content}"
    )

    if image_base64:
        # Multimodal input: text + the actual image, not just its filename/URL as text.
        input_content = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": full_prompt},
                    {"type": "input_image", "image_url": f"data:{image_mime};base64,{image_base64}"},
                ],
            }
        ]
        payload = {"model": MODEL_NAME, "input": input_content}
    else:
        payload = {"model": MODEL_NAME, "input": full_prompt}

    headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
    resp = httpx.post(MODEL_API_URL, json=payload, headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Model API error {resp.status_code}: {resp.text}")
    data = resp.json()

    # Responses API returns output as a list of items; find the text content.
    raw_text = None
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    raw_text = content.get("text")
                    break
    if raw_text is None:
        raise RuntimeError(f"Could not find text output in Responses API result: {data}")

    return _extract_json(raw_text)


def _extract_json(raw_text: str) -> dict:
    """
    Models sometimes wrap JSON in ```json fences or add a stray sentence
    despite instructions not to. Strip fences, then fall back to slicing
    between the first '{' and last '}' before giving up.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise RuntimeError(f"Model did not return parseable JSON: {raw_text!r}")


def fix_alt_text(image_url: str, surrounding_html: str, violation_detail: str,
                  attempt_history: list[str] | None = None,
                  image_base64: str | None = None, image_mime: str = "image/svg+xml") -> dict:
    context = f"Image filename (for reference only, do not rely on this for content): {image_url}\n" \
              f"Surrounding HTML:\n{surrounding_html}\nViolation: {violation_detail}"
    if attempt_history:
        context += "\n\nPrevious attempts that still failed re-scan:\n" + "\n".join(attempt_history)
    return _call_model(ALT_TEXT_SYSTEM_PROMPT, context, image_base64=image_base64, image_mime=image_mime)


def fix_form_label(field_html: str, surrounding_html: str, violation_detail: str,
                    attempt_history: list[str] | None = None) -> dict:
    context = f"Field HTML: {field_html}\nSurrounding context:\n{surrounding_html}\nViolation: {violation_detail}"
    if attempt_history:
        context += "\n\nPrevious attempts that still failed re-scan:\n" + "\n".join(attempt_history)
    return _call_model(LABEL_SYSTEM_PROMPT, context)
