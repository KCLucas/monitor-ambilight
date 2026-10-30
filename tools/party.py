"""Party-Modus: Licht im Takt der Musik - wie auf einem Rave, aber ruhig in den Farben.

Hoert per WASAPI-Loopback den Wave-Link-Kanal "Music" mit (nur die Musik, ohne Voice Chat und EDITH, und
unabhaengig von den Lautstaerke-Reglern). Daraus: Bass, Mitten, Hoehen, Beats und Tempo.

Farben: pro Song EINE Farbrichtung (Hauptfarbe + verwandter Nachbarton) - von EDITH aus dem Spotify-Cover
(POST /api/party), sonst eine zufaellige Familie, die nach einer Songpause wechselt. Wechsel blenden weich ueber.
Kein buntes Hin und Her und keine harten weissen Blitze (Epilepsie) - Bewegung kommt vom Blinken im Takt.

Auf der ganzen Monitorwand als eine Flaeche: Laserfaecher um die Mitte, Druckwelle pro Beat, Balken alle 4 Beats,
die Wand blinkt auf jeden Beat in der Hauptfarbe, Grundton pumpt mit dem Bass, Hi-Hats funkeln hell getoent.
"""

import colorsys
import os
import threading
import time
from collections import deque

import numpy as np

# Farbfamilien ohne Spotify: (Hauptfarbe, Nachbarton)
FAMILIES = [((255, 0, 200), (150, 0, 255)),   # Magenta / Lila
            ((0, 220, 255), (0, 90, 255)),    # Cyan / Blau
            ((255, 20, 30), (255, 90, 0)),    # Rot / Orange
            ((0, 255, 110), (0, 210, 200)),   # Gruen / Tuerkis
            ((130, 0, 255), (60, 40, 255)),   # UV / Indigo
            ((255, 0, 90), (255, 40, 160))]   # Pink / Rosa
FADE = 2.5             # s - so weich wechselt die Farbrichtung
SONG_BREAK = 1.2       # s Stille = neuer Song (ohne Spotify: neue Farbfamilie)
BEAT_GAP = 0.23        # s - schneller als ~260 BPM gibt es keine Beats
BEAT_FACTOR = 1.25     # Bass muss so viel ueber dem Schnitt der letzten ~0,8 s liegen (an echter Musik abgestimmt)
SILENCE = 0.6          # s ohne Audio = Pause


class AudioListener:
    """Analysiert die Musik im Hintergrund; levels() liefert den letzten Stand (Werte 0..1)."""

    def __init__(self, device_hint=None):
        # Welches Wiedergabegeraet mitgehoert wird (Anfang des Namens), z. B. AMBILIGHT_AUDIO="Lautsprecher".
        # Ohne Treffer: die Standardausgabe von Windows.
        self.device_hint = device_hint or os.environ.get("AMBILIGHT_AUDIO", "Music (Elgato")
        self.lock = threading.Lock()
        self.state = {"bass": 0.0, "mid": 0.0, "high": 0.0, "beats": 0, "last_beat": 0.0, "strength": 0.0,
                      "bpm": 0.0, "heard": 0.0, "energy": 0.0, "drive": 0.0}
        self._loudest = 1e-6  # lauteste Stelle der letzten ~40 s - Massstab fuer "energy"
        self.error = None
        self._stop = threading.Event()
        self._history = deque(maxlen=75)        # Bass der letzten ~0,8 s (93 Bloecke/s)
        self._intervals = deque(maxlen=12)
        self._peaks = {"bass": 1e-6, "mid": 1e-6, "high": 1e-6}  # automatische Aussteuerung je Band
        self._window = np.zeros(2048, np.float32)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def levels(self):
        with self.lock:
            return dict(self.state)

    def _run(self):
        try:
            import pyaudiowpatch as pa
        except ImportError:
            self.error = "PyAudioWPatch fehlt (pip install PyAudioWPatch)"
            return
        audio = pa.PyAudio()
        try:
            device = next((d for d in audio.get_loopback_device_info_generator()
                           if d["name"].startswith(self.device_hint)), None)
            if device is None:
                device = audio.get_default_wasapi_loopback()  # Notfall: alles, was die Standardausgabe spielt
            rate, channels = int(device["defaultSampleRate"]), max(1, device["maxInputChannels"])
            freqs = np.fft.rfftfreq(len(self._window), 1 / rate)
            bands = {"bass": (freqs > 35) & (freqs < 150), "mid": (freqs > 300) & (freqs < 2000),
                     "high": (freqs > 4000) & (freqs < 12000)}
            hann = np.hanning(len(self._window)).astype(np.float32)

            def callback(data, frames, _time, _status):
                block = np.frombuffer(data, np.float32).reshape(-1, channels).mean(axis=1)
                self._analyse(block, hann, bands)
                return None, pa.paContinue

            stream = audio.open(format=pa.paFloat32, channels=channels, rate=rate, input=True,
                                input_device_index=device["index"], frames_per_buffer=512, stream_callback=callback)
            while not self._stop.wait(0.2):
                pass
            stream.close()
        except Exception as err:  # Audio-Geraet weg o. ae. - der Party-Modus wabert dann nur
            self.error = str(err)
        finally:
            audio.terminate()

    def _analyse(self, block, hann, bands):
        now = time.monotonic()
        self._window = np.roll(self._window, -len(block))
        self._window[-len(block):] = block[-len(self._window):]
        spectrum = np.abs(np.fft.rfft(self._window * hann))
        raw = {name: float(spectrum[mask].mean()) for name, mask in bands.items()}
        levels = {}
        for name, value in raw.items():
            # Aussteuerung: Spitze faellt langsam, damit leise und laute Songs beide voll ausschlagen
            self._peaks[name] = max(value, self._peaks[name] * 0.9995)
            levels[name] = min(1.0, value / self._peaks[name]) if self._peaks[name] > 1e-4 else 0.0
        # Energie: wie laut es gerade ist, gemessen an den lauten Stellen des Songs (Massstab faellt nur langsam,
        # ~40 s Halbwertszeit) - Ausfaden und Breaks werden so wirklich leiser, statt nachgeregelt zu werden.
        # Weich: steigt in ~0,25 s, faellt in ~1,2 s.
        rms = float(np.sqrt(np.mean(block ** 2)))
        self._loudest = max(rms, self._loudest * 0.99981)
        target = min(1.0, rms / self._loudest) if self._loudest > 1e-5 else 0.0
        dt = len(block) / 48000
        bass = raw["bass"]
        average = (sum(self._history) / len(self._history)) if self._history else bass
        # Kick = schlagartiger Anstieg gegenueber den letzten ~50 ms - eine anschwellende Bass-Melodie zaehlt nicht
        rise = bass - min(list(self._history)[-5:], default=bass)
        self._history.append(bass)
        with self.lock:
            state = self.state
            state.update(levels, heard=now if self._peaks["bass"] > 1e-4 and np.abs(block).max() > 1e-4 else state["heard"])
            tau = 0.25 if target > state["energy"] else 1.2
            state["energy"] += (target - state["energy"]) * (1 - np.exp(-dt / tau))
            # Drive: kommen die Kicks regelmaessig im Takt? Nur dann volle Party - in melodischen Teilen ohne Kick
            # faellt er, und das Licht wird ruhig (auch wenn es laut ist)
            # Regelmaessigkeit der letzten 8 Kicks (halbe/doppelte Abstaende zaehlen mit - Off-Beats, ausgelassene
            # Kicks); bleiben sie laenger als 2 Takte aus, geht es gegen 0. Weich: ~1,5 s rauf, ~2 s runter.
            recent = list(self._intervals)[-8:]
            expected = float(np.median(recent)) if len(recent) >= 4 else 0.5
            gap = now - state["last_beat"] if state["last_beat"] else 99
            regular = float(np.mean([any(abs(iv - k * expected) < 0.2 * k * expected for k in (0.5, 1, 2))
                                     for iv in recent])) if len(recent) >= 4 else 0.0
            # Groove haelt durch: erst wenn der Kick laenger als ~1 Takt (4 Kicks) ausbleibt, gegen 0.
            # Rauf schnell (~0,8 s), runter langsam (~3 s) - einzelne verpasste Kicks machen keine Pause
            target_drive = 0.0 if gap > max(4 * expected, 1.2) else regular
            tau = 0.8 if target_drive > state["drive"] else 3.0
            state["drive"] += (target_drive - state["drive"]) * (1 - np.exp(-dt / tau))
            if (bass > average * BEAT_FACTOR and rise > average * 0.5 and levels["bass"] > 0.25
                    and gap > BEAT_GAP):
                if gap < 2:
                    self._intervals.append(gap)
                    state["bpm"] = 60 / float(np.median(self._intervals))
                if 0.25 <= gap <= 1.0:
                    # zwei Kicks im plausiblen Takt: der Beat ist (wieder) da - sofort, ohne Einblenden
                    # (gemessen an "Sparta - Fantasm Remix": vorher ~2 s zu spaet nach dem Break)
                    state["drive"] = max(state["drive"], 0.9)
                state["beats"] += 1
                state["last_beat"] = now
                state["strength"] = min(1.0, bass / max(average, 1e-6) / 2.5)


# Muster-Kombinationen ("Szenen"): pro Song und alle 32 Beats eine andere, weich ueberblendet
SCENES = [("fan", "waves"), ("scanner", "rise"), ("chase", "pingpong"), ("waves", "rise"), ("fan", "scanner"),
          ("chase", "waves"), ("pingpong", "rise"), ("hop", "comet"), ("center", "rain"), ("comet", "waves"),
          ("hop", "rain"), ("center", "fan"), ("comet", "scanner"), ("hop", "rise"), ("rain", "chase")]
SCENE_BEATS = 32
SCENE_FADE = 1.5   # Beats


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


class PartyEffect:
    """Malt auf die ganze Monitorwand als eine Flaeche: jede LED hat ihre echte Position (Monitor-Position +
    Lage am Rand), Effekte laufen ueber Monitorgrenzen hinweg - wo zwei Monitore aneinanderstossen, passt es.

    Wie stark es zugeht, folgt dem Song: nur wenn es laut ist UND die Kicks regelmaessig im Takt kommen, laeuft die
    volle Party. Leise Stellen (Ausfaden) und melodische Teile ohne Kick blenden weich aus, bis nur ruhiges
    Farbatmen bleibt; mit dem Drop kommen die Effekte zurueck - samt weissen Flashes."""

    def __init__(self, monitors):
        self.monitors = monitors  # Layout aus der Aufnahme: name, count, pos, size, leds (0..1 am Monitorrand)
        self.rng = np.random.default_rng()
        family = FAMILIES[self.rng.integers(len(FAMILIES))]
        self.colors = np.array(family, np.float32)   # aktuelle (Haupt, Nachbar)
        self.fade_from, self.fade_to, self.fade_start = self.colors.copy(), self.colors.copy(), 0.0
        self.from_spotify = 0.0       # wann EDITH zuletzt Farben aus dem Cover geschickt hat
        self.silent_since = None
        xy, ring, owner = [], [], []
        for m in monitors:
            leds = np.array(m.get("leds") or [[i / max(1, m["count"]), 0.5] for i in range(m["count"])], np.float32)
            leds = leds[: m["count"]]
            pos, size = np.array(m.get("pos", [0, 0]), np.float32), np.array(m.get("size", [1, 1]), np.float32)
            xy.append(pos + leds * size)
            # Lage rund um den eigenen Monitor (0..1, oben Mitte = 0, im Uhrzeigersinn) - fuer das Lauflicht
            ring.append((np.arctan2(leds[:, 0] - 0.5, 0.5 - leds[:, 1]) / (2 * np.pi)) % 1)
            owner.append(np.full(m["count"], len(owner)))
        xy = np.concatenate(xy) if xy else np.zeros((0, 2), np.float32)
        low, high = xy.min(axis=0), xy.max(axis=0)
        center, half = (low + high) / 2, np.maximum((high - low) / 2, 1)
        rel = (xy - center) / half.max()                     # Mitte der Wand = 0, Rand ~ 1
        self.angle = np.arctan2(rel[:, 1], rel[:, 0])        # Laserfaecher
        self.radius = np.hypot(rel[:, 0], rel[:, 1])          # Druckwelle
        self.x = (xy[:, 0] - low[0]) / max(1, high[0] - low[0])  # 0 = links aussen, 1 = rechts aussen
        self.y = (xy[:, 1] - low[1]) / max(1, high[1] - low[1])  # 0 = ganz oben, 1 = ganz unten
        self.ring = np.concatenate(ring) if ring else np.zeros(0)
        self.owner = np.concatenate(owner) if owner else np.zeros(0)  # zu welchem Monitor die LED gehoert
        # Monitore von links nach rechts sortiert - fuers Monitor-Hopping
        centers = [m.get("pos", [0, 0])[0] + m.get("size", [1, 1])[0] / 2 for m in monitors]
        self.hop_order = [int(i) for i in np.argsort(centers)] if centers else [0]
        self.drops = deque(maxlen=24)  # Lichtregen: (Startzeit, x-Position)
        self.total = len(xy)
        self.phase = 0.0
        self.last = time.monotonic()
        self.waves = deque(maxlen=6)  # (Startzeit, Staerke) - eine Druckwelle pro Beat
        self.seen_beats = 0
        self.direction = 1
        self.blink, self.blink_color = 0.0, np.zeros(3, np.float32)
        self.last_blink = 0.0         # Zeit des letzten Blinkens - hoechstens ~2,5 pro Sekunde
        self.last_white = -99         # Kick-Nummer des letzten Weiss-Flashs
        self.scene, self.prev_scene, self.scene_start = SCENES[0], SCENES[0], 0.0
        self.scene_beat = 0
        self.peak = 0.0               # Spitze des Equalizers, faellt langsam
        self.calm = 0.0               # 0 = volle Party, 1 = nur ruhiges Atmen (folgt der Energie)

    def set_colors(self, colors, external=True):
        """Neue Farbrichtung (Liste aus zwei #rrggbb) - blendet weich ueber; neuer Song = auch neue Muster."""
        rgb = [[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colors[:2]]
        if len(rgb) == 1:
            h, s, v = colorsys.rgb_to_hsv(*(np.array(rgb[0]) / 255))
            rgb.append([round(x * 255) for x in colorsys.hsv_to_rgb((h + 25 / 360) % 1, s, v)])
        self.fade_from, self.fade_to = self.colors.copy(), np.array(rgb, np.float32)
        self.fade_start = time.monotonic()
        if external:
            self.from_spotify = self.fade_start
        self._new_scene(self.fade_start)

    def _new_scene(self, now):
        choices = [sc for sc in SCENES if sc != self.scene]
        self.prev_scene, self.scene = self.scene, choices[self.rng.integers(len(choices))]
        self.scene_start = now
        self.scene_beat = self.seen_beats

    def _update_colors(self, now):
        t = min(1.0, (now - self.fade_start) / FADE)
        self.colors = self.fade_from + (self.fade_to - self.fade_from) * (t * t * (3 - 2 * t))

    # ---- Muster: liefern je LED eine Helligkeit 0..1 und welche Farbe (0 = Haupt-, 1 = Nachbarfarbe) ----

    def _fan(self, a, now, beat_len, dt):
        beams = 4 if a["mid"] > 0.5 else 3
        width = 0.07 + 0.06 * a["bass"]
        level, which = np.zeros(self.total), np.zeros(self.total)
        for k in range(beams):
            diff = np.angle(np.exp(1j * (self.angle - 2 * np.pi * (self.phase + k / beams))))
            beam = np.exp(-(diff / width) ** 2) * (0.5 + 0.5 * a["mid"])
            which = np.where(beam > level, k % 2, which)
            level = np.maximum(level, beam)
        return level, which

    def _waves(self, a, now, beat_len, dt):
        level = np.zeros(self.total)
        for started, strength in self.waves:
            age = now - started
            ring = age / beat_len * 1.3
            if ring <= 1.6:
                level = np.maximum(level, np.exp(-((self.radius - ring) / 0.12) ** 2) * strength
                                   * max(0.0, 1 - age / (1.5 * beat_len)))
        return level, np.zeros(self.total)

    def _scanner(self, a, now, beat_len, dt):
        t = (now / (2 * beat_len)) % 2               # hin und zurueck in 4 Beats
        front = t if t < 1 else 2 - t
        level = np.exp(-((self.x - front) / 0.05) ** 2)
        echo = np.exp(-((self.x - (1 - front)) / 0.05) ** 2) * 0.6
        return np.maximum(level, echo), (echo > level).astype(float)

    def _rise(self, a, now, beat_len, dt):
        height = a["bass"] ** 0.8                     # Equalizer: der Bass fuellt die Wand von unten
        self.peak = max(height, self.peak - 0.6 * dt)
        filled = 1 - self.y
        level = np.clip((height - filled) * 6, 0, 1) * (0.4 + 0.6 * filled / max(height, 0.05))
        cap = np.exp(-((filled - self.peak) / 0.03) ** 2)
        return np.clip(np.maximum(level, cap), 0, 1), (cap > level).astype(float)

    def _chase(self, a, now, beat_len, dt):
        # Lauflicht um jeden Monitor: springt auf jeden Beat ein Achtel weiter, gleitet dabei kurz nach
        glide = min(1.0, (now - a["last_beat"]) / (beat_len * 0.5)) if a["last_beat"] else 1.0
        pos = (self.seen_beats - 1 + glide) / 8 * self.direction
        level, which = np.zeros(self.total), np.zeros(self.total)
        for k in range(2):
            d = np.abs((self.ring - pos - k / 2) % 1 - 0.5)
            seg = np.clip((d - 0.38) / 0.12, 0, 1)    # zwei Bloecke je Monitor
            which = np.where(seg > level, k, which)
            level = np.maximum(level, seg)
        return level, which

    def _hop(self, a, now, beat_len, dt):
        """Monitor-Hopping: jeder Kick laesst den naechsten Monitor (von links nach rechts) aufleuchten."""
        current = self.hop_order[self.seen_beats % len(self.hop_order)]
        since = now - a["last_beat"] if a["last_beat"] else beat_len
        glow = 0.3 + 0.7 * np.exp(-since / (beat_len * 0.7))
        level = np.where(self.owner == current, glow, 0.05)
        return level, (self.owner % 2).astype(float)

    def _comet(self, a, now, beat_len, dt):
        """Kometen mit Schweif kreisen um jeden Monitor - eine Runde in 2 Beats."""
        head = (now / (2 * beat_len)) % 1
        level, which = np.zeros(self.total), np.zeros(self.total)
        for k in range(2):
            behind = (head + k / 2 - self.ring) % 1          # wie weit die LED hinter dem Kopf liegt
            tail = np.where(behind < 0.22, (1 - behind / 0.22) ** 2, 0)
            which = np.where(tail > level, k, which)
            level = np.maximum(level, tail)
        return level, which

    def _center(self, a, now, beat_len, dt):
        """Auf jeden Kick schiessen zwei Balken vom Wandzentrum nach links und rechts auseinander."""
        since = now - a["last_beat"] if a["last_beat"] else beat_len
        reach = min(1.0, since / (beat_len * 0.8)) * 0.55
        dist = np.abs(self.x - 0.5)
        level = np.exp(-((dist - reach) / 0.05) ** 2) * max(0.0, 1 - since / (beat_len * 1.2))
        return level, (dist > 0.3).astype(float)

    def _rain(self, a, now, beat_len, dt):
        """Lichtregen: Hi-Hats lassen Tropfen von oben nach unten ueber die Wand fallen."""
        if self.rng.random() < a["high"] ** 2 * 0.35:
            self.drops.append((now, float(self.rng.random())))
        level = np.zeros(self.total)
        for started, x in self.drops:
            fall = (now - started) / (beat_len * 2)               # in 2 Beats von oben nach unten
            if fall <= 1.2:
                drop = np.exp(-((self.x - x) / 0.035) ** 2) * np.exp(-((self.y - fall) / 0.08) ** 2)
                level = np.maximum(level, drop)
        return np.clip(level * 1.2, 0, 1), (level > 0.6).astype(float)

    def _pingpong(self, a, now, beat_len, dt):
        side = self.seen_beats % 2                    # linke und rechte Haelfte im Wechsel
        since = now - a["last_beat"] if a["last_beat"] else beat_len
        glow = np.exp(-since / (beat_len * 0.6))
        level = np.where((self.x < 0.5) == bool(side), 0.25 + 0.75 * glow, 0.08)
        return level, np.full(self.total, float(side))

    def _draw(self, scene, a, now, beat_len, dt, main, near):
        out = np.zeros((self.total, 3))
        for name in scene:
            level, which = getattr(self, "_" + name)(a, now, beat_len, dt)
            color = np.where(which[:, None] > 0.5, near, main)
            out = np.maximum(out, level[:, None] * color)
        return out

    def frame(self, audio):
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        a = audio.levels() if audio else None
        playing = bool(a and now - a["heard"] < SILENCE)

        self._update_colors(now)
        main, near = self.colors
        # Ruhiger Grund: die Farbrichtung wogt quer ueber die Wand - in Pausen und leisen Stellen das Einzige
        mix = (np.sin(2 * np.pi * (self.x + now * 0.015)) * 0.5 + 0.5)[:, None]
        calm_light = main * mix + near * (1 - mix)
        if not playing:
            self.silent_since = self.silent_since or now
            return calm_light * 0.3
        if self.silent_since and now - self.silent_since > SONG_BREAK and now - self.from_spotify > 600:
            family = FAMILIES[self.rng.integers(len(FAMILIES))]   # neuer Song ohne Spotify-Farben
            self.set_colors(["#%02x%02x%02x" % c for c in family], external=False)
        self.silent_since = None

        bpm = a["bpm"] if 70 < a["bpm"] < 200 else 128
        if bpm > 170:
            bpm /= 2  # Off-Beats mitgezaehlt - fuer die Bewegungen das halbe Tempo
        beat_len = 60 / bpm
        beats = a["beats"]
        energy = a.get("energy", 1.0)
        # Party-Staerke: Energie (laut genug?) mal Drive (Kick im Takt?) - weich, ohne Spruenge
        drive = a.get("drive", 1.0)
        # normale Lautstaerke-Schwankungen (Energie 0,5-0,8) = volle Party; erst unter ~0,4 (Ausfaden) wird es ruhig
        target = 1 - _smooth((energy - 0.15) / 0.35) * _smooth((drive - 0.2) / 0.4)
        # rauf (mehr Party) sofort, runter (Ausfaden) weich ueber ~1 s
        self.calm += (target - self.calm) * (1 - np.exp(-dt / (0.04 if target < self.calm else 1.0)))
        party = 1 - self.calm

        if beats != self.seen_beats:
            self.seen_beats = beats
            self.waves.append((now, 0.6 + 0.4 * a["strength"]))
            if beats % 8 == 0:
                self.direction *= -1
            if beats - self.scene_beat >= SCENE_BEATS:
                self._new_scene(now)
            # Beat-Blinken in der Hauptfarbe - hoechstens ~2,5 pro Sekunde (bei doppelt erkanntem Tempo nur jeder
            # zweite Kick). Weiss nur alle 16 Kicks oder bei ganz harten Kicks, mit mindestens 4 Kicks Abstand, und
            # nur bei voller Party. Nach einer ruhigen Phase blinkt es erst sanft (party^2) - kein Blitz-Gewitter.
            double = a["bpm"] > 170
            if now - self.last_blink > 0.4 and not (double and beats % 2):
                white = 0.0
                if party > 0.8 and beats - self.last_white >= 4 and (beats % 16 == 0 or a["strength"] > 0.9):
                    white = 0.7 if beats % 16 == 0 else 0.45
                    self.last_white = beats
                self.blink = (0.25 + 0.25 * a["strength"]) * party ** 2
                self.blink_color = main * (1 - white) + 255 * white
                self.last_blink = now
        self.phase += self.direction * dt / (4 * beat_len)

        # Grundton pumpt mit dem Bass (nie ganz schwarz - kein hartes An/Aus), darunter das ruhige Atmen
        out = np.tile(main * 0.12 * (1 + 2 * a["bass"] ** 2) * party, (self.total, 1))
        out = np.maximum(out, calm_light * (0.12 + 0.25 * self.calm) * (0.4 + 0.6 * energy))
        # Muster der aktuellen Szene, beim Wechsel weich aus der alten ueberblendet
        fade = _smooth((now - self.scene_start) / (SCENE_FADE * beat_len))
        pattern = self._draw(self.scene, a, now, beat_len, dt, main, near)
        if fade < 1:
            pattern = pattern * fade + self._draw(self.prev_scene, a, now, beat_len, dt, main, near) * (1 - fade)
        out = np.maximum(out, pattern * party)

        if self.blink > 0.02:
            out = np.maximum(out, self.blink_color * self.blink)
            self.blink *= np.exp(-dt / 0.12)
        # Hi-Hats: einzelne LEDs funkeln weiss - nur, wenn richtig was los ist
        sparks = self.rng.random(self.total) < a["high"] ** 3 * 0.05 * party
        out[sparks] = main * 0.25 + 190
        return np.clip(out, 0, 255)
