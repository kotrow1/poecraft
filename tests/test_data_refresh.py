"""Автообновление базы модов (без сети: загрузки подменены). python tests/test_data_refresh.py"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import moddb  # noqa: E402


def run(version, data_dir):
    fetched = []

    def fake_fetch(remote, path, log):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(version)
        fetched.append(remote)
    moddb._fetch, moddb.remote_version, moddb.DATA_DIR = fake_fetch, (lambda: version), data_dir
    changed = moddb.refresh_data(log=lambda *a: None)
    return changed, fetched


def test_refresh_only_when_remote_changes():
    orig = (moddb._fetch, moddb.remote_version, moddb.DATA_DIR)
    try:
        with tempfile.TemporaryDirectory() as d:
            changed, got = run("v1", d)                         # первый запуск: всё скачать
            assert not changed and sorted(got) == sorted(moddb.DATA_FILES.values())
            assert run("v1", d) == (False, [])                  # та же выгрузка — ничего не качаем
            os.makedirs(os.path.join(d, "Russian"))             # русские данные уже были
            changed, got = run("v2", d)                         # патч: всё заново, и русское тоже
            assert changed and "mods.json" in got and "Russian/stat_translations.json" in got
            assert open(os.path.join(d, "mods.json")).read() == "v2"

            def offline():
                raise OSError("нет сети")
            moddb.remote_version = offline                      # нет связи — работаем на старом
            assert moddb.refresh_data(log=lambda *a: None) is False
    finally:
        moddb._fetch, moddb.remote_version, moddb.DATA_DIR = orig


if __name__ == "__main__":
    test_refresh_only_when_remote_changes()
    print("ok test_refresh_only_when_remote_changes")
