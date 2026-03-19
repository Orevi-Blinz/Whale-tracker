"""
Watcher Scheduler — scans all watched wallets and whale list every N seconds.

For each wallet it checks:
  1. New transactions → large transfer alert
  2. Token transfers  → new buy alert (smart money / known wallet buying)
  3. Balance change   → significant balance change alert
  4. Whale list       → global alerts broadcast to ALL users
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.error import TelegramError

from config import Config
from database import Database
from chain_clients import ChainClients

logger = logging.getLogger(__name__)


class WatcherScheduler:
    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self.clients = ChainClients()
        self._task: asyncio.Task = None
        self._whale_task: asyncio.Task = None

    async def start(self):
        self._task = asyncio.create_task(self._user_wallet_loop())
        self._whale_task = asyncio.create_task(self._whale_wallet_loop())
        logger.info("Watcher scheduler started.")

    async def stop(self):
        for t in [self._task, self._whale_task]:
            if t:
                t.cancel()
                try: await t
                except asyncio.CancelledError: pass
        await self.clients.close()

    # ── User Wallet Loop ───────────────────────────────────────────────────────

    async def _user_wallet_loop(self):
        """Scan user-added wallets on configured interval."""
        while True:
            try:
                interval = int(await self.db.get_config("scan_interval_seconds") or 60)
                wallets = await self.db.get_all_watched_wallets()
                logger.info(f"Scanning {len(wallets)} user wallets...")

                for w in wallets:
                    await self._scan_wallet(
                        address=w["address"],
                        chain=w["chain"],
                        user_id=w["user_id"],
                        label=w["label"] or w["address"][:10] + "...",
                        threshold_usd=w["threshold_usd"],
                        track_transfers=bool(w["track_transfers"]),
                        track_buys=bool(w["track_buys"]),
                        track_balance=bool(w["track_balance"]),
                    )
                    await asyncio.sleep(0.5)  # gentle rate limiting

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"User wallet loop error: {e}", exc_info=True)

            await asyncio.sleep(interval)

    # ── Whale Wallet Loop ──────────────────────────────────────────────────────

    async def _whale_wallet_loop(self):
        """Scan global whale list and broadcast to ALL users."""
        while True:
            try:
                # Whale list scans every 2× the normal interval
                interval = int(await self.db.get_config("scan_interval_seconds") or 60)
                whales = await self.db.get_whale_wallets()
                logger.info(f"Scanning {len(whales)} whale wallets...")

                for w in whales:
                    await self._scan_whale(
                        address=w["address"],
                        chain=w["chain"],
                        label=w["label"],
                    )
                    await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Whale loop error: {e}", exc_info=True)

            await asyncio.sleep(interval * 2)

    # ── Core Scan Logic ────────────────────────────────────────────────────────

    async def _scan_wallet(self, address: str, chain: str, user_id: int,
                           label: str, threshold_usd: float,
                           track_transfers: bool, track_buys: bool, track_balance: bool):
        """Full scan for a single user-watched wallet."""
        try:
            state = await self.db.get_wallet_state(address, chain)
            last_tx = state["last_tx"]
            last_balance = state["last_balance"]

            # Use wallet-specific threshold or global default
            whale_threshold = threshold_usd or float(
                await self.db.get_config("whale_threshold_usd") or Config.DEFAULT_WHALE_THRESHOLD_USD
            )
            buy_threshold = float(
                await self.db.get_config("token_buy_usd") or Config.DEFAULT_TOKEN_BUY_USD
            )

            # ── 1. Large Transfer Detection ────────────────────────────────
            if track_transfers:
                txs = await self.clients.get_latest_transactions(address, chain)
                if txs:
                    newest_hash = self.clients.get_tx_hash(txs[0], chain)

                    if newest_hash and newest_hash != last_tx:
                        # New transaction(s) found — check value
                        for tx in txs:
                            tx_hash = self.clients.get_tx_hash(tx, chain)
                            if tx_hash == last_tx:
                                break  # Already seen everything from here

                            value_native = self.clients.get_tx_value_native(tx, chain, address)
                            value_usd = await self.clients.to_usd(value_native, chain)

                            if value_usd >= whale_threshold:
                                msg = self._format_transfer_alert(
                                    address, chain, label, tx_hash,
                                    value_native, value_usd
                                )
                                await self._fire_alert(
                                    alert_id=f"transfer_{tx_hash}",
                                    user_id=user_id,
                                    address=address, chain=chain,
                                    alert_type="transfer",
                                    amount_usd=value_usd,
                                    tx_hash=tx_hash,
                                    message=msg
                                )

                        # Update last seen tx
                        await self.db.update_wallet_state(address, chain, newest_hash, last_balance)

            # ── 2. Token Buy Detection ─────────────────────────────────────
            if track_buys and chain in ("ETH", "BSC", "TRX"):
                token_txs = await self.clients.get_token_transfers(address, chain)
                for ttx in token_txs:
                    tx_hash = self.clients.get_tx_hash(ttx, chain)
                    if tx_hash == last_tx:
                        break

                    # Check if this is an incoming transfer (buy)
                    to_addr = ttx.get("to", "").lower()
                    if to_addr != address.lower():
                        continue

                    token_name = ttx.get("tokenName", ttx.get("tokenSymbol", "Unknown Token"))
                    token_symbol = ttx.get("tokenSymbol", "?")
                    token_decimals = int(ttx.get("tokenDecimal", 18))

                    try:
                        raw_value = int(ttx.get("value", 0))
                        token_amount = raw_value / (10 ** token_decimals)
                    except Exception:
                        token_amount = 0

                    # We don't have a direct USD price for every token,
                    # so we flag based on the fact it's a large wallet buying
                    msg = self._format_buy_alert(
                        address, chain, label, tx_hash,
                        token_name, token_symbol, token_amount
                    )
                    await self._fire_alert(
                        alert_id=f"buy_{tx_hash}_{token_symbol}",
                        user_id=user_id,
                        address=address, chain=chain,
                        alert_type="buy",
                        amount_usd=0,
                        tx_hash=tx_hash,
                        message=msg
                    )

            # ── 3. Balance Change Detection ────────────────────────────────
            if track_balance:
                current_balance = await self.clients.get_balance(address, chain)
                current_usd = await self.clients.to_usd(current_balance, chain)

                if last_balance > 0:
                    change = current_balance - last_balance
                    change_usd = await self.clients.to_usd(abs(change), chain)
                    pct_change = abs(change / last_balance) * 100 if last_balance > 0 else 0

                    # Alert if change > $10k OR > 10% of balance
                    if change_usd >= 10000 or pct_change >= 10:
                        msg = self._format_balance_alert(
                            address, chain, label,
                            last_balance, current_balance, change,
                            change_usd, pct_change
                        )
                        alert_id = f"bal_{address}_{chain}_{int(current_balance*100)}"
                        await self._fire_alert(
                            alert_id=alert_id,
                            user_id=user_id,
                            address=address, chain=chain,
                            alert_type="balance",
                            amount_usd=change_usd,
                            tx_hash="",
                            message=msg
                        )

                # Update state with new balance
                state = await self.db.get_wallet_state(address, chain)
                await self.db.update_wallet_state(address, chain, state["last_tx"], current_balance)

        except Exception as e:
            logger.error(f"scan_wallet error {address} {chain}: {e}")

    async def _scan_whale(self, address: str, chain: str, label: str):
        """Scan a global whale wallet and broadcast large moves to all users."""
        try:
            state = await self.db.get_wallet_state(address, chain)
            last_tx = state["last_tx"]

            whale_threshold = float(
                await self.db.get_config("whale_threshold_usd") or Config.DEFAULT_WHALE_THRESHOLD_USD
            )

            txs = await self.clients.get_latest_transactions(address, chain)
            if not txs:
                return

            newest_hash = self.clients.get_tx_hash(txs[0], chain)
            if not newest_hash or newest_hash == last_tx:
                return

            for tx in txs:
                tx_hash = self.clients.get_tx_hash(tx, chain)
                if tx_hash == last_tx:
                    break

                value_native = self.clients.get_tx_value_native(tx, chain, address)
                value_usd = await self.clients.to_usd(value_native, chain)

                if value_usd >= whale_threshold:
                    msg = self._format_whale_alert(address, chain, label, tx_hash, value_native, value_usd)
                    alert_id = f"whale_{tx_hash}"

                    if not await self.db.alert_exists(alert_id):
                        # Broadcast to ALL users
                        users = await self.db.get_all_users()
                        for user in users:
                            await self._send_safe(user["user_id"], msg)
                        await self.db.log_alert(
                            alert_id, 0, address, chain, "whale", value_usd, tx_hash, msg
                        )
                        logger.info(f"Whale alert fired: {label} {chain} ${value_usd:,.0f}")

            await self.db.update_wallet_state(address, chain, newest_hash, state["last_balance"])

        except Exception as e:
            logger.error(f"scan_whale error {address} {chain}: {e}")

    # ── Alert Firing ───────────────────────────────────────────────────────────

    async def _fire_alert(self, alert_id: str, user_id: int, address: str,
                           chain: str, alert_type: str, amount_usd: float,
                           tx_hash: str, message: str):
        """Deduplicate and send alert to user."""
        if await self.db.alert_exists(alert_id):
            return
        await self.db.log_alert(alert_id, user_id, address, chain, alert_type, amount_usd, tx_hash, message)
        await self._send_safe(user_id, message)

    async def _send_safe(self, chat_id: int, text: str):
        try:
            await self.bot.send_message(chat_id=chat_id, text=text,
                                        parse_mode="HTML", disable_web_page_preview=True)
        except TelegramError as e:
            logger.warning(f"Send failed to {chat_id}: {e}")

    # ── Message Formatters ─────────────────────────────────────────────────────

    def _format_transfer_alert(self, address, chain, label, tx_hash,
                                value_native, value_usd) -> str:
        emoji = Config.CHAIN_EMOJIS.get(chain, "🔗")
        chain_name = Config.CHAIN_NAMES.get(chain, chain)
        explorer = self.clients.get_explorer_link(tx_hash, chain)
        addr_link = self.clients.get_address_link(address, chain)

        return (
            f"🐋 <b>WHALE TRANSFER DETECTED</b>\n"
            f"{'─'*28}\n"
            f"{emoji} <b>Chain:</b> {chain_name}\n"
            f"👛 <b>Wallet:</b> <a href='{addr_link}'>{label}</a>\n"
            f"💸 <b>Amount:</b> {value_native:,.4f} {chain}\n"
            f"💵 <b>USD Value:</b> <b>${value_usd:,.0f}</b>\n"
            f"{'─'*28}\n"
            f"🔗 <a href='{explorer}'>View Transaction</a>\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        )

    def _format_buy_alert(self, address, chain, label, tx_hash,
                           token_name, token_symbol, token_amount) -> str:
        emoji = Config.CHAIN_EMOJIS.get(chain, "🔗")
        chain_name = Config.CHAIN_NAMES.get(chain, chain)
        explorer = self.clients.get_explorer_link(tx_hash, chain)
        addr_link = self.clients.get_address_link(address, chain)

        return (
            f"🧠 <b>SMART MONEY BUY DETECTED</b>\n"
            f"{'─'*28}\n"
            f"{emoji} <b>Chain:</b> {chain_name}\n"
            f"👛 <b>Wallet:</b> <a href='{addr_link}'>{label}</a>\n"
            f"🪙 <b>Token:</b> {token_name} ({token_symbol})\n"
            f"📦 <b>Amount:</b> {token_amount:,.2f} {token_symbol}\n"
            f"{'─'*28}\n"
            f"🔗 <a href='{explorer}'>View Transaction</a>\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        )

    def _format_balance_alert(self, address, chain, label,
                               old_bal, new_bal, change,
                               change_usd, pct_change) -> str:
        emoji = Config.CHAIN_EMOJIS.get(chain, "🔗")
        chain_name = Config.CHAIN_NAMES.get(chain, chain)
        direction = "📈 INCREASED" if change > 0 else "📉 DECREASED"
        addr_link = self.clients.get_address_link(address, chain)

        return (
            f"💼 <b>BALANCE CHANGE ALERT</b>\n"
            f"{'─'*28}\n"
            f"{emoji} <b>Chain:</b> {chain_name}\n"
            f"👛 <b>Wallet:</b> <a href='{addr_link}'>{label}</a>\n"
            f"{direction}\n"
            f"📊 <b>Before:</b> {old_bal:,.4f} {chain}\n"
            f"📊 <b>After:</b>  {new_bal:,.4f} {chain}\n"
            f"🔄 <b>Change:</b> {abs(change):,.4f} {chain}\n"
            f"💵 <b>~USD:</b> ${change_usd:,.0f} ({pct_change:.1f}%)\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        )

    def _format_whale_alert(self, address, chain, label, tx_hash,
                             value_native, value_usd) -> str:
        emoji = Config.CHAIN_EMOJIS.get(chain, "🔗")
        chain_name = Config.CHAIN_NAMES.get(chain, chain)
        explorer = self.clients.get_explorer_link(tx_hash, chain)
        addr_link = self.clients.get_address_link(address, chain)

        return (
            f"🚨 <b>KNOWN WHALE ALERT</b> 🚨\n"
            f"{'─'*28}\n"
            f"{emoji} <b>Chain:</b> {chain_name}\n"
            f"🏷 <b>Whale:</b> <a href='{addr_link}'>{label}</a>\n"
            f"💸 <b>Amount:</b> {value_native:,.4f} {chain}\n"
            f"💵 <b>USD Value:</b> <b>${value_usd:,.0f}</b>\n"
            f"{'─'*28}\n"
            f"🔗 <a href='{explorer}'>View Transaction</a>\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n\n"
            f"📡 <i>Global whale broadcast — all users notified</i>"
        )
