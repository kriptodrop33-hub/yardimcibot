#!/usr/bin/env python3
"""KriptoDropTR Telegram Botu v4.0 — Scheduler + Deadline + Kullanıcı Sistemi + Admin Ayarları"""

import sqlite3, logging, httpx
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from config import BOT_TOKEN, ADMIN_ID, GROUP_ID, GROK_API_KEY

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ── CONVERSATION STATES ───────────────────────────────────────────────────────
(AIRDROP_NAME, AIRDROP_PROJECT, AIRDROP_DESC, AIRDROP_REWARD,
 AIRDROP_LINK, AIRDROP_DEADLINE, AIRDROP_CATEGORY,
 NEWS_TOPIC, NEWS_PREVIEW,
 ANNOUNCE_TEXT, ANNOUNCE_CONFIRM,
 PRICE_COIN,
 SETTINGS_INPUT) = range(13)

CATEGORIES = ["🪙 DeFi","🎮 GameFi","🖼 NFT","🔗 Layer1/Layer2","📱 Web3","🌐 Diğer"]
BACK_ADMIN    = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
BACK_USER     = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]])
BACK_SETTINGS = InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Ayarlara Dön", callback_data="settings")]])

# ── VERİTABANI ────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect("kriptodrop.db") as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS airdrops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, project TEXT, description TEXT,
                reward TEXT, link TEXT, deadline TEXT, category TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                pinned INTEGER DEFAULT 0, broadcast INTEGER DEFAULT 0,
                deadline_warned INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS news_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT, content TEXT,
                sent_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT, sent_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT, first_name TEXT,
                joined_at TEXT DEFAULT (datetime('now','localtime')),
                airdrop_saves INTEGER DEFAULT 0,
                last_seen TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS user_saves (
                user_id INTEGER,
                airdrop_id INTEGER,
                saved_at TEXT DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (user_id, airdrop_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        defaults = {
            "auto_news_enabled":      "0",
            "auto_news_hour":         "10",
            "auto_news_minute":       "00",
            "auto_news_topic":        "Bitcoin,Ethereum,DeFi piyasası",
            "deadline_warn_days":     "3",
            "deadline_warn_enabled":  "1",
            "weekly_summary_enabled": "0",
            "weekly_summary_day":     "1",
            "weekly_summary_hour":    "09",
            "grok_model":             "grok-2-latest",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))

def db():
    conn = sqlite3.connect("kriptodrop.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES(?,?)", (key, value))

def is_admin(uid): return uid == ADMIN_ID

def fmt(row, idx=None, admin=False):
    pin = "📌 " if row["pinned"] else ""
    i   = f"#{idx} " if idx else f"[ID:{row['id']}] "
    lines = [
        f"{pin}{i}*{row['name']}*",
        f"🏢 Proje: {row['project'] or 'Belirtilmedi'}",
        f"🏷 Kategori: {row['category'] or 'Diğer'}",
        f"💰 Ödül: {row['reward'] or 'Belirtilmedi'}",
        f"📝 Açıklama: {row['description'] or 'Yok'}",
        f"🔗 Link: {row['link'] or 'Yok'}",
        f"⏰ Son Tarih: {row['deadline'] or 'Belirtilmedi'}",
        f"📅 Eklenme: {str(row['created_at'])[:10]}",
    ]
    if admin:
        lines.append("📊 " + ("🟢 Aktif" if row["active"] else "🔴 Pasif"))
    return "\n".join(lines)

# ── GROK AI ───────────────────────────────────────────────────────────────────
GROK_URL = "https://api.x.ai/v1/chat/completions"

NEWS_SYS = """Sen KriptoDropTR adlı Türkçe kripto Telegram grubu için haber asistanısın.
Verilen konuda güncel, bilgilendirici, akıcı Türkçe kripto haberi/analizi yaz.
Emoji kullan, paragraf formatı kullan, 250-400 kelime olsun.
Sonuna '💡 Önemli Not:' ile kısa bir yorum ekle.
Format: 📰 [BAŞLIK]\n\n... içerik ...\n\n💡 Önemli Not: ..."""

ANALYZE_SYS = """Sen KriptoDropTR için kripto analiz uzmanısın.
Türkçe, kısa ve öz analiz yap.
Format:
🔍 [COİN] ANALİZİ
📈 Teknik: ...
🏗 Temel: ...
⚠️ Riskler: ...
✅ Fırsatlar: ...
🎯 Özet: ...
⚠️ Yatırım tavsiyesi değildir."""

SUMMARY_SYS = """Sen KriptoDropTR için haftalık kripto özet yazarısın.
Bu haftanın en önemli kripto gelişmelerini Türkçe özetle.
Başlık, 4-5 madde ve kapanış yorumuyla yaz. Emoji kullan."""

async def call_grok(system: str, prompt: str, tokens: int = 800) -> str:
    model = get_setting("grok_model", "grok-2-latest")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                GROK_URL,
                headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
                      "max_tokens": tokens, "temperature": 0.7}
            )
        if r.status_code == 401: return "❌ API Anahtarı hatalı! GROQ_API_KEY değerini kontrol et."
        if r.status_code == 429: return "❌ API limit aşıldı. Birkaç dakika sonra tekrar dene."
        if r.status_code == 404: return f"❌ Model bulunamadı ({model}). Ayarlar > Grok Model'den değiştir."
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        return "⏱ Grok API zaman aşımı (60s). Tekrar dene."
    except httpx.HTTPStatusError as e:
        return f"❌ API Hatası {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        logger.error(f"Grok hata: {e}", exc_info=True)
        return f"❌ Beklenmeyen hata: {type(e).__name__}: {e}"

# ── COİNGECKO FİYAT ───────────────────────────────────────────────────────────
COIN_IDS = {
    "btc":"bitcoin","eth":"ethereum","sol":"solana","bnb":"binancecoin",
    "avax":"avalanche-2","matic":"matic-network","arb":"arbitrum","op":"optimism",
    "dot":"polkadot","ada":"cardano","xrp":"ripple","link":"chainlink",
    "uni":"uniswap","atom":"cosmos","near":"near","ftm":"fantom","apt":"aptos",
    "sui":"sui","trx":"tron","ton":"the-open-network","not":"notcoin",
    "pepe":"pepe","shib":"shiba-inu","doge":"dogecoin",
}

async def get_price(coin: str) -> str:
    cid = COIN_IDS.get(coin.lower(), coin.lower())
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{cid}",
                params={"localization":"false","tickers":"false","community_data":"false","developer_data":"false"})
        if r.status_code == 404: return f"❓ '{coin}' bulunamadı. BTC, ETH, SOL gibi sembol dene."
        r.raise_for_status()
        d = r.json(); md = d["market_data"]
        p    = md["current_price"].get("usd", 0)
        h1   = md.get("price_change_percentage_1h_in_currency", {}).get("usd", 0) or 0
        h24  = md.get("price_change_percentage_24h") or 0
        d7   = md.get("price_change_percentage_7d") or 0
        cap  = md["market_cap"].get("usd", 0)
        vol  = md["total_volume"].get("usd", 0)
        ath  = md["ath"].get("usd", 0)
        rank = d.get("market_cap_rank", "?")
        def arrow(v): return "🟢 +" if v >= 0 else "🔴 "
        def fn(n):
            if n >= 1e9: return f"${n/1e9:.2f}B"
            if n >= 1e6: return f"${n/1e6:.2f}M"
            return f"${n:,.0f}"
        return (f"💰 *{d['name']} ({d['symbol'].upper()})*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 Fiyat: *${p:,.6g}*\n"
                f"1s:  {arrow(h1)}{h1:.2f}%\n"
                f"24s: {arrow(h24)}{h24:.2f}%\n"
                f"7g:  {arrow(d7)}{d7:.2f}%\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📊 Piyasa Değeri: {fn(cap)}\n"
                f"📦 Hacim (24s): {fn(vol)}\n"
                f"🏆 Sıralama: #{rank}\n"
                f"🔝 ATH: ${ath:,.6g}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    except Exception as e:
        return f"❌ Fiyat alınamadı: {e}"

# ── KULLANICI KAYIT ───────────────────────────────────────────────────────────
def register_user(user):
    with db() as conn:
        conn.execute("""
            INSERT INTO users (id, username, first_name, joined_at, last_seen)
            VALUES (?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username, first_name=excluded.first_name,
                last_seen=datetime('now','localtime')
        """, (user.id, user.username or "", user.first_name or ""))

async def save_airdrop_for_user(user_id: int, airdrop_id: int) -> bool:
    try:
        with db() as conn:
            conn.execute("INSERT INTO user_saves (user_id,airdrop_id) VALUES (?,?)", (user_id, airdrop_id))
            conn.execute("UPDATE users SET airdrop_saves=airdrop_saves+1 WHERE id=?", (user_id,))
        return True
    except sqlite3.IntegrityError:
        return False

# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "👋 Airdrop ve haberler için bana özel mesaj yaz!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📩 Bota Mesaj At", url=f"https://t.me/{context.bot.username}")
            ]]))
        return
    register_user(update.effective_user)
    context.user_data.clear()
    if is_admin(update.effective_user.id): await show_admin(update, context)
    else: await show_user(update, context)

async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        news_n = conn.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
        users  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    an = "🟢" if get_setting("auto_news_enabled") == "1" else "🔴"
    dl = "🟢" if get_setting("deadline_warn_enabled") == "1" else "🔴"
    wk = "🟢" if get_setting("weekly_summary_enabled") == "1" else "🔴"
    text = (f"🛠 *KriptoDropTR — Admin Paneli*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪂 Toplam: *{total}* (Aktif: *{active}*)\n"
            f"📰 Haber: *{news_n}* | 👤 Kullanıcı: *{users}*\n"
            f"⚡ Oto-Haber:{an}  Deadline:{dl}  Haftalık:{wk}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n👇 İşlem seç:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Airdrop Ekle",       callback_data="add_airdrop"),
         InlineKeyboardButton("🗂 Airdrop Yönet",      callback_data="manage_airdrops")],
        [InlineKeyboardButton("📰 Haber Oluştur (AI)", callback_data="send_news"),
         InlineKeyboardButton("🔍 Coin Analizi (AI)",  callback_data="coin_analysis")],
        [InlineKeyboardButton("📢 Duyuru Yap",         callback_data="announce"),
         InlineKeyboardButton("💰 Fiyat Sorgula",      callback_data="price_menu")],
        [InlineKeyboardButton("📊 İstatistikler",      callback_data="stats"),
         InlineKeyboardButton("👤 Kullanıcılar",       callback_data="users_panel")],
        [InlineKeyboardButton("⚙️ Ayarlar",            callback_data="settings"),
         InlineKeyboardButton("👥 Grup Bilgisi",       callback_data="group_info")],
        [InlineKeyboardButton("📜 Haber Geçmişi",      callback_data="news_history"),
         InlineKeyboardButton("📣 Duyuru Geçmişi",     callback_data="ann_history")],
    ])
    try: await update.effective_message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except: await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def show_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        active = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        uid    = update.effective_user.id
        saves  = conn.execute("SELECT airdrop_saves FROM users WHERE id=?", (uid,)).fetchone()
    save_count = saves["airdrop_saves"] if saves else 0
    text = (f"👋 *KriptoDropTR'ye Hoş Geldin!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪂 Şu an *{active}* aktif airdrop var!\n"
            f"💾 Kaydettiğin: *{save_count}* airdrop\n"
            f"━━━━━━━━━━━━━━━━━━━━\n👇 Ne yapmak istiyorsun?")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪂 Aktif Airdroplar", callback_data="u_list"),
         InlineKeyboardButton("📌 Öne Çıkanlar",     callback_data="u_pinned")],
        [InlineKeyboardButton("🔍 Kategoriye Göre",  callback_data="u_category"),
         InlineKeyboardButton("🆕 Son Eklenenler",   callback_data="u_recent")],
        [InlineKeyboardButton("💾 Kaydettiklerim",   callback_data="u_saved"),
         InlineKeyboardButton("💰 Coin Fiyatı",      callback_data="price_menu")],
        [InlineKeyboardButton("❓ Yardım",            callback_data="u_help")],
    ])
    try: await update.effective_message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except: await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ── AIRDROP EKLEME CONV ───────────────────────────────────────────────────────
async def add_airdrop_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    context.user_data.clear()
    await q.message.reply_text(
        "➕ *Yeni Airdrop Ekle*\n━━━━━━━━━━━━━\n📛 *Airdrop adını* girin:\n_(Örn: Arbitrum Season 2)_\n\n❌ /iptal",
        parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_NAME

async def s_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(f"✅ Ad: *{context.user_data['name']}*\n\n🏢 *Proje/Token adı:*\n_(Örn: ARB, Arbitrum)_", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_PROJECT

async def s_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["project"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🤖 AI ile Oluştur", callback_data="ai_desc"),
                                InlineKeyboardButton("✏️ Manuel Gir", callback_data="manual_desc")]])
    await update.message.reply_text(f"✅ Proje: *{context.user_data['project']}*\n\n📝 *Açıklama* için yöntem:", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_DESC

async def cb_ai_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    wait = await q.message.reply_text("🤖 AI açıklama oluşturuyor...")
    desc = await call_grok("Kripto airdrop için 2-3 cümle Türkçe açıklama yaz. Emoji kullan, kullanıcıyı katılmaya teşvik et.",
                           f"Proje: {context.user_data.get('project','?')}, Airdrop: {context.user_data.get('name','?')}")
    await wait.delete()
    if desc.startswith("❌") or desc.startswith("⏱"):
        await q.message.reply_text(f"{desc}\n\nManuel açıklama girin:"); return AIRDROP_DESC
    context.user_data["desc"] = desc
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Kullan", callback_data="use_ai_desc"),
                                InlineKeyboardButton("✏️ Değiştir", callback_data="manual_desc")]])
    await q.message.reply_text(f"🤖 *AI Önerisi:*\n\n{desc}", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_DESC

async def cb_use_ai_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("💰 *Ödül:*\n_(Örn: 1000 ARB token)_", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_REWARD

async def cb_manual_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("📝 *Açıklamayı* girin:", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_DESC

async def s_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["desc"] = update.message.text.strip()
    await update.message.reply_text("💰 *Ödül:*\n_(Örn: 1000 ARB token, Belirsiz)_", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_REWARD

async def s_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reward"] = update.message.text.strip()
    await update.message.reply_text("🔗 *Katılım linki:*\n_(URL veya 'yok')_", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_LINK

async def s_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    context.user_data["link"] = "" if t.lower() in ("yok","-","none") else t
    await update.message.reply_text(
        "⏰ *Son katılım tarihi:*\n_(Örn: 31.12.2025 veya 'belirsiz')_\n\n💡 GG.AA.YYYY formatında girersen deadline uyarısı aktif olur.",
        parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_DEADLINE

async def s_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    context.user_data["deadline"] = "" if t.lower() in ("belirsiz","-","yok") else t
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"nc_{i}")] for i,c in enumerate(CATEGORIES)])
    await update.message.reply_text("🏷 *Kategori seç:*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_CATEGORY

async def s_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    context.user_data["category"] = CATEGORIES[int(q.data.split("_")[1])]
    with db() as conn:
        conn.execute("INSERT INTO airdrops (name,project,description,reward,link,deadline,category) VALUES(?,?,?,?,?,?,?)",
                     (context.user_data["name"], context.user_data["project"], context.user_data.get("desc",""),
                      context.user_data["reward"], context.user_data["link"], context.user_data["deadline"], context.user_data["category"]))
        nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    txt = (f"✅ *Airdrop Eklendi!* [ID: {nid}]\n━━━━━━━━━━━━━━━━━━━━\n"
           f"📛 *{context.user_data['name']}*\n🏢 {context.user_data['project']} | {context.user_data['category']}\n"
           f"💰 {context.user_data['reward']}\n⏰ {context.user_data.get('deadline') or 'Belirsiz'}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Gruba Duyur", callback_data=f"do_broadcast_{nid}"),
         InlineKeyboardButton("📌 Sabitle", callback_data=f"do_pin_{nid}")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]
    ])
    await q.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END

# ── HABER CONV ────────────────────────────────────────────────────────────────
QUICK_TOPICS = [
    ("₿ Bitcoin","Bitcoin"), ("Ξ Ethereum","Ethereum"),
    ("◎ Solana","Solana"),   ("🔵 BNB Chain","BNB Chain"),
    ("🌐 DeFi","DeFi piyasası"), ("🎮 GameFi","GameFi"),
    ("🖼 NFT","NFT piyasası"),   ("⚖️ Regülasyon","Kripto regülasyon"),
]

async def send_news_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    context.user_data.pop("news_content", None); context.user_data.pop("news_topic", None)
    rows = [QUICK_TOPICS[i:i+2] for i in range(0, len(QUICK_TOPICS), 2)]
    kb_rows = [[InlineKeyboardButton(n, callback_data=f"qnews_{t}") for n,t in row] for row in rows]
    kb_rows.append([InlineKeyboardButton("✏️ Kendi Konumu Yaz", callback_data="news_manual")])
    kb_rows.append([InlineKeyboardButton("❌ İptal", callback_data="back_admin")])
    msg_text = "📰 *Haber Oluştur (Grok AI)*\n━━━━━━━━━━━━━━━━━━━━\nKonu seç veya kendin yaz:"
    if q:
        try: await q.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
        except: await q.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    return NEWS_TOPIC

async def cb_quick_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    return await _gen_news(update, context, q.data[6:])

async def cb_news_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("✏️ *Haber konusunu yaz:*\n_(Örn: Ethereum ETF onayı)_\n\n❌ /iptal", parse_mode=ParseMode.MARKDOWN)
    return NEWS_TOPIC

async def news_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _gen_news(update, context, update.message.text.strip())

async def _gen_news(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    q = update.callback_query
    if q: wait = await q.message.reply_text(f"⏳ *{topic}* hakkında haber yazılıyor...", parse_mode=ParseMode.MARKDOWN)
    else: wait = await update.message.reply_text(f"⏳ *{topic}* hakkında haber yazılıyor...", parse_mode=ParseMode.MARKDOWN)
    content = await call_grok(NEWS_SYS, f"'{topic}' hakkında KriptoDropTR grubu için güncel haber/analiz yaz.", 900)
    await wait.delete()
    if content.startswith("❌") or content.startswith("⏱"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Tekrar Dene", callback_data=f"qnews_{topic}")],
            [InlineKeyboardButton("✏️ Başka Konu", callback_data="news_manual")],
            [InlineKeyboardButton("❌ İptal", callback_data="back_admin")],
        ])
        err = f"❌ *Haber oluşturulamadı*\n━━━━━━━━━━\n{content}"
        if q: await q.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return NEWS_TOPIC
    context.user_data["news_content"] = content
    context.user_data["news_topic"]   = topic
    preview = f"📰 *Önizleme — {topic}*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}"
    if len(preview) > 4000: preview = preview[:3990] + "..."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gruba Gönder", callback_data="news_do_send"),
         InlineKeyboardButton("🔄 Yeniden Oluştur", callback_data=f"qnews_{topic}")],
        [InlineKeyboardButton("✏️ Farklı Konu", callback_data="news_manual"),
         InlineKeyboardButton("❌ İptal", callback_data="back_admin")],
    ])
    if q: await q.message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return NEWS_PREVIEW

async def news_do_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Gönderiliyor...")
    content = context.user_data.get("news_content","")
    topic   = context.user_data.get("news_topic","Genel")
    if not content:
        await q.edit_message_text("❌ İçerik bulunamadı. Yeni haber oluştur.", reply_markup=BACK_ADMIN)
        return ConversationHandler.END
    msg = f"📰 *KriptoDropTR — Kripto Haber*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}\n\n🔔 @KriptoDropTR"
    if len(msg) > 4096: msg = msg[:4090] + "..."
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn:
            conn.execute("INSERT INTO news_log (topic,content) VALUES(?,?)", (topic, content))
        await q.edit_message_text(f"✅ *Haber gruba gönderildi!*\n📌 Konu: {topic}", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await q.edit_message_text(f"❌ *Gönderme hatası:*\n`{e}`", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END

# ── COİN ANALİZİ ─────────────────────────────────────────────────────────────
async def coin_analysis_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ BTC", callback_data="qa_Bitcoin"),
         InlineKeyboardButton("Ξ ETH", callback_data="qa_Ethereum"),
         InlineKeyboardButton("◎ SOL", callback_data="qa_Solana")],
        [InlineKeyboardButton("🔵 BNB", callback_data="qa_BNB"),
         InlineKeyboardButton("🔶 AVAX", callback_data="qa_Avalanche"),
         InlineKeyboardButton("🟣 ARB", callback_data="qa_Arbitrum")],
        [InlineKeyboardButton("✏️ Başka Coin", callback_data="qa_custom")],
        [InlineKeyboardButton("❌ İptal", callback_data="back_admin")],
    ])
    await q.edit_message_text("🔍 *Coin Analizi*\nHangi coin?", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_quick_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    coin = q.data[3:]
    if coin == "custom":
        await q.message.reply_text("✏️ Analiz etmek istediğin coini yaz:\n_(Örn: Chainlink)_"); return
    wait = await q.message.reply_text(f"🔍 {coin} analizi yapılıyor...")
    result = await call_grok(ANALYZE_SYS, f"'{coin}' için güncel kısa analiz yap. Türkçe.", 600)
    await wait.delete()
    context.user_data["analysis"] = result
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Gruba Gönder", callback_data=f"send_analysis_{coin}")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")],
    ])
    await q.message.reply_text(result, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def send_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    coin    = q.data.replace("send_analysis_","")
    content = context.user_data.get("analysis","")
    msg = f"🔍 *KriptoDropTR — Coin Analizi*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}\n\n🔔 @KriptoDropTR"
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        await q.edit_message_text(f"✅ {coin} analizi gruba gönderildi!", reply_markup=BACK_ADMIN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)

# ── DUYURU CONV ───────────────────────────────────────────────────────────────
async def announce_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text(
        "📢 *Duyuru Metni*\n━━━━━━━━━━━━━━\nGruba göndereceğin duyuruyu yaz:\n_(Markdown: *kalın*, _italik_)_\n\n❌ /iptal",
        parse_mode=ParseMode.MARKDOWN)
    return ANNOUNCE_TEXT

async def announce_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["announce"] = update.message.text
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gönder", callback_data="ann_send"),
         InlineKeyboardButton("✏️ Düzenle", callback_data="ann_redo")],
        [InlineKeyboardButton("❌ İptal", callback_data="back_admin")],
    ])
    await update.message.reply_text(f"👁 *Önizleme:*\n━━━━━━━━━━━━━━\n\n{update.message.text}\n\nGönderilsin mi?", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return ANNOUNCE_CONFIRM

async def ann_redo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("✏️ Yeni duyuru metnini gir:")
    return ANNOUNCE_TEXT

async def ann_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    text = context.user_data.get("announce","")
    msg  = f"📢 *DUYURU*\n━━━━━━━━━━━━━━━━━━━━\n\n{text}\n\n🔔 @KriptoDropTR"
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn: conn.execute("INSERT INTO announcements (text) VALUES(?)", (text,))
        await q.edit_message_text("✅ Duyuru gönderildi!", reply_markup=BACK_ADMIN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)
    context.user_data.clear()
    return ConversationHandler.END

# ── FİYAT CONV ────────────────────────────────────────────────────────────────
async def price_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    pop = [("₿ BTC","btc"),("Ξ ETH","eth"),("◎ SOL","sol"),("⚡ XRP","xrp"),("🔵 BNB","bnb"),("🔴 TON","ton")]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(n, callback_data=f"qp_{s}") for n,s in pop[:3]],
        [InlineKeyboardButton(n, callback_data=f"qp_{s}") for n,s in pop[3:]],
        [InlineKeyboardButton("✏️ Başka Coin", callback_data="price_custom")],
        [InlineKeyboardButton("🔙 Geri", callback_data="back_admin" if is_admin(uid) else "back_user")],
    ])
    await q.edit_message_text("💰 *Coin Fiyatı*\nHangi coini sorguluyorsun?", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_quick_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    coin = q.data[3:]
    wait = await q.message.reply_text(f"⏳ {coin.upper()} fiyatı alınıyor...")
    result = await get_price(coin)
    await wait.delete()
    uid  = q.from_user.id
    back = "back_admin" if is_admin(uid) else "back_user"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yenile", callback_data=f"qp_{coin}"),
                                InlineKeyboardButton("💰 Başka Coin", callback_data="price_menu")],
                               [InlineKeyboardButton("🏠 Ana Menü", callback_data=back)]])
    await q.message.reply_text(result, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def price_custom_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    context.user_data["price_back"] = "back_admin" if is_admin(q.from_user.id) else "back_user"
    await q.message.reply_text("✏️ *Coin sembolünü* gir:\n_(Örn: BTC, ETH, AVAX, NEAR...)_\n\n❌ /iptal", parse_mode=ParseMode.MARKDOWN)
    return PRICE_COIN

async def price_coin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.strip()
    wait = await update.message.reply_text(f"⏳ {coin.upper()} alınıyor...")
    result = await get_price(coin)
    await wait.delete()
    back = context.user_data.get("price_back","back_admin")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yenile", callback_data=f"qp_{coin.lower()}"),
                                InlineKeyboardButton("💰 Başka Coin", callback_data="price_menu")],
                               [InlineKeyboardButton("🏠 Ana Menü", callback_data=back)]])
    await update.message.reply_text(result, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ── ⚙️ AYARLAR PANELİ ─────────────────────────────────────────────────────────
async def settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    an_en  = get_setting("auto_news_enabled") == "1"
    an_h   = get_setting("auto_news_hour","10")
    an_m   = get_setting("auto_news_minute","00")
    an_t   = get_setting("auto_news_topic","Bitcoin")
    dl_en  = get_setting("deadline_warn_enabled") == "1"
    dl_d   = get_setting("deadline_warn_days","3")
    wk_en  = get_setting("weekly_summary_enabled") == "1"
    wk_day = get_setting("weekly_summary_day","1")
    wk_h   = get_setting("weekly_summary_hour","09")
    model  = get_setting("grok_model","grok-2-latest")
    days_map = {"0":"Pzt","1":"Sal","2":"Çar","3":"Per","4":"Cum","5":"Cmt","6":"Paz"}
    text = (
        f"⚙️ *Bot Ayarları*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📰 *Otomatik Haber*\n"
        f"  Durum: {'🟢 Açık' if an_en else '🔴 Kapalı'}\n"
        f"  Her gün saat *{an_h}:{an_m}*\n"
        f"  Konular: _{an_t}_\n\n"
        f"⏰ *Deadline Uyarısı*\n"
        f"  Durum: {'🟢 Açık' if dl_en else '🔴 Kapalı'}\n"
        f"  Bitiş tarihinden *{dl_d} gün* önce uyar\n\n"
        f"📅 *Haftalık Özet*\n"
        f"  Durum: {'🟢 Açık' if wk_en else '🔴 Kapalı'}\n"
        f"  Her *{days_map.get(wk_day,'?')}* saat *{wk_h}:00*\n\n"
        f"🤖 *Grok Modeli:* `{model}`\n\n"
        f"👇 Değiştirmek istediğin ayarı seç:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📰 Oto-Haber: {'🟢 Kapat' if an_en else '🔴 Aç'}", callback_data="stg_toggle_auto_news")],
        [InlineKeyboardButton("🕐 Haber Saati", callback_data="stg_set_news_hour"),
         InlineKeyboardButton("📝 Haber Konuları", callback_data="stg_set_news_topic")],
        [InlineKeyboardButton(f"⏰ Deadline: {'🟢 Kapat' if dl_en else '🔴 Aç'}", callback_data="stg_toggle_deadline")],
        [InlineKeyboardButton("📆 Uyarı Kaç Gün Önce", callback_data="stg_set_deadline_days")],
        [InlineKeyboardButton(f"📅 Haftalık: {'🟢 Kapat' if wk_en else '🔴 Aç'}", callback_data="stg_toggle_weekly")],
        [InlineKeyboardButton("📅 Özet Günü", callback_data="stg_set_weekly_day"),
         InlineKeyboardButton("🕐 Özet Saati", callback_data="stg_set_weekly_hour")],
        [InlineKeyboardButton("🤖 Grok Modelini Değiştir", callback_data="stg_set_grok_model")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")],
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def settings_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    map_ = {
        "stg_toggle_auto_news": ("auto_news_enabled","Otomatik Haber"),
        "stg_toggle_deadline":  ("deadline_warn_enabled","Deadline Uyarısı"),
        "stg_toggle_weekly":    ("weekly_summary_enabled","Haftalık Özet"),
    }
    key, label = map_[d]
    current = get_setting(key) == "1"
    set_setting(key, "0" if current else "1")
    await q.answer(f"{label} {'🔴 Kapatıldı' if current else '🟢 Açıldı'}", show_alert=True)
    await settings_panel(update, context)

async def settings_input_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    prompts = {
        "stg_set_news_hour":     "🕐 *Otomatik haber saatini gir* (0-23):\n_Örn: 10_",
        "stg_set_news_topic":    "📝 *Haber konularını gir* (virgülle ayır):\n_Örn: Bitcoin,Ethereum,DeFi_",
        "stg_set_deadline_days": "📆 *Kaç gün önce uyarı gelsin?* (1-30):\n_Örn: 3_",
        "stg_set_weekly_hour":   "🕐 *Haftalık özet saatini gir* (0-23):\n_Örn: 9_",
        "stg_set_grok_model":    "🤖 *Grok modelini gir:*\n_Mevcut: grok-2-latest, grok-beta_",
    }
    context.user_data["settings_key"] = q.data
    await q.message.reply_text(f"{prompts[q.data]}\n\n❌ /iptal", parse_mode=ParseMode.MARKDOWN)
    return SETTINGS_INPUT

async def settings_day_picker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    days = [("Pazartesi","0"),("Salı","1"),("Çarşamba","2"),("Perşembe","3"),("Cuma","4"),("Cumartesi","5"),("Pazar","6")]
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(n, callback_data=f"stg_day_{v}")] for n,v in days] +
        [[InlineKeyboardButton("⚙️ Ayarlara Dön", callback_data="settings")]]
    )
    await q.edit_message_text("📅 *Haftalık özet günü:*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def settings_day_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    day = q.data.replace("stg_day_","")
    set_setting("weekly_summary_day", day)
    days_map = {"0":"Pazartesi","1":"Salı","2":"Çarşamba","3":"Perşembe","4":"Cuma","5":"Cumartesi","6":"Pazar"}
    await q.answer(f"✅ {days_map.get(day,'?')} olarak ayarlandı!", show_alert=True)
    await settings_panel(update, context)

async def settings_save_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    key_map = {
        "stg_set_news_hour":     ("auto_news_hour",         lambda v: str(max(0,min(23,int(v))))),
        "stg_set_news_topic":    ("auto_news_topic",         lambda v: v),
        "stg_set_deadline_days": ("deadline_warn_days",      lambda v: str(max(1,min(30,int(v))))),
        "stg_set_weekly_hour":   ("weekly_summary_hour",     lambda v: str(max(0,min(23,int(v))))),
        "stg_set_grok_model":    ("grok_model",              lambda v: v),
    }
    sk = context.user_data.get("settings_key","")
    if sk not in key_map:
        await update.message.reply_text("❌ Bilinmeyen ayar.", reply_markup=BACK_ADMIN); return ConversationHandler.END
    db_key, transform = key_map[sk]
    try:
        final = transform(value)
        set_setting(db_key, final)
        await update.message.reply_text(f"✅ *Kaydedildi!*\n`{db_key}` = `{final}`", parse_mode=ParseMode.MARKDOWN, reply_markup=BACK_SETTINGS)
    except Exception as e:
        await update.message.reply_text(f"❌ Geçersiz değer: {e}", reply_markup=BACK_SETTINGS)
    context.user_data.clear()
    return ConversationHandler.END

# ── AİRDROP YÖNETİM ──────────────────────────────────────────────────────────
async def manage_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Listele", callback_data="mng_list"),
         InlineKeyboardButton("🗑 Sil", callback_data="mng_delete")],
        [InlineKeyboardButton("✅ Aktif/Pasif", callback_data="mng_toggle"),
         InlineKeyboardButton("📌 Sabitle", callback_data="mng_pin")],
        [InlineKeyboardButton("📢 Gruba Duyur", callback_data="mng_broadcast")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")],
    ])
    await q.edit_message_text("🗂 *Airdrop Yönetimi*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def mng_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops ORDER BY pinned DESC, id DESC LIMIT 20").fetchall()
    if not rows: await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_ADMIN); return
    await q.message.reply_text(f"📋 *{len(rows)} Airdrop:*", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1): await q.message.reply_text(fmt(row,i,admin=True), parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_ADMIN)

async def mng_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    action = q.data
    with db() as conn:
        rows = conn.execute("SELECT id,name,active,pinned FROM airdrops ORDER BY id DESC LIMIT 20").fetchall()
    if not rows: await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_ADMIN); return
    icons = {"mng_delete":"🗑","mng_toggle":"✅","mng_pin":"📌","mng_broadcast":"📢"}
    icon  = icons.get(action,"•")
    kb    = []
    for r in rows:
        state = (" 🟢" if r["active"] else " 🔴") if action=="mng_toggle" else (" 📌" if r["pinned"] else "") if action=="mng_pin" else ""
        kb.append([InlineKeyboardButton(f"{icon} [{r['id']}] {r['name']}{state}", callback_data=f"do_{action.replace('mng_','')}_{r['id']}")])
    kb.append([InlineKeyboardButton("🔙 Geri", callback_data="manage_airdrops")])
    await q.edit_message_text(f"{icon} *Airdrop seç:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def do_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row: conn.execute("DELETE FROM airdrops WHERE id=?", (aid,))
    await q.edit_message_text(f"🗑 *{row['name'] if row else aid}* silindi.", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name,active FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row:
            new = 0 if row["active"] else 1
            conn.execute("UPDATE airdrops SET active=? WHERE id=?", (new,aid))
    await q.edit_message_text(f"✅ *{row['name']}* → {'🟢 Aktif' if new else '🔴 Pasif'}", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name,pinned FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row:
            new = 0 if row["pinned"] else 1
            conn.execute("UPDATE airdrops SET pinned=? WHERE id=?", (new,aid))
    await q.edit_message_text(f"{'📌 Sabitlendi' if new else '📋 Sabit Kaldırıldı'}: *{row['name']}*", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Gönderiliyor...")
    aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT * FROM airdrops WHERE id=?", (aid,)).fetchone()
    if not row: await q.edit_message_text("❌ Airdrop bulunamadı.", reply_markup=BACK_ADMIN); return
    msg = "🚨 *YENİ AİRDROP!* 🚨\n━━━━━━━━━━━━━━━━━━━━\n\n" + fmt(row) + "\n\n🔔 @KriptoDropTR"
    kb  = [[InlineKeyboardButton("🚀 Hemen Katıl!", url=row["link"])]] if row["link"] else []
    try:
        await context.bot.send_message(GROUP_ID, msg, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
        with db() as conn: conn.execute("UPDATE airdrops SET broadcast=broadcast+1 WHERE id=?", (aid,))
        await q.edit_message_text(f"✅ *{row['name']}* gruba duyuruldu!", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)

# ── 📊 İSTATİSTİK ─────────────────────────────────────────────────────────────
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
        active   = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        passive  = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=0").fetchone()[0]
        pinned   = conn.execute("SELECT COUNT(*) FROM airdrops WHERE pinned=1").fetchone()[0]
        bcast    = conn.execute("SELECT SUM(broadcast) FROM airdrops").fetchone()[0] or 0
        news_n   = conn.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
        ann_n    = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        user_n   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        save_n   = conn.execute("SELECT COUNT(*) FROM user_saves").fetchone()[0]
        cats     = conn.execute("SELECT category,COUNT(*) n FROM airdrops GROUP BY category ORDER BY n DESC").fetchall()
        month_a  = conn.execute("SELECT COUNT(*) FROM airdrops WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now')").fetchone()[0]
        month_n  = conn.execute("SELECT COUNT(*) FROM news_log WHERE strftime('%Y-%m',sent_at)=strftime('%Y-%m','now')").fetchone()[0]
        last_d   = conn.execute("SELECT name,created_at FROM airdrops ORDER BY id DESC LIMIT 1").fetchone()
        top_b    = conn.execute("SELECT name,broadcast FROM airdrops WHERE broadcast>0 ORDER BY broadcast DESC LIMIT 1").fetchone()
        last_nws = conn.execute("SELECT topic,sent_at FROM news_log ORDER BY id DESC LIMIT 3").fetchall()
        top_s    = conn.execute("SELECT first_name,airdrop_saves FROM users ORDER BY airdrop_saves DESC LIMIT 1").fetchone()
    cat_lines  = "\n".join([f"  {r['category'] or 'Diğer'}: *{r['n']}*" for r in cats]) or "  Henüz yok"
    news_lines = "\n".join([f"  • {r['topic']} _({r['sent_at'][:10]})_" for r in last_nws]) or "  Henüz gönderilmedi"
    last_a_txt = f"{last_d['name']} _({last_d['created_at'][:10]})_" if last_d else "Yok"
    top_b_txt  = f"{top_b['name']} ({top_b['broadcast']}x)" if top_b else "Yok"
    top_s_txt  = f"{top_s['first_name']} ({top_s['airdrop_saves']} kayıt)" if top_s and top_s['airdrop_saves'] > 0 else "Henüz yok"
    text = (
        f"📊 *KriptoDropTR İstatistikleri*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪂 *Airdroplar*\n"
        f"  Toplam: *{total}* | Aktif: *{active}* | Pasif: *{passive}*\n"
        f"  📌 Sabitlenmiş: *{pinned}* | 📢 Duyuru: *{bcast}* kez\n"
        f"  🏆 En Çok Duyurulan: {top_b_txt}\n"
        f"  🆕 Son Eklenen: {last_a_txt}\n\n"
        f"👤 *Kullanıcılar*\n"
        f"  Toplam: *{user_n}* | Toplam Kayıt: *{save_n}*\n"
        f"  🏅 En Aktif: {top_s_txt}\n\n"
        f"📅 *Bu Ay*\n"
        f"  Yeni Airdrop: *{month_a}* | Yeni Haber: *{month_n}*\n\n"
        f"📰 *İçerik*: Haber: *{news_n}* | Duyuru: *{ann_n}*\n\n"
        f"🏷 *Kategori Dağılımı*\n{cat_lines}\n\n"
        f"📰 *Son Haberler*\n{news_lines}\n\n"
        f"🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_"
    )
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def users_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new_wk  = conn.execute("SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now','-7 days')").fetchone()[0]
        top_lst = conn.execute("SELECT first_name,username,airdrop_saves,last_seen FROM users ORDER BY airdrop_saves DESC LIMIT 10").fetchall()
    lines = []
    for i,u in enumerate(top_lst,1):
        uname = f"@{u['username']}" if u['username'] else u['first_name'] or "Anonim"
        lines.append(f"{i}. {uname} — 💾 {u['airdrop_saves']} kayıt")
    text = (f"👤 *Kullanıcı Paneli*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Toplam: *{total}* | 🆕 Son 7 gün: *{new_wk}*\n\n"
            f"🏅 *En Aktif 10 Kullanıcı:*\n" + ("\n".join(lines) or "Henüz yok.") + f"\n\n🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def group_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    try:
        chat  = await context.bot.get_chat(GROUP_ID)
        count = await context.bot.get_chat_member_count(GROUP_ID)
        text  = (f"👥 *Grup Bilgisi*\n━━━━━━━━━━━━━━━━━━━━\n"
                 f"📛 *{chat.title}*\n👥 Üye: *{count}*\n🆔 `{GROUP_ID}`\n"
                 f"📝 {chat.description or 'Açıklama yok'}")
    except Exception as e:
        text = f"⚠️ Hata:\n`{e}`"
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def news_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT topic,sent_at FROM news_log ORDER BY id DESC LIMIT 10").fetchall()
    if not rows: await q.edit_message_text("📭 Haber yok.", reply_markup=BACK_ADMIN); return
    text = "📰 *Son 10 Haber:*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1): text += f"{i}. {r['topic']}\n   _{r['sent_at'][:16]}_\n\n"
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def ann_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT text,sent_at FROM announcements ORDER BY id DESC LIMIT 5").fetchall()
    if not rows: await q.edit_message_text("📭 Duyuru yok.", reply_markup=BACK_ADMIN); return
    text = "📢 *Son 5 Duyuru:*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1):
        prev = r["text"][:80] + "..." if len(r["text"])>80 else r["text"]
        text += f"{i}. {prev}\n   _{r['sent_at'][:16]}_\n\n"
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

# ── KULLANICI PANELİ ──────────────────────────────────────────────────────────
def _airdrop_kb(row):
    btns = []
    if row["link"]: btns.append(InlineKeyboardButton("🚀 Katıl!", url=row["link"]))
    btns.append(InlineKeyboardButton("💾 Kaydet", callback_data=f"save_airdrop_{row['id']}"))
    return InlineKeyboardMarkup([btns])

async def u_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); register_user(q.from_user)
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY pinned DESC, id DESC LIMIT 10").fetchall()
    if not rows: await q.edit_message_text("📭 Aktif airdrop yok. Yakında eklenecek! 🔔", reply_markup=BACK_USER); return
    await q.message.reply_text(f"🪂 *{len(rows)} Aktif Airdrop:*", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1): await q.message.reply_text(fmt(row,i), reply_markup=_airdrop_kb(row), parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("🔔 Yeni airdroplar için grubu takip et!", reply_markup=BACK_USER)

async def u_pinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE pinned=1 AND active=1").fetchall()
    if not rows: await q.edit_message_text("📭 Sabitlenmiş airdrop yok.", reply_markup=BACK_USER); return
    for i,row in enumerate(rows,1): await q.message.reply_text(fmt(row,i), reply_markup=_airdrop_kb(row), parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_USER)

async def u_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [[InlineKeyboardButton(c, callback_data=f"uc_{i}")] for i,c in enumerate(CATEGORIES)]
    kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")])
    await q.edit_message_text("🏷 *Kategori Seç:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def u_filter_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cat = CATEGORIES[int(q.data.split("_")[1])]
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE category=? AND active=1", (cat,)).fetchall()
    if not rows: await q.edit_message_text(f"📭 {cat} kategorisinde airdrop yok.", reply_markup=BACK_USER); return
    await q.message.reply_text(f"🏷 *{cat}* — {len(rows)} airdrop:", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1): await q.message.reply_text(fmt(row,i), reply_markup=_airdrop_kb(row), parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_USER)

async def u_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY id DESC LIMIT 5").fetchall()
    if not rows: await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_USER); return
    await q.message.reply_text("🆕 *Son 5 Eklenen:*", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1): await q.message.reply_text(fmt(row,i), reply_markup=_airdrop_kb(row), parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_USER)

async def u_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    with db() as conn:
        rows = conn.execute("""
            SELECT a.*, us.saved_at FROM airdrops a
            JOIN user_saves us ON a.id=us.airdrop_id
            WHERE us.user_id=? ORDER BY us.saved_at DESC
        """, (uid,)).fetchall()
    if not rows:
        await q.edit_message_text("💾 Henüz kaydedilmiş airdrop yok.\n\n🪂 Airdrop listesinden 'Kaydet' butonuna bas!", reply_markup=BACK_USER); return
    await q.message.reply_text(f"💾 *Kaydettiğin {len(rows)} Airdrop:*", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1):
        status = "🟢" if row["active"] else "🔴 (Sona Erdi)"
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row["link"])]] if row["link"] else []
        await q.message.reply_text(fmt(row,i) + f"\n{status}", reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_USER)

async def cb_save_airdrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; register_user(q.from_user)
    aid   = int(q.data.replace("save_airdrop_",""))
    saved = await save_airdrop_for_user(q.from_user.id, aid)
    await q.answer("✅ Kaydedildi! 'Kaydettiklerim' menüsünden görebilirsin." if saved else "ℹ️ Zaten kayıtlı.", show_alert=True)

async def u_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "❓ *KriptoDropTR Yardım*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "🪂 *Airdrop nedir?*\nKripto projelerin ücretsiz token dağıtımlarıdır.\n\n"
        "🚀 *Nasıl katılırım?*\n'Katıl' butonuna bas, formu doldur.\n\n"
        "💾 *Kaydetme:*\nAirdropları 'Kaydet' butonuyla listeye ekle.\n\n"
        "💰 *Fiyat:*\n'Coin Fiyatı' menüsünden canlı sorgula.\n\n"
        "⚠️ *GÜVENLİK:*\nHiçbir airdrop için *private key veya seed phrase* paylaşma!\n\n"
        "📢 @KriptoDropTR",
        reply_markup=BACK_USER, parse_mode=ParseMode.MARKDOWN)

# ── GRUP KOMUTLARI ────────────────────────────────────────────────────────────
async def cmd_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY pinned DESC, id DESC LIMIT 5").fetchall()
    if not rows: await update.message.reply_text("📭 Aktif airdrop yok. 🔔 Yakında eklenecek!"); return
    text = "🪂 *Aktif Airdroplar — KriptoDropTR*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,row in enumerate(rows,1):
        pin  = "📌 " if row["pinned"] else ""
        link = f" | [Katıl]({row['link']})" if row["link"] else ""
        text += f"{pin}*{i}. {row['name']}* ({row['category'] or 'Genel'})\n💰 {row['reward'] or '?'} | ⏰ {row['deadline'] or '?'}{link}\n\n"
    text += "📩 Detay için bota özel mesaj at!"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def cmd_haberler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT topic,sent_at FROM news_log ORDER BY id DESC LIMIT 5").fetchall()
    if not rows: await update.message.reply_text("📭 Henüz haber gönderilmemiş."); return
    text = "📰 *Son Haberler — KriptoDropTR*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1): text += f"{i}. {r['topic']} _{r['sent_at'][:10]}_\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_fiyat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("📌 Kullanım: `/fiyat BTC`", parse_mode=ParseMode.MARKDOWN); return
    wait = await update.message.reply_text(f"⏳ {context.args[0].upper()} fiyatı alınıyor...")
    result = await get_price(context.args[0])
    await wait.delete()
    await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)

# ── SCHEDULER ─────────────────────────────────────────────────────────────────
_last_auto_news_run = ""  # "YYYY-MM-DD HH:MM" formatında — aynı dakikada iki kez çalışmayı önle

async def auto_news_scheduler(context: ContextTypes.DEFAULT_TYPE):
    global _last_auto_news_run
    if get_setting("auto_news_enabled") != "1": return
    now = datetime.now()
    target_h = int(get_setting("auto_news_hour","10"))
    target_m = int(get_setting("auto_news_minute","0"))
    key = now.strftime(f"%Y-%m-%d {target_h:02d}:{target_m:02d}")
    if now.hour == target_h and now.minute == target_m and _last_auto_news_run != key:
        _last_auto_news_run = key
        topics_raw = get_setting("auto_news_topic","Bitcoin")
        topics     = [t.strip() for t in topics_raw.split(",") if t.strip()]
        topic      = topics[now.day % len(topics)] if topics else "Bitcoin"
        logger.info(f"Oto-haber: {topic}")
        content = await call_grok(NEWS_SYS, f"'{topic}' hakkında KriptoDropTR grubu için güncel haber/analiz yaz.", 900)
        if content.startswith("❌") or content.startswith("⏱"): logger.error(f"Oto-haber hatası: {content}"); return
        msg = f"📰 *KriptoDropTR — Günlük Kripto Haber*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}\n\n🔔 @KriptoDropTR"
        if len(msg) > 4096: msg = msg[:4090] + "..."
        try:
            await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
            with db() as conn: conn.execute("INSERT INTO news_log (topic,content) VALUES(?,?)", (topic,content))
            logger.info(f"Oto-haber gönderildi: {topic}")
        except Exception as e:
            logger.error(f"Oto-haber gönderilemedi: {e}")

async def job_deadline_check(context: ContextTypes.DEFAULT_TYPE):
    if get_setting("deadline_warn_enabled") != "1": return
    warn_days = int(get_setting("deadline_warn_days","3"))
    today     = datetime.now()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 AND deadline!='' AND deadline_warned=0").fetchall()
    for row in rows:
        deadline_dt = None
        for fmt_str in ("%d.%m.%Y","%Y-%m-%d","%d/%m/%Y"):
            try: deadline_dt = datetime.strptime(row["deadline"].strip(), fmt_str); break
            except: pass
        if not deadline_dt: continue
        days_left = (deadline_dt - today).days
        if 0 <= days_left <= warn_days:
            msg = (f"⏰ *HATIRLATMA — Airdrop Bitiyor!*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"*{row['name']}* — son *{days_left}* gün!\n"
                   f"💰 {row['reward'] or 'Belirtilmedi'} | ⏰ {row['deadline']}")
            kb = [[InlineKeyboardButton("🚀 Hemen Katıl!", url=row["link"])]] if row["link"] else []
            try:
                await context.bot.send_message(GROUP_ID, msg, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
                with db() as conn: conn.execute("UPDATE airdrops SET deadline_warned=1 WHERE id=?", (row["id"],))
                logger.info(f"Deadline uyarısı: {row['name']} ({days_left}g)")
            except Exception as e:
                logger.error(f"Deadline uyarısı hatası: {e}")

_last_weekly_run = ""

async def job_weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    global _last_weekly_run
    if get_setting("weekly_summary_enabled") != "1": return
    now        = datetime.now()
    target_day = int(get_setting("weekly_summary_day","1"))
    target_h   = int(get_setting("weekly_summary_hour","9"))
    key        = now.strftime(f"%Y-%W-{target_day}")  # Bu haftanın anahtarı
    if now.weekday() != target_day or now.hour != target_h or _last_weekly_run == key: return
    _last_weekly_run = key
    logger.info("Haftalık özet başlatılıyor...")
    with db() as conn:
        week_drops = conn.execute("SELECT name FROM airdrops WHERE created_at>=datetime('now','-7 days') AND active=1").fetchall()
    drops_text = ", ".join([r["name"] for r in week_drops]) or "Bu hafta yeni airdrop eklenmedi"
    content = await call_grok(SUMMARY_SYS, f"Bu haftanın kripto özetini yaz. Bu hafta eklenen airdroplar: {drops_text}", 900)
    if content.startswith("❌") or content.startswith("⏱"): logger.error(f"Haftalık özet hatası: {content}"); return
    msg = f"📅 *KriptoDropTR — Haftalık Kripto Özet*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}\n\n🔔 @KriptoDropTR"
    if len(msg) > 4096: msg = msg[:4090] + "..."
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        logger.info("Haftalık özet gönderildi.")
    except Exception as e:
        logger.error(f"Haftalık özet hatası: {e}")

def schedule_jobs(app: Application):
    from datetime import time as dtime
    jq = app.job_queue
    jq.run_daily(job_deadline_check, time=dtime(8, 0))   # Her gün 08:00'de deadline kontrolü
    jq.run_repeating(auto_news_scheduler, interval=60, first=10)  # Her dakika saat kontrolü
    jq.run_repeating(job_weekly_summary,  interval=3600, first=30) # Her saat haftalık gün kontrolü
    logger.info("Scheduler görevleri başlatıldı.")

# ── YARDIMCI ──────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = BACK_ADMIN if is_admin(update.effective_user.id) else BACK_USER
    await update.message.reply_text("❌ İşlem iptal edildi.", reply_markup=kb)
    return ConversationHandler.END

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); await show_admin(update, context)

async def back_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); await show_user(update, context)

async def unknown_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private": return
    register_user(update.effective_user)
    if is_admin(update.effective_user.id): await update.message.reply_text("🤖 /start yazarak menüyü aç.", reply_markup=BACK_ADMIN)
    else: await update.message.reply_text("👋 /start yazarak menüyü aç.", reply_markup=BACK_USER)

# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data; uid = q.from_user.id

    if d == "back_admin" and is_admin(uid): await back_admin(update, context); return
    if d == "back_user":                    await back_user(update, context);  return
    if d == "price_menu":                   await price_menu(update, context); return
    if d.startswith("qp_"):                 await cb_quick_price(update, context); return
    if d == "price_custom":                 await price_custom_prompt(update, context); return
    if d.startswith("save_airdrop_"):       await cb_save_airdrop(update, context); return

    if not is_admin(uid):
        u_routes = {"u_list":u_list,"u_pinned":u_pinned,"u_category":u_category,
                    "u_recent":u_recent,"u_saved":u_saved,"u_help":u_help}
        if d in u_routes: await u_routes[d](update, context); return
        if d.startswith("uc_"): await u_filter_cat(update, context); return
        await q.answer("⛔ Yetki yok.", show_alert=True); return

    # Admin rotaları
    admin_routes = {
        "manage_airdrops": manage_airdrops, "mng_list": mng_list,
        "stats": stats_handler,             "users_panel": users_panel,
        "group_info": group_info_handler,   "news_history": news_history_handler,
        "ann_history": ann_history_handler, "coin_analysis": coin_analysis_entry,
        "news_do_send": news_do_send,       "ann_send": ann_send,
        "ann_redo": ann_redo,               "settings": settings_panel,
    }
    if d in admin_routes: await admin_routes[d](update, context); return

    if d in ("stg_toggle_auto_news","stg_toggle_deadline","stg_toggle_weekly"):
        await settings_toggle(update, context); return
    if d == "stg_set_weekly_day":   await settings_day_picker(update, context); return
    if d.startswith("stg_day_"):    await settings_day_set(update, context);    return
    if d in ("stg_set_news_hour","stg_set_news_topic","stg_set_deadline_days","stg_set_weekly_hour","stg_set_grok_model"):
        await settings_input_prompt(update, context); return
    if d in ("mng_delete","mng_toggle","mng_pin","mng_broadcast"): await mng_action(update, context); return
    if d.startswith("do_delete_"):     await do_delete(update, context);      return
    if d.startswith("do_toggle_"):     await do_toggle(update, context);      return
    if d.startswith("do_pin_"):        await do_pin(update, context);         return
    if d.startswith("do_broadcast_"):  await do_broadcast(update, context);   return
    if d.startswith("qa_"):            await cb_quick_analysis(update, context); return
    if d.startswith("send_analysis_"): await send_analysis(update, context);  return

    u_routes = {"u_list":u_list,"u_pinned":u_pinned,"u_category":u_category,
                "u_recent":u_recent,"u_saved":u_saved,"u_help":u_help}
    if d in u_routes: await u_routes[d](update, context); return
    if d.startswith("uc_"): await u_filter_cat(update, context); return
    await q.answer()

# ── post_init ─────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",    "Bot menüsünü aç"),
        BotCommand("airdrops", "Aktif airdropları listele"),
        BotCommand("haberler", "Son haberlere bak"),
        BotCommand("fiyat",    "Coin fiyatı sorgula"),
        BotCommand("iptal",    "İşlemi iptal et"),
    ])

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    airdrop_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_airdrop_entry, pattern="^add_airdrop$")],
        states={
            AIRDROP_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, s_name)],
            AIRDROP_PROJECT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, s_project)],
            AIRDROP_DESC: [
                CallbackQueryHandler(cb_ai_desc,     pattern="^ai_desc$"),
                CallbackQueryHandler(cb_use_ai_desc, pattern="^use_ai_desc$"),
                CallbackQueryHandler(cb_manual_desc, pattern="^manual_desc$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, s_desc),
            ],
            AIRDROP_REWARD:   [MessageHandler(filters.TEXT & ~filters.COMMAND, s_reward)],
            AIRDROP_LINK:     [MessageHandler(filters.TEXT & ~filters.COMMAND, s_link)],
            AIRDROP_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, s_deadline)],
            AIRDROP_CATEGORY: [CallbackQueryHandler(s_category, pattern=r"^nc_\d+$")],
        },
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True, conversation_timeout=300,
    )

    news_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(send_news_entry, pattern="^send_news$")],
        states={
            NEWS_TOPIC: [
                CallbackQueryHandler(cb_quick_news,  pattern=r"^qnews_.+"),
                CallbackQueryHandler(cb_news_manual, pattern="^news_manual$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, news_text_input),
            ],
            NEWS_PREVIEW: [
                CallbackQueryHandler(news_do_send,    pattern="^news_do_send$"),
                CallbackQueryHandler(cb_quick_news,   pattern=r"^qnews_.+"),
                CallbackQueryHandler(cb_news_manual,  pattern="^news_manual$"),
                CallbackQueryHandler(send_news_entry, pattern="^send_news$"),
            ],
        },
        fallbacks=[CommandHandler("iptal", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        allow_reentry=True, conversation_timeout=300,
    )

    announce_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(announce_entry, pattern="^announce$")],
        states={
            ANNOUNCE_TEXT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, announce_preview)],
            ANNOUNCE_CONFIRM: [
                CallbackQueryHandler(ann_send, pattern="^ann_send$"),
                CallbackQueryHandler(ann_redo,  pattern="^ann_redo$"),
            ],
        },
        fallbacks=[CommandHandler("iptal", cancel), CallbackQueryHandler(back_admin, pattern="^back_admin$")],
        allow_reentry=True, conversation_timeout=300,
    )

    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(price_custom_prompt, pattern="^price_custom$")],
        states={PRICE_COIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_coin_input)]},
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True, conversation_timeout=120,
    )

    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(settings_input_prompt,
            pattern=r"^stg_set_(news_hour|news_topic|deadline_days|weekly_hour|grok_model)$")],
        states={SETTINGS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_save_input)]},
        fallbacks=[CommandHandler("iptal", cancel), CallbackQueryHandler(settings_panel, pattern="^settings$")],
        allow_reentry=True, conversation_timeout=120,
    )

    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("airdrops", cmd_airdrops))
    app.add_handler(CommandHandler("haberler", cmd_haberler))
    app.add_handler(CommandHandler("fiyat",    cmd_fiyat))
    app.add_handler(CommandHandler("iptal",    cancel))
    app.add_handler(airdrop_conv)
    app.add_handler(news_conv)
    app.add_handler(announce_conv)
    app.add_handler(price_conv)
    app.add_handler(settings_conv)
    app.add_handler(CallbackQueryHandler(cb_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, unknown_private))

    schedule_jobs(app)
    logger.info("🚀 KriptoDropTR Bot v4.0 başlatıldı!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
