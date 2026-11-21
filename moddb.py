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

DATA_URL = "https://raw.githubusercontent.com/repoe-fork/repoe-fork.github.io/master/data/"
DATA_FILES = {"mods.json": "mods.json",
              "base_items.json": "base_items.json",
              "stat_translations.json": "stat_translations.json",
              "cluster_jewels.json": "cluster_jewels.json"}
POOL_DOMAINS = {"item": ("item",), "affliction_jewel": ("affliction_jewel",)}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

INFLUENCES = {  # суффикс тега в spawn_weights -> название
    "shaper": "Shaper", "elder": "Elder", "crusader": "Crusader",
    "eyrie": "Redeemer", "basilisk": "Hunter", "adjudicator": "Warlord",
}
INFLUENCE_BY_NAME = {v: k for k, v in INFLUENCES.items()}

NUM_RE = re.compile(r"(?<![A-Za-z])[+-]?\d+(?:\.\d+)?(?![A-Za-z])")
RANGE_RE = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*[-–]\s*-?\d+(?:\.\d+)?\s*\)")


def download_data(force=False, log=print):
    os.makedirs(DATA_DIR, exist_ok=True)
    for local, remote in DATA_FILES.items():
        path = os.path.join(DATA_DIR, local)
        if os.path.exists(path) and not force:
            continue
        log(t("Скачиваю {name} ...", name=remote))
        urllib.request.urlretrieve(DATA_URL + remote, path)


def skeleton(text):
    """Текст строки -> 'скелет' без чисел, для сравнения с шаблоном."""
    s = text.lower().replace("\u2013", "-")
    s = RANGE_RE.sub("", s)
    s = NUM_RE.sub("#", s)
    s = re.sub(r"[^a-z#% ]", " ", s)
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
        self.trans = {}
        for e in st:
            if not e.get("English"):
                continue
            for sid in e["ids"]:
                self.trans.setdefault(sid, e)

    # ---------- шаблоны строк ----------
    def mod_lines(self, stats):
        """stats: [{'id','min','max'}] -> [LineTemplate]"""
        vals = {s["id"]: (s["min"], s["max"]) for s in stats}
        out, seen = [], set()
        for s in stats:
            e = self.trans.get(s["id"])
            if e is None or id(e) in seen:
                continue
            seen.add(id(e))
            ranges = [vals.get(i, (0, 0)) for i in e["ids"]]
            variant = self._pick_variant(e["English"], ranges)
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


def parse_item_text(db, text):
    """Текст из Ctrl+Alt+C -> ItemContext."""
    lines = [l.strip() for l in text.replace("\r", "").split("\n")]
    item_class = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Item Class:")), "")
    ilvl = next((int(l.split(":", 1)[1]) for l in lines if l.startswith("Item Level:")), 86)
    influences = [l.split()[0] for l in lines if l.endswith(" Item") and l.split()[0] in INFLUENCE_BY_NAME]
    header = []
    for l in lines[1:]:
        if l.startswith("--------"):
            break
        if not l.startswith("Rarity:"):
            header.append(l)
    best = None
    norm = lambda c: (c or "").lower().rstrip("s")
    for k, b in db.bases.items():
        if b.get("domain") not in POOL_DOMAINS or b.get("release_state") != "released":
            continue
        if item_class and norm(b.get("item_class")) != norm(item_class):
            continue
        if any(b["name"] in h for h in header):
            if best is None or len(b["name"]) > len(best[1]["name"]):
                best = (k, b)
    if best is None:
        raise ValueError(t("Не удалось определить базу предмета. Скопируй предмет через Ctrl+Alt+C."))
    extra_tags, enchant = [], ""
    cluster = db.clusters.get(best[0])
    if cluster:                                     # кластер: пул нотаблов зависит от энчанта
        ench = next((l for l in lines if "Passive Skills grant:" in l), "")
        ench = re.sub(r"\s*\(enchant\)\s*$", "", ench.split("grant:", 1)[-1]).strip()
        for sk in cluster["passive_skills"]:
            if any(skeleton(t) == skeleton(ench) for t in sk.get("stat_text", [])):
                extra_tags.append(sk["tag"])
                enchant = ench
                break
        if not extra_tags:
            raise ValueError(t("Не удалось распознать энчант кластера. Скопируй его через Ctrl+Alt+C."))
    ctx = build_context(db, best[1], ilvl, influences, extra_tags)
    ctx.enchant = enchant
    return ctx


def build_context(db, base, ilvl, influences, extra_tags=()):
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
                  weight, inf, False, db.mod_lines(m["stats"]))
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
                  0, sib.influence, True, db.mod_lines(m["stats"]))
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
            implicit_skels += [l.skel for l in db.mod_lines(m["stats"])]

    return ItemContext(base["name"], base["item_class"], ilvl, influences, tags,
                       implicit_skels, pool, groups, index)


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
