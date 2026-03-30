# 🛒 Market Monitor Bot

A Telegram bot for real-time monitoring of important figures' tweets, Truth Social posts, and global market prices -> Sends to your Telegram channel

---

## Project Structure

```
market_monitor/
├── main.py                  # Main entry point
├── config.py                # All configurations
├── requirements.txt         # Required Python packages
├── .env                     # API Keys (create this from .env.example)
├── monitors/
│   ├── twitter.py           # Nitter RSS polling
│   ├── truthsocial.py       # Truth Social API polling with Botasaurus
│   └── prices.py            # Market prices tracking (via yfinance)       
├── ai/
│   └── processor.py         # AI analysis utilizing Gemini Flash + Groq fallback
├── bot/
│   └── sender.py            # Telegram message sender utility    
├── utils/
│   ├── screenshot.py        # Playwright & Botasaurus screenshot generation tool
│   └── helpers.py           # Shared helper functions
└── data/
    ├── seen_tweets.json      # Auto-generated cache for seen tweets
    └── last_prices.json      # Auto-generated cache for last fetched prices
```

---

## Setup & Installation

### 1. Install Dependencies

You need to install the core python requirements and Playwright's Chromium browser:

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment Variables

Create and fill your `.env` API keys file:

```bash
cp .env.example .env
```

Set the `.env` values accurately:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHANNEL_ID=@your_channel_or_numeric_id
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
```

### 3. Required Keys

| Service | Retrieval Source | Expected Cost |
|---|---|---|
| Telegram Bot Token | @BotFather (on Telegram) | Free |
| Gemini API Key | https://aistudio.google.com/app/apikey | Free (15 req/min) |
| Groq API Key | https://console.groq.com | Free (14400 req/day) |

### 4. Run the Bot

```bash
python main.py
```

---

## Output Examples

### Social Post Format

```text
━━━━━━━━━━━━━━━━━━━━━━
🚨  #New_Truth by Donald J. Trump
━━━━━━━━━━━━━━━━━━━━━━

💬 Original Text:
Saudi Arabia must increase oil production immediately...

━━━━━
🇮🇷 Translation:
عربستان باید فوراً تولید نفت را افزایش دهد...   

🧠 Analysis:
This tweet exerts direct pressure on OPEC to increase supply.
If enacted, it creates a downward effect resulting in bearish movements for WTI and Brent.

🔴 Bearish Oil  |  🔗 View Tweet
━━━━━━━━━━━━━━━━━━━━━━
```

### Market Prices Format

```text
━━━━━━━━━━━━━━━━━━━━━━
📊  Market Update  |  14:00 UTC  |  28 Mar 2026
━━━━━━━━━━━━━━━━━━━━━━

🛢  WTI Crude     $82.40   🔴 -1.20%
🛢  Brent Crude   $85.10   🔴 -0.90%
🥇  Gold          $2,341   🟢 +0.40%
💵  DXY           104.20   🟢 +0.30%
⚡  Nat Gas         $2.18   ⬜ -0.10%

━━━━━━━━━━━━━━━━━━━━━━
```

---

## Verifications & Indicators

| Icon | Meaning |
|---|---|
| ✅ | Verified Source |
| 🟡 | Semi-Verified Source / Mixed |
| 🔴 State Media | Direct State-Controlled Media |
| ⚠️ | Unverified / Anonymous Source |

---

## Core Configurations (`config.py`)

All polling values can be managed directly in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `PRICE_INTERVAL_MINUTES` | 5 | Time interval between routine market price updates |
| `TWITTER_POLL_INTERVAL_MINUTES` | 5 | General time interval between Nitter checks |
| `TWITTER_FAST_INTERVAL_SECONDS` | 20 | High-speed Twitter account polling interval |
| `TRUTHSOCIAL_FAST_INTERVAL_SECONDS` | 30 | Truth Social specific polling loop frequency |
| `PRICE_ALERT_PCT` | 1.5 | Minimum percentage shift required to trigger an instant price alert |
| `NITTER_INSTANCES` | 5 URLs | Fallback array of active Nitter proxy endpoints |

---

## Operating as a Background Service (systemd)

To keep the bot running infinitely on a Linux server:

1. Create a service file: `/etc/systemd/system/market-monitor.service`
2. Insert:

```ini
[Unit]
Description=Market Monitor Bot Service
After=network.target

[Service]
WorkingDirectory=/path/to/market_monitor
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

3. Enable and start:

```bash
sudo systemctl enable market-monitor
sudo systemctl start market-monitor
```
