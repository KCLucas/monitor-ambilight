# monitor-ambilight

Ambient-Licht hinter meinen Monitoren: adressierbare LED-Streifen auf der Rückseite, gesteuert von einem ESP32.

Ziel: mindestens einmal komplett um den Main-Monitor, am besten je einmal um alle 4 Monitore.

## Hardware

| Teil | Daten | Menge |
|---|---|---|
| BTF-LIGHTING WS2812 ECO (B088BDLMH2) | 5 V, 60 LEDs/m, 5 m, 300 LEDs, RGB, laut Angebotsseite IP67 | 2 (= 10 m, 600 LEDs) |
| BTF-LIGHTING Netzteil (B0FY63B939) | 5 V, 10 A, 50 W | 1 |
| ESP32 Dev-Board | vorhanden | 1 |

Noch zu besorgen (Kleinteile):

- Elektrolytkondensator 1000 µF / ≥ 6,3 V, direkt am Netzteil-Ausgang zwischen 5 V und GND
- 4× Widerstand 330–470 Ω, je einer in jeder Datenleitung
- Pegelwandler 74AHCT125 (3,3 V → 5 V) – empfohlen, bei kurzen Datenleitungen oft auch ohne lauffähig
- Litze 0,75–1,0 mm² für 5 V/GND, dünne Litze für Daten
- Eck-Verbinder oder Lötzeug für die 4 Ecken pro Monitor

## Entscheidungen

- **ESP32 statt Arduino.** Ein Arduino Uno hat 2 KB RAM; schon 600 LEDs brauchen 1,8 KB nur für den Farbpuffer. Der ESP32 schafft das locker und bringt WLAN mit.
- **WLED als Firmware**, kein eigener Code. WLED kann mehrere LED-Ausgänge, Segmente, Effekte, Strombegrenzung und Live-Daten vom PC.
- **Ein Datenausgang pro Monitor** (4 GPIOs). Jeder Monitor ist dann ein eigenes Segment, die Datenwege bleiben kurz und ein Defekt legt nicht alles lahm.
- **Helligkeit per Software begrenzen.** Das Netzteil reicht nicht für Vollweiß auf allen LEDs (siehe Strombudget), für Ambient-Licht aber problemlos.

## LED-Aufteilung und Strombudget

`monitors.json` beschreibt Streifen, Netzteil und Monitore; `tools/led_layout.py` rechnet daraus LEDs pro Kante, Startindex pro Monitor und das Stromlimit aus.

```
python tools/led_layout.py
```

Mit den **Platzhaltern** (4× 27" 16:9, Streifen 2 cm eingerückt):

| | LEDs | Länge |
|---|---|---|
| pro Monitor | 102 (33 oben/unten, 18 links/rechts) | 1,70 m |
| alle 4 | 408 von 600 | 6,80 m von 10 m |

- Vollweiß auf 408 LEDs wären ca. 24,5 A (gerechnet mit 60 mA/LED – die ECO-Variante liegt vermutlich darunter, nachmessen).
- Netzteil-Budget: 8,5 A (10 A mit 15 % Reserve) → in WLED als Stromlimit **8500 mA** eintragen.
- Das entspricht ca. 35 % Helligkeit bei Vollweiß; bei farbigem Bildinhalt deutlich mehr.

## Verkabelung

```
Netzteil 5 V ──┬── 1000 µF ── GND
               ├── ESP32 5V/VIN          GND ── ESP32 GND (gemeinsame Masse!)
               ├── Monitor 1: 5 V + GND
               ├── Monitor 2: 5 V + GND
               ├── Monitor 3: 5 V + GND
               └── Monitor 4: 5 V + GND   (sternförmig, jede Schleife eigene Zuleitung)

ESP32 GPIO16 ── [Pegelwandler] ── 330 Ω ── DIN Monitor 1
ESP32 GPIO17 ── [Pegelwandler] ── 330 Ω ── DIN Monitor 2
ESP32 GPIO18 ── [Pegelwandler] ── 330 Ω ── DIN Monitor 3
ESP32 GPIO19 ── [Pegelwandler] ── 330 Ω ── DIN Monitor 4
```

- Jede Monitor-Schleife bekommt 5 V und GND direkt vom Netzteil, nicht durchgeschleift vom vorherigen Monitor.
- Wenn das Ende einer Schleife sichtbar rötlicher/dunkler ist als der Anfang: 5 V und GND zusätzlich am Schleifenende einspeisen.
- Pfeilrichtung auf dem Streifen beachten (Daten laufen von DIN nach DOUT).
- ESP32 nie gleichzeitig über USB **und** das 5 V-Netzteil versorgen, solange nicht klar ist, dass das Board das verträgt – zum Flashen das Netzteil abziehen.
- GPIO16/17 sind auf WROVER-Boards (mit PSRAM) belegt; dann andere Pins nehmen.
- Montage: Die Silikonhülle (IP67) klebt schlecht und lässt sich in den Ecken nicht knicken. Pro Monitor in 4 Stücke schneiden (nur an den Schnittmarken) und die Ecken verbinden.

## Software-Kette

1. **WLED** auf den ESP32 flashen (https://install.wled.me), ins WLAN bringen.
2. In WLED unter *LED Preferences*: 4 Ausgänge (WS281x, GRB) mit den LED-Zahlen aus `led_layout.py`, Stromlimit 8500 mA.
3. Pro Monitor ein Segment anlegen. Damit funktionieren schon Farben und Effekte (Stufe 1: reines Ambient-Licht).
4. Stufe 2, Bildschirm-Farben folgen (Ambilight): Auf dem PC ein Capture-Programm wie HyperHDR oder Hyperion, das die Bildränder abgreift und per Netzwerk an WLED schickt.

## Offene Punkte

- [ ] Echte Monitorgrößen und Anordnung in `monitors.json` eintragen
- [ ] Prüfen, welche Variante des Streifens geliefert wurde (IP-Schutzart, tatsächlich 5 m / 300 LEDs)
- [ ] Welches ESP32-Board genau (WROOM/WROVER, Pinbelegung)?
- [ ] Ambilight über 4 Monitore: klären und testen, ob HyperHDR/Hyperion unter Windows alle 4 Displays gleichzeitig abgreifen kann – noch nicht verifiziert
- [ ] Stromaufnahme der ECO-LEDs nachmessen und `ma_per_led_full_white` anpassen
