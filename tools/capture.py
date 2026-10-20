"""Bildschirmaufnahme der Monitorraender, ausgelagert in einen eigenen Prozess.

dxcam/comtypes stuerzen gelegentlich mit Access Violation ab - beim Aufraeumen von Kameras und
wenn Windows die Desktop-Duplizierung entzieht (Vollbild-Spiel, UAC-Abfrage, Standby,
Aufloesungswechsel). Ein solcher Absturz laesst sich in Python nicht abfangen. Im Unterprozess
trifft er nur die Aufnahme; CaptureProcess startet sie dann neu, Webseite und LEDs laufen weiter.
"""

import ctypes
import multiprocessing as mp
import os
import threading
import time

import dxcam
import numpy as np

from led_layout import monitor_leds

START_TIMEOUT = 20  # s bis der Unterprozess seine Monitore gemeldet haben muss
FRAME_TIMEOUT = 2  # s ohne Antwort gilt die Aufnahme als haengend
RETRY_DELAY = 2  # s Pause vor einem erneuten Startversuch
RESCAN_INTERVAL = 30  # s: so oft wird neu gestartet, solange ein eingeschalteter Monitor fehlt
DEFAULT_SIZE = (2560, 1440)  # Annahme fuer Monitore, die beim Start nicht angeschlossen sind
EXIT_DISPLAY_CHANGED = 3  # Exit-Code des Unterprozesses bei Anzeigenwechsel
PBYTE = ctypes.POINTER(ctypes.c_ubyte)


class DisplayChanged(Exception):
    """Windows hat die Desktop-Duplizierung entzogen (Anzeige aus/an, Aufloesung, UAC, Vollbild)."""


def grab_sampled(cam, scale):
    """Neues Bild als RGB-float32 mit nur jedem scale-ten Pixel, oder None ohne neues Bild.

    Liest direkt aus dem gemappten Abbild statt ueber cam.grab(): grab() kopiert erst das ganze
    Bild (14,7 MB bei 2560x1440), gebraucht wird aber nur 1/64 der Pixel. Gemessen braucht das
    4-5x weniger CPU (4 Monitore bei 60 fps: 9 % statt 42 % eines Kerns). Nutzt dxcam-Interna,
    deshalb ist die dxcam-Version in requirements.txt festgelegt; fehlen sie, geht es ueber grab().

    Entzieht Windows die Duplizierung, wird DisplayChanged geworfen statt dxcams eigene Erholung zu
    nutzen - die stuerzt dabei regelmaessig mit Access Violation ab. Der Unterprozess startet dann
    einfach frisch.
    """
    try:
        duplicator = cam._duplicator
        if not duplicator.update_frame():
            raise DisplayChanged
        if not duplicator.updated:
            return None
        try:
            with cam._multithread_guard():
                width, height = cam._copy_region_to_stage(cam.region)
                rect = cam._stagesurf.map()
                try:
                    pitch = int(rect.Pitch)
                    buf = np.ctypeslib.as_array(ctypes.cast(rect.pBits, PBYTE), shape=(pitch * height,))
                    image = buf.reshape(height, pitch // 4, 4)  # BGRA, Zeilen ggf. breiter als das Bild
                    return image[::scale, :width:scale, 2::-1].astype(np.float32)
                finally:
                    cam._stagesurf.unmap()
        finally:
            cam._release_frame_if_late_release()
    except AttributeError:
        frame = cam.grab()
        return None if frame is None else frame[::scale, ::scale, 2::-1].astype(np.float32)


def open_outputs(device_idx=0):
    """Alle Anzeigen der Grafikkarte genau einmal oeffnen, nach Desktop-Position.

    dxcam stuerzt beim Aufraeumen ab, wenn dieselbe Anzeige mehrfach geoeffnet wurde.
    """
    cams = {}
    idx = 0
    while True:
        try:
            cam = dxcam.create(device_idx=device_idx, output_idx=idx, output_color="BGRA")
        except Exception:
            break
        rect = cam._output.desc.DesktopCoordinates
        cams[(rect.left, rect.top)] = cam
        idx += 1
    return cams


def led_positions(hor, ver):
    """Vorschau-Position (x, y von 0 bis 1) jeder LED, im Uhrzeigersinn ab oben links (von vorne)."""
    top = [((i + 0.5) / hor, 0.0) for i in range(hor)]
    right = [(1.0, (i + 0.5) / ver) for i in range(ver)]
    bottom = [((i + 0.5) / hor, 1.0) for i in reversed(range(hor))]
    left = [(0.0, (i + 0.5) / ver) for i in reversed(range(ver))]
    return top + right + bottom + left


def _segment_means(color_sums, weight_sums, bounds):
    """Gewichtete Mittel ueber die Abschnitte [bounds[i], bounds[i+1]) - alle LEDs einer Kante auf einmal."""
    colors = np.add.reduceat(color_sums, bounds[:-1], axis=0)
    weights = np.add.reduceat(weight_sums, bounds[:-1])
    return colors / weights[:, None]


def _bounds(length, parts):
    """Grenzen von parts gleich langen Abschnitten auf einer Strecke der Laenge length."""
    return np.round(np.linspace(0, length, parts + 1)).astype(int)


def color_weights(img, vivid):
    """Gewicht je Pixel nach Buntheit und das gewichtete Bild.

    Gewicht nach hoechstem minus niedrigstem Kanal: bei vivid=100 zaehlt ein voll gesaettigter
    Pixel 11-mal so viel wie ein grauer - sonst zieht schon etwas Weiss oder Grau im Bereich die
    gemittelte Farbe Richtung Weiss. (Kanalweise statt max(axis=2): numpy ist bei Reduktionen ueber
    die kurze Achse ~10x langsamer.)
    """
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    weight = 1 + (vivid / 10 / 255) * chroma
    return weight, img * weight[..., None]


def _integral(a):
    """Summentabelle: s[y, x] = Summe von a[:y, :x] - Rechtecksummen mit 4 Zugriffen."""
    s = np.zeros((a.shape[0] + 1, a.shape[1] + 1) + a.shape[2:], dtype=np.float64)
    s[1:, 1:] = a.cumsum(0).cumsum(1)
    return s


class Spread:
    """"Aktiver Monitor fuer alle": jede LED - egal an welchem Monitor - zeigt den Rand des
    aktiven Monitors, der in ihrer Richtung liegt.

    Von der LED wird eine Linie zur Mitte des aktiven Monitors gezogen; wo sie in den Monitor
    eintritt, wird wie beim normalen Ambilight ein Stueck Rand (Erfassungstiefe nach innen) gemittelt.
    Die LEDs des aktiven Monitors zeigen so ganz normal seinen Rand, alle anderen verlaengern sein
    Ambilight passend zur Richtung nach aussen.
    """

    def __init__(self, monitors):
        # Position jeder LED auf dem Desktop (in Layout-Reihenfolge aller Monitore)
        self.points = np.array([
            (m.pos[0] + px * m.size[0], m.pos[1] + py * m.size[1])
            for m in monitors
            for px, py in m.layout["leds"]
        ], dtype=np.float64)
        self._key = None

    def _boxes(self, src, h, w, depth):
        """Abtastfeld (x0, x1, y0, y1) je LED im (ggf. beschnittenen) Bild des aktiven Monitors."""
        ax, ay = src.pos
        aw, ah = src.size
        px, py = self.points[:, 0], self.points[:, 1]
        dx, dy = ax + aw / 2 - px, ay + ah / 2 - py
        # Eintrittspunkt der Linie LED -> Monitormitte (Slab-Verfahren); LEDs am aktiven Monitor
        # selbst liegen schon auf seinem Rand und treten bei t = 0 ein
        with np.errstate(divide="ignore", invalid="ignore"):
            tx = np.where(dx != 0, np.minimum((ax - px) / dx, (ax + aw - px) / dx), -np.inf)
            ty = np.where(dy != 0, np.minimum((ay - py) / dy, (ay + ah - py) / dy), -np.inf)
        t = np.clip(np.maximum(tx, ty), 0, 1)
        ex = np.clip((px + t * dx - ax) / aw, 0, 1)  # 0..1 im aktiven Monitor
        ey = np.clip((py + t * dy - ay) / ah, 0, 1)
        side = tx >= ty  # True: Eintritt ueber linke/rechte Kante, sonst oben/unten

        depth_x, depth_y = max(1, w * depth / 100), max(1, h * depth / 100)
        seg_x, seg_y = w / src.hor, h / src.ver  # Breite eines LED-Abschnitts am aktiven Monitor
        x0 = np.where(side, np.where(ex < 0.5, 0, w - depth_x), ex * w - seg_x / 2)
        x1 = np.where(side, np.where(ex < 0.5, depth_x, w), ex * w + seg_x / 2)
        y0 = np.where(side, ey * h - seg_y / 2, np.where(ey < 0.5, 0, h - depth_y))
        y1 = np.where(side, ey * h + seg_y / 2, np.where(ey < 0.5, depth_y, h))
        x0 = np.clip(np.floor(x0), 0, w - 1).astype(int)
        y0 = np.clip(np.floor(y0), 0, h - 1).astype(int)
        x1 = np.clip(np.ceil(x1), x0 + 1, w).astype(int)
        y1 = np.clip(np.ceil(y1), y0 + 1, h).astype(int)
        return x0, x1, y0, y1

    def colors(self, src, img, depth, vivid):
        """Farben aller LEDs aller Monitore (in Layout-Reihenfolge) aus dem Bild des aktiven Monitors."""
        h, w = img.shape[:2]
        key = (src.name, h, w, depth)
        if self._key != key:
            self._key = key
            self.x0, self.x1, self.y0, self.y1 = self._boxes(src, h, w, depth)
        weight, weighted = color_weights(img, vivid)
        sw, sc = _integral(weight), _integral(weighted)
        x0, x1, y0, y1 = self.x0, self.x1, self.y0, self.y1
        wsum = sw[y1, x1] - sw[y0, x1] - sw[y1, x0] + sw[y0, x0]
        csum = sc[y1, x1] - sc[y0, x1] - sc[y1, x0] + sc[y0, x0]
        return (csum / wsum[:, None]).astype(np.float32)


def order_leds(leds, hor, ver, start, clockwise):
    """Bringt die LEDs in die Reihenfolge, in der sie am Streifen liegen."""
    offset = {"top-left": 0, "top-right": hor, "bottom-right": hor + ver, "bottom-left": 2 * hor + ver}[start]
    rotated = leds[offset:] + leds[:offset]
    return rotated if clockwise else rotated[::-1]


class Monitor:
    """Ein Monitor mit seiner LED-Schleife. cam ist None, wenn die Anzeige gerade fehlt (aus,
    Tiefschlaf, abgesteckt) - dann bleiben seine LEDs schwarz, der Rest laeuft weiter."""

    def __init__(self, cfg, strip, inset_cm, capture, cam):
        self.name = cfg["name"]
        self.cam = cam
        _, _, self.hor, self.ver = monitor_leds(cfg, strip["leds_per_m"], inset_cm)
        self.count = 2 * self.hor + 2 * self.ver
        start = cfg.get("start_corner", "bottom-left")
        clockwise = cfg.get("clockwise", True)
        self.scale = capture["downscale"]
        self.pos = cfg["desktop_pos"]
        self.size = (cam.width, cam.height) if cam else DEFAULT_SIZE
        # Reihenfolge am Streifen als Index in die Liste "im Uhrzeigersinn ab oben links"
        self.order = np.array(order_leds(list(range(self.count)), self.hor, self.ver, start, clockwise))
        self.small = None  # letztes Bild, verkleinert, RGB float32
        self.frame_id = 0
        self._key = None
        self.layout = {
            "name": self.name,
            "count": self.count,
            "pos": self.pos,
            "size": list(self.size),
            "missing": cam is None,
            "leds": order_leds(led_positions(self.hor, self.ver), self.hor, self.ver, start, clockwise),
        }
        self.colors = np.zeros((self.count, 3), dtype=np.float32)

    def grab(self):
        """Holt ein neues Bild, falls es eins gibt (fehlende Anzeige: nichts)."""
        if self.cam is None:
            return
        small = grab_sampled(self.cam, self.scale)
        if small is not None:
            self.small = small
            self.frame_id += 1

    def image(self, crop):
        """Letztes Bild ohne die Raender crop = (links, oben, rechts, unten) in echten Pixeln,
        z. B. ohne Taskleiste - oder None, solange es noch kein Bild gibt."""
        if self.small is None:
            return None
        h, w = self.small.shape[:2]
        left, top, right, bottom = (round(v / self.scale) for v in (crop or (0, 0, 0, 0)))
        return self.small[top : max(top + 1, h - bottom), left : max(left + 1, w - right)]

    def edge_colors(self, depth, vivid, crop):
        """Farbe je LED: gewichtetes Mittel ueber ihren Abschnitt des Randstreifens.

        Pro Kante wird der Streifen erst quer aufsummiert und dann in einem Schritt auf die LEDs
        aufgeteilt - ein paar numpy-Aufrufe statt einer Schleife ueber alle LEDs. Ohne neues Bild
        und bei gleichen Einstellungen bleiben die letzten Farben.
        """
        key = (self.frame_id, depth, vivid, crop)
        img = self.image(crop)
        if img is None or key == self._key:
            return self.colors
        self._key = key
        h, w = img.shape[:2]
        xs, ys = _bounds(w, self.hor), _bounds(h, self.ver)
        dy = max(1, round(h * depth / 100))  # Tiefe der Bereiche oben/unten
        dx = max(1, round(w * depth / 100))  # Tiefe der Bereiche links/rechts
        weight, weighted = color_weights(img, vivid)
        top = _segment_means(weighted[:dy].sum(0), weight[:dy].sum(0), xs)
        bottom = _segment_means(weighted[-dy:].sum(0), weight[-dy:].sum(0), xs)
        left = _segment_means(weighted[:, :dx].sum(1), weight[:, :dx].sum(1), ys)
        right = _segment_means(weighted[:, -dx:].sum(1), weight[:, -dx:].sum(1), ys)
        clockwise = np.concatenate([top, right, bottom[::-1], left[::-1]])
        self.colors = clockwise[self.order].astype(np.float32)
        return self.colors


def compute_colors(monitors, spread, req):
    """LED-Farben aller Monitore fuer eine Anfrage des Hauptprogramms.

    req: depth, vivid, active (eingeschaltete Monitore), crops ({Name: (l, o, r, u)} - z. B. ohne
    Taskleiste), spread (Name des aktiven Monitors fuer "Aktiver Monitor fuer alle" oder None).
    """
    active, crops = set(req["active"]), req["crops"]
    source = next((m for m in monitors if m.name == req["spread"]), None)
    for m in monitors:
        # Bei "Aktiver Monitor fuer alle" wird nur dessen Bild gebraucht - spart Rechenzeit
        if m is source or (source is None and m.name in active):
            m.grab()

    if source is not None:
        img = source.image(crops.get(source.name))
        if img is None:
            colors = np.zeros((sum(m.count for m in monitors), 3), dtype=np.float32)
        else:
            colors = spread.colors(source, img, req["depth"], req["vivid"])
    else:
        colors = np.concatenate([
            m.edge_colors(req["depth"], req["vivid"], crops.get(m.name))
            if m.name in active else np.zeros((m.count, 3), dtype=np.float32)
            for m in monitors
        ])
    start = 0
    for m in monitors:  # abgeschaltete Monitore bleiben schwarz
        if m.name not in active:
            colors[start : start + m.count] = 0
        start += m.count
    return colors.astype(np.float32)


def capture_worker(conn, cfg):
    """Laeuft im Unterprozess: meldet zuerst die Monitore, liefert dann pro Anfrage die Randfarben
    und die Namen der Monitore, deren Anzeige gerade fehlt."""
    exit_code = 0
    try:
        # Alle Kameras festhalten, auch ungenutzte: das Wegraeumen einer Kamera stuerzt ab
        cams = open_outputs()
        monitors = [
            Monitor(mon, cfg["strip"], cfg["inset_cm"], cfg["capture"], cams.get(tuple(mon["desktop_pos"])))
            for mon in cfg["monitors"]
            if mon.get("enabled", True)
        ]
        spread = Spread(monitors)
        missing = [m.name for m in monitors if m.cam is None]
        conn.send([m.layout for m in monitors])

        while True:
            conn.send((compute_colors(monitors, spread, conn.recv()), missing))
    except DisplayChanged:
        print("Anzeigen haben sich geaendert - Aufnahme startet neu", flush=True)
        exit_code = EXIT_DISPLAY_CHANGED
    except (EOFError, OSError, KeyboardInterrupt):
        pass  # Hauptprogramm beendet oder Verbindung weg
    finally:
        # dxcam/comtypes stuerzen beim normalen Interpreter-Ende ab, daher hart beenden
        os._exit(exit_code)


class CaptureProcess:
    """Steuert den Aufnahme-Unterprozess und startet ihn nach Absturz oder Haenger neu."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.conn = None
        self.restarts = 0
        self.restarting = False
        self.missing = []  # Monitore, deren Anzeige gerade fehlt
        self.started_at = 0.0
        self.layout = self._start()

    def _start(self):
        parent, child = mp.Pipe()
        proc = mp.Process(target=capture_worker, args=(child, self.cfg), daemon=True)
        proc.start()
        child.close()
        if not parent.poll(START_TIMEOUT):
            proc.kill()
            raise RuntimeError("Bildschirmaufnahme startet nicht")
        layout = parent.recv()
        self.proc, self.conn = proc, parent
        self.missing = [m["name"] for m in layout if m.get("missing")]
        self.started_at = time.monotonic()
        return layout

    def grab(self, req):
        """Randfarben aller Monitore (Anfrage siehe compute_colors), oder None solange die
        Aufnahme (neu) startet."""
        if self.conn is None:
            return None
        try:
            self.conn.send(req)
            if self.conn.poll(FRAME_TIMEOUT):
                colors, self.missing = self.conn.recv()
                # Fehlt ein eingeschalteter Monitor, regelmaessig neu starten, um ihn wieder
                # einzubinden, sobald er zurueck ist (dxcam erkennt neue Anzeigen nur beim Start)
                if set(self.missing) & set(req["active"]) and time.monotonic() - self.started_at > RESCAN_INTERVAL:
                    self._restart(planned=True)
                return colors
        except (EOFError, OSError):
            pass
        self._restart()
        return None

    def _restart(self, planned=False):
        """Raeumt den alten Prozess weg und startet im Hintergrund einen neuen."""
        if self.restarting:
            return
        self.restarting = True
        if not planned:
            self.restarts += 1
        if self.proc is not None:
            self.proc.kill()
        self.proc = self.conn = None

        def restart():
            if not planned:
                time.sleep(RETRY_DELAY)
            while True:
                try:
                    self._start()
                    break
                except (RuntimeError, OSError):
                    time.sleep(RETRY_DELAY)
            self.restarting = False

        threading.Thread(target=restart, daemon=True).start()
