"""Язык интерфейса: русский (исходный текст в коде) и английский.

t("русский текст {x}", x=...) -> строка на текущем языке. Ключ — сам русский текст; перевод на
английский — в EN. Нет перевода — показывается русский (и тест tests/test_i18n.py это ловит).
"""
import ctypes
import locale

LANGS = {"ru": "Русский", "en": "English"}
LANG = "ru"


def set_lang(lang):
    global LANG
    LANG = lang if lang in LANGS else "ru"


def detect():
    """Язык Windows (или системы) при первом запуске: русский — ru, остальное — en."""
    try:
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x19 else "en"
    except Exception:
        pass
    try:
        return "ru" if (locale.getlocale()[0] or "").lower().startswith("ru") else "en"
    except Exception:
        return "ru"


def t(text, **kw):
    s = EN.get(text, text) if LANG == "en" else text
    return s.format(**kw) if kw else s


EN = {
    # горячие клавиши
    "Импорт": "Import",
    "Калибровка": "Calibration",
    "Охрана вкл/выкл": "Guard on/off",
    "Тест OCR": "OCR test",
    "{action}: клавиша {key}": "{action}: key {key}",
    # хуки и охрана
    "Shift-замок: Shift удерживается — можно отпустить и кликать только ЛКМ":
        "Shift lock: Shift is held — you can release it and just left-click",
    "Shift-замок снят": "Shift lock released",
    "Shift-замок снят, Shift отпущен": "Shift lock released, Shift let go",
    "Калибровка: курсор {x},{y}; область {region}": "Calibration: cursor {x},{y}; area {region}",
    "Блок снят, охрана остаётся включённой": "Block lifted, guard stays on",
    "ОХРАНА ВКЛЮЧЕНА": "GUARD ON",
    "Охрана выключена": "Guard off",
    # OCR
    "в Windows не установлен пакет распознавания для английского (en-US)":
        "the English (en-US) text recognition pack is not installed in Windows",
    "Ошибка OCR: {e}": "OCR error: {e}",
    "Область сужена до подсказки: {w}x{h}": "Area narrowed to the tooltip: {w}x{h}",
    "[захват {a} + OCR {b} + разбор {c} = {d} мс]": "[capture {a} + OCR {b} + parse {c} = {d} ms]",
    "ТЕСТ OCR, сырые строки:": "OCR TEST, raw lines:",
    "СОВПАЛО!": "MATCH!",
    # условия цели
    "любое": "any",
    "ровно 1": "exactly 1",
    "ровно 2": "exactly 2",
    "ровно 3": "exactly 3",
    "не меньше 1": "at least 1",
    "не меньше 2": "at least 2",
    "ни одного": "none",
    "все выбранные": "all selected",
    "любой из выбранных": "any selected",
    # шаги
    "1  База": "1  Base",
    "2  Калибровка": "2  Calibration",
    "3  Цель": "3  Target",
    "4  Охрана": "4  Guard",
    "⟳ Новая база": "⟳ New base",
    "⟳ Новая база ({key})": "⟳ New base ({key})",
    "⚙ Настройки": "⚙ Settings",
    "без влияния": "no influence",
    "Ctrl+Alt+C в игре → {key} или клик сюда": "Ctrl+Alt+C in game → {key} or click here",
    "готово · {key} для повтора": "done · {key} to redo",
    "наведи на предмет → {key}": "hover the item → {key}",
    "{n} мод(а)": "{n} mod(s)",
    "выбери моды слева": "pick mods on the left",
    "до {key}": "until {key}",
    "{s} с": "{s} s",
    "КЛИК ЗАБЛОКИРОВАН · {left}": "CLICK BLOCKED · {left}",
    "включена · {key} — выкл": "on · {key} — off",
    "выключена · {key} или клик сюда": "off · {key} or click here",
    "не вышло — сначала Ctrl+Alt+C": "failed — press Ctrl+Alt+C first",
    # список модов
    "Моды базы": "Base mods",
    "скрыть недоступные": "hide unavailable",
    "Все": "All",
    "Префиксы": "Prefixes",
    "Суффиксы": "Suffixes",
    "Мод (лучший доступный тир)": "Mod (best available tier)",
    "Шанс": "Chance",
    "Тиры": "Tiers",
    "Теги": "Tags",
    "Клик по ＋ или двойной клик — добавить в цель / убрать.  Шанс — доля веса мода среди префиксов "
    "(суффиксов) этой базы; поиск ищет и по тегам (attack, life, critical...)":
        "Click ＋ or double-click — add to / remove from the target.  Chance is the mod's share of weight "
        "among this base's prefixes (suffixes); search also matches tags (attack, life, critical...)",
    "Любое": "Any",
    "Обычные": "Normal",
    "Элевейтед": "Elevated",
    "обычные": "normal",
    "элевейтед": "elevated",
    # цель
    "Цель": "Target",
    "Блокировать, когда есть": "Block when there is",
    "Модов влияния на предмете": "Influenced mods on item",
    "Пресет": "Preset",
    "Сохранить": "Save",
    "Удалить": "Delete",
    "Добавь моды из списка слева — нажми ＋ у нужного мода.": "Add mods from the list on the left — press ＋ next to a mod.",
    "Префикс": "Prefix",
    "Суффикс": "Suffix",
    "не хуже T{tier}": "T{tier} or better",
    "(нужен ilvl {lvl})": "(needs ilvl {lvl})",
    "Введи имя пресета в поле и нажми «Сохранить».": "Type a preset name in the field and press “Save”.",
    "Пресет «{name}» сохранён": "Preset “{name}” saved",
    "Пресет загружен, но {n} мод(ов) не может выпасть на этой базе": "Preset loaded, but {n} mod(s) can't roll on this base",
    # низ
    "Последний ролл": "Last roll",
    "Журнал": "Log",
    "СОВПАЛО — клик заблокирован": "MATCH — click blocked",
    "{ms} мс": "{ms} ms",
    # настройки
    "Настройки": "Settings",
    "О программе": "About",
    "Язык": "Language",
    "Горячие клавиши": "Hotkeys",
    "Нажми кнопку, затем новую клавишу (Esc — отмена)": "Press a button, then the new key (Esc — cancel)",
    "Охрана": "Guard",
    "Блокировать клик, секунд": "Block the click, seconds",
    "Держать блок до повторного нажатия клавиши охраны": "Hold the block until the guard key is pressed again",
    "Пошаговый режим: следующий клик только после проверки": "Step mode: next click only after the check",
    "Shift-замок: Shift залипает после первого клика": "Shift lock: Shift sticks after the first click",
    "Окно программы поверх игры": "Keep the window above the game",
    "Общие": "General",
    "Отмена": "Cancel",
    "нажми...": "press...",
    # о программе и обновления
    "Версия {v}": "Version {v}",
    "Что нового": "Release notes",
    "Сообщить об ошибке на GitHub": "Report a bug on GitHub",
    "Обновлять автоматически (проверка при запуске и раз в 6 часов)":
        "Update automatically (check at startup and every 6 hours)",
    "никогда": "never",
    "только что": "just now",
    "{n} мин назад": "{n} min ago",
    "{n} ч назад": "{n} h ago",
    "{n} дн назад": "{n} d ago",
    "Последняя проверка: {when}": "Last checked: {when}",
    "Устанавливаю версию {v}...": "Installing version {v}...",
    "Проверяю...": "Checking...",
    "Версия {v} установлена — перезапусти программу": "Version {v} installed — restart the program",
    "Не удалось: {e}": "Failed: {e}",
    "Доступна версия {v} — папка из git, обнови через git pull": "Version {v} is available — git folder, update with git pull",
    "Доступна версия {v}": "Version {v} is available",
    "Установить {v}": "Install {v}",
    "Установлена последняя версия": "You have the latest version",
    "Обновления ещё не проверялись": "Updates haven't been checked yet",
    "Проверить сейчас": "Check now",
    "Подождите...": "Please wait...",
    "Доступна новая версия {v} (сейчас {cur})": "New version {v} is available (current {cur})",
    "Обновлено до версии {v} — перезапусти программу, чтобы она заработала":
        "Updated to version {v} — restart the program to use it",
    "Обновление не установлено: {e}": "Update not installed: {e}",
    "Проверка обновлений: {e}": "Update check: {e}",
    # updater.py
    "выпусков не найдено (репозиторий закрыт или выпусков ещё нет)": "no releases found (private repository or no releases yet)",
    "GitHub временно ограничил запросы, попробуй позже": "GitHub is rate-limiting requests, try later",
    "GitHub ответил {code}": "GitHub responded {code}",
    "нет связи с GitHub ({e})": "can't reach GitHub ({e})",
    "непонятный ответ GitHub": "unexpected response from GitHub",
    "в выпуске нет тега или архива": "the release has no tag or archive",
    "скачанный архив повреждён": "the downloaded archive is damaged",
    "в архиве нет {files}": "the archive lacks {files}",
    "папка программы — клон git, обнови через git pull": "the program folder is a git clone, update with git pull",
    "архив выпуска слишком большой": "the release archive is too large",
    # база модов
    "База модов готова. Ctrl+Alt+C на предмете в игре → {key}.": "Mod database ready. Ctrl+Alt+C on an item in game → {key}.",
    "Не удалось загрузить базу: {e}": "Failed to load the mod database: {e}",
    "База модов ещё загружается...": "Mod database is still loading...",
    "Импорт не удался: {e}": "Import failed: {e}",
    "Импортирована база: {name}": "Base imported: {name}",
    "СОВПАЛО — левый клик заблокирован": "MATCH — left click blocked",
    "Скачиваю {name} ...": "Downloading {name} ...",
    "Загружаю базу модов ...": "Loading mod database ...",
    "Не удалось определить базу предмета. Скопируй предмет через Ctrl+Alt+C.":
        "Couldn't identify the item base. Copy the item with Ctrl+Alt+C.",
    "Не удалось распознать энчант кластера. Скопируй его через Ctrl+Alt+C.":
        "Couldn't recognize the cluster enchant. Copy it with Ctrl+Alt+C.",
    # индикатор поверх игры
    "⛔ БЛОК до {key}": "⛔ BLOCK until {key}",
    "⛔ БЛОК {s} с": "⛔ BLOCK {s} s",
    "⏳ проверка...": "⏳ checking...",
    "🟢 ОХРАНА": "🟢 GUARD",
    "⚪ охрана выкл": "⚪ guard off",
}
