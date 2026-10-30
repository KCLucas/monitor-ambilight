# Monitor Ambilight

Ambient-Licht für **mehrere Monitore**: Jeder Monitor hat seinen eigenen LED-Streifen auf der Rückseite, und jeder Streifen zeigt die Farben vom Rand **seines eigenen** Bildes. Ein kleines Python-Programm unter Windows greift die Monitore ab und schickt die Farben per DDP an einen ESP32 mit [WLED](https://kno.wled.ge/).

Bedient wird alles über eine Weboberfläche im Browser oder als App auf dem Handy. Dazu gibt es einen Party-Modus im Takt der Musik.

Fertige Programme können das mit vier Monitoren nicht zuverlässig. SignalRGB greift nur einen Monitor ab. HyperHDR fasst mehrere Monitore je nach Treiber zu einem Bild zusammen, Prismatik braucht mehrere Instanzen. Deshalb gibt es dieses Projekt.

## Funktionen

- **Ein Ambilight pro Monitor:** beliebig viele Monitore, jeder mit eigener LED-Schleife und eigenem WLED-Ausgang.
- **„Aktiver für alle“:** Auf Wunsch zeigen alle LEDs den aktiven Monitor, also den mit dem Fenster im Vordergrund oder einen festen. Die LEDs der anderen Monitore führen sein Licht passend zur Richtung nach außen fort.
- **Szenen:** Gaming, Film und Entspannt. Die **Auto-Szene** erkennt Spiele und Videos im Vollbild und schaltet selbst um.
- **Party-Modus:** Licht im Takt der Musik über die ganze Monitorwand, mit Laserfächer, Druckwellen, Kometen und mehr.
- **Hinweis-Licht für andere Programme:** zum Beispiel rot pulsieren, wenn ein Timer abläuft.
- **Weboberfläche** mit Live-Vorschau aller Monitore in ihrer echten Anordnung. Sie läuft auch als **Handy-App** (PWA über HTTPS im Heimnetz).
- **Licht aus, wenn der PC aus ist:** WLED fällt ohne Datenstrom nach ~2,5 s auf Schwarz zurück.
- **Sparsam:** Bei 30 Bildern/s braucht das Programm etwa 6 % eines CPU-Kerns für vier QHD-Monitore (siehe [Leistung](#leistung)).
- **Robust:** Die Aufnahme läuft in einem eigenen Prozess. Monitor aus, Vollbild-Wechsel, UAC oder Standby legen Webseite und LEDs nicht lahm.

## Voraussetzungen

- Windows 10 oder 11 (die Aufnahme nutzt DXGI Desktop Duplication)
- Python 3.11 oder neuer
- ESP32 mit WLED, ein Datenausgang pro Monitor
- adressierbare LED-Streifen (z. B. WS2812, 5 V) und ein passendes Netzteil, siehe [Hardware](#hardware)

## Schnellstart

1. **WLED** auf den ESP32 flashen (https://install.wled.me) und ins WLAN bringen. Als Hostname `wled-ambilight` eintragen oder seine IP in `monitors.json` unter `wled.host`.
2. **Monitore eintragen:** `monitors.json` an deine Monitore anpassen (siehe [Konfiguration](#konfiguration)). Die Positionen zeigt dir
   ```powershell
   .\.venv\Scripts\python tools\ambilight.py --list
   ```
3. **LED-Zahlen ausrechnen:** `python tools\led_layout.py` gibt LEDs pro Kante, den Startindex pro Monitor und das Stromlimit aus. Die Werte unter *LED Preferences* in WLED eintragen (ein Ausgang pro Monitor, in der Reihenfolge aus `monitors.json`).
4. **Starten:** `start.bat` doppelklicken. Beim ersten Mal richtet es die Python-Umgebung ein, danach öffnet sich **http://localhost:8765**.

Zum Ausprobieren ohne Hardware: `start.bat --dry-run` sendet nichts an WLED und zeigt nur die Vorschau.

## Konfiguration

`monitors.json` beschreibt Streifen, Netzteil, WLED und die Monitore. Pro Monitor:

| Feld | Bedeutung |
|---|---|
| `name` | frei wählbar, erscheint in der Oberfläche |
| `diagonal_inch`, `aspect` | Größe, daraus berechnet `led_layout.py` die LEDs pro Kante |
| `desktop_pos` | Position in den Windows-Anzeigeeinstellungen (`--list`). Kleine Abweichungen (bis 400 px) werden toleriert. |
| `start_corner`, `clockwise` | wo der Streifen beginnt und in welche Richtung er läuft, von vorne gesehen. Ist das Licht „verdreht“, nur diese beiden Werte ändern. |
| `enabled` | gehört der Monitor zum Aufbau? An und aus im Alltag geht über die Oberfläche. |

Die Einstellungen aus der Oberfläche landen in `settings.json` (nicht im Repo).

Weitere Optionen von `tools\ambilight.py`:

| Option | Wirkung |
|---|---|
| `--dry-run` | nichts an WLED senden, nur Vorschau |
| `--list` | Monitore mit Desktop-Position anzeigen und beenden |
| `--port 8765` | Port der Weboberfläche |
| `--local` | Oberfläche nur auf diesem PC, nicht im Heimnetz (dann ohne HTTPS) |
| `--config`, `--settings` | andere Dateien als `monitors.json` / `settings.json` |

Umgebungsvariablen:

| Variable | Wirkung |
|---|---|
| `AMBILIGHT_NO_BROWSER=1` | `start.bat` öffnet keinen Browser (z. B. im Autostart) |
| `AMBILIGHT_AUDIO` | Anfang des Namens des Wiedergabegeräts, das der Party-Modus mithört. Standard: Elgato Wave Link „Music“, ohne Treffer die Windows-Standardausgabe. |

## Szenen

Szenen legen nur die Dynamik fest. Die Farbregler (bunte Pixel bevorzugen, Weiß reduzieren, dunkle Töne absenken) gelten für alle Szenen.

| Szene | Bilder/s | Glättung | Tiefe | Gedanke |
|---|---|---|---|---|
| Gaming | 60 | 20 % | 15 % | schnelle Reaktion, kostet dank direktem Bildzugriff kaum etwas |
| Film | 30 | 45 % | 25 % | Filme haben 24 Bilder/s, mehr bringt dort nichts |
| Entspannt | 20 | 85 % | 40 % | ruhiges Raumlicht, wenig Last |
| Eigene | – | – | – | wer einen Regler verstellt, landet hier; die Werte bleiben gespeichert |

**Auto-Szene** (`tools/foreground.py`):

- Spiele (Programme in Steam-, Epic- oder Xbox-Ordnern und andere Vollbild-Programme) schalten auf Gaming.
- Videoplayer sowie Browser oder Store-Apps im Vollbild schalten auf Film.
- Sonst geht es zurück zur zuletzt selbst gewählten Szene.

Umgeschaltet wird erst, wenn ein Wechsel 2 s stabil ist.

Die **Taskleiste** wird nicht ausgewertet, außer auf der Anzeige, auf der gerade ein Vollbild-Programm läuft.

## Party-Modus

Knopf „Party“ in der Oberfläche oder `{"mode": "party"}` an die API. Zurück geht es mit `{"party": false}`.

`tools/party.py` hört die Musik per WASAPI-Loopback mit, nur solange der Modus an ist. Daraus erkennt es Bass, Kicks und Tempo und malt auf die ganze Monitorwand als eine Fläche: Jede LED sitzt an ihrer echten Position, Effekte laufen über Monitorgrenzen hinweg.

- **Zehn Muster:** Laserfächer, Druckwelle, Scanner-Balken, Equalizer, Lauflicht, Pingpong, Monitor-Hopping, Kometen, Balken aus der Mitte und Lichtregen. Je zwei bilden eine Szene, pro Song und alle 32 Beats gibt es eine neue, weich überblendet.
- **Die Stärke folgt dem Song:** Volle Party gibt es nur, wenn es laut ist und die Kicks regelmäßig im Takt kommen. Breaks und melodische Teile blenden auf ruhiges Farbatmen zurück. Kommt der Kick zurück, ist die Party sofort wieder da.
- **Farben:** pro Song eine Farbrichtung (Hauptfarbe und Nachbarton), weiche Wechsel, keine harten bunten Blitze. Ein anderes Programm kann sie vorgeben, z. B. aus dem Album-Cover (siehe API).

## API

Die Oberfläche nutzt dieselbe kleine HTTP-API, die auch anderen Programmen offensteht (`http://<pc>:8765`):

| Aufruf | Wirkung |
|---|---|
| `GET /api/state` | aktueller Zustand und Einstellungen |
| `GET /api/layout` | Monitore und LED-Positionen für die Vorschau |
| `GET /api/stream` | Live-Farben als Server-Sent Events |
| `POST /api/settings` | Einstellungen ändern, z. B. `{"mode": "color", "color": "#ff7a18", "brightness": 60}`. Mit `"for": 30` stellt das Programm nach 30 s selbst zurück. |
| `POST /api/alert` | Hinweis-Licht über allem, z. B. `{"color": "#ff0000", "style": "pulse", "seconds": 600}` mit `style` = `pulse`, `blink` oder `flash`. `{"off": true}` beendet es. Danach blendet das Licht zurück zu dem, was vorher lief. |
| `POST /api/party` | Farbrichtung für den Party-Modus: `{"colors": ["#ff0020", "#ff4126"]}` |

## Handy-App

Chrome installiert Web-Apps nur von HTTPS-Seiten mit vertrauenswürdigem Zertifikat. Das Programm legt dafür beim Start eine eigene kleine Zertifizierungsstelle in `certs/` an. Der Ordner ist nicht im Repo, er enthält private Schlüssel. Zusätzlich bietet es die Seite per HTTPS auf Port 8766 an. Der Knopf „Aufs Handy“ zeigt beide Schritte mit QR-Code:

1. Einmalig `http://<pc>:8765/ca.crt` laden und in Android als **CA-Zertifikat** installieren (Einstellungen, Suche „Zertifikat installieren“).
2. `https://<pc>:8766` in Chrome öffnen, dann im Menü „App installieren“.

Mit einer Fritz!Box nutzt das Programm den Namen `<pc>.fritz.box`, der auch bei IP-Wechseln gültig bleibt. Jeder im WLAN kann das Licht steuern. Mit `--local` ist die Seite nur am PC erreichbar.

## Hardware

Mein Aufbau: vier 27"-Monitore mit 2560×1440 (`links | main | rechts` in einer Reihe, `oben` über dem Main-Monitor).

| Teil | Daten | Menge |
|---|---|---|
| BTF-LIGHTING WS2812 ECO | 5 V, 60 LEDs/m, 5 m, 300 LEDs | 2 (= 10 m, 600 LEDs) |
| BTF-LIGHTING Netzteil | 5 V, 10 A, 50 W | 1 |
| ESP32 Dev-Board | mit WLED | 1 |
| Elektrolytkondensator | 1000 µF, ≥ 6,3 V, am Netzteil-Ausgang | 1 |
| Widerstand | 330–470 Ω, je einer pro Datenleitung | 4 |
| Pegelwandler 74AHCT125 | 3,3 V → 5 V, empfohlen | 1 |

**Warum ESP32 statt Arduino:** Ein Uno oder Nano hat nur 2 KB RAM, und 400 LEDs belegen davon schon 1,2 KB. Ohne WLAN bräuchte er dauerhaft USB, und seriell kommen bei 400 LEDs nur wenige Bilder pro Sekunde an. Der ESP32 mit WLED bringt WLAN, mehrere Ausgänge, Segmente, Strombegrenzung und DDP mit, ganz ohne eigenen Code.

### LEDs und Strom

`tools/led_layout.py` rechnet aus `monitors.json` die LEDs pro Kante und das Stromlimit. Mit vier 27"-Monitoren und 2 cm eingerücktem Streifen:

| | LEDs | Länge |
|---|---|---|
| pro Monitor | 102 (33 oben/unten, 18 links/rechts) | 1,70 m |
| alle 4 | 408 von 600 | 6,80 m von 10 m |

Vollweiß auf 408 LEDs wären ca. 24,5 A. Das Netzteil-Budget liegt bei 8,5 A (10 A minus 15 % Reserve), deshalb in WLED **8500 mA** als Stromlimit eintragen. Für Ambient-Licht reicht das problemlos.

### Verkabelung

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

- Jede Schleife bekommt 5 V und GND direkt vom Netzteil. Wird das Ende rötlich oder dunkler, zusätzlich am Schleifenende einspeisen.
- Pfeilrichtung auf dem Streifen beachten: Die Daten laufen von DIN nach DOUT.
- Den ESP32 nie gleichzeitig über USB und das Netzteil versorgen. Zum Flashen das Netzteil abziehen.
- GPIO16 und 17 sind auf WROVER-Boards (mit PSRAM) belegt, dann andere Pins nehmen.
- Silikonhüllen (IP67) kleben schlecht und lassen sich in den Ecken nicht knicken. Pro Monitor in 4 Stücke schneiden (nur an den Schnittmarken) und die Ecken verbinden.

## Leistung

Gemessen mit RX 9070 XT, 16 Threads und 4× 2560×1440 für das ganze Programm:

| Bilder/s | Last |
|---|---|
| 30 | ~6 % eines CPU-Kerns |
| 60 | ~22 % eines Kerns (1,4 % der gesamten CPU) |

`tools/capture.py` liest das Bild direkt aus dem gemappten Abbild, und zwar nur jedes 8. Pixel, statt 14,7 MB pro Bild zu kopieren. Die LED-Farben rechnet es pro Kante vektorisiert.

- Die Erfassung läuft wie bei OBS. Es gibt keinen Eingriff ins Spiel, unkritisch für Anti-Cheat.
- Spiele am besten im **randlosen Fenstermodus** laufen lassen. Echtes Exklusiv-Vollbild kann die Erfassung kurz unterbrechen.
- Mit HDR werden die Farben blasser. DRM-geschützte Videos (z. B. Netflix im Browser) liefern ein schwarzes Bild.
- Fehlt ein Monitor (aus oder im Tiefschlaf), bleiben seine LEDs schwarz. Die Vorschau zeigt „kein Signal“, und alle 30 s prüft das Programm, ob er zurück ist.

## Autostart

Eine Verknüpfung auf `start.bat` in `shell:startup` legen: minimiert starten und `AMBILIGHT_NO_BROWSER=1` setzen. Mit „Licht aus, wenn der PC aus ist“ wird es nach dem Herunterfahren von selbst dunkel.

## Lizenz

[MIT](LICENSE)
