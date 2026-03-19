"""
Chain Clients — fetches transactions and balances from free public APIs.

ETH/BSC  → Etherscan / BscScan (free, 5 req/sec)
SOL      → Solscan public API (free, no key needed)
BTC      → Blockchair (free tier, 30 req/min)
TRX      → Trongrid (free, no key needed)

USD pricing → CoinGecko free API
"""

import asyncio
import aiohttp
import logging
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

# CoinGecko coin IDs for price lookup
CHAIN_COIN_IDS = {
    "ETH": "ethereum",
    "BSC": "binancecoin",
    "SOL": "solana",
    "BTC": "bitcoin",
    "TRX": "tron",
}

# Native token decimals
CHAIN_DECIMALS = {
    "ETH": 18,
    "BSC": 18,
    "SOL": 9,
    "BTC": 8,
    "TRX": 6,
}


class ChainClients:
    """Unified interface for all 5 blockchain APIs."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._prices: dict = {}   # Cache: chain → USD price
        self._price_updated = 0

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Price Cache (refresh every 5 min) ─────────────────────────────────────
    async def get_usd_prices(self) -> dict:
        import time
        now = time.time()
        if now - self._price_updated < 300 and self._prices:
            return self._prices

        ids = ",".join(CHAIN_COIN_IDS.values())
        url = f"{Config.COINGECKO_BASE}/simple/price?ids={ids}&vs_currencies=usd"
        try:
            session = await self._session_get()
            async with session.get(url) as r:
                data = await r.json()
            self._prices = {
                chain: data.get(coin_id, {}).get("usd", 0)
                for chain, coin_id in CHAIN_COIN_IDS.items()
            }
            self._price_updated = now
            logger.info(f"Prices updated: {self._prices}")
        except Exception as e:
            logger.warning(f"Price fetch failed: {e}")
        return self._prices

    async def to_usd(self, amount_native: float, chain: str) -> float:
        prices = await self.get_usd_prices()
        return amount_native * prices.get(chain, 0)

    # ── Ethereum ──────────────────────────────────────────────────────────────

    async def get_eth_transactions(self, address: str) -> list[dict]:
        """Get latest 10 normal ETH transactions."""
        params = {
            "module": "account", "action": "txlist",
            "address": address, "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": 10, "sort": "desc",
            "apikey": Config.ETHERSCAN_API_KEY or "YourApiKeyToken"
        }
        try:
            session = await self._session_get()
            async with session.get(Config.ETHERSCAN_BASE, params=params) as r:
                data = await r.json()
            if data.get("status") == "1":
                return data.get("result", [])
        except Exception as e:
            logger.error(f"ETH tx fetch error {address}: {e}")
        return []

    async def get_eth_token_transfers(self, address: str) -> list[dict]:
        """Get latest ERC-20 token transfers (for smart money buys)."""
        params = {
            "module": "account", "action": "tokentx",
            "address": address, "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": 10, "sort": "desc",
            "apikey": Config.ETHERSCAN_API_KEY or "YourApiKeyToken"
        }
        try:
            session = await self._session_get()
            async with session.get(Config.ETHERSCAN_BASE, params=params) as r:
                data = await r.json()
            if data.get("status") == "1":
                return data.get("result", [])
        except Exception as e:
            logger.error(f"ETH token tx error {address}: {e}")
        return []

    async def get_eth_balance(self, address: str) -> float:
        """Returns ETH balance as float."""
        params = {
            "module": "account", "action": "balance",
            "address": address, "tag": "latest",
            "apikey": Config.ETHERSCAN_API_KEY or "YourApiKeyToken"
        }
        try:
            session = await self._session_get()
            async with session.get(Config.ETHERSCAN_BASE, params=params) as r:
                data = await r.json()
            if data.get("status") == "1":
                return int(data["result"]) / 10**18
        except Exception as e:
            logger.error(f"ETH balance error {address}: {e}")
        return 0.0

    # ── BSC (same API structure as Etherscan) ─────────────────────────────────

    async def get_bsc_transactions(self, address: str) -> list[dict]:
        params = {
            "module": "account", "action": "txlist",
            "address": address, "page": 1, "offset": 10, "sort": "desc",
            "apikey": Config.BSCSCAN_API_KEY or "YourApiKeyToken"
        }
        try:
            session = await self._session_get()
            async with session.get(Config.BSCSCAN_BASE, params=params) as r:
                data = await r.json()
            if data.get("status") == "1":
                return data.get("result", [])
        except Exception as e:
            logger.error(f"BSC tx error {address}: {e}")
        return []

    async def get_bsc_token_transfers(self, address: str) -> list[dict]:
        params = {
            "module": "account", "action": "tokentx",
            "address": address, "page": 1, "offset": 10, "sort": "desc",
            "apikey": Config.BSCSCAN_API_KEY or "YourApiKeyToken"
        }
        try:
            session = await self._session_get()
            async with session.get(Config.BSCSCAN_BASE, params=params) as r:
                data = await r.json()
            if data.get("status") == "1":
                return data.get("result", [])
        except Exception as e:
            logger.error(f"BSC token tx error {address}: {e}")
        return []

    async def get_bsc_balance(self, address: str) -> float:
        params = {
            "module": "account", "action": "balance",
            "address": address, "tag": "latest",
            "apikey": Config.BSCSCAN_API_KEY or "YourApiKeyToken"
        }
        try:
            session = await self._session_get()
            async with session.get(Config.BSCSCAN_BASE, params=params) as r:
                data = await r.json()
            if data.get("status") == "1":
                return int(data["result"]) / 10**18
        except Exception as e:
            logger.error(f"BSC balance error {address}: {e}")
        return 0.0

    # ── Solana ────────────────────────────────────────────────────────────────

    async def get_sol_transactions(self, address: str) -> list[dict]:
        """Solscan public API — no key needed."""
        url = f"{Config.SOLSCAN_BASE}/account/transactions"
        params = {"account": address, "limit": 10}
        headers = {"accept": "application/json"}
        if Config.SOLSCAN_API_KEY:
            headers["token"] = Config.SOLSCAN_API_KEY
        try:
            session = await self._session_get()
            async with session.get(url, params=params, headers=headers) as r:
                data = await r.json()
            return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.error(f"SOL tx error {address}: {e}")
        return []

    async def get_sol_balance(self, address: str) -> float:
        url = f"{Config.SOLSCAN_BASE}/account"
        params = {"address": address}
        try:
            session = await self._session_get()
            async with session.get(url, params=params) as r:
                data = await r.json()
            lamports = data.get("lamports", 0)
            return lamports / 10**9
        except Exception as e:
            logger.error(f"SOL balance error {address}: {e}")
        return 0.0

    # ── Bitcoin ───────────────────────────────────────────────────────────────

    async def get_btc_transactions(self, address: str) -> list[dict]:
        """Blockchair free API."""
        url = f"{Config.BLOCKCHAIR_BASE}/bitcoin/dashboards/address/{address}"
        params = {"limit": 10}
        if Config.BLOCKCHAIR_API_KEY:
            params["key"] = Config.BLOCKCHAIR_API_KEY
        try:
            session = await self._session_get()
            async with session.get(url, params=params) as r:
                data = await r.json()
            addr_data = data.get("data", {}).get(address, {})
            txs = addr_data.get("transactions", [])
            # Return as list of dicts with hash
            return [{"hash": tx, "chain": "BTC"} for tx in txs[:10]]
        except Exception as e:
            logger.error(f"BTC tx error {address}: {e}")
        return []

    async def get_btc_balance(self, address: str) -> float:
        url = f"{Config.BLOCKCHAIR_BASE}/bitcoin/dashboards/address/{address}"
        try:
            session = await self._session_get()
            async with session.get(url) as r:
                data = await r.json()
            addr_data = data.get("data", {}).get(address, {}).get("address", {})
            satoshis = addr_data.get("balance", 0)
            return satoshis / 10**8
        except Exception as e:
            logger.error(f"BTC balance error {address}: {e}")
        return 0.0

    async def get_btc_tx_detail(self, tx_hash: str) -> Optional[dict]:
        """Get BTC transaction value in BTC."""
        url = f"{Config.BLOCKCHAIR_BASE}/bitcoin/dashboards/transaction/{tx_hash}"
        try:
            session = await self._session_get()
            async with session.get(url) as r:
                data = await r.json()
            tx_data = data.get("data", {}).get(tx_hash, {}).get("transaction", {})
            return tx_data
        except Exception as e:
            logger.error(f"BTC tx detail error {tx_hash}: {e}")
        return None

    # ── Tron (TRX) ────────────────────────────────────────────────────────────

    async def get_trx_transactions(self, address: str) -> list[dict]:
        """Trongrid — free, no key needed."""
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
        params = {"limit": 10, "order_by": "block_timestamp,desc"}
        try:
            session = await self._session_get()
            async with session.get(url, params=params) as r:
                data = await r.json()
            return data.get("data", [])
        except Exception as e:
            logger.error(f"TRX tx error {address}: {e}")
        return []

    async def get_trx_balance(self, address: str) -> float:
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        try:
            session = await self._session_get()
            async with session.get(url) as r:
                data = await r.json()
            account_data = data.get("data", [{}])
            if account_data:
                sun = account_data[0].get("balance", 0)
                return sun / 10**6  # SUN to TRX
        except Exception as e:
            logger.error(f"TRX balance error {address}: {e}")
        return 0.0

    async def get_trx_token_transfers(self, address: str) -> list[dict]:
        """TRC-20 token transfers."""
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
        params = {"limit": 10, "order_by": "block_timestamp,desc"}
        try:
            session = await self._session_get()
            async with session.get(url, params=params) as r:
                data = await r.json()
            return data.get("data", [])
        except Exception as e:
            logger.error(f"TRX token error {address}: {e}")
        return []

    # ── Unified fetchers ──────────────────────────────────────────────────────

    async def get_balance(self, address: str, chain: str) -> float:
        """Return native token balance for any chain."""
        if chain == "ETH": return await self.get_eth_balance(address)
        if chain == "BSC": return await self.get_bsc_balance(address)
        if chain == "SOL": return await self.get_sol_balance(address)
        if chain == "BTC": return await self.get_btc_balance(address)
        if chain == "TRX": return await self.get_trx_balance(address)
        return 0.0

    async def get_latest_transactions(self, address: str, chain: str) -> list[dict]:
        """Return raw transaction list for any chain."""
        if chain == "ETH": return await self.get_eth_transactions(address)
        if chain == "BSC": return await self.get_bsc_transactions(address)
        if chain == "SOL": return await self.get_sol_transactions(address)
        if chain == "BTC": return await self.get_btc_transactions(address)
        if chain == "TRX": return await self.get_trx_transactions(address)
        return []

    async def get_token_transfers(self, address: str, chain: str) -> list[dict]:
        """Return token transfers (ERC20/BEP20/TRC20) for buy detection."""
        if chain == "ETH": return await self.get_eth_token_transfers(address)
        if chain == "BSC": return await self.get_bsc_token_transfers(address)
        if chain == "TRX": return await self.get_trx_token_transfers(address)
        return []  # BTC/SOL handled differently

    def get_tx_hash(self, tx: dict, chain: str) -> str:
        """Extract tx hash from a raw transaction dict."""
        if chain in ("ETH", "BSC"):
            return tx.get("hash", "")
        if chain == "SOL":
            return tx.get("txHash", tx.get("signature", ""))
        if chain == "BTC":
            return tx.get("hash", "")
        if chain == "TRX":
            return tx.get("txID", "")
        return ""

    def get_tx_value_native(self, tx: dict, chain: str, address: str) -> float:
        """Extract native token transfer value from raw tx."""
        try:
            if chain in ("ETH", "BSC"):
                wei = int(tx.get("value", 0))
                return wei / 10**18

            if chain == "TRX":
                raw = tx.get("raw_data", {}).get("contract", [{}])[0]
                val = raw.get("parameter", {}).get("value", {})
                amount = val.get("amount", 0)
                return amount / 10**6

            if chain == "SOL":
                # Solscan returns lamport changes
                changes = tx.get("lamport", 0)
                return abs(changes) / 10**9

        except Exception:
            pass
        return 0.0

    def get_explorer_link(self, tx_hash: str, chain: str) -> str:
        """Return block explorer URL for a transaction."""
        if chain == "ETH":
            return f"https://etherscan.io/tx/{tx_hash}"
        if chain == "BSC":
            return f"https://bscscan.com/tx/{tx_hash}"
        if chain == "SOL":
            return f"https://solscan.io/tx/{tx_hash}"
        if chain == "BTC":
            return f"https://blockchair.com/bitcoin/transaction/{tx_hash}"
        if chain == "TRX":
            return f"https://tronscan.org/#/transaction/{tx_hash}"
        return ""

    def get_address_link(self, address: str, chain: str) -> str:
        """Return block explorer URL for an address."""
        if chain == "ETH":
            return f"https://etherscan.io/address/{address}"
        if chain == "BSC":
            return f"https://bscscan.com/address/{address}"
        if chain == "SOL":
            return f"https://solscan.io/account/{address}"
        if chain == "BTC":
            return f"https://blockchair.com/bitcoin/address/{address}"
        if chain == "TRX":
            return f"https://tronscan.org/#/address/{address}"
        return ""
