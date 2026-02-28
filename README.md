# 🤖 Telegram Grup Yönetim Botu

Kapsamlı Telegram grup yönetim botu — Railway üzerinde çalışır, DM üzerinden yönetilir.

## 🚀 Kurulum

### 1. GitHub'a Yükle
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/KULLANICI_ADI/REPO_ADI.git
git push -u origin main
```

### 2. Railway Kurulumu
1. [railway.app](https://railway.app) adresine git → **New Project → Deploy from GitHub Repo**
2. Repoyu seç
3. **Variables** sekmesine git ve şu değişkenleri ekle:

| Değişken | Değer |
|----------|-------|
| `BOT_TOKEN` | BotFather'dan aldığın token |
| `ADMIN_ID` | Senin Telegram kullanıcı ID'n |
| `GROUP_ID` | Grubun chat ID'si (örn: `-1001234567890`) |

4. Deploy otomatik başlar ✅

---

## 📋 Komutlar

### 👥 Kullanıcı Yönetimi
| Komut | Açıklama |
|-------|----------|
| `/ban [id/reply] [neden]` | Kullanıcıyı banla |
| `/unban [id/reply]` | Banı kaldır |
| `/kick [id/reply]` | Kullanıcıyı at (tekrar girebilir) |
| `/mute [id/reply] [dakika]` | Kullanıcıyı sustur |
| `/unmute [id/reply]` | Susturmayı kaldır |
| `/warn [id/reply] [neden]` | Uyarı ver (3'te otomatik ban) |
| `/unwarn [id/reply]` | Uyarıları sıfırla |
| `/warnings [id/reply]` | Uyarı sayısını gör |

### 📢 Mesaj Yönetimi
| Komut | Açıklama |
|-------|----------|
| `/pin` | Yanıtladığın mesajı sabitle |
| `/unpin` | Aktif sabitlemeyi kaldır |
| `/delete` | Yanıtladığın mesajı sil |
| `/purge [n]` | Son n mesajı sil (max 100) |
| `/broadcast [metin]` | Gruba duyuru gönder |

### ⚙️ Grup Ayarları
| Komut | Açıklama |
|-------|----------|
| `/setwelcome [metin]` | Karşılama mesajı ayarla |
| `/addban [kelime]` | Yasaklı kelime ekle |
| `/removeban [kelime]` | Yasaklı kelime kaldır |
| `/listban` | Yasaklı kelimeleri listele |
| `/autodelete [sn]` | Mesajları otomatik sil (0=kapat) |
| `/antiflood [on/off]` | Flood korumasını aç/kapat |

### 📊 Bilgi
| Komut | Açıklama |
|-------|----------|
| `/info [id/reply]` | Kullanıcı bilgisi |
| `/groupinfo` | Grup bilgisi |
| `/stats` | Bot istatistikleri |
| `/id` | Chat ve kullanıcı ID'si |

---

## 🛡️ Özellikler
- ✅ Ban / Unban / Kick
- ✅ Mute ile süre belirleme
- ✅ 3 uyarıda otomatik ban
- ✅ Yasaklı kelime filtresi
- ✅ Anti-flood koruması (10sn'de 5+ mesaj = 5dk mute)
- ✅ Yeni üye karşılama (özelleştirilebilir)
- ✅ Otomatik mesaj silme
- ✅ Duyuru sistemi
- ✅ Mesaj sabitleme
- ✅ Sadece admin kullanabilir (DM üzerinden yönetim)

---

## 💡 Karşılama Mesajı Değişkenleri
```
{name}  → Kullanıcı adı
{id}    → Kullanıcı ID'si
{group} → Grup adı
```
Örnek: `/setwelcome Merhaba {name}! Gruba hoş geldin 🎉`

---

## 🔧 Gereksinimler
- Python 3.11+
- `python-telegram-bot==20.7`
