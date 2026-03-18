#!/usr/bin/env python3
"""
KriptoDropTR Telegram Botu
Admin DM üzerinden yönetilen, airdrop ve kripto haber botu.
"""

import asyncio
import sqlite3
import logging
import httpx
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from config import BOT_TOKEN, ADMIN_ID, GROUP_ID, GROK_API_KEY

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─── Conversation States ───────────────────────────────────────────────────────
(
    AIRDROP_NAME, AIRDROP_PROJECT, AIRDROP_DESC,
    AIRDROP_REWARD, AIRDROP_LINK, AIRDROP_DEADLINE, AIRDROP_CATEGORY,
    NEWS_TOPIC,
    ANNOUNCE_TEXT,
    CUSTOM_MSG,
    EDIT_CHOOSE, EDIT_FIELD, EDIT_VALUE,
) = range(13)

CATEGORIES = ["🪙 DeFi", "🎮 GameFi", "🖼 NFT", "🔗 Layer1/Layer2", "📱 Web3", "🌐 Diğer"]

# ─── Database ──────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("kriptodrop.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS airdrops (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            project     TEXT,
            description TEXT,
            reward      TEXT,
            link        TEXT,
            deadline    TEXT,
            category    TEXT,
            active      INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            pinned      INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS news_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic      TEXT,
            content    TEXT,
            sent_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def db():
    return sqlite3.connect("kriptodrop.db")

# ─── Helpers ───────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if not is_admin(uid):
            await update.effective_message.reply_text("⛔ Bu komut sadece admin içindir.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper

def fmt_airdrop(row, index=None) -> str:
    aid, name, project, desc, reward, link, deadline, category, active, created, pinned = row
    pin = "📌 " if pinned else ""
    idx = f"#{index} " if index is not None else f"[ID:{aid}] "
    lines = [
        f"{pin}{idx}*{name}*",
        f"🏢 Proje: {project or 'Belirtilmedi'}",
        f"🏷 Kategori: {category or 'Diğer'}",
        f"💰 Ödül: {reward or 'Belirtilmedi'}",
        f"📝 Açıklama: {desc or 'Yok'}",
        f"🔗 Link: {link or 'Yok'}",
        f"⏰ Son Tarih: {deadline or 'Belirtilmedi'}",
        f"📅 Eklenme: {created[:10]}",
    ]
    return "\n".join(lines)

# ─── Grok API ─────────────────────────────────────────────────────────────────
async def fetch_grok_news(topic: str) -> str:
    """Grok API'den kripto haberi çeker."""
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "grok-3-latest",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Sen KriptoDropTR Telegram grubu için çalışan bir kripto haber asistanısın. "
                    "Verilen konuda güncel, bilgilendirici ve özlü Türkçe kripto haberleri yaz. "
                    "Emoji kullan, okunabilir paragraflar halinde sun, kaynak ekle (mevcut değilse genel söyle)."
                )
            },
            {
                "role": "user",
                "content": f"'{topic}' hakkında KriptoDropTR grubuna paylaşılacak güncel ve bilgilendirici bir haber/analiz yaz."
            }
        ],
        "max_tokens": 700,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.x.ai/v1/chat/completions",
                headers=headers,
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Grok API hatası: {e}")
        return f"⚠️ Haber çekilemedi: {e}"

# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text(
            "👋 Merhaba! Komutlar için bana özel mesaj at.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📩 Bota Mesaj At", url=f"https://t.me/{context.bot.username}")
            ]])
        )
        return

    if is_admin(user.id):
        await send_admin_panel(update, context)
    else:
        await send_user_panel(update, context)

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db(); c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
    news_count = c.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
    conn.close()

    text = (
        "🛠 *KriptoDropTR Admin Paneli*\n\n"
        f"📋 Aktif Airdrop: *{total}*\n"
        f"📰 Toplam Haber Gönderimi: *{news_count}*\n\n"
        "Aşağıdan işlem seçin:"
    )
    kb = [
        [
            InlineKeyboardButton("➕ Airdrop Ekle", callback_data="admin_add_airdrop"),
            InlineKeyboardButton("📋 Airdropları Listele", callback_data="admin_list_airdrops"),
        ],
        [
            InlineKeyboardButton("✏️ Airdrop Düzenle", callback_data="admin_edit_airdrop"),
            InlineKeyboardButton("🗑 Airdrop Sil", callback_data="admin_delete_airdrop"),
        ],
        [
            InlineKeyboardButton("📰 Haber Gönder (Grok)", callback_data="admin_send_news"),
            InlineKeyboardButton("📢 Duyuru Yap", callback_data="admin_announce"),
        ],
        [
            InlineKeyboardButton("📌 Airdrop Sabitle", callback_data="admin_pin_airdrop"),
            InlineKeyboardButton("📊 İstatistikler", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("✅ Airdrop Aktif/Pasif", callback_data="admin_toggle_airdrop"),
            InlineKeyboardButton("🔄 Grup Bilgisi", callback_data="admin_group_info"),
        ],
    ]
    await update.effective_message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def send_user_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *KriptoDropTR'ye hoş geldin!*\n\n"
        "🪂 En güncel airdropları takip et, kripto dünyasından haberleri kaçırma!\n\n"
        "Aşağıdan istediğin işlemi seç:"
    )
    kb = [
        [
            InlineKeyboardButton("🪂 Aktif Airdroplar", callback_data="user_list_airdrops"),
            InlineKeyboardButton("📌 Öne Çıkanlar", callback_data="user_pinned"),
        ],
        [
            InlineKeyboardButton("🔍 Kategoriye Göre", callback_data="user_by_category"),
            InlineKeyboardButton("📅 Son Eklenenler", callback_data="user_recent"),
        ],
        [
            InlineKeyboardButton("📢 Gruba Katıl", url=f"https://t.me/{GROUP_ID[4:] if str(GROUP_ID).startswith('-100') else GROUP_ID}"),
            InlineKeyboardButton("❓ Yardım", callback_data="user_help"),
        ],
    ]
    await update.effective_message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

# ─── Admin: Airdrop Ekle ───────────────────────────────────────────────────────
@admin_only
async def admin_add_airdrop_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    context.user_data.clear()
    await update.effective_message.reply_text(
        "➕ *Yeni Airdrop Ekle*\n\n"
        "Airdrop adını girin:\n_(Örn: Arbitrum Airdrop)_",
        parse_mode="Markdown"
    )
    return AIRDROP_NAME

async def airdrop_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("🏢 Proje/Token adı nedir?\n_(Örn: ARB)_")
    return AIRDROP_PROJECT

async def airdrop_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["project"] = update.message.text
    await update.message.reply_text("📝 Açıklamayı girin:\n_(Kısa bir tanıtım)_")
    return AIRDROP_DESC

async def airdrop_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["desc"] = update.message.text
    await update.message.reply_text("💰 Ödül miktarı/türü nedir?\n_(Örn: 1000 ARB token)_")
    return AIRDROP_REWARD

async def airdrop_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reward"] = update.message.text
    await update.message.reply_text("🔗 Katılım linki:\n_(URL girin veya 'yok' yazın)_")
    return AIRDROP_LINK

async def airdrop_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["link"] = update.message.text if update.message.text.lower() != "yok" else ""
    await update.message.reply_text("⏰ Son katılım tarihi:\n_(Örn: 31.12.2025 veya 'belirtilmedi')_")
    return AIRDROP_DEADLINE

async def airdrop_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["deadline"] = update.message.text if update.message.text.lower() != "belirtilmedi" else ""
    kb = [[InlineKeyboardButton(cat, callback_data=f"cat_{i}")] for i, cat in enumerate(CATEGORIES)]
    await update.message.reply_text("🏷 Kategori seçin:", reply_markup=InlineKeyboardMarkup(kb))
    return AIRDROP_CATEGORY

async def airdrop_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[1])
    context.user_data["category"] = CATEGORIES[idx]

    conn = db(); c = conn.cursor()
    c.execute("""
        INSERT INTO airdrops (name, project, description, reward, link, deadline, category)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        context.user_data["name"], context.user_data["project"],
        context.user_data["desc"], context.user_data["reward"],
        context.user_data["link"], context.user_data["deadline"],
        context.user_data["category"]
    ))
    new_id = c.lastrowid
    conn.commit(); conn.close()

    summary = (
        f"✅ *Airdrop başarıyla eklendi!* [ID: {new_id}]\n\n"
        f"📛 *{context.user_data['name']}*\n"
        f"🏢 {context.user_data['project']} | {context.user_data['category']}\n"
        f"💰 {context.user_data['reward']}\n"
        f"⏰ {context.user_data.get('deadline') or 'Belirtilmedi'}"
    )
    kb = [
        [
            InlineKeyboardButton("📢 Gruba Duyur", callback_data=f"broadcast_airdrop_{new_id}"),
            InlineKeyboardButton("📌 Sabitle", callback_data=f"pin_airdrop_{new_id}"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]
    ]
    await q.edit_message_text(summary, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ConversationHandler.END

# ─── Admin: Airdrop Listele ────────────────────────────────────────────────────
async def admin_list_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM airdrops ORDER BY pinned DESC, id DESC LIMIT 20"
    ).fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "📭 Henüz airdrop eklenmemiş.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
        )
        return

    for i, row in enumerate(rows, 1):
        status = "🟢 Aktif" if row[8] else "🔴 Pasif"
        text = fmt_airdrop(row, i) + f"\n📊 Durum: {status}"
        await update.effective_message.reply_text(text, parse_mode="Markdown")

    await update.effective_message.reply_text(
        f"📋 Toplam {len(rows)} airdrop listelendi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
    )

# ─── Admin: Airdrop Sil ────────────────────────────────────────────────────────
async def admin_delete_airdrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT id, name FROM airdrops ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text("📭 Silinecek airdrop yok.")
        return

    kb = [[InlineKeyboardButton(f"🗑 [{r[0]}] {r[1]}", callback_data=f"del_confirm_{r[0]}")] for r in rows]
    kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")])
    await update.effective_message.reply_text(
        "Silmek istediğiniz airdropi seçin:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[2])
    conn = db(); c = conn.cursor()
    name = c.execute("SELECT name FROM airdrops WHERE id=?", (aid,)).fetchone()
    if name:
        c.execute("DELETE FROM airdrops WHERE id=?", (aid,))
        conn.commit()
        await q.edit_message_text(
            f"🗑 *{name[0]}* silindi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]]),
            parse_mode="Markdown"
        )
    conn.close()

# ─── Admin: Airdrop Aktif/Pasif ───────────────────────────────────────────────
async def admin_toggle_airdrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT id, name, active FROM airdrops ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()

    kb = []
    for r in rows:
        icon = "🟢" if r[2] else "🔴"
        kb.append([InlineKeyboardButton(f"{icon} [{r[0]}] {r[1]}", callback_data=f"toggle_{r[0]}")])
    kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")])
    await update.effective_message.reply_text(
        "Aktif/Pasif değiştirmek istediğinizi seçin:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def toggle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[1])
    conn = db(); c = conn.cursor()
    row = c.execute("SELECT name, active FROM airdrops WHERE id=?", (aid,)).fetchone()
    if row:
        new_status = 0 if row[1] else 1
        c.execute("UPDATE airdrops SET active=? WHERE id=?", (new_status, aid))
        conn.commit()
        status_text = "🟢 Aktif" if new_status else "🔴 Pasif"
        await q.edit_message_text(
            f"✅ *{row[0]}* → {status_text} yapıldı.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]]),
            parse_mode="Markdown"
        )
    conn.close()

# ─── Admin: Sabitle ────────────────────────────────────────────────────────────
async def admin_pin_airdrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT id, name, pinned FROM airdrops WHERE active=1 ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()

    kb = []
    for r in rows:
        icon = "📌" if r[2] else "📋"
        kb.append([InlineKeyboardButton(f"{icon} [{r[0]}] {r[1]}", callback_data=f"pin_{r[0]}")])
    kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")])
    await update.effective_message.reply_text(
        "📌 Sabitlenecek airdropi seçin:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def pin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[1])
    conn = db(); c = conn.cursor()
    row = c.execute("SELECT name, pinned FROM airdrops WHERE id=?", (aid,)).fetchone()
    if row:
        new_pin = 0 if row[1] else 1
        c.execute("UPDATE airdrops SET pinned=? WHERE id=?", (new_pin, aid))
        conn.commit()
        icon = "📌" if new_pin else "📋"
        await q.edit_message_text(
            f"{icon} *{row[0]}* {'sabitlendi' if new_pin else 'sabit kaldırıldı'}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]]),
            parse_mode="Markdown"
        )
    conn.close()

# ─── Admin: Gruba Duyur (Airdrop) ─────────────────────────────────────────────
async def broadcast_airdrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    aid = int(q.data.split("_")[2])
    conn = db(); c = conn.cursor()
    row = c.execute("SELECT * FROM airdrops WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not row:
        await q.answer("Airdrop bulunamadı!", show_alert=True)
        return

    msg = (
        "🪂 *YENİ AİRDROP!* 🪂\n\n" +
        fmt_airdrop(row) +
        "\n\n🔔 @KriptoDropTR"
    )
    kb = []
    if row[5]:  # link
        kb.append([InlineKeyboardButton("🚀 Katıl!", url=row[5])])

    await context.bot.send_message(
        GROUP_ID, msg,
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        parse_mode="Markdown"
    )
    await q.edit_message_text(
        "✅ Airdrop gruba duyuruldu!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
    )

# ─── Admin: Haber Gönder (Grok) ───────────────────────────────────────────────
@admin_only
async def admin_send_news_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    await update.effective_message.reply_text(
        "📰 *Haber/Analiz Gönder*\n\n"
        "Grok AI ile haber oluşturmak istediğiniz konuyu yazın:\n"
        "_(Örn: Bitcoin yükselişi, Ethereum ETF, Solana ekosistemi)_",
        parse_mode="Markdown"
    )
    return NEWS_TOPIC

async def news_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text
    wait_msg = await update.message.reply_text("⏳ Grok AI haber oluşturuyor, lütfen bekleyin...")
    
    content = await fetch_grok_news(topic)
    
    await wait_msg.delete()
    
    context.user_data["news_content"] = content
    context.user_data["news_topic"] = topic

    kb = [
        [
            InlineKeyboardButton("📢 Gruba Gönder", callback_data="news_send_group"),
            InlineKeyboardButton("✏️ Düzenle", callback_data="news_edit"),
        ],
        [InlineKeyboardButton("❌ İptal", callback_data="back_admin")]
    ]
    preview = f"📰 *Önizleme ({topic}):*\n\n{content}"
    if len(preview) > 4096:
        preview = preview[:4090] + "..."
    
    await update.message.reply_text(preview, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ConversationHandler.END

async def news_send_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    content = context.user_data.get("news_content", "")
    topic = context.user_data.get("news_topic", "")
    
    msg = f"📰 *KriptoDropTR Haber* 📰\n\n{content}\n\n🔔 @KriptoDropTR"
    if len(msg) > 4096:
        msg = msg[:4090] + "..."
    
    await context.bot.send_message(GROUP_ID, msg, parse_mode="Markdown")
    
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO news_log (topic, content) VALUES (?,?)", (topic, content))
    conn.commit(); conn.close()
    
    await q.edit_message_text(
        "✅ Haber gruba gönderildi!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
    )

# ─── Admin: Duyuru ────────────────────────────────────────────────────────────
@admin_only
async def admin_announce_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    await update.effective_message.reply_text(
        "📢 *Duyuru Metni*\n\n"
        "Gruba gönderilecek duyuruyu yazın:\n"
        "_(Markdown kullanabilirsiniz: *kalın*, _italik_, [link](url))_",
        parse_mode="Markdown"
    )
    return ANNOUNCE_TEXT

async def announce_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["announce"] = text
    kb = [
        [
            InlineKeyboardButton("📢 Gönder", callback_data="announce_send"),
            InlineKeyboardButton("❌ İptal", callback_data="back_admin"),
        ]
    ]
    await update.message.reply_text(
        f"📋 *Önizleme:*\n\n{text}\n\nGönderilsin mi?",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ConversationHandler.END

async def announce_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    text = context.user_data.get("announce", "")
    msg = f"📢 *DUYURU*\n\n{text}\n\n🔔 @KriptoDropTR"
    await context.bot.send_message(GROUP_ID, msg, parse_mode="Markdown")
    await q.edit_message_text(
        "✅ Duyuru gönderildi!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
    )

# ─── Admin: İstatistikler ─────────────────────────────────────────────────────
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    conn = db(); c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM airdrops").fetchone()[0]
    active = c.execute("SELECT COUNT(*) FROM airdrops WHERE active=1").fetchone()[0]
    pinned = c.execute("SELECT COUNT(*) FROM airdrops WHERE pinned=1").fetchone()[0]
    news = c.execute("SELECT COUNT(*) FROM news_log").fetchone()[0]
    cats = c.execute("SELECT category, COUNT(*) FROM airdrops GROUP BY category").fetchall()
    conn.close()

    cat_text = "\n".join([f"  • {r[0] or 'Diğer'}: {r[1]}" for r in cats]) or "  Yok"
    
    text = (
        "📊 *Bot İstatistikleri*\n\n"
        f"🪂 Toplam Airdrop: *{total}*\n"
        f"🟢 Aktif: *{active}*\n"
        f"📌 Sabitlenmiş: *{pinned}*\n"
        f"📰 Haber Gönderimi: *{news}*\n\n"
        f"📁 *Kategoriler:*\n{cat_text}"
    )
    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]]),
        parse_mode="Markdown"
    )

# ─── Admin: Grup Bilgisi ──────────────────────────────────────────────────────
async def admin_group_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    try:
        chat = await context.bot.get_chat(GROUP_ID)
        count = await context.bot.get_chat_member_count(GROUP_ID)
        text = (
            f"🔗 *Grup Bilgisi*\n\n"
            f"📛 Ad: *{chat.title}*\n"
            f"👥 Üye Sayısı: *{count}*\n"
            f"🆔 ID: `{GROUP_ID}`\n"
            f"📝 Açıklama: {chat.description or 'Yok'}"
        )
    except Exception as e:
        text = f"⚠️ Grup bilgisi alınamadı: {e}"

    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]]),
        parse_mode="Markdown"
    )

# ─── User: Airdropları Listele ────────────────────────────────────────────────
async def user_list_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q: await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute(
        "SELECT * FROM airdrops WHERE active=1 ORDER BY pinned DESC, id DESC LIMIT 10"
    ).fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "📭 Şu an aktif airdrop bulunmuyor.\n🔔 Yakında yeni airdroplar eklenecek!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]])
        )
        return

    await update.effective_message.reply_text(f"🪂 *{len(rows)} Aktif Airdrop:*", parse_mode="Markdown")
    for i, row in enumerate(rows, 1):
        text = fmt_airdrop(row, i)
        kb = []
        if row[5]:  # link
            kb.append([InlineKeyboardButton("🚀 Katıl!", url=row[5])])
        await update.effective_message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
            parse_mode="Markdown"
        )
    await update.effective_message.reply_text(
        "🔔 Yeni airdroplar için grubu takip et!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]])
    )

async def user_pinned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM airdrops WHERE pinned=1 AND active=1").fetchall()
    conn.close()

    if not rows:
        await q.edit_message_text(
            "📭 Sabitlenmiş airdrop yok.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]])
        )
        return
    for row in rows:
        text = fmt_airdrop(row)
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row[5])]] if row[5] else []
        await update.effective_message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode="Markdown"
        )

async def user_by_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    kb = [[InlineKeyboardButton(cat, callback_data=f"filter_cat_{i}")] for i, cat in enumerate(CATEGORIES)]
    kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")])
    await q.edit_message_text("🏷 Kategori seçin:", reply_markup=InlineKeyboardMarkup(kb))

async def filter_by_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    idx = int(q.data.split("_")[2])
    cat = CATEGORIES[idx]
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM airdrops WHERE category=? AND active=1", (cat,)).fetchall()
    conn.close()

    if not rows:
        await q.edit_message_text(
            f"📭 {cat} kategorisinde airdrop yok.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="user_by_category")]])
        )
        return

    await q.edit_message_text(f"🏷 *{cat}* - {len(rows)} Airdrop:", parse_mode="Markdown")
    for i, row in enumerate(rows, 1):
        text = fmt_airdrop(row, i)
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row[5])]] if row[5] else []
        await update.effective_message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode="Markdown"
        )

async def user_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()

    await q.edit_message_text(f"🆕 *Son Eklenen {len(rows)} Airdrop:*", parse_mode="Markdown")
    for i, row in enumerate(rows, 1):
        text = fmt_airdrop(row, i)
        kb = [[InlineKeyboardButton("🚀 Katıl!", url=row[5])]] if row[5] else []
        await update.effective_message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode="Markdown"
        )

async def user_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    text = (
        "❓ *KriptoDropTR Bot Yardım*\n\n"
        "🪂 *Airdrop nedir?*\n"
        "Kripto projelerin ücretsiz token dağıtımlarıdır.\n\n"
        "📋 *Nasıl katılırım?*\n"
        "Listelenen airdroplarda 'Katıl' butonuna tıklayarak katılabilirsiniz.\n\n"
        "🔔 *Bildirimleri nasıl açarım?*\n"
        "Telegram grubumuzda bildirimlerinizi açık tutun.\n\n"
        "📢 *Grup:* @KriptoDropTR\n"
        "⚠️ *Uyarı:* Hiçbir airdrop için private key paylaşmayın!"
    )
    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_user")]]),
        parse_mode="Markdown"
    )

# ─── /airdrops & /haberler (Grup komutları) ───────────────────────────────────
async def cmd_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT * FROM airdrops WHERE active=1 ORDER BY pinned DESC, id DESC LIMIT 5").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Aktif airdrop yok. Yakında eklenecek! 🔔")
        return
    text = "🪂 *Aktif Airdroplar:*\n\n"
    for i, row in enumerate(rows, 1):
        pin = "📌 " if row[10] else ""
        link_part = f" | [Katıl]({row[5]})" if row[5] else ""
        text += f"{pin}*{i}. {row[1]}* ({row[7] or 'Genel'})\n💰 {row[4] or '?'} | ⏰ {row[6] or '?'}{link_part}\n\n"
    text += "📩 Detay için bota özel mesaj at!"
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_haberler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db(); c = conn.cursor()
    rows = c.execute("SELECT topic, sent_at FROM news_log ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Henüz haber gönderilmemiş.")
        return
    text = "📰 *Son Haberler:*\n\n"
    for r in rows:
        text += f"• {r[0]} _{r[1][:10]}_\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── Geri / Ana Menü ──────────────────────────────────────────────────────────
async def back_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await send_admin_panel(update, context)

async def back_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await send_user_panel(update, context)

# ─── Callback Router ──────────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if not is_admin(q.from_user.id):
        # User callbacks
        if data == "user_list_airdrops": await user_list_airdrops(update, context)
        elif data == "user_pinned":       await user_pinned(update, context)
        elif data == "user_by_category": await user_by_category(update, context)
        elif data == "user_recent":      await user_recent(update, context)
        elif data == "user_help":        await user_help(update, context)
        elif data == "back_user":        await back_user(update, context)
        elif data.startswith("filter_cat_"): await filter_by_category(update, context)
        else: await q.answer("Bu işlem için yetkiniz yok.", show_alert=True)
        return

    # Admin callbacks
    if data == "back_admin":               await back_admin(update, context)
    elif data == "admin_list_airdrops":    await admin_list_airdrops(update, context)
    elif data == "admin_delete_airdrop":   await admin_delete_airdrop(update, context)
    elif data == "admin_toggle_airdrop":   await admin_toggle_airdrop(update, context)
    elif data == "admin_pin_airdrop":      await admin_pin_airdrop(update, context)
    elif data == "admin_stats":            await admin_stats(update, context)
    elif data == "admin_group_info":       await admin_group_info(update, context)
    elif data == "news_send_group":        await news_send_group(update, context)
    elif data == "announce_send":          await announce_send(update, context)
    elif data.startswith("del_confirm_"):  await delete_confirm(update, context)
    elif data.startswith("toggle_"):       await toggle_confirm(update, context)
    elif data.startswith("pin_airdrop_"):  
        # from broadcast confirm panel
        aid = int(data.split("_")[2])
        conn = db(); c = conn.cursor()
        c.execute("UPDATE airdrops SET pinned=1 WHERE id=?", (aid,))
        conn.commit(); conn.close()
        await q.answer("📌 Sabitlendi!", show_alert=True)
    elif data.startswith("pin_"):          await pin_confirm(update, context)
    elif data.startswith("broadcast_airdrop_"): await broadcast_airdrop(update, context)
    elif data == "user_list_airdrops":     await user_list_airdrops(update, context)
    elif data == "user_pinned":            await user_pinned(update, context)
    elif data == "user_by_category":       await user_by_category(update, context)
    elif data == "user_recent":            await user_recent(update, context)
    elif data == "user_help":             await user_help(update, context)
    elif data.startswith("filter_cat_"):  await filter_by_category(update, context)
    elif data == "news_edit":
        await q.edit_message_text(
            "✏️ Düzenleme: Yeni metni gönderin (sonra tekrar önizlenecek)",
        )
    else:
        await q.answer()

# ─── /iptal ───────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ İşlem iptal edildi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
    )
    return ConversationHandler.END

# ─── Bilinmeyen mesaj ─────────────────────────────────────────────────────────
async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private" and is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🤖 Admin paneli için /start yazın.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="back_admin")]])
        )

# ─── Main ─────────────────────────────────────────────────────────────────────
async def post_init(app: Application):
    commands = [
        BotCommand("start", "Bot menüsünü aç"),
        BotCommand("airdrops", "Aktif airdropları listele"),
        BotCommand("haberler", "Son haberlere göz at"),
        BotCommand("iptal", "İşlemi iptal et"),
    ]
    await app.bot.set_my_commands(commands)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Airdrop ekleme conversation
    airdrop_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_airdrop_start, pattern="^admin_add_airdrop$")],
        states={
            AIRDROP_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, airdrop_name)],
            AIRDROP_PROJECT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, airdrop_project)],
            AIRDROP_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, airdrop_desc)],
            AIRDROP_REWARD:   [MessageHandler(filters.TEXT & ~filters.COMMAND, airdrop_reward)],
            AIRDROP_LINK:     [MessageHandler(filters.TEXT & ~filters.COMMAND, airdrop_link)],
            AIRDROP_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, airdrop_deadline)],
            AIRDROP_CATEGORY: [CallbackQueryHandler(airdrop_category, pattern=r"^cat_\d+$")],
        },
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True,
    )

    # Haber gönderme conversation
    news_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_send_news_start, pattern="^admin_send_news$")],
        states={
            NEWS_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, news_topic)],
        },
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True,
    )

    # Duyuru conversation
    announce_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_announce_start, pattern="^admin_announce$")],
        states={
            ANNOUNCE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, announce_text)],
        },
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("airdrops", cmd_airdrops))
    app.add_handler(CommandHandler("haberler", cmd_haberler))
    app.add_handler(CommandHandler("iptal", cancel))
    app.add_handler(airdrop_conv)
    app.add_handler(news_conv)
    app.add_handler(announce_conv)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, unknown_message))

    logger.info("🚀 KriptoDropTR Bot başlatıldı!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
