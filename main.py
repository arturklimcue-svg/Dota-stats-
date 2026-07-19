"""
main.py — точка входа. Создаёт бота, включает нужные intents,
грузит dota_stats_v3 и server_management, запускается по токену.

Токен бота НЕ хранится в коде — берётся из переменной окружения
DISCORD_BOT_TOKEN. Как её задать на вашем хостинге — см. README.md.
"""

import asyncio
import os

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True          # нужно для on_member_join (выдача роли Unverified)
intents.message_content = True  # нужно, чтобы работали префиксные команды (!dota_setup и т.п.)

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user} (id: {bot.user.id})")


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
