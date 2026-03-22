"""
Whale Ops Bot — Upgraded Classification Engine
Rules: wallet tagging, direction classification, tier sizing,
repetition confidence, net exchange flow bias, price confirmation.
"""
import asyncio, logging, aiosqlite, aiohttp, os, time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import TelegramError

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO,
                    handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

TOKEN           = os.getenv("TELEGRAM_BOT_TOKEN","")
ADMIN_IDS       = [int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()]
DB_PATH         = os.getenv("DATABASE_PATH","whale_bot.db")
ETHERSCAN_KEY   = os.getenv("ETHERSCAN_API_KEY","")
BSCSCAN_KEY     = os.getenv("BSCSCAN_API_KEY","")
WHALE_THRESHOLD = float(os.getenv("WHALE_THRESHOLD_USD","100000"))
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL_SECONDS","60"))
MAX_WALLETS_FREE= int(os.getenv("MAX_WALLETS_FREE","3"))
MAX_WALLETS_VIP = int(os.getenv("MAX_WALLETS_VIP","20"))

CHAIN_EMOJIS={"ETH":"🔷","BSC":"🟡","SOL":"🟣","BTC":"🟠","TRX":"🔴"}
VALID_CHAINS={"ETH","BSC","SOL","BTC","TRX"}
COIN_IDS={"ETH":"ethereum","BSC":"binancecoin","SOL":"solana","BTC":"bitcoin","TRX":"tron"}

EXCHANGE_ADDRESSES={
    "0x28c6c06298d514db089934071355e5743bf21d60",
    "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549",
    "0xf977814e90da44bfa03b6295a0616a897441acec",
    "0x8894e0a0c962cb723c1976a4421c95949be2d4e3",
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0",
    "0xcda4e840411c00a614ad9205caec807c7458a0e3",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d",
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b",
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40",
    "0xab5c66752a9e8167967685f1450532fb96d5d24f",
    "tjdensfbjs4rfett1x1w8wmdс8m5xnjhce",
    "34xp4vrocgjym3xr7ycvpfhocnxv4twseo",
}

KNOWN_WHALES=[
    {"address":"0x28c6c06298d514db089934071355e5743bf21d60","chain":"ETH","label":"Binance Hot Wallet"},
    {"address":"0xbe0eb53f46cd790cd13851d5eff43d12404d33e8","chain":"ETH","label":"Binance 7"},
    {"address":"0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0","chain":"ETH","label":"Kraken"},
    {"address":"0x8894e0a0c962cb723c1976a4421c95949be2d4e3","chain":"BSC","label":"Binance BSC Hot"},
    {"address":"TJDENsfBJs4RFETt1X1W8wMDc8M5XnJhCe","chain":"TRX","label":"Binance TRX Hot"},
    {"address":"34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo","chain":"BTC","label":"Binance BTC Hot"},
]

rolling_events=defaultdict(list)
exchange_flows=defaultdict(list)

def wallet_type(addr):
    return "EXCHANGE" if addr.lower() in EXCHANGE_ADDRESSES else "PRIVATE"

def classify_direction(ft,tt):
    if ft=="PRIVATE"  and tt=="EXCHANGE": return "DISTRIBUTION"
    if ft=="EXCHANGE" and tt=="PRIVATE":  return "ACCUMULATION"
    if ft=="EXCHANGE" and tt=="EXCHANGE": return "INTERNAL"
    return "NEUTRAL"

def get_tier(usd):
    if usd>=1_000_000: return "T3 🔴 $1M+"
    if usd>=250_000:   return "T2 🟠 $250k-$999k"
    return               "T1 🟡 $100k-$249k"

def get_confidence(chain,direction,now):
    window=1800
    rolling_events[chain]=[e for e in rolling_events[chain] if now-e[0]<=window]
    count=sum(1 for e in rolling_events[chain] if e[1]==direction)
    rolling_events[chain].append((now,direction))
    if count==0:   return "LOW",1
    if count<=2:   return "MEDIUM",count+1
    return "HIGH",count+1

def record_flow(chain,direction,usd,now):
    exchange_flows[chain].append((now,direction,usd))

def net_bias(chain,now):
    window=3600
    exchange_flows[chain]=[e for e in exchange_flows[chain] if now-e[0]<=window]
    inn=sum(e[2] for e in exchange_flows[chain] if e[1]=="IN")
    out=sum(e[2] for e in exchange_flows[chain] if e[1]=="OUT")
    total=inn+out
    if total==0: return "NEUTRAL"
    if inn>out and (inn-out)/total>=0.20: return "BEARISH"
    if out>inn and (out-inn)/total>=0.20: return "BULLISH"
    return "NEUTRAL"

def get_label(addr):
    for w in KNOWN_WHALES:
        if w["address"].lower()==addr.lower(): return w["label"]
    return ""

def explorer_tx(h,chain):
    m={"ETH":f"https://etherscan.io/tx/{h}","BSC":f"https://bscscan.com/tx/{h}",
       "SOL":f"https://solscan.io/tx/{h}","BTC":f"https://blockchair.com/bitcoin/transaction/{h}",
       "TRX":f"https://tronscan.org/#/transaction/{h}"}
    return m.get(chain,"")

def explorer_addr(a,chain):
    m={"ETH":f"https://etherscan.io/address/{a}","BSC":f"https://bscscan.com/address/{a}",
       "SOL":f"https://solscan.io/account/{a}","BTC":f"https://blockchair.com/bitcoin/address/{a}",
       "TRX":f"https://tronscan.org/#/address/{a}"}
    return m.get(chain,"")

def format_signal(direction,confidence,chain,usd,val_n,tier,bias,
                  from_addr,to_addr,from_lbl,to_lbl,txh):
    sig_map={"DISTRIBUTION":"🔴 DISTRIBUTION SIGNAL","ACCUMULATION":"🟢 ACCUMULATION SIGNAL",
             "NEUTRAL":"🟡 WATCH ONLY","INTERNAL":"⚪ INTERNAL MOVEMENT"}
    reason_map={
        "DISTRIBUTION":"Private wallet depositing to exchange. Whale preparing to sell.",
        "ACCUMULATION":"Exchange withdrawing to private wallet. Smart money accumulating.",
        "NEUTRAL":"Private-to-private transfer. Monitor for follow-up activity.",
        "INTERNAL":"Exchange-to-exchange reshuffle. Not a market signal.",
    }
    conf_e={"LOW":"🔅","MEDIUM":"🔆","HIGH":"💥"}.get(confidence,"🔅")
    bias_e={"BULLISH":"📈","BEARISH":"📉","NEUTRAL":"➡️"}.get(bias,"➡️")
    fl=from_lbl or (from_addr[:8]+"..."+from_addr[-4:] if from_addr else "Unknown")
    tl=to_lbl   or (to_addr[:8]+"..."+to_addr[-4:]     if to_addr   else "Unknown")
    ft=wallet_type(from_addr) if from_addr else "PRIVATE"
    tt=wallet_type(to_addr)   if to_addr   else "PRIVATE"

    bias_note=""
    if bias=="BULLISH" and direction=="ACCUMULATION": bias_note="✅ Net outflow confirms bullish bias."
    elif bias=="BEARISH" and direction=="DISTRIBUTION": bias_note="✅ Net inflow confirms bearish bias."
    elif bias!="NEUTRAL": bias_note=f"⚠️ Net flow ({bias}) conflicts — reduce conviction."

    msg=(f"{sig_map.get(direction,'🔔 SIGNAL')}\n"
         f"{'─'*26}\n"
         f"{CHAIN_EMOJIS.get(chain,'🔗')} Chain: <b>{chain}</b>\n"
         f"💰 Amount: {val_n:,.4f} {chain}\n"
         f"💵 USD: <b>${usd:,.0f}</b>\n"
         f"📦 Size: {tier}\n"
         f"{'─'*26}\n"
         f"📤 From: {fl} [{ft}]\n"
         f"📥 To:   {tl} [{tt}]\n"
         f"{'─'*26}\n"
         f"{conf_e} Confidence: <b>{confidence}</b>\n"
         f"{bias_e} Net Flow Bias: <b>{bias}</b>\n"
         f"{'─'*26}\n"
         f"📋 {reason_map.get(direction,'')}\n")
    if bias_note: msg+=f"{bias_note}\n"
    if txh: msg+=f"\n🔗 <a href='{explorer_tx(txh,chain)}'>View Transaction</a>\n"
    msg+=f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
    msg+="\n⚠️ <i>Not financial advice.</i>"
    return msg

# ── DB ─────────────────────────────────────────────────────────────────────────
async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,is_vip INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS watched_wallets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,address TEXT,chain TEXT,label TEXT DEFAULT '',threshold_usd REAL DEFAULT 0,UNIQUE(user_id,address,chain));
            CREATE TABLE IF NOT EXISTS whale_wallets(id INTEGER PRIMARY KEY AUTOINCREMENT,address TEXT,chain TEXT,label TEXT,UNIQUE(address,chain));
            CREATE TABLE IF NOT EXISTS alerts_log(id INTEGER PRIMARY KEY AUTOINCREMENT,alert_id TEXT UNIQUE,user_id INTEGER,address TEXT,chain TEXT,alert_type TEXT,amount_usd REAL,tx_hash TEXT,signal TEXT DEFAULT '',confidence TEXT DEFAULT '',fired_at TEXT DEFAULT(datetime('now')));
            CREATE TABLE IF NOT EXISTS wallet_state(address TEXT,chain TEXT,last_tx TEXT DEFAULT '',last_balance REAL DEFAULT 0,PRIMARY KEY(address,chain));
        """)
        await db.commit()
    async with aiosqlite.connect(DB_PATH) as db:
        for w in KNOWN_WHALES:
            await db.execute("INSERT OR IGNORE INTO whale_wallets(address,chain,label) VALUES(?,?,?)",(w["address"],w["chain"],w["label"]))
        await db.commit()

async def upsert_user(uid,un,fn):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(user_id,username,first_name) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name",(uid,un,fn))
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?",(uid,)) as c:
            r=await c.fetchone(); return dict(r) if r else None

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM users") as c: return [dict(r) for r in await c.fetchall()]

async def add_wallet(uid,addr,chain,label):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO watched_wallets(user_id,address,chain,label) VALUES(?,?,?,?)",(uid,addr.lower(),chain,label))
            await db.commit()
        return True
    except: return False

async def remove_wallet(uid,addr):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("DELETE FROM watched_wallets WHERE user_id=? AND address=?",(uid,addr.lower()))
        await db.commit(); return cur.rowcount>0

async def get_user_wallets(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM watched_wallets WHERE user_id=?",(uid,)) as c: return [dict(r) for r in await c.fetchall()]

async def count_user_wallets(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM watched_wallets WHERE user_id=?",(uid,)) as c: return (await c.fetchone())[0]

async def get_all_watched():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM watched_wallets WHERE user_id IS NOT NULL") as c: return [dict(r) for r in await c.fetchall()]

async def get_whale_wallets(chain=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        if chain:
            async with db.execute("SELECT * FROM whale_wallets WHERE chain=?",(chain,)) as c: return [dict(r) for r in await c.fetchall()]
        async with db.execute("SELECT * FROM whale_wallets") as c: return [dict(r) for r in await c.fetchall()]

async def add_whale(addr,chain,label):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO whale_wallets(address,chain,label) VALUES(?,?,?)",(addr.lower(),chain,label))
            await db.commit()
        return True
    except: return False

async def remove_whale(addr):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("DELETE FROM whale_wallets WHERE address=?",(addr.lower(),))
        await db.commit(); return cur.rowcount>0

async def alert_exists(aid):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM alerts_log WHERE alert_id=?",(aid,)) as c: return (await c.fetchone()) is not None

async def log_alert(aid,uid,addr,chain,atype,usd,txh,sig="",conf=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO alerts_log(alert_id,user_id,address,chain,alert_type,amount_usd,tx_hash,signal,confidence) VALUES(?,?,?,?,?,?,?,?,?)",(aid,uid,addr,chain,atype,usd,txh,sig,conf))
        await db.commit()

async def get_recent_alerts(uid,limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM alerts_log WHERE user_id=? ORDER BY fired_at DESC LIMIT ?",(uid,limit)) as c: return [dict(r) for r in await c.fetchall()]

async def get_wallet_state(addr,chain):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM wallet_state WHERE address=? AND chain=?",(addr.lower(),chain)) as c:
            r=await c.fetchone(); return dict(r) if r else {"last_tx":"","last_balance":0}

async def update_wallet_state(addr,chain,lt,lb):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO wallet_state(address,chain,last_tx,last_balance) VALUES(?,?,?,?) ON CONFLICT(address,chain) DO UPDATE SET last_tx=excluded.last_tx,last_balance=excluded.last_balance",(addr.lower(),chain,lt,lb))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: u=(await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM watched_wallets") as c: w=(await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM whale_wallets") as c: wh=(await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM alerts_log") as c: a=(await c.fetchone())[0]
    return {"users":u,"wallets":w,"whales":wh,"alerts":a}

# ── API ────────────────────────────────────────────────────────────────────────
_session:Optional[aiohttp.ClientSession]=None
_prices={}; _price_ts=0

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    return _session

async def get_prices():
    global _prices,_price_ts
    if time.time()-_price_ts<300 and _prices: return _prices
    try:
        ids=",".join(COIN_IDS.values()); s=await get_session()
        async with s.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd") as r:
            data=await r.json()
        _prices={chain:data.get(cid,{}).get("usd",0) for chain,cid in COIN_IDS.items()}
        _price_ts=time.time()
    except Exception as e: logger.warning(f"Price: {e}")
    return _prices

async def to_usd(amount,chain):
    p=await get_prices(); return amount*p.get(chain,0)

async def get_txs(addr,chain):
    results=[]
    try:
        s=await get_session()
        if chain in("ETH","BSC"):
            base="https://api.etherscan.io/api" if chain=="ETH" else "https://api.bscscan.com/api"
            key=ETHERSCAN_KEY if chain=="ETH" else BSCSCAN_KEY
            params={"module":"account","action":"txlist","address":addr,"page":1,"offset":10,"sort":"desc","apikey":key or "YourApiKeyToken"}
            async with s.get(base,params=params) as r: d=await r.json()
            if d.get("status")=="1":
                for tx in d.get("result",[]):
                    results.append({"hash":tx.get("hash",""),"value_native":int(tx.get("value",0))/1e18,
                                    "from_address":tx.get("from","").lower(),"to_address":tx.get("to","").lower()})
        elif chain=="TRX":
            async with s.get(f"https://api.trongrid.io/v1/accounts/{addr}/transactions",params={"limit":10,"order_by":"block_timestamp,desc"}) as r: d=await r.json()
            for tx in d.get("data",[]):
                try:
                    c=tx.get("raw_data",{}).get("contract",[{}])[0]; v=c.get("parameter",{}).get("value",{})
                    results.append({"hash":tx.get("txID",""),"value_native":v.get("amount",0)/1e6,
                                    "from_address":v.get("owner_address","").lower(),"to_address":v.get("to_address","").lower()})
                except: pass
        elif chain=="BTC":
            async with s.get(f"https://api.blockchair.com/bitcoin/dashboards/address/{addr}") as r: d=await r.json()
            for h in d.get("data",{}).get(addr,{}).get("transactions",[])[:10]:
                results.append({"hash":h,"value_native":0,"from_address":"","to_address":""})
        elif chain=="SOL":
            async with s.get("https://public-api.solscan.io/account/transactions",params={"account":addr,"limit":10}) as r: d=await r.json()
            for tx in(d if isinstance(d,list) else d.get("data",[])):
                results.append({"hash":tx.get("txHash",tx.get("signature","")),"value_native":abs(tx.get("lamport",0))/1e9,"from_address":"","to_address":""})
    except Exception as e: logger.error(f"get_txs {chain}: {e}")
    return results

async def get_balance(addr,chain):
    try:
        s=await get_session()
        if chain in("ETH","BSC"):
            base="https://api.etherscan.io/api" if chain=="ETH" else "https://api.bscscan.com/api"
            key=ETHERSCAN_KEY if chain=="ETH" else BSCSCAN_KEY
            async with s.get(base,params={"module":"account","action":"balance","address":addr,"tag":"latest","apikey":key or "YourApiKeyToken"}) as r: d=await r.json()
            return int(d["result"])/1e18 if d.get("status")=="1" else 0
        elif chain=="TRX":
            async with s.get(f"https://api.trongrid.io/v1/accounts/{addr}") as r: d=await r.json()
            return d.get("data",[{}])[0].get("balance",0)/1e6
        elif chain=="BTC":
            async with s.get(f"https://api.blockchair.com/bitcoin/dashboards/address/{addr}") as r: d=await r.json()
            return d.get("data",{}).get(addr,{}).get("address",{}).get("balance",0)/1e8
        elif chain=="SOL":
            async with s.get("https://public-api.solscan.io/account",params={"address":addr}) as r: d=await r.json()
            return d.get("lamports",0)/1e9
    except Exception as e: logger.error(f"balance {chain}: {e}")
    return 0

# ── Send ───────────────────────────────────────────────────────────────────────
async def send_safe(bot,chat_id,text):
    try: await bot.send_message(chat_id=chat_id,text=text,parse_mode="HTML",disable_web_page_preview=True)
    except TelegramError as e: logger.warning(f"Send {chat_id}: {e}")

# ── Scanner ────────────────────────────────────────────────────────────────────
async def process_tx(bot,tx,addr,chain,uid,label,threshold,broadcast=False):
    val_n=tx["value_native"]; val_usd=await to_usd(val_n,chain)
    if val_usd<threshold: return
    from_addr=tx["from_address"] or addr.lower()
    to_addr=tx["to_address"] or ""
    txh=tx["hash"]
    ft=wallet_type(from_addr); tt=wallet_type(to_addr) if to_addr else "PRIVATE"
    direction=classify_direction(ft,tt)

    # INTERNAL — skip, never a signal
    if direction=="INTERNAL":
        logger.info(f"INTERNAL skipped: {txh}"); return

    now=time.time()
    if tt=="EXCHANGE": record_flow(chain,"IN",val_usd,now)
    elif ft=="EXCHANGE": record_flow(chain,"OUT",val_usd,now)

    confidence,count=get_confidence(chain,direction,now)
    # Safety: no HIGH from single tx
    if count==1: confidence="LOW"

    bias=net_bias(chain,now)
    tier=get_tier(val_usd)
    from_lbl=get_label(from_addr) or (label if from_addr==addr.lower() else "")
    to_lbl=get_label(to_addr)

    aid=f"sig_{txh}_{direction}"
    if await alert_exists(aid): return

    msg=format_signal(direction,confidence,chain,val_usd,val_n,tier,bias,
                      from_addr,to_addr,from_lbl,to_lbl,txh)
    await log_alert(aid,uid if not broadcast else 0,addr,chain,direction.lower(),val_usd,txh,
                    f"{direction} SIGNAL" if direction!="NEUTRAL" else "WATCH ONLY",confidence)

    if broadcast:
        users=await get_all_users()
        for u in users: await send_safe(bot,u["user_id"],msg)
        cid=os.getenv("FREE_CHANNEL_ID","")
        if cid:
            try: await send_safe(bot,int(cid),msg)
            except: pass
    else:
        await send_safe(bot,uid,msg)
    logger.info(f"Signal: {direction} {chain} ${val_usd:,.0f} conf={confidence}")

async def scan_wallet(bot,addr,chain,uid,label,threshold):
    try:
        state=await get_wallet_state(addr,chain)
        last_tx=state["last_tx"]; last_bal=state["last_balance"]
        thresh=threshold or WHALE_THRESHOLD
        txs=await get_txs(addr,chain)
        if txs:
            newest=txs[0]["hash"]
            if newest and newest!=last_tx:
                for tx in txs:
                    if tx["hash"]==last_tx: break
                    await process_tx(bot,tx,addr,chain,uid,label,thresh,broadcast=False)
                await update_wallet_state(addr,chain,newest,last_bal)
        cur_bal=await get_balance(addr,chain)
        if last_bal>0:
            change=cur_bal-last_bal; change_usd=await to_usd(abs(change),chain); pct=abs(change/last_bal)*100
            if change_usd>=10000 or pct>=10:
                aid=f"bal_{addr}_{chain}_{int(cur_bal*100)}"
                if not await alert_exists(aid):
                    d="📈 INCREASED" if change>0 else "📉 DECREASED"
                    msg=(f"💼 <b>BALANCE CHANGE</b>\n{CHAIN_EMOJIS.get(chain,'🔗')} {chain} | "
                         f"<a href='{explorer_addr(addr,chain)}'>{label}</a>\n"
                         f"{d} by {abs(change):,.4f} (~${change_usd:,.0f}) {pct:.1f}%\n"
                         f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
                    await log_alert(aid,uid,addr,chain,"balance",change_usd,"")
                    await send_safe(bot,uid,msg)
        st2=await get_wallet_state(addr,chain)
        await update_wallet_state(addr,chain,st2["last_tx"],cur_bal)
    except Exception as e: logger.error(f"scan_wallet {addr}: {e}")

async def scan_whale(bot,addr,chain,label):
    try:
        state=await get_wallet_state(addr,chain); last_tx=state["last_tx"]
        txs=await get_txs(addr,chain)
        if not txs: return
        newest=txs[0]["hash"]
        if not newest or newest==last_tx: return
        for tx in txs:
            if tx["hash"]==last_tx: break
            await process_tx(bot,tx,addr,chain,0,label,WHALE_THRESHOLD,broadcast=True)
        await update_wallet_state(addr,chain,newest,state["last_balance"])
    except Exception as e: logger.error(f"scan_whale {addr}: {e}")

async def scan_loop(bot):
    while True:
        try:
            for w in await get_all_watched():
                await scan_wallet(bot,w["address"],w["chain"],w["user_id"],w["label"] or w["address"][:10]+"...",w["threshold_usd"])
                await asyncio.sleep(0.5)
            for w in await get_whale_wallets():
                await scan_whale(bot,w["address"],w["chain"],w["label"])
                await asyncio.sleep(1)
        except Exception as e: logger.error(f"scan_loop: {e}",exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)

# ── Commands ───────────────────────────────────────────────────────────────────
async def cmd_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; await upsert_user(u.id,u.username or "",u.first_name or "")
    kb=[[InlineKeyboardButton("👛 My Wallets",callback_data="u_wallets"),InlineKeyboardButton("🐋 Whales",callback_data="u_whales")],
        [InlineKeyboardButton("🔔 Signals",callback_data="u_alerts"),InlineKeyboardButton("📖 Help",callback_data="u_help")]]
    await update.message.reply_text(
        f"👋 Welcome, <b>{u.first_name}</b>!\n\n🐋 <b>Whale Ops</b> — On-Chain Intelligence\n\n"
        f"ETH 🔷 BSC 🟡 SOL 🟣 BTC 🟠 TRX 🔴\n\n"
        f"<b>Signal Types:</b>\n"
        f"🟢 ACCUMULATION — whales withdrawing (bullish)\n"
        f"🔴 DISTRIBUTION — whales depositing (bearish)\n"
        f"🟡 WATCH ONLY — private movement\n"
        f"⚪ INTERNAL — reshuffles (ignored)\n\n"
        f"/watch ADDRESS CHAIN — track a wallet\n/whales — known whale list\n/help — commands",
        parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def cmd_help(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Commands</b>\n\n/watch ADDRESS CHAIN [label]\n/unwatch ADDRESS\n"
        "/mywallet\n/alerts\n/whales\n/threshold USD\n\n"
        "<b>Confidence:</b>\n💥 HIGH = 4+ events/30min\n🔆 MEDIUM = 2-3\n🔅 LOW = single\n\n"
        "Chains: ETH · BSC · SOL · BTC · TRX",parse_mode="HTML")

async def cmd_watch(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2:
        await update.message.reply_text("Usage: /watch <code>ADDRESS CHAIN [label]</code>\nExample: <code>/watch 0xABC...123 ETH Whale</code>",parse_mode="HTML"); return
    addr=ctx.args[0].strip(); chain=ctx.args[1].upper().strip()
    label=" ".join(ctx.args[2:]) if len(ctx.args)>2 else addr[:10]+"..."
    if chain not in VALID_CHAINS: await update.message.reply_text("❌ Invalid chain. Use: ETH BSC SOL BTC TRX"); return
    uid=update.effective_user.id; user=await get_user(uid)
    is_vip=user.get("is_vip",0) if user else 0
    max_w=MAX_WALLETS_VIP if is_vip else MAX_WALLETS_FREE
    count=await count_user_wallets(uid)
    if count>=max_w: await update.message.reply_text(f"⚠️ Limit ({max_w}) reached. /unwatch one first."); return
    ok=await add_wallet(uid,addr,chain,label)
    wt=wallet_type(addr)
    if ok: await update.message.reply_text(f"✅ <b>Tracking!</b>\n{CHAIN_EMOJIS.get(chain,'🔗')} {chain} | {label}\n<code>{addr}</code>\nType: <b>{wt}</b> | {count+1}/{max_w}",parse_mode="HTML")
    else: await update.message.reply_text("⚠️ Already tracking.")

async def cmd_unwatch(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("Usage: /unwatch ADDRESS"); return
    ok=await remove_wallet(update.effective_user.id,ctx.args[0])
    await update.message.reply_text("✅ Removed." if ok else "⚠️ Not found.")

async def cmd_mywallet(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ws=await get_user_wallets(update.effective_user.id)
    if not ws: await update.message.reply_text("No wallets tracked.\nAdd: /watch ADDRESS CHAIN"); return
    lines=["👛 <b>Your Wallets</b>\n"]
    for w in ws: lines.append(f"{CHAIN_EMOJIS.get(w['chain'],'🔗')} <b>{w['label']}</b> [{w['chain']}] [{wallet_type(w['address'])}]\n<code>{w['address']}</code>")
    await update.message.reply_text("\n\n".join(lines),parse_mode="HTML")

async def cmd_alerts(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    alerts=await get_recent_alerts(update.effective_user.id)
    if not alerts: await update.message.reply_text("No signals yet. Monitoring 📡"); return
    em={"ACCUMULATION SIGNAL":"🟢","DISTRIBUTION SIGNAL":"🔴","WATCH ONLY":"🟡","INTERNAL MOVEMENT":"⚪"}
    lines=["🔔 <b>Recent Signals</b>\n"]
    for a in alerts:
        sig=a.get("signal") or a["alert_type"]; e=em.get(sig,"🔔"); cf=a.get("confidence","")
        lines.append(f"{e} {a['chain']} ${a['amount_usd']:,.0f} {cf} | {a['fired_at'][:16]}")
    await update.message.reply_text("\n".join(lines),parse_mode="HTML")

async def cmd_whales(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ws=await get_whale_wallets(); by_chain={}
    for w in ws: by_chain.setdefault(w["chain"],[]).append(w)
    lines=["🐋 <b>Whale Wallets</b>\n"]
    for chain,cws in by_chain.items():
        lines.append(f"\n{CHAIN_EMOJIS.get(chain,'🔗')} <b>{chain}</b>")
        for w in cws: lines.append(f"  • {w['label']} [EXCHANGE]")
    await update.message.reply_text("\n".join(lines),parse_mode="HTML")

async def cmd_threshold(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].replace(".","").isdigit():
        await update.message.reply_text("Usage: /threshold USD\nExample: /threshold 50000"); return
    amount=float(ctx.args[0])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE watched_wallets SET threshold_usd=? WHERE user_id=?",(amount,update.effective_user.id))
        await db.commit()
    await update.message.reply_text(f"✅ Threshold: ${amount:,.0f}")

def is_admin(u): return u.effective_user.id in ADMIN_IDS

async def cmd_admin(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): await update.message.reply_text("⛔ Admin only."); return
    s=await get_stats()
    await update.message.reply_text(f"🔧 <b>Admin</b>\n👥 {s['users']} users | 👛 {s['wallets']} wallets\n🐋 {s['whales']} whales | 🔔 {s['alerts']} alerts\n\n/addwhale ADDR CHAIN LABEL\n/removewhale ADDR\n/setinterval SEC\n/broadcast MSG\n/stats",parse_mode="HTML")

async def cmd_addwhale(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if len(ctx.args)<3: await update.message.reply_text("Usage: /addwhale ADDRESS CHAIN LABEL"); return
    addr,chain,label=ctx.args[0],ctx.args[1].upper()," ".join(ctx.args[2:])
    if chain not in VALID_CHAINS: await update.message.reply_text("Invalid chain."); return
    ok=await add_whale(addr,chain,label)
    await update.message.reply_text(f"✅ Added {label}" if ok else "⚠️ Exists.")

async def cmd_removewhale(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Usage: /removewhale ADDRESS"); return
    ok=await remove_whale(ctx.args[0])
    await update.message.reply_text("✅ Removed." if ok else "⚠️ Not found.")

async def cmd_broadcast(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Usage: /broadcast MSG"); return
    msg=" ".join(ctx.args); users=await get_all_users(); sent=failed=0
    for u in users:
        try: await ctx.bot.send_message(u["user_id"],f"📢 <b>Announcement</b>\n\n{msg}",parse_mode="HTML"); sent+=1
        except: failed+=1
    await update.message.reply_text(f"✅ Sent:{sent} Failed:{failed}")

async def cmd_stats(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s=await get_stats()
    await update.message.reply_text(f"📊 Users:{s['users']} Wallets:{s['wallets']} Whales:{s['whales']} Alerts:{s['alerts']}")

async def cmd_setinterval(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args or not ctx.args[0].isdigit(): await update.message.reply_text("Usage: /setinterval SEC"); return
    global SCAN_INTERVAL; SCAN_INTERVAL=max(30,int(ctx.args[0]))
    await update.message.reply_text(f"✅ Interval:{SCAN_INTERVAL}s")

async def handle_callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="u_wallets":
        ws=await get_user_wallets(q.from_user.id)
        if not ws: await q.edit_message_text("No wallets. Use /watch ADDRESS CHAIN"); return
        lines=["👛 <b>Wallets</b>\n"]
        for w in ws: lines.append(f"{CHAIN_EMOJIS.get(w['chain'],'🔗')} {w['label']} [{w['chain']}] [{wallet_type(w['address'])}]")
        await q.edit_message_text("\n".join(lines),parse_mode="HTML")
    elif q.data=="u_whales":
        ws=await get_whale_wallets()
        lines=[f"🐋 <b>{len(ws)} Whales</b>\n"]
        for w in ws[:10]: lines.append(f"{CHAIN_EMOJIS.get(w['chain'],'🔗')} {w['label']} [EXCHANGE]")
        await q.edit_message_text("\n".join(lines),parse_mode="HTML")
    elif q.data=="u_alerts":
        alerts=await get_recent_alerts(q.from_user.id,5)
        if not alerts: await q.edit_message_text("No signals yet 📡"); return
        lines=["🔔 <b>Signals</b>\n"]
        for a in alerts: lines.append(f"• {a['chain']} {a.get('signal',a['alert_type'])} {a['fired_at'][:16]}")
        await q.edit_message_text("\n".join(lines),parse_mode="HTML")
    elif q.data=="u_help":
        await q.edit_message_text("🟢 ACCUMULATION = exchange outflow (bullish)\n🔴 DISTRIBUTION = exchange inflow (bearish)\n🟡 WATCH ONLY = private movement\n⚪ INTERNAL = ignored\n\n💥 HIGH = 4+ events/30min\n🔆 MEDIUM = 2-3\n🔅 LOW = single",parse_mode="HTML")

async def main():
    if not TOKEN: logger.error("No TOKEN!"); return
    await db_init(); logger.info("Whale Ops starting...")
    app=Application.builder().token(TOKEN).build()
    for cmd,func in [("start",cmd_start),("help",cmd_help),("watch",cmd_watch),("unwatch",cmd_unwatch),
                     ("mywallet",cmd_mywallet),("alerts",cmd_alerts),("whales",cmd_whales),
                     ("threshold",cmd_threshold),("admin",cmd_admin),("addwhale",cmd_addwhale),
                     ("removewhale",cmd_removewhale),("broadcast",cmd_broadcast),("stats",cmd_stats),
                     ("setinterval",cmd_setinterval)]:
        app.add_handler(CommandHandler(cmd,func))
    app.add_handler(CallbackQueryHandler(handle_callback))
    await app.initialize(); await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    asyncio.create_task(scan_loop(app.bot))
    logger.info("Whale Ops live!")
    try: await asyncio.Event().wait()
    except(KeyboardInterrupt,SystemExit): pass
    finally:
        await app.updater.stop(); await app.stop(); await app.shutdown()
        if _session and not _session.closed: await _session.close()

if __name__=="__main__":
    asyncio.run(main())
