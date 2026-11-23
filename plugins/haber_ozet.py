"""Gerçek örnek plugin: RSS/Atom akışından son başlıklar. / Real example: latest headlines from RSS/Atom.

AI bu aracı "haberleri özetle" gibi isteklerde kullanır; zamanlanmış bir istemle birleşince
"her sabah 9'da haberleri özetle" çalışır (schedule.add kind=prompt).

Ağ erişimi ai-bot'un SSRF korumalı getiricisiyle yapılır (`app.fetcher`), böylece plugin
yerel ağa / metadata adresine istek atamaz. Kendi araçlarında da bunu kullan.

Ayar: HABER_FEEDS (virgüllü URL listesi). Boşsa aşağıdaki varsayılanlar.
"""

import xml.etree.ElementTree as ET

from stack_kit import ToolError, env_list, get_stack, tool

DEFAULT_FEEDS = [
    "https://feeds.bbci.co.uk/turkce/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
]
MAX_ITEMS = 15


def parse_feed(xml_bytes: bytes, limit: int) -> list[dict]:
    """RSS 2.0 <item> veya Atom <entry> öğelerinden başlık/bağlantı/tarih çıkarır."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise ToolError("akış okunamadı (XML değil)", code="bad_feed") from None
    items: list[dict] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date = ""
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "title":
                title = (child.text or "").strip()
            elif ctag == "link":
                link = (child.text or child.get("href") or "").strip()
            elif ctag in ("pubDate", "updated", "published"):
                date = (child.text or "").strip()
        if title:
            items.append({"title": title, "link": link, "date": date})
        if len(items) >= limit:
            break
    return items


@tool("haber.basliklar", "RSS akışlarından son haber başlıklarını getirir (feed boşsa varsayılanlar) / "
      "Latest headlines from RSS feeds.", scope="read", timeout=45)
async def basliklar(feed: str = "", limit: int = 8) -> dict:
    app = getattr(get_stack(), "app_ai_bot", None)
    if app is None:
        raise ToolError("ai-bot kurulu değil", code="config")
    limit = max(1, min(limit, MAX_ITEMS))
    feeds = [feed] if feed else (env_list("HABER_FEEDS") or DEFAULT_FEEDS)
    out, errors = [], []
    for url in feeds[:5]:
        try:
            res = await app.fetcher.fetch(url)
            if res.status >= 400:
                raise ToolError(f"HTTP {res.status}")
            out.append({"feed": url, "items": parse_feed(res.body, limit)})
        except Exception as exc:  # one bad feed should not hide the others
            errors.append({"feed": url, "error": str(getattr(exc, "message", exc))[:200]})
    if not out:
        raise ToolError(f"hiçbir akış okunamadı: {errors}", code="fetch_failed")
    return {"feeds": out, "errors": errors}
