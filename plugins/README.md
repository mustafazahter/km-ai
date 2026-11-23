# plugins/

> EN: Drop `*.py` files here. Every `@tool` becomes callable by the AI, over `/bridge` and over MCP.
> Money-moving tools must be `confirm=True` (+ `scope="trade"`): the AI cannot run them without a Telegram button press.

Bu klasördeki `*.py` dosyaları açılışta yüklenir (`_` ile başlayanlar atlanır). Her `@tool`:

- AI'ya görünür (`ornek.zar` → `ornek_zar`),
- `POST /bridge/call/ornek.zar` ve MCP `ornek_zar` ile çağrılabilir.

| Dosya | Ne |
|---|---|
| `ornek_arac.py` | En küçük örnek (`ornek.zar`) ve onaylı araç örneği (`ornek.bagis`, `confirm=True`, `scope="trade"`) |
| `haber_ozet.py` | Gerçek örnek: RSS başlıkları (`haber.basliklar`), SSRF korumalı `app.fetcher` ile |

Kurallar: para hareketi → `confirm=True`; dış istek → `get_stack().app_ai_bot.fetcher`; hata → `ToolError`.
Adım adım: `../docs/GELISTIRICI.md` ve `.claude/skills/add-ai-tool`.
