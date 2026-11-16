"""Colour-vision helpers for the theme tests (RZG-33): WCAG contrast, simulated
colour blindness (Machado et al. 2009) and CIEDE2000 colour difference."""

from __future__ import annotations

import math

# Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear RGB.
CVD_MATRICES = {
    "protanopia": ((0.152286, 1.052583, -0.204868),
                   (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968),
                     (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779),
                   (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}
VISIONS = ("normal", *CVD_MATRICES)


def _linear(hex_colour: str) -> tuple[float, float, float]:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                 for c in channels)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours."""
    def luminance(hex_colour: str) -> float:
        r, g, b = _linear(hex_colour)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def simulate(hex_colour: str, vision: str) -> tuple[float, float, float]:
    """The colour as seen with ``vision``, in linear RGB clamped to 0..1."""
    rgb = _linear(hex_colour)
    if vision == "normal":
        return rgb
    return tuple(min(1.0, max(0.0, sum(m * c for m, c in zip(row, rgb))))
                 for row in CVD_MATRICES[vision])


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str, vision: str = "normal") -> float:
    """CIEDE2000 difference between two #rrggbb colours as seen with ``vision``."""
    l1, a1, b1 = _lab(simulate(a, vision))
    l2, a2, b2 = _lab(simulate(b, vision))
    c_mean = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7)))
    a1, a2 = a1 * (1 + g), a2 * (1 + g)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    h1 = math.degrees(math.atan2(b1, a1)) % 360
    h2 = math.degrees(math.atan2(b2, a2)) % 360
    dl, dc = l2 - l1, c2 - c1
    dh = 0.0 if c1 * c2 == 0 else (h2 - h1 + 180) % 360 - 180
    dh_big = 2 * math.sqrt(c1 * c2) * math.sin(math.radians(dh / 2))
    l_mean, c_mean = (l1 + l2) / 2, (c1 + c2) / 2
    if c1 * c2 == 0:
        h_mean = h1 + h2
    elif abs(h1 - h2) <= 180:
        h_mean = (h1 + h2) / 2
    else:
        h_mean = (h1 + h2 + 360) / 2 % 360
    t = (1 - 0.17 * math.cos(math.radians(h_mean - 30))
         + 0.24 * math.cos(math.radians(2 * h_mean))
         + 0.32 * math.cos(math.radians(3 * h_mean + 6))
         - 0.20 * math.cos(math.radians(4 * h_mean - 63)))
    sl = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    sc = 1 + 0.045 * c_mean
    sh = 1 + 0.015 * c_mean * t
    rt = (-2 * math.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7))
          * math.sin(math.radians(60 * math.exp(-(((h_mean - 275) / 25) ** 2)))))
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dh_big / sh) ** 2
                     + rt * (dc / sc) * (dh_big / sh))


def min_delta_e(a: str, b: str) -> float:
    """The smallest difference over normal vision and the three simulations."""
    return min(delta_e(a, b, vision) for vision in VISIONS)
