"""Örnek plugin: kopyala, adını ve işini değiştir. / Example plugin: copy and adapt.

Bu dosyadaki her @tool otomatik olarak:
  * AI'ya görünür (ad noktası `_` olur: `ornek.zar` -> `ornek_zar`),
  * HTTP köprüsünde (`POST /bridge/call/ornek.zar`) ve MCP'de (`ornek_zar`) çağrılabilir.

Kural: para hareketi / geri alınamaz yan etki -> `confirm=True` (ve uygunsa scope="trade").
AI bu araçları kullanıcı Telegram'da onaylamadan ÇALIŞTIRAMAZ.
"""

import random

from stack_kit import ToolError, tool


@tool("ornek.zar", "Zar atar / Rolls dice. sides: yüz sayısı, count: kaç zar", scope="read")
def zar(sides: int = 6, count: int = 1) -> dict:
    if not 2 <= sides <= 1000 or not 1 <= count <= 20:
        raise ToolError("sides 2-1000, count 1-20 olmalı", code="invalid_args")
    rolls = [random.randint(1, sides) for _ in range(count)]
    return {"rolls": rolls, "total": sum(rolls)}


@tool("ornek.bagis", "Örnek onaylı araç: sahte bağış kaydı (gerçek para yok) / Example confirm tool (no real money)",
      confirm=True, scope="trade")
def bagis(miktar: float, hedef: str = "ornek-dernek") -> dict:
    if miktar <= 0:
        raise ToolError("miktar pozitif olmalı", code="invalid_args")
    # Gerçek bir ödeme burada olurdu. confirm=True olduğu için önizleme + kullanıcı onayı olmadan çalışmaz.
    return {"kaydedildi": True, "miktar": miktar, "hedef": hedef, "not": "örnek; para hareketi yok"}
