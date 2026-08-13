"""Translucent momo - floating menu panel.

Apple-inspired redesign: light frosted surfaces, hairline dividers, capsule
buttons, a Mac-style segmented control, an iOS-style switch and circular
color swatches. Drawn entirely on a canvas so every box is rounded; the
Toplevel uses a chroma-key transparentcolor to drop the corners out.

Only dragging from the header moves the window, so the slider never shifts it.
"""
import base64
import io
import json
import os
import tkinter as tk

from .logo import LOGO_PNG_B64

from PIL import Image, ImageDraw

BG = "#f5f5f7"
TITLE = "#1d1d1f"
SECONDARY = "#86868b"
HAIRLINE = "#d9d9de"
TRACK = "#e3e3e8"
SEG_BG = "#ececf0"
SEG_ACTIVE = "#ffffff"
CLOSE_BG = "#e7e7ec"
SWITCH_OFF = "#e3e3e8"
SWITCH_ON = "#34c759"
KNOB = "#ffffff"
CHROMA = "#ff00fe"

THEMES = (
    ("Indigo", "#6c63ff"),
    ("Purple", "#af52de"),
    ("Pink", "#ff2d55"),
    ("Orange", "#ff9500"),
    ("Green", "#34c759"),
)
DEFAULT_THEME = THEMES[0][1]

WIDTH = 300
HEIGHT = 344
CORNER = 18
HEADER_H = 56

TRACK_X1 = 24
TRACK_X2 = WIDTH - 24
TRACK_TOP = 108
TRACK_BOT = 114
TRACK_MID = (TRACK_TOP + TRACK_BOT) // 2
THUMB_R = 9

PRESETS = (
    ("Clear", 100),
    ("Light", 75),
    ("Medium", 50),
    ("Dark", 25),
    ("Opaque", 0),
)

SEG_Y1 = 128
SEG_Y2 = 168
SEG_X1 = 28
SEG_W = 46
SEG_GAP = 3

SWATCH_Y = 218
SWATCH_R = 18

STEP = 5

FONT_NAME = "Segoe UI"


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _blend(color, target, t):
    rgb = _hex_to_rgb(color)
    tgt = _hex_to_rgb(target)
    return _rgb_to_hex(tuple(int(a + (b - a) * t) for a, b in zip(rgb, tgt)))


def _lighten(color, t=0.12):
    return _blend(color, "#ffffff", t)


def _settings_path():
    return os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "TaskbarGlass", "settings.json"
    )


def _load_theme():
    try:
        with open(_settings_path(), "r", encoding="utf-8") as file:
            saved = json.load(file)
        color = saved.get("theme")
        if color in {c for _, c in THEMES}:
            return color
    except Exception:
        pass
    return DEFAULT_THEME


def _save_theme(color):
    try:
        path = _settings_path()
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
        data["theme"] = color
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file)
    except Exception:
        pass


def round_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kwargs)


def load_logo(size=22):
    try:
        data = base64.b64decode(LOGO_PNG_B64)
        img = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
    except Exception:
        return None


def _swatch_photo(color, selected=False, accent=None, size=(SWATCH_R + 4) * 2):
    """High-res anti-aliased color disc, rendered 4x then downscaled."""
    s = 4
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if selected:
        draw.ellipse([(0, 0), (big - 1, big - 1)], fill=(255, 255, 255, 255))
    pad = 4
    draw.ellipse([(pad * s, pad * s), (big - pad * s - 1, big - pad * s - 1)],
                 fill=_hex_to_rgb(color) + (255,))
    if selected:
        draw.ellipse([(0, 0), (big - 1, big - 1)], fill=None,
                     outline=_hex_to_rgb(accent) + (255,), width=int(1.75 * s))
    small = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


class GlassMenu:
    def __init__(self, root, on_close, transparency=100, on_transparency=None,
                 startup_enabled=False, on_startup_toggle=None):
        self._on_close = on_close
        self._on_transparency = on_transparency
        self._on_startup_toggle = on_startup_toggle
        self._transparency = max(0, min(100, int(transparency)))
        self._startup = bool(startup_enabled)
        self._accent = _load_theme()
        self._accent_items = []
        self._btn_rects = {}
        self._hover_state = {}

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        try:
            self._win.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            pass
        self._win.configure(bg=CHROMA)
        self._win.geometry(f"{WIDTH}x{HEIGHT}")
        self._win.update_idletasks()
        self._position_above_taskbar()

        self._canvas = tk.Canvas(self._win, width=WIDTH, height=HEIGHT,
                                 bg=CHROMA, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)

        self._slider_active = False
        self._dragging = False
        self._drag_x = 0
        self._drag_y = 0

        self._build()

        self._canvas.tag_bind("btn_startup", "<Button-1>", lambda _e: self._toggle_startup())
        self._win.bind("<Escape>", lambda _e: self.close())
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)

    # -- accent tracking ----------------------------------------------------

    def _add_accent(self, pid):
        if pid is not None:
            self._accent_items.append(pid)

    def _remove_accent(self, pid):
        if pid in self._accent_items:
            self._accent_items.remove(pid)

    # -- drawing ------------------------------------------------------------

    def _build(self):
        c = self._canvas

        # Main box + hairline header.
        round_rect(c, 1, 1, WIDTH - 1, HEIGHT - 1, CORNER, fill=BG, outline="", width=0)
        c.create_rectangle(0, HEADER_H, WIDTH, HEADER_H + 1, fill=HAIRLINE, outline="")

        self._logo = load_logo()
        if self._logo is not None:
            c.create_image(20, HEADER_H // 2, image=self._logo)
        c.create_text(50, HEADER_H // 2, text="Translucent momo", anchor="w",
                      fill=TITLE, font=(FONT_NAME, 14, "bold"))
        self._make_pill(c, WIDTH - 42, 14, WIDTH - 14, 42, "\u2715", 11,
                        "btn_close", self.close, CLOSE_BG, "#3a3a3c")

        # Transparency.
        c.create_text(24, 76, text="Transparency", anchor="w",
                      fill=SECONDARY, font=(FONT_NAME, 11))
        self._value_text = c.create_text(WIDTH - 24, 78, text=f"{self._transparency}%",
                                         anchor="e", fill=self._accent,
                                         font=(FONT_NAME, 26, "bold"))
        self._add_accent(self._value_text)

        # Slider.
        self._track_base = round_rect(c, TRACK_X1, TRACK_TOP, TRACK_X2, TRACK_BOT, 3,
                                      fill=TRACK, outline="", width=0)
        self._redraw_slider()

        # Segmented presets.
        round_rect(c, 24, SEG_Y1, WIDTH - 24, SEG_Y2, 14, fill=SEG_BG, outline="", width=0)
        self._seg_labels = []
        for i, (name, value) in enumerate(PRESETS):
            x1 = SEG_X1 + i * (SEG_W + SEG_GAP)
            hit = c.create_rectangle(x1, SEG_Y1, x1 + SEG_W, SEG_Y2, fill="", outline="")
            c.addtag_withtag(f"btn_seg_{i}", hit)
            c.tag_bind(f"btn_seg_{i}", "<Button-1>",
                       lambda _e, v=value: self.set_transparency(v))
            self._seg_labels.append(c.create_text(
                x1 + SEG_W // 2, (SEG_Y1 + SEG_Y2) // 2, text=name,
                fill=SECONDARY, font=(FONT_NAME, 10, "bold")))
        self._seg_hl = None
        self._seg_active_label = None
        self._active_index = self._nearest_preset(self._transparency)
        self._draw_segment_highlight()

        # Theme.
        c.create_text(24, 186, text="Theme", anchor="w",
                      fill=SECONDARY, font=(FONT_NAME, 11))
        self._swatch_images = {}
        self._swatch_img_items = []
        for i, (name, color) in enumerate(THEMES):
            cx = 44 + i * 53
            img = _swatch_photo(color, False)
            self._swatch_images[i] = img
            item = c.create_image(cx, SWATCH_Y, image=img)
            self._swatch_img_items.append(item)
            c.addtag_withtag(f"btn_theme_{i}", item)
            c.tag_bind(f"btn_theme_{i}", "<Button-1>", lambda _e, n=i: self.select_theme(n))
        self._draw_swatch_rings()
        c.create_text(WIDTH // 2, 258, text=THEMES[self._theme_index()][0],
                      fill=SECONDARY, font=(FONT_NAME, 11))

        # Divider before startup.
        c.create_rectangle(24, 270, WIDTH - 24, 271, fill=HAIRLINE, outline="")

        # Startup switch.
        c.create_text(24, 298, text="Launch at startup", anchor="w",
                      fill=TITLE, font=(FONT_NAME, 11))
        self._startup_ids = None
        self._make_switch(230, 284, 276, 312)

        c.create_text(WIDTH // 2, 330, text="Version 1.0", anchor="center",
                      fill="#b0b0b5", font=(FONT_NAME, 10))

    def _theme_index(self):
        for i, (_, color) in enumerate(THEMES):
            if color == self._accent:
                return i
        return 0

    def _nearest_preset(self, value):
        best = 0
        for i, (_, v) in enumerate(PRESETS):
            if abs(v - value) < abs(PRESETS[best][1] - value):
                best = i
        return best

    def _draw_segment_highlight(self):
        c = self._canvas
        if self._seg_hl is not None:
            c.delete(self._seg_hl)
        if self._seg_active_label is not None:
            self._remove_accent(self._seg_active_label)
            c.itemconfig(self._seg_active_label, fill=SECONDARY)
        x1 = SEG_X1 + self._active_index * (SEG_W + SEG_GAP)
        self._seg_hl = round_rect(c, x1 + 2, SEG_Y1 + 4, x1 + SEG_W - 2, SEG_Y2 - 4,
                                  16, fill=SEG_ACTIVE, outline="", width=0)
        self._seg_active_label = self._seg_labels[self._active_index]
        self._add_accent(self._seg_active_label)
        c.itemconfig(self._seg_active_label, fill=self._accent)
        c.tag_raise(self._seg_active_label)

    def _draw_swatch_rings(self):
        for i, (_, color) in enumerate(THEMES):
            self._swatch_images[i] = _swatch_photo(
                color, color == self._accent, accent=self._accent)
            self._canvas.itemconfig(self._swatch_img_items[i], image=self._swatch_images[i])

    def _make_pill(self, canvas, x1, y1, x2, y2, text, size, tag, command,
                   base, text_fill):
        r = (y2 - y1) / 2
        old = self._btn_rects.get(tag)
        if old is not None:
            self._remove_accent(old)
        item = round_rect(canvas, x1, y1, x2, y2, r, fill=base, outline="", width=0)
        self._btn_rects[tag] = item
        label = canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                   text=text, fill=text_fill,
                                   font=(FONT_NAME, size, "bold"))
        for i in (item, label):
            canvas.addtag_withtag(tag, i)
        canvas.tag_bind(tag, "<Button-1>", lambda _e, f=command: f())
        canvas.tag_bind(item, "<Enter>", lambda _e, t=tag, b=base: self._hover(t, True, b))
        canvas.tag_bind(item, "<Leave>", lambda _e, t=tag, b=base: self._hover(t, False, b))
        return (x1, y1, x2, y2)

    def _hover(self, tag, on, base):
        if self._hover_state.get(tag) == on:
            return
        self._hover_state[tag] = on
        pid = self._btn_rects.get(tag)
        if pid is not None:
            try:
                self._canvas.itemconfig(pid, fill=_lighten(base) if on else base)
            except tk.TclError:
                pass

    def _make_switch(self, x1, y1, x2, y2):
        c = self._canvas
        if self._startup_ids:
            for pid in self._startup_ids:
                c.delete(pid)
        half = (y2 - y1) / 2
        knob = (y2 - y1) - 4
        vcenter = (y1 + y2) / 2
        track = round_rect(c, x1, y1, x2, y2, half,
                           fill=SWITCH_ON if self._startup else SWITCH_OFF,
                           outline="", width=0)
        if self._startup:
            knob_cx = x2 - 2 - knob / 2
        else:
            knob_cx = x1 + 2 + knob / 2
        knob_id = c.create_oval(knob_cx - knob / 2, vcenter - knob / 2,
                                knob_cx + knob / 2, vcenter + knob / 2,
                                fill=KNOB, outline="", width=0)
        for pid in (track, knob_id):
            c.addtag_withtag("btn_startup", pid)
        self._startup_ids = [track, knob_id]

    def _redraw_slider(self):
        c = self._canvas
        frac = self._transparency / 100.0
        thumb_x = TRACK_X1 + (TRACK_X2 - TRACK_X1) * frac
        if hasattr(self, "_track_fill"):
            self._remove_accent(self._track_fill)
            c.delete(self._track_fill)
            del self._track_fill
        if hasattr(self, "_thumb"):
            c.delete(self._thumb)
            del self._thumb
        self._track_fill = round_rect(c, TRACK_X1, TRACK_TOP, thumb_x, TRACK_BOT, 3,
                                      fill=self._accent, outline="", width=0)
        self._add_accent(self._track_fill)
        self._thumb = c.create_oval(thumb_x - THUMB_R, TRACK_MID - THUMB_R,
                                    thumb_x + THUMB_R, TRACK_MID + THUMB_R,
                                    fill=KNOB, outline="#d5d5da", width=1)
        c.tag_raise(self._thumb)

    # -- theme --------------------------------------------------------------

    def select_theme(self, index):
        color = THEMES[index][1]
        if color == self._accent:
            return
        self._accent = color
        for pid in list(self._accent_items):
            try:
                self._canvas.itemconfig(pid, fill=color)
            except tk.TclError:
                self._remove_accent(pid)
        self._redraw_slider()
        self._draw_swatch_rings()
        _save_theme(color)

    # -- state --------------------------------------------------------------

    def _nudge(self, delta):
        self.set_transparency(max(0, min(100, self._transparency + delta)))

    def set_transparency(self, value):
        value = max(0, min(100, int(value)))
        if value == self._transparency:
            return
        self._transparency = value
        self._canvas.itemconfig(self._value_text, text=f"{value}%")
        self._redraw_slider()
        index = self._nearest_preset(value)
        if index != self._active_index:
            self._active_index = index
            self._draw_segment_highlight()
        if self._on_transparency is not None:
            self._on_transparency(value)

    def _toggle_startup(self):
        self._startup = not self._startup
        self._make_switch(230, 284, 276, 312)
        if self._on_startup_toggle is not None:
            self._on_startup_toggle(self._startup)

    # -- input --------------------------------------------------------------

    def _slider_region(self, x, y):
        if not (TRACK_TOP - 12 <= y <= TRACK_BOT + 14):
            return False
        pad = THUMB_R + 2
        return TRACK_X1 - pad <= x <= TRACK_X2 + pad

    def _on_press(self, event):
        current = self._canvas.find_withtag("current")
        tags = set()
        for item in current:
            tags.update(self._canvas.gettags(item))
        if any(t.startswith("btn_") for t in tags):
            return
        if self._slider_region(event.x, event.y):
            self._slider_active = True
            self._value_from_x(event.x)
            return
        if event.y < HEADER_H:
            self._dragging = True
            self._drag_x = event.x_root - self._win.winfo_x()
            self._drag_y = event.y_root - self._win.winfo_y()

    def _on_motion(self, event):
        if self._slider_active:
            self._value_from_x(event.x)
        elif self._dragging:
            self._win.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _on_release(self, _event):
        self._slider_active = False
        self._dragging = False

    def _value_from_x(self, x):
        x = max(TRACK_X1, min(TRACK_X2, x))
        frac = (x - TRACK_X1) / (TRACK_X2 - TRACK_X1)
        self.set_transparency(round(frac * 100))

    # -- window -------------------------------------------------------------

    def _position_above_taskbar(self):
        screen_w = self._win.winfo_screenwidth()
        screen_h = self._win.winfo_screenheight()
        x = screen_w - WIDTH - 20
        y = screen_h - HEIGHT - 60
        self._win.geometry(f"+{x}+{y}")

    def close(self):
        try:
            self._on_close()
        finally:
            try:
                self._win.destroy()
            except Exception:
                pass