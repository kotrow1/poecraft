"""Проверка: есть ли в Windows английский пакет распознавания текста (код выхода 2, если нет)."""
import sys
try:
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    ok = OcrEngine.is_language_supported(Language("en-US"))
except Exception as e:
    print("OCR check failed:", e)
    sys.exit(1)
sys.exit(0 if ok else 2)
