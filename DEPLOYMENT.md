# Whale & Wallet Tracker Bot — FREE Deployment Guide

## 100% Free Stack
- **Bot hosting**: Railway.app (free tier, no credit card needed)
- **APIs**: Etherscan, BscScan, Solscan, Trongrid, Blockchair (all free)
- **Price data**: CoinGecko free API (no key needed)
- **Database**: SQLite (included, no setup needed)

---

## Step 1 — Create Your Telegram Bot (2 min)

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Name it (e.g. `Whale Tracker`)
4. Username ending in `bot` (e.g. `whaletrackervip_bot`)
5. **Copy the token** — looks like `7123456789:AAFxxx...`
6. Message **@userinfobot** → copy your numeric ID

---

## Step 2 — Get Free API Keys (5 min, all free)

### Etherscan (ETH tracking)
1. Go to etherscan.io → click Sign In → Register
2. After login → top right menu → API Keys → Add
3. Copy the key

### BscScan (BSC tracking)
1. Go to bscscan.com → same process as Etherscan
2. Register → API Keys → Add → Copy

### Solscan (optional, works without key)
- Leave blank OR register at solscan.io for higher limits

### Blockchair (BTC + TRX fallback)
- Works without a key for low volume
- Register free at blockchair.com if you hit limits

---

## Step 3 — Deploy on Railway (FREE, no credit card)

### 3a. Prepare files
Upload your bot folder to **GitHub**:

1. Go to github.com → sign up free
2. New repository → name it `whale-tracker` → Public
3. On your phone, use the GitHub website to upload files:
   - Click `Add file` → `Upload files`
   - Upload ALL files from the whale_tracker folder

### 3b. Deploy on Railway
1. Go to **railway.app** on your phone
2. Sign up with your GitHub account (free)
3. Click **New Project** → **Deploy from GitHub repo**
4. Select your `whale-tracker` repository
5. Railway auto-detects Python ✅

### 3c. Set Environment Variables
In your Railway project:
1. Click your service → **Variables** tab
2. Add each variable from `.env.example`:

```
TELEGRAM_BOT_TOKEN  = your_token_here
ADMIN_IDS           = your_telegram_id
ETHERSCAN_API_KEY   = your_etherscan_key
BSCSCAN_API_KEY     = your_bscscan_key
WHALE_THRESHOLD_USD = 100000
SCAN_INTERVAL_SECONDS = 60
```

### 3d. Set Start Command
In Railway → your service → **Settings** → **Start Command**:
```
python bot.py
```

Click **Deploy** → wait ~2 minutes → your bot is live! 🎉

---

## Alternative: Render.com (also free)

1. Go to render.com → sign up free with GitHub
2. New → **Web Service** → connect your repo
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python bot.py`
5. Add environment variables in the Environment tab
6. Deploy

⚠️ Render free tier sleeps after 15min inactivity.
Use Railway for always-on bots.

---

## Testing Your Bot

Send these to your bot in Telegram:

```
/start
/whales
/watch 0x28C6c06298d514Db089934071355E5743bf21d60 ETH Binance Hot
/mywallet
/alerts
```

---

## Admin Commands

| Command | Example |
|---------|---------|
| `/addwhale ADDRESS CHAIN Label` | `/addwhale 0xABC... ETH NewWhale` |
| `/removewhale ADDRESS` | `/removewhale 0xABC...` |
| `/setinterval SECONDS` | `/setinterval 30` |
| `/broadcast MESSAGE` | `/broadcast Bot updated!` |
| `/stats` | `/stats` |

---

## User Commands

| Command | Description |
|---------|-------------|
| `/watch ADDRESS CHAIN` | Track a wallet |
| `/watch 0xABC... ETH MyWhale` | Track with custom label |
| `/unwatch ADDRESS` | Stop tracking |
| `/mywallet` | List your wallets |
| `/alerts` | Recent alerts |
| `/whales` | Global whale list |
| `/threshold 50000` | Set your alert min ($) |

---

## What Gets Tracked

### 🐋 Whale Alerts (Global — all users notified)
- Any transaction ≥ $100,000 from known whale wallets
- Pre-seeded: Binance hot/cold wallets, Kraken, Coinbase, etc.

### 💸 Transfer Alerts (Your personal wallets)
- Large transfers above your threshold on ETH, BSC, SOL, BTC, TRX

### 🧠 Smart Money Buy Alerts
- ERC-20, BEP-20, TRC-20 token buys from wallets you track

### 💼 Balance Change Alerts
- If a wallet's balance changes by ≥10% or ≥$10,000

---

## Supported Chains

| Chain | API Used | Key Required |
|-------|----------|-------------|
| Ethereum (ETH) | Etherscan | Free (register) |
| BNB Chain (BSC) | BscScan | Free (register) |
| Solana (SOL) | Solscan | No key needed |
| Bitcoin (BTC) | Blockchair | No key needed |
| Tron (TRX) | Trongrid | No key needed |

---

## Notes

- SQLite database resets on Railway redeploys (use `/watch` to re-add wallets)
- For persistence, upgrade Railway to paid OR switch to PostgreSQL (free on Supabase)
- Scan interval default: every 60 seconds
- All whale list wallets are pre-seeded (Binance, Kraken, Coinbase, etc.)

---

## Disclaimer
This bot is for informational purposes only.
Large wallet movements do not guarantee price direction.
Not financial advice.
