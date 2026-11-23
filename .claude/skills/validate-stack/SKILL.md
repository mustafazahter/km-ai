---
name: validate-stack
description: Run the stack-spec local validator on this repository (files, komuta.yaml shape, placeholders, secret leak scan) and fix what it reports. Use before committing, publishing or deploying.
---

# Stack'i doğrula

Gerekli: `komuta-stack-spec` deposu (yolu aşağıda `<stack-spec>`) ve PyYAML.

```bash
python -m pip install -r <stack-spec>/requirements.txt      # yalnız PyYAML
python <stack-spec>/tools/validate_stack.py .                # bu repo
python <stack-spec>/tools/validate_stack.py komuta.yaml      # yalnız manifest
python <stack-spec>/tools/validate_stack.py . --strict       # uyarılar da hata
python <stack-spec>/tools/validate_stack.py . --format json  # makine okunur çıktı
```

- Çıktı sonunda `GECTI: 0 hata, ...` görmelisin; çıkış kodu 0.
- `<...>` yer tutucuları yalnız şablon/örnek için `--allow-placeholders` ile kabul edilir; yayınlanacak repoda kullanma.
- Sır taraması bulgusu çıkarsa değeri dosyadan sil, Komuta'da `secrets` + `valueFrom.secret` ile ver; geçmişe commit edildiyse sırrı değiştir.
- Ardından testler: `python -m pytest -q tests` ve köprü/plugin durumu: `python -m stack_kit doctor`.

Bu yerel ön doğrulamadır; Komuta'nın kendi doğrulaması için `deploy-to-komuta` becerisine geç.
