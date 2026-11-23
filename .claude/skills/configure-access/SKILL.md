---
name: configure-access
description: Manage who can reach this stack — admin panel sign-in (Komuta identity or password, first-time setup code, password change, sign out everywhere, protection switch) and the bridge gate (/bridge, /mcp, /hook on/off, tokens shown once). Use when the user asks to secure, open, close or reset access.
---

# Erişimi yönet

Tehdit modeli: servis internete açık, linki bilen herkes adrese ulaşabilir. Panel (`/ui`) ve makine yolları (`/bridge/*`, `/mcp`, `/hook/*`) kendi korumalarına dayanır.

## Panel girişi (`stack_ui`)

| Durum | Ne yapılır |
|---|---|
| Komuta'da yayın | Varsayılan `komuta.yaml`: `access.paths: [{path: /ui, signIn: true}]` + `forwardIdentity: true`; yalnız `/ui` Komuta girişi ister, `/health`, `/bridge/`, `/mcp`, `/hook/`, `/channels/` girişsiz açık ve kendi korumalarını (token, HMAC, JWT) taşır. Panel ayrıca kendi parolasını ister; `UI_AUTH=auto` ile Komuta kimliği varken parola sorulmaz. Adresi sabitlemek için `UI_PUBLIC_URL` veya `UI_ALLOWED_HOSTS=<public host>`. Komuta girişini tamamen kapatmak: `examples/komuta-giris-kapali.yaml` |
| Parola, ilk kurulum | `UI_AUTH=password` (veya `default_auth="password"`) ve `UI_PASSWORD` yok → `/ui/setup`. **Kurulum kodu** servisin başlangıç günlüğünde: Komuta konsolu → servis → **Loglar** → `Kurulum kodu`. Kod tek kullanımlık, yeniden başlatınca değişir; istenirse `UI_SETUP_CODE` ile verilir. Parola ≥ 10 karakter, scrypt ile `DATA_DIR/ui-auth.json`'a (600) yazılır. |
| Parola env'den | `UI_PASSWORD` verilirse kurulum atlanır; parola yalnız env'den değişir. |
| Parola değiştir / herkesi çıkar | Panel → Erişim → "Parolayı değiştir" (mevcut + yeni) veya "Tüm oturumları kapat". API: `POST /ui/api/auth/password`, `POST /ui/api/auth/sessions/revoke`. |
| Parolayı unuttum | Servisi durdurun, `DATA_DIR/ui-auth.json` dosyasını silin, başlatın → yeni kurulum kodu günlükte. |
| Korumayı kapat (önerilmez) | Panel → Erişim → parola + `KORUMAYI KAPAT`. Kapalıyken her sayfada kırmızı bant; tek tıkla açılır. Sıkı kurulum: `UI_FORCE_PROTECTION=1` (kapatma reddedilir). |

## Köprü kapısı (`stack_kit`)

```bash
python -m stack_kit gate status
python -m stack_kit gate disable mcp       # bridge | mcp | hook | all ; kapalı = 404
python -m stack_kit gate enable bridge
python -m stack_kit gate rotate            # yeni köprü token'ı, YALNIZ BİR KEZ yazdırılır
python -m stack_kit gate rotate hook-secret
```

Panelden: Erişim → Köprü kapısı (açınca token yoksa otomatik üretilir ve bir kez gösterilir). Env `BRIDGE_TOKEN` / `HOOK_SECRET` kapı değerleriyle birlikte geçerlidir. Başlangıç değerleri: `BRIDGE_ENABLED`, `MCP_ENABLED`, `HOOKS_ENABLED`.

Doğrulama:

```bash
python -m pytest -q tests
```

Asla: token, parola veya kurulum kodunu commit'leme, sohbete/issue'ya yapıştırma; korumayı "geçici" diye kapalı bırakma.
