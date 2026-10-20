"""Party-Modus: Licht im Takt der Musik - wie auf einem Rave.

Hoert per WASAPI-Loopback den Wave-Link-Kanal "Music" mit (nur die Musik, ohne Voice Chat und EDITH, und
unabhaengig von den Lautstaerke-Reglern). Daraus: Bass, Mitten, Hoehen, Beats und Tempo.

Auf den LEDs (jeder Monitor-Ring fuer sich):
- Laser: schmale, grelle Strahlen kreisen um jeden Monitor, Tempo nach BPM, Richtung kippt alle 8 Beats
- Beat-Flash: auf jeden Beat blitzt alles in der Palettenfarbe auf (starke Beats heller), klingt schnell ab
- Bass-Pumpe: der Grundton atmet mit dem Bass
- Funkeln: Hoehen (Hi-Hats) lassen einzelne LEDs weiss aufblitzen
- alle 16 Beats eine neue Rave-Palette
Ohne Musik: langsames Farbwabern, bis wieder etwas laeuft.
"""

import threading
import time
from collections import deque

import numpy as np

# Rave-Paletten: (Laser/Flash, zweite Laserfarbe, Grundton)
PALETTES = [
    ((255, 0, 200), (0, 230, 255), (40, 0, 60)),     # Magenta / Cyan
    ((0, 255, 90), (150, 0, 255), (0, 30, 20)),      # Laser-Gruen / UV
    ((255, 20, 20), (30, 60, 255), (40, 0, 10)),     # Rot / Blau
    ((255, 120, 0), (255, 0, 120), (40, 10, 0)),     # Orange / Pink
    ((120, 0, 255), (255, 255, 255), (20, 0, 40)),   # UV / Weiss
    ((0, 255, 255), (255, 230, 0), (0, 25, 35)),     # Cyan / Gelb
]
BEAT_GAP = 0.23        # s - schneller als ~260 BPM gibt es keine Beats
BEAT_FACTOR = 1.25     # Bass muss so viel ueber dem Schnitt der letzten ~0,8 s liegen (an echter Musik abgestimmt)
SILENCE = 0.6          # s ohne Audio = Pause


class AudioListener:
    """Analysiert die Musik im Hintergrund; levels() liefert den letzten Stand (Werte 0..1)."""

    def __init__(self, device_hint="Music (Elgato"):
        self.device_hint = device_hint
        self.lock = threading.Lock()
        self.state = {"bass": 0.0, "mid": 0.0, "high": 0.0, "beats": 0, "last_beat": 0.0, "strength": 0.0,
                      "bpm": 0.0, "heard": 0.0}
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
        bass = raw["bass"]
        average = (sum(self._history) / len(self._history)) if self._history else bass
        self._history.append(bass)
        with self.lock:
            state = self.state
            state.update(levels, heard=now if self._peaks["bass"] > 1e-4 and np.abs(block).max() > 1e-4 else state["heard"])
            if (bass > average * BEAT_FACTOR and levels["bass"] > 0.25
                    and now - state["last_beat"] > BEAT_GAP):
                if state["last_beat"] and now - state["last_beat"] < 2:
                    self._intervals.append(now - state["last_beat"])
                    state["bpm"] = 60 / float(np.median(self._intervals))
                state["beats"] += 1
                state["last_beat"] = now
                state["strength"] = min(1.0, bass / max(average, 1e-6) / 2.5)


class PartyEffect:
    def __init__(self, monitors):
        self.monitors = monitors  # [{"name", "count"}, ...] in Streifen-Reihenfolge
        self.phase = 0.0
        self.last = time.monotonic()
        self.flash = 0.0
        self.seen_beats = 0
        self.direction = 1
        self.rng = np.random.default_rng()

    def frame(self, audio):
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        total = sum(m["count"] for m in self.monitors)
        out = np.zeros((total, 3), np.float32)
        a = audio.levels() if audio else None
        playing = bool(a and now - a["heard"] < SILENCE)

        if not playing:
            # Pause: langsames Farbwabern rund um die Monitore
            self.phase += dt * 0.05
            start = 0
            for m in self.monitors:
                pos = (np.arange(m["count"]) / max(1, m["count"]) + self.phase) % 1
                hue = np.stack([np.sin(2 * np.pi * (pos + k / 3)) for k in range(3)], axis=1) * 0.5 + 0.5
                out[start:start + m["count"]] = hue * 70
                start += m["count"]
            return out

        beats = a["beats"]
        new_beat = beats != self.seen_beats
        if new_beat:
            self.seen_beats = beats
            self.flash = 0.55 + 0.45 * a["strength"]
            if beats % 8 == 0:
                self.direction *= -1
        palette = PALETTES[(beats // 16) % len(PALETTES)]
        laser1, laser2, base = (np.array(c, np.float32) for c in palette)

        # Laser kreisen: eine Runde in 4 Beats (bei unbekanntem Tempo ~128 BPM)
        bpm = a["bpm"] if 70 < a["bpm"] < 200 else 128
        self.phase += self.direction * dt * bpm / 60 / 4
        beams = 3 if a["mid"] > 0.5 else 2
        width = 0.025 + 0.02 * a["bass"]

        start = 0
        for m in self.monitors:
            n = m["count"]
            pos = np.arange(n) / max(1, n)
            seg = base * (0.15 + 0.6 * a["bass"] ** 2) * np.ones((n, 1), np.float32)  # Bass-Pumpe
            for k in range(beams):
                center = (self.phase + k / beams) % 1
                dist = np.minimum(np.abs(pos - center), 1 - np.abs(pos - center))
                beam = np.exp(-(dist / width) ** 2)[:, None]
                color = laser1 if k % 2 == 0 else laser2
                seg = np.maximum(seg, beam * color * (0.55 + 0.45 * a["mid"]))
            out[start:start + n] = seg
            start += n

        # Beat-Flash ueber alles, klingt in ~150 ms ab
        if self.flash > 0.02:
            out = np.maximum(out, laser1 * self.flash)
            self.flash *= np.exp(-dt / 0.06)
        # Hi-Hats: einzelne LEDs blitzen weiss
        sparks = self.rng.random(total) < a["high"] ** 3 * 0.06
        out[sparks] = 255
        return np.clip(out, 0, 255)
