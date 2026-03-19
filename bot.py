"""
Whale & Wallet Tracker — Single File Bot
All code in one file to avoid import issues on Railway.
"""
import asyncio
import logging
import aiosqlite
import aiohttp
import json
import hashlib
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DB_PATH = os.getenv("DATABASE_PATH", "whale_bot.db")
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_KEY = os.getenv("BSCSCAN_API_KEY", "")
WHALE_THRESHOLD = float(os.getenv("WHALE_THRESHOLD_USD", "100000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
MAX_WALLETS_FREE = int(os.getenv("MAX_WALLETS_FREE", "3"))
MAX_WALLETS_VIP = int(os.getenv("MAX_WALLETS_VIP", "20"))

CHAIN_EMOJIS = {"ETH": "🔷", "BSC": "🟡", "SOL": "🟣", "BTC": "🟠", "TRX": "🔴"}
CHAIN_NAMES  = {"ETH": "Ethereum", "BSC": "BNB Chain", "SOL": "Solana", "BTC": "Bitcoin", "TRX": "Tron"}
VALID_CHAINS = {"ETH", "BSC", "SOL", "BTC", "TRX"}
COIN_IDS     = {"ETH": "ethereum", "BSC": "binancecoin", "SOL": "solana", "BTC": "bitcoin", "TRX": "tron"}

KNOWN_WHALES = [
    {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "chain": "ETH", "label": "Binance Hot Wallet"},
    {"address": "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8", "chain": "ETH", "label": "Binance 7"},
    {"address": "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0", "chain": "ETH", "label": "Kraken"},
    {"address": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "chain": "BSC", "label": "Binance BSC Hot"},
    {"address": "TJDENsfBJs4RFETt1X1W8wMDc8M5XnJhCe",        "chain": "TRX", "label": "Binance TRX Hot"},
    {"address": "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",         "chain": "BTC", "label": "Binance BTC Hot"},
]

# ── Database ───────────────────────────────────────────────────────────────────
async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, is_vip INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS watched_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, address TEXT, chain TEXT, label TEXT DEFAULT '',
                threshold_usd REAL DEFAULT 0,
                UNIQUE(user_id, address, chain)
            );
            CREATE TABLE IF NOT EXISTS whale_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT, chain TEXT, label TEXT,
                UNIQUE(address, chain)
            );
            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT UNIQUE, user_id INTEGER, address TEXT, chain TEXT,
                alert_type TEXT, amount_usd REAL, tx_hash TEXT,
                fired_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS wallet_state (
                address TEXT, chain TEXT, last_tx TEXT DEFAULT '', last_balance REAL DEFAULT 0,
                PRIMARY KEY(address, chain)
            );
        """)
        await db.commit()
    # Seed whales
    async with aiosqlite.connect(DB_PATH) as db:
        for w in KNOWN_WHALES:
            await db.execute(
                "INSERT OR IGNORE INTO whale_wallets(address,chain,label) VALUES(?,?,?)",
                (w["address"], w["chain"], w["label"])
            )
        await db.commit()

async def upsert_user(user_id, username, first_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users(user_id,username,first_name) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name)
        )
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as c:
            return [dict(r) for r in await c.fetchall()]

async def add_wallet(user_id, address, chain, label):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO watched_wallets(user_id,address,chain,label) VALUES(?,?,?,?)",
                (user_id, address.lower(), chain, label)
            )
            await db.commit()
        return True
    except:
        return False

async def remove_wallet(user_id, address):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM watched_wallets WHERE user_id=? AND address=?",
            (user_id, address.lower())
        )
        await db.commit()
        return cur.rowcount > 0

async def get_user_wallets(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM watched_wallets WHERE user_id=?", (user_id,)) as c:
            return [dict(r) for r in await c.fetchall()]

async def count_user_wallets(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM watched_wallets WHERE user_id=?", (user_id,)) as c:
            return (await c.fetchone())[0]

async def get_all_watched():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM watched_wallets WHERE user_id IS NOT NULL") as c:
            return [dict(r) for r in await c.fetchall()]

async def get_whale_wallets(chain=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if chain:
            async with db.execute("SELECT * FROM whale_wallets WHERE chain=?", (chain,)) as c:
                return [dict(r) for r in await c.fetchall()]
        async with db.execute("SELECT * FROM whale_wallets") as c:
            return [dict(r) for r in await c.fetchall()]

async def add_whale(address, chain, label):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO whale_wallets(address,chain,label) VALUES(?,?,?)",
                             (address.lower(), chain, label))
            await db.commit()
        return True
    except:
        return False

async def remove_whale(address):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM whale_wallets WHERE address=?", (address.lower(),))
        await db.commit()
        return cur.rowcount > 0

async def alert_exists(alert_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM alerts_log WHERE alert_id=?", (alert_id,)) as c:
            return (await c.fetchone()) is not None

async def log_alert(alert_id, user_id, address, chain, alert_type, amount_usd, tx_hash):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO alerts_log(alert_id,user_id,address,chain,alert_type,amount_usd,tx_hash) "
            "VALUES(?,?,?,?,?,?,?)",
            (alert_id, user_id, address, chain, alert_type, amount_usd, tx_hash)
        )
        await db.commit()

async def get_recent_alerts(user_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM alerts_log WHERE user_id=? ORDER BY fired_at DESC LIMIT ?",
            (user_id, limit)
        ) as c:
            return [dict(r) for r in await c.fetchall()]

async def get_wallet_state(address, chain):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM wallet_state WHERE address=? AND chain=?",
                              (address.lower(), chain)) as c:
            r = await c.fetchone()
            return dict(r) if r else {"last_tx": "", "last_balance": 0}

async def update_wallet_state(address, chain, last_tx, last_balance):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_state(address,chain,last_tx,last_balance) VALUES(?,?,?,?) "
            "ON CONFLICT(address,chain) DO UPDATE SET last_tx=excluded.last_tx, last_balance=excluded.last_balance",
            (address.lower(), chain, last_tx, last_balance)
        )
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM watched_wallets") as c: wallets = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM whale_wallets") as c: whales = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM alerts_log") as c: alerts = (await c.fetchone())[0]
    return {"users": users, "wallets": wallets, "whales": whales, "alerts": alerts}

# ── Chain API ──────────────────────────────────────────────────────────────────
_session: Optional[aiohttp.ClientSession] = None
_prices = {}
_price_ts = 0

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _session

async def get_prices():
    global _prices, _price_ts
    import time
    if time.time() - _price_ts < 300 and _prices:
        return _prices
    try:
        ids = ",".join(COIN_IDS.values())
        s = await get_session()
        async with s.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd") as r:
            data = await r.json()
        _prices = {chain: data.get(cid, {}).get("usd", 0) for chain, cid in COIN_IDS.items()}
        _price_ts = time.time()
    except Exception as e:
        logger.warning(f"Price fetch failed: {e}")
    return _prices

async def to_usd(amount, chain):
    prices = await get_prices()
    return amount * prices.get(chain, 0)

async def get_txs(address, chain):
    try:
        s = await get_session()
        if chain == "ETH":
            params = {"module":"account","action":"txlist","address":address,
                      "page":1,"offset":5,"sort":"desc","apikey": ETHERSCAN_KEY or "YourApiKeyToken"}
            async with s.get("https://api.etherscan.io/api", params=params) as r:
                d = await r.json()
            return d.get("result", []) if d.get("status") == "1" else []
        elif chain == "BSC":
            params = {"module":"account","action":"txlist","address":address,
                      "page":1,"offset":5,"sort":"desc","apikey": BSCSCAN_KEY or "YourApiKeyToken"}
            async with s.get("https://api.bscscan.com/api", params=params) as r:
                d = await r.json()
            return d.get("result", []) if d.get("status") == "1" else []
        elif chain == "TRX":
            async with s.get(f"https://api.trongrid.io/v1/accounts/{address}/transactions",
                             params={"limit":5,"order_by":"block_timestamp,desc"}) as r:
                d = await r.json()
            return d.get("data", [])
        elif chain == "BTC":
            async with s.get(f"https://api.blockchair.com/bitcoin/dashboards/address/{address}") as r:
                d = await r.json()
            txs = d.get("data", {}).get(address, {}).get("transactions", [])
            return [{"hash": t, "chain": "BTC"} for t in txs[:5]]
        elif chain == "SOL":
            async with s.get(f"https://public-api.solscan.io/account/transactions",
                             params={"account":address,"limit":5}) as r:
                d = await r.json()
            return d if isinstance(d, list) else d.get("data", [])
    except Exception as e:
        logger.error(f"get_txs {chain} {address}: {e}")
    return []

async def get_balance(address, chain):
    try:
        s = await get_session()
        if chain == "ETH":
            params = {"module":"account","action":"balance","address":address,
                      "tag":"latest","apikey": ETHERSCAN_KEY or "YourApiKeyToken"}
            async with s.get("https://api.etherscan.io/api", params=params) as r:
                d = await r.json()
            return int(d["result"]) / 1e18 if d.get("status") == "1" else 0
        elif chain == "BSC":
            params = {"module":"account","action":"balance","address":address,
                      "tag":"latest","apikey": BSCSCAN_KEY or "YourApiKeyToken"}
            async with s.get("https://api.bscscan.com/api", params=params) as r:
                d = await r.json()
            return int(d["result"]) / 1e18 if d.get("status") == "1" else 0
        elif chain == "TRX":
            async with s.get(f"https://api.trongrid.io/v1/accounts/{address}") as r:
                d = await r.json()
            return d.get("data", [{}])[0].get("balance", 0) / 1e6
        elif chain == "BTC":
            async with s.get(f"https://api.blockchair.com/bitcoin/dashboards/address/{address}") as r:
                d = await r.json()
            return d.get("data",{}).get(address,{}).get("address",{}).get("balance",0) / 1e8
        elif chain == "SOL":
            async with s.get(f"https://public-api.solscan.io/account", params={"address":address}) as r:
                d = await r.json()
            return d.get("lamports", 0) / 1e9
    except Exception as e:
        logger.error(f"get_balance {chain} {address}: {e}")
    return 0

def tx_hash(tx, chain):
    if chain in ("ETH","BSC"): return tx.get("hash","")
    if chain == "SOL": return tx.get("txHash", tx.get("signature",""))
    if chain == "BTC": return tx.get("hash","")
    if chain == "TRX": return tx.get("txID","")
    return ""

def tx_value(tx, chain):
    try:
        if chain in ("ETH","BSC"): return int(tx.get("value",0)) / 1e18
        if chain == "TRX":
            c = tx.get("raw_data",{}).get("contract",[{}])[0]
            return c.get("parameter",{}).get("value",{}).get("amount",0) / 1e6
        if chain == "SOL": return abs(tx.get("lamport",0)) / 1e9
    except: pass
    return 0

def explorer_tx(tx_hash_val, chain):
    links = {"ETH": f"https://etherscan.io/tx/{tx_hash_val}",
             "BSC": f"https://bscscan.com/tx/{tx_hash_val}",
             "SOL": f"https://solscan.io/tx/{tx_hash_val}",
             "BTC": f"https://blockchair.com/bitcoin/transaction/{tx_hash_val}",
             "TRX": f"https://tronscan.org/#/transaction/{tx_hash_val}"}
    return links.get(chain, "")

def explorer_addr(address, chain):
    links = {"ETH": f"https://etherscan.io/address/{address}",
             "BSC": f"https://bscscan.com/address/{address}",
             "SOL": f"https://solscan.io/account/{address}",
             "BTC": f"https://blockchair.com/bitcoin/address/{address}",
             "TRX": f"https://tronscan.org/#/address/{address}"}
    return links.get(chain, "")

# ── Alert Formatters ───────────────────────────────────────────────────────────
def fmt_transfer(address, chain, label, txh, val_native, val_usd):
    e = CHAIN_EMOJIS.get(chain,"🔗")
    return (f"🐋 <b>WHALE TRANSFER DETECTED</b>\n{'─'*26}\n"
            f"{e} Chain: <b>{chain}</b>\n"
            f"👛 Wallet: <a href='{explorer_addr(address,chain)}'>{label}</a>\n"
            f"💸 Amount: {val_native:,.4f} {chain}\n"
            f"💵 USD: <b>${val_usd:,.0f}</b>\n{'─'*26}\n"
            f"🔗 <a href='{explorer_tx(txh,chain)}'>View Transaction</a>\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

def fmt_whale(address, chain, label, txh, val_native, val_usd):
    e = CHAIN_EMOJIS.get(chain,"🔗")
    return (f"🚨 <b>KNOWN WHALE ALERT</b> 🚨\n{'─'*26}\n"
            f"{e} Chain: <b>{chain}</b>\n"
            f"🏷 Whale: <a href='{explorer_addr(address,chain)}'>{label}</a>\n"
            f"💸 Amount: {val_native:,.4f} {chain}\n"
            f"💵 USD: <b>${val_usd:,.0f}</b>\n{'─'*26}\n"
            f"🔗 <a href='{explorer_tx(txh,chain)}'>View Transaction</a>\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n\n"
            f"📡 <i>Global whale broadcast</i>")

def fmt_balance(address, chain, label, old, new, change, change_usd, pct):
    e = CHAIN_EMOJIS.get(chain,"🔗")
    d = "📈 INCREASED" if change > 0 else "📉 DECREASED"
    return (f"💼 <b>BALANCE CHANGE</b>\n{'─'*26}\n"
            f"{e} Chain: <b>{chain}</b>\n"
            f"👛 <a href='{explorer_addr(address,chain)}'>{label}</a>\n"
            f"{d}\n"
            f"Before: {old:,.4f} {chain}\n"
            f"After:  {new:,.4f} {chain}\n"
            f"Change: {abs(change):,.4f} (~${change_usd:,.0f}) {pct:.1f}%\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

# ── Send helper ────────────────────────────────────────────────────────────────
async def send_safe(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text,
                               parse_mode="HTML", disable_web_page_preview=True)
    except TelegramError as e:
        logger.warning(f"Send failed {chat_id}: {e}")

# ── Scanner ────────────────────────────────────────────────────────────────────
async def scan_loop(bot):
    while True:
        try:
            # Scan user wallets
            wallets = await get_all_watched()
            for w in wallets:
                await scan_wallet(bot, w["address"], w["chain"],
                                  w["user_id"], w["label"] or w["address"][:10]+"...",
                                  w["threshold_usd"])
                await asyncio.sleep(0.5)

            # Scan whale wallets → broadcast
            whales = await get_whale_wallets()
            for w in whales:
                await scan_whale(bot, w["address"], w["chain"], w["label"])
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Scan loop error: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)

async def scan_wallet(bot, address, chain, user_id, label, threshold):
    try:
        state = await get_wallet_state(address, chain)
        last_tx = state["last_tx"]
        last_bal = state["last_balance"]
        thresh = threshold or WHALE_THRESHOLD

        txs = await get_txs(address, chain)
        if txs:
            newest = tx_hash(txs[0], chain)
            if newest and newest != last_tx:
                for t in txs:
                    h = tx_hash(t, chain)
                    if h == last_tx: break
                    val_n = tx_value(t, chain)
                    val_usd = await to_usd(val_n, chain)
                    if val_usd >= thresh:
                        aid = f"transfer_{h}"
                        if not await alert_exists(aid):
                            msg = fmt_transfer(address, chain, label, h, val_n, val_usd)
                            await log_alert(aid, user_id, address, chain, "transfer", val_usd, h)
                            await send_safe(bot, user_id, msg)
                await update_wallet_state(address, chain, newest, last_bal)

        # Balance check
        cur_bal = await get_balance(address, chain)
        if last_bal > 0:
            change = cur_bal - last_bal
            change_usd = await to_usd(abs(change), chain)
            pct = abs(change / last_bal) * 100
            if change_usd >= 10000 or pct >= 10:
                aid = f"bal_{address}_{chain}_{int(cur_bal*100)}"
                if not await alert_exists(aid):
                    msg = fmt_balance(address, chain, label, last_bal, cur_bal, change, change_usd, pct)
                    await log_alert(aid, user_id, address, chain, "balance", change_usd, "")
                    await send_safe(bot, user_id, msg)
        state2 = await get_wallet_state(address, chain)
        await update_wallet_state(address, chain, state2["last_tx"], cur_bal)
    except Exception as e:
        logger.error(f"scan_wallet {address} {chain}: {e}")

async def scan_whale(bot, address, chain, label):
    try:
        state = await get_wallet_state(address, chain)
        last_tx_val = state["last_tx"]
        txs = await get_txs(address, chain)
        if not txs: return
        newest = tx_hash(txs[0], chain)
        if not newest or newest == last_tx_val: return
        for t in txs:
            h = tx_hash(t, chain)
            if h == last_tx_val: break
            val_n = tx_value(t, chain)
            val_usd = await to_usd(val_n, chain)
            if val_usd >= WHALE_THRESHOLD:
                aid = f"whale_{h}"
                if not await alert_exists(aid):
                    msg = fmt_whale(address, chain, label, h, val_n, val_usd)
                    await log_alert(aid, 0, address, chain, "whale", val_usd, h)
                    users = await get_all_users()
                    for u in users:
                        await send_safe(bot, u["user_id"], msg)
        await update_wallet_state(address, chain, newest, state["last_balance"])
    except Exception as e:
        logger.error(f"scan_whale {address} {chain}: {e}")

# ── User Commands ──────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")
    kb = [[InlineKeyboardButton("👛 My Wallets", callback_data="u_wallets"),
           InlineKeyboardButton("🐋 Whale List", callback_data="u_whales")],
          [InlineKeyboardButton("🔔 Recent Alerts", callback_data="u_alerts"),
           InlineKeyboardButton("📖 Help", callback_data="u_help")]]
    await update.message.reply_text(
        f"👋 Welcome, <b>{u.first_name}</b>!\n\n"
        f"🐋 <b>Whale & Wallet Tracker</b>\n\n"
        f"Monitoring ETH 🔷 BSC 🟡 SOL 🟣 BTC 🟠 TRX 🔴\n\n"
        f"• 🐋 Whale transfers ($100k+)\n"
        f"• 🧠 Smart money moves\n"
        f"• 💼 Balance changes\n\n"
        f"<b>Start:</b> /watch ADDRESS CHAIN\n"
        f"/whales — known whale list\n/help — all commands",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Commands</b>\n\n"
        "/watch ADDRESS CHAIN [label]\n"
        "  Example: <code>/watch 0xABC...123 ETH Binance</code>\n\n"
        "/unwatch ADDRESS\n"
        "/mywallet — your wallets\n"
        "/alerts — recent alerts\n"
        "/threshold USD — alert minimum\n"
        "/whales — global whale list\n\n"
        "Chains: ETH · BSC · SOL · BTC · TRX",
        parse_mode="HTML"
    )

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /watch <code>ADDRESS CHAIN [label]</code>\n"
            "Example: <code>/watch 0xABC...123 ETH MyWhale</code>",
            parse_mode="HTML"
        )
        return
    address = ctx.args[0].strip()
    chain = ctx.args[1].upper().strip()
    label = " ".join(ctx.args[2:]) if len(ctx.args) > 2 else address[:10]+"..."
    if chain not in VALID_CHAINS:
        await update.message.reply_text(f"❌ Invalid chain. Use: ETH BSC SOL BTC TRX")
        return
    user_id = update.effective_user.id
    user = await get_user(user_id)
    is_vip = user.get("is_vip", 0) if user else 0
    max_w = MAX_WALLETS_VIP if is_vip else MAX_WALLETS_FREE
    count = await count_user_wallets(user_id)
    if count >= max_w:
        await update.message.reply_text(f"⚠️ Wallet limit ({max_w}) reached. Remove one first with /unwatch")
        return
    ok = await add_wallet(user_id, address, chain, label)
    if ok:
        e = CHAIN_EMOJIS.get(chain,"🔗")
        await update.message.reply_text(
            f"✅ <b>Tracking!</b>\n{e} {chain} | {label}\n<code>{address}</code>\n\n"
            f"Wallets: {count+1}/{max_w}", parse_mode="HTML"
        )
    else:
        await update.message.reply_text("⚠️ Already tracking that wallet.")

async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /unwatch ADDRESS")
        return
    ok = await remove_wallet(update.effective_user.id, ctx.args[0])
    await update.message.reply_text("✅ Removed." if ok else "⚠️ Not found.")

async def cmd_mywallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ws = await get_user_wallets(update.effective_user.id)
    if not ws:
        await update.message.reply_text("No wallets tracked yet.\nAdd one: /watch ADDRESS CHAIN")
        return
    lines = ["👛 <b>Your Wallets</b>\n"]
    for w in ws:
        e = CHAIN_EMOJIS.get(w["chain"],"🔗")
        lines.append(f"{e} <b>{w['label']}</b> [{w['chain']}]\n<code>{w['address']}</code>")
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    alerts = await get_recent_alerts(update.effective_user.id)
    if not alerts:
        await update.message.reply_text("No alerts yet. Monitoring in progress 📡")
        return
    emojis = {"transfer":"💸","buy":"🧠","balance":"💼","whale":"🐋"}
    lines = ["🔔 <b>Recent Alerts</b>\n"]
    for a in alerts:
        e = emojis.get(a["alert_type"],"🔔")
        usd = f"${a['amount_usd']:,.0f}" if a["amount_usd"] else ""
        lines.append(f"{e} {a['chain']} {a['alert_type'].upper()} {usd} | {a['fired_at'][:16]}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ws = await get_whale_wallets()
    by_chain = {}
    for w in ws:
        by_chain.setdefault(w["chain"], []).append(w)
    lines = ["🐋 <b>Whale Wallets</b> (auto-tracked for all)\n"]
    for chain, cws in by_chain.items():
        e = CHAIN_EMOJIS.get(chain,"🔗")
        lines.append(f"\n{e} <b>{chain}</b>")
        for w in cws:
            lines.append(f"  • {w['label']}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_threshold(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].replace(".","").isdigit():
        await update.message.reply_text("Usage: /threshold USD\nExample: /threshold 50000")
        return
    amount = float(ctx.args[0])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE watched_wallets SET threshold_usd=? WHERE user_id=?",
                         (amount, update.effective_user.id))
        await db.commit()
    await update.message.reply_text(f"✅ Alert threshold set to ${amount:,.0f}")

# ── Admin Commands ─────────────────────────────────────────────────────────────
def is_admin(update):
    return update.effective_user.id in ADMIN_IDS

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Admin only.")
        return
    stats = await get_stats()
    await update.message.reply_text(
        f"🔧 <b>Admin Panel</b>\n\n"
        f"👥 Users: {stats['users']}\n👛 Wallets: {stats['wallets']}\n"
        f"🐋 Whales: {stats['whales']}\n🔔 Alerts: {stats['alerts']}\n\n"
        f"/addwhale ADDRESS CHAIN LABEL\n/removewhale ADDRESS\n"
        f"/setinterval SECONDS\n/broadcast MESSAGE\n/stats",
        parse_mode="HTML"
    )

async def cmd_addwhale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if len(ctx.args) < 3:
        await update.message.reply_text("Usage: /addwhale ADDRESS CHAIN LABEL")
        return
    address, chain, label = ctx.args[0], ctx.args[1].upper(), " ".join(ctx.args[2:])
    if chain not in VALID_CHAINS:
        await update.message.reply_text("Invalid chain.")
        return
    ok = await add_whale(address, chain, label)
    await update.message.reply_text(f"✅ Added {label}" if ok else "⚠️ Already exists.")

async def cmd_removewhale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /removewhale ADDRESS")
        return
    ok = await remove_whale(ctx.args[0])
    await update.message.reply_text("✅ Removed." if ok else "⚠️ Not found.")

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast MESSAGE")
        return
    msg = " ".join(ctx.args)
    users = await get_all_users()
    sent = failed = 0
    for u in users:
        try:
            await ctx.bot.send_message(u["user_id"], f"📢 <b>Announcement</b>\n\n{msg}", parse_mode="HTML")
            sent += 1
        except: failed += 1
    await update.message.reply_text(f"✅ Sent: {sent} | Failed: {failed}")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    stats = await get_stats()
    await update.message.reply_text(
        f"📊 Users: {stats['users']} | Wallets: {stats['wallets']} | "
        f"Whales: {stats['whales']} | Alerts: {stats['alerts']}"
    )

async def cmd_setinterval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /setinterval SECONDS")
        return
    global SCAN_INTERVAL
    SCAN_INTERVAL = max(30, int(ctx.args[0]))
    await update.message.reply_text(f"✅ Interval set to {SCAN_INTERVAL}s")

# ── Callbacks ──────────────────────────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "u_wallets":
        ws = await get_user_wallets(q.from_user.id)
        if not ws:
            await q.edit_message_text("No wallets yet. Use /watch ADDRESS CHAIN")
            return
        lines = ["👛 <b>Your Wallets</b>\n"]
        for w in ws:
            lines.append(f"{CHAIN_EMOJIS.get(w['chain'],'🔗')} {w['label']} [{w['chain']}]")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML")
    elif q.data == "u_whales":
        ws = await get_whale_wallets()
        lines = [f"🐋 <b>{len(ws)} Whales Tracked</b>\n"]
        for w in ws[:10]:
            lines.append(f"{CHAIN_EMOJIS.get(w['chain'],'🔗')} {w['label']} [{w['chain']}]")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML")
    elif q.data == "u_alerts":
        alerts = await get_recent_alerts(q.from_user.id, 5)
        if not alerts:
            await q.edit_message_text("No alerts yet 📡")
            return
        lines = ["🔔 <b>Recent Alerts</b>\n"]
        for a in alerts:
            lines.append(f"• {a['chain']} {a['alert_type']} {a['fired_at'][:16]}")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML")
    elif q.data == "u_help":
        await q.edit_message_text(
            "📖 /watch ADDRESS CHAIN\n/unwatch ADDRESS\n"
            "/mywallet\n/alerts\n/whales\n/threshold USD",
            parse_mode="HTML"
        )

# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return

    await db_init()
    logger.info("Database ready.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("watch",       cmd_watch))
    app.add_handler(CommandHandler("unwatch",     cmd_unwatch))
    app.add_handler(CommandHandler("mywallet",    cmd_mywallet))
    app.add_handler(CommandHandler("alerts",      cmd_alerts))
    app.add_handler(CommandHandler("whales",      cmd_whales))
    app.add_handler(CommandHandler("threshold",   cmd_threshold))
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("addwhale",    cmd_addwhale))
    app.add_handler(CommandHandler("removewhale", cmd_removewhale))
    app.add_handler(CommandHandler("broadcast",   cmd_broadcast))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("setinterval", cmd_setinterval))
    app.add_handler(CallbackQueryHandler(handle_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Start scanner in background
    asyncio.create_task(scan_loop(app.bot))

    logger.info("Bot is running!")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        if _session and not _session.closed:
            await _session.close()

if __name__ == "__main__":
    asyncio.run(main())
