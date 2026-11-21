"""Обновление программы из выпусков GitHub (без Windows, можно тестировать где угодно).

Выпуск = тег vX.Y.Z + GitHub Release (создаёт .github/workflows/release.yml по тегу).
Программа берёт последний выпуск, сравнивает с VERSION и при необходимости скачивает архив
исходников тега и раскладывает его поверх своей папки. data/ и config.json не трогаются,
заменённые файлы складываются в backup/.
"""
import io
import json
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile

from i18n import t

VERSION = "1.2.0"
REPO = "kotrow1/poecraft"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
ISSUES_URL = f"https://github.com/{REPO}/issues/new"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# что в папке программы обновление не трогает: пользовательское и служебное
KEEP = {"data", "config.json", "backup", ".git", "__pycache__"}
REQUIRED = ("app.py", "moddb.py", "updater.py", "i18n.py")    # без них архив — не наш выпуск
HEADERS = {"User-Agent": "poecraft-updater", "Accept": "application/vnd.github+json"}
MAX_ZIP = 20 * 1024 * 1024


class UpdateError(Exception):
    pass


def parse_version(text):
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(remote, local=None):
    return parse_version(remote) > parse_version(local or VERSION)


def _get(url, timeout):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(MAX_ZIP + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(t("выпусков не найдено (репозиторий закрыт или выпусков ещё нет)"))
        if e.code == 403:
            raise UpdateError(t("GitHub временно ограничил запросы, попробуй позже"))
        raise UpdateError(t("GitHub ответил {code}", code=e.code))
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(t("нет связи с GitHub ({e})", e=getattr(e, "reason", e)))


def check(timeout=10):
    """Последний выпуск: {'version', 'tag', 'notes', 'url', 'zip'}."""
    try:
        data = json.loads(_get(API_LATEST, timeout))
    except ValueError:
        raise UpdateError(t("непонятный ответ GitHub"))
    tag = data.get("tag_name") or ""
    if not tag or not data.get("zipball_url"):
        raise UpdateError(t("в выпуске нет тега или архива"))
    return {"version": tag.lstrip("vV"), "tag": tag, "notes": data.get("body") or "",
            "url": data.get("html_url") or RELEASES_URL, "zip": data["zipball_url"]}


def is_git_checkout(app_dir=APP_DIR):
    """Папка — клон git: такую обновляют через git pull, сами файлы не перезаписываем."""
    return os.path.isdir(os.path.join(app_dir, ".git"))


def _members(zf):
    """Файлы архива GitHub (в нём одна верхняя папка owner-repo-sha/) -> {отн. путь: имя в архиве}."""
    out = {}
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        parts = name.replace("\\", "/").split("/")[1:]
        if not parts or any(p in ("", ".", "..") or ":" in p for p in parts):
            continue                                   # пустые и подозрительные пути пропускаем
        if parts[0] in KEEP:
            continue
        out["/".join(parts)] = name
    return out


def install_zip(blob, app_dir=APP_DIR, backup_name="prev"):
    """Разложить архив выпуска поверх app_dir. Возвращает число записанных файлов."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise UpdateError(t("скачанный архив повреждён"))
    files = _members(zf)
    missing = [f for f in REQUIRED if f not in files]
    if missing:
        raise UpdateError(t("в архиве нет {files}", files=", ".join(missing)))
    root = os.path.realpath(app_dir)
    backup = os.path.join(root, "backup", backup_name)
    shutil.rmtree(backup, ignore_errors=True)
    written = 0
    for rel, name in sorted(files.items()):
        dst = os.path.realpath(os.path.join(root, *rel.split("/")))
        if not dst.startswith(root + os.sep):
            continue
        data = zf.read(name)
        if os.path.isfile(dst):
            with open(dst, "rb") as f:
                if f.read() == data:
                    continue
            bk = os.path.join(backup, *rel.split("/"))
            os.makedirs(os.path.dirname(bk), exist_ok=True)
            shutil.copy2(dst, bk)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".new"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        written += 1
    return written


def install(info, app_dir=APP_DIR, timeout=60):
    if is_git_checkout(app_dir):
        raise UpdateError(t("папка программы — клон git, обнови через git pull"))
    blob = _get(info["zip"], timeout)
    if len(blob) > MAX_ZIP:
        raise UpdateError(t("архив выпуска слишком большой"))
    return install_zip(blob, app_dir, backup_name=VERSION)
