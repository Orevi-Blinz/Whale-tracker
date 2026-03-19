"""
Whale & Wallet Tracker Telegram Bot — Entry Point
Tracks ETH, BSC, SOL, BTC, TRX chains for whale moves,
smart money buys, balance changes, and large transfers.
"""

import asyncio
import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from config import Config
from database import Database
from handlers.user import UserHandlers
from handlers.admin import AdminHandlers
from scheduler import WatcherScheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("whale_bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting Whale & Wallet Tracker Bot...")

    db = Database(Config.DATABASE_PATH)
    await db.initialize()

    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    user = UserHandlers(db)
    admin = AdminHandlers(db)

    # ── User Commands ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    user.cmd_start))
    app.add_handler(CommandHandler("help",     user.cmd_help))
    app.add_handler(CommandHandler("watch",    user.cmd_watch))
    app.add_handler(CommandHandler("unwatch",  user.cmd_unwatch))
    app.add_handler(CommandHandler("mywallet", user.cmd_my_wallets))
    app.add_handler(CommandHandler("alerts",   user.cmd_recent_alerts))
    app.add_handler(CommandHandler("whales",   user.cmd_whale_list))
    app.add_handler(CommandHandler("threshold",user.cmd_set_threshold))

    # ── Admin Commands ─────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("admin",       admin.cmd_panel))
    app.add_handler(CommandHandler("addwhale",    admin.cmd_add_whale))
    app.add_handler(CommandHandler("removewhale", admin.cmd_remove_whale))
    app.add_handler(CommandHandler("broadcast",   admin.cmd_broadcast))
    app.add_handler(CommandHandler("stats",       admin.cmd_stats))
    app.add_handler(CommandHandler("setinterval", admin.cmd_set_interval))

    # ── Callbacks ──────────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(user.handle_callback,  pattern="^u_"))
    app.add_handler(CallbackQueryHandler(admin.handle_callback, pattern="^a_"))

    # ── Scheduler ──────────────────────────────────────────────────────────────
    scheduler = WatcherScheduler(app.bot, db)
    await scheduler.start()

    logger.info("Bot is live. Ctrl+C to stop.")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await scheduler.stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
