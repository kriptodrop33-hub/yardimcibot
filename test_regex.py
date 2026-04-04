import re

text = """🚀 Binance TR yeni üye Bonusu! 🎁

Yeni kullanıcılar için 880 TL bonus kazanma fırsatı 🤐

----------------------

🎯 YAPMAN GEREKENLER:

1️⃣ Promosyona katılım için kayıt olun
2️⃣ Kayıt olduktan sonra etkinlik sayfasına git otomatik kaydolur
3️⃣ İlk para yatırma işlemini tamamla

----------------------
» Hemen Kaydol: 🔗 TIKLA 🔗
» Etkinlik Sayfası: 🔗 TIKLA 🔗

Görev zorluğu: Kolay
Ödül miktarı: 880 TL
Airdrop puanı: ⭐⭐⭐⭐⭐

📅 Kampanya Dönemi: 16.03.2026 Saat 16.00 - 09.04.2026 Saat 23.59
----------------------
🔥 Daha fazla airdrop için duyuru kanalını pinle
📢 @kriptodropduyuru
🎁 @kriptodroptr

Skor: 🟢 GÜVENİLİR (90/100)"""

title_match = re.search(r'^(.*?)\n', text)
title = title_match.group(1).strip() if title_match else "Bilinmiyor"

reward_match = re.search(r'Ödül miktarı:\s*(.*)', text, re.IGNORECASE)
reward = reward_match.group(1).strip() if reward_match else "Belirtilmedi"

deadline_match = re.search(r'Kampanya Dönemi:\s*(.*)', text, re.IGNORECASE)
deadline = deadline_match.group(1).strip() if deadline_match else "Belirsiz"

description = text[:text.find('Hemen Kaydol')].strip()
if len(description) > 300:
    description = description[:297] + "..."

print("Title:", title)
print("Reward:", reward)
print("Deadline:", deadline)

project_match = re.search(r'🚀\s*(.*?)\s*yeni üye', text, re.IGNORECASE)
if not project_match:
    # grab the first few letters after emoji
    project_match = re.search(r'(?:🚀|🎉|🔥|\w)\s*([A-Za-z0-9]+(?:\s+[A-Za-z0-9]+){0,2})', title)
project = project_match.group(1).strip() if project_match else "Belirtilmedi"
print("Project:", project)
