---
name: add-bridge
description: Connect this stack to another stack or to n8n through the bridge (BRIDGES or BRIDGE_<NAME>_URL + BRIDGE_<NAME>_TOKEN) and call remote tools. Use when the user wants two stacks or an external system to work together.
---

# Köprü bağla

## Bu stack başka bir stack'i çağıracaksa

Ortam (ikisi birleşir; aynı adda parçalı olan kazanır):

```bash
BRIDGES='mt5=http://mt5-trader:8080|<mt5 BRIDGE_TOKEN>'       # elle çalıştırırken
BRIDGE_MT5_URL=http://mt5-trader:8080                          # Komuta: valueFrom.service {stack, name, property: url}
BRIDGE_MT5_TOKEN=<mt5 BRIDGE_TOKEN>                            # Komuta: valueFrom.secret (prompt secret)
BRIDGE_MT5_URL=local                                           # iki stack aynı süreçteyse
```

Komuta interpolasyon yapmadığı için `komuta.yaml`'da **parçalı** biçimi kullan; entrypoint'te birleştirmeye gerek yok, kit kendisi birleştirir. Sağlayıcı serviste `network.public: true` gerekir (stack-spec `examples/iki-stack-kopru.yaml`).

Kontrol (token yazdırmaz):

```bash
python -m stack_kit doctor
#   - mt5 = http://mt5-trader:8080, token set
```

Kodda:

```python
from stack_kit import Bridges

bridges = Bridges.from_env()
tools = await bridges.tools()                     # her girdi: {..., "bridge": "mt5"}
r = await bridges.call("mt5.price", {"symbol": "XAUUSD"})
if r.get("needs_confirmation"):                   # önizlemeyi insana göster; onaylarsa:
    r = await bridges.call("mt5.price", {"symbol": "XAUUSD"}, confirm_id=r["confirm_id"])
```

Telegram'dan çağırırken onayı düğmeyle almak için: `await ctx.run_tool("mt5.buy", args, via=bridges)`.

## Dış sistem (n8n) bu stack'i çağıracaksa

`POST https://<servis>/bridge/call/<araç>` · başlık `Authorization: Bearer <BRIDGE_TOKEN>` · gövde `{"args": {...}}`. Araç listesi: `GET /bridge/manifest` (aynı başlıkla). Uzak bir köprüyü buradan görmek için:

```bash
BRIDGE_TOKEN=<uzak token> python -m stack_kit tools --url https://<uzak-servis>
```

Yüzeyler kapalı olabilir (köprü kapısı; kapalı = 404). Kontrol ve açma:

```bash
python -m stack_kit gate status
python -m stack_kit gate enable bridge
```

