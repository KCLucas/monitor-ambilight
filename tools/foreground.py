"""Erkennt, was am PC im Vordergrund laeuft - Grundlage fuer die Auto-Szene.

Ergebnis von detect(): ("game" | "video" | None, Programmname). Heuristik:
- Programm liegt in einem typischen Spiele-Ordner (Steam, Epic, Xbox, ...)    -> Spiel
- bekannter Videoplayer                                                        -> Video
- Browser oder Store-App (Netflix, Disney+, ...) im Vollbild                   -> Video
- sonstiges Programm im Vollbild (Desktop und Explorer ausgenommen)            -> Spiel
"""

import ctypes
from ctypes import wintypes
from pathlib import PureWindowsPath

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2

GAME_DIRS = (
    "\\steamapps\\common\\",
    "\\epic games\\",
    "\\xboxgames\\",
    "\\riot games\\",
    "\\ubisoft game launcher\\games\\",
    "\\gog galaxy\\games\\",
    "\\ea games\\",
    "\\battle.net\\games\\",
)
VIDEO_PLAYERS = {
    "vlc.exe", "mpv.exe", "mpc-hc.exe", "mpc-hc64.exe", "mpc-be.exe", "mpc-be64.exe",
    "potplayer.exe", "potplayermini.exe", "potplayermini64.exe", "wmplayer.exe", "video.ui.exe",
    "kodi.exe", "plex.exe", "plex htpc.exe", "jellyfin media player.exe", "stremio.exe",
}






# Im Vollbild meist Video (YouTube, Netflix im Browser bzw. Store-Apps)
VIDEO_WHEN_FULLSCREEN = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "applicationframehost.exe",
}






# Niemals Spiel, auch wenn das Fenster den ganzen Bildschirm fuellt
NEVER_GAME = {
    "explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "lockapp.exe", "textinputhost.exe", "taskmgr.exe", "python.exe", "pythonw.exe",
    "wallpaper32.exe", "wallpaper64.exe", "code.exe", "windowsterminal.exe",
}
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd"}


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _process_path(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _monitor_of(hwnd):
    """(Rechteck der Anzeige, auf der das Fenster liegt) oder None."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info.rcMonitor


def _is_fullscreen(hwnd, monitor_rect):
    rect = wintypes.RECT()
    if monitor_rect is None or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    m = monitor_rect
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def snapshot():
    """Was im Vordergrund ist: {"kind": "game"|"video"|None, "app": exe, "monitor": [links, oben]
    der Anzeige mit dem aktiven Fenster (None beim Desktop), "fullscreen": bool}."""
    result = {"kind": None, "app": None, "monitor": None, "fullscreen": False}
    hwnd = user32.GetForegroundWindow()
    if not hwnd or _class_name(hwnd) in DESKTOP_CLASSES:
        return result
    monitor_rect = _monitor_of(hwnd)
    if monitor_rect is not None:
        result["monitor"] = [monitor_rect.left, monitor_rect.top]
    result["fullscreen"] = _is_fullscreen(hwnd, monitor_rect)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    path = _process_path(pid.value)
    if not path:
        return result
    exe = result["app"] = PureWindowsPath(path).name.lower()
    lower = path.lower()

    if any(d in lower for d in GAME_DIRS):
        result["kind"] = "game"
    elif exe in VIDEO_PLAYERS:
        result["kind"] = "video"
    elif result["fullscreen"]:
        if exe in VIDEO_WHEN_FULLSCREEN:
            result["kind"] = "video"
        elif exe not in NEVER_GAME:
            result["kind"] = "game"
    return result


def detect():
    """("game" | "video" | None, Programmname) fuer das Fenster im Vordergrund."""
    snap = snapshot()
    return snap["kind"], snap["app"]


MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


def taskbar_insets():
    """{(links, oben): (l, o, r, u)} je Anzeige: wie viele Pixel Taskleiste(n) und angedockte
    Leisten an jedem Rand belegen (Differenz zwischen Anzeige und nutzbarem Arbeitsbereich)."""
    insets = {}

    def callback(hmonitor, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            m, w = info.rcMonitor, info.rcWork
            insets[(m.left, m.top)] = (w.left - m.left, w.top - m.top, m.right - w.right, m.bottom - w.bottom)
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(callback), 0)
    return insets


if __name__ == "__main__":
    import time

    print("Zeigt 15 s lang, was im Vordergrund erkannt wird (Fenster wechseln zum Testen) ...")
    for _ in range(15):
        print(detect())
        time.sleep(1)
