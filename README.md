# 🪂 KriptoDropTR Telegram Botu — Kurulum Kılavuzu

## 📦 Gereksinimler
- Python 3.10+
- pip

---

## 🚀 Kurulum Adımları

### 1. Gerekli kütüphaneleri yükle
```bash
pip install -r requirements.txt
```

### 2. `config.py` dosyasını düzenle
```python
BOT_TOKEN   = "7123456789:AAFxxxxxx"   # BotFather'dan
ADMIN_ID    = 987654321                 # Kendi Telegram ID'n
GROUP_ID    = -1001234567890            # Grubun ID'si
GROK_API_KEY = "xai-xxxxxxxxxxxxx"     # xAI dashboard'dan
```

> **Telegram ID'ni nasıl öğrenirim?**  
> @userinfobot veya @getmyid_bot'a mesaj at.

> **Grup ID'sini nasıl öğrenirim?**  
> Botu gruba admin olarak ekledikten sonra gruba `/start` yaz,  
> konsol loglarında `chat_id` görünür.  
> Ya da @getidsbot ile öğrenebilirsin.

### 3. Botu başlat
```bash
python bot.py
```

---

## 🛠 Özellikler

### Admin (DM üzerinden):
| Özellik | Açıklama |
|---|---|
| ➕ Airdrop Ekle | Adım adım form ile airdrop ekle |
| 📋 Airdropları Listele | Tüm airdropları admin görünümüyle gör |
| ✏️ Airdrop Aktif/Pasif | Airdropleri geçici kapat/aç |
| 🗑 Airdrop Sil | Kalıcı sil |
| 📌 Sabitle | Öne çıkarılacak airdropları sabitle |
| 📰 Haber Gönder | Grok AI ile konu bazlı haber oluştur, gruba gönder |
| 📢 Duyuru Yap | Gruba manuel duyuru yaz |
| 📊 İstatistikler | Toplam airdrop, haber ve kategori özeti |
| 🔄 Grup Bilgisi | Üye sayısı ve grup detayları |

### Kullanıcı (DM):
| Özellik | Açıklama |
|---|---|
| 🪂 Aktif Airdroplar | Tüm aktif airdropları göster |
| 📌 Öne Çıkanlar | Sabitlenmiş airdroplar |
| 🔍 Kategoriye Göre | DeFi, NFT, GameFi vb. filtrele |
| 📅 Son Eklenenler | En yeni 5 airdrop |
| ❓ Yardım | Airdrop rehberi |

### Grup komutları:
| Komut | Açıklama |
|---|---|
| `/airdrops` | Aktif airdropları özet listele |
| `/haberler` | Son haber başlıklarını listele |
| `/start` | Bota özel mesaj atmaya yönlendir |

---

## 🔐 Güvenlik Notları
- Sadece `ADMIN_ID` olarak tanımladığın hesap admin paneline erişebilir.
- Botu gruba **yönetici** olarak ekle (mesaj gönderebilmesi için).
- `config.py` dosyasını asla paylaşma/commit etme.

---

## 📁 Dosya Yapısı
```
kriptodrop_bot/
├── bot.py            # Ana bot kodu
├── config.py         # Konfigürasyon (tokenlar burada)
├── requirements.txt  # Python bağımlılıkları
├── kriptodrop.db     # SQLite veritabanı (otomatik oluşur)
└── bot.log           # Log dosyası (otomatik oluşur)
```

---

## 🔧 Sürekli Çalıştırma (Linux/VPS)

### systemd servisi olarak:
```bash
sudo nano /etc/systemd/system/kriptodrop.service
```
```ini
[Unit]
Description=KriptoDropTR Telegram Bot
After=network.target

[Service]
WorkingDirectory=/home/user/kriptodrop_bot
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable kriptodrop && sudo systemctl start kriptodrop
```

### screen ile (basit yöntem):
```bash
screen -S kriptobot
python bot.py
# Ctrl+A → D ile çıkış
```
