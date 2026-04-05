#!/usr/bin/env python3
"""KriptoDropTR Telegram Botu v5.0 — Gelişmiş Haber Sistemi"""

import sqlite3, logging, httpx, re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode, MessageEntityType
from config import BOT_TOKEN, ADMIN_ID, GROUP_ID, CHANNEL_ID, GROK_API_KEY, DB_PATH

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ── CONVERSATION STATES ───────────────────────────────────────────────────────
(AIRDROP_NAME, AIRDROP_PROJECT, AIRDROP_DESC, AIRDROP_REWARD,
 AIRDROP_LINK, AIRDROP_DEADLINE, AIRDROP_CATEGORY,
 NEWS_TOPIC, NEWS_STYLE, NEWS_PREVIEW,
 ANNOUNCE_TEXT, ANNOUNCE_CONFIRM,
 SETTINGS_INPUT) = range(13)

CATEGORIES    = ["🪙 DeFi","🎮 GameFi","🖼 NFT","🔗 Layer1/Layer2","📱 Web3","🌐 Diğer"]
BACK_ADMIN    = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
BACK_USER     = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]])
BACK_SETTINGS = InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Ayarlara Dön", callback_data="settings")]])
BACK_NEWS     = InlineKeyboardMarkup([[InlineKeyboardButton("📰 Habere Dön", callback_data="send_news")]])

# ── VERİTABANI ────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as c:
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
                topic TEXT, style TEXT, content TEXT,
                sent_at TEXT DEFAULT (datetime('now','localtime')),
                auto INTEGER DEFAULT 0
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
                user_id INTEGER, airdrop_id INTEGER,
                saved_at TEXT DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (user_id, airdrop_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT
            );
        """)
        defaults = {
            "auto_news_enabled":      "0",
            "auto_news_hour":         "10",
            "auto_news_minute":       "00",
            "auto_news_topic":        "Bitcoin,Ethereum,DeFi piyasası,Solana,Kripto regülasyon",
            "auto_news_style":        "haber",      # haber | analiz | ozet
            "deadline_warn_days":     "3",
            "deadline_warn_enabled":  "1",
            "weekly_summary_enabled": "0",
            "weekly_summary_day":     "1",
            "weekly_summary_hour":    "09",
            "grok_model":             "llama-3.3-70b-versatile",
            "news_footer":            "🔔 @KriptoDropTR",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))

def db():
    conn = sqlite3.connect(DB_PATH)
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

# ── GROQ AI (Ücretsiz) ────────────────────────────────────────────────────────
GROK_URL = "https://api.groq.com/openai/v1/chat/completions"

# Haber stilleri — her biri farklı bir prompt sistemi kullanır
NEWS_STYLES = {
    "haber": {
        "label": "📰 Standart Haber",
        "emoji": "📰",
        "system": """Sen KriptoDropTR adlı Türkçe kripto Telegram grubu için haber yazarısın.
Verilen konuda güncel, bilgilendirici, akıcı Türkçe kripto haberi yaz.
Emoji kullan, kısa paragraflar kullan, 250-350 kelime olsun.
Sonuna '💡 Önemli Not:' ile kısa bir yorum ekle.
Format: 📰 [BAŞLIK]\n\n[içerik]\n\n💡 Önemli Not: [yorum]""",
    },
    "analiz": {
        "label": "🔍 Derinlemesine Analiz",
        "emoji": "🔍",
        "system": """Sen KriptoDropTR için kripto piyasa analisti ve yazarısın.
Verilen konu hakkında derinlemesine, profesyonel Türkçe analiz yaz.
Teknik ve temel analiz unsurlarını birleştir. 350-450 kelime olsun.
Başlık, trend analizi, destekler/dirençler, önemli gelişmeler ve sonuç bölümleri olsun.
Format: 🔍 [BAŞLIK]\n\n📈 Piyasa Durumu:\n[analiz]\n\n🎯 Önemli Seviyeler:\n[seviyeler]\n\n✅ Sonuç:\n[sonuç]\n\n⚠️ Yatırım tavsiyesi değildir.""",
    },
    "ozet": {
        "label": "⚡ Hızlı Özet",
        "emoji": "⚡",
        "system": """Sen KriptoDropTR için kısa ve öz kripto haber özeti yazarısın.
Verilen konu hakkında hızlı, madde madde Türkçe özet yaz. 150-200 kelime olsun.
Format: ⚡ [BAŞLIK]\n\n🔸 [madde 1]\n🔸 [madde 2]\n🔸 [madde 3]\n🔸 [madde 4]\n🔸 [madde 5]\n\n📌 Sonuç: [tek cümle özet]""",
    },
    "bulteni": {
        "label": "📋 Günlük Bülten",
        "emoji": "📋",
        "system": """Sen KriptoDropTR için günlük kripto bülten yazarısın.
Verilen konuyu merkeze alarak o günün kripto piyasasını değerlendiren Türkçe bülten yaz.
Sabah bülteni havasında, heyecan verici, emoji dolu, 300-400 kelime olsun.
Format: 📋 GÜNLÜK KRİPTO BÜLTENİ — [tarih]\n\n[içerik]\n\n🚀 Günün Özeti:\n[özet]""",
    },
    "haftalik": {
        "label": "📅 Haftalık Özet",
        "emoji": "📅",
        "system": """Sen KriptoDropTR için haftalık kripto özet yazarısın.
Bu haftanın en önemli kripto gelişmelerini Türkçe özetle. 350-450 kelime olsun.
Başlık, 5-6 önemli gelişme ve kapanış yorumuyla yaz. Emoji kullan.
Format: 📅 HAFTALIK KRİPTO ÖZETİ\n\n[içerik]\n\n🎯 Haftanın Özeti: [kapanış]""",
    },
}

QUICK_TOPICS = [
    ("₿ Bitcoin","Bitcoin"),         ("Ξ Ethereum","Ethereum"),
    ("◎ Solana","Solana"),            ("🔵 BNB Chain","BNB Chain"),
    ("🌐 DeFi","DeFi piyasası"),      ("🎮 GameFi","GameFi"),
    ("🖼 NFT","NFT piyasası"),         ("⚖️ Regülasyon","Kripto regülasyon"),
    ("🔗 Layer 2","Layer 2 projeleri"),("🪙 Altcoin","Altcoin sezonu"),
    ("📊 Piyasa","Kripto piyasa genel"),("🚀 Airdrop","Kripto airdrop trendleri"),
]

async def call_grok(system: str, prompt: str, tokens: int = 900) -> str:
    model = get_setting("grok_model", "llama-3.3-70b-versatile")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                GROK_URL,
                headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role":"system","content":system},
                                   {"role":"user","content":prompt}],
                      "max_tokens": tokens, "temperature": 0.75}
            )
        if r.status_code == 401:
            return "❌ API Anahtarı hatalı! Railway'de GROQ_API_KEY değerini kontrol et.\nAnahtarı https://console.groq.com adresinden alabilirsin."
        if r.status_code == 429:
            return "❌ API limit aşıldı. Birkaç dakika sonra tekrar dene."
        if r.status_code == 404:
            return (f"❌ Model bulunamadı: `{model}`\n\n"
                    f"⚙️ Ayarlar > AI Modeli'nden değiştir.\n"
                    f"Ücretsiz modeller: `llama-3.3-70b-versatile`, `llama3-70b-8192`, `mixtral-8x7b-32768`")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        return "⏱ Grok API zaman aşımı (60s). Tekrar dene."
    except httpx.HTTPStatusError as e:
        return f"❌ API Hatası {e.response.status_code}:\n`{e.response.text[:300]}`"
    except Exception as e:
        logger.error(f"Grok hata: {e}", exc_info=True)
        return f"❌ Beklenmeyen hata: {type(e).__name__}: {e}"

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
        return  # Grup içinde /start'ı tamamen yoksay
    register_user(update.effective_user)
    context.user_data.clear()
    if is_admin(update.effective_user.id): await show_admin(update, context)
    else: await show_user(update, context)

async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        news_n = conn.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
        auto_n = conn.execute("SELECT COUNT(*) FROM news_log WHERE auto=1").fetchone()[0]
        users  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    an = "🟢" if get_setting("auto_news_enabled") == "1" else "🔴"
    dl = "🟢" if get_setting("deadline_warn_enabled") == "1" else "🔴"
    wk = "🟢" if get_setting("weekly_summary_enabled") == "1" else "🔴"
    model = get_setting("grok_model","grok-3")
    text = (f"🛠 *KriptoDropTR — Admin Paneli*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪂 Toplam: *{total}* (Aktif: *{active}*)\n"
            f"📰 Haber: *{news_n}* (Oto: *{auto_n}*) | 👤 Kullanıcı: *{users}*\n"
            f"⚡ Oto-Haber:{an}  Deadline:{dl}  Haftalık:{wk}\n"
            f"🤖 Model: `{model}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n👇 İşlem seç:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Airdrop Ekle",       callback_data="add_airdrop"),
         InlineKeyboardButton("🗂 Airdrop Yönet",      callback_data="manage_airdrops")],
        [InlineKeyboardButton("📰 Haber Oluştur (AI)", callback_data="send_news"),
         InlineKeyboardButton("📋 Haber Geçmişi",      callback_data="news_history")],
        [InlineKeyboardButton("📢 Duyuru Yap",         callback_data="announce"),
         InlineKeyboardButton("📊 İstatistikler",      callback_data="stats")],
        [InlineKeyboardButton("👤 Kullanıcılar",       callback_data="users_panel"),
         InlineKeyboardButton("⚙️ Ayarlar",            callback_data="settings")],
        [InlineKeyboardButton("👥 Grup Bilgisi",       callback_data="group_info"),
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
         InlineKeyboardButton("❓ Yardım",            callback_data="u_help")],
    ])
    try: await update.effective_message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except: await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ── AIRDROP EKLEME CONV ───────────────────────────────────────────────────────
async def add_airdrop_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); context.user_data.clear()
    await q.message.reply_text(
        "➕ *Yeni Airdrop Ekle*\n━━━━━━━━━━━━━\n📛 *Airdrop adını* girin:\n_(Örn: Arbitrum Season 2)_\n\n❌ /iptal",
        parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_NAME

async def s_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(f"✅ Ad: *{context.user_data['name']}*\n\n🏢 *Proje/Token adı:*\n_(Örn: ARB)_", parse_mode=ParseMode.MARKDOWN)
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
    sys  = "Kripto airdrop için 2-3 cümle Türkçe açıklama yaz. Emoji kullan, katılmaya teşvik et."
    desc = await call_grok(sys, f"Proje: {context.user_data.get('project','?')}, Airdrop: {context.user_data.get('name','?')}", 200)
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
    await update.message.reply_text("💰 *Ödül:*\n_(Örn: 1000 ARB token)_", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_REWARD

async def s_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reward"] = update.message.text.strip()
    await update.message.reply_text("🔗 *Katılım linki:*\n_(URL veya 'yok')_", parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_LINK

async def s_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    context.user_data["link"] = "" if t.lower() in ("yok","-","none") else t
    await update.message.reply_text(
        "⏰ *Son katılım tarihi:*\n_(Örn: 31.12.2025 veya 'belirsiz')_\n\n💡 GG.AA.YYYY formatı → deadline uyarısı aktif olur.",
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

# ── 📰 GELİŞMİŞ HABER CONV ───────────────────────────────────────────────────

async def send_news_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """1. Adım: Konu seç."""
    q = update.callback_query
    if q: await q.answer()
    context.user_data.pop("news_content", None)
    context.user_data.pop("news_topic",   None)
    context.user_data.pop("news_style",   None)

    rows    = [QUICK_TOPICS[i:i+3] for i in range(0, len(QUICK_TOPICS), 3)]
    kb_rows = [[InlineKeyboardButton(n, callback_data=f"qnews_{t}") for n,t in row] for row in rows]
    kb_rows.append([InlineKeyboardButton("✏️ Kendi Konumu Yaz", callback_data="news_manual")])
    kb_rows.append([InlineKeyboardButton("❌ İptal", callback_data="back_admin")])

    msg_text = ("📰 *Haber Oluştur — Konu Seç*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Aşağıdan bir konu seç ya da kendin yaz:")
    if q:
        try: await q.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
        except: await q.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    return NEWS_TOPIC

async def cb_quick_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hızlı konu seçildi → stil seçimine geç."""
    q = update.callback_query; await q.answer()
    topic = q.data[6:]  # "qnews_" kaldır
    context.user_data["news_topic"] = topic
    return await ask_news_style(update, context)

async def cb_news_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manuel konu girişi."""
    q = update.callback_query; await q.answer()
    await q.message.reply_text(
        "✏️ *Haber konusunu yaz:*\n_(Örn: Ethereum ETF onayı)_\n\n❌ /iptal",
        parse_mode=ParseMode.MARKDOWN)
    return NEWS_TOPIC

async def news_topic_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yazılan konuyu kaydet → stil seçimine geç."""
    context.user_data["news_topic"] = update.message.text.strip()
    return await ask_news_style(update, context)

async def ask_news_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """2. Adım: Haber stilini seç."""
    topic = context.user_data.get("news_topic","?")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(s["label"], callback_data=f"nstyle_{k}")]
        for k, s in NEWS_STYLES.items()
    ] + [[InlineKeyboardButton("❌ İptal", callback_data="back_admin")]])
    msg_text = (f"✅ Konu: *{topic}*\n\n"
                f"🎨 *Haber stilini seç:*\n"
                f"━━━━━━━━━━━━━━━━━━━━")
    q = update.callback_query
    if q:
        try: await q.edit_message_text(msg_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except: await q.message.reply_text(msg_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(msg_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return NEWS_STYLE

async def cb_news_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stil seçildi → haber üret."""
    q = update.callback_query; await q.answer()
    style_key = q.data.replace("nstyle_", "")
    context.user_data["news_style"] = style_key
    return await _gen_news(update, context)


# ── GERÇEK ZAMANLI PİYASA & HABER CONTEXT ────────────────────────────────────
# Konu → CoinGecko coin ID eşleştirmesi
TOPIC_COINS = {
    "bitcoin":               [("Bitcoin","BTC","bitcoin")],
    "ethereum":              [("Ethereum","ETH","ethereum")],
    "solana":                [("Solana","SOL","solana")],
    "bnb chain":             [("BNB Chain","BNB","binancecoin")],
    "bnb":                   [("BNB Chain","BNB","binancecoin")],
    "defi piyasası":         [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum"),("Uniswap","UNI","uniswap")],
    "defi":                  [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum"),("Uniswap","UNI","uniswap")],
    "nft piyasası":          [("Ethereum","ETH","ethereum"),("Solana","SOL","solana")],
    "gamefi":                [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum")],
    "kripto regülasyon":     [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum")],
    "layer 2 projeleri":     [("Ethereum","ETH","ethereum"),("Arbitrum","ARB","arbitrum"),("Optimism","OP","optimism")],
    "altcoin sezonu":        [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum"),("Solana","SOL","solana")],
    "kripto piyasa genel":   [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum"),("Solana","SOL","solana"),("BNB Chain","BNB","binancecoin")],
    "kripto airdrop trendleri": [("Bitcoin","BTC","bitcoin"),("Ethereum","ETH","ethereum")],
}

# RSS kaynaklarından haber başlığı çek
RSS_SOURCES = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
]

async def _fetch_rss_headlines(topic: str, max_items: int = 5) -> list[str]:
    """İki RSS kaynağından konu ile ilgili güncel haber başlıklarını çek."""
    topic_words = [w.lower() for w in topic.split() if len(w) > 3]
    headlines   = []
    for source_name, url in RSS_SOURCES:
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            text = r.text
            # Basit XML parse — <title> taglarını çıkar
            import re
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', text)
            if not titles:
                titles = re.findall(r'<title>(.*?)</title>', text)
            titles = [t.strip() for t in titles if len(t.strip()) > 20][1:]  # ilk başlık feed başlığı
            # Konuyla ilgili olanları filtrele; yoksa ilk N tanesini al
            matched = [t for t in titles if any(w in t.lower() for w in topic_words)]
            selected = matched[:3] if matched else titles[:2]
            for t in selected:
                headlines.append(f"[{source_name}] {t}")
            if len(headlines) >= max_items:
                break
        except Exception as e:
            logger.debug(f"RSS çekme hatası ({source_name}): {e}")
    return headlines[:max_items]

async def _fetch_coin_prices(coins: list) -> dict:
    """CoinGecko'dan fiyat, değişim ve piyasa değeri çek."""
    ids = ",".join(c[2] for c in coins)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids":                ids,
                    "vs_currencies":      "usd",
                    "include_24hr_change":"true",
                    "include_7d_change":  "true",
                    "include_market_cap": "true",
                    "include_24hr_vol":   "true",
                })
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        logger.warning(f"CoinGecko hatası: {e}")
        return {}

def _build_market_system_block(topic: str, price_data: dict, coins: list, headlines: list) -> str:
    """
    Gerçek verileri SYSTEM mesajının başına eklenecek bir blok olarak formatla.
    Veriler system'a eklenir → AI bunu 'gerçek bağlam' olarak alır, görmezden gelemez.
    """
    today = datetime.now().strftime("%d %B %Y, %H:%M")
    lines = [
        "=" * 60,
        f"GERÇEK ZAMANLI GÜNCEL VERİLER — {today}",
        "Bu veriler canlı API'den alınmıştır. Haberde SADECE bu",
        "verileri kullan. Asla farklı fiyat veya tarih uydurma.",
        "=" * 60,
        "",
    ]

    # Fiyat tablosu
    if price_data:
        lines.append("📊 CANLI FİYAT VERİLERİ:")
        def fmt_n(n):
            if n >= 1e9:  return f"${n/1e9:.2f}B"
            if n >= 1e6:  return f"${n/1e6:.1f}M"
            if n >= 1000: return f"${n:,.0f}"
            return f"${n:,.4f}"
        for name, symbol, cid in coins:
            if cid not in price_data: continue
            d      = price_data[cid]
            price  = d.get("usd", 0)
            ch24   = d.get("usd_24h_change", 0) or 0
            ch7    = d.get("usd_7d_change", 0) or 0
            vol    = d.get("usd_24h_vol", 0) or 0
            mcap   = d.get("usd_market_cap", 0) or 0
            lines.append(
                f"  {name} ({symbol}): {fmt_n(price)} | "
                f"24s: {'▲' if ch24>=0 else '▼'}{ch24:+.2f}% | "
                f"7g: {'▲' if ch7>=0 else '▼'}{ch7:+.2f}% | "
                f"Hacim: {fmt_n(vol)} | Mcap: {fmt_n(mcap)}"
            )
        lines.append("")

    # Haber başlıkları
    if headlines:
        lines.append(f"📰 BUGÜNÜN GÜNCEL HABER BAŞLIKLARI ({topic} ile ilgili):")
        for h in headlines:
            lines.append(f"  • {h}")
        lines.append("")

    lines += [
        "=" * 60,
        "ÖNEMLİ: Yukarıdaki fiyatlar gerçek ve günceldir.",
        "Haberde bu rakamlara atıfta bulun. ASLA farklı fiyat yazma.",
        "=" * 60,
        "",
    ]
    return "\n".join(lines)

async def _build_news_context(topic: str) -> tuple[str, str]:
    """
    Hem fiyat hem haber verisi çek.
    Döndürür: (zenginleştirilmiş_system_prefix, kısa_özet_log)
    """
    topic_lower = topic.lower().strip()

    # Konu → coin eşleştir
    coins = TOPIC_COINS.get(topic_lower, [])
    if not coins:
        for key, val in TOPIC_COINS.items():
            if any(w in topic_lower for w in key.split() if len(w) > 3):
                coins = val; break
    if not coins:
        coins = [("Bitcoin","BTC","bitcoin"), ("Ethereum","ETH","ethereum")]

    # Paralel çek: fiyat + RSS aynı anda
    import asyncio
    price_data, headlines = await asyncio.gather(
        _fetch_coin_prices(coins),
        _fetch_rss_headlines(topic),
    )

    system_block = _build_market_system_block(topic, price_data, coins, headlines)
    log_summary  = f"{len(price_data)} coin fiyatı, {len(headlines)} haber başlığı"
    return system_block, log_summary

async def call_grok_with_data(base_system: str, data_block: str, prompt: str, tokens: int = 1000) -> str:
    """
    Gerçek verileri system mesajının BAŞINA ekleyerek çağır.
    Bu şekilde AI verileri 'talimat' seviyesinde görür, user mesajı gibi görmez.
    """
    enriched_system = data_block + "\n\n" + base_system
    return await call_grok(enriched_system, prompt, tokens)

async def _gen_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerçek fiyat + RSS haber başlıklarıyla zenginleştirilmiş haber üret."""
    topic     = context.user_data.get("news_topic","Bitcoin")
    style_key = context.user_data.get("news_style","haber")
    style     = NEWS_STYLES.get(style_key, NEWS_STYLES["haber"])
    q         = update.callback_query

    wait_text = f"📡 *{topic}* için canlı veri çekiliyor..."
    if q: wait = await q.message.reply_text(wait_text, parse_mode=ParseMode.MARKDOWN)
    else: wait = await update.message.reply_text(wait_text, parse_mode=ParseMode.MARKDOWN)

    # Paralel: fiyat + RSS haber başlıkları
    data_block, log_summary = await _build_news_context(topic)
    logger.info(f"Haber verisi hazır: {log_summary}")

    today  = datetime.now().strftime("%d %B %Y, %H:%M")
    prompt = (
        f"Konu: '{topic}'\n"
        f"Tarih: {today}\n\n"
        f"Sistem bloğundaki GERÇEK verileri ve haber başlıklarını kullanarak "
        f"içerik oluştur. Başlıklardaki gerçek gelişmeleri yansıt."
    )
    # Veriler system mesajına ekleniyor → AI görmezden gelemez
    content = await call_grok_with_data(style["system"], data_block, prompt, 1000)
    await wait.delete()

    if content.startswith("❌") or content.startswith("⏱"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Tekrar Dene",   callback_data="news_retry")],
            [InlineKeyboardButton("🎨 Stil Değiştir", callback_data=f"qnews_{topic}")],
            [InlineKeyboardButton("✏️ Başka Konu",    callback_data="news_manual")],
            [InlineKeyboardButton("❌ İptal",          callback_data="back_admin")],
        ])
        err = f"❌ *Haber oluşturulamadı*\n━━━━━━━━━━\n{content}"
        if q: await q.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return NEWS_PREVIEW

    context.user_data["news_content"] = content

    footer  = get_setting("news_footer","🔔 @KriptoDropTR")
    preview = f"{style['emoji']} *Önizleme — {topic}*\n🎨 Stil: {style['label']}\n━━━━━━━━━━━━━━━━━━━━\n\n{content}"
    if len(preview) > 4000: preview = preview[:3990] + "..."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gruba Gönder",    callback_data="news_do_send"),
         InlineKeyboardButton("🔄 Yeniden Oluştur", callback_data="news_retry")],
        [InlineKeyboardButton("🎨 Stil Değiştir",   callback_data=f"qnews_{topic}"),
         InlineKeyboardButton("✏️ Farklı Konu",     callback_data="news_manual")],
        [InlineKeyboardButton("❌ İptal",            callback_data="back_admin")],
    ])
    if q: await q.message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return NEWS_PREVIEW

async def news_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aynı konu ve stil ile yeniden üret."""
    q = update.callback_query; await q.answer()
    return await _gen_news(update, context)

async def news_do_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Haberi gruba gönder."""
    q = update.callback_query; await q.answer("Gönderiliyor...")
    content   = context.user_data.get("news_content","")
    topic     = context.user_data.get("news_topic","Genel")
    style_key = context.user_data.get("news_style","haber")
    style     = NEWS_STYLES.get(style_key, NEWS_STYLES["haber"])
    footer    = get_setting("news_footer","🔔 @KriptoDropTR")

    if not content:
        await q.edit_message_text("❌ İçerik bulunamadı. Yeni haber oluştur.", reply_markup=BACK_ADMIN)
        return ConversationHandler.END

    msg = f"{content}\n\n━━━━━━━━━━━━━━━━━━━━\n{footer}"
    if len(msg) > 4096: msg = msg[:4086] + "...\n\n" + footer

    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn:
            conn.execute("INSERT INTO news_log (topic,style,content,auto) VALUES(?,?,?,0)",
                         (topic, style_key, content))
        await q.edit_message_text(
            f"✅ *Haber gruba gönderildi!*\n\n"
            f"📌 Konu: {topic}\n"
            f"🎨 Stil: {style['label']}",
            reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Haber gönderildi: {topic} [{style_key}]")
    except Exception as e:
        logger.error(f"Haber gönderme hatası: {e}")
        await q.edit_message_text(
            f"❌ *Gönderme hatası:*\n`{e}`\n\n_Botun grupta admin olduğundan emin ol._",
            reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END

# ── 📋 HABER GEÇMİŞİ ─────────────────────────────────────────────────────────
async def news_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Son 10 haberi listele + detay butonu."""
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute(
            "SELECT id,topic,style,sent_at,auto FROM news_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
    if not rows:
        await q.edit_message_text("📭 Henüz haber gönderilmemiş.", reply_markup=BACK_ADMIN); return

    text = "📋 *Son 10 Haber*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    kb   = []
    for r in rows:
        style_label = NEWS_STYLES.get(r["style"] or "haber", NEWS_STYLES["haber"])["emoji"]
        auto_icon   = "🤖" if r["auto"] else "👤"
        text += f"{auto_icon} {style_label} *{r['topic']}*\n   _{r['sent_at'][:16]}_\n\n"
        kb.append([InlineKeyboardButton(f"👁 {r['topic'][:30]}", callback_data=f"news_detail_{r['id']}")])
    kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def news_detail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Haber içeriğini göster."""
    q   = update.callback_query; await q.answer()
    nid = int(q.data.replace("news_detail_",""))
    with db() as conn:
        row = conn.execute("SELECT * FROM news_log WHERE id=?", (nid,)).fetchone()
    if not row:
        await q.answer("Haber bulunamadı.", show_alert=True); return
    style   = NEWS_STYLES.get(row["style"] or "haber", NEWS_STYLES["haber"])
    content = row["content"] or ""
    preview = f"{style['emoji']} *{row['topic']}*\n🎨 {style['label']} | _{row['sent_at'][:16]}_\n━━━━━━━━━━━━━━━━━━━━\n\n{content}"
    if len(preview) > 4000: preview = preview[:3990] + "..."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Tekrar Gönder", callback_data=f"news_resend_{nid}")],
        [InlineKeyboardButton("🔙 Listeye Dön",   callback_data="news_history")],
    ])
    try: await q.edit_message_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except: await q.message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def news_resend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Geçmiş haberi tekrar gruba gönder."""
    q   = update.callback_query; await q.answer("Gönderiliyor...")
    nid = int(q.data.replace("news_resend_",""))
    with db() as conn:
        row = conn.execute("SELECT * FROM news_log WHERE id=?", (nid,)).fetchone()
    if not row:
        await q.edit_message_text("❌ Haber bulunamadı.", reply_markup=BACK_ADMIN); return
    footer = get_setting("news_footer","🔔 @KriptoDropTR")
    msg    = f"{row['content']}\n\n━━━━━━━━━━━━━━━━━━━━\n{footer}"
    if len(msg) > 4096: msg = msg[:4086] + "...\n\n" + footer
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        await q.edit_message_text(f"✅ *{row['topic']}* tekrar gönderildi!", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)

# ── DUYURU CONV ───────────────────────────────────────────────────────────────
async def announce_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text(
        "📢 *Duyuru Metni*\n━━━━━━━━━━━━━━\nGruba göndereceğin duyuruyu yaz:\n❌ /iptal",
        parse_mode=ParseMode.MARKDOWN)
    return ANNOUNCE_TEXT

async def announce_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["announce"] = update.message.text
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gönder", callback_data="ann_send"),
         InlineKeyboardButton("✏️ Düzenle", callback_data="ann_redo")],
        [InlineKeyboardButton("❌ İptal", callback_data="back_admin")],
    ])
    await update.message.reply_text(
        f"👁 *Önizleme:*\n━━━━━━━━━━━━━━\n\n{update.message.text}\n\nGönderilsin mi?",
        reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return ANNOUNCE_CONFIRM

async def ann_redo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("✏️ Yeni duyuru metnini gir:")
    return ANNOUNCE_TEXT

async def ann_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    text = context.user_data.get("announce","")
    footer = get_setting("news_footer","🔔 @KriptoDropTR")
    msg  = f"📢 *DUYURU*\n━━━━━━━━━━━━━━━━━━━━\n\n{text}\n\n{footer}"
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn: conn.execute("INSERT INTO announcements (text) VALUES(?)", (text,))
        await q.edit_message_text("✅ Duyuru gönderildi!", reply_markup=BACK_ADMIN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)
    context.user_data.clear()
    return ConversationHandler.END

# ── ⚙️ AYARLAR PANELİ ─────────────────────────────────────────────────────────
async def settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    an_en   = get_setting("auto_news_enabled") == "1"
    an_h    = get_setting("auto_news_hour","10")
    an_m    = get_setting("auto_news_minute","00")
    an_t    = get_setting("auto_news_topic","Bitcoin")
    an_s    = get_setting("auto_news_style","haber")
    dl_en   = get_setting("deadline_warn_enabled") == "1"
    dl_d    = get_setting("deadline_warn_days","3")
    wk_en   = get_setting("weekly_summary_enabled") == "1"
    wk_day  = get_setting("weekly_summary_day","1")
    wk_h    = get_setting("weekly_summary_hour","09")
    model   = get_setting("grok_model","grok-3")
    footer  = get_setting("news_footer","🔔 @KriptoDropTR")
    days_map = {"0":"Pzt","1":"Sal","2":"Çar","3":"Per","4":"Cum","5":"Cmt","6":"Paz"}
    style_label = NEWS_STYLES.get(an_s, NEWS_STYLES["haber"])["label"]
    text = (
        f"⚙️ *Bot Ayarları*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📰 *Otomatik Haber*\n"
        f"  Durum: {'🟢 Açık' if an_en else '🔴 Kapalı'}\n"
        f"  Her gün saat *{an_h}:{an_m}*\n"
        f"  Stil: *{style_label}*\n"
        f"  Konular: _{an_t}_\n\n"
        f"⏰ *Deadline Uyarısı*\n"
        f"  Durum: {'🟢 Açık' if dl_en else '🔴 Kapalı'}\n"
        f"  Bitiş tarihinden *{dl_d} gün* önce uyar\n\n"
        f"📅 *Haftalık Özet*\n"
        f"  Durum: {'🟢 Açık' if wk_en else '🔴 Kapalı'}\n"
        f"  Her *{days_map.get(wk_day,'?')}* saat *{wk_h}:00*\n\n"
        f"🤖 *Grok Modeli:* `{model}`\n"
        f"📝 *Haber Footer:* _{footer}_\n\n"
        f"👇 Değiştirmek istediğin ayarı seç:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📰 Oto-Haber: {'🟢 Kapat' if an_en else '🔴 Aç'}", callback_data="stg_toggle_auto_news")],
        [InlineKeyboardButton("🕐 Haber Saati",    callback_data="stg_set_news_hour"),
         InlineKeyboardButton("📝 Haber Konuları", callback_data="stg_set_news_topic")],
        [InlineKeyboardButton("🎨 Oto-Haber Stili", callback_data="stg_set_news_style")],
        [InlineKeyboardButton(f"⏰ Deadline: {'🟢 Kapat' if dl_en else '🔴 Aç'}", callback_data="stg_toggle_deadline")],
        [InlineKeyboardButton("📆 Uyarı Kaç Gün Önce", callback_data="stg_set_deadline_days")],
        [InlineKeyboardButton(f"📅 Haftalık: {'🟢 Kapat' if wk_en else '🔴 Aç'}", callback_data="stg_toggle_weekly")],
        [InlineKeyboardButton("📅 Özet Günü",  callback_data="stg_set_weekly_day"),
         InlineKeyboardButton("🕐 Özet Saati", callback_data="stg_set_weekly_hour")],
        [InlineKeyboardButton("🤖 AI Modeli",   callback_data="stg_set_grok_model"),
         InlineKeyboardButton("📝 Haber Footer", callback_data="stg_set_news_footer")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")],
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def settings_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    map_ = {"stg_toggle_auto_news":("auto_news_enabled","Otomatik Haber"),
            "stg_toggle_deadline": ("deadline_warn_enabled","Deadline Uyarısı"),
            "stg_toggle_weekly":   ("weekly_summary_enabled","Haftalık Özet")}
    key, label = map_[q.data]
    current = get_setting(key) == "1"
    set_setting(key, "0" if current else "1")
    await q.answer(f"{label} {'🔴 Kapatıldı' if current else '🟢 Açıldı'}", show_alert=True)
    await settings_panel(update, context)

async def settings_news_style_picker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oto-haber için stil seçici."""
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(s["label"], callback_data=f"stg_nstyle_{k}")]
         for k, s in NEWS_STYLES.items()] +
        [[InlineKeyboardButton("⚙️ Ayarlara Dön", callback_data="settings")]]
    )
    await q.edit_message_text("🎨 *Otomatik haber için stil seç:*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def settings_news_style_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    style = q.data.replace("stg_nstyle_","")
    set_setting("auto_news_style", style)
    label = NEWS_STYLES.get(style, NEWS_STYLES["haber"])["label"]
    await q.answer(f"✅ Oto-haber stili: {label}", show_alert=True)
    await settings_panel(update, context)

async def settings_input_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    prompts = {
        "stg_set_news_hour":     "🕐 *Otomatik haber saatini gir* (0-23):\n_Örn: 10_",
        "stg_set_news_topic":    "📝 *Haber konularını gir* (virgülle ayır):\n_Örn: Bitcoin,Ethereum,DeFi_",
        "stg_set_deadline_days": "📆 *Kaç gün önce uyarı gelsin?* (1-30):\n_Örn: 3_",
        "stg_set_weekly_hour":   "🕐 *Haftalık özet saatini gir* (0-23):\n_Örn: 9_",
        "stg_set_grok_model":    "🤖 *AI modelini gir:*\n_Ücretsiz seçenekler:_\n• `llama-3.3-70b-versatile` _(önerilen)_\n• `llama3-70b-8192`\n• `mixtral-8x7b-32768`",
        "stg_set_news_footer":   "📝 *Haber footer metnini gir:*\n_Örn: 🔔 @KriptoDropTR_",
    }
    context.user_data["settings_key"] = q.data
    await q.message.reply_text(f"{prompts[q.data]}\n\n❌ /iptal", parse_mode=ParseMode.MARKDOWN)
    return SETTINGS_INPUT

async def settings_day_picker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    days = [("Pazartesi","0"),("Salı","1"),("Çarşamba","2"),("Perşembe","3"),
            ("Cuma","4"),("Cumartesi","5"),("Pazar","6")]
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(n, callback_data=f"stg_day_{v}")] for n,v in days] +
        [[InlineKeyboardButton("⚙️ Ayarlara Dön", callback_data="settings")]])
    await q.edit_message_text("📅 *Haftalık özet günü:*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def settings_day_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    day = q.data.replace("stg_day_","")
    set_setting("weekly_summary_day", day)
    days_map = {"0":"Pazartesi","1":"Salı","2":"Çarşamba","3":"Perşembe","4":"Cuma","5":"Cumartesi","6":"Pazar"}
    await q.answer(f"✅ {days_map.get(day,'?')} olarak ayarlandı!", show_alert=True)
    await settings_panel(update, context)

async def settings_save_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value  = update.message.text.strip()
    key_map = {
        "stg_set_news_hour":     ("auto_news_hour",       lambda v: str(max(0,min(23,int(v))))),
        "stg_set_news_topic":    ("auto_news_topic",       lambda v: v),
        "stg_set_deadline_days": ("deadline_warn_days",    lambda v: str(max(1,min(30,int(v))))),
        "stg_set_weekly_hour":   ("weekly_summary_hour",   lambda v: str(max(0,min(23,int(v))))),
        "stg_set_grok_model":    ("grok_model",            lambda v: v),
        "stg_set_news_footer":   ("news_footer",           lambda v: v),
    }
    sk = context.user_data.get("settings_key","")
    if sk not in key_map:
        await update.message.reply_text("❌ Bilinmeyen ayar.", reply_markup=BACK_ADMIN)
        return ConversationHandler.END
    db_key, transform = key_map[sk]
    try:
        final = transform(value)
        set_setting(db_key, final)
        await update.message.reply_text(
            f"✅ *Kaydedildi!*\n`{db_key}` = `{final}`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=BACK_SETTINGS)
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
    q = update.callback_query; await q.answer(); action = q.data
    with db() as conn:
        rows = conn.execute("SELECT id,name,active,pinned FROM airdrops ORDER BY id DESC LIMIT 20").fetchall()
    if not rows: await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_ADMIN); return
    icons = {"mng_delete":"🗑","mng_toggle":"✅","mng_pin":"📌","mng_broadcast":"📢"}
    icon  = icons.get(action,"•"); kb = []
    for r in rows:
        state = (" 🟢" if r["active"] else " 🔴") if action=="mng_toggle" else (" 📌" if r["pinned"] else "") if action=="mng_pin" else ""
        kb.append([InlineKeyboardButton(f"{icon} [{r['id']}] {r['name']}{state}", callback_data=f"do_{action.replace('mng_','')}_{r['id']}")])
    kb.append([InlineKeyboardButton("🔙 Geri", callback_data="manage_airdrops")])
    await q.edit_message_text(f"{icon} *Airdrop seç:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def do_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row: conn.execute("DELETE FROM airdrops WHERE id=?", (aid,))
    await q.edit_message_text(f"🗑 *{row['name'] if row else aid}* silindi.", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name,active FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row:
            new = 0 if row["active"] else 1
            conn.execute("UPDATE airdrops SET active=? WHERE id=?", (new,aid))
    await q.edit_message_text(f"✅ *{row['name']}* → {'🟢 Aktif' if new else '🔴 Pasif'}", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name,pinned FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row:
            new = 0 if row["pinned"] else 1
            conn.execute("UPDATE airdrops SET pinned=? WHERE id=?", (new,aid))
    await q.edit_message_text(f"{'📌 Sabitlendi' if new else '📋 Sabit Kaldırıldı'}: *{row['name']}*", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Gönderiliyor..."); aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT * FROM airdrops WHERE id=?", (aid,)).fetchone()
    if not row: await q.edit_message_text("❌ Airdrop bulunamadı.", reply_markup=BACK_ADMIN); return
    footer = get_setting("news_footer","🔔 @KriptoDropTR")
    msg    = "🚨 *YENİ AİRDROP!* 🚨\n━━━━━━━━━━━━━━━━━━━━\n\n" + fmt(row) + f"\n\n{footer}"
    kb     = [[InlineKeyboardButton("🚀 Hemen Katıl!", url=row["link"])]] if row["link"] else []
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
        total   = conn.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
        active  = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        passive = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=0").fetchone()[0]
        pinned  = conn.execute("SELECT COUNT(*) FROM airdrops WHERE pinned=1").fetchone()[0]
        bcast   = conn.execute("SELECT SUM(broadcast) FROM airdrops").fetchone()[0] or 0
        news_n  = conn.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
        auto_n  = conn.execute("SELECT COUNT(*) FROM news_log WHERE auto=1").fetchone()[0]
        ann_n   = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        user_n  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        save_n  = conn.execute("SELECT COUNT(*) FROM user_saves").fetchone()[0]
        cats    = conn.execute("SELECT category,COUNT(*) n FROM airdrops GROUP BY category ORDER BY n DESC").fetchall()
        month_a = conn.execute("SELECT COUNT(*) FROM airdrops WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now')").fetchone()[0]
        month_n = conn.execute("SELECT COUNT(*) FROM news_log WHERE strftime('%Y-%m',sent_at)=strftime('%Y-%m','now')").fetchone()[0]
        top_b   = conn.execute("SELECT name,broadcast FROM airdrops WHERE broadcast>0 ORDER BY broadcast DESC LIMIT 1").fetchone()
        last_d  = conn.execute("SELECT name,created_at FROM airdrops ORDER BY id DESC LIMIT 1").fetchone()
        top_s   = conn.execute("SELECT first_name,airdrop_saves FROM users ORDER BY airdrop_saves DESC LIMIT 1").fetchone()
        # Haber stil dağılımı
        styles  = conn.execute("SELECT style,COUNT(*) n FROM news_log GROUP BY style ORDER BY n DESC").fetchall()
    cat_lines   = "\n".join([f"  {r['category'] or 'Diğer'}: *{r['n']}*" for r in cats]) or "  Henüz yok"
    style_lines = "\n".join([f"  {NEWS_STYLES.get(r['style'] or 'haber',NEWS_STYLES['haber'])['emoji']} {r['style'] or 'haber'}: *{r['n']}*" for r in styles]) or "  Henüz yok"
    last_a_txt  = f"{last_d['name']} _({last_d['created_at'][:10]})_" if last_d else "Yok"
    top_b_txt   = f"{top_b['name']} ({top_b['broadcast']}x)" if top_b else "Yok"
    top_s_txt   = f"{top_s['first_name']} ({top_s['airdrop_saves']} kayıt)" if top_s and top_s['airdrop_saves']>0 else "Henüz yok"
    text = (
        f"📊 *KriptoDropTR İstatistikleri*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪂 *Airdroplar*\n"
        f"  Toplam: *{total}* | Aktif: *{active}* | Pasif: *{passive}*\n"
        f"  📌 Sabitlenmiş: *{pinned}* | 📢 Duyuru: *{bcast}* kez\n"
        f"  🏆 En Çok Duyurulan: {top_b_txt}\n"
        f"  🆕 Son Eklenen: {last_a_txt}\n\n"
        f"📰 *Haberler*\n"
        f"  Toplam: *{news_n}* (Oto: *{auto_n}*, Manuel: *{news_n-auto_n}*)\n"
        f"  Stil Dağılımı:\n{style_lines}\n\n"
        f"👤 *Kullanıcılar*\n"
        f"  Toplam: *{user_n}* | Toplam Kayıt: *{save_n}*\n"
        f"  🏅 En Aktif: {top_s_txt}\n\n"
        f"📅 *Bu Ay*: Airdrop: *{month_a}* | Haber: *{month_n}* | Duyuru: *{ann_n}*\n\n"
        f"🏷 *Kategori Dağılımı*\n{cat_lines}\n\n"
        f"🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_"
    )
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def users_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new_wk = conn.execute("SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now','-7 days')").fetchone()[0]
        top    = conn.execute("SELECT first_name,username,airdrop_saves FROM users ORDER BY airdrop_saves DESC LIMIT 10").fetchall()
    lines = []
    for i,u in enumerate(top,1):
        uname = f"@{u['username']}" if u['username'] else u['first_name'] or "Anonim"
        lines.append(f"{i}. {uname} — 💾 {u['airdrop_saves']}")
    text = (f"👤 *Kullanıcı Paneli*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Toplam: *{total}* | 🆕 Son 7 gün: *{new_wk}*\n\n"
            f"🏅 *En Aktif 10 (Kayıt Sayısı):*\n" + ("\n".join(lines) or "Henüz yok.") +
            f"\n\n🕐 _{datetime.now().strftime('%d.%m.%Y %H:%M')}_")
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
        rows = conn.execute("SELECT topic,style,sent_at FROM news_log ORDER BY id DESC LIMIT 5").fetchall()
    if not rows: await update.message.reply_text("📭 Henüz haber gönderilmemiş."); return
    text = "📰 *Son Haberler — KriptoDropTR*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1):
        emoji = NEWS_STYLES.get(r["style"] or "haber", NEWS_STYLES["haber"])["emoji"]
        text += f"{i}. {emoji} {r['topic']} _{r['sent_at'][:10]}_\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ── SCHEDULER ─────────────────────────────────────────────────────────────────
_last_auto_news_run = ""
_last_weekly_run    = ""

async def auto_news_scheduler(context: ContextTypes.DEFAULT_TYPE):
    global _last_auto_news_run
    if get_setting("auto_news_enabled") != "1": return
    now      = datetime.now()
    target_h = int(get_setting("auto_news_hour","10"))
    target_m = int(get_setting("auto_news_minute","0"))
    run_key  = now.strftime(f"%Y-%m-%d {target_h:02d}:{target_m:02d}")
    if now.hour != target_h or now.minute != target_m or _last_auto_news_run == run_key: return
    _last_auto_news_run = run_key

    topics    = [t.strip() for t in get_setting("auto_news_topic","Bitcoin").split(",") if t.strip()]
    topic     = topics[now.day % len(topics)] if topics else "Bitcoin"
    style_key = get_setting("auto_news_style","haber")
    style     = NEWS_STYLES.get(style_key, NEWS_STYLES["haber"])
    footer    = get_setting("news_footer","🔔 @KriptoDropTR")
    today     = now.strftime("%d %B %Y")
    logger.info(f"Oto-haber: {topic} [{style_key}]")

    data_block, log_summary = await _build_news_context(topic)
    logger.info(f"Oto-haber verisi: {log_summary}")
    prompt_text = (
        f"Konu: '{topic}'\n"
        f"Tarih: {today}\n\n"
        f"Sistem bloğundaki GERÇEK verileri ve güncel haber başlıklarını kullanarak içerik oluştur."
    )
    content = await call_grok_with_data(style["system"], data_block, prompt_text, 1000)
    if content.startswith("❌") or content.startswith("⏱"):
        logger.error(f"Oto-haber hatası: {content}"); return

    msg = f"{content}\n\n━━━━━━━━━━━━━━━━━━━━\n{footer}"
    if len(msg) > 4096: msg = msg[:4086] + "...\n\n" + footer
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn: conn.execute("INSERT INTO news_log (topic,style,content,auto) VALUES(?,?,?,1)", (topic,style_key,content))
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
            footer = get_setting("news_footer","🔔 @KriptoDropTR")
            msg = (f"⏰ *HATIRLATMA — Airdrop Bitiyor!*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"*{row['name']}* — son *{days_left}* gün!\n"
                   f"💰 {row['reward'] or 'Belirtilmedi'} | ⏰ {row['deadline']}\n\n{footer}")
            kb = [[InlineKeyboardButton("🚀 Hemen Katıl!", url=row["link"])]] if row["link"] else []
            try:
                await context.bot.send_message(GROUP_ID, msg, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
                with db() as conn: conn.execute("UPDATE airdrops SET deadline_warned=1 WHERE id=?", (row["id"],))
                logger.info(f"Deadline uyarısı: {row['name']} ({days_left}g)")
            except Exception as e:
                logger.error(f"Deadline uyarısı hatası: {e}")

async def job_weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    global _last_weekly_run
    if get_setting("weekly_summary_enabled") != "1": return
    now        = datetime.now()
    target_day = int(get_setting("weekly_summary_day","1"))
    target_h   = int(get_setting("weekly_summary_hour","9"))
    week_key   = now.strftime(f"%Y-W%W-{target_day}")
    if now.weekday() != target_day or now.hour != target_h or _last_weekly_run == week_key: return
    _last_weekly_run = week_key
    logger.info("Haftalık özet başlatılıyor...")
    with db() as conn:
        week_drops = conn.execute("SELECT name FROM airdrops WHERE created_at>=datetime('now','-7 days') AND active=1").fetchall()
    drops_text = ", ".join([r["name"] for r in week_drops]) or "Bu hafta yeni airdrop eklenmedi"
    footer  = get_setting("news_footer","🔔 @KriptoDropTR")
    style   = NEWS_STYLES["haftalik"]
    today   = now.strftime("%d %B %Y")
    content = await call_grok(style["system"], f"Tarih: {today}. Bu haftanın kripto özetini yaz. Bu hafta eklenen airdroplar: {drops_text}", 1000)
    if content.startswith("❌") or content.startswith("⏱"):
        logger.error(f"Haftalık özet hatası: {content}"); return
    msg = f"{content}\n\n━━━━━━━━━━━━━━━━━━━━\n{footer}"
    if len(msg) > 4096: msg = msg[:4086] + "...\n\n" + footer
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn: conn.execute("INSERT INTO news_log (topic,style,content,auto) VALUES(?,?,?,1)", ("Haftalık Özet","haftalik",content))
        logger.info("Haftalık özet gönderildi.")
    except Exception as e:
        logger.error(f"Haftalık özet hatası: {e}")

def schedule_jobs(app: Application):
    from datetime import time as dtime
    jq = app.job_queue
    if jq is None:
        logger.error("JobQueue bulunamadı! requirements.txt'de 'python-telegram-bot[job-queue]' olmalı.")
        return
    jq.run_daily(job_deadline_check, time=dtime(8, 0))
    jq.run_repeating(auto_news_scheduler, interval=60, first=10)
    jq.run_repeating(job_weekly_summary,  interval=3600, first=30)
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

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🏓 Pong! Bot aktif ve çalışıyor. Kod güncel.")

async def unknown_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private": return
    user = update.effective_user
    register_user(user)
    
    if is_admin(user.id):
        msg = update.effective_message
        text = msg.text or msg.caption or ""
        logger.info(f"Admin DM mesajı alındı ({len(text)} karakter): {text[:80]}...")
        
        try:
            # ── 1) Kanaldan forward edilen mesajları DOĞRUDAN airdrop olarak dene ──
            is_forwarded = bool(
                getattr(msg, 'forward_date', None) or getattr(msg, 'forward_origin', None)
            )
            
            if is_forwarded and text:
                logger.info(f"Forward mesaj algılandı, airdrop olarak parse ediliyor...")
                success, result = parse_and_save_airdrop(msg)
                
                # Markdown parse hatalarını önlemek için MarkdownV2 veya parse_mode olmadan gönderelim
                if success:
                    await msg.reply_text(
                        f"✅ Airdrop Listeye Eklendi!\n\n"
                        f"📛 {result}\n\n"
                        f"💡 Gruba duyurmak için Ana Menü → Airdrop Yönet → Gruba Duyur",
                        reply_markup=BACK_ADMIN)
                    return
                else:
                    await msg.reply_text(
                        f"⚠️ Otomatik parse başarısız:\n{result}\n\n"
                        f"💡 Manuel eklemek için Ana Menü → Airdrop Ekle",
                        reply_markup=BACK_ADMIN)
                    return
            
            # ── 2) Normal DM metin: geniş anahtar kelime kontrolü ──
            airdrop_keywords = [
                "ödül", "airdrop", "reward", "bonus", "token", "kaydol",
                "kampanya", "görev", "quest", "claim", "earn", "free",
                "kazanma fırsatı", "dağıtım", "puanı", "hemen katıl"
            ]
            text_lower = text.lower()
            if any(kw in text_lower for kw in airdrop_keywords):
                logger.info(f"Airdrop anahtar kelimesi bulundu, parse deneniyor...")
                success, result = parse_and_save_airdrop(msg)
                if success:
                    await msg.reply_text(
                        f"✅ Airdrop Listeye Eklendi!\n\n"
                        f"📛 {result}\n\n"
                        f"💡 Gruba duyurmak için Ana Menü → Airdrop Yönet → Gruba Duyur",
                        reply_markup=BACK_ADMIN)
                    return
                else:
                    await msg.reply_text(
                        f"⚠️ Otomatik parse başarısız:\n{result}\n\n"
                        f"💡 Manuel eklemek için Ana Menü → Airdrop Ekle",
                        reply_markup=BACK_ADMIN)
                    return
            
            await msg.reply_text("🤖 /start yazarak menüyü aç.", reply_markup=BACK_ADMIN)
            
        except Exception as e:
            logger.error(f"unknown_private hatası: {e}")
            await msg.reply_text(f"❌ Bir hata oluştu:\n{str(e)}")
    else: 
        await update.effective_message.reply_text("👋 /start yazarak menüyü aç.", reply_markup=BACK_USER)

# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; d = q.data; uid = q.from_user.id

    if d == "back_admin" and is_admin(uid): await back_admin(update, context); return
    if d == "back_user":                    await back_user(update, context);  return
    if d.startswith("save_airdrop_"):       await cb_save_airdrop(update, context); return

    if not is_admin(uid):
        u_routes = {"u_list":u_list,"u_pinned":u_pinned,"u_category":u_category,
                    "u_recent":u_recent,"u_saved":u_saved,"u_help":u_help}
        if d in u_routes: await u_routes[d](update, context); return
        if d.startswith("uc_"): await u_filter_cat(update, context); return
        await q.answer("⛔ Yetki yok.", show_alert=True); return

    admin_routes = {
        "manage_airdrops": manage_airdrops, "mng_list": mng_list,
        "stats": stats_handler,             "users_panel": users_panel,
        "group_info": group_info_handler,   "news_history": news_history_handler,
        "ann_history": ann_history_handler,
        "news_do_send": news_do_send,       "ann_send": ann_send,
        "ann_redo": ann_redo,               "settings": settings_panel,
        "news_retry": news_retry,
    }
    if d in admin_routes: await admin_routes[d](update, context); return

    if d.startswith("news_detail_"):  await news_detail_handler(update, context); return
    if d.startswith("news_resend_"):  await news_resend_handler(update, context); return

    if d in ("stg_toggle_auto_news","stg_toggle_deadline","stg_toggle_weekly"):
        await settings_toggle(update, context); return
    if d == "stg_set_news_style":       await settings_news_style_picker(update, context); return
    if d.startswith("stg_nstyle_"):     await settings_news_style_set(update, context);    return
    if d == "stg_set_weekly_day":       await settings_day_picker(update, context);        return
    if d.startswith("stg_day_"):        await settings_day_set(update, context);           return
    if d in ("stg_set_news_hour","stg_set_news_topic","stg_set_deadline_days",
             "stg_set_weekly_hour","stg_set_grok_model","stg_set_news_footer"):
        await settings_input_prompt(update, context); return

    if d in ("mng_delete","mng_toggle","mng_pin","mng_broadcast"): await mng_action(update, context); return
    if d.startswith("do_delete_"):    await do_delete(update, context);   return
    if d.startswith("do_toggle_"):    await do_toggle(update, context);   return
    if d.startswith("do_pin_"):       await do_pin(update, context);      return
    if d.startswith("do_broadcast_"): await do_broadcast(update, context);return

    u_routes = {"u_list":u_list,"u_pinned":u_pinned,"u_category":u_category,
                "u_recent":u_recent,"u_saved":u_saved,"u_help":u_help}
    if d in u_routes: await u_routes[d](update, context); return
    if d.startswith("uc_"): await u_filter_cat(update, context); return
    await q.answer()

# ── AUTO AIRDROP FROM CHANNEL ──────────────────────────────────────────────────
def parse_and_save_airdrop(msg):
    text = msg.text or msg.caption
    if not text or len(text.strip()) < 10:
        return False, "Metin bulunamadı veya çok kısa."
    
    # ── BAŞLIK (ilk satır) ──
    title_match = re.search(r'^(.*?)[\n]', text)
    title = title_match.group(1).strip() if title_match else text[:60].strip()
    # Emojiyi temizle ama tamamen boş kalmasın
    title_clean = re.sub(r'^[\s🚀🎉🔥🎁💰🪂⭐✨🔔📢]+', '', title).strip()
    if title_clean:
        title = title_clean if len(title_clean) > 3 else title
    if not title:
        title = "Yeni Airdrop"
    # Başlık çok uzunsa kısalt
    if len(title) > 80:
        title = title[:77] + "..."
    
    # ── PROJE ADI ──
    project_match = re.search(r'(?:🚀|🎉|🔥)\s*(.*?)(?:\s+(?:yeni|airdrop|bonus|kampanya))', text, re.IGNORECASE)
    if not project_match:
        project_match = re.search(r'(?:Proje|Project|Token)[:\s]+([A-Za-z0-9]+(?:\s+[A-Za-z0-9]+){0,2})', text, re.IGNORECASE)
    if not project_match:
        # İlk satırdaki büyük harfle başlayan kelimeleri al
        project_match = re.search(r'([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){0,2})', title)
    project = project_match.group(1).strip() if project_match else title[:30]
    
    # ── ÖDÜL ──
    reward = "Belirtilmedi"
    reward_patterns = [
        r'[Öö]dül\s*(?:miktarı)?[:\s]+(.+?)(?:\n|$)',
        r'[Rr]eward[:\s]+(.+?)(?:\n|$)',
        r'[Bb]onus[:\s]+(.+?)(?:\n|$)',
        r'(\d+[\.,]?\d*\s*(?:TL|USD|\$|USDT|token|coin|puan))',
    ]
    for pattern in reward_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            reward = m.group(1).strip()
            break
    
    # ── SON TARİH ──
    deadline = "Belirsiz"
    deadline_patterns = [
        r'[Kk]ampanya\s*[Dd]önemi[:\s]+(.+?)(?:\n|$)',
        r'[Ss]on\s*(?:tarih|katılım)[:\s]+(.+?)(?:\n|$)',
        r'[Dd]eadline[:\s]+(.+?)(?:\n|$)',
        r'[Bb]itiş[:\s]+(.+?)(?:\n|$)',
        r'(\d{1,2}[./]\d{1,2}[./]\d{2,4})',  # Tarih formatı
    ]
    for pattern in deadline_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            deadline = m.group(1).strip()
            break
    
    # ── LİNK ──
    link = "yok"
    
    # forward_origin kontrolü (yeni Telegram Bot API ve PTB v21.5+)
    if hasattr(msg, 'forward_origin') and msg.forward_origin:
        origin = msg.forward_origin
        if hasattr(origin, 'chat') and origin.chat and origin.chat.username:
            mid = getattr(origin, 'message_id', None)
            if mid:
                link = f"https://t.me/{origin.chat.username}/{mid}"
            else:
                link = f"https://t.me/{origin.chat.username}"
    # Metinden link çıkar
    if link == "yok":
        ents = msg.entities or msg.caption_entities or []
        for ent in ents:
            if ent.type == MessageEntityType.TEXT_LINK and ent.url:
                link = ent.url
                break
            elif ent.type == MessageEntityType.URL:
                try:
                    link = msg.parse_entity(ent) or text[ent.offset:ent.offset+ent.length]
                except:
                    link = text[ent.offset:ent.offset+ent.length]
                break
    
    # ── AÇIKLAMA ──
    lines = text.split('\n')
    desc_lines = []
    stop_words = ['YAPMAN GEREKENLER', 'Hemen Kaydol', 'Görev zorluğu', 
                  'Ödül miktarı', 'ödül miktarı', 'Kampanya Dönemi',
                  'Skor:', '---', '===', 'duyuru kanalını']
    for line in lines[1:]:  # Başlığı atla
        line_clean = line.strip()
        if not line_clean or set(line_clean).issubset(set('-=_. »›')):
            continue
        if any(sw in line for sw in stop_words):
            break
        desc_lines.append(line_clean)
    
    desc = " ".join(desc_lines)
    if len(desc) > 200:
        desc = desc[:197] + "..."
    if not desc:
        desc = title
    
    # ── KATEGORİ TAHMİNİ ──
    category = "🌐 Diğer"
    cat_keywords = {
        "🪙 DeFi":          ["defi", "swap", "liquidity", "yield", "farm"],
        "🎮 GameFi":        ["game", "gamefi", "play", "nft game", "oyun"],
        "🖼 NFT":           ["nft", "mint", "opensea", "collectible"],
        "🔗 Layer1/Layer2": ["layer", "l1", "l2", "chain", "mainnet", "testnet"],
        "📱 Web3":          ["web3", "dapp", "wallet", "metamask"],
    }
    text_lower = text.lower()
    for cat, keywords in cat_keywords.items():
        if any(kw in text_lower for kw in keywords):
            category = cat
            break
    
    # ── VERITABANINA KAYDET ──
    try:
        with db() as conn:
            # Aynı isimde airdrop var mı kontrol et (duplicate önleme)
            existing = conn.execute("SELECT id FROM airdrops WHERE name=? AND active=1", (title,)).fetchone()
            if existing:
                return False, f"'{title}' adında zaten aktif bir airdrop var (ID: {existing['id']})."
            
            conn.execute(
                "INSERT INTO airdrops (name, project, description, reward, link, deadline, category) VALUES(?,?,?,?,?,?,?)",
                (title, project, desc, reward, link, deadline, category)
            )
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        logger.info(f"✅ Yeni airdrop eklendi: {title} [ID:{new_id}] | Ödül: {reward} | Link: {link}")
        return True, title
    except Exception as e:
        logger.error(f"Airdrop eklenirken hata: {e}")
        return False, f"Veritabanı hatası: {e}"

async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    
    if CHANNEL_ID:
        cid_str = str(CHANNEL_ID)
        # IDBot bazen -100 vermez, Telegram API ise her zaman kanal ID'sini -100 ile başlatır.
        if not cid_str.startswith("-100"):
            cid_str = f"-100{cid_str}"
        
        if str(msg.chat.id) != cid_str:
            return
            
    success, result = parse_and_save_airdrop(msg)
    
    # Başarılı olduğunda Admin'e otomatik bilgi ver
    if success:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ *Kanaldan Otomatik Airdrop Eklendi!*\n\n📛 {result}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.debug(f"Admin'e bildirim gönderilemedi: {e}")

async def group_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grupta admin'in forward ettiği mesajları airdrop olarak algıla."""
    msg = update.effective_message
    if not msg or not update.effective_user:
        return
    
    # Sadece admin'in mesajlarını işle
    if not is_admin(update.effective_user.id):
        return
    
    # Forward mesaj mı kontrol et
    is_forwarded = bool(
        getattr(msg, 'forward_date', None) or getattr(msg, 'forward_origin', None)
    )
    if not is_forwarded:
        return
    
    text = msg.text or msg.caption or ""
    if not text or len(text.strip()) < 10:
        return
    
    logger.info(f"Grupta admin forward mesajı algılandı: {text[:60]}...")
    
    success, result = parse_and_save_airdrop(msg)
    if success:
        # Gruba kısa onay mesajı gönder
        try:
            confirm = await msg.reply_text(
                f"✅ *Airdrop otomatik eklendi:* {result}",
                parse_mode=ParseMode.MARKDOWN)
            # 10 saniye sonra onay mesajını sil (grubu kirletmesin)
            _chat_id = confirm.chat_id
            _msg_id = confirm.message_id
            async def _del_confirm(ctx):
                try: await ctx.bot.delete_message(_chat_id, _msg_id)
                except: pass
            context.job_queue.run_once(_del_confirm, when=10, name=f"del_confirm_{_msg_id}")
        except Exception as e:
            logger.debug(f"Onay mesajı gönderilemedi: {e}")
        logger.info(f"✅ Gruptan airdrop eklendi: {result}")
    else:
        logger.info(f"Grup forward parse başarısız: {result}")

# ── YENİ KOMUTLAR ─────────────────────────────────────────────────────────────
async def cmd_iletisim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📩 *İletişim ve Destek*\n\n"
        "Reklam, iş birliği veya herhangi bir sorunuz için yöneticiyle iletişime geçebilirsiniz:\n"
        "👉 @kriptodropadmin"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_kaydet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg.reply_to_message:
        target = msg.reply_to_message
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"📌 *Kaydedilen Gönderi:*\n\n{target.text or target.caption or 'İçerik bulunamadı.'}\n\n🔗 Kanal/Grup Kaynağı: {target.link or 'Bilinmiyor'}",
                parse_mode=ParseMode.MARKDOWN
            )
            await msg.reply_text("✅ Gönderi özel mesaj kutuna kaydedildi!")
        except Exception:
            await msg.reply_text("❌ Sana özel mesaj atamıyorum. Lütfen önce bana özelden /start yaz, ardından tekrar dene.")
    else:
        await msg.reply_text("💡 Bir airdrop veya haber mesajını yanıtlayarak (reply) /kaydet yazarsan, onu senin özel mesaj kutuna (DM) kaydederim.")

# ── post_init ─────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    # Özel (DM) menüsü
    await app.bot.set_my_commands([
        BotCommand("start",    "Bot menüsünü aç"),
        BotCommand("airdrops", "Aktif airdropları listele"),
        BotCommand("haberler", "Son haberlere bak"),
        BotCommand("kaydet",   "İçeriği DM'ye kaydet"),
        BotCommand("iletisim", "Yönetici iletişimi"),
        BotCommand("iptal",    "İşlemi iptal et"),
    ], scope=BotCommandScopeAllPrivateChats())
    
    # Grup menüsü
    await app.bot.set_my_commands([
        BotCommand("airdrops", "🪂 Aktif Airdroplar"),
        BotCommand("haberler", "📰 Son Kripto Haberleri"),
        BotCommand("kaydet",   "💾 İçeriği Özelime Kaydet"),
        BotCommand("iletisim", "📩 İletişim ve Destek"),
    ], scope=BotCommandScopeAllGroupChats())

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    init_db()

    # ✅ Job-queue aktif build
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

    # ✅ Gelişmiş haber conversation — 3 adım: Konu → Stil → Önizle/Gönder
    news_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(send_news_entry, pattern="^send_news$")],
        states={
            NEWS_TOPIC: [
                CallbackQueryHandler(cb_quick_news,   pattern=r"^qnews_.+"),
                CallbackQueryHandler(cb_news_manual,  pattern="^news_manual$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, news_topic_input),
            ],
            NEWS_STYLE: [
                CallbackQueryHandler(cb_news_style, pattern=r"^nstyle_.+"),
            ],
            NEWS_PREVIEW: [
                CallbackQueryHandler(news_do_send,    pattern="^news_do_send$"),
                CallbackQueryHandler(news_retry,      pattern="^news_retry$"),
                CallbackQueryHandler(cb_quick_news,   pattern=r"^qnews_.+"),
                CallbackQueryHandler(cb_news_manual,  pattern="^news_manual$"),
                CallbackQueryHandler(send_news_entry, pattern="^send_news$"),
            ],
        },
        fallbacks=[
            CommandHandler("iptal", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
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

    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(settings_input_prompt,
            pattern=r"^stg_set_(news_hour|news_topic|deadline_days|weekly_hour|grok_model|news_footer)$")],
        states={SETTINGS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_save_input)]},
        fallbacks=[CommandHandler("iptal", cancel), CallbackQueryHandler(settings_panel, pattern="^settings$")],
        allow_reentry=True, conversation_timeout=120,
    )

    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("airdrops", cmd_airdrops))
    app.add_handler(CommandHandler("haberler", cmd_haberler))
    app.add_handler(CommandHandler("kaydet",   cmd_kaydet))
    app.add_handler(CommandHandler("iletisim", cmd_iletisim))
    app.add_handler(CommandHandler("iptal",    cancel))
    app.add_handler(CommandHandler("ping",     cmd_ping))
    app.add_handler(airdrop_conv)
    app.add_handler(news_conv)
    app.add_handler(announce_conv)
    app.add_handler(settings_conv)
    
    # Kanal dinleyicisi
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post_handler))
    
    # Grupta admin forward mesajlarını yakala (airdrop otomatik ekleme)
    app.add_handler(MessageHandler(
        (filters.ChatType.GROUPS & ~filters.COMMAND & filters.FORWARDED),
        group_forward_handler
    ))
    
    app.add_handler(CallbackQueryHandler(cb_router))
    # Özel mesajdaki TÜM mesaj tiplerini yakala (metin, fotoğraf, forward vs.)
    app.add_handler(MessageHandler(
        (~filters.COMMAND & filters.ChatType.PRIVATE),
        unknown_private
    ))

    schedule_jobs(app)
    logger.info("🚀 KriptoDropTR Bot v5.0 başlatıldı!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
