---
name: add-ui-page
description: Add a page to this stack's admin UI (/ui) with the shared komuta-ui design language, a protected /ui/api endpoint and a test. Use when the user wants a new screen, form or status view in the panel.
---

# Yönetim arayüzüne sayfa ekle

Altyapı `stack_ui`'dedir (vendor edilmiş `stack_ui/`, `stack_kit/` yanında); ayrıntı `komuta-stack-ui` reposunda `docs/UI.md`.

1. Bu stack'in sayfa klasörünü bulun (`<static_dir>`; her stack'te farklı olabilir):
   ```bash
   grep -rn "mount_ui(" --include='*.py' --exclude-dir=stack_kit --exclude-dir=stack_ui --exclude-dir=.venv .
   ```
   `static_dir=` ile verilen yol `<static_dir>`'dir (çoğunlukla `Path(__file__).parent / "ui"` gibi paket içi bir yol). `mount_ui` hiç yoksa çekirdeğe ekleyin: `mount_ui(app, stack, static_dir=<yol>)`. Sayfa tek dosyada `#/rota` ile yönleniyorsa (hash routing) yeni dosya yerine yeni rota ekleyin.
   Yeni dosya için iskeleti kopyalayın:
   ```bash
   cp stack_ui/assets/example/index.html <static_dir>/<sayfa>.html
   ```
   `<main class="content">` içini değiştirin; bağlantıyı hem `.nav` hem `.bottombar` içine ekleyin; `<script src="example.js" defer>` yerine `<script src="<sayfa>.js" defer>`.
2. **Satır içi `<script>` ve `style="..."` yazmayın**: CSP engeller. Stil için `komuta-ui.css` sınıfları (`.card`, `.btn`, `.field`, `.table`, `.badge`…), kod için ayrı `.js`. Metni `KUI.el(...)`/`textContent` ile ekleyin, `innerHTML` kullanmayın.
3. Veri ucu (`APIRouter`; kimlik ve CSRF `mount_ui` tarafından eklenir):
   ```python
   from fastapi import APIRouter, Depends
   from stack_ui import UIUser, current_user

   router = APIRouter()

   @router.get("/<ad>")
   async def list_items(user: UIUser = Depends(current_user)):
       return {"ok": True, "items": []}
   ```
   ve mevcut `mount_ui(...)` çağrısının `api_router=[...]` listesine ekleyin (`mount_ui` uygulama başına bir kez çağrılır; ikinci çağrı hata verir). Tarayıcıda: `KUI.api("/<ad>")`, yazma: `KUI.api("/<ad>", {method: "POST", body: {...}})` (CSRF başlığı otomatik).
4. Para hareketi / geri alınamaz düğme: `KUI.confirm({title, body, danger: true})` ve sunucuda `confirm=True` araç (`stack.call` → `needs_confirmation` → `confirm_id`). Gizli değerleri asla geri döndürmeyin (`SecretStore.describe` yalnız `…son4` verir).
5. Test (`tests/test_ui_<ad>.py`):
   ```python
   from fastapi.testclient import TestClient
   from stack_kit import BridgeConfig, Stack, create_app
   from stack_ui import UIConfig, mount_ui

   STATIC_DIR = "<static_dir>"   # adım 1'deki yol

   def test_page_api():
       stack = Stack("test")
       app = create_app(stack, config=BridgeConfig(token="test-token-0123456789abcdef"), load_plugins=False)
       mount_ui(app, stack, static_dir=STATIC_DIR, api_router=router, config=UIConfig(auth="off", allow_insecure=True))
       with TestClient(app) as c:
           assert c.get("/ui/<sayfa>.html").status_code == 200
           assert c.get("/ui/api/<ad>").json()["ok"] is True
   ```
   ```bash
   python -m pytest -q tests
   ```
6. Elle bakmak için parola moduyla çalıştırın (`UI_AUTH=password UI_PASSWORD=<8+ karakter>` (`UI_PASSWORD` en az 8, panelde belirlenen parola en az 10 karakter)), `/ui/` açın; 375 px genişlikte yatay kaydırma olmamalı.

Yerleşim, uçları doğrulama (FastAPI rota listesi yerine `TestClient` ile çağırma), birleşik servis: `komuta-stack-ui` reposu `docs/UI.md` §10–12.

Tasarım kuralları: vurgu platin/çelik mavisi; altın, kahve, turuncu, bej, mor yok; serif ve soldan çizgili kart yok; dış CDN/yazı tipi yok.

## ai-bot'ta (bu repo)

ai-bot'un paneli tek sayfalık bir uygulamadır; yukarıdaki genel adımların yerine şunu izleyin:

1. **Sayfa:** `src/ai_bot/ui/pages/<ad>.js` yazın ve `A.pages.<ad> = { title: "...", render: function (main, route) {...} }` ile kaydedin. Yardımcılar `window.AIB` içinde (`widgets.js`: `chatManager`, `telegramToken`, `llmPicker`): `pageHead`, `card`, `button`, `field`, `badge`, `empty`, `skeleton`, `errorBox`, `callout`, `secretInput`, `icon`, `onLeave` (sayfadan çıkınca `KUI.poll`'u durdurun).
2. **Kayıt:** `src/ai_bot/ui/index.html` içine `<script src="pages/<ad>.js" defer></script>` (boot.js'ten önce); `app.js` içindeki `routes` tablosuna `"<adres>": "<ad>"`; menüye bağlantıyı hem `.nav` hem (gerekirse) `[data-sheet]` "Diğer" listesine `data-route="<ad>"` ile ekleyin. Alt menü en çok 5 öğedir.
3. **API:** `src/ai_bot/panel.py` içinde `Panel._build()`'e uç ekleyin (`/ui/api/<ad>`; kimlik ve CSRF `mount_ui` tarafından eklenir). Ayar değiştiren her yazma `self._save(values, secrets, user)` üzerinden gitsin: doğrular, saklar ve `LiveConfig.apply()` ile çalışan bota uygular.
4. **Yeni ayar:** `src/ai_bot/runtime_config.py` → `SCHEMA` (+ `FIELD_MAP`, `GROUP_OF`). Güvenlik kilidi olan hiçbir şeyi (onay kuralı, SSRF sınırları, izin listesi kuralı, `BRIDGE_TOKEN`, `UI_*`) şemaya EKLEMEYİN; alt sınırları varsayılanın altına indirmeyin.
5. **Sır:** `SECRET_NAMES`'e ekleyin; hiçbir yanıtta değeri döndürmeyin (`secrets.describe` yalnız `…son4`).
6. **Test:** `tests/test_ui.py`'deki `Env` düzeneğini kullanın (parola modu, sahte LLM/Telegram):
   ```bash
   .venv/bin/python -m pytest -q tests/test_ui.py
   ```
7. **Çeviri:** metinler Türkçe yazılır; İngilizcesi `src/ai_bot/ui/i18n-en.js` sözlüğüne (anahtar = Türkçe metin) eklenir. Eksikleri `python scripts/i18n_extract.py --missing` listeler; birleştirilmiş dinamik metinler için dosya sonundaki kurallara (`AIB_I18N_RULES`) düzenli ifade ekleyin.
8. **Periyodik yenilenen sayfa** (`KUI.poll`): içeriği her turda silip yeniden kurmayın; ilk çizimde yapıyı kurun, sonraki turlarda `A.morph(host, yeniDüğüm)` ile yalnız değişeni yerinde güncelleyin (titreme yok, odak kalır; yalnız dinleyicisiz işaretleme için), son veriyi modül değişkeninde tutup sayfaya dönüşte hemen çizin, yenileme hatasında ekranı bozmayın ("eski olabilir" notu). Örnek: `pages/overview.js`.
9. **Parola/sır alanı:** `A.secretInput` kullanın; kit her `type=password` alanına göz düğmesi ekler (`KUI.passwordField`), elle yazmayın. Arayüz metinlerinde emoji yok.
10. **Kurulum sihirbazı:** adımlar sunucudan gelir (`panel.setup_status`, kimlikler `panel`, `channel`, `channel_setup`/`telegram`+`chats`, `llm`, `search`, `summary`) ve seçilen kanala göre değişir; `pages/setup.js` içindeki `STEPS` her kimlik için bir işlev ister. Yeni adım eklerseniz hem sunucuda hem `STEPS`/`HINT` içinde tanımlayın, `ORDER` sunucudan eşitlenir (`syncOrder`).
11. **Göz kontrolü:** `UI_AUTH=password UI_PASSWORD=<8+> LLM_BASE_URL=http://127.0.0.1:9999/v1 PYTHONPATH=src:. .venv/bin/python -m ai_bot` (önce `scripts/fake_llm.py 9999`), `/ui/` aç; masaüstü + 375 px, açık + koyu tema, konsolda hata yok.
