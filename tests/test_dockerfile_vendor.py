"""Dockerfile her vendor/uygulama dizinini imaja kopyalamalı.

Yerel testler repo kökünü Python yoluna eklediği için eksik bir COPY satırı testlerde görünmez,
ama Komuta'daki imaj `ModuleNotFoundError` ile açılmaz. Bu test o hatayı yakalar.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _copied_sources() -> set[str]:
    sources: set[str] = set()
    for line in DOCKERFILE.splitlines():
        m = re.match(r"\s*COPY\s+(?:--\S+\s+)*(.+)", line)
        if not m:
            continue
        parts = m.group(1).split()
        # son öğe hedef; kalanlar kaynaklar
        for src in parts[:-1]:
            sources.add(src.rstrip("/").lstrip("./"))
    return sources


def test_vendored_packages_are_copied_into_the_image():
    copied = _copied_sources()
    for pkg in ("stack_kit", "stack_ui", "src", "plugins"):
        assert (ROOT / pkg).is_dir(), f"{pkg} dizini repoda yok"
        assert pkg in copied, f"Dockerfile `COPY {pkg}` satırı eksik: imaj açılışta ModuleNotFoundError verir"


def test_every_top_level_python_package_imported_by_the_app_is_copied():
    """src/ai_bot içinde içe aktarılan üst düzey vendor paketleri Dockerfile'da olmalı."""
    copied = _copied_sources()
    imported: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        for m in re.finditer(r"^\s*(?:from|import)\s+(stack_[a-z_]+)", path.read_text(encoding="utf-8"), re.M):
            imported.add(m.group(1))
    assert imported, "stack_* içe aktarması bulunamadı (test geçersiz)"
    missing = sorted(p for p in imported if p not in copied)
    assert not missing, f"Dockerfile şu paketleri kopyalamıyor: {missing}"
