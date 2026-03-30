# 🛢 Market Monitor Bot

یک ربات تلگرام برای پایش لحظه‌ای توییت‌های شخصیت‌های مهم و قیمت بازارهای جهانی.

---

## ساختار پروژه

```
market_monitor/
├── main.py                  # نقطه ورود اصلی
├── config.py                # تمام تنظیمات
├── requirements.txt
├── .env                     # کلیدها (ایجاد کنید)
├── monitors/
│   ├── twitter.py           # پایش Nitter RSS
│   └── prices.py            # قیمت بازارها (yfinance)
├── ai/
│   └── processor.py         # Gemini Flash + Groq fallback
├── bot/
│   └── sender.py            # ارسال پیام به تلگرام
├── utils/
│   └── helpers.py           # توابع کمکی
└── data/
    ├── seen_tweets.json      # توییت‌های دیده‌شده (خودکار)
    └── last_prices.json      # قیمت‌های آخر (خودکار)
```

---

## راه‌اندازی

### ۱. نصب پیش‌نیازها

```bash
pip install -r requirements.txt
```

### ۲. ساخت فایل .env

```bash
cp .env.example .env
```

فایل `.env` را با مقادیر واقعی پر کنید:

```env
TELEGRAM_BOT_TOKEN=توکن_ربات_تلگرام
TELEGRAM_CHANNEL_ID=@کانال_یا_آیدی_عددی
GEMINI_API_KEY=کلید_جمینی
GROQ_API_KEY=کلید_گروک
```

### ۳. دریافت کلیدها

| سرویس | لینک | هزینه |
|---|---|---|
| Telegram Bot | @BotFather | رایگان |
| Gemini API | https://aistudio.google.com/app/apikey | رایگان (15 req/min) |
| Groq API | https://console.groq.com | رایگان (14400 req/day) |

### ۴. اجرا

```bash
python main.py
```

---

## نمونه فرمت توییت در تلگرام

```
━━━━━━━━━━━━━━━━━━━━━━
🚨  Donald Trump  |  @realDonaldTrump
🏛 Politics   ✅
━━━━━━━━━━━━━━━━━━━━━━

🕐  ۱۵ دقیقه پیش  |  14:32 UTC

💬  متن اصلی:
Saudi Arabia must increase oil production immediately...

🇮🇷  ترجمه:
عربستان باید فوراً تولید نفت را افزایش دهد...

🧠  تحلیل:
این توییت فشار مستقیم بر OPEC برای افزایش عرضه است.
در صورت اجرا فشار نزولی روی WTI و Brent ایجاد می‌شود.

🔴  نفت  |  🔗 مشاهده توییت
━━━━━━━━━━━━━━━━━━━━━━
```

---

## نمونه فرمت قیمت‌ها

```
━━━━━━━━━━━━━━━━━━━━━━
📊  آپدیت بازار  |  14:00 UTC  |  28 Mar 2026
━━━━━━━━━━━━━━━━━━━━━━

🛢  WTI Crude     $82.40   🔴 -1.20%
🛢  Brent Crude   $85.10   🔴 -0.90%
🥇  Gold          $2,341   🟢 +0.40%
💵  DXY           104.20   🟢 +0.30%
⚡  Nat Gas         $2.18  ⬜ -0.10%

━━━━━━━━━━━━━━━━━━━━━━
```

---

## اعتبارسنجی منابع

| نشانه | معنا |
|---|---|
| ✅ | منبع معتبر |
| 🟡 | منبع نیمه‌معتبر |
| 🔴 State Media | رسانه دولتی |
| ⚠️ | منبع تأییدنشده |

---

## تنظیمات قابل تغییر (config.py)

| تنظیم | پیشفرض | توضیح |
|---|---|---|
| `PRICE_INTERVAL_MINUTES` | 5 | فاصله آپدیت قیمت |
| `TWITTER_POLL_INTERVAL_MINUTES` | 5 | فاصله بررسی توییت |
| `PRICE_ALERT_PCT` | 1.5 | درصد تغییر برای هشدار |
| `NITTER_INSTANCES` | 5 آدرس | لیست سرورهای Nitter |

---

## اجرا به عنوان سرویس (systemd)

```ini
[Unit]
Description=Market Monitor Bot
After=network.target

[Service]
WorkingDirectory=/path/to/market_monitor
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable market-monitor
sudo systemctl start market-monitor
```
