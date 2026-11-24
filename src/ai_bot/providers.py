"""LLM providers: presets, model listing, client factory and readable errors.

* Every provider except Anthropic is reached through its OpenAI compatible ``/chat/completions``
  endpoint (:class:`~ai_bot.llm.LLMClient`).
* Anthropic uses the native Messages API through :class:`~ai_bot.llm.AnthropicClient` (tool_use /
  tool_result mapping); its OpenAI compatible endpoint is not recommended for production.
* ``list_models`` fetches the provider's model list (OpenAI style ``GET {base}/models``, Anthropic
  ``GET /v1/models``, Ollama ``/api/tags``) so the panel can offer a searchable list.

Base URLs marked ``verified=False`` are from the providers' public docs but were not called from
this repository (no keys); see docs/LLM-PROVIDERS.md.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from stack_kit import redact

from .llm import ANTHROPIC_VERSION, AnthropicClient, LLMClient, LLMError
from .netpolicy import GuardedTransport, NetBlocked, NetPolicy
from .safefetch import Resolver

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,200}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    kind: str = "openai"          # "openai" (compatible) | "anthropic" (native adapter)
    key: bool = True              # needs an API key
    model: str = ""               # suggested default model
    models: str = "openai"        # how to list models: openai | anthropic | ollama | none
    hint: str = ""
    tools: bool | None = True     # tool calling support (None = depends on the model)
    local: bool = False
    custom: bool = False


PROVIDERS: list[Provider] = [
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", model="deepseek/deepseek-v4.1-flash-1017",
             hint="Tek anahtarla yüzlerce model. openrouter.ai/keys", tools=None),
    Provider("openai", "OpenAI (GPT)", "https://api.openai.com/v1", model="gpt-4o-mini",
             hint="platform.openai.com/api-keys"),
    Provider("anthropic", "Anthropic (Claude)", "https://api.anthropic.com", kind="anthropic", models="anthropic",
             model="claude-sonnet-4-5", hint="console.anthropic.com > API keys. Yerel Messages API adaptörü."),
    Provider("gemini", "Google (Gemini)", "https://generativelanguage.googleapis.com/v1beta/openai",
             model="gemini-2.5-flash", hint="aistudio.google.com/apikey. OpenAI uyumlu uç."),
    Provider("xai", "xAI (Grok)", "https://api.x.ai/v1", model="grok-4", hint="console.x.ai"),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1", model="llama-3.3-70b-versatile",
             hint="Çok hızlı açık modeller. console.groq.com/keys", tools=None),
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1", model="deepseek-chat",
             hint="platform.deepseek.com/api_keys"),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1", model="mistral-small-latest",
             hint="console.mistral.ai > API keys"),
    Provider("together", "Together", "https://api.together.xyz/v1", model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
             hint="api.together.ai > Settings > API keys", tools=None),
    Provider("perplexity", "Perplexity", "https://api.perplexity.ai", model="sonar", models="none",
             hint="Model listesi ucu yok: model adını elle girin (ör. sonar, sonar-pro). Araç çağırma desteklenmez.",
             tools=False),
    Provider("ollama", "Ollama (yerel)", "http://127.0.0.1:11434/v1", key=False, model="llama3.1", models="ollama",
             hint="Anahtar gerekmez. Docker içinden: http://host.docker.internal:11434/v1", tools=None, local=True),
    Provider("lmstudio", "LM Studio (yerel)", "http://127.0.0.1:1234/v1", key=False, model="",
             hint="Anahtar gerekmez. LM Studio > Developer > Start server", tools=None, local=True),
    Provider("vllm", "vLLM (yerel)", "http://127.0.0.1:8000/v1", key=False, model="",
             hint="vllm serve <model> ile; anahtar isteğe bağlı (--api-key)", tools=None, local=True),
    Provider("custom", "Özel (OpenAI uyumlu)", "", key=False, hint="Herhangi bir OpenAI uyumlu /chat/completions adresi.",
             tools=None, custom=True),
]
BY_ID = {p.id: p for p in PROVIDERS}
PROVIDER_IDS = [p.id for p in PROVIDERS]


def infer_provider(base_url: str) -> str:
    """Provider id for a base URL (used when LLM_PROVIDER is ``auto``)."""
    host = (urlsplit(base_url or "").hostname or "").lower()
    for p in PROVIDERS:
        if p.base_url and (urlsplit(p.base_url).hostname or "").lower() == host and not p.local:
            return p.id
    if host in ("127.0.0.1", "localhost", "::1", "host.docker.internal"):
        port = urlsplit(base_url).port
        return {11434: "ollama", 1234: "lmstudio", 8000: "vllm"}.get(port or 0, "custom")
    return "custom"


def resolve_provider(provider: str | None, base_url: str) -> Provider:
    pid = (provider or "auto").lower()
    if pid == "auto" or pid not in BY_ID:
        pid = infer_provider(base_url)
    return BY_ID[pid]


def is_cloud(p: Provider) -> bool:
    """Fixed cloud presets: their base URL is locked to the preset host."""
    return not p.local and not p.custom


def preset_host(p: Provider) -> str:
    return (urlsplit(p.base_url).hostname or "").lower()


def effective_base(p: Provider, base_url: str) -> str:
    """Cloud presets always use their own base URL (the admin cannot point them elsewhere)."""
    if is_cloud(p):
        if base_url and (urlsplit(base_url).hostname or "").lower() not in ("", preset_host(p)):
            raise NetBlocked(f"{p.name} adresi sabittir ({p.base_url}); başka adres için 'Özel' sağlayıcıyı seçin")
        return p.base_url
    return (base_url or p.base_url).rstrip("/")


def policy_for(p: Provider, *, allow_private_custom: bool = False, resolver: Resolver | None = None) -> NetPolicy:
    if is_cloud(p):
        return NetPolicy(allow_private=False, resolver=resolver, trusted_hosts={preset_host(p)})
    return NetPolicy(allow_private=p.local or allow_private_custom, resolver=resolver)


def guarded(p: Provider, transport: httpx.AsyncBaseTransport | None, *, allow_private_custom: bool = False,
            resolver: Resolver | None = None) -> GuardedTransport:
    return GuardedTransport(policy_for(p, allow_private_custom=allow_private_custom, resolver=resolver), transport)


def make_client(provider: str | None, base_url: str, api_key: str | None, *, timeout: float = 60.0,
                retries: int = 2, transport: httpx.AsyncBaseTransport | None = None,
                allow_private_custom: bool = False, resolver: Resolver | None = None) -> LLMClient:
    """OpenAI compatible client, or the Anthropic adapter for ``anthropic``. Every request passes the
    network policy (:mod:`ai_bot.netpolicy`); cloud presets are locked to their host."""
    p = resolve_provider(provider, base_url)
    try:
        base = effective_base(p, base_url)
    except NetBlocked:
        base = p.base_url  # a stale/edited URL for a cloud preset falls back to the preset
    gt = guarded(p, transport, allow_private_custom=allow_private_custom, resolver=resolver)
    if p.kind == "anthropic":
        return AnthropicClient(base, api_key, timeout=timeout, retries=retries, transport=gt)
    return LLMClient(base, api_key, timeout=timeout, retries=retries, transport=gt)


def friendly_error(exc: LLMError, provider: Provider) -> str:
    """A short Turkish explanation for the panel (no secrets: built from status codes and safe text)."""
    s = exc.status
    name = provider.name
    if s in (401, 403):
        return f"{name} anahtarı reddetti (HTTP {s}). Anahtarı kontrol edin; yalnız-yazılır alana yeniden girin."
    if s == 402:
        return f"{name}: bakiye/kota yetersiz (HTTP 402)."
    if s == 429:
        return f"{name}: hız veya kota sınırı (HTTP 429). Biraz bekleyin ya da planınızı kontrol edin."
    if s == 404:
        return f"{name}: model veya adres bulunamadı (HTTP 404). Model adını ve Base URL'yi kontrol edin."
    if s == 400:
        return f"{name}: istek reddedildi (HTTP 400). Model adını ve anahtarı kontrol edin."
    if s and 300 <= s < 400:
        return f"{name}: HTTP {s} yönlendirmesi izlenmez; son adresi doğrudan yazın."
    if s and s >= 500:
        return f"{name} sunucu hatası (HTTP {s}); biraz sonra tekrar deneyin."
    if s:
        return f"{name}: HTTP {s}."
    msg = exc.message or ""
    if msg.startswith(provider.name):  # our own text (e.g. "... bir model listesi ucu sunmuyor")
        return msg
    if "blocked by network policy" in msg:
        return f"{name}: adres ağ politikasınca engellendi."
    if msg.startswith(("model request timed out", "model endpoint unreachable", "model endpoint returned invalid JSON",
                       "model response")) or msg.startswith(("model listesi", "sağlayıcıya", "model listesi isteği")):
        return f"{name}: {msg}"
    return f"{name}: sağlayıcı bir hata döndürdü."  # never echo body text


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    tools: bool | None = None
    context: int | None = None
    price_in: float | None = None   # USD per 1M input tokens, when the provider publishes it
    price_out: float | None = None  # USD per 1M output tokens
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name or self.id, "tools": self.tools, "context": self.context,
                "price_in": self.price_in, "price_out": self.price_out}


def _price(value: Any, per_million: bool) -> float | None:
    """One published price as USD per 1M tokens, or None for anything odd (never raises)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != x or x < 0 or x > 1e9:  # NaN, negative, absurd (OpenRouter uses -1 for "varies")
        return None
    x = x if per_million else x * 1_000_000
    return round(x, 4)


def parse_pricing(model: dict) -> tuple[float | None, float | None]:
    """OpenRouter: ``pricing.prompt``/``completion`` in USD per token (strings). Together: ``pricing.input``/
    ``output`` in USD per 1M tokens. Others publish nothing."""
    pr = model.get("pricing")
    if not isinstance(pr, dict):
        return None, None
    if "prompt" in pr or "completion" in pr:
        return _price(pr.get("prompt"), False), _price(pr.get("completion"), False)
    if "input" in pr or "output" in pr:
        return _price(pr.get("input"), True), _price(pr.get("output"), True)
    return None, None


async def list_models(provider: Provider, base_url: str, api_key: str | None, *,
                      transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0,
                      allow_private_custom: bool = False, resolver: Resolver | None = None) -> list[ModelInfo]:
    """Fetch the provider's model list. Raises :class:`LLMError` (safe message, ``status`` set) or
    :class:`~ai_bot.netpolicy.NetBlocked`. Only parsed model ids/names are returned (never the raw body)."""
    base = effective_base(provider, base_url).rstrip("/")
    transport = guarded(provider, transport, allow_private_custom=allow_private_custom, resolver=resolver)
    if provider.models == "none":
        raise LLMError(f"{provider.name} bir model listesi ucu sunmuyor; model adını elle girin.", None)
    headers: dict[str, str] = {"Accept": "application/json"}
    if provider.models == "anthropic":
        url = (base[:-3] if base.endswith("/v1") else base) + "/v1/models?limit=1000"
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if api_key:
            headers["x-api-key"] = api_key
    elif provider.models == "ollama":
        root = base[:-3] if base.endswith("/v1") else base
        url = root + "/api/tags"
    else:
        url = base + "/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
            if provider.models == "ollama" and resp.status_code == 404:  # older/compatible servers
                resp = await client.get(base + "/models", headers=headers)
    except httpx.TimeoutException:
        raise LLMError("model listesi isteği zaman aşımına uğradı", None) from None
    except httpx.HTTPError as exc:
        if "blocked by network policy" in str(exc):
            raise NetBlocked(str(exc).split("blocked by network policy: ", 1)[-1]) from None
        raise LLMError(f"sağlayıcıya ulaşılamadı ({type(exc).__name__})", None) from None
    if 300 <= resp.status_code < 400:
        raise LLMError(f"model listesi HTTP {resp.status_code} (yönlendirme izlenmez)", resp.status_code)
    if resp.status_code >= 400:
        raise LLMError(f"model listesi HTTP {resp.status_code}", resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        raise LLMError("model listesi geçersiz JSON döndü", resp.status_code) from None
    out: list[ModelInfo] = []
    if isinstance(data, dict) and isinstance(data.get("models"), list):  # ollama /api/tags
        for m in data["models"][:5000]:
            if isinstance(m, dict) and isinstance(m.get("name") or m.get("model"), str):
                out.append(ModelInfo(str(m.get("name") or m.get("model")), tools=None))
    items = data.get("data") if isinstance(data, dict) else data if isinstance(data, list) else None
    for m in (items or [])[:5000]:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str):
            continue
        mid = m["id"]
        if provider.id == "gemini" and mid.startswith("models/"):
            mid = mid[len("models/"):]
        tools: bool | None = provider.tools if provider.tools is not None else None
        params = m.get("supported_parameters")
        if isinstance(params, list):  # OpenRouter publishes this per model
            tools = "tools" in params
        if provider.kind == "anthropic":
            tools = True
        ctx = m.get("context_length") or m.get("context_window")
        p_in, p_out = parse_pricing(m)
        out.append(ModelInfo(mid, name=_CONTROL.sub("", str(m.get("display_name") or m.get("name") or ""))[:80],
                             tools=tools, context=ctx if isinstance(ctx, int) else None,
                             price_in=p_in, price_out=p_out))
    seen: set[str] = set()
    unique = []
    for m in out:
        if m.id not in seen and MODEL_ID_RE.match(m.id):  # odd ids (control chars, html, long text) dropped
            seen.add(m.id)
            unique.append(m)
    unique.sort(key=lambda m: m.id.lower())
    _ = time.monotonic() - started
    return unique[:2000]


def public_providers() -> list[dict[str, Any]]:
    return [{"id": p.id, "name": p.name, "base_url": p.base_url, "kind": p.kind, "key": p.key, "model": p.model,
             "models": p.models, "hint": p.hint, "tools": p.tools, "local": p.local, "custom": p.custom}
            for p in PROVIDERS]


def safe_message(text: str) -> str:
    return redact(text)[:300]
