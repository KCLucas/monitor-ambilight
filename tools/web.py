"""Weboberflaeche: liefert die Seite samt App-Dateien aus und eine kleine JSON-API fuer Vorschau und Einstellungen.

Laeuft per HTTP (PC, localhost) und - fuers Handy - zusaetzlich per HTTPS mit eigenem Zertifikat,
weil Chrome eine Web-App nur von HTTPS-Seiten installiert.
"""

import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import segno

# Dateien aus web/, die ausgeliefert werden duerfen (bei jedem Aufruf neu gelesen,
# damit Aenderungen ohne Neustart sichtbar sind)
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/sw.js": ("sw.js", "application/javascript"),
}


def qr(url):
    return segno.make(url, error="m").svg_inline(scale=5, border=2, dark="#111111", light="#ffffff")


def serve(engine, host, port, web_dir, phone=None, https_port=None, tls=None, ca_path=None):
    """phone = {"app_url", "ca_url"} fuer den Handy-Dialog; tls = (cert, key) startet zusaetzlich HTTPS."""
    phone_info = None
    if phone:
        phone_info = dict(phone, app_qr=qr(phone["app_url"]), ca_qr=qr(phone["ca_url"]))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                name, content_type = STATIC[path]
                self._send(200, (web_dir / name).read_bytes(), content_type)
            elif path == "/ca.crt" and ca_path:
                self._send(200, ca_path.read_bytes(), "application/x-x509-ca-cert",
                           {"Content-Disposition": 'attachment; filename="ambilight-ca.crt"'})
            elif path == "/api/layout":
                self._json(200, engine.layout())
            elif path == "/api/state":
                self._json(200, engine.state())
            elif path == "/api/stream":
                self._stream()
            elif path == "/api/phone":
                self._json(200 if phone_info else 404, phone_info or {"error": "nur lokal gestartet"})
            else:
                self._json(404, {"error": "nicht gefunden"})

        def do_POST(self):
            actions = {"/api/settings": engine.update_settings, "/api/alert": engine.set_alert,
                       "/api/party": engine.set_party_colors}
            if self.path not in actions:
                self._json(404, {"error": "nicht gefunden"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                actions[self.path](json.loads(self.rfile.read(length) or b"{}"))
            except (ValueError, TypeError, AttributeError) as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, engine.state())

        def _stream(self):
            """LED-Farben als Server-Sent Events, je fertigem Frame eine Nachricht - so laeuft die
            Vorschau im Takt der LEDs statt im Takt einer Abfrage."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = None
            try:
                while True:
                    frame_id, colors = engine.next_frame(last)
                    if frame_id == last:
                        self.wfile.write(b": ping\n\n")  # haelt die Verbindung offen, merkt Abbrueche
                    else:
                        self.wfile.write(f"data: {colors}\n\n".encode())
                        last = frame_id
                    self.wfile.flush()
            except OSError:
                pass  # Seite geschlossen

        def _json(self, code, data):
            self._send(code, json.dumps(data).encode(), "application/json")

        def _send(self, code, body, content_type, headers=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*tls)
        https = ThreadingHTTPServer((host, https_port), Handler)
        # Handshake erst im Verbindungs-Thread, damit ein langsamer Client nicht alle blockiert
        https.socket = context.wrap_socket(https.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=https.serve_forever, daemon=True).start()

    ThreadingHTTPServer((host, port), Handler).serve_forever()
