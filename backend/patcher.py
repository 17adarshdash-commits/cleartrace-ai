"""
The Surgeon.

Applies a fix to the actual HTML file by parsing it into a proper AST
(BeautifulSoup) and mutating the target node, rather than doing
regex/string replacement on raw text. This is what keeps fixes
structurally valid even when Codex/the model's suggested edit is a bit
loosely specified.

Note the CSS caveat: this can only patch attributes that live inline
on the element (style="color:#777" or bare attributes like alt/
aria-label). The demo site is deliberately built with the contrast
violation as an inline style for exactly this reason — if you point
this at a real site where color comes from an external stylesheet or
a <style> block, this module cannot safely reach it without a CSS
parser, which isn't built here. Say that limitation explicitly in your
project write-up rather than letting it be a silent gap.
"""

from bs4 import BeautifulSoup


def load(html_path: str) -> BeautifulSoup:
    with open(html_path, "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "html.parser")


def save(soup: BeautifulSoup, html_path: str) -> None:
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(str(soup))


def patch_css_color(css_path: str, selector: str, new_hex: str) -> bool:
    """
    Handles the one case the HTML-AST patcher above can't reach: a color
    declaration living in an external stylesheet rather than an inline
    style attribute. This uses a plain string/regex replacement rather
    than a proper CSS parser — that's a deliberate, bounded exception to
    the "no regex on markup" rule elsewhere in this project, justified
    only because this is a single small stylesheet we authored ourselves,
    not arbitrary third-party CSS. Don't reuse this pattern against a
    real site's stylesheet without a real CSS parser.
    """
    import re
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    # Find the selector's rule block, then replace its color property.
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}", re.DOTALL)
    match = pattern.search(css)
    if not match:
        return False

    block = match.group(1)
    if "color:" not in block:
        return False

    new_block = re.sub(r"color:\s*#[0-9a-fA-F]{3,6}\s*;", f"color: {new_hex};", block)
    new_css = css[:match.start(1)] + new_block + css[match.end(1):]

    with open(css_path, "w", encoding="utf-8") as f:
        f.write(new_css)
    return True


def patch_attribute(soup: BeautifulSoup, css_selector: str, attribute: str, value: str) -> bool:
    """
    Finds the element via CSS selector (axe-core gives us these as the
    violation "target") and sets attribute=value on it. Returns True if
    a matching element was found and patched.
    """
    element = soup.select_one(css_selector)
    if element is None:
        return False
    element[attribute] = value
    return True


def patch_inline_style_color(soup: BeautifulSoup, css_selector: str, new_hex: str) -> bool:
    """
    Special case for the contrast fixer: updates (or adds) the `color`
    property inside an inline style="" attribute without disturbing
    any other properties already set there.
    """
    element = soup.select_one(css_selector)
    if element is None:
        return False

    existing_style = element.get("style", "")
    props = {}
    for decl in existing_style.split(";"):
        decl = decl.strip()
        if not decl or ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        props[prop.strip()] = val.strip()

    props["color"] = new_hex
    element["style"] = "; ".join(f"{k}: {v}" for k, v in props.items())
    return True
