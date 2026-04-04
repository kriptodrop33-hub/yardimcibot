# ─── KriptoDropTR Bot Konfigürasyonu ──────────────────────────────────────────
# Railway environment variable'larından otomatik okunur.
# Yerel geliştirme için .env dosyası da kullanabilirsiniz.

import os
import sys

def _get(key: str, cast=str, required=True):
    val = os.environ.get(key)
    if not val:
        if required:
            print(f"❌ HATA: '{key}' environment variable tanımlı değil!")
            sys.exit(1)
        return None
    try:
        return cast(val)
    except Exception:
        print(f"❌ HATA: '{key}' değeri '{cast.__name__}' türüne çevrilemedi: {val!r}")
        sys.exit(1)

BOT_TOKEN    = _get("BOT_TOKEN")
ADMIN_ID     = _get("ADMIN_ID",   cast=int)
GROUP_ID     = _get("GROUP_ID",   cast=int)
CHANNEL_ID   = _get("CHANNEL_ID", cast=int, required=False)
GROK_API_KEY = _get("GROQ_API_KEY")   # Railway'deki değişken adı GROQ_API_KEY
