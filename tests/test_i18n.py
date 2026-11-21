"""Перевод: у каждого t("...") в коде есть английский вариант с теми же {полями}.
python -m pytest tests  или  python tests/test_i18n.py (без Windows и сети)"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import i18n  # noqa: E402


def literals(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.IfExp):
        yield from literals(node.body)
        yield from literals(node.orelse)


def keys_in_code():
    found = set()
    for name in ("app.py", "moddb.py", "updater.py"):
        tree = ast.parse(open(os.path.join(ROOT, name), encoding="utf-8").read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "t" and n.args:
                found.update(literals(n.args[0]))
            # таблицы, подписи из которых идут через t(переменная)
            if isinstance(n, ast.Assign) and any(getattr(tg, "id", "") in
                                                 ("HOTKEY_NAMES", "INF_OPTIONS", "MODE_OPTIONS", "SECTIONS")
                                                 for tg in n.targets):
                for c in ast.walk(n.value):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and re.search("[а-яё]", c.value, re.I):
                        found.add(c.value)
            if isinstance(n, ast.Assign) and any(getattr(tg, "id", "") == "checks" for tg in n.targets):
                for c in ast.walk(n.value):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and re.search("[а-яё]", c.value, re.I):
                        found.add(c.value)
    return found


def test_every_key_translated():
    missing = sorted(k for k in keys_in_code() if k not in i18n.EN)
    assert not missing, "нет перевода: " + " | ".join(missing)


def test_placeholders_match():
    field = re.compile(r"\{(\w+)\}")
    bad = [k for k, v in i18n.EN.items() if set(field.findall(k)) != set(field.findall(v))]
    assert not bad, bad


def test_no_cyrillic_in_english():
    bad = [v for v in i18n.EN.values() if re.search("[а-яё]", v, re.I)]
    assert not bad, bad


def test_t_not_shadowed():
    """Локальная переменная t в функции, которая зовёт t(), ломает её (UnboundLocalError)."""
    skip = (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp, ast.FunctionDef, ast.Lambda)
    bad = []
    for name in ("app.py", "moddb.py", "updater.py"):
        tree = ast.parse(open(os.path.join(ROOT, name), encoding="utf-8").read())
        for f in ast.walk(tree):
            if not isinstance(f, ast.FunctionDef):
                continue
            stack = list(ast.iter_child_nodes(f))
            while stack:
                n = stack.pop()
                if isinstance(n, skip):
                    continue
                if isinstance(n, ast.Name) and n.id == "t" and isinstance(n.ctx, ast.Store):
                    bad.append(f"{name}:{n.lineno} {f.name}")
                stack.extend(ast.iter_child_nodes(n))
    assert not bad, bad


def test_switch():
    i18n.set_lang("en")
    assert i18n.t("Версия {v}", v="1.2.0") == "Version 1.2.0"
    i18n.set_lang("ru")
    assert i18n.t("Версия {v}", v="1.2.0") == "Версия 1.2.0"
    i18n.set_lang("xx")
    assert i18n.LANG == "ru"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
