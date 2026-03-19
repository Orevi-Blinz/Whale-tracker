"""
Database — async SQLite for users, watched wallets, alerts, whale list.
"""
import aiosqlite
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str):
        self.path = path

    async def initialize(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                -- Bot users
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    username    TEXT,
                    first_name  TEXT,
                    is_vip      INTEGER DEFAULT 0,
                    joined_at   TEXT DEFAULT (datetime('now'))
                );

                -- Wallets being watched (user-added OR system whale list)
                CREATE TABLE IF NOT EXISTS watched_wallets (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER,            -- NULL = global whale list
                    address     TEXT NOT NULL,
                    chain       TEXT NOT NULL,      -- ETH/BSC/SOL/BTC/TRX
                    label       TEXT DEFAULT '',    -- User-set nickname
                    threshold_usd  REAL DEFAULT 0, -- 0 = use global default
                    track_transfers INTEGER DEFAULT 1,
                    track_buys      INTEGER DEFAULT 1,
                    track_balance   INTEGER DEFAULT 1,
                    added_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, address, chain)
                );

                -- Alert delivery log (dedup + history)
                CREATE TABLE IF NOT EXISTS alerts_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id    TEXT UNIQUE NOT NULL,
                    user_id     INTEGER,
                    address     TEXT,
                    chain       TEXT,
                    alert_type  TEXT,   -- transfer/buy/balance/smart_money
                    amount_usd  REAL,
                    tx_hash     TEXT,
                    message     TEXT,
                    fired_at    TEXT DEFAULT (datetime('now'))
                );

                -- Last known tx hash per wallet (for change detection)
                CREATE TABLE IF NOT EXISTS wallet_state (
                    address     TEXT NOT NULL,
                    chain       TEXT NOT NULL,
                    last_tx     TEXT DEFAULT '',
                    last_balance REAL DEFAULT 0,
                    updated_at  TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (address, chain)
                );

                -- Bot config
                CREATE TABLE IF NOT EXISTS bot_config (
                    key     TEXT PRIMARY KEY,
                    value   TEXT NOT NULL
                );

                -- Global whale wallet list (admin-managed)
                CREATE TABLE IF NOT EXISTS whale_wallets (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    address     TEXT NOT NULL,
                    chain       TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    added_by    INTEGER,
                    added_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(address, chain)
                );
            """)
            await db.commit()

        await self._seed_defaults()
        await self._seed_whales()

    async def _seed_defaults(self):
        from config import Config
        defaults = {
            "scan_interval_seconds": str(Config.SCAN_INTERVAL_SECONDS),
            "whale_threshold_usd": str(Config.DEFAULT_WHALE_THRESHOLD_USD),
            "token_buy_usd": str(Config.DEFAULT_TOKEN_BUY_USD),
        }
        async with aiosqlite.connect(self.path) as db:
            for k, v in defaults.items():
                await db.execute("INSERT OR IGNORE INTO bot_config(key,value) VALUES(?,?)", (k, v))
            await db.commit()

    async def _seed_whales(self):
        from config import Config
        async with aiosqlite.connect(self.path) as db:
            for w in Config.KNOWN_WHALE_WALLETS:
                await db.execute(
                    "INSERT OR IGNORE INTO whale_wallets(address,chain,label) VALUES(?,?,?)",
                    (w["address"], w["chain"], w["label"])
                )
            await db.commit()

    # ── Config ─────────────────────────────────────────────────────────────────
    async def get_config(self, key: str) -> Optional[str]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT value FROM bot_config WHERE key=?", (key,)) as c:
                r = await c.fetchone()
                return r[0] if r else None

    async def set_config(self, key: str, value: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT OR REPLACE INTO bot_config(key,value) VALUES(?,?)", (key, value))
            await db.commit()

    # ── Users ──────────────────────────────────────────────────────────────────
    async def upsert_user(self, user_id: int, username: str, first_name: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                   first_name=excluded.first_name""",
                (user_id, username, first_name)
            )
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as c:
                r = await c.fetchone()
                return dict(r) if r else None

    async def get_all_users(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users") as c:
                return [dict(r) for r in await c.fetchall()]

    async def count_user_wallets(self, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM watched_wallets WHERE user_id=?", (user_id,)
            ) as c:
                return (await c.fetchone())[0]

    # ── Watched Wallets ────────────────────────────────────────────────────────
    async def add_wallet(self, user_id: int, address: str, chain: str,
                         label: str = "", threshold_usd: float = 0) -> bool:
        """Returns False if already exists."""
        try:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    """INSERT INTO watched_wallets
                       (user_id,address,chain,label,threshold_usd) VALUES(?,?,?,?,?)""",
                    (user_id, address.lower(), chain, label, threshold_usd)
                )
                await db.commit()
            return True
        except Exception:
            return False

    async def remove_wallet(self, user_id: int, address: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM watched_wallets WHERE user_id=? AND address=?",
                (user_id, address.lower())
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_user_wallets(self, user_id: int) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM watched_wallets WHERE user_id=?", (user_id,)
            ) as c:
                return [dict(r) for r in await c.fetchall()]

    async def get_all_watched_wallets(self) -> list[dict]:
        """All user-added wallets (for scanning)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM watched_wallets WHERE user_id IS NOT NULL"
            ) as c:
                return [dict(r) for r in await c.fetchall()]

    # ── Whale List ─────────────────────────────────────────────────────────────
    async def get_whale_wallets(self, chain: str = None) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if chain:
                async with db.execute(
                    "SELECT * FROM whale_wallets WHERE chain=?", (chain,)
                ) as c:
                    return [dict(r) for r in await c.fetchall()]
            async with db.execute("SELECT * FROM whale_wallets") as c:
                return [dict(r) for r in await c.fetchall()]

    async def add_whale_wallet(self, address: str, chain: str, label: str, added_by: int) -> bool:
        try:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "INSERT INTO whale_wallets(address,chain,label,added_by) VALUES(?,?,?,?)",
                    (address.lower(), chain, label, added_by)
                )
                await db.commit()
            return True
        except Exception:
            return False

    async def remove_whale_wallet(self, address: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM whale_wallets WHERE address=?", (address.lower(),)
            )
            await db.commit()
            return cur.rowcount > 0

    # ── Wallet State (for change detection) ───────────────────────────────────
    async def get_wallet_state(self, address: str, chain: str) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM wallet_state WHERE address=? AND chain=?",
                (address.lower(), chain)
            ) as c:
                r = await c.fetchone()
                return dict(r) if r else {"last_tx": "", "last_balance": 0}

    async def update_wallet_state(self, address: str, chain: str,
                                   last_tx: str, last_balance: float):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO wallet_state(address,chain,last_tx,last_balance,updated_at)
                   VALUES(?,?,?,?,datetime('now'))
                   ON CONFLICT(address,chain) DO UPDATE SET
                   last_tx=excluded.last_tx,
                   last_balance=excluded.last_balance,
                   updated_at=excluded.updated_at""",
                (address.lower(), chain, last_tx, last_balance)
            )
            await db.commit()

    # ── Alerts Log ─────────────────────────────────────────────────────────────
    async def alert_exists(self, alert_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT 1 FROM alerts_log WHERE alert_id=?", (alert_id,)
            ) as c:
                return (await c.fetchone()) is not None

    async def log_alert(self, alert_id: str, user_id: int, address: str,
                         chain: str, alert_type: str, amount_usd: float,
                         tx_hash: str, message: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO alerts_log
                   (alert_id,user_id,address,chain,alert_type,amount_usd,tx_hash,message)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (alert_id, user_id, address, chain, alert_type, amount_usd, tx_hash, message)
            )
            await db.commit()

    async def get_recent_alerts(self, user_id: int = None, limit: int = 10) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if user_id:
                async with db.execute(
                    "SELECT * FROM alerts_log WHERE user_id=? ORDER BY fired_at DESC LIMIT ?",
                    (user_id, limit)
                ) as c:
                    return [dict(r) for r in await c.fetchall()]
            async with db.execute(
                "SELECT * FROM alerts_log ORDER BY fired_at DESC LIMIT ?", (limit,)
            ) as c:
                return [dict(r) for r in await c.fetchall()]

    # ── Stats ──────────────────────────────────────────────────────────────────
    async def get_stats(self) -> dict:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c:
                users = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM watched_wallets WHERE user_id IS NOT NULL") as c:
                wallets = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM alerts_log") as c:
                alerts = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM whale_wallets") as c:
                whales = (await c.fetchone())[0]
        return {"users": users, "wallets": wallets, "alerts": alerts, "whales": whales}
