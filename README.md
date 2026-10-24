# Kontent arxiv boti

Instagram, TikTok, Pinterest, X, Facebook havolalaridan video/rasmni yuklab, Telegram supergroup'ning kerakli **topic**iga joylaydi.

## Ishlatish
**1-usul — topic ichida:** guruhda kerakli topicga havola tashlang → bot yuklab, o'sha topicga joylaydi va havolali xabarni o'chiradi.

**2-usul — shaxsiy xabar:** botga havola yuboring → topic tugmasini bosing → bot guruhdagi o'sha topicga joylaydi.

## Guruhni sozlash (bir marta)
1. Supergroup → Sozlamalar → **Topics** yoqilgan bo'lsin.
2. Botni guruhga qo'shib **admin** qiling (xabarlarni o'chirish, topiclarni boshqarish).
3. Guruhda `/ulash`.
4. Eski topiclarning har birida `/bogla` (yoki `/bogla Nom`). Yangi topiclarni bot o'zi taniydi.
5. `/topiclar` — bot biladigan ro'yxat.

## Cheklovlar
- Telegram Bot API: bitta fayl **50 MB** gacha. Katta video avval 480p da qayta yuklanadi.
- **YouTube** server (Vercel) IP manzilini bloklaydi — hozircha ishlamaydi.
- **Instagram** login'siz: Stories yuklanmaydi; ochiq post/Reels ishlaydi (Instagram ba'zan IP'ni cheklaydi).
- Faqat `ALLOWED_USER_IDS` dagi foydalanuvchilar.

## Texnik
- Vercel (Python, FastAPI) — `main.py`; webhook `/webhook`.
- Yuklash: `yt-dlp` + `gallery-dl`, `ffmpeg` (imageio-ffmpeg). Fayllar vaqtinchalik papkada, yuborilgach o'chadi.
- Baza: Supabase (`media_*` jadvallar, RLS, faqat `DB_SECRET` bilan RPC).
- Muhit o'zgaruvchilari: `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`, `WEBHOOK_SECRET`, `SUPABASE_URL`, `SUPABASE_KEY`, `DB_SECRET`; ixtiyoriy `DELETE_SOURCE=0`, `PHOTOS_AS=photo`, `YT_CLIENTS`.
- Webhook o'rnatish: `GET /setup?key=<WEBHOOK_SECRET>`; diagnostika: `GET /probe?key=…&url=…`.
- Testlar: `pytest -q`.
