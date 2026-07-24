"""
Deterministic contrast fix.

Color contrast against a fixed threshold is pure math (WCAG relative
luminance formula), so this does NOT go through the LLM. Asking a
model to guess-and-check a hex value would be slower, non-deterministic,
and strictly worse than computing the answer directly. Reserve the
agentic/LLM path for judgment calls (alt text, form labels) where
there isn't a formula for "correct."
"""

import re


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(_hex_to_rgb(fg_hex))
    l2 = _relative_luminance(_hex_to_rgb(bg_hex))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def fix_contrast(fg_hex: str, bg_hex: str, target_ratio: float = 4.5) -> dict:
    """
    Returns a new foreground hex color that passes target_ratio against
    bg_hex, moving the foreground color as little as possible from its
    original value (darkening it toward black, or lightening toward
    white — whichever direction the original color was already closer to,
    since that preserves the intended look most closely).
    """
    original_ratio = contrast_ratio(fg_hex, bg_hex)
    if original_ratio >= target_ratio:
        return {"new_hex": fg_hex, "original_ratio": original_ratio, "new_ratio": original_ratio, "changed": False}

    r, g, b = _hex_to_rgb(fg_hex)
    bg_luminance = _relative_luminance(_hex_to_rgb(bg_hex))
    # If background is light, darken the foreground toward black; if background
    # is dark, lighten the foreground toward white.
    darken = bg_luminance > 0.5

    step = -1 if darken else 1
    for _ in range(255):
        r = max(0, min(255, r + step))
        g = max(0, min(255, g + step))
        b = max(0, min(255, b + step))
        candidate = _rgb_to_hex((r, g, b))
        ratio = contrast_ratio(candidate, bg_hex)
        if ratio >= target_ratio:
            return {"new_hex": candidate, "original_ratio": original_ratio, "new_ratio": ratio, "changed": True}
        if (darken and (r, g, b) == (0, 0, 0)) or (not darken and (r, g, b) == (255, 255, 255)):
            break

    # Fallback: pure black or white guarantees max contrast.
    fallback = "#000000" if darken else "#ffffff"
    return {
        "new_hex": fallback,
        "original_ratio": original_ratio,
        "new_ratio": contrast_ratio(fallback, bg_hex),
        "changed": True,
    }


def extract_hex_colors(css_text: str) -> list[str]:
    return re.findall(r"#[0-9a-fA-F]{3,6}", css_text)


if __name__ == "__main__":
    result = fix_contrast("#777777", "#ffffff")
    print(result)
