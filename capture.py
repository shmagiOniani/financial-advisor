"""Screen capture utilities — cross-platform (macOS + Linux X11/Wayland)."""

import os
import mss
from PIL import Image
from typing import Optional, Tuple
from urllib.parse import urlparse
import tkinter as tk

_IS_WAYLAND = os.environ.get("XDG_SESSION_TYPE") == "wayland"


def _capture_wayland_portal(region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
    import dbus
    import dbus.mainloop.glib
    import random
    import string
    from gi.repository import GLib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    # Subscribe BEFORE calling Screenshot to avoid missing the Response signal.
    token = "gt" + "".join(random.choices(string.ascii_lowercase, k=6))
    sender = bus.get_unique_name().lstrip(":").replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    state = {"uri": None}
    loop = GLib.MainLoop()

    def on_response(response, results):
        if response == 0:
            state["uri"] = str(results.get("uri", ""))
        loop.quit()

    req_obj = bus.get_object("org.freedesktop.portal.Desktop", request_path)
    req_obj.connect_to_signal("Response", on_response,
                               dbus_interface="org.freedesktop.portal.Request")

    portal = bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
    iface = dbus.Interface(portal, "org.freedesktop.portal.Screenshot")
    options = dbus.Dictionary(
        {"handle_token": dbus.String(token), "interactive": dbus.Boolean(False)},
        signature="sv",
    )
    iface.Screenshot("", options)

    GLib.timeout_add(15000, loop.quit)
    loop.run()

    if not state["uri"]:
        raise RuntimeError("XDG portal screenshot failed or was denied")

    path = urlparse(state["uri"]).path
    img = Image.open(path).convert("RGB")
    if region:
        img = img.crop(region)

    try:
        os.unlink(path)
    except OSError:
        pass

    return img


def capture_screen(region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
    if _IS_WAYLAND:
        return _capture_wayland_portal(region)

    with mss.mss() as sct:
        if region:
            left, top, right, bottom = region
            mon = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        else:
            mon = sct.monitors[1]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def select_region() -> Optional[Tuple[int, int, int, int]]:
    """Show a semi-transparent fullscreen overlay to drag-select a region."""
    result: list[Optional[Tuple[int, int, int, int]]] = [None]
    start_x: list[int] = [0]
    start_y: list[int] = [0]
    rect_id: list[Optional[int]] = [None]

    root = tk.Tk()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.25)
    try:
        root.overrideredirect(True)
    except Exception:
        pass

    root.configure(bg="gray10")
    canvas = tk.Canvas(root, bg="gray10", highlightthickness=0, cursor="cross")
    canvas.pack(fill=tk.BOTH, expand=True)
    canvas.create_text(
        sw // 2, 36,
        text="Click and drag to select the chart region  •  Esc to cancel",
        fill="white", font=("Helvetica", 14, "bold"),
    )

    def on_press(e):
        start_x[0], start_y[0] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])

    def on_drag(e):
        if rect_id[0]:
            canvas.delete(rect_id[0])
        rect_id[0] = canvas.create_rectangle(
            start_x[0], start_y[0], e.x, e.y, outline="#00e676", width=2)

    def on_release(e):
        x1, x2 = min(start_x[0], e.x), max(start_x[0], e.x)
        y1, y2 = min(start_y[0], e.y), max(start_y[0], e.y)
        if x2 - x1 > 20 and y2 - y1 > 20:
            result[0] = (x1, y1, x2, y2)
        root.destroy()

    canvas.bind("<ButtonPress-1>",   on_press)
    canvas.bind("<B1-Motion>",       on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", lambda _: root.destroy())
    root.mainloop()
    return result[0]
