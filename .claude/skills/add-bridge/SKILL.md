---
name: add-bridge
description: Connect this stack to another stack or to n8n through the bridge (BRIDGES or BRIDGE_<NAME>_URL + BRIDGE_<NAME>_TOKEN) and call remote tools. Use when the user wants two stacks or an external system to work together.
---

# Connect a bridge

## If this stack will call another stack

Environment (the two forms merge; on the same name the split form wins):

```bash
BRIDGES='mt5=http://mt5-trader:8080|<mt5 BRIDGE_TOKEN>'       # when running by hand
BRIDGE_MT5_URL=http://mt5-trader:8080                          # Komuta: valueFrom.service {stack, name, property: url}
BRIDGE_MT5_TOKEN=<mt5 BRIDGE_TOKEN>                            # Komuta: valueFrom.secret (prompt secret)
BRIDGE_MT5_URL=local                                           # if both stacks run in the same process
```

Komuta does no interpolation, so use the **split** form in `komuta.yaml`; there is no need to merge in the entrypoint, the kit merges them itself. The provider service needs `network.public: true` (stack-spec `examples/iki-stack-kopru.yaml`).

Check (prints no token):

```bash
python -m stack_kit doctor
#   - mt5 = http://mt5-trader:8080, token set
```

Kodda:

```python
from stack_kit import Bridges

bridges = Bridges.from_env()
tools = await bridges.tools()                     # each entry: {..., "bridge": "mt5"}
r = await bridges.call("mt5.price", {"symbol": "XAUUSD"})
if r.get("needs_confirmation"):                   # show the preview to a human; if approved:
    r = await bridges.call("mt5.price", {"symbol": "XAUUSD"}, confirm_id=r["confirm_id"])
```

To get the confirmation with a button when calling from Telegram: `await ctx.run_tool("mt5.buy", args, via=bridges)`.

## If an external system (n8n) will call this stack

`POST https://<service>/bridge/call/<tool>` · header `Authorization: Bearer <BRIDGE_TOKEN>` · body `{"args": {...}}`. Tool list: `GET /bridge/manifest` (same header). To see a remote bridge from here:

```bash
BRIDGE_TOKEN=<remote token> python -m stack_kit tools --url https://<remote-service>
```

Surfaces may be closed (bridge gate; closed = 404). Check and open:

```bash
python -m stack_kit gate status
python -m stack_kit gate enable bridge
```

