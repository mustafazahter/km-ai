---
name: add-memory-policy
description: Change what ai-bot remembers or refuses to remember (system prompt rules, secret filters, limits, how many memories go into context) with tests. Use when the user says the bot remembers too much/too little or must never store some kind of data.
---

# Hafıza politikasını değiştir

Neyin hatırlanacağına AI karar verir; kurallar üç yerdedir:

| Ne | Nerede |
|---|---|
| AI'ya ne zaman hatırlayıp unutacağını öğreten metin | `src/ai_bot/agent.py` → `SYSTEM_PROMPT` içindeki `HAFIZA / MEMORY` bölümü |
| Kesin red (AI istese de kaydedilmez): token, kart, şifre kalıpları | `src/ai_bot/memory.py` → `BLOCK_PATTERNS` |
| Sınırlar | env: `MEMORY_MAX_PER_CHAT` (500), `MEMORY_CONTEXT_LIMIT` (her mesaja eklenen anı sayısı, 6); kod: `MAX_MEMORY_CHARS` |

Adımlar:
1. Yasak veri türü eklemek için `BLOCK_PATTERNS`'e regex ekle (örn. IBAN):
   ```python
   re.compile(r"\bTR\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.IGNORECASE),
   ```
2. Davranış değişikliği için `SYSTEM_PROMPT`'taki maddeleri düzenle; kısa ve emir kipinde yaz. Ek talimatı kod değiştirmeden vermek için `SYSTEM_PROMPT_EXTRA` env'i de var.
3. Test ekle (`tests/test_memory.py::test_secrets_are_not_stored` parametre listesine yeni örnek) ve koş:
   ```bash
   .venv/bin/python -m pytest tests/test_memory.py tests/test_agent.py
   ```
4. Kullanıcıya dönük anlatımı `docs/HAFIZA.md`'de güncelle.

Asla: hafızayı sohbetler arası paylaştırma (her sorgu `chat_id` ile süzülür), sır kalıplarını gevşetme.
