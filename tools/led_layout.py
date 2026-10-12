"""Berechnet aus monitors.json die LED-Aufteilung pro Monitor und das Strombudget.

Aufruf:  python tools/led_layout.py [pfad/zu/monitors.json]
"""

import json
import math
import sys
from pathlib import Path

INCH_CM = 2.54


def panel_size_cm(diagonal_inch, aspect):
    """Breite und Hoehe der Bildflaeche aus Diagonale und Seitenverhaeltnis."""
    w, h = aspect
    diag = math.hypot(w, h)
    return diagonal_inch * INCH_CM * w / diag, diagonal_inch * INCH_CM * h / diag


def monitor_leds(mon, leds_per_m, inset_cm):
    if "width_cm" in mon and "height_cm" in mon:
        width, height = mon["width_cm"], mon["height_cm"]
    else:
        width, height = panel_size_cm(mon["diagonal_inch"], mon["aspect"])
    # Der Streifen sitzt etwas eingerueckt auf der Rueckseite, nicht exakt am Rand
    width -= 2 * inset_cm
    height -= 2 * inset_cm
    horizontal = round(width / 100 * leds_per_m)
    vertical = round(height / 100 * leds_per_m)
    return width, height, horizontal, vertical


def main():
    default = Path(__file__).resolve().parent.parent / "monitors.json"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    cfg = json.loads(path.read_text(encoding="utf-8"))

    strip, psu = cfg["strip"], cfg["psu"]
    available = strip["count"] * strip["leds_per_strip"]

    print(f"{'Monitor':<14}{'Flaeche cm':>14}{'oben/unten':>12}{'links/rechts':>14}{'LEDs':>7}{'Start':>7}{'Meter':>7}")
    start = 0
    for mon in cfg["monitors"]:
        if not mon.get("enabled", True):
            continue
        width, height, hor, ver = monitor_leds(mon, strip["leds_per_m"], cfg["inset_cm"])
        count = 2 * hor + 2 * ver
        print(
            f"{mon['name']:<14}{f'{width:.1f} x {height:.1f}':>14}{hor:>12}{ver:>14}"
            f"{count:>7}{start:>7}{count / strip['leds_per_m']:>7.2f}"
        )
        start += count

    total = start
    budget_ma = psu["amps"] * 1000 * psu["headroom"]
    full_white_a = total * strip["ma_per_led_full_white"] / 1000
    print()
    print(f"LEDs gesamt:        {total} von {available} vorhanden ({available - total} uebrig)")
    if total > available:
        print("  -> ZU VIELE LEDs: Monitor weglassen oder weiteren Streifen besorgen")
    print(f"Volllast (weiss):   {full_white_a:.1f} A bei {strip['ma_per_led_full_white']} mA/LED")
    print(f"Netzteil-Budget:    {budget_ma / 1000:.1f} A ({psu['amps']} A x {psu['headroom']:.0%})")
    print(f"WLED-Stromlimit:    {budget_ma:.0f} mA eintragen")
    if total:
        print(f"Max. Helligkeit bei Vollweiss: ca. {min(1, budget_ma / 1000 / full_white_a):.0%}")


if __name__ == "__main__":
    main()
