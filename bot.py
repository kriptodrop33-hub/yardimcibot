"""
╔══════════════════════════════════════════════════════════════╗
║          TELEGRAM GRUP YÖNETİM BOTU  v3.0                   ║
║  • Tüm işlemler DM'deki inline butonlardan yapılır           ║
║  • Bot senden adım adım bilgi ister (ID, miktar vs.)         ║
║  • Açıklayıcı, uzun panel metinleri                          ║
║  • Grupta /komut yazınca BotFather listesi görünür           ║
╚══════════════════════════════════════════════════════════════╝
"""

import logging
import os
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    ForceReply,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError

# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])
GROUP_ID  = int(os.environ["GROUP_ID"])

# ──────────────────────────────────────────────────────────────
# UYGULAMA DURUMU
# ──────────────────────────────────────────────────────────────
warnings_db    : dict[int, int]      = {}   # user_id → uyarı sayısı
muted_users    : dict[int, datetime] = {}
banned_words   : list[str]           = []
notes          : dict[str, str]      = {}
welcome_msg    : str  = "👋 Merhaba {name}! <b>{group}</b> grubuna hoş geldin!\n\nLütfen grup kurallarını oku ve saygılı ol. İyi eğlenceler! 🎉"
auto_delete_sec: int  = 0
antiflood_on   : bool = True
antiflood_buf  : dict[int, list]     = {}
group_locked   : bool = False
slowmode_sec   : int  = 0

stats: dict[str, int] = {
    "total_messages"  : 0,
    "deleted_messages": 0,
    "banned_users"    : 0,
    "warned_users"    : 0,
}

# "Bekleme" durumu: hangi adımda olduğumuzu tutar
# pending[user_id] = {"action": str, "data": dict}
pending: dict[int, dict] = {}

# ──────────────────────────────────────────────────────────────
# YARDIMCILAR
# ──────────────────────────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def fmt(user) -> str:
    return f'<a href="tg://user?id={user.id}">{user.full_name}</a>'

async def notify_admin(ctx, text: str):
    await ctx.bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)

async def auto_delete(ctx, chat_id: int, msg_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
    except TelegramError:
        pass

async def _bulk_delete(ctx, chat_id: int, from_id: int, to_id: int) -> int:
    """from_id'den to_id'ye kadar (to_id dahil) tüm mesajları 100'lük batch'lerle siler.
    Telegram delete_messages API'si max 100 ID kabul eder.
    Döner: silinen mesaj sayısı."""
    if from_id < to_id:
        from_id, to_id = to_id, from_id  # her zaman from_id >= to_id

    all_ids = list(range(to_id, from_id + 1))  # küçükten büyüğe
    deleted = 0

    # 100'lük batch'lere böl
    for i in range(0, len(all_ids), 100):
        batch = all_ids[i:i + 100]
        try:
            # delete_messages toplu silme (Python-telegram-bot 20+)
            await ctx.bot.delete_messages(chat_id, batch)
            deleted += len(batch)
        except TelegramError:
            # Toplu başarısız olursa tek tek dene
            for mid in batch:
                try:
                    await ctx.bot.delete_message(chat_id, mid)
                    deleted += 1
                except TelegramError:
                    pass
        await asyncio.sleep(0.05)  # rate limit

    return deleted

def back_btn(target="main") -> InlineKeyboardMarkup:
    labels = {
        "main"    : "🏠 Ana Menü",
        "users"   : "◀️ Kullanıcı Yönetimi",
        "msgs"    : "◀️ Mesaj Yönetimi",
        "settings": "◀️ Grup Ayarları",
        "security": "◀️ Güvenlik",
        "notes"   : "◀️ Not Sistemi",
        "info"    : "◀️ Bilgi & İstatistik",
    }
    return InlineKeyboardMarkup([[InlineKeyboardButton(labels.get(target, "◀️ Geri"), callback_data=f"menu_{target}")]])

# ──────────────────────────────────────────────────────────────
# ANA MENÜ
# ──────────────────────────────────────────────────────────────
MAIN_MENU_TEXT = (
    "🤖 <b>Grup Yönetim Paneli — v3.0</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Bu panel üzerinden grubunu <b>tek tıkla</b> yönetebilirsin.\n"
    "Aşağıdaki kategorilerden birini seç ve işlemini gerçekleştir.\n\n"
    "💡 <b>Nasıl çalışır?</b>\n"
    "Bir kategoriye tıkla → İşlem butonlarını gör → Butona bas → "
    "Bot senden gerekli bilgiyi ister → İşlem tamamlanır.\n\n"
    "📌 Grupta komut da kullanabilirsin (<code>/ban</code>, <code>/mute</code> vb.) "
    "ama bu panel çok daha pratik! 😎"
)

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Kullanıcı Yönetimi", callback_data="menu_users"),
            InlineKeyboardButton("📢 Mesaj Yönetimi",     callback_data="menu_msgs"),
        ],
        [
            InlineKeyboardButton("⚙️ Grup Ayarları",      callback_data="menu_settings"),
            InlineKeyboardButton("🛡️ Güvenlik",           callback_data="menu_security"),
        ],
        [
            InlineKeyboardButton("📝 Not Sistemi",         callback_data="menu_notes"),
            InlineKeyboardButton("📊 Bilgi & İstatistik",  callback_data="menu_info"),
        ],
        [
            InlineKeyboardButton("📣 Gruba Duyuru Gönder", callback_data="menu_broadcast"),
        ],
    ])

# ──────────────────────────────────────────────────────────────
# KATEGORİ MENÜLERİ — Metin + Butonlar
# ──────────────────────────────────────────────────────────────
def users_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "👥 <b>Kullanıcı Yönetimi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Bu bölümden gruptaki kullanıcıları yönetebilirsin.\n\n"
        "🔨 <b>Banla</b> — Kullanıcıyı kalıcı olarak gruptan kovar ve bir daha girmesini engeller.\n"
        "✅ <b>Ban Kaldır</b> — Daha önce banlanan kullanıcının yasağını kaldırır, gruba tekrar girebilir.\n"
        "👢 <b>At (Kick)</b> — Kullanıcıyı gruptan atar, ancak davet linki ile tekrar girebilir.\n"
        "🔇 <b>Sustur (Mute)</b> — Kullanıcının mesaj göndermesini belirli bir süre engeller.\n"
        "🔊 <b>Sesi Aç</b> — Daha önce susturulan kullanıcıyı tekrar konuşturur.\n"
        "⚠️ <b>Uyarı Ver</b> — Kullanıcıya uyarı gönderir. <b>3 uyarıda otomatik ban!</b>\n"
        "🔄 <b>Uyarı Sıfırla</b> — Kullanıcının tüm uyarı geçmişini temizler.\n"
        "📊 <b>Uyarı Sorgula</b> — Bir kullanıcının kaç uyarısı olduğunu gösterir.\n"
        "⬆️ <b>Admin Yap</b> — Kullanıcıyı grup yöneticisi yapar.\n"
        "⬇️ <b>Admin'den Al</b> — Kullanıcının yönetici yetkilerini iptal eder.\n"
        "👤 <b>Kullanıcı Bilgisi</b> — ID, kullanıcı adı, grup durumu ve uyarı sayısını gösterir.\n\n"
        "💡 Bir işleme tıkladıktan sonra bot senden <b>kullanıcı ID'sini</b> veya gruba iletmek için "
        "<b>mesajı yanıtlamanı</b> isteyecek."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔨 Banla",          callback_data="act_ban"),
            InlineKeyboardButton("✅ Ban Kaldır",      callback_data="act_unban"),
            InlineKeyboardButton("👢 At",              callback_data="act_kick"),
        ],
        [
            InlineKeyboardButton("🔇 Sustur",          callback_data="act_mute"),
            InlineKeyboardButton("🔊 Sesi Aç",         callback_data="act_unmute"),
        ],
        [
            InlineKeyboardButton("⚠️ Uyarı Ver",       callback_data="act_warn"),
            InlineKeyboardButton("🔄 Uyarı Sıfırla",   callback_data="act_unwarn"),
            InlineKeyboardButton("📊 Uyarı Sorgula",   callback_data="act_warnings"),
        ],
        [
            InlineKeyboardButton("⬆️ Admin Yap",       callback_data="act_promote"),
            InlineKeyboardButton("⬇️ Admin'den Al",    callback_data="act_demote"),
        ],
        [
            InlineKeyboardButton("👤 Kullanıcı Bilgisi", callback_data="act_info"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def msgs_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📢 <b>Mesaj Yönetimi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Gruptaki mesajları bu bölümden yönetebilirsin.\n\n"
        "📌 <b>Mesaj Sabitle</b> — Gruba gidip bir mesajı yanıtla, sonra bu butona bas. "
        "Mesaj grubun en üstüne sabitlenir ve tüm üyeler görebilir.\n"
        "📌 <b>Sabitlemeyi Kaldır</b> — Aktif sabitlenmiş mesajı kaldırır.\n"
        "🗑️ <b>Mesaj Sil</b> — Belirli bir mesajı grubun içinden kaldırır.\n"
        "🧹 <b>Son N Mesajı Sil</b> — İstediğin kadar mesajı toplu siler. "
        "Kaç mesaj sileceğini girdikten sonra <b>onay butonu</b> gelir.\n"
        "💣 <b>Son 100 Mesajı Sil</b> — Grubun son 100 mesajını tek seferde temizler. "
        "Onay gerektirir, geri alınamaz!\n"
        "⏩ <b>Şu Mesajdan Sonrasını Sil</b> — Grupta bir mesajı <b>yanıtlayıp</b> "
        "<code>/purgefrom</code> yaz. O mesajdan en sona kadar her şey silinir. "
        "Panelden de başlatabilirsin — bot seni grupta reply yapmaya yönlendirir.\n"
        "📣 <b>Duyuru Gönder</b> — Gruba resmi formatta bir duyuru mesajı gönderir.\n"
        "📊 <b>Anket Oluştur</b> — Grup içinde interaktif bir anket başlatır. "
        "Kullanım: <code>Soru?|Seçenek1|Seçenek2|Seçenek3</code>\n\n"
        "⚠️ <b>Dikkat:</b> Silme işlemleri geri alınamaz! Onay butonları tam bu yüzden var."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📌 Mesaj Sabitle",      callback_data="act_pin"),
            InlineKeyboardButton("📌 Sabitlemeyi Kaldır", callback_data="act_unpin"),
        ],
        [
            InlineKeyboardButton("🗑️ Tek Mesaj Sil",     callback_data="act_delete"),
            InlineKeyboardButton("🧹 Son N Mesajı Sil",   callback_data="act_purge_ask"),
        ],
        [
            InlineKeyboardButton("💣 Son 100 Mesajı Sil", callback_data="act_clearall"),
            InlineKeyboardButton("⏩ Mesajdan Sonrasını Sil", callback_data="act_purge_after"),
        ],
        [
            InlineKeyboardButton("📣 Duyuru Gönder",      callback_data="act_broadcast"),
            InlineKeyboardButton("📊 Anket Oluştur",      callback_data="act_poll"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def settings_menu() -> tuple[str, InlineKeyboardMarkup]:
    lock_icon = "🔓 Grubu Aç" if group_locked else "🔒 Grubu Kilitle"
    lock_cb   = "act_unlock" if group_locked else "act_lock"
    flood_icon = "🌊 Anti-Flood: ✅" if antiflood_on else "🌊 Anti-Flood: ❌"
    text = (
        "⚙️ <b>Grup Ayarları</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Grubun genel davranışını bu bölümden özelleştirebilirsin.\n\n"
        "👋 <b>Karşılama Mesajı</b> — Gruba yeni üye katıldığında otomatik gönderilir. "
        "Metinde <code>{name}</code> (üye adı), <code>{group}</code> (grup adı), "
        "<code>{id}</code> (kullanıcı ID) kullanabilirsin.\n"
        "🔒 <b>Grubu Kilitle</b> — Sadece adminlerin yazabildiği mod. Etkinleşince "
        "normal üyeler mesaj gönderemez. Duyuru/önemli an için ideal.\n"
        "🔓 <b>Grubu Aç</b> — Kilidi kaldırır, herkes tekrar yazabilir.\n"
        "🐌 <b>Yavaş Mod</b> — Üyeler arasına saniye cinsinden bekleme ekler. "
        "Örn: 30 saniye → her üye 30 saniyede bir mesaj atabilir.\n"
        "⏱️ <b>Otomatik Mesaj Silme</b> — Her mesaj belirtilen süre sonra otomatik silinir. "
        "0 girerek kapatabilirsin. Spam'e karşı çok etkili!\n"
        f"🌊 <b>Anti-Flood</b> — Şu an: <b>{'Aktif ✅' if antiflood_on else 'Pasif ❌'}</b>. "
        "10 saniye içinde 5'ten fazla mesaj atan üyeyi otomatik 5 dakika susturur.\n"
        "🔗 <b>Yeni Davet Linki</b> — Mevcut linki geçersiz kılar, yeni link oluşturur.\n\n"
        f"📌 <b>Mevcut Durum:</b> Kilit: {'🔒 Kilitli' if group_locked else '🔓 Açık'} | "
        f"Yavaş mod: {slowmode_sec}sn | Otomatik silme: {auto_delete_sec}sn"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👋 Karşılama Mesajı Ayarla", callback_data="act_setwelcome"),
        ],
        [
            InlineKeyboardButton(lock_icon,                     callback_data=lock_cb),
            InlineKeyboardButton("🐌 Yavaş Mod Ayarla",        callback_data="act_slowmode"),
        ],
        [
            InlineKeyboardButton("⏱️ Otomatik Silme Süresi",   callback_data="act_autodelete"),
            InlineKeyboardButton(flood_icon,                    callback_data="act_toggle_flood"),
        ],
        [
            InlineKeyboardButton("🔗 Yeni Davet Linki Oluştur", callback_data="act_newlink"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def security_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🛡️ <b>Güvenlik & Filtreler</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Grubunu istenmeyen içeriklerden korumak için filtreler ve otomatik önlemler.\n\n"
        "🚫 <b>Yasaklı Kelime Ekle</b> — Eklediğin kelimeyi içeren her mesaj otomatik silinir "
        "ve kullanıcıya uyarı mesajı gönderilir. Küçük/büyük harf fark etmez.\n"
        "✅ <b>Yasaklı Kelime Sil</b> — Listeden bir kelimeyi kaldırır.\n"
        "📋 <b>Yasaklı Kelime Listesi</b> — Aktif tüm filtre kelimelerini listeler.\n\n"
        "🤖 <b>Otomatik Güvenlik Sistemleri:</b>\n\n"
        "   🌊 <b>Anti-Flood</b> — 10 saniye içinde 5'ten fazla mesaj gönderen üye "
        "otomatik olarak 5 dakika susturulur. Bot sana bildirim gönderir.\n\n"
        "   ⚠️ <b>Uyarı Sistemi</b> — Uyarılar birikir. Bir kullanıcı 3 uyarıya ulaşırsa "
        "sistem otomatik olarak banlar. Manuel müdahaleye gerek kalmaz.\n\n"
        "   🔤 <b>Kelime Filtresi</b> — Yasaklı kelime içeren mesaj silinir, kullanıcı "
        "uyarılır, sen bildirim alırsın.\n\n"
        "   👤 <b>Yeni Üye Bildirimi</b> — Birisi gruba katıldığında anında DM bildirimi "
        "alırsın: kim katıldı, ID'si nedir.\n\n"
        f"📊 <b>Aktif Filtre Sayısı:</b> {len(banned_words)} kelime"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Kelime Filtresi Ekle",   callback_data="act_addban"),
            InlineKeyboardButton("✅ Filtre Sil",              callback_data="act_removeban"),
        ],
        [
            InlineKeyboardButton("📋 Filtre Listesini Gör",   callback_data="act_listban"),
        ],
        [
            InlineKeyboardButton(
                f"🌊 Anti-Flood: {'✅ Aktif' if antiflood_on else '❌ Pasif'} → Değiştir",
                callback_data="act_toggle_flood"
            ),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def notes_menu() -> tuple[str, InlineKeyboardMarkup]:
    note_count = len(notes)
    note_list  = ", ".join(f"#{k}" for k in list(notes.keys())[:10]) or "Henüz not yok"
    text = (
        "📝 <b>Not Sistemi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Sık kullandığın metinleri, kuralları, linkleri not olarak kaydet. "
        "İstediğinde tek komutla gruba gönder!\n\n"
        "💾 <b>Not Kaydet</b> — Bot senden not adı ve içeriğini isteyecek. "
        "Kaydettikten sonra grupta <code>#notadı</code> yazarak veya "
        "<code>/note notadı</code> komutuyla gösterebilirsin.\n\n"
        "📖 <b>Notu Gruba Gönder</b> — Seçtiğin notu direkt gruba iletir. "
        "Kurallar, duyurular veya sık sorulan sorular için süper pratik!\n\n"
        "📋 <b>Tüm Notları Listele</b> — Kayıtlı tüm notların adlarını görürsün.\n\n"
        "🗑️ <b>Not Sil</b> — Artık kullanmadığın bir notu listeden kaldırır.\n\n"
        "💡 <b>Kısayol:</b> Grupta herhangi biri <code>#notadı</code> yazarsa "
        "bot otomatik olarak o notu yanıt olarak gönderir!\n\n"
        f"📊 <b>Kayıtlı Not Sayısı:</b> {note_count}\n"
        f"📌 <b>Notlar:</b> {note_list}"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Yeni Not Kaydet",        callback_data="act_savenote"),
            InlineKeyboardButton("📖 Notu Gruba Gönder",      callback_data="act_sendnote"),
        ],
        [
            InlineKeyboardButton("📋 Tüm Notları Listele",    callback_data="act_notes"),
            InlineKeyboardButton("🗑️ Not Sil",               callback_data="act_deletenote"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def info_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📊 <b>Bilgi & İstatistik</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Grup ve kullanıcılar hakkında detaylı bilgiye buradan ulaşabilirsin.\n\n"
        "👤 <b>Kullanıcı Bilgisi</b> — Bir kullanıcının Telegram adı, ID, kullanıcı adı, "
        "gruptaki rolü (üye/admin/banlı) ve kaç uyarı aldığını gösterir.\n\n"
        "🏘️ <b>Grup Bilgisi</b> — Grubun adı, ID'si, üye sayısı, açıklaması, "
        "davet linki, kilit durumu ve yavaş mod ayarlarını gösterir.\n\n"
        "👥 <b>Üye Sayısı</b> — Grubun anlık üye sayısını hızlıca sorgular.\n\n"
        "📈 <b>Bot İstatistikleri</b> — Botun bu oturumda yaptıklarının özeti: "
        "toplam işlenen mesaj sayısı, silinen mesajlar, banlanan kullanıcılar, "
        "uyarılan kullanıcılar, aktif filtreler, kayıtlı notlar ve tüm ayarların durumu.\n\n"
        "🆔 <b>ID Göster</b> — Kendi Telegram ID'ni ve o andaki chat ID'sini gösterir. "
        "Bot kurulumunda GROUP_ID bulmak için kullanışlıdır."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Kullanıcı Bilgisi", callback_data="act_info"),
            InlineKeyboardButton("🏘️ Grup Bilgisi",     callback_data="act_groupinfo"),
        ],
        [
            InlineKeyboardButton("👥 Üye Sayısı",        callback_data="act_membercount"),
            InlineKeyboardButton("📈 Bot İstatistikleri", callback_data="act_stats"),
        ],
        [
            InlineKeyboardButton("🆔 ID Göster",         callback_data="act_id"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

# ──────────────────────────────────────────────────────────────
# ACTİON AÇIKLAMALARI (kullanıcıya bilgi ister)
# ──────────────────────────────────────────────────────────────
ACTION_PROMPTS = {
    "act_ban"       : "🔨 <b>Kullanıcı Banla</b>\n\nBanlamak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n(Opsiyonel: ID'nin ardından boşluk bırakıp neden yazabilirsin)\n\nÖrnek: <code>123456789 spam yapıyor</code>",
    "act_unban"     : "✅ <b>Ban Kaldır</b>\n\nBanını kaldırmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_kick"      : "👢 <b>Kullanıcı At</b>\n\nAtmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n(Kullanıcı daha sonra tekrar girebilir)\n\nÖrnek: <code>123456789</code>",
    "act_mute"      : "🔇 <b>Kullanıcı Sustur</b>\n\nSusturmak istediğin kullanıcının <b>ID ve dakika süresini</b> gönder.\n\nÖrnek: <code>123456789 30</code>\n(Süre girmezsen varsayılan 60 dakika uygulanır)",
    "act_unmute"    : "🔊 <b>Sesi Aç</b>\n\nSusturmasını kaldırmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_warn"      : "⚠️ <b>Uyarı Ver</b>\n\nUyarmak istediğin kullanıcının <b>ID ve uyarı nedenini</b> gönder.\n⚡ 3 uyarıda otomatik ban!\n\nÖrnek: <code>123456789 kurallara uymadı</code>",
    "act_unwarn"    : "🔄 <b>Uyarı Sıfırla</b>\n\nUyarılarını sıfırlamak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_warnings"  : "📊 <b>Uyarı Sorgula</b>\n\nUyarılarını sorgulamak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_promote"   : "⬆️ <b>Admin Yap</b>\n\nAdmin yapmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\nKullanıcıya: mesaj silme, üye kısıtlama, mesaj sabitleme yetkileri verilecek.\n\nÖrnek: <code>123456789</code>",
    "act_demote"    : "⬇️ <b>Admin'den Al</b>\n\nYetkilerini iptal etmek istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_info"      : "👤 <b>Kullanıcı Bilgisi</b>\n\nBilgilerini görmek istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_pin"       : "📌 <b>Mesaj Sabitle</b>\n\nSabitlemek istediğin mesajın <b>mesaj ID'sini</b> gönder.\n\n💡 Gruba git, mesajın üzerine tıkla → Detaylar → Message ID'yi kopyala.\n\nÖrnek: <code>1234</code>",
    "act_delete"    : "🗑️ <b>Mesaj Sil</b>\n\nSilmek istediğin mesajın <b>mesaj ID'sini</b> gönder.\n\n💡 Gruba git, mesajın üzerine tıkla → Detaylar → Message ID'yi kopyala.\n\nÖrnek: <code>1234</code>",
    "act_purge_ask"  : "🧹 <b>Son N Mesajı Sil</b>\n\nKaç mesaj silmek istediğini yaz.\n📌 Maksimum: 200 mesaj\n⚠️ Bu işlem geri alınamaz!\n\nÖrnek: <code>20</code>\n\n10, 20, 50, 100 gibi bir sayı gir:",
    "act_purge_after": "⏩ <b>Şu Mesajdan Sonrasını Sil</b>\n\nGruba git, silmenin başlamasını istediğin mesajı <b>yanıtla (reply)</b> ve şunu yaz:\n\n<code>/purgefrom</code>\n\nBot onay isteyecek, onayladıktan sonra o mesajdan en sona kadar her şey silinecek.",
    "act_broadcast" : "📣 <b>Gruba Duyuru Gönder</b>\n\nDuyuru metnini yaz. Mesaj resmi duyuru formatında (<b>DUYURU</b> başlığıyla) gruba gönderilecek.\n\nHTML etiketlerini kullanabilirsin: <code>&lt;b&gt;kalın&lt;/b&gt;</code>, <code>&lt;i&gt;italik&lt;/i&gt;</code>\n\nDuyuru metni:",
    "act_poll"      : "📊 <b>Anket Oluştur</b>\n\nSoru ve seçenekleri <b>| (boru çizgisi)</b> ile ayırarak gönder.\nEn az 2, en fazla 10 seçenek ekleyebilirsin.\n\nFormat: <code>Soru?|Seçenek1|Seçenek2|Seçenek3</code>\n\nÖrnek: <code>En sevdiğiniz dil hangisi?|Python|JavaScript|Go|Rust</code>",
    "act_setwelcome": "👋 <b>Karşılama Mesajı Ayarla</b>\n\nYeni karşılama metnini yaz. HTML formatı desteklenir.\n\n🔑 <b>Kullanılabilir değişkenler:</b>\n• <code>{name}</code> → Üyenin adı\n• <code>{id}</code> → Üyenin ID'si\n• <code>{group}</code> → Grubun adı\n\nÖrnek:\n<code>Merhaba {name}! Grubumuz {group}'a hoş geldin! 🎉</code>",
    "act_slowmode"  : "🐌 <b>Yavaş Mod Ayarla</b>\n\nKaç saniyelik yavaş mod istiyorsun? Sıfır (0) girerek kapatabilirsin.\n\n📌 Önerilen değerler:\n• <code>0</code> → Kapat\n• <code>10</code> → 10 saniye\n• <code>30</code> → 30 saniye\n• <code>60</code> → 1 dakika\n\nSaniye cinsinden değer:",
    "act_autodelete": "⏱️ <b>Otomatik Mesaj Silme</b>\n\nKaç saniye sonra mesajlar otomatik silinsin? Sıfır (0) girerek kapatabilirsin.\n\n📌 Önerilen değerler:\n• <code>0</code> → Kapat\n• <code>3600</code> → 1 saat\n• <code>86400</code> → 1 gün\n• <code>604800</code> → 1 hafta\n\nSaniye cinsinden değer:",
    "act_addban"    : "🚫 <b>Yasaklı Kelime Ekle</b>\n\nFiltrelemek istediğin kelimeyi yaz.\n\n⚠️ Bu kelimeyi içeren her mesaj otomatik silinecek ve kullanıcı uyarılacak!\n\nBirden fazla kelime için ayrı ayrı gönderebilirsin.\n\nKelimeyi yaz:",
    "act_removeban" : "✅ <b>Yasaklı Kelime Kaldır</b>\n\nListeden kaldırmak istediğin kelimeyi yaz.\n\nMevcut kelimeler: " + (", ".join(f"<code>{w}</code>" for w in banned_words) or "Liste boş"),
    "act_savenote"  : "💾 <b>Not Kaydet</b>\n\nÖnce not adını, sonra bir boşluk bırakıp içeriğini yaz.\n\nFormat: <code>notadı Not içeriği buraya</code>\n\nÖrnek: <code>kurallar 1. Saygılı ol 2. Spam yapma 3. Reklam yasak</code>",
    "act_sendnote"  : "📖 <b>Notu Gruba Gönder</b>\n\nGruba göndermek istediğin notun adını yaz.\n\nMevcut notlar: " + (", ".join(f"<code>#{k}</code>" for k in list(notes.keys())[:15]) or "Henüz not yok"),
    "act_deletenote": "🗑️ <b>Not Sil</b>\n\nSilmek istediğin notun adını yaz.\n\nMevcut notlar: " + (", ".join(f"<code>#{k}</code>" for k in list(notes.keys())[:15]) or "Henüz not yok"),
}

# ──────────────────────────────────────────────────────────────
# /start  /help
# ──────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        # Sadece admin kullanabilir, diğerleri sessizce görmezden gel
        if not is_admin(user.id):
            try:
                await update.message.delete()  # komutu sil ki kalabalık olmasın
            except TelegramError:
                pass
            return
        # Admin grupta /start yazdıysa DM'e yönlendir
        m = await update.message.reply_text(
            "🤖 Yönetim paneli için DM'e geç 👉 @me",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat.id, m.message_id, 8))
        asyncio.create_task(auto_delete(ctx, chat.id, update.message.message_id, 8))
        return

    if not is_admin(user.id):
        await update.message.reply_text("⛔ Bu bot yalnızca grup sahibi tarafından kullanılabilir.")
        return

    await update.message.reply_text(MAIN_MENU_TEXT, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not is_admin(update.effective_user.id): return
        await update.message.reply_text(
            "📋 <b>Tüm Komutlar</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>👥</b> /ban /unban /kick /mute /unmute /warn /unwarn /promote /demote\n"
            "<b>📢</b> /pin /unpin /delete /purge [n] /clearall /broadcast /poll\n"
            "<b>⚙️</b> /lock /unlock /slowmode /setwelcome /autodelete /antiflood /newlink\n"
            "<b>🛡️</b> /addban /removeban /listban\n"
            "<b>📝</b> /savenote /note /notes /deletenote\n"
            "<b>📊</b> /info /groupinfo /membercount /stats /id\n\n"
            "💡 DM'den /start ile görsel paneli kullan!",
            parse_mode=ParseMode.HTML,
        )
    else:
        await cmd_start(update, ctx)

# ──────────────────────────────────────────────────────────────
# CALLBACK HANDLER — Ana yönlendirici
# ──────────────────────────────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    uid  = q.from_user.id
    await q.answer()

    if not is_admin(uid):
        await q.answer("⛔ Yetkisiz erişim!", show_alert=True)
        return

    # ── Menü navigasyonu ────────────────────────────────────
    if data == "menu_main":
        await q.message.edit_text(MAIN_MENU_TEXT, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        return

    if data == "menu_users":
        txt, kb = users_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_msgs":
        txt, kb = msgs_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_settings":
        txt, kb = settings_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_security":
        txt, kb = security_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_notes":
        txt, kb = notes_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_info":
        txt, kb = info_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_broadcast":
        pending[uid] = {"action": "act_broadcast"}
        await q.message.edit_text(
            ACTION_PROMPTS["act_broadcast"],
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="menu_main")]]),
        )
        return

    # ── Anlık işlemler (girdi gerektirmez) ──────────────────
    if data == "act_unpin":
        await _exec_unpin(q.message, ctx)
        return

    if data == "act_lock":
        await _exec_lock(q.message, ctx, lock=True)
        return

    if data == "act_unlock":
        await _exec_lock(q.message, ctx, lock=False)
        return

    if data == "act_toggle_flood":
        global antiflood_on
        antiflood_on = not antiflood_on
        status = "✅ Aktif" if antiflood_on else "❌ Pasif"
        await q.answer(f"Anti-Flood şimdi: {status}", show_alert=True)
        # Menüyü yenile
        txt, kb = settings_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "act_newlink":
        try:
            link = await ctx.bot.export_chat_invite_link(GROUP_ID)
            await q.message.reply_text(
                f"🔗 <b>Yeni Davet Linki Oluşturuldu</b>\n\n"
                f"Eski link artık geçersiz.\nYeni link:\n{link}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await q.message.reply_text(f"❌ Hata: {e}")
        return

    if data == "act_listban":
        if not banned_words:
            await q.message.reply_text("📋 Yasaklı kelime listesi boş.")
        else:
            await q.message.reply_text(
                "📋 <b>Aktif Kelime Filtreleri</b>\n━━━━━━━━━━━━━━━━\n" +
                "\n".join(f"🚫 <code>{w}</code>" for w in banned_words),
                parse_mode=ParseMode.HTML,
            )
        return

    if data == "act_notes":
        if not notes:
            await q.message.reply_text("📋 Kayıtlı not bulunamadı.")
        else:
            await q.message.reply_text(
                "📝 <b>Kayıtlı Notlar</b>\n━━━━━━━━━━━━━━━━\n" +
                "\n".join(f"• <code>#{k}</code>" for k in notes.keys()) +
                "\n\n💡 Grupta <code>#notadı</code> yazarak gösterebilirsin.",
                parse_mode=ParseMode.HTML,
            )
        return

    if data == "act_groupinfo":
        await _exec_groupinfo(q.message, ctx)
        return

    if data == "act_membercount":
        try:
            count = await ctx.bot.get_chat_member_count(GROUP_ID)
            await q.message.reply_text(f"👥 Anlık üye sayısı: <b>{count}</b>", parse_mode=ParseMode.HTML)
        except TelegramError as e:
            await q.message.reply_text(f"❌ Hata: {e}")
        return

    if data == "act_stats":
        await _exec_stats(q.message)
        return

    if data == "act_id":
        await q.message.reply_text(
            f"🆔 <b>ID Bilgisi</b>\n\n"
            f"👤 Senin ID'n: <code>{uid}</code>\n"
            f"💬 Grup ID: <code>{GROUP_ID}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "act_clearall":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Evet, 100 Mesajı Sil!", callback_data="clearall_confirm"),
            InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
        ]])
        await q.message.edit_text(
            "⚠️ <b>UYARI — Toplu Mesaj Silme</b>\n\n"
            "Grubun son <b>100 mesajını</b> silmek üzeresin.\n\n"
            "• Bu işlem <b>geri alınamaz!</b>\n"
            "• Bot her mesajı tek tek siler, bu birkaç saniye sürebilir.\n"
            "• İşlem sırasında grupta mesaj atmana gerek yok.\n\n"
            "Devam etmek istiyor musun?",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    if data == "clearall_confirm":
        await q.message.edit_text("🗑️ Silme işlemi başladı, lütfen bekleyin...")
        try:
            sentinel = await ctx.bot.send_message(GROUP_ID, "🧹")
            last_id  = sentinel.message_id
            await ctx.bot.delete_message(GROUP_ID, last_id)
        except TelegramError as e:
            await q.message.edit_text(f"❌ Grup mesaj ID alınamadı: {e}")
            return
        deleted = await _bulk_delete(ctx, GROUP_ID, last_id - 1, last_id - 100)
        stats["deleted_messages"] += deleted
        await q.message.edit_text(
            f"✅ Tamamlandı! <b>{deleted}</b> mesaj silindi.\n<i>(Erişilemeyen mesajlar atlandı)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Purge N mesaj onay
    if data.startswith("purge_confirm:"):
        parts = data.split(":")
        n = int(parts[1])
        await q.message.edit_text(f"🧹 Son <b>{n}</b> mesaj siliniyor...", parse_mode=ParseMode.HTML)
        try:
            sentinel = await ctx.bot.send_message(GROUP_ID, "🧹")
            last_id  = sentinel.message_id
            await ctx.bot.delete_message(GROUP_ID, last_id)
        except TelegramError as e:
            await q.message.edit_text(f"❌ Grup mesaj ID alınamadı: {e}")
            return
        deleted = await _bulk_delete(ctx, GROUP_ID, last_id - 1, last_id - n)
        stats["deleted_messages"] += deleted
        await q.message.edit_text(
            f"✅ <b>{deleted}</b> mesaj silindi.\n<i>(Bazı mesajlar zaten silinmiş veya erişilemez olabilir)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Purge after (şu mesajdan sonrasını sil) onay
    if data.startswith("purge_after_confirm:"):
        from_id = int(data.split(":")[1])
        await q.message.edit_text(f"⏩ Mesaj <code>{from_id}</code>'den itibaren siliniyor...", parse_mode=ParseMode.HTML)
        try:
            sentinel = await ctx.bot.send_message(GROUP_ID, "🧹")
            last_id  = sentinel.message_id
            await ctx.bot.delete_message(GROUP_ID, last_id)
        except TelegramError as e:
            await q.message.edit_text(f"❌ Grup mesaj ID alınamadı: {e}")
            return
        deleted = await _bulk_delete(ctx, GROUP_ID, last_id - 1, from_id)
        stats["deleted_messages"] += deleted
        await q.message.edit_text(
            f"✅ <b>{deleted}</b> mesaj silindi.\n<i>(Mesaj {from_id}'den en sona kadar)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "purgefrom_cancel":
        await q.message.delete()
        return

    if data == "rules":
        await q.message.reply_text(
            "📋 <b>Grup Kuralları</b>\n━━━━━━━━━━━━━━━━\n"
            "1️⃣ Saygılı ve nazik olun\n"
            "2️⃣ Spam ve flood yapmayın\n"
            "3️⃣ Reklam ve tanıtım yasaktır\n"
            "4️⃣ Hakaret ve küfür yasaktır\n"
            "5️⃣ Kurallara uymayanlar banlanır",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Girdi gerektiren işlemler → pending'e ekle ──────────
    if data in ACTION_PROMPTS:
        pending[uid] = {"action": data}
        await q.message.edit_text(
            ACTION_PROMPTS[data],
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="menu_main")]]),
        )
        return

# ──────────────────────────────────────────────────────────────
# DM METİN HANDLER — pending işlemleri işle
# ──────────────────────────────────────────────────────────────
async def handle_dm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = (update.message.text or update.message.caption or "").strip()

    if not is_admin(uid):
        return

    # > ile gruba mesaj ilet (forward mesajlarda çalışmasın)
    if text.startswith(">") and not update.message.forward_date and not getattr(update.message, "forward_origin", None):
        msg = text[1:].strip()
        if msg:
            try:
                await ctx.bot.send_message(
                    GROUP_ID,
                    f"📢 <b>Yönetici Mesajı</b>\n━━━━━━━━━━━━\n{msg}",
                    parse_mode=ParseMode.HTML,
                )
                await update.message.reply_text("✅ Mesaj gruba iletildi.")
            except TelegramError as e:
                await update.message.reply_text(f"❌ Hata: {e}")
        return

    # Bekleyen işlem var mı?
    if uid not in pending:
        await update.message.reply_text(
            "💡 Paneli açmak için /start — Gruba mesaj iletmek için: <code>&gt; mesajın</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return

    action = pending[uid]["action"]

    # act_purge_after için iletilen mesajın ID'sini otomatik çek
    if action == "act_purge_after":
        fwd_msg_id = None
        fwd_err    = None
        m = update.message

        # Yeni API: forward_origin (python-telegram-bot 20+)
        origin = getattr(m, "forward_origin", None)
        if origin:
            otype = getattr(origin, "type", None) or type(origin).__name__

            # Kanal veya gruptan iletilmiş → message_id var
            msg_id  = getattr(origin, "message_id", None)
            chat    = getattr(origin, "chat", None)
            chat_id = getattr(chat, "id", None) if chat else None

            if msg_id:
                if chat_id and chat_id != GROUP_ID:
                    fwd_err = f"⚠️ Bu mesaj <b>farklı bir kanaldan/gruptan</b> iletilmiş (ID: <code>{chat_id}</code>).\nLütfen <b>hedef grubunuzdaki</b> bir mesajı iletin."
                else:
                    fwd_msg_id = msg_id
            else:
                fwd_err = "⚠️ Bu mesaj bir <b>kullanıcıdan</b> iletilmiş, gruba ait mesaj ID'si alınamıyor.\nLütfen <b>grup/kanal mesajını</b> iletin."

        # Eski API fallback: forward_from_chat
        elif getattr(m, "forward_from_chat", None) and getattr(m, "forward_from_message_id", None):
            if m.forward_from_chat.id == GROUP_ID:
                fwd_msg_id = m.forward_from_message_id
            else:
                fwd_err = f"⚠️ Bu mesaj farklı bir gruptan iletilmiş.\nLütfen hedef grubunuzdaki bir mesajı iletin."

        elif getattr(m, "forward_date", None):
            fwd_err = "⚠️ Mesaj ID'si alınamadı.\nLütfen grubunuzdaki bir mesajı bota iletin."

        if fwd_msg_id:
            del pending[uid]
            await _process_action(update, ctx, action, str(fwd_msg_id))
            return
        elif fwd_err:
            await m.reply_text(fwd_err, parse_mode=ParseMode.HTML)
            return
        elif not m.text or not m.text.strip().isdigit():
            # Ne forward ne de sayı — pending'i koruyup tekrar sor
            await m.reply_text(
                "⚠️ Mesaj algılanamadı.\n\n"
                "Grupta bir mesajı <b>İlet (Forward)</b> yapıp bota gönderin,\n"
                "ya da mesaj ID'sini rakam olarak yazın.",
                parse_mode=ParseMode.HTML,
            )
            return
        # Düz sayı olarak yazıldıysa normal akışa devam

    del pending[uid]

    # ── İşlemleri yürüt ─────────────────────────────────────
    await _process_action(update, ctx, action, text)

async def _process_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE, action: str, text: str):
    """Kullanıcıdan gelen girdiyi işle ve ilgili bot işlemini yürüt."""
    msg = update.message

    # Kullanıcı ID çözümleyici
    async def get_uid_and_rest(default_reason="Belirtilmedi"):
        parts = text.strip().split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            await msg.reply_text("❌ Geçersiz format. Lütfen bir <b>kullanıcı ID</b> gir.", parse_mode=ParseMode.HTML)
            return None, None
        return int(parts[0]), parts[1] if len(parts) > 1 else default_reason

    # ── BAN ─────────────────────────────────────────────────
    if action == "act_ban":
        uid, reason = await get_uid_and_rest("Belirtilmedi")
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.ban_chat_member(GROUP_ID, uid)
            stats["banned_users"] += 1
            await msg.reply_text(
                f"🔨 <b>Kullanıcı Banlandı</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 Kullanıcı: {fmt(member.user)}\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"📝 Neden: {reason}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── UNBAN ───────────────────────────────────────────────
    elif action == "act_unban":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            await ctx.bot.unban_chat_member(GROUP_ID, uid)
            await msg.reply_text(
                f"✅ <b>Ban Kaldırıldı</b>\n\n"
                f"🆔 ID <code>{uid}</code> numaralı kullanıcının yasağı kaldırıldı. "
                f"Artık gruba tekrar katılabilir.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── KICK ────────────────────────────────────────────────
    elif action == "act_kick":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.ban_chat_member(GROUP_ID, uid)
            await ctx.bot.unban_chat_member(GROUP_ID, uid)
            await msg.reply_text(
                f"👢 <b>Kullanıcı Atıldı</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 Kullanıcı: {fmt(member.user)}\n"
                f"🆔 ID: <code>{uid}</code>\n\n"
                f"ℹ️ Kullanıcı davet linki ile tekrar girebilir.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── MUTE ────────────────────────────────────────────────
    elif action == "act_mute":
        parts = text.strip().split()
        if not parts or not parts[0].isdigit():
            await msg.reply_text("❌ Geçersiz format. Örnek: <code>123456789 30</code>", parse_mode=ParseMode.HTML)
            return
        uid     = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
        until   = datetime.now() + timedelta(minutes=minutes)
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.restrict_chat_member(
                GROUP_ID, uid,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            muted_users[uid] = until
            await msg.reply_text(
                f"🔇 <b>Kullanıcı Susturuldu</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 Kullanıcı: {fmt(member.user)}\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"⏱️ Süre: {minutes} dakika\n"
                f"🕐 Bitiş: {until.strftime('%H:%M, %d.%m.%Y')}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── UNMUTE ──────────────────────────────────────────────
    elif action == "act_unmute":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.restrict_chat_member(
                GROUP_ID, uid,
                permissions=ChatPermissions(
                    can_send_messages=True, can_send_media_messages=True,
                    can_send_other_messages=True, can_add_web_page_previews=True,
                ),
            )
            muted_users.pop(uid, None)
            await msg.reply_text(
                f"🔊 <b>Susturma Kaldırıldı</b>\n\n"
                f"👤 {fmt(member.user)} artık mesaj gönderebilir.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── WARN ────────────────────────────────────────────────
    elif action == "act_warn":
        uid, reason = await get_uid_and_rest("Kural ihlali")
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            warnings_db[uid] = warnings_db.get(uid, 0) + 1
            count = warnings_db[uid]
            stats["warned_users"] += 1
            if count >= 3:
                await ctx.bot.ban_chat_member(GROUP_ID, uid)
                stats["banned_users"] += 1
                warnings_db.pop(uid, None)
                await msg.reply_text(
                    f"🔨 <b>Otomatik Ban!</b>\n━━━━━━━━━━━━━━━━\n"
                    f"👤 {fmt(member.user)} 3 uyarıya ulaştı ve <b>otomatik olarak banlandı!</b>\n"
                    f"📝 Son neden: {reason}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await msg.reply_text(
                    f"⚠️ <b>Uyarı Verildi</b>\n━━━━━━━━━━━━━━━━\n"
                    f"👤 Kullanıcı: {fmt(member.user)}\n"
                    f"📊 Uyarı sayısı: <b>{count}/3</b>\n"
                    f"📝 Neden: {reason}\n\n"
                    f"{'⚡ Bir daha uyarılırsa otomatik ban!' if count == 2 else f'Toplam {3 - count} uyarı hakkı kaldı.'}",
                    parse_mode=ParseMode.HTML,
                )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── UNWARN ──────────────────────────────────────────────
    elif action == "act_unwarn":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        prev = warnings_db.pop(uid, 0)
        await msg.reply_text(
            f"🔄 <b>Uyarılar Sıfırlandı</b>\n\n"
            f"🆔 ID <code>{uid}</code> numaralı kullanıcının {prev} uyarısı temizlendi.",
            parse_mode=ParseMode.HTML,
        )

    # ── WARNINGS ────────────────────────────────────────────
    elif action == "act_warnings":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        count = warnings_db.get(uid, 0)
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            uname  = fmt(member.user)
        except Exception:
            uname  = f"ID <code>{uid}</code>"
        await msg.reply_text(
            f"📊 <b>Uyarı Durumu</b>\n━━━━━━━━━━━━━━━━\n"
            f"👤 Kullanıcı: {uname}\n"
            f"⚠️ Uyarı: <b>{count}/3</b>\n\n"
            f"{'🔴 Bir uyarı daha alırsa otomatik ban!' if count == 2 else '🟢 Sorunsuz.' if count == 0 else '🟡 Dikkat gerekiyor.'}",
            parse_mode=ParseMode.HTML,
        )

    # ── PROMOTE ─────────────────────────────────────────────
    elif action == "act_promote":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.promote_chat_member(
                GROUP_ID, uid,
                can_delete_messages=True, can_restrict_members=True,
                can_pin_messages=True, can_manage_chat=True,
            )
            await msg.reply_text(
                f"⬆️ <b>Admin Yapıldı</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 {fmt(member.user)} artık grup yöneticisi.\n\n"
                f"✅ Verilen yetkiler: Mesaj silme, Üye kısıtlama, Mesaj sabitleme, Grubu yönetme",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── DEMOTE ──────────────────────────────────────────────
    elif action == "act_demote":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.promote_chat_member(
                GROUP_ID, uid,
                can_delete_messages=False, can_restrict_members=False,
                can_pin_messages=False, can_manage_chat=False,
            )
            await msg.reply_text(
                f"⬇️ <b>Yetkiler Alındı</b>\n\n"
                f"👤 {fmt(member.user)} artık normal üye statüsünde.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── INFO ────────────────────────────────────────────────
    elif action == "act_info":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            u = member.user
            status_map = {
                "creator": "👑 Kurucu", "administrator": "🛡️ Admin",
                "member": "👤 Üye", "restricted": "⛔ Kısıtlı",
                "left": "🚪 Ayrıldı", "kicked": "🔨 Banlı",
            }
            status = status_map.get(member.status, member.status)
            await msg.reply_text(
                f"👤 <b>Kullanıcı Profili</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 Ad: {fmt(u)}\n"
                f"🆔 ID: <code>{u.id}</code>\n"
                f"📛 Kullanıcı adı: @{u.username or 'Yok'}\n"
                f"📊 Grup rolü: {status}\n"
                f"⚠️ Uyarılar: {warnings_db.get(u.id, 0)}/3\n"
                f"🤖 Bot hesabı: {'Evet' if u.is_bot else 'Hayır'}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── PIN ─────────────────────────────────────────────────
    elif action == "act_pin":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>mesaj ID</b> gir.", parse_mode=ParseMode.HTML)
            return
        try:
            await ctx.bot.pin_chat_message(GROUP_ID, int(text.strip()))
            await msg.reply_text("📌 Mesaj sabitlendi.")
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── DELETE ──────────────────────────────────────────────
    elif action == "act_delete":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>mesaj ID</b> gir.", parse_mode=ParseMode.HTML)
            return
        try:
            await ctx.bot.delete_message(GROUP_ID, int(text.strip()))
            stats["deleted_messages"] += 1
            await msg.reply_text("✅ Mesaj silindi.")
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── PURGE ───────────────────────────────────────────────
    elif action == "act_purge_ask":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>sayı</b> gir.", parse_mode=ParseMode.HTML)
            return
        n = min(int(text.strip()), 200)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Evet, {n} mesajı sil!", callback_data=f"purge_confirm:{n}"),
            InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
        ]])
        await msg.reply_text(
            f"⚠️ <b>Onay Gerekiyor</b>\n\n"
            f"Grubun son <b>{n} mesajını</b> silmek üzeresin.\n"
            f"Bu işlem <b>geri alınamaz!</b>\n\nDevam etmek istiyor musun?",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    # ── PURGE AFTER ─────────────────────────────────────────
    elif action == "act_purge_after":
        text_clean = text.strip()
        # Forward edilmiş mesaj ID veya düz sayı kabul et
        if not text_clean.isdigit():
            await msg.reply_text(
                "❌ Geçerli bir <b>mesaj ID'si</b> gir.\n"
                "Örnek: <code>12345</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        from_id = int(text_clean)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ Evet, {from_id}'den itibaren sil!",
                callback_data=f"purge_after_confirm:{from_id}"
            ),
            InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
        ]])
        await msg.reply_text(
            f"⚠️ <b>Onay Gerekiyor</b>\n\n"
            f"Mesaj <code>{from_id}</code>'den başlayarak en son mesaja kadar\n"
            f"<b>tüm mesajlar silinecek.</b>\n\n"
            f"Bu işlem <b>geri alınamaz!</b>\n\nDevam etmek istiyor musun?",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    # ── BROADCAST ───────────────────────────────────────────
    elif action == "act_broadcast":
        try:
            await ctx.bot.send_message(
                GROUP_ID,
                f"📢 <b>DUYURU</b>\n━━━━━━━━━━━━━━━━\n{text}",
                parse_mode=ParseMode.HTML,
            )
            await msg.reply_text("✅ Duyuru başarıyla gruba gönderildi.", reply_markup=main_menu_kb())
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── POLL ────────────────────────────────────────────────
    elif action == "act_poll":
        parts = text.split("|")
        if len(parts) < 3:
            await msg.reply_text("❌ Format: <code>Soru|Seçenek1|Seçenek2</code>", parse_mode=ParseMode.HTML)
            return
        question = parts[0].strip()
        options  = [p.strip() for p in parts[1:] if p.strip()]
        try:
            await ctx.bot.send_poll(GROUP_ID, question, options, is_anonymous=False)
            await msg.reply_text(
                f"✅ <b>Anket Oluşturuldu!</b>\n\n"
                f"❓ Soru: {question}\n"
                f"📊 Seçenek sayısı: {len(options)}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── SETWELCOME ──────────────────────────────────────────
    elif action == "act_setwelcome":
        global welcome_msg
        welcome_msg = text
        await msg.reply_text(
            f"✅ <b>Karşılama Mesajı Güncellendi</b>\n\n"
            f"Yeni mesaj:\n<i>{welcome_msg}</i>",
            parse_mode=ParseMode.HTML,
        )

    # ── SLOWMODE ────────────────────────────────────────────
    elif action == "act_slowmode":
        global slowmode_sec
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>saniye değeri</b> gir.", parse_mode=ParseMode.HTML)
            return
        slowmode_sec = int(text.strip())
        try:
            await ctx.bot.set_chat_slow_mode_delay(GROUP_ID, slowmode_sec)
            status = f"{slowmode_sec} saniye" if slowmode_sec else "Kapalı"
            await msg.reply_text(
                f"🐌 <b>Yavaş Mod Güncellendi</b>\n\n"
                f"Yeni değer: <b>{status}</b>\n"
                f"{'Artık üyeler arasına ' + str(slowmode_sec) + ' saniye bekleme eklenecek.' if slowmode_sec else 'Yavaş mod devre dışı bırakıldı.'}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── AUTODELETE ──────────────────────────────────────────
    elif action == "act_autodelete":
        global auto_delete_sec
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>saniye değeri</b> gir.", parse_mode=ParseMode.HTML)
            return
        auto_delete_sec = int(text.strip())
        status = f"{auto_delete_sec} saniye sonra" if auto_delete_sec else "Kapalı"
        await msg.reply_text(
            f"⏱️ <b>Otomatik Silme Güncellendi</b>\n\n"
            f"Yeni değer: <b>{status}</b>\n"
            f"{'Gruba gelen her mesaj ' + str(auto_delete_sec) + ' saniye sonra otomatik silinecek.' if auto_delete_sec else 'Otomatik silme devre dışı bırakıldı.'}",
            parse_mode=ParseMode.HTML,
        )

    # ── ADDBAN ──────────────────────────────────────────────
    elif action == "act_addban":
        word = text.strip().lower()
        if not word:
            await msg.reply_text("❌ Geçerli bir kelime gir.")
            return
        if word not in banned_words:
            banned_words.append(word)
            await msg.reply_text(
                f"✅ <b>Filtre Eklendi</b>\n\n"
                f"🚫 <code>{word}</code> artık yasaklı kelimeler listesinde.\n"
                f"Bu kelimeyi içeren her mesaj otomatik silinecek.\n\n"
                f"📊 Toplam aktif filtre: {len(banned_words)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await msg.reply_text(f"ℹ️ <code>{word}</code> zaten listede.", parse_mode=ParseMode.HTML)

    # ── REMOVEBAN ───────────────────────────────────────────
    elif action == "act_removeban":
        word = text.strip().lower()
        if word in banned_words:
            banned_words.remove(word)
            await msg.reply_text(
                f"✅ <b>Filtre Kaldırıldı</b>\n\n"
                f"<code>{word}</code> artık filtrelenmeyecek.\n"
                f"Kalan filtre sayısı: {len(banned_words)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await msg.reply_text(f"❌ <code>{word}</code> listede bulunamadı.", parse_mode=ParseMode.HTML)

    # ── SAVENOTE ────────────────────────────────────────────
    elif action == "act_savenote":
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await msg.reply_text("❌ Format: <code>notadı İçerik</code>", parse_mode=ParseMode.HTML)
            return
        name, content = parts[0].lower(), parts[1]
        notes[name] = content
        await msg.reply_text(
            f"✅ <b>Not Kaydedildi</b>\n━━━━━━━━━━━━━━━━\n"
            f"📝 Ad: <code>#{name}</code>\n"
            f"📄 İçerik: <i>{content[:100]}{'...' if len(content) > 100 else ''}</i>\n\n"
            f"💡 Grupta <code>#{name}</code> yazarak gösterebilirsin.",
            parse_mode=ParseMode.HTML,
        )

    # ── SENDNOTE ────────────────────────────────────────────
    elif action == "act_sendnote":
        name = text.strip().lower().lstrip("#")
        if name not in notes:
            await msg.reply_text(f"❌ <code>#{name}</code> adında bir not bulunamadı.", parse_mode=ParseMode.HTML)
            return
        try:
            await ctx.bot.send_message(
                GROUP_ID,
                f"📝 <b>{name}</b>\n━━━━━━━━━━━━━━━━\n{notes[name]}",
                parse_mode=ParseMode.HTML,
            )
            await msg.reply_text(f"✅ <code>#{name}</code> notu gruba gönderildi.", parse_mode=ParseMode.HTML)
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── DELETENOTE ──────────────────────────────────────────
    elif action == "act_deletenote":
        name = text.strip().lower().lstrip("#")
        if name in notes:
            del notes[name]
            await msg.reply_text(f"✅ <code>#{name}</code> notu silindi.", parse_mode=ParseMode.HTML)
        else:
            await msg.reply_text(f"❌ <code>#{name}</code> adında not bulunamadı.", parse_mode=ParseMode.HTML)

# ──────────────────────────────────────────────────────────────
# YARDIMCI EKSECÜTİFLER
# ──────────────────────────────────────────────────────────────
async def _exec_unpin(msg, ctx):
    try:
        await ctx.bot.unpin_chat_message(GROUP_ID)
        await msg.reply_text("📌 Sabitlenmiş mesaj kaldırıldı.")
    except TelegramError as e:
        await msg.reply_text(f"❌ Hata: {e}")

async def _exec_lock(msg, ctx, lock: bool):
    global group_locked
    try:
        if lock:
            await ctx.bot.set_chat_permissions(GROUP_ID, ChatPermissions(can_send_messages=False))
            group_locked = True
            await msg.reply_text(
                "🔒 <b>Grup Kilitlendi</b>\n\nArtık sadece yöneticiler mesaj gönderebilir. "
                "Açmak için Grup Ayarları → Grubu Aç butonunu kullan.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await ctx.bot.set_chat_permissions(
                GROUP_ID, ChatPermissions(
                    can_send_messages=True, can_send_media_messages=True,
                    can_send_other_messages=True, can_add_web_page_previews=True,
                ),
            )
            group_locked = False
            await msg.reply_text(
                "🔓 <b>Grup Açıldı</b>\n\nTüm üyeler tekrar mesaj gönderebilir.",
                parse_mode=ParseMode.HTML,
            )
    except TelegramError as e:
        await msg.reply_text(f"❌ Hata: {e}")

async def _exec_groupinfo(msg, ctx):
    try:
        chat  = await ctx.bot.get_chat(GROUP_ID)
        count = await ctx.bot.get_chat_member_count(GROUP_ID)
        await msg.reply_text(
            f"🏘️ <b>Grup Bilgisi</b>\n━━━━━━━━━━━━━━━━\n"
            f"📛 Ad: <b>{chat.title}</b>\n"
            f"🆔 ID: <code>{chat.id}</code>\n"
            f"👥 Üye sayısı: <b>{count}</b>\n"
            f"📝 Açıklama: {chat.description or 'Yok'}\n"
            f"🔗 Davet linki: {chat.invite_link or 'Yok'}\n"
            f"🔒 Kilit durumu: {'Kilitli 🔒' if group_locked else 'Açık 🔓'}\n"
            f"🐌 Yavaş mod: {slowmode_sec}sn\n"
            f"⏱️ Otomatik silme: {auto_delete_sec}sn\n"
            f"🌊 Anti-flood: {'Aktif ✅' if antiflood_on else 'Pasif ❌'}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await msg.reply_text(f"❌ Hata: {e}")

async def _exec_stats(msg):
    await msg.reply_text(
        f"📈 <b>Bot Oturum İstatistikleri</b>\n━━━━━━━━━━━━━━━━\n"
        f"💬 İşlenen mesaj: <b>{stats['total_messages']}</b>\n"
        f"🗑️ Silinen mesaj: <b>{stats['deleted_messages']}</b>\n"
        f"🔨 Banlanan kullanıcı: <b>{stats['banned_users']}</b>\n"
        f"⚠️ Uyarılan kullanıcı: <b>{stats['warned_users']}</b>\n"
        f"🔇 Şu an susturulmuş: <b>{len(muted_users)}</b>\n"
        f"🚫 Aktif filtre: <b>{len(banned_words)}</b>\n"
        f"📝 Kayıtlı not: <b>{len(notes)}</b>\n\n"
        f"⚙️ <b>Aktif Ayarlar</b>\n"
        f"🌊 Anti-flood: {'✅' if antiflood_on else '❌'}\n"
        f"🔒 Grup kilidi: {'✅ Kilitli' if group_locked else '🔓 Açık'}\n"
        f"🐌 Yavaş mod: {slowmode_sec}sn\n"
        f"⏱️ Otomatik silme: {auto_delete_sec}sn",
        parse_mode=ParseMode.HTML,
    )

# ──────────────────────────────────────────────────────────────
# GRUP KOMUTLARI (direkt yazılanlar)
# ──────────────────────────────────────────────────────────────
async def _group_cmd(update, ctx, action):
    """Grup içi komutları pending'e atarak DM akışını kullan."""
    if not is_admin(update.effective_user.id): return
    uid = update.effective_user.id

    # Argüman varsa direkt işle, yoksa DM'e yönlendir
    if ctx.args:
        text = " ".join(ctx.args)
        # reply varsa ID ekle
        if update.message.reply_to_message:
            text = f"{update.message.reply_to_message.from_user.id} {text}"
        await _process_action(update, ctx, f"act_{action}", text)
    elif update.message.reply_to_message:
        text = str(update.message.reply_to_message.from_user.id)
        await _process_action(update, ctx, f"act_{action}", text)
    else:
        await update.message.reply_text(
            f"ℹ️ Kullanım: /{action} [ID veya yanıtla]\n"
            f"💡 Ya da DM'den /start → görsel panel",
        )

async def cmd_ban    (u, c): await _group_cmd(u, c, "ban")
async def cmd_unban  (u, c): await _group_cmd(u, c, "unban")
async def cmd_kick   (u, c): await _group_cmd(u, c, "kick")
async def cmd_mute   (u, c): await _group_cmd(u, c, "mute")
async def cmd_unmute (u, c): await _group_cmd(u, c, "unmute")
async def cmd_warn   (u, c): await _group_cmd(u, c, "warn")
async def cmd_unwarn (u, c): await _group_cmd(u, c, "unwarn")
async def cmd_promote(u, c): await _group_cmd(u, c, "promote")
async def cmd_demote (u, c): await _group_cmd(u, c, "demote")
async def cmd_info   (u, c): await _group_cmd(u, c, "info")

async def cmd_warnings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _group_cmd(update, ctx, "warnings")

async def cmd_pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if update.message.reply_to_message:
        try:
            await ctx.bot.pin_chat_message(GROUP_ID, update.message.reply_to_message.message_id)
            await update.message.reply_text("📌 Mesaj sabitlendi.")
        except TelegramError as e:
            await update.message.reply_text(f"❌ Hata: {e}")
    else:
        await update.message.reply_text("❌ Sabitlemek için bir mesajı yanıtlayın.")

async def cmd_unpin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_unpin(update.message, ctx)

async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if update.message.reply_to_message:
        try:
            await ctx.bot.delete_message(GROUP_ID, update.message.reply_to_message.message_id)
            await update.message.delete()
            stats["deleted_messages"] += 1
        except TelegramError as e:
            await update.message.reply_text(f"❌ Hata: {e}")

async def cmd_purge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Kullanım: /purge [n]"); return
    n = min(int(ctx.args[0]), 200)
    chat_id = update.effective_chat.id
    try:
        sentinel = await ctx.bot.send_message(chat_id, "🧹")
        last_id  = sentinel.message_id
        await ctx.bot.delete_message(chat_id, last_id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}"); return
    deleted = await _bulk_delete(ctx, chat_id, last_id - 1, last_id - n)
    stats["deleted_messages"] += deleted
    m = await ctx.bot.send_message(chat_id, f"🗑️ {deleted} mesaj silindi.")
    asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 5))

async def cmd_purgefrom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Grupta bir mesajı reply'layıp /purgefrom yaz → o mesajdan itibaren sil."""
    if not is_admin(update.effective_user.id): return
    chat_id = update.effective_chat.id

    reply = update.message.reply_to_message
    if not reply:
        m = await update.message.reply_text(
            "ℹ️ Kullanım: Silmenin başlamasını istediğin mesajı <b>yanıtla</b> ve /purgefrom yaz.",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 5))
        asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 5))
        return

    from_id = reply.message_id

    # Onay mesajı gönder
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ Evet, {from_id}'den itibaren sil!",
            callback_data=f"purge_after_confirm:{from_id}"
        ),
        InlineKeyboardButton("❌ İptal", callback_data="purgefrom_cancel"),
    ]])
    m = await update.message.reply_text(
        f"⚠️ <b>Onay Gerekiyor</b>\n\n"
        f"Mesaj <code>{from_id}</code>'den başlayarak en son mesaja kadar\n"
        f"<b>tüm mesajlar silinecek.</b>\n\n"
        f"Bu işlem <b>geri alınamaz!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    # Komut mesajını hemen sil
    asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 2))

async def cmd_clearall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    mid = update.message.message_id
    kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Evet, 100 mesajı sil!", callback_data="purge_confirm:100"),
        InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
    ]])
    await update.message.reply_text(
        "⚠️ <b>Son 100 mesajı silmek istediğine emin misin?</b>\nBu işlem geri alınamaz!",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /broadcast [metin]"); return
    text = " ".join(ctx.args)
    await ctx.bot.send_message(GROUP_ID, f"📢 <b>DUYURU</b>\n━━━━━━━━━━━━━━━━\n{text}", parse_mode=ParseMode.HTML)
    await update.message.reply_text("✅ Duyuru gönderildi.")

async def cmd_poll(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /poll Soru|Seç1|Seç2"); return
    parts = " ".join(ctx.args).split("|")
    if len(parts) < 3: await update.message.reply_text("❌ En az 1 soru + 2 seçenek"); return
    await ctx.bot.send_poll(GROUP_ID, parts[0].strip(), [p.strip() for p in parts[1:] if p.strip()], is_anonymous=False)
    await update.message.reply_text("✅ Anket oluşturuldu.")

async def cmd_lock(u, c):
    if not is_admin(u.effective_user.id): return
    await _exec_lock(u.message, c, lock=True)

async def cmd_unlock(u, c):
    if not is_admin(u.effective_user.id): return
    await _exec_lock(u.message, c, lock=False)

async def cmd_slowmode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global slowmode_sec
    if not is_admin(update.effective_user.id): return
    if not ctx.args or not ctx.args[0].isdigit(): await update.message.reply_text("Kullanım: /slowmode [sn]"); return
    slowmode_sec = int(ctx.args[0])
    await ctx.bot.set_chat_slow_mode_delay(GROUP_ID, slowmode_sec)
    await update.message.reply_text(f"🐌 Yavaş mod: {slowmode_sec}sn")

async def cmd_setwelcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global welcome_msg
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /setwelcome [metin]"); return
    welcome_msg = " ".join(ctx.args)
    await update.message.reply_text(f"✅ Karşılama güncellendi.")

async def cmd_addban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /addban [kelime]"); return
    word = " ".join(ctx.args).lower()
    if word not in banned_words: banned_words.append(word)
    await update.message.reply_text(f"✅ '{word}' eklendi.")

async def cmd_removeban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /removeban [kelime]"); return
    word = " ".join(ctx.args).lower()
    if word in banned_words: banned_words.remove(word); await update.message.reply_text(f"✅ '{word}' kaldırıldı.")
    else: await update.message.reply_text("❌ Listede yok.")

async def cmd_listban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not banned_words: await update.message.reply_text("📋 Liste boş."); return
    await update.message.reply_text("📋 Yasaklı: " + ", ".join(f"`{w}`" for w in banned_words))

async def cmd_autodelete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global auto_delete_sec
    if not is_admin(update.effective_user.id): return
    if not ctx.args or not ctx.args[0].isdigit(): await update.message.reply_text("Kullanım: /autodelete [sn]"); return
    auto_delete_sec = int(ctx.args[0])
    await update.message.reply_text(f"✅ Otomatik silme: {auto_delete_sec}sn")

async def cmd_antiflood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global antiflood_on
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /antiflood [on/off]"); return
    antiflood_on = ctx.args[0].lower() == "on"
    await update.message.reply_text(f"🌊 Anti-flood: {'Aktif ✅' if antiflood_on else 'Pasif ❌'}")

async def cmd_newlink(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    link = await ctx.bot.export_chat_invite_link(GROUP_ID)
    await update.message.reply_text(f"🔗 Yeni link:\n{link}")

async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("Kullanım: /note [ad]"); return
    name = ctx.args[0].lower()
    if name not in notes: await update.message.reply_text(f"❌ '{name}' bulunamadı."); return
    await update.message.reply_text(f"📝 <b>{name}</b>\n━━━━━━━━━━\n{notes[name]}", parse_mode=ParseMode.HTML)

async def cmd_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not notes: await update.message.reply_text("📋 Not yok."); return
    await update.message.reply_text("📋 Notlar:\n" + "\n".join(f"• #{k}" for k in notes.keys()))

async def cmd_savenote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args or len(ctx.args) < 2: await update.message.reply_text("Kullanım: /savenote [ad] [metin]"); return
    notes[ctx.args[0].lower()] = " ".join(ctx.args[1:])
    await update.message.reply_text(f"✅ Not kaydedildi.")

async def cmd_deletenote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /deletenote [ad]"); return
    name = ctx.args[0].lower()
    if name in notes: del notes[name]; await update.message.reply_text(f"✅ Silindi.")
    else: await update.message.reply_text("❌ Bulunamadı.")

async def cmd_groupinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_groupinfo(update.message, ctx)

async def cmd_membercount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = await ctx.bot.get_chat_member_count(GROUP_ID)
    await update.message.reply_text(f"👥 Üye sayısı: <b>{count}</b>", parse_mode=ParseMode.HTML)

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_stats(update.message)

async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    c = update.effective_chat
    await update.message.reply_text(
        f"👤 Senin ID: <code>{u.id}</code>\n💬 Chat ID: <code>{c.id}</code>",
        parse_mode=ParseMode.HTML,
    )

# ──────────────────────────────────────────────────────────────
# YENİ ÜYE KARŞILAMA
# ──────────────────────────────────────────────────────────────
async def handle_new_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot: continue
        text = welcome_msg.format(
            name=member.full_name, id=member.id, group=update.effective_chat.title,
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Grup Kuralları", callback_data="rules")]])
        m  = await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        if auto_delete_sec > 0:
            asyncio.create_task(auto_delete(ctx, update.effective_chat.id, m.message_id, auto_delete_sec))
        await notify_admin(ctx, f"👤 Yeni üye: {fmt(member)} (ID: <code>{member.id}</code>) gruba katıldı.")

# ──────────────────────────────────────────────────────────────
# MESAJ FİLTRESİ
# ──────────────────────────────────────────────────────────────
async def filter_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    stats["total_messages"] += 1
    user = msg.from_user
    if not user or is_admin(user.id): return

    text       = msg.text or msg.caption or ""
    text_lower = text.lower()

    # #notadı kısayolu
    if text.startswith("#"):
        note_name = text[1:].strip().lower().split()[0]
        if note_name in notes:
            await msg.reply_text(
                f"📝 <b>{note_name}</b>\n━━━━━━━━━━\n{notes[note_name]}",
                parse_mode=ParseMode.HTML,
            )
        return

    # Yasaklı kelime filtresi
    for word in banned_words:
        if word in text_lower:
            try:
                await msg.delete()
                stats["deleted_messages"] += 1
                m = await ctx.bot.send_message(
                    msg.chat_id,
                    f"⚠️ {fmt(user)}, mesajın yasaklı içerik barındırdığı için silindi.",
                    parse_mode=ParseMode.HTML,
                )
                asyncio.create_task(auto_delete(ctx, msg.chat_id, m.message_id, 5))
                await notify_admin(ctx, f"🚫 Yasaklı kelime tespit edildi!\n👤 {fmt(user)}\n🔤 Kelime: <code>{word}</code>")
            except TelegramError:
                pass
            return

    # Anti-flood (10sn'de 5+ mesaj → 5dk mute)
    if antiflood_on:
        now = datetime.now()
        uid = user.id
        antiflood_buf.setdefault(uid, [])
        antiflood_buf[uid] = [t for t in antiflood_buf[uid] if (now - t).total_seconds() < 10]
        antiflood_buf[uid].append(now)
        if len(antiflood_buf[uid]) > 5:
            try:
                await ctx.bot.restrict_chat_member(
                    msg.chat_id, uid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=datetime.now() + timedelta(minutes=5),
                )
                m = await ctx.bot.send_message(
                    msg.chat_id,
                    f"🌊 {fmt(user)} çok hızlı mesaj gönderdiği için <b>5 dakika susturuldu</b>.",
                    parse_mode=ParseMode.HTML,
                )
                asyncio.create_task(auto_delete(ctx, msg.chat_id, m.message_id, 10))
                await notify_admin(ctx, f"🌊 Flood koruması devreye girdi!\n👤 {fmt(user)} (ID: <code>{uid}</code>) 5dk susturuldu.")
                antiflood_buf[uid] = []
            except TelegramError:
                pass

    # Otomatik silme
    if auto_delete_sec > 0:
        asyncio.create_task(auto_delete(ctx, msg.chat_id, msg.message_id, auto_delete_sec))

# ──────────────────────────────────────────────────────────────
# HATA HANDLER
# ──────────────────────────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Hata: {ctx.error}", exc_info=ctx.error)

# ──────────────────────────────────────────────────────────────
# POST INIT — Komut listeleri
# ──────────────────────────────────────────────────────────────
async def post_init(app: Application):
    # DM'de tüm komutlar görünsün (sadece admin DM açabilir zaten)
    dm_cmds = [
        BotCommand("start",       "🤖 Yönetim Panelini Aç"),
        BotCommand("help",        "📋 Tüm Komutları Listele"),
        BotCommand("groupinfo",   "🏘️ Grup Bilgilerini Gör"),
        BotCommand("membercount", "👥 Üye Sayısını Gör"),
        BotCommand("stats",       "📈 Bot İstatistiklerini Gör"),
        BotCommand("notes",       "📝 Kayıtlı Notları Listele"),
        BotCommand("broadcast",   "📣 Gruba Duyuru Gönder"),
    ]
    # Grupta sadece /start görünsün
    group_cmds = [
        BotCommand("start", "🤖 Yönetim Paneli"),
    ]
    await app.bot.set_my_commands(dm_cmds,    scope=BotCommandScopeAllPrivateChats())
    await app.bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
    logger.info("✅ Komut listeleri Telegram'a kaydedildi.")

# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))

    # Komutlar
    for name, fn in [
        ("ban",cmd_ban),("unban",cmd_unban),("kick",cmd_kick),("mute",cmd_mute),
        ("unmute",cmd_unmute),("warn",cmd_warn),("unwarn",cmd_unwarn),
        ("warnings",cmd_warnings),("promote",cmd_promote),("demote",cmd_demote),
        ("pin",cmd_pin),("unpin",cmd_unpin),("delete",cmd_delete),
        ("purge",cmd_purge),("purgefrom",cmd_purgefrom),("clearall",cmd_clearall),("broadcast",cmd_broadcast),
        ("poll",cmd_poll),("lock",cmd_lock),("unlock",cmd_unlock),
        ("slowmode",cmd_slowmode),("setwelcome",cmd_setwelcome),
        ("autodelete",cmd_autodelete),("antiflood",cmd_antiflood),
        ("addban",cmd_addban),("removeban",cmd_removeban),("listban",cmd_listban),
        ("newlink",cmd_newlink),("note",cmd_note),("notes",cmd_notes),
        ("savenote",cmd_savenote),("deletenote",cmd_deletenote),
        ("info",cmd_info),("groupinfo",cmd_groupinfo),
        ("membercount",cmd_membercount),("stats",cmd_stats),("id",cmd_id),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.TEXT | filters.FORWARDED),
        handle_dm,
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
        filter_messages,
    ))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    logger.info("🚀 Bot başlatılıyor...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
