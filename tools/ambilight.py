"""PC-Seite des Ambilights: greift jeden Monitor einzeln ab und schickt die Randfarben per DDP an WLED.

Jeder Monitor speist nur seine eigene LED-Schleife. Erfasst wird ueber DXGI Desktop Duplication
(dxcam), dieselbe Technik wie bei OBS - kein Eingriff in Spiele. Bedienung und Live-Vorschau laufen
ueber eine Webseite, erreichbar am PC und vom Handy im selben Heimnetz.

Aufruf:
    python tools/ambilight.py             starten, Oberflaeche unter http://localhost:8765
    python tools/ambilight.py --dry-run   nichts an WLED senden (nur Vorschau)
    python tools/ambilight.py --local     Oberflaeche nur auf diesem PC, nicht im Heimnetz
    python tools/ambilight.py --list      Monitore mit Desktop-Position anzeigen
"""

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import dxcam
import numpy as np

from capture import CaptureProcess, open_outputs
from certs import ensure_certs
from foreground import snapshot as foreground_snapshot
from foreground import taskbar_insets
from web import serve

ROOT = Path(__file__).resolve().parent.parent
DDP_PORT = 4048
DDP_MAX_DATA = 1440  # WLED nimmt pro DDP-Paket hoechstens 480 RGB-LEDs an
MODES = ("ambilight", "color", "off")
# "Weiss reduzieren" nach Kanalabstand (hoechster minus niedrigster Kanal) gestaffelt:
# bis NEUTRAL_CHROMA gilt eine Farbe als Weiss/Grau und bleibt unveraendert, ab TINTED_CHROMA voll
NEUTRAL_CHROMA = 16
TINTED_CHROMA = 64

# Szenen legen die Dynamik fest - wie schnell und wie grossflaechig das Licht reagiert. Die
# Farbwiedergabe (Bunte Pixel, Weiss reduzieren, Gamma) gilt fuer alle Szenen gleich.
SCENE_KEYS = ("smoothing", "depth", "fps")
SCENES = {
    # Schnelle Reaktion. 60 fps kosten dank direktem Bildzugriff kaum etwas
    # (gemessen: 4 Monitore bei 60 fps unter 10 % eines CPU-Kerns)
    "gaming": {"smoothing": 0.2, "depth": 15, "fps": 60},
    # Filme laufen mit 24 Bildern/s - mehr als 30 fps bringt dort nichts
    "film": {"smoothing": 0.45, "depth": 20, "fps": 30},
    # Ruhiges Raumlicht: grossflaechig, sanft, wenig Last
    "relax": {"smoothing": 0.85, "depth": 40, "fps": 20},
}
SCENE_NAMES = (*SCENES, "custom")
AUTO_SCENES = {"game": "gaming", "video": "film"}  # Erkennung im Vordergrund -> Szene
AUTO_DEBOUNCE = 2  # s, so lange muss eine Erkennung stabil sein, bevor die Szene wechselt

DEFAULT_SETTINGS = {
    "mode": "ambilight",
    "color": "#ff7a18",
    "brightness": 100,
    "smoothing": 0.5,
    "gamma": 2.2,
    "depth": 20,
    "vivid": 60,
    "white_cut": 50,
    "fps": 30,
    "monitors": {},
    "scene": "custom",
    "base_scene": "custom",  # zuletzt selbst gewaehlte Szene - dahin kehrt die Auto-Szene zurueck
    "custom": {"smoothing": 0.5, "depth": 20, "fps": 30},
    "auto_scene": False,
    "off_when_pc_off": True,
    "spread": False,  # "Aktiver Monitor fuer alle": alle LEDs zeigen den aktiven Monitor
    "spread_source": "auto",  # welcher Monitor: "auto" = der mit dem aktiven Fenster, sonst Name
    "hide_taskbar": True,  # Taskleiste nicht mitauswerten (ausser im Vollbild)
}
NUMBERS = {  # Schluessel: (Typ, Minimum, Maximum)
    "brightness": (int, 0, 100),
    "smoothing": (float, 0.0, 0.95),
    "gamma": (float, 1.0, 3.0),
    "depth": (int, 2, 50),
    "vivid": (int, 0, 100),
    "white_cut": (int, 0, 100),
    "fps": (int, 5, 60),
}
FLAGS = ("auto_scene", "off_when_pc_off", "spread", "hide_taskbar")


class DdpSender:
    def __init__(self, addr):
        self.addr = addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0

    def send(self, rgb_bytes):
        self.seq = self.seq % 15 + 1
        for offset in range(0, len(rgb_bytes), DDP_MAX_DATA):
            chunk = rgb_bytes[offset : offset + DDP_MAX_DATA]
            last = offset + DDP_MAX_DATA >= len(rgb_bytes)
            flags = 0x40 | (0x01 if last else 0x00)  # Version 1, Push erst beim letzten Paket
            header = bytes([flags, self.seq, 0x0B, 0x01]) + offset.to_bytes(4, "big") + len(chunk).to_bytes(2, "big")
            self.sock.sendto(header + chunk, self.addr)


def _number(key, value):
    kind, low, high = NUMBERS[key]
    return min(high, max(low, kind(value)))


def scene_values(settings, scene):
    """Dynamik-Werte einer Szene; "custom" sind die selbst eingestellten."""
    return dict(SCENES[scene]) if scene in SCENES else dict(settings["custom"])


def clean_settings(changes, current, monitor_names):
    """Uebernimmt gueltige Aenderungen; wirft ValueError bei unbrauchbaren Werten."""
    result = dict(current, monitors=dict(current["monitors"]), custom=dict(current["custom"]))
    if "mode" in changes:
        if changes["mode"] not in MODES:
            raise ValueError(f"unbekannter Modus: {changes['mode']}")
        result["mode"] = changes["mode"]
    if "color" in changes:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(changes["color"])):
            raise ValueError("Farbe muss #rrggbb sein")
        result["color"] = changes["color"].lower()
    for key in NUMBERS:
        if key in changes:
            result[key] = _number(key, changes[key])
    for key in FLAGS:
        if key in changes:
            result[key] = bool(changes[key])
    if "spread_source" in changes:
        if changes["spread_source"] != "auto" and changes["spread_source"] not in monitor_names:
            raise ValueError(f"unbekannter Monitor: {changes['spread_source']}")
        result["spread_source"] = changes["spread_source"]
    for key in ("scene", "base_scene"):
        if key in changes:
            if changes[key] not in SCENE_NAMES:
                raise ValueError(f"unbekannte Szene: {changes[key]}")
            result[key] = changes[key]
    if "custom" in changes:
        if not isinstance(changes["custom"], dict):
            raise ValueError("custom muss ein Objekt sein")
        for key in SCENE_KEYS:
            if key in changes["custom"]:
                result["custom"][key] = _number(key, changes["custom"][key])
    for name, enabled in changes.get("monitors", {}).items():
        if name not in monitor_names:
            raise ValueError(f"unbekannter Monitor: {name}")
        result["monitors"][name] = bool(enabled)
    return result


class Engine:
    def __init__(self, cfg, dry_run, settings_path):
        self.host = cfg["wled"]["host"]
        self.port = cfg["wled"].get("port", DDP_PORT)
        self.dry_run = dry_run
        self.settings_path = settings_path
        self.sender = None
        self.resolving = False
        self.next_resolve = 0.0
        self.status = "Dry run - es wird nichts an WLED gesendet" if dry_run else "verbinde ..."
        self.foreground = {"kind": None, "app": None, "monitor": None, "fullscreen": False}
        self.insets = {}  # Taskleiste je Anzeige: {(links, oben): (l, o, r, u)}
        self.active_monitor = None  # Monitor mit dem aktiven Fenster (fuer "Aktiver Monitor fuer alle")
        self.auto_reset = False

        names = [mon["name"] for mon in cfg["monitors"] if mon.get("enabled", True)]
        self.settings = dict(DEFAULT_SETTINGS, monitors={}, custom=dict(DEFAULT_SETTINGS["custom"]))
        if settings_path.exists():
            try:
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                saved["monitors"] = {k: v for k, v in saved.get("monitors", {}).items() if k in names}
                if "custom" not in saved:
                    # Einstellungen von vor den Szenen: die bisherigen Werte werden die eigene Szene
                    saved["custom"] = {k: saved[k] for k in SCENE_KEYS if k in saved}
                self.settings = clean_settings(saved, self.settings, names)
            except (ValueError, TypeError) as err:
                print(f"{settings_path.name} ignoriert: {err}")

        # Aufnahme laeuft im Unterprozess; liefert Name, LED-Zahl und Vorschau-Layout je Monitor
        self.capture = CaptureProcess(cfg)
        self.monitors = self.capture.layout

        self.lock = threading.Lock()
        self.output = np.zeros((sum(m["count"] for m in self.monitors), 3), dtype=np.float32)
        self.fps = 0.0
        self.wled = {"online": False, "url": f"http://{self.host}/"}
        # Live-Vorschau: jedes fertige Frame weckt die wartenden Stream-Verbindungen
        self.frame = threading.Condition()
        self.frame_id = 0
        self.frame_hex = ""

    def layout(self):
        return self.monitors

    def next_frame(self, last_id, timeout=2.0):
        """Wartet auf ein Frame nach last_id; liefert (id, Farben als Hex-String, 6 Zeichen je LED)."""
        with self.frame:
            self.frame.wait_for(lambda: self.frame_id != last_id, timeout)
            return self.frame_id, self.frame_hex

    def state(self):
        with self.lock:
            settings, fps, status, wled = dict(self.settings), self.fps, self.status, dict(self.wled)
            foreground = dict(self.foreground)
        if self.capture.restarting:
            status += " · Aufnahme startet neu ..."
        elif self.capture.restarts:
            status += f" · Aufnahme {self.capture.restarts}x neu gestartet"
        return {
            "settings": settings,
            "fps": round(fps, 1),
            "status": status,
            "wled": wled,
            "foreground": foreground,
            "scenes": SCENES,
            "missing": list(self.capture.missing),
            "spread_source": self._spread_source(settings),
        }

    def _save(self):
        """Einstellungen sichern; Aufruf nur mit gehaltenem Lock."""
        self.settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def _wled(self, path, data=None):
        """GET (ohne data) bzw. POST an die WLED-JSON-API."""
        ip = socket.gethostbyname(self.host)
        req = urllib.request.Request(
            f"http://{ip}{path}",
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.load(resp)

    def set_wled_fallback(self, dark):
        """Legt fest, was WLED zeigt, wenn der PC nicht sendet (PC aus, Standby, Programm beendet).

        WLED bleibt immer "an" - im Aus-Zustand zeigt WLED 16 auch die Live-Daten vom PC nicht
        (getestet). Stattdessen wird seine eigene Farbe gesetzt: bei dark Schwarz, sodass es ~2,5 s
        nach dem letzten Bild vom PC dunkel wird; sonst die im Programm gewaehlte Farbe.
        Ausserdem zeigt WLED die Live-Daten mit voller Helligkeit ("Force max brightness"), damit nur
        der Helligkeitsregler im Programm zaehlt.
        """
        if self.dry_run:
            return
        with self.lock:
            color = self.settings["color"]
        rgb = [0, 0, 0] if dark else [int(color[i : i + 2], 16) for i in (1, 3, 5)]
        try:
            self._wled("/json/cfg", {"if": {"live": {"maxbri": True}}})
            segments = self._wled("/json/state").get("seg", [])
            seg = [{"id": s["id"], "on": True, "fx": 0, "col": [rgb, [0, 0, 0], [0, 0, 0]]} for s in segments]
            self._wled("/json/state", {"on": True, "seg": seg})
            # Auch als Start-Preset: nach einem Neustart (Strom weg) kaeme WLED sonst mit seiner
            # Werksfarbe Orange-Gelb hoch und leuchtet, bis dieses Programm laeuft. Presets lassen sich
            # waehrend des Live-Stroms nicht per API speichern - daher die Datei direkt hochladen.
            preset = {"0": {}, "1": {"n": "Rueckfall (PC sendet nicht)", "on": True, "bri": 128, "seg": seg}}
            self._wled_upload("/presets.json", json.dumps(preset).encode())
            self._wled("/json/cfg", {"def": {"ps": 1, "on": True}})
        except (OSError, ValueError) as err:
            print(f"WLED-Rueckfallfarbe nicht gesetzt: {err}")

    def _wled_upload(self, name, data):
        """Datei ins WLED-Dateisystem schreiben (wie /edit im Browser)."""
        ip = socket.gethostbyname(self.host)
        boundary = "----ambilight" + os.urandom(8).hex()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"data\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/json\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"http://{ip}/upload", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=5):
            pass

    def watch_wled(self):
        """Fragt alle 5 s die WLED-API ab: erreichbar?, IP fuer den Link, konfigurierte LED-Zahl.

        Wird WLED (wieder) erreichbar und ist "Licht aus, wenn PC aus" an, bekommt es Schwarz als
        eigene Farbe (siehe set_wled_fallback).
        """
        while True:
            was_online = self.wled["online"]
            try:
                ip = socket.gethostbyname(self.host)
                with urllib.request.urlopen(f"http://{ip}/json/info", timeout=3) as resp:
                    info = json.load(resp)
                wled = {
                    "online": True,
                    "url": f"http://{ip}/",
                    "name": info.get("name"),
                    "version": info.get("ver"),
                    "leds": info.get("leds", {}).get("count"),
                }
            except (OSError, ValueError):
                wled = {"online": False, "url": self.wled["url"]}
            with self.lock:
                self.wled = wled
                off_when_pc_off = self.settings["off_when_pc_off"]
            if wled["online"] and not was_online and off_when_pc_off:
                self.set_wled_fallback(dark=True)
            time.sleep(5)

    def watch_foreground(self):
        """Auto-Szene: Spiele -> Gaming, Videos -> Film, sonst die zuletzt selbst gewaehlte Szene.

        Umgeschaltet wird nur, wenn sich die Erkennung aendert und AUTO_DEBOUNCE lang stabil ist
        (kurzes Alt+Tab schaltet nicht). Was man waehrend eines Spiels selbst nachstellt, bleibt so
        bis zum naechsten Wechsel.
        """
        applied = candidate = object()  # Platzhalter: noch nichts erkannt/angewandt
        since = 0.0
        while True:
            try:
                snap = foreground_snapshot()
                insets = taskbar_insets()
            except OSError:
                snap, insets = {"kind": None, "app": None, "monitor": None, "fullscreen": False}, {}
            kind = snap["kind"]
            now = time.monotonic()
            if kind != candidate:
                candidate, since = kind, now
            with self.lock:
                self.foreground = snap
                self.insets = insets
                # Aktiver Monitor: der mit dem aktiven Fenster; beim Klick auf den Desktop bleibt der letzte
                for m in self.monitors:
                    if snap["monitor"] == list(m["pos"]):
                        self.active_monitor = m["name"]
                if self.auto_reset:
                    applied, self.auto_reset = object(), False
                if self.settings["auto_scene"] and candidate != applied and now - since >= AUTO_DEBOUNCE:
                    applied = candidate
                    target = AUTO_SCENES.get(candidate, self.settings["base_scene"])
                    if target != self.settings["scene"]:
                        settings = dict(self.settings)
                        settings.update(scene_values(settings, target))
                        settings["scene"] = target
                        self.settings = settings
                        self._save()
            time.sleep(1)

    def update_settings(self, changes):
        names = {m["name"] for m in self.monitors}
        with self.lock:
            old = self.settings
            new = clean_settings(changes, old, names)
            if "scene" in changes:
                # Szene gewaehlt: ihre Werte uebernehmen und auf Ambilight schalten
                new.update(scene_values(new, new["scene"]))
                new["mode"] = "ambilight"
                new["base_scene"] = new["scene"]
            elif any(key in changes for key in SCENE_KEYS):
                # Von Hand nachgestellt: das ist ab jetzt die eigene Szene
                new["scene"] = new["base_scene"] = "custom"
                new["custom"] = {key: new[key] for key in SCENE_KEYS}
            if new["auto_scene"] and not old["auto_scene"]:
                self.auto_reset = True  # sofort fuer das bewerten, was gerade laeuft
            self.settings = new
            self._save()
        if new["off_when_pc_off"] != old["off_when_pc_off"]:
            self.set_wled_fallback(dark=new["off_when_pc_off"])

    def _connect(self):
        """Startet bei Bedarf die Namensaufloesung von WLED im Hintergrund.

        Eine fehlschlagende Aufloesung (.local ohne Geraet) blockiert mehrere Sekunden und
        darf deshalb nicht in der Bildschleife laufen.
        """
        if self.dry_run or self.sender or self.resolving or time.monotonic() < self.next_resolve:
            return
        self.resolving = True
        threading.Thread(target=self._resolve, daemon=True).start()

    def _resolve(self):
        try:
            addr = (socket.gethostbyname(self.host), self.port)
            self.sender = DdpSender(addr)
            self.status = f"sendet an {self.host} ({addr[0]})"
        except OSError:
            self.status = f"WLED '{self.host}' nicht gefunden - neuer Versuch in 5 s"
            self.next_resolve = time.monotonic() + 5
        finally:
            self.resolving = False

    def _spread_source(self, settings):
        """Monitor, dessen Bild bei "Aktiver Monitor fuer alle" alle LEDs bestimmt (sonst None)."""
        if not settings["spread"]:
            return None
        if settings["spread_source"] != "auto":
            return settings["spread_source"]
        return self.active_monitor or self.monitors[0]["name"]

    def _crops(self, settings):
        """Raender, die nicht ausgewertet werden: die Taskleiste - ausser auf der Anzeige, auf der
        gerade ein Vollbild-Programm laeuft (dort ist keine Taskleiste zu sehen)."""
        if not settings["hide_taskbar"]:
            return {}
        with self.lock:
            insets, fg = dict(self.insets), dict(self.foreground)
        crops = {}
        for m in self.monitors:
            inset = insets.get(tuple(m["pos"]))
            if inset and any(inset) and not (fg["fullscreen"] and fg["monitor"] == list(m["pos"])):
                crops[m["name"]] = tuple(inset)
        return crops

    def _target(self, settings, captured):
        """Sollfarben aller LEDs fuer den aktuellen Modus; abgeschaltete Monitore bleiben schwarz."""
        active = [m["name"] for m in self.monitors if settings["monitors"].get(m["name"], True)]
        if settings["mode"] == "ambilight":
            frame = self.capture.grab({
                "depth": settings["depth"],
                "vivid": settings["vivid"],
                "active": active,
                "crops": self._crops(settings),
                "spread": self._spread_source(settings),
            })
            if frame is not None:
                captured[:] = frame
            # Waehrend die Aufnahme neu startet, bleiben die letzten Farben stehen
            current = captured.copy()
            # Weissanteil (niedrigster Kanal) bei eingefaerbten Farben abziehen: Hellgruen wird gruen,
            # neutrales Weiss bleibt weiss. Nur im Ambilight-Modus.
            lowest = current.min(axis=1, keepdims=True)
            chroma = current.max(axis=1, keepdims=True) - lowest
            tint = np.clip((chroma - NEUTRAL_CHROMA) / (TINTED_CHROMA - NEUTRAL_CHROMA), 0, 1)
            current -= lowest * tint * settings["white_cut"] / 100
        elif settings["mode"] == "color":
            rgb = [int(settings["color"][i : i + 2], 16) for i in (1, 3, 5)]
            current = np.tile(np.array(rgb, dtype=np.float32), (len(captured), 1))
        else:
            return np.zeros_like(captured)
        start = 0
        for m in self.monitors:
            if m["name"] not in active:
                current[start : start + m["count"]] = 0
            start += m["count"]
        return current

    def run(self):
        smoothed = np.zeros_like(self.output)
        captured = np.zeros_like(self.output)
        frames, window_start = 0, time.perf_counter()
        while True:
            started = time.perf_counter()
            with self.lock:
                settings = self.settings
            self._connect()

            current = self._target(settings, captured)
            # Glaettung gegen Flackern: Anteil der alten Farbe, der pro Frame erhalten bleibt
            smoothed += (current - smoothed) * (1 - settings["smoothing"])
            output = np.clip(smoothed * settings["brightness"] / 100, 0, 255)
            if self.sender:
                # Bildschirmwerte sind gamma-kodiert, LEDs leuchten linear: ohne Korrektur glimmt
                # "Schwarz" (Werte um 20) sichtbar. Die Vorschau zeigt weiter die unkorrigierten Farben.
                led = 255 * (output / 255) ** settings["gamma"]
                self.sender.send(np.round(led).astype(np.uint8).tobytes())

            frames += 1
            elapsed = time.perf_counter() - window_start
            with self.lock:
                self.output = output
                if elapsed >= 1:
                    self.fps, frames, window_start = frames / elapsed, 0, time.perf_counter()
            frame_hex = output.astype(np.uint8).tobytes().hex()
            with self.frame:
                self.frame_id, self.frame_hex = self.frame_id + 1, frame_hex
                self.frame.notify_all()
            time.sleep(max(0.0, 1 / settings["fps"] - (time.perf_counter() - started)))


def lan_ip():
    """IP dieses PCs im Heimnetz (fuer den Handy-Link). Der UDP-"connect" verschickt nichts."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def phone_host(ip):
    """Name, unter dem das Handy den PC erreicht: <pc>.fritz.box, wenn die Fritz!Box ihn kennt
    (bleibt bei IP-Wechsel gueltig), sonst die IP."""
    name = f"{socket.gethostname().lower()}.fritz.box"
    try:
        return name if socket.gethostbyname(name) == ip else ip
    except OSError:
        return ip


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=ROOT / "monitors.json", type=Path)
    parser.add_argument("--list", action="store_true", help="Monitore anzeigen und beenden")
    parser.add_argument("--dry-run", action="store_true", help="nichts an WLED senden, nur Vorschau")
    parser.add_argument("--port", default=8765, type=int, help="Port der Weboberflaeche")
    parser.add_argument("--local", action="store_true", help="Oberflaeche nur auf diesem PC, nicht im Heimnetz")
    parser.add_argument("--settings", default=ROOT / "settings.json", type=Path, help="Datei fuer die Einstellungen")
    args = parser.parse_args()

    try:
        if args.list:
            print(dxcam.device_info())
            for idx, (pos, cam) in enumerate(open_outputs().items()):
                print(f"Anzeige {idx}: Desktop-Position {list(pos)}, {cam.width}x{cam.height}")
            return

        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        engine = Engine(cfg, args.dry_run, args.settings)
        threading.Thread(target=engine.run, daemon=True).start()
        threading.Thread(target=engine.watch_wled, daemon=True).start()
        threading.Thread(target=engine.watch_foreground, daemon=True).start()
        print(f"Oberflaeche: http://localhost:{args.port}  (Strg+C beendet)")
        if args.local:
            serve(engine, "127.0.0.1", args.port, ROOT / "web")
        else:
            # Fuers Handy zusaetzlich HTTPS (Port + 1) mit eigener CA - noetig, damit Chrome die
            # Seite als App installiert
            ip = lan_ip()
            host = phone_host(ip)
            names = list(dict.fromkeys([host, socket.gethostname().lower(), "localhost"]))
            names = [n for n in names if not n.replace(".", "").isdigit()]
            ca, cert, key = ensure_certs(ROOT / "certs", names, list(dict.fromkeys([ip, "127.0.0.1"])))
            https_port = args.port + 1
            phone = {"app_url": f"https://{host}:{https_port}/", "ca_url": f"http://{host}:{args.port}/ca.crt"}
            print(f"Vom Handy (gleiches WLAN): {phone['app_url']}  - einmalig vorher {phone['ca_url']} installieren")
            serve(engine, "0.0.0.0", args.port, ROOT / "web", phone, https_port, (cert, key), ca)
    except KeyboardInterrupt:
        exit_code = 0
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    else:
        exit_code = 0
    # dxcam/comtypes stuerzen beim normalen Interpreter-Ende ab, daher hart beenden
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
