#!/usr/bin/env python3
"""KriptoDropTR Telegram Botu v2.0"""

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

(AIRDROP_NAME, AIRDROP_PROJECT, AIRDROP_DESC, AIRDROP_REWARD,
 AIRDROP_LINK, AIRDROP_DEADLINE, AIRDROP_CATEGORY,
 NEWS_TOPIC, NEWS_PREVIEW,
 ANNOUNCE_TEXT, ANNOUNCE_CONFIRM,
 PRICE_COIN) = range(12)

CATEGORIES = ["🪙 DeFi","🎮 GameFi","🖼 NFT","🔗 Layer1/Layer2","📱 Web3","🌐 Diğer"]
BACK_ADMIN = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
BACK_USER  = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]])

# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect("kriptodrop.db") as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS airdrops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, project TEXT, description TEXT,
                reward TEXT, link TEXT, deadline TEXT, category TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                pinned INTEGER DEFAULT 0, broadcast INTEGER DEFAULT 0
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
        """)

def db():
    conn = sqlite3.connect("kriptodrop.db")
    conn.row_factory = sqlite3.Row
    return conn

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
    if admin: lines.append("📊 " + ("🟢 Aktif" if row["active"] else "🔴 Pasif"))
    return "\n".join(lines)

# ── GROK AI ───────────────────────────────────────────────────────────────────
GROK_URL = "https://api.x.ai/v1/chat/completions"

NEWS_SYS = """Sen KriptoDropTR adlı Türkçe kripto Telegram grubu için haber asistanısın.
Verilen konuda güncel, bilgilendirici, akıcı Türkçe kripto haberi/analizi yaz.
Emoji kullan, paragraf formatı kullan, 250-400 kelime olsun.
Sonuna '💡 Önemli Not:' ile kısa bir yorum ekle.
Format: 📰 [BAŞLIK] ... içerik ... 💡 Önemli Not: ..."""

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

async def call_grok(system: str, prompt: str, tokens: int = 800) -> str:
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(GROK_URL,
                headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
                json={"model": "grok-3-latest",
                      "messages": [{"role":"system","content":system},
                                   {"role":"user","content":prompt}],
                      "max_tokens": tokens, "temperature": 0.7})
        if r.status_code == 401:
            return "❌ API Anahtarı hatalı! GROQ_API_KEY değerini kontrol et."
        if r.status_code == 429:
            return "❌ API limit aşıldı. Birkaç dakika sonra tekrar dene."
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        return "⏱ Grok API zaman aşımı. Tekrar dene."
    except httpx.HTTPStatusError as e:
        return f"❌ API Hatası {e.response.status_code}: {e.response.text[:150]}"
    except Exception as e:
        logger.error(f"Grok hata: {e}", exc_info=True)
        return f"❌ Hata: {type(e).__name__}: {e}"

# ── COINGECKO FİYAT ───────────────────────────────────────────────────────────
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
                params={"localization":"false","tickers":"false",
                        "community_data":"false","developer_data":"false"})
        if r.status_code == 404:
            return f"❓ '{coin}' bulunamadı. BTC, ETH, SOL gibi sembol dene."
        r.raise_for_status()
        d  = r.json()
        md = d["market_data"]
        p   = md["current_price"].get("usd",0)
        h1  = md["price_change_percentage_1h_in_currency"].get("usd",0) or 0
        h24 = md["price_change_percentage_24h"] or 0
        d7  = md["price_change_percentage_7d"] or 0
        cap = md["market_cap"].get("usd",0)
        vol = md["total_volume"].get("usd",0)
        ath = md["ath"].get("usd",0)
        rank= d.get("market_cap_rank","?")
        def arrow(v): return "🟢 +" if v>=0 else "🔴 "
        def fn(n):
            if n>=1e9: return f"${n/1e9:.2f}B"
            if n>=1e6: return f"${n/1e6:.2f}M"
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

# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "👋 Airdrop ve haberler için bana özel mesaj yaz!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📩 Bota Mesaj At", url=f"https://t.me/{context.bot.username}")
            ]]))
        return
    context.user_data.clear()
    if is_admin(update.effective_user.id):
        await show_admin(update, context)
    else:
        await show_user(update, context)

async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        news_n = conn.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
    text = (f"🛠 *KriptoDropTR — Admin Paneli*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪂 Toplam: *{total}* (Aktif: *{active}*)\n"
            f"📰 Haber: *{news_n}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n👇 İşlem seç:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Airdrop Ekle",        callback_data="add_airdrop"),
         InlineKeyboardButton("🗂 Airdrop Yönet",       callback_data="manage_airdrops")],
        [InlineKeyboardButton("📰 Haber Oluştur (AI)",  callback_data="send_news"),
         InlineKeyboardButton("🔍 Coin Analizi (AI)",   callback_data="coin_analysis")],
        [InlineKeyboardButton("📢 Duyuru Yap",          callback_data="announce"),
         InlineKeyboardButton("💰 Fiyat Sorgula",       callback_data="price_menu")],
        [InlineKeyboardButton("📊 İstatistikler",       callback_data="stats"),
         InlineKeyboardButton("👥 Grup Bilgisi",        callback_data="group_info")],
        [InlineKeyboardButton("📜 Haber Geçmişi",       callback_data="news_history"),
         InlineKeyboardButton("📣 Duyuru Geçmişi",      callback_data="ann_history")],
    ])
    try: await update.effective_message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except: await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def show_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        active = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
    text = (f"👋 *KriptoDropTR'ye Hoş Geldin!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪂 Şu an *{active}* aktif airdrop var!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n👇 Ne yapmak istiyorsun?")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪂 Aktif Airdroplar",  callback_data="u_list"),
         InlineKeyboardButton("📌 Öne Çıkanlar",      callback_data="u_pinned")],
        [InlineKeyboardButton("🔍 Kategoriye Göre",   callback_data="u_category"),
         InlineKeyboardButton("🆕 Son Eklenenler",    callback_data="u_recent")],
        [InlineKeyboardButton("💰 Coin Fiyatı",       callback_data="price_menu"),
         InlineKeyboardButton("❓ Yardım",             callback_data="u_help")],
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
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🤖 AI ile Oluştur", callback_data="ai_desc"),
        InlineKeyboardButton("✏️ Manuel Gir",     callback_data="manual_desc"),
    ]])
    await update.message.reply_text(
        f"✅ Proje: *{context.user_data['project']}*\n\n📝 *Açıklama* için yöntem:",
        reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return AIRDROP_DESC

async def cb_ai_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    wait = await q.message.reply_text("🤖 AI açıklama oluşturuyor...")
    desc = await call_grok(
        "Kripto airdrop için 2-3 cümle Türkçe açıklama yaz. Emoji kullan, kullanıcıyı katılmaya teşvik et.",
        f"Proje: {context.user_data.get('project','?')}, Airdrop: {context.user_data.get('name','?')}")
    await wait.delete()
    if desc.startswith("❌") or desc.startswith("⏱"):
        await q.message.reply_text(f"{desc}\n\nManuel açıklama girin:")
        return AIRDROP_DESC
    context.user_data["desc"] = desc
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Kullan", callback_data="use_ai_desc"),
        InlineKeyboardButton("✏️ Değiştir", callback_data="manual_desc"),
    ]])
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
    await update.message.reply_text("⏰ *Son katılım tarihi:*\n_(Örn: 31.12.2025 veya 'belirsiz')_", parse_mode=ParseMode.MARKDOWN)
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
             context.user_data["reward"], context.user_data["link"],
             context.user_data["deadline"], context.user_data["category"]))
        nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    txt = (f"✅ *Airdrop Eklendi!* [ID: {nid}]\n━━━━━━━━━━━━━━━━━━━━\n"
           f"📛 *{context.user_data['name']}*\n"
           f"🏢 {context.user_data['project']} | {context.user_data['category']}\n"
           f"💰 {context.user_data['reward']}\n⏰ {context.user_data.get('deadline') or 'Belirsiz'}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Gruba Duyur", callback_data=f"do_broadcast_{nid}"),
         InlineKeyboardButton("📌 Sabitle",     callback_data=f"do_pin_{nid}")],
        [InlineKeyboardButton("🏠 Ana Menü",    callback_data="back_admin")]
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
    context.user_data.pop("news_content", None)
    context.user_data.pop("news_topic", None)
    rows = [QUICK_TOPICS[i:i+2] for i in range(0, len(QUICK_TOPICS), 2)]
    kb_rows = [[InlineKeyboardButton(n, callback_data=f"qnews_{t}") for n,t in row] for row in rows]
    kb_rows.append([InlineKeyboardButton("✏️ Kendi Konumu Yaz", callback_data="news_manual")])
    kb_rows.append([InlineKeyboardButton("❌ İptal", callback_data="back_admin")])
    await update.effective_message.reply_text(
        "📰 *Haber Oluştur (Grok AI)*\n━━━━━━━━━━━━━━━━━━━━\nKonu seç veya kendin yaz:",
        reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    return NEWS_TOPIC

async def cb_quick_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    topic = q.data[6:]  # remove "qnews_"
    return await _gen_news(update, context, topic, q.message)

async def cb_news_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("✏️ *Haber konusunu yaz:*\n_(Örn: Ethereum ETF onayı)_\n\n❌ /iptal", parse_mode=ParseMode.MARKDOWN)
    return NEWS_TOPIC

async def news_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _gen_news(update, context, update.message.text.strip(), update.message)

async def _gen_news(update, context, topic: str, reply_to):
    wait = await reply_to.reply_text(f"⏳ *{topic}* hakkında haber yazılıyor...")
    content = await call_grok(NEWS_SYS, f"'{topic}' hakkında KriptoDropTR grubu için haber/analiz yaz.", 900)
    await wait.delete()

    if content.startswith("❌") or content.startswith("⏱"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Tekrar Dene", callback_data=f"qnews_{topic}")],
            [InlineKeyboardButton("❌ İptal",        callback_data="back_admin")],
        ])
        await reply_to.reply_text(
            f"*Haber oluşturulamadı:*\n\n{content}\n\n_API anahtarını ve model adını kontrol edin._",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return NEWS_TOPIC

    context.user_data["news_content"] = content
    context.user_data["news_topic"]   = topic
    preview = f"📰 *Önizleme — {topic}*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}"
    if len(preview) > 4000: preview = preview[:3990] + "..."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gruba Gönder",    callback_data="news_do_send"),
         InlineKeyboardButton("🔄 Yeniden Oluştur", callback_data=f"qnews_{topic}")],
        [InlineKeyboardButton("✏️ Farklı Konu",     callback_data="send_news"),
         InlineKeyboardButton("❌ İptal",            callback_data="back_admin")],
    ])
    await reply_to.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return NEWS_PREVIEW

async def news_do_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Gönderiliyor...")
    content = context.user_data.get("news_content","")
    topic   = context.user_data.get("news_topic","Genel")
    if not content:
        await q.edit_message_text("❌ İçerik bulunamadı.", reply_markup=BACK_ADMIN)
        return ConversationHandler.END
    msg = f"📰 *KriptoDropTR — Kripto Haber*\n━━━━━━━━━━━━━━━━━━━━\n\n{content}\n\n🔔 @KriptoDropTR"
    if len(msg) > 4096: msg = msg[:4090] + "..."
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn:
            conn.execute("INSERT INTO news_log (topic,content) VALUES(?,?)", (topic, content))
        await q.edit_message_text(f"✅ Haber gruba gönderildi!\n📌 Konu: {topic}", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await q.edit_message_text(f"❌ Gönderme hatası:\n`{e}`\n\nBotun grupta admin olduğundan emin ol.", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END

# ── COİN ANALİZİ ─────────────────────────────────────────────────────────────
async def coin_analysis_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ BTC",  callback_data="qa_Bitcoin"),
         InlineKeyboardButton("Ξ ETH",  callback_data="qa_Ethereum"),
         InlineKeyboardButton("◎ SOL",  callback_data="qa_Solana")],
        [InlineKeyboardButton("🔵 BNB", callback_data="qa_BNB"),
         InlineKeyboardButton("🔶 AVAX",callback_data="qa_Avalanche"),
         InlineKeyboardButton("🟣 ARB", callback_data="qa_Arbitrum")],
        [InlineKeyboardButton("✏️ Başka Coin", callback_data="qa_custom")],
        [InlineKeyboardButton("❌ İptal", callback_data="back_admin")],
    ])
    await q.edit_message_text("🔍 *Coin Analizi*\nHangi coin?", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_quick_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    coin = q.data[3:]
    wait = await q.message.reply_text(f"🔍 {coin} analizi yapılıyor...")
    result = await call_grok(ANALYZE_SYS, f"'{coin}' için kısa analiz yap.", 600)
    await wait.delete()
    context.user_data["analysis"] = result
    context.user_data["analysis_coin"] = coin
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Gruba Gönder", callback_data=f"send_analysis_{coin}")],
        [InlineKeyboardButton("🏠 Ana Menü",     callback_data="back_admin")],
    ])
    await q.message.reply_text(result, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def send_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    coin = q.data.replace("send_analysis_","")
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
        [InlineKeyboardButton("✅ Gönder",  callback_data="ann_send"),
         InlineKeyboardButton("✏️ Düzenle", callback_data="ann_redo")],
        [InlineKeyboardButton("❌ İptal",   callback_data="back_admin")],
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
    msg = f"📢 *DUYURU*\n━━━━━━━━━━━━━━━━━━━━\n\n{text}\n\n🔔 @KriptoDropTR"
    try:
        await context.bot.send_message(GROUP_ID, msg, parse_mode=ParseMode.MARKDOWN)
        with db() as conn:
            conn.execute("INSERT INTO announcements (text) VALUES(?)", (text,))
        await q.edit_message_text("✅ Duyuru gönderildi!", reply_markup=BACK_ADMIN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)
    context.user_data.clear()
    return ConversationHandler.END

# ── FİYAT CONV ────────────────────────────────────────────────────────────────
async def price_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    pop = [("₿ BTC","btc"),("Ξ ETH","eth"),("◎ SOL","sol"),
           ("⚡ XRP","xrp"),("🔵 BNB","bnb"),("🔴 TON","ton")]
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
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data=f"qp_{coin}"),
        InlineKeyboardButton("💰 Başka Coin", callback_data="price_menu"),
    ],[InlineKeyboardButton("🏠 Ana Menü", callback_data=back)]])
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
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data=f"qp_{coin.lower()}"),
        InlineKeyboardButton("💰 Başka Coin", callback_data="price_menu"),
    ],[InlineKeyboardButton("🏠 Ana Menü", callback_data=back)]])
    await update.message.reply_text(result, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ── AİRDROP YÖNETİM ──────────────────────────────────────────────────────────
async def manage_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Listele",       callback_data="mng_list"),
         InlineKeyboardButton("🗑 Sil",            callback_data="mng_delete")],
        [InlineKeyboardButton("✅ Aktif/Pasif",    callback_data="mng_toggle"),
         InlineKeyboardButton("📌 Sabitle",        callback_data="mng_pin")],
        [InlineKeyboardButton("📢 Gruba Duyur",   callback_data="mng_broadcast")],
        [InlineKeyboardButton("🏠 Ana Menü",      callback_data="back_admin")],
    ])
    await q.edit_message_text("🗂 *Airdrop Yönetimi*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def mng_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops ORDER BY pinned DESC, id DESC LIMIT 20").fetchall()
    if not rows:
        await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_ADMIN); return
    await q.message.reply_text(f"📋 *{len(rows)} Airdrop:*", parse_mode=ParseMode.MARKDOWN)
    for i, row in enumerate(rows, 1):
        await q.message.reply_text(fmt(row, i, admin=True), parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_ADMIN)

async def mng_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    action = q.data
    with db() as conn:
        rows = conn.execute("SELECT id,name,active,pinned FROM airdrops ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_ADMIN); return
    icons = {"mng_delete":"🗑","mng_toggle":"✅","mng_pin":"📌","mng_broadcast":"📢"}
    icon  = icons.get(action,"•")
    kb = []
    for r in rows:
        state = (" 🟢" if r["active"] else " 🔴") if action=="mng_toggle" else (" 📌" if r["pinned"] else "") if action=="mng_pin" else ""
        kb.append([InlineKeyboardButton(f"{icon} [{r['id']}] {r['name']}{state}",
                    callback_data=f"do_{action.replace('mng_','')}_{r['id']}")])
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
            conn.execute("UPDATE airdrops SET active=? WHERE id=?", (new, aid))
    icon = "🟢 Aktif" if new else "🔴 Pasif"
    await q.edit_message_text(f"✅ *{row['name']}* → {icon}", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT name,pinned FROM airdrops WHERE id=?", (aid,)).fetchone()
        if row:
            new = 0 if row["pinned"] else 1
            conn.execute("UPDATE airdrops SET pinned=? WHERE id=?", (new, aid))
    icon = "📌 Sabitlendi" if new else "📋 Sabit Kaldırıldı"
    await q.edit_message_text(f"{icon}: *{row['name']}*", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer("Gönderiliyor...")
    aid = int(q.data.split("_")[-1])
    with db() as conn:
        row = conn.execute("SELECT * FROM airdrops WHERE id=?", (aid,)).fetchone()
    if not row:
        await q.edit_message_text("❌ Airdrop bulunamadı.", reply_markup=BACK_ADMIN); return
    msg = ("🚨 *YENİ AİRDROP!* 🚨\n━━━━━━━━━━━━━━━━━━━━\n\n"
           + fmt(row) + "\n\n🔔 @KriptoDropTR")
    kb = [[InlineKeyboardButton("🚀 Hemen Katıl!", url=row["link"])]] if row["link"] else []
    try:
        await context.bot.send_message(GROUP_ID, msg,
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
            parse_mode=ParseMode.MARKDOWN)
        with db() as conn:
            conn.execute("UPDATE airdrops SET broadcast=broadcast+1 WHERE id=?", (aid,))
        await q.edit_message_text(f"✅ *{row['name']}* gruba duyuruldu!", reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await q.edit_message_text(f"❌ Hata: {e}", reply_markup=BACK_ADMIN)

# ── İSTATİSTİK ────────────────────────────────────────────────────────────────
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        total  = conn.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
        pinned = conn.execute("SELECT COUNT(*) FROM airdrops WHERE pinned=1").fetchone()[0]
        news_n = conn.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
        ann_n  = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        cats   = conn.execute("SELECT category,COUNT(*) n FROM airdrops GROUP BY category ORDER BY n DESC").fetchall()
        last_n = conn.execute("SELECT topic,sent_at FROM news_log ORDER BY id DESC LIMIT 3").fetchall()
    cat_t = "\n".join([f"  • {r['category'] or 'Diğer'}: *{r['n']}*" for r in cats]) or "  Yok"
    news_t = "\n".join([f"  • {r['topic']} _{r['sent_at'][:10]}_" for r in last_n]) if last_n else ""
    text = (f"📊 *Bot İstatistikleri*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🪂 Toplam: *{total}* | Aktif: *{active}* | 📌 *{pinned}*\n"
            f"📰 Haber: *{news_n}* | 📢 Duyuru: *{ann_n}*\n\n"
            f"📁 *Kategoriler:*\n{cat_t}"
            + (f"\n\n📰 *Son Haberler:*\n{news_t}" if news_t else ""))
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
    if not rows:
        await q.edit_message_text("📭 Haber yok.", reply_markup=BACK_ADMIN); return
    text = "📰 *Son 10 Haber:*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1):
        text += f"{i}. {r['topic']}\n   _{r['sent_at'][:16]}_\n\n"
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

async def ann_history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT text,sent_at FROM announcements ORDER BY id DESC LIMIT 5").fetchall()
    if not rows:
        await q.edit_message_text("📭 Duyuru yok.", reply_markup=BACK_ADMIN); return
    text = "📢 *Son 5 Duyuru:*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1):
        prev = r["text"][:80] + "..." if len(r["text"])>80 else r["text"]
        text += f"{i}. {prev}\n   _{r['sent_at'][:16]}_\n\n"
    await q.edit_message_text(text, reply_markup=BACK_ADMIN, parse_mode=ParseMode.MARKDOWN)

# ── KULLANICI PANELİ ──────────────────────────────────────────────────────────
async def u_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY pinned DESC, id DESC LIMIT 10").fetchall()
    if not rows:
        await q.edit_message_text("📭 Aktif airdrop yok. Yakında eklenecek! 🔔", reply_markup=BACK_USER); return
    await q.message.reply_text(f"🪂 *{len(rows)} Aktif Airdrop:*", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1):
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row["link"])]] if row["link"] else []
        await q.message.reply_text(fmt(row,i), reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("🔔 Yeni airdroplar için grubu takip et!", reply_markup=BACK_USER)

async def u_pinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE pinned=1 AND active=1").fetchall()
    if not rows:
        await q.edit_message_text("📭 Sabitlenmiş airdrop yok.", reply_markup=BACK_USER); return
    for i,row in enumerate(rows,1):
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row["link"])]] if row["link"] else []
        await q.message.reply_text(fmt(row,i), reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
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
    if not rows:
        await q.edit_message_text(f"📭 {cat} kategorisinde airdrop yok.", reply_markup=BACK_USER); return
    await q.message.reply_text(f"🏷 *{cat}* — {len(rows)} airdrop:", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1):
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row["link"])]] if row["link"] else []
        await q.message.reply_text(fmt(row,i), reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_USER)

async def u_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY id DESC LIMIT 5").fetchall()
    if not rows:
        await q.edit_message_text("📭 Airdrop yok.", reply_markup=BACK_USER); return
    await q.message.reply_text("🆕 *Son 5 Eklenen:*", parse_mode=ParseMode.MARKDOWN)
    for i,row in enumerate(rows,1):
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row["link"])]] if row["link"] else []
        await q.message.reply_text(fmt(row,i), reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode=ParseMode.MARKDOWN)
    await q.message.reply_text("─────", reply_markup=BACK_USER)

async def u_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "❓ *KriptoDropTR Yardım*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "🪂 *Airdrop nedir?*\nKripto projelerin ücretsiz token dağıtımlarıdır.\n\n"
        "🚀 *Nasıl katılırım?*\n'Katıl' butonuna bas, formu doldur.\n\n"
        "💰 *Fiyat nereden bakılır?*\n'Coin Fiyatı' menüsünden canlı sorgulayabilirsin.\n\n"
        "⚠️ *GÜVENLİK:*\nHiçbir airdrop için *private key veya seed phrase* paylaşma!\n\n"
        "📢 @KriptoDropTR",
        reply_markup=BACK_USER, parse_mode=ParseMode.MARKDOWN)

# ── GRUP KOMUTLARI ────────────────────────────────────────────────────────────
async def cmd_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        rows = conn.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY pinned DESC, id DESC LIMIT 5").fetchall()
    if not rows:
        await update.message.reply_text("📭 Aktif airdrop yok. 🔔 Yakında eklenecek!"); return
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
    if not rows:
        await update.message.reply_text("📭 Henüz haber gönderilmemiş."); return
    text = "📰 *Son Haberler — KriptoDropTR*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i,r in enumerate(rows,1):
        text += f"{i}. {r['topic']} _{r['sent_at'][:10]}_\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_fiyat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 Kullanım: `/fiyat BTC`", parse_mode=ParseMode.MARKDOWN); return
    wait = await update.message.reply_text(f"⏳ {context.args[0].upper()} fiyatı alınıyor...")
    result = await get_price(context.args[0])
    await wait.delete()
    await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ İşlem iptal edildi.", reply_markup=BACK_ADMIN)
    return ConversationHandler.END

async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await show_admin(update, context)

async def back_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await show_user(update, context)

async def unknown_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private": return
    if is_admin(update.effective_user.id):
        await update.message.reply_text("🤖 /start yazarak menüyü aç.", reply_markup=BACK_ADMIN)
    else:
        await update.message.reply_text("👋 /start yazarak menüyü aç.", reply_markup=BACK_USER)

# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    uid = q.from_user.id

    # Ortak
    if d == "back_admin" and is_admin(uid): await back_admin(update, context); return
    if d == "back_user":                    await back_user(update, context);  return
    if d == "price_menu":                   await price_menu(update, context);  return
    if d.startswith("qp_"):                 await cb_quick_price(update, context); return
    if d == "price_custom":                 await price_custom_prompt(update, context); return

    # Kullanıcı
    if not is_admin(uid):
        routes = {"u_list":u_list,"u_pinned":u_pinned,"u_category":u_category,"u_recent":u_recent,"u_help":u_help}
        if d in routes: await routes[d](update, context); return
        if d.startswith("uc_"): await u_filter_cat(update, context); return
        await q.answer("⛔ Yetki yok.", show_alert=True); return

    # Admin
    routes = {
        "manage_airdrops": manage_airdrops,
        "mng_list":        mng_list,
        "stats":           stats_handler,
        "group_info":      group_info_handler,
        "news_history":    news_history_handler,
        "ann_history":     ann_history_handler,
        "coin_analysis":   coin_analysis_entry,
        "news_do_send":    news_do_send,
        "ann_send":        ann_send,
        "ann_redo":        ann_redo,
    }
    if d in routes: await routes[d](update, context); return

    if d in ("mng_delete","mng_toggle","mng_pin","mng_broadcast"):
        await mng_action(update, context); return
    if d.startswith("do_delete_"):     await do_delete(update, context);      return
    if d.startswith("do_toggle_"):     await do_toggle(update, context);      return
    if d.startswith("do_pin_"):        await do_pin(update, context);         return
    if d.startswith("do_broadcast_"):  await do_broadcast(update, context);   return
    if d.startswith("qa_"):            await cb_quick_analysis(update, context); return
    if d.startswith("send_analysis_"): await send_analysis(update, context);  return
    if d.startswith("qnews_"):         await cb_quick_news(update, context);  return
    if d == "news_manual":             await cb_news_manual(update, context); return
    if d == "send_news":               await send_news_entry(update, context); return
    if d == "ai_desc":                 await cb_ai_desc(update, context);     return
    if d == "use_ai_desc":             await cb_use_ai_desc(update, context); return
    if d == "manual_desc":             await cb_manual_desc(update, context); return
    # kullanıcı routes admin için de
    u_routes = {"u_list":u_list,"u_pinned":u_pinned,"u_category":u_category,"u_recent":u_recent,"u_help":u_help}
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

# ── MAIN ─────────────────────────────────────────────────────────────────────
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
                CallbackQueryHandler(cb_quick_news,    pattern=r"^qnews_.+"),
                CallbackQueryHandler(cb_news_manual,   pattern="^news_manual$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, news_text_input),
            ],
            NEWS_PREVIEW: [
                CallbackQueryHandler(news_do_send,     pattern="^news_do_send$"),
                CallbackQueryHandler(cb_quick_news,    pattern=r"^qnews_.+"),
                CallbackQueryHandler(send_news_entry,  pattern="^send_news$"),
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
        fallbacks=[
            CommandHandler("iptal", cancel),
            CallbackQueryHandler(back_admin, pattern="^back_admin$"),
        ],
        allow_reentry=True, conversation_timeout=300,
    )

    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(price_custom_prompt, pattern="^price_custom$")],
        states={PRICE_COIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_coin_input)]},
        fallbacks=[CommandHandler("iptal", cancel)],
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
    app.add_handler(CallbackQueryHandler(cb_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, unknown_private))

    logger.info("🚀 KriptoDropTR Bot v2.0 başlatıldı!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
