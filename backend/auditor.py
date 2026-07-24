"""
The Auditor.

Spins up a headless browser with Playwright, loads the target page,
runs axe-core against it, and returns a clean list of violations.
This is the same check used before AND after fixes are applied —
the re-scan is what makes the "verify" step in the agent loop honest
rather than a claim.
"""

from playwright.sync_api import sync_playwright
from axe_playwright_python.sync_playwright import Axe


def run_audit(url: str) -> list[dict]:
    """
    Returns a list of violation dicts, each shaped like:
    {
        "id": "color-contrast",              # axe-core rule id
        "impact": "serious",
        "description": "...",
        "help": "...",
        "nodes": [
            {"target": ["button.cta-button"], "html": "<button ...>...</button>", "failureSummary": "..."}
        ]
    }
    This is the raw shape the rest of the pipeline (context extraction,
    the LLM call, the patcher) is built around.
    """
    axe = Axe()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        results = axe.run(page)
        browser.close()

    violations = results.response.get("violations", [])
    cleaned = []
    for v in violations:
        cleaned.append({
            "id": v["id"],
            "impact": v.get("impact"),
            "description": v.get("description"),
            "help": v.get("help"),
            "nodes": [
                {
                    "target": n.get("target"),
                    "html": n.get("html"),
                    "failureSummary": n.get("failureSummary"),
                }
                for n in v.get("nodes", [])
            ],
        })
    return cleaned


if __name__ == "__main__":
    import json
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/index.html"
    print(json.dumps(run_audit(target), indent=2))
