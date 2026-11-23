#!/usr/bin/env python3
"""List the user-visible Turkish strings of the admin UI (for the English dictionary).

    python scripts/i18n_extract.py            # print candidates as JSON
    python scripts/i18n_extract.py --missing  # only those without an entry in src/ai_bot/ui/i18n-en.js

Sources: src/ai_bot/ui/**/*.js|html and the vendored stack_ui/assets/*.js|html. A string counts as visible text when it
has a Turkish letter or a space-separated phrase / capitalised word and does not look like code (selector, path, id).
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [*sorted(f for f in (ROOT / "src/ai_bot/ui").rglob("*.js") if f.name != "i18n-en.js"), *sorted((ROOT / "src/ai_bot/ui").glob("*.html")),
         *sorted((ROOT / "stack_ui/assets").glob("*.js")), *sorted((ROOT / "stack_ui/assets").glob("*.html"))]
TR_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜâîû]")
TR_WORDS = re.compile(r"\b(ve|bir|için|ile|değil|yok|var|bu|veya|kadar|önce|sonra|gerekli|ayar|kaydet|sil|aç|kapat|tamam|hata|"
                      r"sohbet|bot|anahtar|sunucu|adres|liste|görev|hafıza|arama|model|panel|parola|kurulum|giriş|çıkış|yeni|"
                      r"son|tüm|hepsi|geri|ileri|devam|atla|düzenle|test|dene|bağlan|bağlı|kapalı|açık|eksik|hazır)\b", re.I)
LIT = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
HTML_TEXT = re.compile(r">([^<>{}]+)<")
HTML_ATTR = re.compile(r'\b(?:aria-label|placeholder|title|alt)="([^"]+)"')


def looks_like_code(s: str) -> bool:
    if not s.strip() or len(s) > 400:
        return True
    if re.match(r"^(https?:|/|#|\.|--|data-|aria-|icons\.svg|logo\.svg)", s):
        return True
    if re.fullmatch(r"[a-z][a-z0-9_-]*( [a-z0-9_-]+)*", s) and not TR_CHARS.search(s):
        return True  # css classes, ids, event names, lower-case identifiers
    if re.fullmatch(r"[\w:.\-\[\]=,>~*()%{}$@!&|+/ ]*", s) and re.search(r"[.\[\]=>{}()]|__|::", s) and not TR_CHARS.search(s) and " " not in s:
        return True  # selectors, expressions
    return False


def candidates() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        strings: list[str] = []
        if path.suffix == ".html":
            strings += [html.unescape(m.group(1)).strip() for m in HTML_TEXT.finditer(text)]
            strings += [html.unescape(m.group(1)).strip() for m in HTML_ATTR.finditer(text)]
        else:
            for m in LIT.finditer(text):
                s = bytes(m.group(1), "utf-8").decode("unicode_escape", errors="ignore") if "\\" in m.group(1) else m.group(1)
                try:
                    s = json.loads('"' + m.group(1) + '"')
                except ValueError:
                    pass
                strings.append(s.strip())
        for s in strings:
            if looks_like_code(s):
                continue
            if TR_CHARS.search(s) or TR_WORDS.search(s) or (" " in s and re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", s)) \
                    or re.fullmatch(r"[A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,}", s):
                found.setdefault(s, set()).add(str(path.relative_to(ROOT)))
    return found


def have() -> set[str]:
    f = ROOT / "src/ai_bot/ui/i18n-en.js"
    if not f.exists():
        return set()
    body = f.read_text(encoding="utf-8")
    m = re.search(r"AIB_I18N_EN\s*=\s*(\{.*?\n\});", body, re.S)  # the dictionary object ends with "\n};"
    try:
        return set(json.loads(m.group(1)).keys()) if m else set()
    except ValueError:
        return set()


def main() -> None:
    found = candidates()
    if "--missing" in sys.argv:
        known = have()
        found = {k: v for k, v in found.items() if k not in known}
    json.dump(sorted(found), sys.stdout, ensure_ascii=False, indent=1)
    print()
    print(f"# {len(found)} strings", file=sys.stderr)


if __name__ == "__main__":
    main()
