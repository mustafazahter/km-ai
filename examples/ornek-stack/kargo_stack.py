"""Örnek kullanım senaryosu: küçük bir "kargo takip" stack'i, km-ai'ye köprüyle bağlanır.
Example scenario: a tiny "parcel tracking" stack that plugs into km-ai over the bridge.

    BRIDGE_TOKEN=kargo-token-change-me-0123456789 HTTP_HOST=127.0.0.1 HTTP_PORT=8081 \
        python examples/ornek-stack/kargo_stack.py

km-ai tarafında (.env veya Komuta ortamı):
    BRIDGE_KARGO_URL=http://127.0.0.1:8081
    BRIDGE_KARGO_TOKEN=kargo-token-change-me-0123456789

Sonra herhangi bir kanaldan: "1042 numaralı siparişim nerede?" ya da "1042'yi iade et" (onay ister).
Belge: README.md > Örnek kullanım senaryosu, docs/DEVELOPER.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # use the vendored stack_kit/ of this repo without installing

from stack_kit import ToolError, configure, emit, hook, run, tool

configure(name="kargo", version="0.1.0", events=["kargo.gecikti"])

# Gerçek projede burası veritabanınız ya da kargo firmasının API'si olur.
SIPARISLER = {
    "1042": {"durum": "dagitimda", "kargo": "Yurtiçi", "tahmini": "bugün 18:00", "tutar": 899.90},
    "1043": {"durum": "hazirlaniyor", "kargo": None, "tahmini": "2 gün", "tutar": 149.00},
}


@tool("kargo.durum", "Sipariş numarasıyla kargo durumunu söyler / Tells the parcel status of an order")
def durum(siparis_no: str) -> dict:
    kayit = SIPARISLER.get(siparis_no.strip())
    if kayit is None:
        raise ToolError(f"{siparis_no} numaralı sipariş bulunamadı", code="not_found")
    return {"siparis_no": siparis_no.strip(), **{k: v for k, v in kayit.items() if k != "tutar"}}


@tool("kargo.iade_baslat", "Siparişi iade eder, ücret iadesi yapar (onay ister) / Starts a return and refund (needs approval)",
      confirm=True, scope="admin")
def iade_baslat(siparis_no: str, neden: str = "") -> dict:
    kayit = SIPARISLER.get(siparis_no.strip())
    if kayit is None:
        raise ToolError(f"{siparis_no} numaralı sipariş bulunamadı", code="not_found")
    if kayit["durum"] == "iade":
        raise ToolError(f"{siparis_no} numaralı sipariş zaten iade edildi", code="invalid_args")
    kayit["durum"] = "iade"
    return {"siparis_no": siparis_no.strip(), "iade_tutari": kayit["tutar"], "durum": "iade", "neden": neden}


@hook("kargo-guncelle")
def kargo_guncelle(req):
    """Kargo firmasının webhook'u: durum değişince kaydı günceller, gecikmede olay yayar."""
    veri = req.json()
    no, yeni = str(veri.get("siparis_no", "")), str(veri.get("durum", ""))
    if no in SIPARISLER and yeni:
        SIPARISLER[no]["durum"] = yeni
        if yeni == "gecikti":
            emit("kargo.gecikti", {"siparis_no": no})   # WEBHOOKS_OUT_* ile n8n'e ya da başka bir akışa gider
    return {"ok": True}


if __name__ == "__main__":
    run(dotenv=None)
