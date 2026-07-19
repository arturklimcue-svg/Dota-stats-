"""
main.py — точка входа. Создаёт бота, включает нужные intents,
грузит dota_stats_v3 и server_management, запускается по токену.

Токен бота НЕ хранится в коде — берётся из переменной окружения
DISCORD_BOT_TOKEN. Как её задать на вашем хостинге — см. README.md.
"""

import asyncio
import os

# МАРКЕР ДЕПЛОЯ — если этой строки нет в логе при старте, значит хостинг
# запускает СТАРУЮ версию файла, и дело не в самом коде, а в git/деплое.
# После проверки можно убрать, но пока не мешает.
print(">>> BUILD MARKER: dota-bot v4-fix-2026-07-20 <<<")

import discord
from discord.ext import commands

# ВАЖНО: .env должен подгружаться ДО импорта/загрузки dota_stats_v3 —
# там ключи (GROQ_API_KEY и т.д.) читаются в константы на уровне модуля,
# то есть один раз при импорте. Если load_dotenv() вызвать позже или не
# вызвать вообще, os.environ.get(...) в dota_stats_v3.py увидит пустую
# строку, даже если сам .env-файл на месте и заполнен корректно.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[WARN] python-dotenv не установлен — .env не подхватится, "
          "переменные окружения должны быть заданы на уровне хостинга/ОС "
          "(pip install python-dotenv, если нужен именно файл .env)")

intents = discord.Intents.default()
intents.members = True          # нужно для on_member_join (выдача роли Unverified)
intents.message_content = True  # нужно, чтобы работали префиксные команды (!dota_setup и т.п.)

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user} (id: {bot.user.id})")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Без этого обработчика discord.py по умолчанию печатает ошибку только
    в консоль сервера, а в самом Discord ничего не происходит — команда
    выглядит как "не работает", хотя на деле просто тихо падает."""
    if isinstance(error, commands.CommandNotFound):
        return  # не спамим на опечатки в командах
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f"⛔ Не хватает прав для этой команды: {', '.join(error.missing_permissions)}")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Не хватает аргумента: `{error.param.name}`. "
                        f"Проверьте синтаксис команды.")
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send("⛔ Эта команда вам недоступна (не прошла проверка прав).")
        return
    # всё остальное — реальная ошибка в коде, печатаем и в канал, и в консоль
    print(f"[COMMAND ERROR] {ctx.command}: {error!r}")
    await ctx.send(f"❌ Ошибка при выполнении команды: `{error}`")


async def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Не найдена переменная окружения DISCORD_BOT_TOKEN. "
            "Задайте её на хостинге (см. README.md)."
        )
    async with bot:
        await bot.load_extension("dota_stats_v3")
        await bot.load_extension("server_management")
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
