"""
Configuration — all settings loaded from .env
"""
import os
from dotenv import load_dotenv
load_dotenv()


class Config:
    # ── Telegram ───────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
    ADMIN_IDS: list[int] = [
        int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
    ]

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "whale_bot.db")

    # ── Free API Keys (all have generous free tiers) ──────────────────────────
    # etherscan.io → free, 5 req/sec, 100k/day
    ETHERSCAN_API_KEY: str    = os.getenv("ETHERSCAN_API_KEY", "")
    # bscscan.com → free, same limits as etherscan
    BSCSCAN_API_KEY: str      = os.getenv("BSCSCAN_API_KEY", "")
    # solscan.io → free, no key needed for basic endpoints
    SOLSCAN_API_KEY: str      = os.getenv("SOLSCAN_API_KEY", "")
    # blockchair.com → free tier, 30 req/min (covers BTC + TRX)
    BLOCKCHAIR_API_KEY: str   = os.getenv("BLOCKCHAIR_API_KEY", "")

    # ── Alert Thresholds (defaults, users can override per-wallet) ─────────────
    # Minimum USD value to trigger a large transfer alert
    DEFAULT_WHALE_THRESHOLD_USD: float = float(os.getenv("WHALE_THRESHOLD_USD", "100000"))

    # Min USD value for token buy alerts
    DEFAULT_TOKEN_BUY_USD: float = float(os.getenv("TOKEN_BUY_USD", "50000"))

    # ── Scan Interval ─────────────────────────────────────────────────────────
    SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

    # ── Max wallets per free user ──────────────────────────────────────────────
    MAX_WALLETS_FREE: int = int(os.getenv("MAX_WALLETS_FREE", "3"))
    MAX_WALLETS_VIP: int  = int(os.getenv("MAX_WALLETS_VIP", "20"))

    # ── Known Smart Money / Whale wallets (pre-seeded) ────────────────────────
    # These are publicly known on-chain addresses (fund wallets, early whales)
    KNOWN_WHALE_WALLETS: list[dict] = [
        # Ethereum
        {"address": "0x28C6c06298d514Db089934071355E5743bf21d60", "chain": "ETH", "label": "Binance Hot Wallet"},
        {"address": "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549", "chain": "ETH", "label": "Binance Cold Wallet"},
        {"address": "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8", "chain": "ETH", "label": "Binance 7"},
        {"address": "0xF977814e90dA44bFA03b6295A0616a897441aceC", "chain": "ETH", "label": "Binance 8"},
        {"address": "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0", "chain": "ETH", "label": "Kraken"},
        {"address": "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d", "chain": "ETH", "label": "Coinbase 2"},
        # BTC
        {"address": "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97", "chain": "BTC", "label": "Binance BTC Cold"},
        {"address": "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo", "chain": "BTC", "label": "Binance BTC Hot"},
        # BSC
        {"address": "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3", "chain": "BSC", "label": "Binance BSC Hot"},
        # Tron
        {"address": "TJDENsfBJs4RFETt1X1W8wMDc8M5XnJhCe", "chain": "TRX", "label": "Binance TRX Hot"},
        {"address": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8", "chain": "TRX", "label": "Tron Foundation"},
    ]

    # ── Chain API base URLs ────────────────────────────────────────────────────
    ETHERSCAN_BASE   = "https://api.etherscan.io/api"
    BSCSCAN_BASE     = "https://api.bscscan.com/api"
    SOLSCAN_BASE     = "https://public-api.solscan.io"
    BLOCKCHAIR_BASE  = "https://api.blockchair.com"
    COINGECKO_BASE   = "https://api.coingecko.com/api/v3"

    # Chain display names
    CHAIN_NAMES = {
        "ETH": "Ethereum",
        "BSC": "BNB Chain",
        "SOL": "Solana",
        "BTC": "Bitcoin",
        "TRX": "Tron",
    }

    CHAIN_EMOJIS = {
        "ETH": "🔷",
        "BSC": "🟡",
        "SOL": "🟣",
        "BTC": "🟠",
        "TRX": "🔴",
    }
