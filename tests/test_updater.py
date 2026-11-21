"""Тесты обновления (без сети): python -m pytest tests  или  python tests/test_updater.py"""
import io
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import updater  # noqa: E402


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_versions():
    assert updater.parse_version("v1.2") == (1, 2, 0)
    assert updater.is_newer("v1.10.0", "1.9.3")
    assert not updater.is_newer("1.1.0", "1.1.0")


def test_install_keeps_user_files_and_blocks_bad_paths():
    top = "kotrow1-poecraft-abc123/"
    blob = make_zip({top + "app.py": "new app", top + "moddb.py": "same", top + "updater.py": "u", top + "i18n.py": "i",
                     top + "tests/x.py": "t", top + "config.json": "{}", top + "data/mods.json": "[]",
                     top + "../evil.py": "x", top + "sub/../../evil2.py": "x"})
    with tempfile.TemporaryDirectory() as d:
        for name, data in {"app.py": "old app", "moddb.py": "same", "config.json": "mine"}.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(data)
        n = updater.install_zip(blob, d, backup_name="1.0.0")
        read = lambda *p: open(os.path.join(d, *p)).read()
        assert n == 4                                       # app.py, updater.py, i18n.py, tests/x.py
        assert read("app.py") == "new app" and read("tests", "x.py") == "t"
        assert read("config.json") == "mine" and not os.path.exists(os.path.join(d, "data"))
        assert read("backup", "1.0.0", "app.py") == "old app"
        assert not os.path.exists(os.path.join(d, "backup", "1.0.0", "moddb.py"))   # не менялся
        assert not os.path.exists(os.path.join(os.path.dirname(d), "evil.py"))
        assert not any("evil" in f for _, _, fs in os.walk(d) for f in fs)


def test_install_rejects_foreign_zip():
    with tempfile.TemporaryDirectory() as d:
        try:
            updater.install_zip(make_zip({"x/readme.txt": "hi"}), d)
        except updater.UpdateError as e:
            assert "app.py" in str(e)
        else:
            raise AssertionError("чужой архив установлен")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
