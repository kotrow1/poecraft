"""
PoE Craft Guard — следит за подсказкой предмета и блокирует левый клик,
когда выпали выбранные моды. Сам ничего не нажимает.

Горячие клавиши настраиваются в «⚙ Настройки» (сохраняются в config.json). По умолчанию:
  F7  — импорт базы из буфера (сначала Ctrl+Alt+C на предмете в игре)
  F8  — калибровка: наведи курсор на предмет в слоте крафта и нажми клавишу
  F6  — включить / выключить охрану (и снять удержание блока)
  F10 — тест: распознать подсказку прямо сейчас и показать результат
"""
import asyncio
import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import mss
import pyperclip
try:
    import sv_ttk
except ImportError:
    sv_ttk = None
import winocr
import winsound
from PIL import Image, ImageChops, ImageOps
from pynput import keyboard, mouse

from moddb import ModDB, Requirement, check_rule, match_lines, parse_item_text
import updater

OCR_LANG = "en-US"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
HOTKEY_NAMES = {"import": "Импорт", "calibrate": "Калибровка", "arm": "Охрана вкл/выкл", "test": "Тест OCR"}
HOTKEYS = {"import": "f7", "calibrate": "f8", "arm": "f6", "test": "f10"}


CONFIG = {"hotkeys": HOTKEYS, "settings": {}, "region": None, "presets": {}, "update": {}}
UPDATE_EVERY = 6 * 3600          # автопроверка обновлений не чаще, секунд


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    HOTKEYS.update(data.get("hotkeys", {}))
    for k in ("settings", "presets", "update"):
        CONFIG[k] = data.get(k, {}) or {}
    CONFIG["region"] = data.get("region")


def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def hk(action):
    return HOTKEYS[action].upper()


def key_name(key):
    if isinstance(key, keyboard.Key):
        return key.name.lower()
    if getattr(key, "char", None):
        return key.char.lower()
    vk = getattr(key, "vk", None)
    return f"vk{vk}" if vk else ""


WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_SHIFTS = (0x10, 0xA0, 0xA1)
# область поиска подсказки вокруг курсора: подсказка бывает над предметом (тайник)
# и сбоку/ниже (станок Harvest, другие окна крафта)
REGION_W, REGION_UP, REGION_DOWN = 1700, 800, 650
OCR_SCALE = 1.5          # увеличение картинки для OCR (больше — точнее, но медленнее)
CHANGE_THRESHOLD = 0.012  # доля изменившихся точек, чтобы считать подсказку новой
PENDING_TIMEOUT = 1.5    # пошаговый режим: если подсказка не сменилась за это время — клик снова разрешён


class State:
    def __init__(self):
        self.region = None          # dict(left, top, width, height)
        self.ctx = None
        self.reqs = []
        self.mode = "all"
        self.inf_min = None
        self.inf_max = None
        self.armed = False
        self.block_seconds = 6.0
        self.hold_until_f9 = False
        self.block_until = 0.0
        self.block_hold = False
        self.suppressed_down = False
        self.running = True
        self.force_ocr = False
        self.step_mode = True       # следующий клик только после проверки результата предыдущего
        self.pending = False
        self.pending_since = 0.0
        self.pending_key = None
        self.last_key = None
        self.shift_lock = True      # Shift-замок разрешён
        self.shift_down = False     # Shift физически нажат
        self.latched = False        # Shift удерживается программой (его отпускание не передаётся)
        self.crop = None            # (x0, y0, x1, y1) внутри region — только подсказка
        self.lock = threading.Lock()

    def blocked(self):
        return self.block_hold or time.time() < self.block_until


S = State()
UI = queue.Queue()


def log(msg):
    UI.put(("log", time.strftime("%H:%M:%S ") + msg))


# ---------------- блокировка левого клика ----------------
def mouse_filter(msg, data):
    if msg == WM_LBUTTONDOWN:
        now = time.time()
        if S.pending and now - S.pending_since > PENDING_TIMEOUT:
            S.pending = False
        if S.blocked() or (S.step_mode and S.pending):
            S.suppressed_down = True
            mouse_listener.suppress_event()
        else:
            S.suppressed_down = False
            if S.armed and S.shift_lock and S.shift_down and not S.latched:
                S.latched = True
                log("Shift-замок: Shift удерживается — можно отпустить и кликать только ЛКМ")
            if S.armed and S.step_mode and S.region and S.ctx:
                S.pending, S.pending_since, S.pending_key = True, now, S.last_key
    elif msg == WM_LBUTTONUP and S.suppressed_down:
        S.suppressed_down = False
        mouse_listener.suppress_event()


mouse_listener = mouse.Listener(win32_event_filter=mouse_filter)


# ---------------- Shift-замок ----------------
# Программа ничего не нажимает: пока замок активен, она просто не передаёт игре
# отпускание Shift. Отпускается замок новым нажатием Shift или выключением охраны.
def kb_filter(msg, data):
    if data.vkCode not in VK_SHIFTS:
        return
    if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
        if S.latched and not S.shift_down:     # новое нажатие Shift — снять замок
            S.latched = False
            log("Shift-замок снят")
        S.shift_down = True
    elif msg in (WM_KEYUP, WM_SYSKEYUP):
        S.shift_down = False
        if S.latched:
            kb_listener.suppress_event()


def release_latch():
    """Снять замок при выключении охраны/выходе: отдаём системе отпускание Shift."""
    if S.latched:
        S.latched = False
        try:
            keyboard.Controller().release(keyboard.Key.shift)
        except Exception:
            pass
        log("Shift-замок снят, Shift отпущен")


kb_listener = keyboard.Listener(on_press=lambda k: on_key(k), win32_event_filter=kb_filter)


# ---------------- горячие клавиши ----------------
def calibrate():
    x, y = mouse.Controller().position
    with mss.mss() as sct:
        mons = sct.monitors[1:] or sct.monitors
    mon = next((m for m in mons if m["left"] <= x < m["left"] + m["width"]
                and m["top"] <= y < m["top"] + m["height"]), mons[0])
    left = max(mon["left"], int(x - REGION_W / 2))
    top = max(mon["top"], int(y - REGION_UP))
    right = min(mon["left"] + mon["width"], int(x + REGION_W / 2))
    bottom = min(mon["top"] + mon["height"], int(y + REGION_DOWN))
    S.region = dict(left=left, top=top, width=right - left, height=bottom - top)
    S.crop = None
    CONFIG["region"] = S.region
    save_config()
    log(f"Калибровка: курсор {x},{y}; область {S.region}")


def on_key_action(action):
    if action == "import":
        UI.put(("import", None))
    elif action == "calibrate":
        calibrate()
    elif action == "arm":
        if S.blocked():                  # первое нажатие после попадания — только снять блок
            S.block_hold = False
            S.block_until = 0
            log("Блок снят, охрана остаётся включённой")
            return
        S.armed = not S.armed
        if not S.armed:
            release_latch()
        log("ОХРАНА ВКЛЮЧЕНА" if S.armed else "Охрана выключена")
    elif action == "test":
        S.force_ocr = True


def on_key(key):
    name = key_name(key)
    if not name:
        return
    if UI_CAPTURE[0]:                       # окно ждёт новую клавишу для назначения
        UI.put(("bind", name))
        return
    for action, k in HOTKEYS.items():
        if name == k:
            on_key_action(action)
            return


UI_CAPTURE = [None]      # действие, для которого ждём нажатие новой клавиши


# ---------------- OCR ----------------
def prepare(img):
    g = ImageOps.grayscale(img)
    g = g.resize((int(g.width * OCR_SCALE), int(g.height * OCR_SCALE)), Image.BILINEAR)
    return ImageOps.invert(ImageOps.autocontrast(g))


class FastOCR:
    """Прямой вызов Windows OCR: движок создаётся один раз, без лишних преобразований."""
    def __init__(self, lang):
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
        self.DataWriter, self.SoftwareBitmap, self.fmt = DataWriter, SoftwareBitmap, BitmapPixelFormat.RGBA8
        if not OcrEngine.is_language_supported(Language(lang)):
            raise RuntimeError("в Windows не установлен пакет распознавания для английского (en-US)")
        self.engine = OcrEngine.try_create_from_language(Language(lang))
        self.loop = asyncio.new_event_loop()

    async def _run(self, op):
        return await op

    def lines(self, img):
        """img (уже подготовленная) -> [(текст, (x0, y0, x1, y1))] в координатах img"""
        rgba = img.convert("RGBA")
        w = self.DataWriter()
        w.write_bytes(rgba.tobytes())
        sb = self.SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), self.fmt, rgba.width, rgba.height)
        res = self.loop.run_until_complete(self._run(self.engine.recognize_async(sb)))
        out = []
        for line in res.lines:
            xs0, ys0, xs1, ys1 = [], [], [], []
            for word in line.words:
                r = word.bounding_rect
                xs0.append(r.x); ys0.append(r.y); xs1.append(r.x + r.width); ys1.append(r.y + r.height)
            box = (min(xs0), min(ys0), max(xs1), max(ys1)) if xs0 else None
            out.append((line.text, box))
        return out


_OCR = [None]


def ocr_lines(img):
    """img — исходный кадр. Возвращает [(текст, box в координатах кадра)]."""
    prepared = prepare(img)
    try:
        if _OCR[0] is None:
            _OCR[0] = FastOCR(OCR_LANG)
        raw = _OCR[0].lines(prepared)
    except ImportError:                      # запасной путь через winocr
        r = winocr.recognize_pil_sync(prepared, OCR_LANG)
        raw = [(l["text"], None) for l in r["lines"]]
    k = OCR_SCALE
    return [(t, None if b is None else (b[0] / k, b[1] / k, b[2] / k, b[3] / k)) for t, b in raw]


def signature(img):
    return ImageOps.grayscale(img).resize((160, 120)).point(lambda p: p // 16 * 16)


def changed(a, b):
    if a is None or b is None:
        return True
    diff = ImageChops.difference(a, b).point(lambda p: 255 if p > 0 else 0)
    return diff.histogram()[255] / (a.width * a.height) > CHANGE_THRESHOLD


def beep():
    for f in (1500, 1900, 1500):
        winsound.Beep(f, 120)


CROP_PAD_X, CROP_PAD_UP, CROP_PAD_DOWN = 260, 200, 200
EDGE = 10   # строка у края обрезки — подсказка могла вырасти, пересчитать по полному кадру


def crop_from(lines, matches, w, h):
    """Обрезка по строкам найденных модов с запасом: подсказка растёт вверх и вширь."""
    wanted = {l for m in matches for l in m.lines}
    boxes = [b for t, b in lines if b and t.strip() in wanted]
    if not boxes:
        return None
    x0 = max(0, int(min(b[0] for b in boxes) - CROP_PAD_X))
    y0 = max(0, int(min(b[1] for b in boxes) - CROP_PAD_UP))
    x1 = min(w, int(max(b[2] for b in boxes) + CROP_PAD_X))
    y1 = min(h, int(max(b[3] for b in boxes) + CROP_PAD_DOWN))
    return (x0, y0, x1, y1)


def worker():
    """Захват -> при заметном изменении сразу OCR. После первого удачного распознавания
    захватываем и распознаём только область подсказки — так в разы быстрее."""
    sct = mss.mss()
    done_sig, last_text, area_key = None, None, None
    while S.running:
        if not S.region or not S.ctx:
            time.sleep(0.05)
            continue
        R, crop = S.region, S.crop
        if crop:
            area = dict(left=R["left"] + crop[0], top=R["top"] + crop[1],
                        width=crop[2] - crop[0], height=crop[3] - crop[1])
        else:
            area = R
        if (crop, R["left"], R["top"]) != area_key:         # сменилась область — сравнивать не с чем
            area_key, done_sig = (crop, R["left"], R["top"]), None
        t0 = time.perf_counter()
        t_grab = time.time()
        shot = sct.grab(area)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        sig = signature(img)
        force, S.force_ocr = S.force_ocr, False
        if not force and not changed(sig, done_sig):
            time.sleep(0.003)
            continue
        done_sig = sig
        t1 = time.perf_counter()
        try:
            lines = ocr_lines(img)
        except Exception as e:
            log(f"Ошибка OCR: {e}")
            time.sleep(1)
            continue
        texts = [t for t, _ in lines]
        t2 = time.perf_counter()
        matches = match_lines(S.ctx, texts)
        hit = check_rule(matches, S.reqs, S.mode, S.inf_min, S.inf_max)
        if hit and S.armed and not S.blocked():
            S.block_until = time.time() + S.block_seconds
            if S.hold_until_f9:
                S.block_hold = True
            UI.put(("hit", None))
            threading.Thread(target=beep, daemon=True).start()
        t3 = time.perf_counter()

        # обрезка: нашли моды на полном кадре — дальше смотрим только туда;
        # на обрезке ничего не нашли — возвращаемся к полному кадру
        if crop is None and matches:
            new = crop_from(lines, matches, R["width"], R["height"])
            if new:
                S.crop = new
                log(f"Область сужена до подсказки: {new[2]-new[0]}x{new[3]-new[1]}")
        elif crop is not None:
            w, h = crop[2] - crop[0], crop[3] - crop[1]
            wanted = {l for m in matches for l in m.lines}
            near_edge = any(b and (b[1] < EDGE or b[3] > h - EDGE or b[0] < EDGE or b[2] > w - EDGE)
                            for t, b in lines if t.strip() in wanted)
            if not matches or near_edge:
                S.crop = None                     # следующий кадр — снова полный поиск

        timing = (f"[захват {1000*(t1-t0):.0f} + OCR {1000*(t2-t1):.0f} + разбор {1000*(t3-t2):.0f}"
                  f" = {1000*(t3-t0):.0f} мс]")
        if force:
            log("ТЕСТ OCR, сырые строки:\n    " + "\n    ".join(texts))
        text_key = "|".join(m.describe() for m in matches)
        repeat = text_key == last_text
        last_text = text_key
        S.last_key = text_key
        if S.pending and t_grab > S.pending_since and text_key != S.pending_key and matches:
            S.pending = False                    # результат клика проверен — следующий клик разрешён
        if (matches and not repeat) or hit:
            UI.put(("roll", (matches, hit, 1000 * (t3 - t0))))
        if (matches and not repeat) or force or hit:
            log(timing + (" СОВПАЛО! " if hit else " ") + "; ".join(m.describe() for m in matches))


# ---------------- окно ----------------
GREEN, AMBER, RED, GRAY = "#4caf50", "#f9a825", "#e53935", "#8a8a8a"
INF_OPTIONS = [("любое", None, None), ("ровно 1", 1, 1), ("ровно 2", 2, 2), ("ровно 3", 3, 3),
               ("не меньше 1", 1, None), ("не меньше 2", 2, None), ("ни одного", 0, 0)]
DEFAULT_SETTINGS = {"block_seconds": 6, "hold": False, "step": True, "shift_lock": True, "on_top": False,
                    "auto_update": True}


def fmt_chance(c):
    if c <= 0:
        return "—"
    return "<0.1%" if c < 0.001 else f"{c * 100:.1f}%"


def group_tags(mods):
    seen = []
    for m in mods:
        for t in m.tags:
            if t not in seen:
                seen.append(t)
    return seen


class App:
    def __init__(self, root):
        self.root = root
        self.db = None
        self.import_err = ("", 0.0)   # текст ошибки импорта и до какого времени её показывать
        self.selected = {}            # group_key -> tier
        self.rows = {}                # iid -> group_key
        self.settings = dict(DEFAULT_SETTINGS, **CONFIG["settings"])
        root.title(f"PoE Craft Guard {updater.VERSION}")
        self.upd_busy = ""            # "" / "check" / "install"
        self.upd_info = None          # последний выпуск с GitHub (dict из updater.check)
        self.about = None             # виджеты вкладки «О программе», пока окно настроек открыто
        root.geometry("1300x780")
        root.minsize(980, 620)
        if sv_ttk:
            sv_ttk.set_theme("dark")
        style = ttk.Style()
        style.configure("Treeview", rowheight=26)
        style.configure("Step.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Hint.TLabel", foreground=GRAY)
        style.configure("Head.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Link.TLabel", font=("Segoe UI", 10, "underline"), foreground="#64b5f6")
        root.attributes("-topmost", self.settings["on_top"])

        self.build_steps()
        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        right = ttk.Frame(body, padding=(8, 0, 0, 0))
        body.add(left, weight=3)
        body.add(right, weight=2)
        self.build_pool(left)
        self.build_target(right)
        self.build_bottom()
        self.make_overlay()
        root.after(5000, self.auto_check)
        self.apply_settings()

        if CONFIG.get("region"):
            S.region = CONFIG["region"]
        threading.Thread(target=self.load_db, daemon=True).start()
        root.after(50, self.poll)

    # ======== шаги ========
    def build_steps(self):
        bar = ttk.Frame(self.root, padding=10)
        bar.pack(fill="x")
        self.steps = {}
        specs = [("base", "1  База", self.do_import), ("calib", "2  Калибровка", None),
                 ("target", "3  Цель", None), ("guard", "4  Охрана", self.toggle_guard)]
        for i, (key, title, cmd) in enumerate(specs):
            f = ttk.Frame(bar, style="Card.TFrame", padding=(12, 8))
            f.grid(row=0, column=i, sticky="nsew", padx=4)
            bar.columnconfigure(i, weight=1, uniform="steps")
            t = ttk.Label(f, text=title, style="Step.TLabel")
            t.pack(anchor="w")
            d = ttk.Label(f, text="", style="Hint.TLabel")
            d.pack(anchor="w")
            if cmd:
                for w in (f, t, d):
                    w.bind("<Button-1>", lambda e, c=cmd: c())
                    w.configure(cursor="hand2")
            self.steps[key] = (t, d)
        self.base_btn = ttk.Button(bar, text="⟳ Новая база", command=self.do_import)
        self.base_btn.grid(row=0, column=4, sticky="ns", padx=(8, 0))
        gear = ttk.Button(bar, text="⚙ Настройки", command=self.open_settings)
        gear.grid(row=0, column=5, sticky="ns", padx=(8, 0))

    def set_step(self, key, done, detail, color=None):
        t, d = self.steps[key]
        if len(detail) > 46:
            detail = detail[:45] + "…"
        t.configure(foreground=color or (GREEN if done else AMBER))
        d.configure(text=detail)

    # ======== пул модов ========
    def build_pool(self, parent):
        ttk.Label(parent, text="Моды базы", style="Head.TLabel").pack(anchor="w")
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 4))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self.fill())
        e = ttk.Entry(row, textvariable=self.search)
        e.pack(side="left", fill="x", expand=True)
        self.hide_na = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="скрыть недоступные", variable=self.hide_na,
                        command=self.fill).pack(side="left", padx=(8, 0))

        chips = ttk.Frame(parent)
        chips.pack(fill="x", pady=(0, 6))
        self.gen_f = tk.StringVar(value="all")
        for val, txt in (("all", "Все"), ("prefix", "Префиксы"), ("suffix", "Суффиксы")):
            ttk.Radiobutton(chips, text=txt, value=val, variable=self.gen_f, style="Toggle.TButton",
                            command=self.fill).pack(side="left", padx=(0, 4))
        self.inf_f = tk.StringVar(value="all")
        self.inf_chips = ttk.Frame(parent)
        self.inf_chips.pack(fill="x", pady=(0, 6))
        self.rebuild_inf_chips([])

        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=("add", "chance", "tiers", "tags"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Мод (лучший доступный тир)", anchor="w")
        self.tree.heading("add", text="")
        self.tree.heading("chance", text="Шанс", anchor="e")
        self.tree.heading("tiers", text="Тиры", anchor="w")
        self.tree.heading("tags", text="Теги", anchor="w")
        self.tree.column("#0", width=380, stretch=True)
        self.tree.column("add", width=36, anchor="center", stretch=False)
        self.tree.column("chance", width=62, anchor="e", stretch=False)
        self.tree.column("tiers", width=72, anchor="w", stretch=False)
        self.tree.column("tags", width=170, anchor="w", stretch=False)
        self.tree.tag_configure("section", foreground=GRAY)
        self.tree.tag_configure("na", foreground="#666666")
        self.tree.tag_configure("chosen", foreground=GREEN)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", lambda e: self.toggle_row(self.tree.identify_row(e.y)))
        self.tree.bind("<Return>", lambda e: self.toggle_row(self.tree.focus()))
        ttk.Label(parent, text="Клик по ＋ или двойной клик — добавить в цель / убрать.  "
                               "Шанс — доля веса мода среди префиксов (суффиксов) этой базы; "
                               "поиск ищет и по тегам (attack, life, critical...)",
                  style="Hint.TLabel", wraplength=640, justify="left").pack(anchor="w", pady=(4, 0))

    def rebuild_inf_chips(self, influences):
        for w in self.inf_chips.winfo_children():
            w.destroy()
        opts = [("all", "Любое")] + [("none", "Обычные")] + [(i, i) for i in influences] + \
               ([("elev", "Элевейтед")] if influences else [])
        if self.inf_f.get() not in [v for v, _ in opts]:
            self.inf_f.set("all")
        for val, txt in opts:
            ttk.Radiobutton(self.inf_chips, text=txt, value=val, variable=self.inf_f,
                            style="Toggle.TButton", command=self.fill).pack(side="left", padx=(0, 4))

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        if not S.ctx:
            return
        q = self.search.get().lower().strip()
        gen_f, inf_f = self.gen_f.get(), self.inf_f.get()
        sections = {}
        for key, mods in S.ctx.groups.items():
            typ, inf, gen, elev = key
            if gen_f != "all" and gen != gen_f:
                continue
            if inf_f == "none" and inf:
                continue
            if inf_f == "elev" and not elev:
                continue
            if inf_f not in ("all", "none", "elev") and inf != inf_f:
                continue
            avail = [m for m in mods if m.spawnable]
            if self.hide_na.get() and not avail and key not in self.selected:
                continue
            hay = " ".join(m.text.lower() for m in mods) + " " + " ".join(t for m in mods for t in m.tags)
            if q and q not in hay and q not in inf.lower():
                continue
            sec = (gen, inf, elev)
            sections.setdefault(sec, []).append((key, mods, avail))
        order = lambda sec: (sec[0] != "prefix", sec[1] != "", sec[2], sec[1])
        for sec in sorted(sections, key=order):
            gen, inf, elev = sec
            title = ("Префиксы" if gen == "prefix" else "Суффиксы") + " · " + (inf or "обычные") + \
                    (" · элевейтед" if elev else "")
            items = sorted(sections[sec], key=lambda x: (x[0] not in self.selected,
                                                         -sum(m.chance for m in x[1]), (x[2] or x[1])[0].text))
            pid = self.tree.insert("", "end", text=f"{title}  ({len(items)})", open=True, tags=("section",))
            for key, mods, avail in items:
                best = (avail or mods)[0]
                chosen = key in self.selected
                if not avail:
                    tiers = f"ilvl {mods[-1].level}+"
                elif best.tier == mods[-1].tier:
                    tiers = f"T{best.tier}"
                else:
                    tiers = f"T{best.tier}–T{mods[-1].tier}"
                tags = ("chosen",) if chosen else (() if avail else ("na",))
                iid = self.tree.insert(pid, "end", text=best.text,
                                       values=("✓" if chosen else "＋", fmt_chance(sum(m.chance for m in mods)),
                                               tiers, ", ".join(group_tags(mods))), tags=tags)
                self.rows[iid] = key

    def on_tree_click(self, e):
        if self.tree.identify_column(e.x) == "#1":
            self.toggle_row(self.tree.identify_row(e.y))

    def toggle_row(self, iid):
        key = self.rows.get(iid)
        if key is None:
            return
        if key in self.selected:
            del self.selected[key]
        else:
            mods = S.ctx.groups[key]
            avail = [m for m in mods if m.spawnable] or mods
            self.selected[key] = avail[0].tier            # по умолчанию — лучший доступный тир
        self.update_row(key)
        self.render_cards()
        self.sync()
        self.update_steps()

    def update_row(self, key):
        """Обновить одну строку списка, не перестраивая его (не сбивая прокрутку)."""
        for iid, k in self.rows.items():
            if k == key:
                mods = S.ctx.groups[key]
                chosen = key in self.selected
                avail = any(m.spawnable for m in mods)
                vals = list(self.tree.item(iid, "values"))
                vals[0] = "✓" if chosen else "＋"
                self.tree.item(iid, values=vals,
                               tags=("chosen",) if chosen else (() if avail else ("na",)))

    # ======== цель ========
    def build_target(self, parent):
        ttk.Label(parent, text="Цель", style="Head.TLabel").pack(anchor="w")
        cond = ttk.Frame(parent)
        cond.pack(fill="x", pady=(6, 2))
        ttk.Label(cond, text="Блокировать, когда есть").pack(side="left")
        self.mode = tk.StringVar(value="все выбранные")
        cb = ttk.Combobox(cond, textvariable=self.mode, state="readonly", width=16,
                          values=["все выбранные", "любой из выбранных"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.sync())
        cond2 = ttk.Frame(parent)
        cond2.pack(fill="x", pady=(2, 6))
        ttk.Label(cond2, text="Модов влияния на предмете").pack(side="left")
        self.inf_count = tk.StringVar(value="любое")
        cb2 = ttk.Combobox(cond2, textvariable=self.inf_count, state="readonly", width=14,
                           values=[o[0] for o in INF_OPTIONS])
        cb2.pack(side="left", padx=6)
        cb2.bind("<<ComboboxSelected>>", lambda e: self.sync())

        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        self.cards_canvas = tk.Canvas(outer, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self.cards_canvas.yview)
        self.cards = ttk.Frame(self.cards_canvas)
        self.cards.bind("<Configure>", lambda e: self.cards_canvas.configure(
            scrollregion=self.cards_canvas.bbox("all")))
        self.cards_win = self.cards_canvas.create_window((0, 0), window=self.cards, anchor="nw")
        self.cards_canvas.bind("<Configure>", lambda e: self.cards_canvas.itemconfigure(
            self.cards_win, width=e.width))
        self.cards_canvas.configure(yscrollcommand=sb.set)
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        pre = ttk.Frame(parent)
        pre.pack(fill="x", pady=(8, 0))
        ttk.Label(pre, text="Пресет").pack(side="left")
        self.preset = tk.StringVar()
        self.preset_cb = ttk.Combobox(pre, textvariable=self.preset, width=22)
        self.preset_cb.pack(side="left", padx=6, fill="x", expand=True)
        self.preset_cb.bind("<<ComboboxSelected>>", lambda e: self.load_preset())
        ttk.Button(pre, text="Сохранить", command=self.save_preset).pack(side="left")
        ttk.Button(pre, text="Удалить", command=self.delete_preset).pack(side="left", padx=(4, 0))
        self.update_presets()

    def render_cards(self):
        for w in self.cards.winfo_children():
            w.destroy()
        if not self.selected:
            ttk.Label(self.cards, text="Добавь моды из списка слева — нажми ＋ у нужного мода.",
                      style="Hint.TLabel", wraplength=380, justify="left").pack(anchor="w", pady=12)
            return
        for key, tier in self.selected.items():
            mods = S.ctx.groups.get(key) if S.ctx else None
            if not mods:
                continue
            typ, inf, gen, elev = key
            meta = ("Префикс" if gen == "prefix" else "Суффикс") + (f" · {inf}" if inf else "") + \
                   (" · элевейтед" if elev else "")
            tg = group_tags(mods)
            if tg:
                meta += "  ·  " + ", ".join(tg)
            card = ttk.LabelFrame(self.cards, text=meta, padding=(10, 6))
            card.pack(fill="x", pady=4, padx=(0, 6))
            top = ttk.Frame(card)
            top.pack(fill="x")
            best = ([m for m in mods if m.spawnable] or mods)[0]
            ttk.Button(top, text="✕", width=3, command=lambda k=key: self.remove(k)).pack(side="right")
            ttk.Label(top, text=best.text, wraplength=360, justify="left").pack(side="left", fill="x")
            values = []
            for m in mods:
                v = f"не хуже T{m.tier}   ·   {m.text}" + (f"   ·   {fmt_chance(m.chance)}" if m.chance else "")
                if not m.spawnable:
                    v += f"   (нужен ilvl {m.level})"
                values.append(v)
            cb = ttk.Combobox(card, values=values, state="readonly")
            cb.pack(fill="x", pady=(6, 0))
            cb.current(min(tier, len(values)) - 1)
            cb.bind("<<ComboboxSelected>>",
                    lambda e, k=key, c=cb: self.set_tier(k, c.current() + 1))

    def remove(self, key):
        self.selected.pop(key, None)
        self.update_row(key)
        self.render_cards()
        self.sync()
        self.update_steps()

    def set_tier(self, key, tier):
        self.selected[key] = tier
        self.sync()
        self.update_steps()

    # ======== пресеты ========
    def update_presets(self):
        self.preset_cb.configure(values=sorted(CONFIG["presets"].keys()))

    def save_preset(self):
        name = self.preset.get().strip()
        if not name:
            log("Введи имя пресета в поле и нажми «Сохранить».")
            return
        CONFIG["presets"][name] = {
            "reqs": [[list(k), t] for k, t in self.selected.items()],
            "mode": self.mode.get(), "inf": self.inf_count.get()}
        save_config()
        self.update_presets()
        log(f"Пресет «{name}» сохранён")

    def load_preset(self):
        p = CONFIG["presets"].get(self.preset.get())
        if not p:
            return
        self.selected.clear()
        missing = 0
        for k, t in p.get("reqs", []):
            k = tuple(k)
            if S.ctx and k in S.ctx.groups:
                self.selected[k] = t
            else:
                missing += 1
        self.mode.set(p.get("mode", "все выбранные"))
        self.inf_count.set(p.get("inf", "любое"))
        self.refresh_all()
        if missing:
            log(f"Пресет загружен, но {missing} мод(ов) не может выпасть на этой базе")

    def delete_preset(self):
        if CONFIG["presets"].pop(self.preset.get(), None) is not None:
            save_config()
            self.preset.set("")
            self.update_presets()

    # ======== низ: последний ролл и журнал ========
    def build_bottom(self):
        bot = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bot.pack(fill="x")
        head = ttk.Frame(bot)
        head.pack(fill="x")
        ttk.Label(head, text="Последний ролл", style="Head.TLabel").pack(side="left")
        self.roll_ms = ttk.Label(head, text="", style="Hint.TLabel")
        self.roll_ms.pack(side="left", padx=10)
        self.log_btn = ttk.Button(head, text="Журнал ▸", command=self.toggle_log)
        self.log_btn.pack(side="right")
        self.roll = tk.Text(bot, height=4, wrap="word", relief="flat", font=("Segoe UI", 11),
                            padx=8, pady=6, cursor="arrow")
        self.roll.pack(fill="x", pady=(4, 0))
        self.roll.tag_configure("ok", foreground=GREEN)
        self.roll.tag_configure("hit", foreground=GREEN, font=("Segoe UI", 11, "bold"))
        self.roll.tag_configure("dim", foreground=GRAY)
        self.roll.configure(state="disabled")
        self.logbox = tk.Text(bot, height=8, wrap="word", font=("Consolas", 9), relief="flat")
        self.log_visible = False

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.logbox.pack(fill="x", pady=(6, 0))
            self.log_btn.configure(text="Журнал ▾")
        else:
            self.logbox.pack_forget()
            self.log_btn.configure(text="Журнал ▸")

    def show_roll(self, matches, hit, ms):
        self.roll.configure(state="normal")
        self.roll.delete("1.0", "end")
        if hit:
            self.roll.insert("end", "СОВПАЛО — клик заблокирован\n", "hit")
        for mt in matches:
            good = any(m.group_key in self.selected and m.tier <= self.selected[m.group_key]
                       for m in mt.candidates)
            tiers = "/".join(f"T{m.tier}" for m in sorted(mt.candidates, key=lambda m: m.tier))
            inf = f" · {mt.best.influence}" if mt.best.influence else ""
            text = " | ".join(mt.lines)
            self.roll.insert("end", ("✓ " if good else "   ") + f"{text}   ", "ok" if good else "")
            self.roll.insert("end", f"{tiers}{inf}\n", "dim")
        self.roll.configure(state="disabled")
        self.roll_ms.configure(text=f"{ms:.0f} мс")

    # ======== настройки ========
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Настройки")
        win.transient(self.root)
        win.resizable(False, False)
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        f = ttk.Frame(nb, padding=14)
        nb.add(f, text="Настройки")
        about = ttk.Frame(nb, padding=14)
        nb.add(about, text="О программе")
        self.build_about(about)
        ttk.Label(f, text="Горячие клавиши", style="Head.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.key_btns = {}
        r = 1
        for action, title in HOTKEY_NAMES.items():
            ttk.Label(f, text=title).grid(row=r, column=0, sticky="w", pady=3)
            b = ttk.Button(f, width=12, command=lambda a=action: self.start_bind(a))
            b.grid(row=r, column=1, sticky="e", pady=3)
            self.key_btns[action] = b
            r += 1
        self.update_key_btns()
        ttk.Label(f, text="Нажми кнопку, затем новую клавишу (Esc — отмена)",
                  style="Hint.TLabel").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=2, sticky="ew", pady=10)
        r += 1
        ttk.Label(f, text="Охрана", style="Head.TLabel").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        secs = tk.StringVar(value=str(self.settings["block_seconds"]))
        ttk.Label(f, text="Блокировать клик, секунд").grid(row=r, column=0, sticky="w", pady=3)
        sp = ttk.Spinbox(f, from_=1, to=60, textvariable=secs, width=6)
        sp.grid(row=r, column=1, sticky="e")
        r += 1
        checks = [("hold", "Держать блок до повторного нажатия клавиши охраны"),
                  ("step", "Пошаговый режим: следующий клик только после проверки"),
                  ("shift_lock", "Shift-замок: Shift залипает после первого клика"),
                  ("on_top", "Окно программы поверх игры")]
        vars_ = {}
        for k, txt in checks:
            v = tk.BooleanVar(value=self.settings[k])
            ttk.Checkbutton(f, text=txt, variable=v).grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
            vars_[k] = v
            r += 1

        def apply_and_close():
            try:
                self.settings["block_seconds"] = max(0.5, float(secs.get().replace(",", ".")))
            except ValueError:
                pass
            for k, v in vars_.items():
                self.settings[k] = v.get()
            CONFIG["settings"] = self.settings
            save_config()
            self.apply_settings()
            UI_CAPTURE[0] = None
            self.about = None
            win.destroy()
        ttk.Button(f, text="Готово", style="Accent.TButton", command=apply_and_close).grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        win.protocol("WM_DELETE_WINDOW", apply_and_close)

    # ======== о программе и обновления ========
    def build_about(self, f):
        f.columnconfigure(0, weight=1)
        ttk.Label(f, text="PoE Craft Guard", style="Title.TLabel").grid(row=0, column=0, pady=(6, 0))
        ttk.Label(f, text=f"Версия {updater.VERSION}").grid(row=1, column=0)
        links = ttk.Frame(f)
        links.grid(row=2, column=0, pady=(4, 12))
        for i, (txt, url) in enumerate((("Что нового", updater.RELEASES_URL),
                                        ("Сообщить об ошибке на GitHub", updater.ISSUES_URL))):
            lb = ttk.Label(links, text=txt, style="Link.TLabel", cursor="hand2")
            lb.grid(row=0, column=i, padx=8)
            lb.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        box = ttk.Frame(f, style="Card.TFrame", padding=(14, 10))
        box.grid(row=3, column=0, sticky="ew", padx=20)
        box.columnconfigure(0, weight=1)
        status = ttk.Label(box, text="", font=("Segoe UI", 10, "bold"), wraplength=380)
        status.grid(row=0, column=0, sticky="w")
        when = ttk.Label(box, text="", style="Hint.TLabel")
        when.grid(row=1, column=0, sticky="w")
        btn = ttk.Button(box, text="", command=self.update_button)
        btn.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        auto = tk.BooleanVar(value=self.settings["auto_update"])

        def toggle_auto():
            self.settings["auto_update"] = auto.get()
            CONFIG["settings"] = self.settings
            save_config()
        ttk.Checkbutton(f, text="Обновлять автоматически (проверка при запуске и раз в 6 часов)",
                        variable=auto, command=toggle_auto).grid(row=4, column=0, pady=(10, 0))
        f.rowconfigure(5, weight=1, minsize=40)
        ttk.Label(f, text="Created by kotrow", style="Hint.TLabel").grid(row=6, column=0, sticky="s")
        self.about = {"status": status, "when": when, "btn": btn}
        self.refresh_about()

    @staticmethod
    def time_ago(ts):
        if not ts:
            return "никогда"
        d = max(0, time.time() - ts)
        if d < 60:
            return "только что"
        if d < 3600:
            return f"{int(d // 60)} мин назад"
        if d < 86400:
            return f"{int(d // 3600)} ч назад"
        return f"{int(d // 86400)} дн назад"

    def update_state(self):
        """(текст статуса, цвет, текст кнопки) для вкладки «О программе»."""
        st = CONFIG["update"]
        latest, installed = st.get("latest", ""), st.get("installed", "")
        if self.upd_busy == "install":
            return f"Устанавливаю версию {latest}...", AMBER, None
        if self.upd_busy == "check":
            return "Проверяю...", GRAY, None
        if installed and updater.is_newer(installed):
            return f"Версия {installed} установлена — перезапусти программу", GREEN, "Проверить сейчас"
        if st.get("error"):
            return f"Не удалось: {st['error']}", RED, "Проверить сейчас"
        if latest and updater.is_newer(latest):
            if updater.is_git_checkout():
                return f"Доступна версия {latest} — папка из git, обнови через git pull", AMBER, "Проверить сейчас"
            return f"Доступна версия {latest}", AMBER, f"Установить {latest}"
        if latest:
            return "Установлена последняя версия", None, "Проверить сейчас"
        return "Обновления ещё не проверялись", None, "Проверить сейчас"

    def refresh_about(self):
        if not self.about:
            return
        text, color, btn = self.update_state()
        try:
            self.about["status"].configure(text=text, foreground=color or "")
            self.about["when"].configure(
                text="Последняя проверка: " + self.time_ago(CONFIG["update"].get("last_check")))
            self.about["btn"].configure(text=btn or "Подождите...", state="normal" if btn else "disabled")
        except tk.TclError:
            self.about = None

    def update_button(self):
        st = CONFIG["update"]
        latest = st.get("latest", "")
        if (latest and updater.is_newer(latest) and st.get("installed") != latest
                and not st.get("error") and not updater.is_git_checkout()):
            self.install_update()
        else:
            self.check_updates()

    def auto_check(self):
        last = CONFIG["update"].get("last_check") or 0
        if self.settings["auto_update"] and time.time() - last >= UPDATE_EVERY:
            self.check_updates()
        self.root.after(30 * 60 * 1000, self.auto_check)

    def _bg(self, kind, fn):
        def run():
            try:
                UI.put(("update", (kind, fn())))
            except updater.UpdateError as e:
                UI.put(("update", ("error", str(e))))
            except Exception as e:
                UI.put(("update", ("error", f"{type(e).__name__}: {e}")))
        threading.Thread(target=run, daemon=True).start()

    def check_updates(self):
        if self.upd_busy:
            return
        self.upd_busy = "check"
        self.refresh_about()
        self._bg("checked", updater.check)

    def install_update(self):
        if self.upd_busy or not self.upd_info:
            if not self.upd_info:
                self.check_updates()
            return
        self.upd_busy = "install"
        self.refresh_about()
        info = self.upd_info
        self._bg("installed", lambda: (updater.install(info), info["version"])[1])

    def on_update(self, kind, data):
        st = CONFIG["update"]
        busy, self.upd_busy = self.upd_busy, ""
        if kind == "checked":
            self.upd_info = data
            st.update(last_check=time.time(), latest=data["version"], error="")
            if updater.is_newer(data["version"]) and st.get("installed") != data["version"]:
                log(f"Доступна новая версия {data['version']} (сейчас {updater.VERSION})")
                if self.settings["auto_update"] and not updater.is_git_checkout():
                    save_config()
                    self.install_update()
                    return
        elif kind == "installed":
            st["installed"] = data
            log(f"Обновлено до версии {data} — перезапусти программу, чтобы она заработала")
        else:
            st["error"] = data
            log(("Обновление не установлено: " if busy == "install" else "Проверка обновлений: ") + data)
        save_config()
        self.refresh_about()

    def apply_settings(self):
        st = self.settings
        S.block_seconds = float(st["block_seconds"])
        S.hold_until_f9 = bool(st["hold"])
        S.step_mode = bool(st["step"])
        S.shift_lock = bool(st["shift_lock"])
        if not S.step_mode:
            S.pending = False
        if not S.shift_lock:
            release_latch()
        self.root.attributes("-topmost", bool(st["on_top"]))

    def update_key_btns(self):
        for action, b in getattr(self, "key_btns", {}).items():
            try:
                b.config(text="нажми..." if UI_CAPTURE[0] == action else hk(action))
            except tk.TclError:
                pass

    def start_bind(self, action):
        UI_CAPTURE[0] = action
        self.update_key_btns()

    def finish_bind(self, name):
        action, UI_CAPTURE[0] = UI_CAPTURE[0], None
        if action is None:
            return
        if name != "esc":
            other = next((a for a, k in HOTKEYS.items() if k == name and a != action), None)
            if other:
                HOTKEYS[other] = HOTKEYS[action]
            HOTKEYS[action] = name
            save_config()
            log(f"{HOTKEY_NAMES[action]}: клавиша {name.upper()}")
        self.update_key_btns()

    # ======== индикатор поверх игры ========
    def make_overlay(self):
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", 0.88)
        self.ov_lbl = tk.Label(ov, text="", font=("Segoe UI", 12, "bold"),
                               bg="#202020", fg="white", padx=12, pady=4)
        self.ov_lbl.pack()
        ov.update_idletasks()
        ov.geometry(f"+{ov.winfo_screenwidth() - 340}+8")
        try:
            hwnd = ctypes.windll.user32.GetParent(ov.winfo_id())
            GWL_EXSTYLE, LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE = -20, 0x80000, 0x20, 0x80, 0x08000000
            st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                                st | LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE)
        except Exception:
            pass
        self.ov = ov

    # ======== общее ========
    def load_db(self):
        try:
            self.db = ModDB(log=log)
            log(f"База модов готова. Ctrl+Alt+C на предмете в игре → {hk('import')}.")
        except Exception as e:
            log(f"Не удалось загрузить базу: {e}")

    def do_import(self):
        if self.db is None:
            log("База модов ещё загружается...")
            return
        try:
            ctx = parse_item_text(self.db, pyperclip.paste())
        except Exception as e:
            log(f"Импорт не удался: {e}")
            # ошибку держим на плашке несколько секунд, иначе poll сразу затрёт её старой базой
            self.import_err = ("не вышло — сначала Ctrl+Alt+C", time.time() + 6)
            return
        self.import_err = ("", 0.0)
        S.ctx = ctx
        S.crop = None
        self.selected = {k: v for k, v in self.selected.items() if k in ctx.groups}
        self.rebuild_inf_chips(ctx.influences)
        self.refresh_all()
        log(f"Импортирована база: {ctx.base_name}")

    def toggle_guard(self):
        on_key_action("arm")

    def refresh_all(self):
        self.fill()
        self.render_cards()
        self.sync()
        self.update_steps()

    def sync(self):
        S.reqs = [Requirement(k, t) for k, t in self.selected.items()]
        S.mode = "all" if self.mode.get().startswith("все") else "any"
        _, S.inf_min, S.inf_max = next(o for o in INF_OPTIONS if o[0] == self.inf_count.get())

    def update_steps(self):
        try:
            self.base_btn.config(text=f"⟳ Новая база ({hk('import')})")
        except tk.TclError:
            pass
        err, until = self.import_err
        if err and time.time() < until:
            self.set_step("base", False, err, RED)
        elif S.ctx:
            inf = ", ".join(S.ctx.influences) or "без влияния"
            extra = S.ctx.enchant if S.ctx.enchant else inf
            self.set_step("base", True, f"{S.ctx.base_name} · ilvl {S.ctx.ilvl} · {extra}")
        else:
            self.set_step("base", False, f"Ctrl+Alt+C в игре → {hk('import')} или клик сюда")
        if S.region:
            self.set_step("calib", True, f"готово · {hk('calibrate')} для повтора")
        else:
            self.set_step("calib", False, f"наведи на предмет → {hk('calibrate')}")
        n = len(self.selected)
        self.set_step("target", n > 0, f"{n} мод(а)" if n else "выбери моды слева")
        if S.blocked():
            left = f"до {hk('arm')}" if S.block_hold else f"{S.block_until - time.time():.1f} с"
            self.set_step("guard", True, f"КЛИК ЗАБЛОКИРОВАН · {left}", RED)
        elif S.armed:
            self.set_step("guard", True, f"включена · {hk('arm')} — выкл" + ("  ·  ⇧ Shift" if S.latched else ""))
        else:
            self.set_step("guard", False, f"выключена · {hk('arm')} или клик сюда")

    def poll(self):
        while not UI.empty():
            kind, data = UI.get()
            if kind == "log":
                self.logbox.insert("end", data + "\n")
                self.logbox.see("end")
            elif kind == "bind":
                self.finish_bind(data)
            elif kind == "import":
                self.do_import()
            elif kind == "roll":
                self.show_roll(*data)
            elif kind == "update":
                self.on_update(*data)
            elif kind == "hit":
                log("СОВПАЛО — левый клик заблокирован")
        self.update_steps()
        if S.blocked():
            short, bg = ("⛔ БЛОК до " + hk("arm") if S.block_hold
                         else f"⛔ БЛОК {S.block_until - time.time():.1f} с"), "#c62828"
        elif S.armed and S.pending and time.time() - S.pending_since < PENDING_TIMEOUT:
            short, bg = "⏳ проверка...", "#f9a825"
        elif S.armed:
            short, bg = "🟢 ОХРАНА" + ("  ⇧ Shift" if S.latched else ""), "#2e7d32"
        else:
            short, bg = "⚪ охрана выкл", "#424242"
        self.ov_lbl.config(text=short, bg=bg)
        self.root.after(50, self.poll)


def main():
    load_config()
    root = tk.Tk()
    App(root)
    mouse_listener.start()
    kb_listener.start()
    threading.Thread(target=worker, daemon=True).start()
    root.protocol("WM_DELETE_WINDOW", lambda: (release_latch(), setattr(S, "running", False), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
