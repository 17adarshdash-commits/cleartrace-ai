"""
The Orchestrator.

Wires the Auditor (axe-core), the Agent (LLM calls for judgment-based
fixes), the deterministic contrast fixer, and the Surgeon (patcher)
into one loop:

    scan -> for each violation, plan a fix -> apply it -> re-scan ->
    if it still fails and attempts < MAX_RETRIES, try again with the
    failure history included -> otherwise flag for human review.

Every step is appended to a log list with a genuine "reasoning" field
where one exists (verbatim from the model, for alt-text/label fixes)
so the frontend can stream real steps, not narrated ones.

This is intentionally synchronous and single-file for a hackathon
timeline. If you have time left after the core loop works, upgrading
the /audit endpoint to Server-Sent Events so the frontend gets each
step as it happens (rather than one lump response at the end) would
make the live demo noticeably stronger — but get the loop correct
and honest first.
"""

import base64
import mimetypes
import os
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import httpx

from auditor import run_audit
from contrast import fix_contrast, extract_hex_colors
from llm_agent import fix_alt_text, fix_form_label, MAX_RETRIES
from patcher import load, save, patch_attribute, patch_inline_style_color, patch_css_color

app = FastAPI(title="Accessibility Self-Auditing Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_SITE_DIR = Path(__file__).parent.parent / "demo-site"
WORKING_COPY = Path(__file__).parent.parent / "demo-site-working"

# Maps a port in the requested URL to the (source, working-copy) directory pair
# to patch. Add an entry here for every demo site you stand up on a new port —
# this is what was missing when a second site got added without updating this file.
SITE_DIRS_BY_PORT = {
    "8080": (Path(__file__).parent.parent / "demo-site", Path(__file__).parent.parent / "demo-site-working"),
    "8081": (Path(__file__).parent.parent / "demo-site-2", Path(__file__).parent.parent / "demo-site-2-working"),
    "8082": (Path(__file__).parent.parent / "demo-site-visual", Path(__file__).parent.parent / "demo-site-visual-working"),
}


def _dirs_for_url(url: str):
    from urllib.parse import urlparse
    port = str(urlparse(url).port or "8080")
    if port not in SITE_DIRS_BY_PORT:
        raise ValueError(
            f"No demo site directory configured for port {port}. "
            f"Add an entry to SITE_DIRS_BY_PORT in main.py."
        )
    return SITE_DIRS_BY_PORT[port]


class AuditRequest(BaseModel):
    url: str = "http://localhost:8080/index.html"
    html_filename: str = "index.html"


def _reset_working_copy(source_dir: Path, working_dir: Path):
    if working_dir.exists():
        shutil.rmtree(working_dir)
    shutil.copytree(source_dir, working_dir)


def _log(steps: list, **kwargs):
    steps.append(kwargs)


@app.post("/audit")
def audit(req: AuditRequest):
    """
    Runs one full cycle against the demo site and returns:
      - before: initial violation list
      - after: final violation list
      - steps: an ordered log of every plan/act/verify/retry action taken
    """
    steps: list = []
    source_dir, working_dir = _dirs_for_url(req.url)
    _reset_working_copy(source_dir, working_dir)
    html_path = working_dir / req.html_filename

    before = run_audit(req.url)
    _log(steps, type="scan", phase="before", violation_count=len(before))

    for violation in before:
        for node in violation["nodes"]:
            selector = node["target"][0] if node["target"] else None
            if not selector:
                continue

            if violation["id"] == "image-alt":
                _fix_alt_text_violation(steps, html_path, selector, violation, node, page_url=req.url)
            elif violation["id"] == "color-contrast":
                _fix_contrast_violation(steps, html_path, selector, node)
            elif violation["id"] in ("label", "aria-input-field-name"):
                _fix_label_violation(steps, html_path, selector, violation, node, page_url=req.url)
            else:
                _log(steps, type="skip", selector=selector, reason=f"no fix strategy for rule '{violation['id']}'")

    after = run_audit(req.url)
    _log(steps, type="scan", phase="after", violation_count=len(after))

    return {"before": before, "after": after, "steps": steps}


def _fix_contrast_violation(steps, html_path, selector, node):
    """Deterministic — no model call. See contrast.py for why."""
    soup = load(str(html_path))
    element = soup.select_one(selector)
    if element is None:
        _log(steps, type="fix_failed", selector=selector, reason="element not found")
        return

    style = element.get("style", "")
    colors = extract_hex_colors(style)
    if not colors:
        _fix_contrast_via_stylesheet(steps, html_path, selector, element)
        return

    fg = colors[0]
    bg = "#ffffff"  # known from the demo site; a general tool would need to resolve computed background
    result = fix_contrast(fg, bg)
    patch_inline_style_color(soup, selector, result["new_hex"])
    save(soup, str(html_path))

    _log(
        steps, type="fix_applied", strategy="deterministic", rule="color-contrast",
        selector=selector, from_hex=fg, to_hex=result["new_hex"],
        original_ratio=round(result["original_ratio"], 2), new_ratio=round(result["new_ratio"], 2),
    )


def _fix_contrast_via_stylesheet(steps, html_path, selector, element):
    """
    Fallback for the one known case where color lives in the external
    stylesheet rather than inline: matches the element's class against
    a CSS rule and patches that instead. Bounded to this project's own
    single, known stylesheet — see patcher.patch_css_color for why this
    isn't a general solution.
    """
    css_path = html_path.parent / "style.css"
    if not css_path.exists():
        _log(steps, type="fix_failed", selector=selector, reason="no inline color and no style.css found")
        return

    classes = element.get("class", [])
    target_el = element
    if not classes and element.parent is not None:
        classes = element.parent.get("class", [])
        target_el = element.parent
    if not classes:
        _log(steps, type="fix_failed", selector=selector, reason="no inline color and no CSS class to target")
        return

    css_selector = "." + classes[0]
    css_text = css_path.read_text(encoding="utf-8")
    import re
    match = re.search(re.escape(css_selector) + r"\s*\{([^}]*)\}", css_text, re.DOTALL)
    if not match or "color:" not in match.group(1):
        _log(steps, type="fix_failed", selector=selector,
             reason=f"no color declaration found on {css_selector} in style.css")
        return

    current_hex = extract_hex_colors(match.group(1))
    if not current_hex:
        _log(steps, type="fix_failed", selector=selector, reason=f"could not parse color on {css_selector}")
        return

    fg = current_hex[0]
    bg = "#faf6f1"  # the demo site's page background — known, not resolved generically
    result = fix_contrast(fg, bg)
    patched = patch_css_color(str(css_path), css_selector, result["new_hex"])
    if not patched:
        _log(steps, type="fix_failed", selector=selector, reason=f"failed to write patch to {css_selector}")
        return

    _log(
        steps, type="fix_applied", strategy="deterministic", rule="color-contrast", via="stylesheet",
        selector=selector, css_selector=css_selector, from_hex=fg, to_hex=result["new_hex"],
        original_ratio=round(result["original_ratio"], 2), new_ratio=round(result["new_ratio"], 2),
    )


def _fix_alt_text_violation(steps, html_path, selector, violation, node, page_url, attempt=1, history=None):
    history = history or []
    soup = load(str(html_path))
    element = soup.select_one(selector)
    if element is None:
        _log(steps, type="fix_failed", selector=selector, reason="element not found")
        return

    image_url = element.get("src", "")
    surrounding = str(element.parent) if element.parent else str(element)

    image_base64, image_mime = None, "image/svg+xml"
    if image_url:
        image_path = html_path.parent / image_url
        if image_path.exists():
            image_bytes = image_path.read_bytes()
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            guessed_mime, _ = mimetypes.guess_type(str(image_path))
            image_mime = guessed_mime or "image/svg+xml"
        else:
            _log(steps, type="warning", selector=selector,
                 reason=f"image file not found on disk at {image_path}, model will not see actual image content")

    try:
        result = fix_alt_text(image_url, surrounding, violation["description"], attempt_history=history,
                               image_base64=image_base64, image_mime=image_mime)
    except RuntimeError as e:
        _log(steps, type="fix_error", selector=selector, error=str(e))
        return

    patch_attribute(soup, selector, "alt", result["value"])
    save(soup, str(html_path))
    _log(
        steps, type="fix_applied", strategy="agentic", rule="image-alt", attempt=attempt,
        selector=selector, value=result["value"], reasoning=result.get("reasoning"),
    )

    still_failing = _revalidate(page_url, selector, "image-alt")
    if still_failing and attempt < MAX_RETRIES:
        history.append(f"Attempt {attempt}: set alt=\"{result['value']}\" — still flagged.")
        _log(steps, type="retry", selector=selector, attempt=attempt + 1)
        _fix_alt_text_violation(steps, html_path, selector, violation, node, page_url, attempt + 1, history)
    elif still_failing:
        _log(steps, type="flagged_for_review", selector=selector, reason=f"exceeded {MAX_RETRIES} attempts")


def _fix_label_violation(steps, html_path, selector, violation, node, page_url, attempt=1, history=None):
    history = history or []
    soup = load(str(html_path))
    element = soup.select_one(selector)
    if element is None:
        _log(steps, type="fix_failed", selector=selector, reason="element not found")
        return

    surrounding = str(element.parent) if element.parent else str(element)

    try:
        result = fix_form_label(str(element), surrounding, violation["description"], attempt_history=history)
    except RuntimeError as e:
        _log(steps, type="fix_error", selector=selector, error=str(e))
        return

    patch_attribute(soup, selector, "aria-label", result["value"])
    save(soup, str(html_path))
    confidence = result.get("confidence", "high")
    _log(
        steps, type="fix_applied" if confidence != "low" else "fix_applied_low_confidence",
        strategy="agentic", rule=violation["id"], attempt=attempt,
        selector=selector, value=result["value"], reasoning=result.get("reasoning"), confidence=confidence,
    )

    still_failing = _revalidate(page_url, selector, violation["id"])
    if still_failing and attempt < MAX_RETRIES:
        history.append(f"Attempt {attempt}: set aria-label=\"{result['value']}\" — still flagged.")
        _log(steps, type="retry", selector=selector, attempt=attempt + 1)
        _fix_label_violation(steps, html_path, selector, violation, node, page_url, attempt + 1, history)
    elif still_failing:
        _log(steps, type="flagged_for_review", selector=selector, reason=f"exceeded {MAX_RETRIES} attempts")


def _revalidate(page_url: str, selector: str, rule_id: str) -> bool:
    """
    Re-runs the full audit against the SAME url this request targeted
    (not a hardcoded default) and checks whether a violation with this
    rule_id still targets this selector. This is the actual verify
    step — it re-reads the live served page, it does not just trust
    that the patch was applied.
    """
    results = run_audit(page_url)
    for v in results:
        if v["id"] != rule_id:
            continue
        for n in v["nodes"]:
            if n["target"] and n["target"][0] == selector:
                return True
    return False


@app.get("/view-page")
def view_page(url: str):
    """
    Proxies a fetch of the demo site's current HTML through the backend.
    Exists because the dashboard (port 5500) fetching the demo site
    (port 8080/8081) directly hits a CORS block — the demo site's plain
    Python static server sends no CORS headers. This endpoint sits on
    the backend, which already has CORS enabled for the dashboard, and
    does the actual fetch server-to-server where CORS doesn't apply.
    """
    resp = httpx.get(url, timeout=10)
    return Response(content=resp.content, media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok"}
