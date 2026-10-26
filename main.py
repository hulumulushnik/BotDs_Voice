"""Точка входа: сборка зависимостей и запуск бота."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import traceback

import discord
from discord.ext import commands

from cogs.triarch_voice import TriarchVoiceCog
from config import BotConfig, ConfigError, load_config
from core.audit_log import AuditLog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
)
logger = logging.getLogger("main")


class TriarchBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.members = True  # нужен для on_member_update (выдача роли «Заглушён»)

        super().__init__(
            # Текстовых команд нет: префикс-заглушка, чтобы не реагировать на «!».
            command_prefix=commands.when_mentioned,
            intents=intents,
            # Бот никогда не пингует @everyone/@here и роли — защита от злоупотреблений.
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.config = config
        self.audit = AuditLog(config.log_channel_name)

    async def setup_hook(self) -> None:
        await self.add_cog(TriarchVoiceCog(self, self.config, self.audit))
        logger.info("Модуль TriarchVoiceCog загружен.")

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:
        # on_error — метод клиента, а не событие: Cog.listener("on_error") никогда не вызывается.
        details = traceback.format_exc()
        logger.error("Необработанная ошибка в %s:\n%s", event_method, details)
        await self.audit.broadcast(
            self.guilds,
            title="Критическая ошибка",
            description=f"Непредвиденная ошибка в событии `{event_method}`",
            color=discord.Color.dark_red(),
            fields=[("Детали", f"```{details[-900:]}```")],
        )


def _install_stop_signals(bot: TriarchBot) -> None:
    """SIGTERM/SIGINT (кнопка Stop в панели хостинга, Ctrl+C) → корректное закрытие соединения с Discord."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_shutdown(bot, s)))
        except (NotImplementedError, RuntimeError):
            pass  # Windows: остаётся стандартная обработка Ctrl+C


async def _shutdown(bot: TriarchBot, sig: signal.Signals) -> None:
    logger.info("Получен сигнал %s, останавливаем бота...", sig.name)
    await bot.close()


async def run() -> None:
    config = load_config()
    bot = TriarchBot(config)
    _install_stop_signals(bot)
    async with bot:
        await bot.start(config.token)
    logger.info("Бот остановлен.")


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        logger.critical("Ошибка конфигурации: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")


if __name__ == "__main__":
    main()
