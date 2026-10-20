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

- **ESP32 statt Arduino.** Jeden Monitor einzeln ansteuern ginge auch mit einem Arduino (4 Datenpins, FastLED). Aber ein Uno/Nano hat nur 2 KB RAM, und ~408 LEDs belegen davon schon 1,2 KB – das reicht knapp für feste Farben und einfache Effekte, nicht für mehr. Ohne WLAN braucht er außerdem dauerhaft USB zum PC, und über die serielle Leitung kommen bei 400 LEDs nur wenige Bilder pro Sekunde an – zu langsam, damit das Licht dem Bild folgt. Der ESP32 hat genug Speicher, WLAN, Web-Oberfläche/App und läuft mit WLED ohne eigenen Code.
- **WLED als Firmware**, kein eigener Code auf dem ESP32. WLED kann mehrere LED-Ausgänge, Segmente, Effekte, Strombegrenzung und Live-Daten vom PC (DDP).
- **Eigenes PC-Programm** für das Ambilight pro Monitor, mit lokaler Weboberfläche (siehe Software).
- **Ein Datenausgang pro Monitor** (4 GPIOs). Jeder Monitor ist dann ein eigenes Segment, die Datenwege bleiben kurz und ein Defekt legt nicht alles lahm.
- **Helligkeit per Software begrenzen.** Das Netzteil reicht nicht für Vollweiß auf allen LEDs (siehe Strombudget), für Ambient-Licht aber problemlos.

## LED-Aufteilung und Strombudget

`monitors.json` beschreibt Streifen, Netzteil und Monitore; `tools/led_layout.py` rechnet daraus LEDs pro Kante, Startindex pro Monitor und das Stromlimit aus.

```
python tools/led_layout.py
```

Anordnung: `links | main | rechts` in einer Reihe, `oben` über dem Main-Monitor, alle 2560×1440. Main ist ein MSI MAG271CQP (27", curved), die drei LG IPS QHD sind als 27" angenommen.

Ergebnis mit 4× 27" 16:9, Streifen 2 cm eingerückt:

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

## Software

Ziel: Jeder Monitor spiegelt **seinen eigenen** Bildinhalt in seine eigene LED-Schleife.

Fertige Programme können das mit 4 Monitoren nicht zuverlässig: SignalRGB greift laut Doku nur einen Monitor ab, HyperHDR kann mehrere Monitore nur zu einem Bild zusammenfassen (je nach Grafikkarte/Treiber), Prismatik nur über mehrere Instanzen. Deshalb gibt es ein eigenes kleines PC-Programm in `tools/`.

### ESP32 (WLED)

1. **WLED** auf den ESP32 flashen (https://install.wled.me), ins WLAN bringen, Hostname `wled-ambilight` (oder IP in `monitors.json` unter `wled.host` eintragen).
2. *LED Preferences*: 4 Ausgänge (WS281x, GRB) in der Reihenfolge aus `monitors.json` – main, links, rechts, oben – mit je der LED-Zahl aus `led_layout.py`. Stromlimit 8500 mA.
3. Ohne laufenden PC kann WLED selbst Farben/Effekte zeigen; sobald das PC-Programm sendet, übernimmt es automatisch (Realtime/DDP).

### PC-Programm mit Weboberfläche

Am einfachsten: **`start.bat`** doppelklicken. Beim ersten Mal richtet es die Python-Umgebung ein, danach startet es das Programm und öffnet den Browser. Fenster schließen oder Strg+C beendet es.

Von Hand in PowerShell (im Projektordner, mit `.\` davor):

```powershell
cd "H:\IoT Projects\monitor-ambilight"
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python tools\ambilight.py
```

Dann im Browser **http://localhost:8765** öffnen:

- Live-Vorschau aller 4 Monitore in ihrer echten Anordnung, mit den LED-Farben drumherum; Monitor antippen schaltet ihn an/aus
- Modus: **Ambilight** (folgt dem Bild), **Farbe**, **Aus**, dazu die Helligkeit
- Feste Farben mit einem Tipp (Palette + eigene Farbe), ohne vorher den Modus zu wechseln
- Szenen (siehe unten) und Auto-Szene
- Darstellung **„Jeder für sich“** (jeder Monitor speist seine eigenen LEDs) oder **„Aktiver für alle“**: alle LEDs zeigen den aktiven Monitor – „Auto“ nimmt den mit dem aktiven Fenster, oder ein fester Monitor. Von jeder LED wird eine Linie zur Mitte des aktiven Monitors gezogen; wo sie in ihn eintritt, wird sein Rand ausgewertet. Seine eigenen LEDs zeigen also ganz normal seinen Rand, die der anderen Monitore verlängern sein Ambilight passend zur Richtung nach außen.
- **Taskleiste ignorieren:** der Bereich der Taskleiste (aus dem Windows-Arbeitsbereich jeder Anzeige) wird nicht ausgewertet – außer auf der Anzeige, auf der gerade ein Vollbild-Programm läuft
- Feineinstellungen in zwei Gruppen: **Dynamik** (Glättung, Erfassungstiefe, Bilder pro Sekunde – gehört zur Szene) und **Farbe** (Bunte Pixel bevorzugen, Weiß reduzieren, Dunkle Töne absenken – gilt für alle Szenen)
- **Licht aus, wenn PC aus:** WLED bleibt „an“, bekommt aber Schwarz als eigene Farbe. Endet der Datenstrom vom PC (PC aus, Standby, Programm beendet), fällt WLED nach ~2,5 s auf diese Farbe zurück – also dunkel. (WLED auf „aus“ zu stellen funktioniert nicht: im Aus-Zustand zeigt WLED 16 auch die Live-Daten nicht an.) Ist der Schalter aus, wird die im Programm gewählte Farbe die Rückfallfarbe. Zusätzlich ist in WLED „Force max brightness“ für Live-Daten an, damit nur der Helligkeitsregler im Programm zählt.

**Szenen** legen nur die Dynamik fest:

| Szene | Bilder/s | Glättung | Tiefe | Gedanke |
|---|---|---|---|---|
| Gaming | 60 | 20 % | 15 % | schnelle Reaktion; kostet dank direktem Bildzugriff kaum etwas |
| Film | 30 | 45 % | 25 % | Filme haben 24 Bilder/s – mehr als 30 bringt dort nichts |
| Entspannt | 20 | 85 % | 40 % | ruhiges Raumlicht, wenig Last |
| Eigene | – | – | – | wer einen Dynamik-Regler verstellt, landet hier; die Werte bleiben gespeichert, auch wenn man zwischendurch Film oder Gaming wählt |

**Auto-Szene:** erkennt das Programm im Vordergrund (`tools/foreground.py`): Spiele (Programm in Steam-/Epic-/Xbox-/… Ordnern oder anderes Vollbild-Programm) → Gaming, Videoplayer bzw. Browser/Store-App im Vollbild → Film, sonst zurück auf die zuletzt selbst gewählte Szene. Umgeschaltet wird nur bei einem Wechsel, der 2 s stabil ist; was man währenddessen selbst nachstellt, bleibt.
- WLED-Status und Button zur WLED-Oberfläche des ESP32

**Vom Handy als App:** Chrome installiert Web-Apps nur von HTTPS-Seiten mit vertrauenswürdigem Zertifikat. Das Programm legt dafür beim Start eine eigene kleine Zertifizierungsstelle in `certs/` an (nicht im Repo, enthält private Schlüssel) und bietet die Seite zusätzlich per HTTPS auf Port 8766 an, unter `https://<pc>.fritz.box:8766` – der Fritz!Box-Name bleibt auch bei IP-Wechsel gültig. Der Button „Aufs Handy“ zeigt beide Schritte mit QR-Code:

1. Einmalig `http://<pc>.fritz.box:8765/ca.crt` laden und in den Android-Einstellungen als **CA-Zertifikat** installieren (Suche: „Zertifikat installieren“; Display-Sperre nötig).
2. `https://<pc>.fritz.box:8766` in Chrome öffnen → Menü → „App installieren“.

Firewall-Freigaben für Port 8765 und 8766 gibt es nur im privaten Netzwerkprofil. Jeder im WLAN kann das Licht steuern; mit `--local` ist die Seite nur am PC erreichbar (dann ohne HTTPS).

Die Bildschirmaufnahme läuft in einem eigenen Prozess (`tools/capture.py`): Entzieht Windows die Aufnahme (Monitor aus/an, Vollbild-Wechsel, UAC, Standby) oder stürzt sie ab, startet sie frisch, ohne dass Webseite oder LEDs ausfallen. dxcams eigene Erholung wird dabei umgangen, weil sie selbst abstürzt. Fehlt ein Monitor (aus oder im Tiefschlaf – DisplayPort-Monitore melden sich dann bei Windows ab), bleiben seine LEDs schwarz, die Vorschau zeigt „kein Signal“, und alle 30 s wird geprüft, ob er zurück ist.

Party-Modus (Knopf „Party“ oder `{"mode": "party"}`, zurück mit `{"party": false}`): Licht im Takt der Musik. `tools/party.py` hört per WASAPI-Loopback den Wave-Link-Kanal „Music“ mit (nur Musik, unabhängig von den Reglern), erkennt Bass, Beats und Tempo und malt Laser, die um jeden Monitor kreisen (Tempo nach BPM, Richtung kippt alle 8 Beats), einen Flash auf jeden Beat, Bass-Pumpen, weißes Funkeln bei Hi-Hats und alle 16 Beats eine neue Rave-Palette – ungeglättet mit mindestens 50 fps. Ohne Musik wabert es langsam. Die Musik wird nur mitgehört, solange der Modus an ist.

Hinweis-Licht für andere Programme (z. B. EDITH bei einem abgelaufenen Timer): `POST /api/alert` mit `{"color": "#ff0000", "style": "pulse", "seconds": 600}` (pulse = atmen, blink = einmal weich aufleuchten) liegt in voller Helligkeit über allem, auch wenn das Licht aus oder gedimmt ist; `{"off": true}` beendet es. Die Einstellungen bleiben dabei unberührt – danach blendet das Licht zurück zu dem, was vorher lief. Spätestens nach einer Stunde läuft es aus.

Einstellungen werden in `settings.json` gespeichert. Weitere Aufrufe:

- `start.bat --dry-run` – nichts an WLED senden, nur Vorschau (zum Testen ohne Hardware)
- `.\.venv\Scripts\python tools\ambilight.py --list` – Anzeigen mit Desktop-Position; muss zu `desktop_pos` in `monitors.json` passen

Wo der Streifen an jedem Monitor beginnt und in welche Richtung er läuft, steht in `monitors.json` (`start_corner`, `clockwise`, von vorne gesehen). Wenn das Licht nach dem Aufkleben „verdreht“ ist, nur diese beiden Werte anpassen.

### Beim Spielen

- Erfasst wird per DXGI Desktop Duplication, wie bei OBS – kein Eingriff ins Spiel, Anti-Cheat-unkritisch.
- Gemessen auf diesem PC (RX 9070 XT, 16 Threads, 4× 2560×1440), ganzes Programm: alle 4 Monitore bei 30 Bildern/s ~6 % eines CPU-Kerns, bei 60 Bildern/s ~22 % eines Kerns (1,4 % der gesamten CPU). Möglich durch zwei Optimierungen in `tools/capture.py`: Das Bild wird direkt aus dem gemappten Abbild gelesen, nur jedes 8. Pixel (statt das ganze 14,7-MB-Bild zu kopieren), und die LED-Farben werden pro Kante vektorisiert berechnet.
- Spiele am besten im **randlosen Fenstermodus**; echtes Exklusiv-Vollbild kann die Erfassung kurz unterbrechen.
- Mit HDR werden die Farben blasser. DRM-geschützte Videos (Netflix etc. im Browser) liefern ein schwarzes Bild.

## Offene Punkte

- [ ] Größe der drei LG-Monitore bestätigen (angenommen 27") oder Rückseiten ausmessen
- [ ] Prüfen, welche Variante des Streifens geliefert wurde (IP-Schutzart, tatsächlich 5 m / 300 LEDs)
- [ ] Welches ESP32-Board genau (WROOM/WROVER, Pinbelegung)?
- [ ] Erster Test mit echter Hardware: kommen die DDP-Daten an, stimmen `start_corner`/`clockwise`?
- [ ] Verhalten mit exklusivem Vollbild in deinen Spielen prüfen
- [ ] Stromaufnahme der ECO-LEDs nachmessen und `ma_per_led_full_white` anpassen
