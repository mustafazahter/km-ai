---
name: deploy-to-komuta
description: Validate, plan and publish this stack on Komuta from komuta.yaml using the Komuta MCP tools, with secrets entered as prompt secrets. Use when the user wants to deploy or update the stack on Komuta.
---

# Komuta'ya yayınla

Ön koşul: Claude oturumunda Komuta MCP bağlı; Komuta'da hedef proje **önceden var** (`apply.createMissingProject: false`); repo, Komuta'nın erişebildiği bir git bağlantısında.

1. Yerel kontroller (hepsi temiz olmalı):
   ```bash
   python -m pytest -q tests
   python <stack-spec>/tools/validate_stack.py .
   python -m stack_kit doctor
   ```
2. `komuta.yaml` içinde kullanıcıya özgü yer tutucuları doldur (`target.project`, `target.connections.git`, `source.repository`). Sır değeri **yazma**: `BRIDGE_TOKEN`, `HOOK_SECRET`, `TELEGRAM_BOT_TOKEN`, `*_TOKEN`, `*_SECRET` her zaman `secrets` (kaynak `prompt` veya `generate`) + `valueFrom.secret` ile bağlanır.
3. Komuta MCP ile, sırayla (her adımın çıktısını kullanıcıya göster):
   1. `komuta_validate_stack` — manifesti doğrula; hata varsa düzelt ve tekrarla.
   2. `komuta_plan_stack` — ne oluşacağını/değişeceğini göster; prompt secret'ları kullanıcı burada/Komuta arayüzünde girer.
   3. `komuta_apply_stack` — **yalnız kullanıcı planı açıkça onayladıktan sonra.**
   4. `komuta_get_stack_run` — çalışmanın durumunu izle.
4. Yayından sonra: `GET https://<servis-adresi>/health` → `{"ok": true, ...}`; korumalı uçlar için `Authorization: Bearer <BRIDGE_TOKEN>`.

Kurallar: kullanıcının onayı olmadan apply etme; sırları sohbet geçmişine, dosyaya veya commit'e yazma; para hareketi yapan stack'lerde varsayılan modu (ör. `MODE=demo`) değiştirme.

Not: Bu beceri kit geliştirilirken gerçek bir Komuta apply ile denenmedi (repolar henüz yayında değil); araç adları Komuta MCP araç listesinden alındı.
