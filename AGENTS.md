# AGENTS.md — komuta ai-bot (Codex, Cursor, diğer ajanlar için; CLAUDE.md ile aynı kurallar)

AI altyapısı stack'i (Telegram, WhatsApp, Teams, Slack, e-posta ve panel kanalları): araç çağıran LLM (OpenAI uyumlu), web arama/okuma, zamanlanmış görev, hafıza, köprüler. Köprü/MCP/Telegram altyapısı vendor edilmiş `stack_kit/` (komuta-stack-kit), yönetim paneli altyapısı vendor `stack_ui/` (komuta-stack-ui) üzerinde.

## Harita

- `src/ai_bot/__init__.py` `setup(stack, bot=None)` — dışa açık giriş; birleşik servis (mt5-trader) bu imzaya güvenir, bozma
- `src/ai_bot/agent.py` araç döngüsü + onay kuralı · `llm.py` OpenAI uyumlu istemci · `core.py` `AiBot` kapsayıcı
- `src/ai_bot/builtin_tools.py` `web.*`, `schedule.*`, `memory.*`, `ai.ask` · `telegram_ui.py` yerleşik komutlar + Telegram serbest metni · `commands.py` komutları diğer kanallarda çalıştıran dağıtıcı + `@command` (her kanal)
- `src/ai_bot/safefetch.py` SSRF korumalı getirici · `search.py` arama sağlayıcıları · `htmltext.py` HTML→metin
- `src/ai_bot/scheduler.py` + `timeparse.py` (cron/göreli/ISO) · `memory.py` + `db.py` (SQLite, FTS5)
- Yönetim arayüzü `/ui`: `src/ai_bot/panel.py` (API, `mount_panel`, `default_auth="password"`) · `runtime_config.py` (ayar şeması, panel > ortam > varsayılan, `LiveConfig`) · `providers.py` (14 LLM sağlayıcısı, model listesi, bulut host kilidi) · `netpolicy.py` (panelden girilen adreslerin SSRF politikası) · `llm.py` (OpenAI uyumlu + Anthropic adaptörü) · `ui/` (`app.js`, `widgets.js`, `pages/*.js`) · `main.py` `build_app()` (köprü kapısı varsayılan kapalı) · belge `docs/ARAYUZ.md`
- `plugins/` geliştirici araçları (açılışta yüklenir) · `tests/` pytest (`tests/fakes.py`: sahte LLM/Telegram)
- `komuta.yaml`, `examples/*.yaml` Komuta manifestleri · `docs/` belgeler · `.claude/skills/` beceriler
- `stack_kit/` ve `stack_ui/` VENDOR — burada düzenleme; `stack-kit/scripts/sync-kit.sh . --skills` ve `stack-ui/scripts/sync-ui.sh . --skills` ile kopyala

## Komutlar (hepsi doğrulandı)

- Kurulum + test: `uv venv --python 3.12 && uv pip install -r requirements-dev.txt && .venv/bin/python -m pytest`
- Yerel çalıştırma: `scripts/setup.sh` sonra `PYTHONPATH=src:. .venv/bin/python -m ai_bot`
- Sahte LLM: `.venv/bin/python scripts/fake_llm.py 9999` + `LLM_BASE_URL=http://127.0.0.1:9999/v1`
- Plugin araçları: `.venv/bin/python -m stack_kit tools`
- Manifest denetimi: `.venv/bin/python ../stack-spec/tools/validate_stack.py . --allow-placeholders`
- Arayüzü yerelde aç: `LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:9999/v1 PYTHONPATH=src:. .venv/bin/python -m ai_bot` → günlükteki `Kurulum kodu` ile `http://127.0.0.1:8080/ui/setup` (veya `UI_PASSWORD=...`)
- Köprü kapısı: `.venv/bin/python -m stack_kit gate status` (varsayılan hepsi kapalı)

## Asla yapma

- Sır, token, chat id, e-posta commit'leme (`.env` ignore'da; `.env.example` boş kalır; manifestlerde değer yok, sırlar sihirbazdan veya Secret'tan).
- AI'nın para/işlem aracını onaysız çalıştırmasına izin verme: `confirm_id`'yi modele gösterme, `confirmed` geçirme, `HUMAN_SCOPES`/`needs_human` kuralını gevşetme, onaysız yol açma.
- SSRF korumasını gevşetme (`safefetch.py`: IP kontrolü, sabitleme, yönlendirme kontrolü, port/boyut/süre sınırı). Dış istekler için `app.fetcher` kullan.
- Hafızayı sohbetler arası paylaştırma; sır kalıplarını (`memory.BLOCK_PATTERNS`) gevşetme.
- İstem enjeksiyonu kilidini kaldırma (`UNTRUSTED_OUTPUT_TOOLS`/`PERSISTENT_TOOLS`, anı veri bloğu, zamanlanmış turda `schedule.add` yasağı); `ALLOWED_CHAT_IDS` boşken sohbet araçlarını açma.
- `/data` sahipliğini 1000 dışına chown etme; çalışma anında chown/setpriv/root başlangıcı ekleme. Komuta diski için uygulama uid 1000 olmalı (`USER 1000`); disk sahipliğini değiştirmek sonraki yayını başlatmaz.
- Arayüzden güvenlik kilidi açtırma: onay kuralı, SSRF sınırları (`WEB_READ_*` port/yönlendirme/boyut), "izin listesi boşken sohbet araçları kapalı", istem enjeksiyonu kilidi, `BRIDGE_TOKEN`, `UI_*` asla `runtime_config.SCHEMA`'ya girmez; alt sınırlar varsayılanın altına inmez.
- Sırrı geri gösterme: hiçbir `/ui/api` yanıtı, HTML/JS, log veya hata mesajı token/anahtar içermez (yalnız `secrets.describe` → `…son4`); test uçları verilen değeri geri yazmaz.
- `/ui/api` yazma uçlarını kimliksiz/CSRF'siz açma (`mount_ui` ekler; kendi router'ını `mount_ui` dışına bağlama); `UI_AUTH=off`'u varsayılan yapma; `default_auth`'u gevşetme.
- Köprü/MCP/hook'u varsayılan açık yapma (`main.GATE_DEFAULT` hepsi `False`); token'ı bir kezden fazla gösterme; kayıtlı LLM anahtarını başka host'a gönderme (`panel._llm_target`).
- Kurulum kodunu log dışında bir yere yazma (yalnız başlangıç günlüğüne bir kez, kit yapar).
- Ağ politikasını gevşetme (`netpolicy.py`): bulut ön ayarlarının host kilidi, link-local/metadata/0.0.0.0/kendi port engeli, IP sabitleme, yönlendirme izlememe; panel uçlarında ham sağlayıcı gövdesi yansıtma.
- `stack_kit/` içinde düzenleme; git commit/push (kullanıcı yapar).
