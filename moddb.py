"""
База модов PoE 1 (данные RePoE, обновляются под каждый патч) + сопоставление
строк подсказки предмета с модами и тирами. Платформонезависимый модуль.
"""
import difflib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

from i18n import t
from paths import APP_DIR

DATA_URL = "https://raw.githubusercontent.com/repoe-fork/repoe-fork.github.io/master/data/"
DATA_FILES = {"mods.json": "mods.json",
              "base_items.json": "base_items.json",
              "stat_translations.json": "stat_translations.json",
              "cluster_jewels.json": "cluster_jewels.json"}
# язык клиента игры: ключ перевода в stat_translations и папка данных (английские файлы — в корне data/)
GAME_LANGS = {"en": ("English", ""), "ru": ("Russian", "Russian/")}
LANG_FILES = ("base_items.json", "stat_translations.json", "cluster_jewels.json", "item_classes.json")
POOL_DOMAINS = {"item": ("item",), "affliction_jewel": ("affliction_jewel",)}
DATA_DIR = os.path.join(APP_DIR, "data")
CYR = "А-Яа-яЁё"

INFLUENCES = {  # суффикс тега в spawn_weights -> название
    "shaper": "Shaper", "elder": "Elder", "crusader": "Crusader",
    "eyrie": "Redeemer", "basilisk": "Hunter", "adjudicator": "Warlord",
}
INFLUENCE_BY_NAME = {v: k for k, v in INFLUENCES.items()}
# русский клиент: строка «Предмет Создателя» и т. п. — по основе слова, окончания не важны
INFLUENCE_RU = {"создател": "Shaper", "древн": "Elder", "крестонос": "Crusader",
                "искупител": "Redeemer", "охотник": "Hunter", "вожд": "Warlord"}

NUM_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё])[+-]?\d+(?:\.\d+)?(?![A-Za-zА-Яа-яЁё])")
RANGE_RE = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*[-–]\s*-?\d+(?:\.\d+)?\s*\)")


def _fetch(remote, path, log):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    log(t("Скачиваю {name} ...", name=remote))
    tmp = path + ".part"
    urllib.request.urlretrieve(DATA_URL + remote, tmp)
    os.replace(tmp, path)                 # оборванная загрузка не оставит битый файл


def download_data(force=False, log=print):
    for local, remote in DATA_FILES.items():
        path = os.path.join(DATA_DIR, local)
        if force or not os.path.exists(path):
            _fetch(remote, path, log)


def download_lang(lang, force=False, log=print):
    sub = GAME_LANGS[lang][1]
    for name in LANG_FILES if sub else ():
        path = os.path.join(DATA_DIR, *sub.strip("/").split("/"), name)
        if force or not os.path.exists(path):
            _fetch(sub + name, path, log)


def skeleton(text):
    """Текст строки -> 'скелет' без чисел, для сравнения с шаблоном."""
    s = text.lower().replace("\u2013", "-").replace("ё", "е")
    s = RANGE_RE.sub("", s)
    s = NUM_RE.sub("#", s)
    s = re.sub(r"[^a-zа-я#% ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- преобразования значений (index_handlers) ----------
_HANDLERS = {
    "negate": lambda v: -v,
    "double": lambda v: v * 2,
    "negate_and_double": lambda v: -v * 2,
    "times_twenty": lambda v: v * 20,
    "30%_of_value": lambda v: v * 0.3,
    "60%_of_value": lambda v: v * 0.6,
}
for _n, _d in (("divide_by_one_hundred", 100), ("milliseconds_to_seconds", 1000),
               ("per_minute_to_per_second", 60), ("divide_by_ten", 10),
               ("divide_by_two", 2), ("divide_by_five", 5), ("divide_by_twelve", 12),
               ("divide_by_one_thousand", 1000), ("deciseconds_to_seconds", 10),
               ("locations_to_metres", 10)):
    _HANDLERS[_n] = (lambda d: (lambda v: v / d))(_d)


def apply_handlers(v, handlers):
    for h in handlers:
        if not isinstance(h, str):
            continue
        fn = _HANDLERS.get(h)
        if fn is None:  # варианты с суффиксами вроде _2dp_if_required
            for k, f in _HANDLERS.items():
                if h.startswith(k):
                    fn = f
                    break
        if fn:
            v = fn(v)
    return v


@dataclass
class LineTemplate:
    skel: str
    slots: list          # [(lo, hi, tolerance)] по порядку чисел в строке
    display: str


@dataclass
class Mod:
    id: str
    name: str
    gen: str             # prefix / suffix
    type: str
    level: int
    weight: int
    influence: str       # '' | Hunter | Redeemer ...
    elevated: bool
    lines: list = field(default_factory=list)   # [LineTemplate]
    tier: int = 0
    tiers_total: int = 0
    spawnable: bool = True
    tags: list = field(default_factory=list)   # теги мода (attack, defences, life...) — на них смотрит Harvest
    chance: float = 0.0                         # доля веса среди префиксов/суффиксов базы

    @property
    def group_key(self):
        return (self.type, self.influence, self.gen, self.elevated)

    @property
    def text(self):
        return " / ".join(l.display for l in self.lines)


class ModDB:
    def __init__(self, log=print):
        self.log = log
        download_data(log=log)
        log(t("Загружаю базу модов ..."))
        with open(os.path.join(DATA_DIR, "mods.json"), encoding="utf-8") as f:
            self.mods = json.load(f)
        with open(os.path.join(DATA_DIR, "base_items.json"), encoding="utf-8") as f:
            self.bases = json.load(f)
        with open(os.path.join(DATA_DIR, "stat_translations.json"), encoding="utf-8") as f:
            st = json.load(f)
        try:
            with open(os.path.join(DATA_DIR, "cluster_jewels.json"), encoding="utf-8") as f:
                self.clusters = json.load(f)
        except OSError:
            self.clusters = {}
        # по языкам клиента: переводы статов, названия баз, классы предметов, кластеры
        self.trans = {"en": self._trans_index(st, "English")}
        self.base_names = {"en": {k: b["name"] for k, b in self.bases.items()}}
        self.class_names = {"en": {}}
        self.lang_clusters = {"en": self.clusters}

    @staticmethod
    def _trans_index(st, key):
        out = {}
        for e in st:
            if not e.get(key):
                continue
            for sid in e["ids"]:
                out.setdefault(sid, e)
        return out

    def load_lang(self, lang):
        """Данные для клиента на этом языке (русские — докачиваются при первом русском предмете)."""
        if lang in self.trans:
            return
        key, sub = GAME_LANGS[lang]
        download_lang(lang, log=self.log)
        folder = os.path.join(DATA_DIR, *sub.strip("/").split("/"))
        load = lambda name: json.load(open(os.path.join(folder, name), encoding="utf-8"))
        bases, classes = load("base_items.json"), load("item_classes.json")
        try:
            clusters = load("cluster_jewels.json")
        except OSError:
            clusters = {}
        self.base_names[lang] = {k: b.get("name", "") for k, b in bases.items() if k in self.bases}
        self.class_names[lang] = {k: c.get("name", "") for k, c in classes.items()}
        self.lang_clusters[lang] = clusters
        self.trans[lang] = self._trans_index(load("stat_translations.json"), key)

    # ---------- шаблоны строк ----------
    def mod_lines(self, stats, lang="en"):
        """stats: [{'id','min','max'}] -> [LineTemplate] на языке клиента"""
        trans, key = self.trans[lang], GAME_LANGS[lang][0]
        vals = {s["id"]: (s["min"], s["max"]) for s in stats}
        out, seen = [], set()
        for s in stats:
            e = trans.get(s["id"])
            if e is None or id(e) in seen:
                continue
            seen.add(id(e))
            ranges = [vals.get(i, (0, 0)) for i in e["ids"]]
            variant = self._pick_variant(e[key], ranges)
            if variant is None:
                continue
            out.extend(self._render(variant, ranges))
        return out

    @staticmethod
    def _cond_ok(conds, values):
        for c, v in zip(conds, values):
            ok = (c.get("min") is None or v >= c["min"]) and (c.get("max") is None or v <= c["max"])
            if c.get("negated"):
                ok = not ok
            if not ok:
                return False
        return True

    def _pick_variant(self, variants, ranges):
        for pick in (1, 0):
            values = [r[pick] for r in ranges]
            if all(v == 0 for v in values):
                continue
            for var in variants:
                if self._cond_ok(var["condition"], values):
                    return var
        return None

    @staticmethod
    def _render(var, ranges):
        string = re.sub(r"\[([^\]|]+)\|([^\]]+)\]", r"\2", var["string"])
        string = re.sub(r"\[([^\]]+)\]", r"\1", string)
        lines = []
        for part in string.split("\n"):
            slots, display = [], part
            token_re = re.compile(r"\{(\d+)\}|" + NUM_RE.pattern)
            for m in token_re.finditer(part):
                if m.group(1) is None:                 # число прямо в тексте шаблона
                    v = float(m.group(0))
                    slots.append((v, v, 0.001))
                    continue
                i = int(m.group(1))
                if i >= len(ranges) or var["format"][i] == "ignore":
                    slots.append((float("-inf"), float("inf"), 0))   # число есть, но не проверяем
                    display = display.replace(m.group(0), "#", 1)
                    continue
                handlers = var["index_handlers"][i] if i < len(var["index_handlers"]) else []
                a = apply_handlers(ranges[i][0], handlers)
                b = apply_handlers(ranges[i][1], handlers)
                lo, hi = min(a, b), max(a, b)
                tol = 0.001 if not handlers else max(0.06, abs(hi) * 0.02)
                slots.append((lo, hi, tol))
                sign = "+" if var["format"][i] == "+#" else ""
                fmt = lambda x: f"{x:g}"
                txt = f"{sign}{fmt(lo)}" if lo == hi else f"{sign}({fmt(lo)}-{fmt(hi)})"
                display = display.replace(m.group(0), txt, 1)
            lines.append(LineTemplate(skeleton(re.sub(r"\{\d+\}", "1", part)), slots, display))
        return lines


@dataclass
class ItemContext:
    base_name: str
    item_class: str
    ilvl: int
    influences: list
    tags: list
    implicit_skels: list
    pool: list            # [Mod]
    groups: dict          # group_key -> [Mod] (отсортированы по тиру)
    index: dict           # skel -> [(Mod, line_idx)]
    enchant: str = ""     # для кластеров: что дают малые пассивки
    lang: str = "en"      # язык клиента игры (по тексту Ctrl+Alt+C): шаблоны строк и OCR


def detect_game_lang(text):
    head = "\n".join(text.replace("\r", "").split("\n")[:4])
    return "ru" if re.search(f"[{CYR}]", head) else "en"


def _influences(lines, lang):
    found = []
    for l in lines:
        if lang == "en":
            if l.endswith(" Item") and l.split()[0] in INFLUENCE_BY_NAME:
                found.append(l.split()[0])
        elif "предмет" in l.lower() and len(l.split()) <= 3:
            low = l.lower()
            found += [name for stem, name in INFLUENCE_RU.items() if stem in low]
    return list(dict.fromkeys(found))


def _norm_class(c):
    return re.sub(r"(s|ы|и|а|я)$", "", (c or "").lower().strip())


def parse_item_text(db, text):
    """Текст из Ctrl+Alt+C (английский или русский клиент) -> ItemContext."""
    lines = [l.strip() for l in text.replace("\r", "").split("\n")]
    lang = detect_game_lang(text)
    db.load_lang(lang)

    def field(*names):
        return next((l.split(":", 1)[1].strip() for l in lines
                     if any(l.startswith(n + ":") for n in names)), "")
    item_class = field("Item Class", "Класс предмета")
    lvl = re.search(r"\d+", field("Item Level", "Уровень предмета"))
    ilvl = int(lvl.group()) if lvl else 86
    influences = _influences(lines, lang)
    header = []
    for l in lines[1:]:
        if l.startswith("--------"):
            break
        if not (l.startswith("Rarity:") or l.startswith("Редкость:")):
            header.append(l)
    names = db.base_names[lang]
    classes = None                       # подходящие классы (для русского — по названию класса)
    if item_class and lang != "en":
        classes = {k for k, n in db.class_names[lang].items() if _norm_class(n) == _norm_class(item_class)} or None
    best = None
    for k, b in db.bases.items():
        if b.get("domain") not in POOL_DOMAINS or b.get("release_state") != "released":
            continue
        if item_class and lang == "en" and _norm_class(b.get("item_class")) != _norm_class(item_class):
            continue
        if classes is not None and b.get("item_class") not in classes:
            continue
        name = names.get(k) or ""
        if name and any(name.lower() in h.lower() for h in header):
            if best is None or len(name) > len(best[2]):
                best = (k, b, name)
    if best is None:
        raise ValueError(t("Не удалось определить базу предмета. Скопируй предмет через Ctrl+Alt+C."))
    extra_tags, enchant = [], ""
    cluster = db.clusters.get(best[0])
    if cluster:                                     # кластер: пул нотаблов зависит от энчанта
        local = db.lang_clusters[lang].get(best[0], cluster)
        tag_of = {sk.get("id"): sk["tag"] for sk in cluster["passive_skills"]}
        for l in lines:
            ench = re.sub(r"\s*\((enchant|зачарование|энчант)\)\s*$", "", l).strip()
            cands = {ench, ench.split(":", 1)[-1].strip()}
            for sk in local["passive_skills"]:
                if any(skeleton(x) == skeleton(c) for x in sk.get("stat_text", []) for c in cands if c):
                    extra_tags.append(tag_of.get(sk.get("id"), sk.get("tag")))
                    enchant = ench.split(":", 1)[-1].strip()
                    break
            if extra_tags:
                break
        if not extra_tags:
            raise ValueError(t("Не удалось распознать энчант кластера. Скопируй его через Ctrl+Alt+C."))
    ctx = build_context(db, best[1], ilvl, influences, extra_tags, lang=lang, base_name=best[2])
    ctx.enchant = enchant
    return ctx


def build_context(db, base, ilvl, influences, extra_tags=(), lang="en", base_name=None):
    db.load_lang(lang)
    tags = list(base["tags"]) + list(extra_tags)
    domains = POOL_DOMAINS.get(base.get("domain"), ("item",))
    for inf in influences:
        suf = INFLUENCE_BY_NAME[inf]
        tags += [f"{t}_{suf}" for t in base["tags"]]
    tagset = set(tags)

    pool, by_prefix = [], {}
    for mid, m in db.mods.items():
        if m["domain"] not in domains or m["generation_type"] not in ("prefix", "suffix"):
            continue
        weight, tag_hit = 0, None
        for w in m["spawn_weights"]:
            if w["tag"] in tagset:
                weight, tag_hit = w["weight"], w["tag"]
                break
        if weight <= 0 or m.get("is_essence_only") or "Royale" in mid:
            continue
        inf = ""
        if tag_hit and "_" in tag_hit:
            inf = INFLUENCES.get(tag_hit.rsplit("_", 1)[1], "")
        mod = Mod(mid, m["name"], m["generation_type"], m["type"], m["required_level"],
                  weight, inf, False, db.mod_lines(m["stats"], lang))
        mod.spawnable = m["required_level"] <= ilvl
        mod.tags = [t for t in m.get("implicit_tags", []) if t != "influence_mod"]
        if mod.lines:
            pool.append(mod)
            by_prefix[re.sub(r"[\d_]+$", "", mid)] = mod

    # элевейтед (Maven) версии модов влияния из пула
    for mid, m in db.mods.items():
        if m["domain"] not in domains or "Maven" not in mid or m["generation_type"] not in ("prefix", "suffix"):
            continue
        sib = by_prefix.get(mid.split("Maven")[0])
        if sib is None or not sib.influence:
            continue
        mod = Mod(mid, m["name"], m["generation_type"], m["type"], m["required_level"],
                  0, sib.influence, True, db.mod_lines(m["stats"], lang))
        mod.spawnable = m["required_level"] <= ilvl
        mod.tags = [t for t in m.get("implicit_tags", []) if t != "influence_mod"]
        if mod.lines:
            pool.append(mod)

    totals = {}
    for mod in pool:
        if mod.spawnable and not mod.elevated:
            totals[mod.gen] = totals.get(mod.gen, 0) + mod.weight
    for mod in pool:
        if mod.spawnable and not mod.elevated and totals.get(mod.gen):
            mod.chance = mod.weight / totals[mod.gen]

    groups = {}
    for mod in pool:
        groups.setdefault(mod.group_key, []).append(mod)
    for mods in groups.values():
        mods.sort(key=lambda x: (-x.level, -sum(s[1] for l in x.lines for s in l.slots)))
        for i, mod in enumerate(mods, 1):
            mod.tier, mod.tiers_total = i, len(mods)

    index = {}
    for mod in pool:
        for i, l in enumerate(mod.lines):
            index.setdefault(l.skel, []).append((mod, i))

    implicit_skels = []
    for imp in base.get("implicits", []):
        m = db.mods.get(imp)
        if m:
            implicit_skels += [l.skel for l in db.mod_lines(m["stats"], lang)]

    return ItemContext(base_name or base["name"], base["item_class"], ilvl, influences, tags,
                       implicit_skels, pool, groups, index, lang=lang)


@dataclass
class Match:
    candidates: list      # [Mod] — подходящие по значениям (неоднозначность тира)
    lines: list           # исходные строки

    @property
    def best(self):
        return min(self.candidates, key=lambda m: m.tier)

    def describe(self):
        tiers = sorted({m.tier for m in self.candidates})
        tier_txt = "/".join(f"T{x}" for x in tiers)
        m = self.best
        el = " ELEVATED" if m.elevated else ""
        inf = f" [{m.influence}]" if m.influence else ""
        return f"{m.gen[0].upper()} {tier_txt}{inf}{el}: " + " | ".join(self.lines)


def _values_fit(tpl, nums):
    if len(nums) != len(tpl.slots):
        return False
    return all(lo - tol <= v <= hi + tol for v, (lo, hi, tol) in zip(nums, tpl.slots))


def match_lines(ctx, raw_lines, fuzzy=0.86):
    """Строки подсказки (OCR или текст) -> [Match]."""
    skels_known = list(ctx.index.keys())
    parsed = []
    implicit_left = list(ctx.implicit_skels)
    for raw in raw_lines:
        clean = RANGE_RE.sub("", raw)
        sk = skeleton(clean)
        if not sk or "#" not in sk and len(sk) < 12:
            if sk not in ctx.index:
                continue
        imp = difflib.get_close_matches(sk, implicit_left, n=1, cutoff=0.8) if implicit_left else []
        if imp:                            # первая строка, похожая на имплисит, — это имплисит
            implicit_left.remove(imp[0])
            continue
        if sk not in ctx.index:
            close = difflib.get_close_matches(sk, skels_known, n=1, cutoff=fuzzy)
            if not close:
                continue
            sk = close[0]
        nums = [float(x) for x in NUM_RE.findall(clean)]
        parsed.append({"raw": raw.strip(), "skel": sk, "nums": nums, "used": False})

    # кандидаты: мод -> какие строки он покрывает
    options = []
    seen = set()
    for p in parsed:
        for mod, _ in ctx.index[p["skel"]]:
            if mod.id in seen:
                continue
            seen.add(mod.id)
            assign, ok = [], True
            for tpl in mod.lines:
                hit = next((q for q in parsed if q["skel"] == tpl.skel and q not in assign
                            and _values_fit(tpl, q["nums"])), None)
                if hit is None:
                    ok = False
                    break
                assign.append(hit)
            if ok:
                options.append((mod, assign))

    # жадно: сначала многострочные (гибриды/элевейтед), строки не переиспользуем
    options.sort(key=lambda o: -len(o[1]))
    results = []
    for mod, assign in options:
        if any(q["used"] for q in assign):
            continue
        same = [m for m, a in options if a == assign or
                ([id(x) for x in a] == [id(x) for x in assign])]
        same = [m for m in same if len(m.lines) == len(mod.lines)]
        for q in assign:
            q["used"] = True
        results.append(Match(same, [q["raw"] for q in assign]))
    return results


# ---------- правило ----------
@dataclass
class Requirement:
    group_key: tuple
    max_tier: int


def check_rule(matches, reqs, mode="all", inf_min=None, inf_max=None):
    def satisfied(r):
        return any(m.group_key == r.group_key and m.tier <= r.max_tier
                   for mt in matches for m in mt.candidates)
    if reqs:
        ok = all(satisfied(r) for r in reqs) if mode == "all" else any(satisfied(r) for r in reqs)
    else:
        ok = True
    if inf_min is not None or inf_max is not None:
        n = sum(1 for mt in matches if mt.best.influence)
        if inf_min is not None and n < inf_min:
            ok = False
        if inf_max is not None and n > inf_max:
            ok = False
    return ok and bool(reqs or inf_min is not None or inf_max is not None)
