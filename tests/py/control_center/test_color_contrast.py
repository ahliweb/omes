"""WCAG 2.x contrast check for the Control Center design palette (issue
#211/#212).

docs/ui-ux-design-system.md #9 (Accessibility) and PR #212 claim WCAG-AA
(4.5:1) contrast for body/label text against the canvas/panel backgrounds
in the palette. This test makes that claim true rather than aspirational:
it parses the palette hex values straight out of docs/ui-ux-design-system.md
#2.1 (the same tokens ui/control-center/index.html uses inline) and
computes WCAG 2.x relative luminance / contrast ratio for every text-on-
background pair the design actually renders, so a future palette tweak
that drops a pair below 4.5:1 fails this test instead of silently
regressing. Pure Python stdlib only (ADR-0012); no external color library.
"""
import os
import re
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
_DESIGN_DOC = os.path.join(_REPO_ROOT, "docs", "ui-ux-design-system.md")

# Labels as they appear in docs/ui-ux-design-system.md #2.1, e.g.:
#   "  Text Dim:            #7A8894  (Tertiary captions and subtle labels)"
_TOKEN_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z /]*?):\s*#([0-9A-Fa-f]{6})\b", re.MULTILINE
)

# The background surfaces body/label text is rendered on (docs #2.1
# "Canvas & Panels"), and the text tokens documented as meeting AA against
# them (docs #9 Accessibility: "#7A8894 and lighter").
_BACKGROUND_LABELS = (
    "Canvas Background",
    "Panel / Card Base",
    "Panel Hover / Alt",
    "Sidebar Background",
)
_TEXT_LABELS = (
    "Text Primary",
    "Text Muted",
    "Text Dim",
)

AA_NORMAL_TEXT_RATIO = 4.5


def parse_palette(doc_text: str) -> dict[str, str]:
    """Return {label: '#RRGGBB'} for every "Label: #HEX" line in the
    palette code block of docs/ui-ux-design-system.md #2.1."""
    palette: dict[str, str] = {}
    for label, hexval in _TOKEN_LINE_RE.findall(doc_text):
        palette[label.strip()] = hexval.upper()
    return palette


def _linearize(channel_255: int) -> float:
    c = channel_255 / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance for a #RRGGBB (or RRGGBB) sRGB color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 2.x contrast ratio between two sRGB colors, always >= 1.0."""
    l_a, l_b = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = (l_a, l_b) if l_a >= l_b else (l_b, l_a)
    return (lighter + 0.05) / (darker + 0.05)


class TestControlCenterColorContrast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_DESIGN_DOC, encoding="utf-8") as fh:
            cls.doc_text = fh.read()
        cls.palette = parse_palette(cls.doc_text)

    def test_palette_parses_the_documented_tokens(self):
        for label in _BACKGROUND_LABELS + _TEXT_LABELS:
            self.assertIn(label, self.palette, f"palette token missing from {_DESIGN_DOC}: {label!r}")

    def test_every_text_on_background_pair_meets_wcag_aa_normal_text(self):
        failures = []
        for text_label in _TEXT_LABELS:
            for bg_label in _BACKGROUND_LABELS:
                ratio = contrast_ratio(self.palette[text_label], self.palette[bg_label])
                if ratio < AA_NORMAL_TEXT_RATIO:
                    failures.append(f"{text_label} on {bg_label}: {ratio:.3f} < {AA_NORMAL_TEXT_RATIO}")
        self.assertEqual(failures, [], "WCAG-AA (4.5:1) contrast failures:\n" + "\n".join(failures))

    def test_the_tightest_documented_pair_has_not_regressed_below_aa(self):
        # docs #9 flags Text Dim as the dimmest token meeting AA; Text Dim
        # on Panel Hover / Alt is the tightest of all pairs checked here
        # (~4.51:1, a 0.01 margin). Pin it explicitly so a palette tweak
        # that erodes this specific margin is caught even if some other
        # pair coincidentally still passes.
        ratio = contrast_ratio(self.palette["Text Dim"], self.palette["Panel Hover / Alt"])
        self.assertGreaterEqual(ratio, AA_NORMAL_TEXT_RATIO)

    def test_relative_luminance_of_black_and_white(self):
        # Sanity-check the luminance/ratio math itself against known values.
        self.assertAlmostEqual(relative_luminance("000000"), 0.0, places=6)
        self.assertAlmostEqual(relative_luminance("FFFFFF"), 1.0, places=6)
        self.assertAlmostEqual(contrast_ratio("000000", "FFFFFF"), 21.0, places=6)
        self.assertAlmostEqual(contrast_ratio("FFFFFF", "FFFFFF"), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
