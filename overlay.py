"""Floating always-on-top overlay for Gemini Trader."""

import queue
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime

from analyzer import AnalysisResult
from markets import get_market_status

_UI = "DejaVu Sans"

# ─────────────────────────────────────────────────────────────── Palette ─────
_BG        = "#0b0f17"
_TITLE_BG  = "#0a0d14"
_CARD      = "#141b26"
_CARD_2    = "#0f1620"
_BORDER    = "#232c3a"
_DIM       = "#2b3444"
_FAINT     = "#5a6577"
_MUTED     = "#8390a3"
_TEXT      = "#e7ebf2"
_HEADING   = "#93a0b4"
_ACCENT    = "#5b63f0"
_ACCENT_HI = "#7b82f5"
_GREEN     = "#26c66a"
_RED       = "#ef4a4a"
_YELLOW    = "#e5b23a"
_BLUE_TXT  = "#7ea6ff"

_SIGNAL_COLORS = {
    "BUY":     _GREEN,
    "SELL":    _RED,
    "HOLD":    _YELLOW,
    "UNCLEAR": "#9aa4b2",
    "CHAT":    "#7c9cff",
}
_DEFAULT_ACCENT = "#5c7cfa"
_ARROWS = {"BUY": "↗", "SELL": "↘", "HOLD": "→"}

# icon, label, prompt sent to the model
_QUICK = [
    ("↗", "Trend",   "What is the current trend direction and momentum strength?"),
    ("⊙", "Entry",   "Where is the best entry point right now, and why?"),
    ("⇅", "S/R",     "List the key support and resistance levels visible on this chart."),
    ("◈", "Risk",    "What is the risk/reward ratio here? Where should the stop loss go?"),
    ("≣", "Pattern", "What chart pattern is currently forming? Is it confirmed or pending?"),
]

_HIST_TINT = {
    "BUY":  ("#10261b", _GREEN),
    "HOLD": ("#26220f", _YELLOW),
    "SELL": ("#291414", _RED),
}
_SIG_HISTORY_MAX = 6


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    """Draw a smooth rounded rectangle; return the polygon id."""
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


class _Btn(tk.Canvas):
    """A rounded, hover-highlighting canvas button."""

    def __init__(self, master, text, command, *, width, height=34, radius=9,
                 icon="", fill=_CARD, hover=_DIM, border=_BORDER,
                 fg=_MUTED, fg_hover=_TEXT, font_size=9, bg=_BG):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self._cmd = command
        self._fill, self._hover = fill, hover
        self._fg, self._fg_hover = fg, fg_hover
        self._rect = _round_rect(self, 1, 1, width - 1, height - 1, radius,
                                 fill=fill, outline=border)
        label = f"{icon}  {text}" if icon and text else (icon or text)
        self._txt = self.create_text(width / 2, height / 2 + 1, text=label,
                                     fill=fg, font=(_UI, font_size, "bold"))
        self.bind("<Enter>", lambda _: self._paint(self._hover, self._fg_hover))
        self.bind("<Leave>", lambda _: self._paint(self._fill, self._fg))
        self.bind("<Button-1>", lambda _: self._cmd and self._cmd())

    def _paint(self, fill, fg):
        self.itemconfigure(self._rect, fill=fill)
        self.itemconfigure(self._txt, fill=fg)

    def set_label(self, text):
        self.itemconfigure(self._txt, text=text)

    def set_colors(self, *, fill=None, fg=None):
        if fill is not None:
            self._fill = fill
        if fg is not None:
            self._fg = fg
        self._paint(self._fill, self._fg)


class TradingOverlay:

    def __init__(self, result_queue: queue.Queue, stop_event: threading.Event,
                 pause_event: threading.Event, manual_event: threading.Event,
                 prompt_queue: queue.Queue, interval: int) -> None:
        self._q        = result_queue
        self._stop     = stop_event
        self._pause    = pause_event
        self._manual   = manual_event
        self._pq       = prompt_queue
        self._interval = interval

        self._analyzing   = False
        self._last_result = None
        self._collapsed   = False
        self._expanded_geo = ""
        self._sig_history: deque = deque(maxlen=_SIG_HISTORY_MAX)

        self._build_window()
        self._build_titlebar()
        self._build_body()

    # ─────────────────────────────────────────────────────────── Window ─────
    def _build_window(self) -> None:
        self.root = tk.Tk()
        self.root.title("Gemini Trader")
        self.root.configure(bg=_BORDER)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)
        self.root.resizable(False, False)

        try:
            self.root.wm_attributes("-type", "splash")
        except tk.TclError:
            try:
                self.root.overrideredirect(True)
            except Exception:
                pass

        self._W = 500
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{self._W}x940+{sw - self._W - 24}+20")
        self._dx = self._dy = 0

        self._outer = tk.Frame(self.root, bg=_BG)
        self._outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

    # ─────────────────────────────────────────────────────────── Title ──────
    def _build_titlebar(self) -> None:
        tbar = tk.Frame(self._outer, bg=_TITLE_BG, height=54)
        tbar.pack(fill=tk.X)
        tbar.pack_propagate(False)

        logo = tk.Canvas(tbar, width=30, height=30, bg=_TITLE_BG,
                         highlightthickness=0)
        _round_rect(logo, 2, 2, 28, 28, 8, fill=_ACCENT, outline="")
        logo.create_text(15, 16, text="✦", font=(_UI, 13, "bold"),
                         fill="#ffffff")
        logo.pack(side=tk.LEFT, padx=(16, 10), pady=12)

        name = tk.Frame(tbar, bg=_TITLE_BG)
        name.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(name, text="Gemini Trader", bg=_TITLE_BG, fg=_TEXT,
                 font=(_UI, 13, "bold")).pack(anchor="w", pady=(9, 0))
        tk.Label(name, text="AI Trading Assistant", bg=_TITLE_BG, fg=_FAINT,
                 font=(_UI, 9)).pack(anchor="w")

        close = tk.Label(tbar, text="✕", bg=_TITLE_BG, fg=_MUTED,
                         font=(_UI, 13), cursor="hand2", padx=12)
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda _: self._on_close())
        close.bind("<Enter>", lambda _: close.configure(fg=_RED))
        close.bind("<Leave>", lambda _: close.configure(fg=_MUTED))

        mini = tk.Label(tbar, text="—", bg=_TITLE_BG, fg=_MUTED,
                        font=(_UI, 13, "bold"), cursor="hand2", padx=8)
        mini.pack(side=tk.RIGHT)
        mini.bind("<Button-1>", lambda _: self._toggle_collapse())
        mini.bind("<Enter>", lambda _: mini.configure(fg=_TEXT))
        mini.bind("<Leave>", lambda _: mini.configure(fg=_MUTED))

        draggable = [tbar, name, logo] + list(name.winfo_children())
        for w in draggable:
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    # ──────────────────────────────────────────────────────────── Body ─────
    def _build_body(self) -> None:
        self._body = tk.Frame(self._outer, bg=_BG)
        self._body.pack(fill=tk.BOTH, expand=True)
        b = self._body

        self._build_markets(b)
        self._build_signal(b)
        self._build_analysis(b)
        self._build_history(b)
        self._build_quick(b)
        self._build_ask(b)
        self._build_bottom(b)

    def _heading(self, parent, text):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=_HEADING,
                        font=(_UI, 9, "bold"))

    # ───────────────────────────────────────────────────────── Markets ─────
    def _build_markets(self, b) -> None:
        sec = tk.Frame(b, bg=_BG)
        sec.pack(fill=tk.X, padx=16, pady=(12, 0))

        hdr = tk.Frame(sec, bg=_BG)
        hdr.pack(fill=tk.X)
        self._heading(hdr, "MARKETS").pack(side=tk.LEFT)
        tk.Label(hdr, text=f"⟳ {self._interval}s", bg=_BG, fg=_FAINT,
                 font=(_UI, 9)).pack(side=tk.RIGHT)

        self._mkt_row = tk.Frame(sec, bg=_BG)
        self._mkt_row.pack(fill=tk.X, pady=(8, 0))
        self._build_market_cards()

    def _build_market_cards(self) -> None:
        for w in self._mkt_row.winfo_children():
            w.destroy()
        for i, m in enumerate(get_market_status()):
            is_open = m["open"]
            c = tk.Canvas(self._mkt_row, width=88, height=46, bg=_BG,
                          highlightthickness=0)
            c.pack(side=tk.LEFT, padx=(0 if i == 0 else 6, 0))
            _round_rect(c, 1, 1, 87, 45, 10, fill=_CARD, outline=_BORDER)
            c.create_oval(12, 16, 20, 24,
                          fill=_GREEN if is_open else _RED, outline="")
            c.create_text(26, 20, anchor="w", text=m["name"],
                          font=(_UI, 9, "bold"),
                          fill=_TEXT if is_open else _MUTED)
            c.create_text(12, 34, anchor="w", text=m["local"],
                          font=(_UI, 8), fill=_MUTED)
            self._add_tooltip(
                c, f"{m['flag']} {m['name']} — {m['local']} local — "
                   f"{'OPEN' if is_open else 'closed'}")

    # ────────────────────────────────────────────────────────── Signal ─────
    def _build_signal(self, b) -> None:
        self._sc = sc = tk.Canvas(b, width=468, height=188, bg=_BG,
                                  highlightthickness=0)
        sc.pack(padx=16, pady=(14, 0))
        _round_rect(sc, 0, 0, 468, 188, 16, fill=_CARD, outline=_BORDER)

        sc.create_text(20, 22, anchor="w", text="CURRENT SIGNAL",
                       font=(_UI, 9, "bold"), fill=_HEADING)
        self._sig_item = sc.create_text(18, 88, anchor="w", text="—",
                                        font=(_UI, 40, "bold"), fill=_MUTED)
        self._arrow_item = sc.create_text(150, 84, anchor="w", text="",
                                          font=(_UI, 22, "bold"), fill=_MUTED)

        sc.create_text(230, 46, anchor="w", text="CONFIDENCE",
                       font=(_UI, 9, "bold"), fill=_HEADING)
        self._confval_item = sc.create_text(230, 70, anchor="w", text="—",
                                            font=(_UI, 15, "bold"), fill=_MUTED)

        self._seg_items = []
        for i in range(3):
            x = 230 + i * 41
            self._seg_items.append(
                _round_rect(sc, x, 100, x + 35, 110, 3, fill=_DIM, outline=""))
        for i, (a, p) in enumerate((("LOW", "28%"), ("MEDIUM", "62%"),
                                    ("HIGH", "100%"))):
            cx = 230 + i * 41 + 17
            sc.create_text(cx, 124, text=a, font=(_UI, 7), fill=_FAINT)
            sc.create_text(cx, 135, text=p, font=(_UI, 7), fill=_FAINT)

        _round_rect(sc, 362, 28, 452, 160, 12, fill=_CARD_2, outline=_BORDER)
        sc.create_oval(400, 48, 414, 62, outline=_MUTED, width=2)
        sc.create_line(407, 55, 407, 50, fill=_MUTED, width=2)
        sc.create_line(407, 55, 411, 57, fill=_MUTED, width=2)
        self._time_item = sc.create_text(407, 84, text="--:--:--",
                                         font=(_UI, 12, "bold"), fill=_TEXT)
        self._date_item = sc.create_text(
            407, 104, text=datetime.now().strftime("%b %d, %Y"),
            font=(_UI, 8), fill=_MUTED)

    # ──────────────────────────────────────────────────────── Analysis ─────
    def _build_analysis(self, b) -> None:
        cv = self._an_canvas = tk.Canvas(b, width=468, height=216, bg=_BG,
                                         highlightthickness=0)
        cv.pack(padx=16, pady=(14, 0))
        _round_rect(cv, 0, 0, 468, 216, 16, fill=_CARD, outline=_BORDER)
        cv.create_text(20, 22, anchor="w", text="✦  ANALYSIS",
                       font=(_UI, 9, "bold"), fill=_HEADING)
        self._an_status = cv.create_text(448, 22, anchor="e", text="",
                                         font=(_UI, 8), fill=_FAINT)

        inner = tk.Frame(cv, bg=_CARD)
        cv.create_window(20, 42, anchor="nw", window=inner, width=430)

        self._reasoning_lbl = tk.Label(
            inner, text="Waiting for first scan…", bg=_CARD, fg=_MUTED,
            font=(_UI, 11), wraplength=428, justify="left", anchor="nw")
        self._reasoning_lbl.pack(fill="x", anchor="nw")

        self._exit_wrap = tk.Frame(inner, bg=_CARD)
        tk.Frame(self._exit_wrap, bg=_DIM, height=1).pack(fill="x", pady=(12, 8))
        self._exit_head = tk.Label(
            self._exit_wrap, text="▸  გასვლის პირობა:", bg=_CARD, fg=_GREEN,
            font=(_UI, 10, "bold"), wraplength=428, justify="left", anchor="nw")
        self._exit_head.pack(fill="x", anchor="nw")
        self._exit_body = tk.Label(
            self._exit_wrap, text="", bg=_CARD, fg=_MUTED,
            font=(_UI, 10), wraplength=428, justify="left", anchor="nw")
        self._exit_body.pack(fill="x", anchor="nw", pady=(2, 0))

    # ───────────────────────────────────────────────────────── History ─────
    def _build_history(self, b) -> None:
        sec = tk.Frame(b, bg=_BG)
        sec.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._heading(sec, "HISTORY  (Last 6)").pack(anchor="w")
        self._hist_row = tk.Frame(sec, bg=_BG)
        self._hist_row.pack(fill=tk.X, pady=(8, 0))
        self._rebuild_history()

    def _rebuild_history(self) -> None:
        for w in self._hist_row.winfo_children():
            w.destroy()
        items = list(self._sig_history)
        if not items:
            tk.Label(self._hist_row, text="no scans yet", bg=_BG, fg=_FAINT,
                     font=(_UI, 8)).pack(anchor="w")
            return
        for i, (sig, hhmm) in enumerate(items):
            bg, fg = _HIST_TINT.get(sig, (_CARD, _MUTED))
            c = tk.Canvas(self._hist_row, width=72, height=48, bg=_BG,
                          highlightthickness=0)
            c.pack(side=tk.LEFT, padx=(0 if i == 0 else 6, 0))
            _round_rect(c, 1, 1, 71, 47, 9, fill=bg, outline=fg)
            c.create_text(36, 18, text=sig, font=(_UI, 9, "bold"), fill=fg)
            c.create_text(36, 33, text=hhmm, font=(_UI, 8), fill=_MUTED)

    # ─────────────────────────────────────────────────── Quick questions ───
    def _build_quick(self, b) -> None:
        sec = tk.Frame(b, bg=_BG)
        sec.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._heading(sec, "QUICK QUESTIONS").pack(anchor="w")
        row = tk.Frame(sec, bg=_BG)
        row.pack(fill=tk.X, pady=(8, 0))
        for i, (icon, label, prompt) in enumerate(_QUICK):
            btn = _Btn(row, label, lambda p=prompt: self._send_quick(p),
                       width=88, height=34, icon=icon, font_size=8)
            btn.pack(side=tk.LEFT, padx=(0 if i == 0 else 6, 0))

    # ────────────────────────────────────────────────────────── Ask box ───
    def _build_ask(self, b) -> None:
        sec = tk.Frame(b, bg=_BG)
        sec.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._heading(sec, "ASK ANYTHING ABOUT THE CHART").pack(anchor="w")

        row = tk.Frame(sec, bg=_BG)
        row.pack(fill=tk.X, pady=(8, 0))

        box = tk.Frame(row, bg=_CARD_2, highlightbackground=_BORDER,
                       highlightthickness=1)
        box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._text_input = tk.Text(
            box, height=2, bg=_CARD_2, fg=_MUTED, insertbackground=_TEXT,
            relief=tk.FLAT, font=(_UI, 10), wrap=tk.WORD, padx=8, pady=6,
            highlightthickness=0)
        self._text_input.pack(fill=tk.BOTH, expand=True)
        self._placeholder = "Type your question…"
        self._text_input.insert("1.0", self._placeholder)
        self._text_input.bind("<FocusIn>", self._on_focus_in)
        self._text_input.bind("<FocusOut>", self._on_focus_out)
        self._text_input.bind("<Return>", self._on_return)

        send = _Btn(row, "➤", self._send_input, width=48, height=48, radius=10,
                    fill=_ACCENT, hover=_ACCENT_HI, border=_ACCENT,
                    fg="#ffffff", fg_hover="#ffffff", font_size=13)
        send.pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(sec, text="Shift + Enter for new line", bg=_BG, fg=_FAINT,
                 font=(_UI, 8)).pack(anchor="w", pady=(4, 0))

    # ─────────────────────────────────────────────────────── Bottom bar ───
    def _build_bottom(self, b) -> None:
        bar = tk.Frame(b, bg=_TITLE_BG, height=60)
        bar.pack(fill=tk.X, pady=(16, 0))
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=_TITLE_BG)
        left.pack(side=tk.LEFT, padx=16)
        tk.Label(left, text="Next scan in", bg=_TITLE_BG, fg=_BLUE_TXT,
                 font=(_UI, 9)).pack(side=tk.LEFT, padx=(0, 8))
        self._ring = tk.Canvas(left, width=44, height=44, bg=_TITLE_BG,
                               highlightthickness=0)
        self._ring.pack(side=tk.LEFT)
        tk.Label(left, text="sec", bg=_TITLE_BG, fg=_MUTED,
                 font=(_UI, 9)).pack(side=tk.LEFT, padx=(8, 0))
        self._draw_ring("—", 1.0, _DIM)

        right = tk.Frame(bar, bg=_TITLE_BG)
        right.pack(side=tk.RIGHT, padx=16)
        self._now_btn = _Btn(right, "Analyze Now", self._trigger_now,
                             width=142, height=40, radius=10, icon="▶",
                             fill=_ACCENT, hover=_ACCENT_HI, border=_ACCENT,
                             fg="#ffffff", fg_hover="#ffffff", font_size=10,
                             bg=_TITLE_BG)
        self._now_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._pause_btn = _Btn(right, "Pause", self._toggle_pause,
                               width=104, height=40, radius=10, icon="‖",
                               fill=_CARD, hover=_DIM, border=_BORDER,
                               fg=_MUTED, fg_hover=_TEXT, font_size=10,
                               bg=_TITLE_BG)
        self._pause_btn.pack(side=tk.LEFT)

    def _draw_ring(self, text, frac, color) -> None:
        c = self._ring
        c.delete("all")
        c.create_oval(4, 4, 40, 40, outline=_DIM, width=3)
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            c.create_arc(4, 4, 40, 40, start=90, extent=-359.999 * frac,
                         style=tk.ARC, outline=color, width=3)
        c.create_text(22, 23, text=text, font=(_UI, 10, "bold"), fill=_TEXT)

    # ─────────────────────────────────────────────────────────── Tooltip ───
    def _add_tooltip(self, widget, text: str) -> None:
        tip = {"w": None}

        def show(_):
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + 52
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, bg="#1a2030", fg=_TEXT, font=(_UI, 8),
                     padx=6, pady=3).pack()
            tip["w"] = tw

        def hide(_):
            if tip["w"]:
                tip["w"].destroy()
                tip["w"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ─────────────────────────────────────────────────────── Input logic ───
    def _on_focus_in(self, _) -> None:
        if self._text_input.get("1.0", "end-1c") == self._placeholder:
            self._text_input.delete("1.0", tk.END)
            self._text_input.configure(fg=_TEXT)

    def _on_focus_out(self, _) -> None:
        if not self._text_input.get("1.0", "end-1c").strip():
            self._text_input.delete("1.0", tk.END)
            self._text_input.insert("1.0", self._placeholder)
            self._text_input.configure(fg=_MUTED)

    def _on_return(self, event) -> "str | None":
        if event.state & 0x1:            # Shift held → newline
            return None
        self._send_input()
        return "break"

    def _send_input(self) -> None:
        text = self._text_input.get("1.0", "end-1c").strip()
        if not text or text == self._placeholder:
            return
        self._text_input.delete("1.0", tk.END)
        self._text_input.insert("1.0", self._placeholder)
        self._text_input.configure(fg=_MUTED)
        self.root.focus()
        self._pq.put(text)
        self._manual.set()
        self._set_analyzing()

    def _send_quick(self, prompt: str) -> None:
        self._pq.put(prompt)
        self._manual.set()
        self._set_analyzing()

    def _set_analyzing(self) -> None:
        self._analyzing = True
        self._an_canvas.itemconfigure(self._an_status, text="analyzing…")

    # ───────────────────────────────────────────────────── Button actions ──
    def _toggle_pause(self) -> None:
        if self._pause.is_set():
            self._pause.clear()
            self._pause_btn.set_label("‖  Pause")
            self._pause_btn.set_colors(fill=_CARD, fg=_MUTED)
        else:
            self._pause.set()
            self._pause_btn.set_label("▶  Resume")
            self._pause_btn.set_colors(fill=_CARD, fg=_YELLOW)

    def _trigger_now(self) -> None:
        if not self._analyzing:
            self._manual.set()
            self._set_analyzing()

    def _on_close(self) -> None:
        self._stop.set()
        self.root.destroy()

    def _toggle_collapse(self) -> None:
        if self._collapsed:
            self._body.pack(fill=tk.BOTH, expand=True)
            if self._expanded_geo:
                self.root.geometry(self._expanded_geo)
            self._collapsed = False
        else:
            self._expanded_geo = self.root.geometry()
            self._body.pack_forget()
            self.root.update_idletasks()
            self.root.geometry(
                f"{self._W}x56+{self.root.winfo_x()}+{self.root.winfo_y()}")
            self._collapsed = True

    def _drag_start(self, e) -> None:
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e) -> None:
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f"+{x}+{y}")

    # ──────────────────────────────────────────────────── Display updates ──
    def _set_confidence(self, level, accent) -> None:
        n = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get((level or "").upper(), 0)
        for i, item in enumerate(self._seg_items):
            self._sc.itemconfigure(item, fill=accent if i < n else _DIM)

    def _show_result(self, result: AnalysisResult) -> None:
        if getattr(result, "is_chat", False):
            accent = _SIGNAL_COLORS["CHAT"]
            self._sc.itemconfigure(self._sig_item, text="CHAT",
                                   font=(_UI, 30, "bold"), fill=accent)
            self._sc.coords(self._arrow_item, 118, 84)
            self._sc.itemconfigure(self._arrow_item, text="", fill=accent)
            self._sc.itemconfigure(self._confval_item, text="—", fill=_MUTED)
            self._set_confidence(None, accent)
            self._reasoning_lbl.configure(
                text=result.reasoning or result.raw_response or "—", fg=_TEXT)
            self._exit_wrap.pack_forget()
        else:
            sig = (result.signal or "UNCLEAR").upper()
            accent = _SIGNAL_COLORS.get(sig, _DEFAULT_ACCENT)
            self._sc.itemconfigure(self._sig_item, text=sig,
                                   font=(_UI, 40, "bold"), fill=accent)
            bbox = self._sc.bbox(self._sig_item)
            ax = (bbox[2] + 14) if bbox else 150
            self._sc.coords(self._arrow_item, min(ax, 214), 84)
            self._sc.itemconfigure(self._arrow_item,
                                   text=_ARROWS.get(sig, ""), fill=accent)
            self._sc.itemconfigure(self._confval_item,
                                   text=(result.confidence or "—").upper(),
                                   fill=accent)
            self._set_confidence(result.confidence, accent)

            if sig == "UNKNOWN" and result.raw_response:
                self._reasoning_lbl.configure(
                    text=f"[parse failed]\n{result.raw_response[:400]}",
                    fg=_MUTED)
                self._exit_wrap.pack_forget()
            else:
                self._reasoning_lbl.configure(text=result.reasoning or "—",
                                              fg=_TEXT)
                ex = result.exit_condition
                if ex and ex != "UNKNOWN":
                    self._exit_body.configure(text=ex)
                    self._exit_wrap.pack(fill="x", anchor="nw")
                else:
                    self._exit_wrap.pack_forget()

            self._sig_history.appendleft((sig, (result.timestamp or "")[:5]))
            self._rebuild_history()

        self._sc.itemconfigure(self._time_item,
                               text=result.timestamp or "--:--:--")
        self._sc.itemconfigure(
            self._date_item,
            text=getattr(result, "date", "") or
            datetime.now().strftime("%b %d, %Y"))
        self._an_canvas.itemconfigure(self._an_status, text="")
        self._analyzing = False
        self._last_result = time.monotonic()

    def _show_error(self, msg: str) -> None:
        self._reasoning_lbl.configure(text=f"⚠  {msg}", fg=_RED)
        self._exit_wrap.pack_forget()
        self._an_canvas.itemconfigure(self._an_status, text="error")
        self._analyzing = False

    # ─────────────────────────────────────────────────────── Poll + run ────
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "result":
                    self._show_result(payload)
                elif kind == "analyzing":
                    self._set_analyzing()
                elif kind == "error":
                    self._show_error(str(payload))
        except queue.Empty:
            pass

        if self._pause.is_set():
            self._draw_ring("‖", 1.0, _YELLOW)
        elif self._analyzing:
            self._draw_ring("...", 1.0, _ACCENT)
        elif self._last_result is not None:
            elapsed = time.monotonic() - self._last_result
            secs = max(0, self._interval - int(elapsed))
            self._draw_ring(str(secs), secs / max(1, self._interval),
                            _GREEN if secs > 5 else _YELLOW)
        else:
            self._draw_ring("—", 1.0, _DIM)

        if not self._stop.is_set():
            self.root.after(300, self._poll)

    def _refresh_markets(self) -> None:
        if self._stop.is_set():
            return
        self._build_market_cards()
        self.root.after(60_000, self._refresh_markets)

    def run(self) -> None:
        self.root.update_idletasks()
        h = self._outer.winfo_reqheight() + 2
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{self._W}x{h}+{sw - self._W - 24}+20")
        self._expanded_geo = self.root.geometry()

        self.root.after(300, self._poll)
        self.root.after(30_000, self._refresh_markets)
        self.root.mainloop()
