import logging
import os
import json
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ChatMemberHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
ADMIN_ID   = int(os.environ["ADMIN_ID"])   # senin Telegram kullanıcı ID'n
GROUP_ID   = int(os.environ["GROUP_ID"])   # grubun chat ID'si

# ─── State (hafıza içi; kalıcılık için SQLite eklenebilir) ────────────────────
warnings    : dict[int, int]  = {}   # user_id -> uyarı sayısı
muted_users : dict[int, datetime] = {}
banned_words: list[str] = []
welcome_msg : str = "Gruba hoş geldin {name}! 🎉"
auto_delete_seconds: int = 0  # 0 = devre dışı
antiflood_enabled: bool = True
antiflood_counter: dict[int, list] = {}

# ─── Yardımcı fonksiyonlar ────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def fmt_user(user) -> str:
    name = user.full_name
    return f'<a href="tg://user?id={user.id}">{name}</a>'

async def send_admin(context: ContextTypes.DEFAULT_TYPE, text: str):
    await context.bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)

async def delete_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id, msg_id)
    except TelegramError:
        pass

# ─── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = (
        "🤖 <b>Grup Yönetim Botu Aktif</b>\n\n"
        "<b>👥 Kullanıcı Yönetimi</b>\n"
        "/ban [id/reply] [neden] – Kullanıcıyı banla\n"
        "/unban [id] – Banı kaldır\n"
        "/kick [id/reply] – Kullanıcıyı at\n"
        "/mute [id/reply] [dk] – Sustur (varsayılan 60 dk)\n"
        "/unmute [id/reply] – Susturmayı kaldır\n"
        "/warn [id/reply] [neden] – Uyarı ver (3'te ban)\n"
        "/unwarn [id/reply] – Uyarıları sıfırla\n"
        "/warnings [id/reply] – Uyarı sayısını gör\n\n"
        "<b>📢 Mesaj Yönetimi</b>\n"
        "/pin [reply] – Mesajı sabitle\n"
        "/unpin – Aktif sabitlenmiş mesajı kaldır\n"
        "/delete [reply] – Mesajı sil\n"
        "/purge [n] – Son n mesajı sil\n"
        "/broadcast [metin] – Gruba duyuru gönder\n\n"
        "<b>⚙️ Grup Ayarları</b>\n"
        "/setwelcome [metin] – Karşılama mesajı ayarla\n"
        "/addban [kelime] – Yasaklı kelime ekle\n"
        "/removeban [kelime] – Yasaklı kelime kaldır\n"
        "/listban – Yasaklı kelimeleri listele\n"
        "/autodelete [sn] – Otomatik silme süresi (0=kapat)\n"
        "/antiflood [on/off] – Anti-flood aç/kapat\n\n"
        "<b>📊 Bilgi</b>\n"
        "/info [id/reply] – Kullanıcı bilgisi\n"
        "/groupinfo – Grup bilgisi\n"
        "/stats – Bot istatistikleri\n"
        "/id – ID göster\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ─── Hedef kullanıcı bul ───────────────────────────────────────────────────────
async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply'den veya argümandan hedef kullanıcıyı çöz."""
    msg = update.message
    if msg.reply_to_message:
        return msg.reply_to_message.from_user
    if context.args:
        try:
            uid = int(context.args[0])
            member = await context.bot.get_chat_member(GROUP_ID, uid)
            return member.user
        except Exception:
            pass
    return None

# ─── BAN ───────────────────────────────────────────────────────────────────────
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    reason = " ".join(context.args[1:]) if context.args and len(context.args) > 1 else "Belirtilmedi"
    try:
        await context.bot.ban_chat_member(GROUP_ID, target.id)
        await update.message.reply_text(
            f"🔨 {fmt_user(target)} gruptan banlandı.\n📝 Neden: {reason}",
            parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── UNBAN ─────────────────────────────────────────────────────────────────────
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    try:
        await context.bot.unban_chat_member(GROUP_ID, target.id)
        await update.message.reply_text(f"✅ {fmt_user(target)} banı kaldırıldı.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── KICK ─────────────────────────────────────────────────────────────────────
async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    try:
        await context.bot.ban_chat_member(GROUP_ID, target.id)
        await context.bot.unban_chat_member(GROUP_ID, target.id)
        await update.message.reply_text(f"👢 {fmt_user(target)} gruptan atıldı.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── MUTE ─────────────────────────────────────────────────────────────────────
async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    minutes = 60
    try:
        arg_index = 0 if not update.message.reply_to_message else 0
        if context.args:
            for a in context.args:
                if a.isdigit():
                    minutes = int(a)
                    break
    except Exception:
        pass
    until = datetime.now() + timedelta(minutes=minutes)
    try:
        await context.bot.restrict_chat_member(
            GROUP_ID, target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )
        muted_users[target.id] = until
        await update.message.reply_text(
            f"🔇 {fmt_user(target)} {minutes} dakika susturuldu.",
            parse_mode=ParseMode.HTML
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── UNMUTE ───────────────────────────────────────────────────────────────────
async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    try:
        await context.bot.restrict_chat_member(
            GROUP_ID, target.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True
            )
        )
        muted_users.pop(target.id, None)
        await update.message.reply_text(f"🔊 {fmt_user(target)} susturması kaldırıldı.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── WARN ─────────────────────────────────────────────────────────────────────
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    reason = " ".join(context.args[1:]) if context.args and len(context.args) > 1 else "Belirtilmedi"
    warnings[target.id] = warnings.get(target.id, 0) + 1
    count = warnings[target.id]
    if count >= 3:
        await context.bot.ban_chat_member(GROUP_ID, target.id)
        await update.message.reply_text(
            f"🔨 {fmt_user(target)} 3 uyarıya ulaştı ve banlandı!", parse_mode=ParseMode.HTML
        )
        warnings.pop(target.id, None)
    else:
        await update.message.reply_text(
            f"⚠️ {fmt_user(target)} uyarıldı. ({count}/3)\n📝 Neden: {reason}",
            parse_mode=ParseMode.HTML
        )

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    warnings.pop(target.id, None)
    await update.message.reply_text(f"✅ {fmt_user(target)} uyarıları sıfırlandı.", parse_mode=ParseMode.HTML)

async def cmd_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        await update.message.reply_text("❌ Hedef kullanıcı bulunamadı.")
        return
    count = warnings.get(target.id, 0)
    await update.message.reply_text(f"⚠️ {fmt_user(target)}: {count}/3 uyarı", parse_mode=ParseMode.HTML)

# ─── PIN / UNPIN ──────────────────────────────────────────────────────────────
async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Sabitlemek için bir mesajı yanıtlayın.")
        return
    try:
        await context.bot.pin_chat_message(GROUP_ID, update.message.reply_to_message.message_id)
        await update.message.reply_text("📌 Mesaj sabitlendi.")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        await context.bot.unpin_chat_message(GROUP_ID)
        await update.message.reply_text("📌 Sabitleme kaldırıldı.")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── DELETE / PURGE ───────────────────────────────────────────────────────────
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if update.message.reply_to_message:
        try:
            await context.bot.delete_message(GROUP_ID, update.message.reply_to_message.message_id)
            await update.message.delete()
        except TelegramError as e:
            await update.message.reply_text(f"❌ Hata: {e}")

async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: /purge [sayı]")
        return
    n = min(int(context.args[0]), 100)
    msgs = []
    async for msg in context.bot.get_chat(GROUP_ID):
        pass
    # Basit yaklaşım: son n mesaj ID'yi sil
    msg_id = update.message.message_id
    deleted = 0
    for i in range(n):
        try:
            await context.bot.delete_message(GROUP_ID, msg_id - i)
            deleted += 1
        except TelegramError:
            pass
    m = await update.message.reply_text(f"🗑️ {deleted} mesaj silindi.")
    asyncio.create_task(delete_later(context, update.effective_chat.id, m.message_id, 5))

# ─── BROADCAST ────────────────────────────────────────────────────────────────
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Kullanım: /broadcast [metin]")
        return
    text = " ".join(context.args)
    try:
        await context.bot.send_message(GROUP_ID, f"📢 <b>Duyuru</b>\n\n{text}", parse_mode=ParseMode.HTML)
        await update.message.reply_text("✅ Duyuru gönderildi.")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

# ─── WELCOME ──────────────────────────────────────────────────────────────────
async def cmd_setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global welcome_msg
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Kullanım: /setwelcome [metin]\nDeğişkenler: {name}, {id}, {group}")
        return
    welcome_msg = " ".join(context.args)
    await update.message.reply_text(f"✅ Karşılama mesajı güncellendi:\n{welcome_msg}")

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        text = welcome_msg.format(
            name=member.full_name,
            id=member.id,
            group=update.effective_chat.title
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 Kurallar", callback_data="rules"),
        ]])
        msg = await update.message.reply_text(text, reply_markup=keyboard)
        if auto_delete_seconds > 0:
            asyncio.create_task(delete_later(context, update.effective_chat.id, msg.message_id, auto_delete_seconds))
        await send_admin(context, f"👤 Yeni üye: {fmt_user(member)} (ID: {member.id}) gruba katıldı.")

# ─── YASAKLI KELİMELER ────────────────────────────────────────────────────────
async def cmd_addban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Kullanım: /addban [kelime]")
        return
    word = " ".join(context.args).lower()
    if word not in banned_words:
        banned_words.append(word)
    await update.message.reply_text(f"✅ '{word}' yasaklı kelimeler listesine eklendi.")

async def cmd_removeban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Kullanım: /removeban [kelime]")
        return
    word = " ".join(context.args).lower()
    if word in banned_words:
        banned_words.remove(word)
        await update.message.reply_text(f"✅ '{word}' listeden kaldırıldı.")
    else:
        await update.message.reply_text("❌ Bu kelime listede yok.")

async def cmd_listban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not banned_words:
        await update.message.reply_text("📋 Yasaklı kelime listesi boş.")
        return
    await update.message.reply_text("📋 Yasaklı kelimeler:\n" + "\n".join(f"• {w}" for w in banned_words))

# ─── AUTO DELETE ──────────────────────────────────────────────────────────────
async def cmd_autodelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_delete_seconds
    if not is_admin(update.effective_user.id): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: /autodelete [saniye] (0=kapat)")
        return
    auto_delete_seconds = int(context.args[0])
    if auto_delete_seconds == 0:
        await update.message.reply_text("✅ Otomatik silme devre dışı.")
    else:
        await update.message.reply_text(f"✅ Mesajlar {auto_delete_seconds} saniye sonra otomatik silinecek.")

# ─── ANTİFLOOD ────────────────────────────────────────────────────────────────
async def cmd_antiflood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global antiflood_enabled
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Kullanım: /antiflood [on/off]")
        return
    if context.args[0].lower() == "on":
        antiflood_enabled = True
        await update.message.reply_text("✅ Anti-flood aktif.")
    elif context.args[0].lower() == "off":
        antiflood_enabled = False
        await update.message.reply_text("✅ Anti-flood devre dışı.")

# ─── INFO ─────────────────────────────────────────────────────────────────────
async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target = await resolve_target(update, context)
    if not target:
        target = update.effective_user
    try:
        member = await context.bot.get_chat_member(GROUP_ID, target.id)
        status = member.status
    except Exception:
        status = "Bilinmiyor"
    text = (
        f"👤 <b>Kullanıcı Bilgisi</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 Ad: {fmt_user(target)}\n"
        f"🆔 ID: <code>{target.id}</code>\n"
        f"📛 Kullanıcı adı: @{target.username or 'Yok'}\n"
        f"📊 Grup durumu: {status}\n"
        f"⚠️ Uyarılar: {warnings.get(target.id, 0)}/3\n"
        f"🤖 Bot: {'Evet' if target.is_bot else 'Hayır'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_groupinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        chat = await context.bot.get_chat(GROUP_ID)
        count = await context.bot.get_chat_member_count(GROUP_ID)
        text = (
            f"📊 <b>Grup Bilgisi</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"📛 Ad: {chat.title}\n"
            f"🆔 ID: <code>{chat.id}</code>\n"
            f"👥 Üye sayısı: {count}\n"
            f"📝 Bio: {chat.description or 'Yok'}\n"
            f"🔗 Link: {chat.invite_link or 'Yok'}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    c = update.effective_chat
    await update.message.reply_text(
        f"👤 Senin ID'n: <code>{u.id}</code>\n"
        f"💬 Bu chat ID: <code>{c.id}</code>",
        parse_mode=ParseMode.HTML
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text(
        f"📊 <b>Bot İstatistikleri</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"⚠️ Toplam uyarılı kullanıcı: {len(warnings)}\n"
        f"🔇 Susturulmuş kullanıcı: {len(muted_users)}\n"
        f"🚫 Yasaklı kelime sayısı: {len(banned_words)}\n"
        f"🗑️ Otomatik silme: {auto_delete_seconds}sn\n"
        f"🌊 Anti-flood: {'Aktif' if antiflood_enabled else 'Pasif'}",
        parse_mode=ParseMode.HTML
    )

# ─── Mesaj filtresi (yasaklı kelime + antiflood) ──────────────────────────────
async def filter_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    if is_admin(user.id):
        return

    # Yasaklı kelime kontrolü
    text_lower = msg.text.lower()
    for word in banned_words:
        if word in text_lower:
            try:
                await msg.delete()
                m = await context.bot.send_message(
                    msg.chat_id,
                    f"⚠️ {fmt_user(user)}, yasaklı kelime kullandın!",
                    parse_mode=ParseMode.HTML
                )
                asyncio.create_task(delete_later(context, msg.chat_id, m.message_id, 5))
            except TelegramError:
                pass
            return

    # Anti-flood kontrolü (10 saniyede 5 mesaj)
    if antiflood_enabled:
        now = datetime.now()
        uid = user.id
        if uid not in antiflood_counter:
            antiflood_counter[uid] = []
        antiflood_counter[uid] = [t for t in antiflood_counter[uid] if (now - t).seconds < 10]
        antiflood_counter[uid].append(now)
        if len(antiflood_counter[uid]) > 5:
            try:
                await context.bot.restrict_chat_member(
                    msg.chat_id, uid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=datetime.now() + timedelta(minutes=5)
                )
                m = await context.bot.send_message(
                    msg.chat_id,
                    f"🌊 {fmt_user(user)} flood yaptığı için 5 dakika susturuldu.",
                    parse_mode=ParseMode.HTML
                )
                asyncio.create_task(delete_later(context, msg.chat_id, m.message_id, 10))
                await send_admin(context, f"🌊 Flood: {fmt_user(user)} (ID: {uid}) susturuldu.")
                antiflood_counter[uid] = []
            except TelegramError:
                pass

    # Otomatik silme
    if auto_delete_seconds > 0:
        asyncio.create_task(delete_later(context, msg.chat_id, msg.message_id, auto_delete_seconds))

# ─── Callback (inline butonlar) ───────────────────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "rules":
        await query.message.reply_text(
            "📋 <b>Grup Kuralları</b>\n\n"
            "1. Saygılı olun\n"
            "2. Spam yapmayın\n"
            "3. Reklam yasak\n"
            "4. Kurallara uymayanlar banlanır.",
            parse_mode=ParseMode.HTML
        )

# ─── Hata yakalayıcı ──────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Hata: {context.error}", exc_info=context.error)

# ─── Ana fonksiyon ────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Komutlar
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("warnings", cmd_warnings))
    app.add_handler(CommandHandler("pin", cmd_pin))
    app.add_handler(CommandHandler("unpin", cmd_unpin))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("purge", cmd_purge))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome))
    app.add_handler(CommandHandler("addban", cmd_addban))
    app.add_handler(CommandHandler("removeban", cmd_removeban))
    app.add_handler(CommandHandler("listban", cmd_listban))
    app.add_handler(CommandHandler("autodelete", cmd_autodelete))
    app.add_handler(CommandHandler("antiflood", cmd_antiflood))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("groupinfo", cmd_groupinfo))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Yeni üye
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))

    # Mesaj filtresi
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, filter_messages))

    # Inline butonlar
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Hata
    app.add_error_handler(error_handler)

    logger.info("Bot başlatılıyor...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
