import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock

import main


async def test_sigterm_closes_bot_gracefully():
    """Панель хостинга шлёт SIGTERM при остановке — бот должен закрыть соединение, а не умереть."""
    bot = MagicMock()
    closed = asyncio.Event()
    bot.close = AsyncMock(side_effect=lambda: closed.set())

    main._install_stop_signals(bot)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(closed.wait(), timeout=2)
    finally:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)

    bot.close.assert_awaited_once()
