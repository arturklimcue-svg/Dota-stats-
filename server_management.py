"""
server_management.py — верификация по SteamID, ранговые роли и доступ
к каналам, join-to-create войсы, треды вместо спама, автоочистка чатов.

Зависит от уже готового dota_stats_v3.py (переиспользует OpenDota-клиент
и конвертацию SteamID, чтобы не дублировать код).

Установка:
  await bot.load_extension("dota_stats_v3")       # сначала
  await bot.load_extension("server_management")   # потом

После загрузки на сервере один раз выполните (нужны права администратора):
  !dota_server_setup

Это создаст:
  - роли: Unverified, Verified, Unranked, Herald..Immortal
  - канал #verification (виден всем, писать нельзя, только кнопка)
  - категории "📋 Начало" / "💬 Таверна" / "📊 Стратегия" / "🎮 Игровое"
    с тематическими каналами, видимые только верифицированным
  - ранговые категории "Herald–Crusader" / "Archon–Legend" / "Ancient–Immortal"
    с текстовым + голосовым каналом, видимые только по ранговой роли
  - категорию "🎙 Голосовые комнаты" с join-to-create, чатами и жалобами
  - закроет все остальные существующие каналы от @everyone

ВАЖНО: закрытие каналов от @everyone необратимо переписывает права —
запускайте команду на чистом/тестовом сервере или будьте готовы,
что существующие permission overwrites на каналах будут заменены.
"""

import asyncio
import random
from datetime import timedelta, time as dt_time
from pathlib import Path

import discord
from discord.ext import commands, tasks

from dota_stats_v3 import od, db, to_account_id, to_steam64, Storage, DB_PATH, PatchAnalyticsView

# ---------------- конфиг ----------------

UNVERIFIED_ROLE = "Unverified"
VERIFIED_ROLE = "Verified"
UNRANKED_ROLE = "Unranked"
VERIFICATION_CHANNEL = "🔐-ВЕРИФИКАЦИЯ"
MOD_LOG_CHANNEL = "🛠-mod-log"

RANK_TIER_NAMES = {
    1: "Herald", 2: "Guardian", 3: "Crusader", 4: "Archon",
    5: "Legend", 6: "Ancient", 7: "Divine", 8: "Immortal",
}
RANK_GROUPS = {
    "🐎 Herald – Crusader": ["Herald", "Guardian", "Crusader"],
    "🛡 Archon – Legend": ["Archon", "Legend"],
    "👑 Ancient – Immortal": ["Ancient", "Divine", "Immortal"],
}
RANK_RESYNC_INTERVAL_HOURS = 24

# ---- тематические категории и каналы ----

INFO_CATEGORY = "📋 Начало"
INFO_TEXT_CHANNELS = ["📜-правила", "📢-объявления"]  # + VERIFICATION_CHANNEL создаётся отдельно

SHOP_CHANNEL = "🛒-магазин"

COMMUNITY_CATEGORY = "💬 Таверна"
COMMUNITY_TEXT_CHANNELS = ["👋-приветствия", "💬-общий-чат", "🖼-скриншоты",
                           "🎬-клипы-и-фейлы", "😂-мемы", "🎉-ивенты", SHOP_CHANNEL]

STRATEGY_CATEGORY = "📊 Стратегия"
STRATEGY_TEXT_CHANNELS = ["🏆-лидерборд", "🟢-кто-в-игре", "🧠-советы-и-стратегии", PATCH_ANALYTICS_CHANNEL]

GAME_CATEGORY = "🎮 Игровое"
LFG_CHANNEL = "🔍-лфг"
GAME_TEXT_CHANNELS = [LFG_CHANNEL, "🛒-трейд-предметов", "🐲-бестиарий-героев"]

RANK_VOICE_NAMES = ["🔊 Radiant", "🔊 Dire"]  # по 2 голосовых в каждой ранговой категории

JOIN_TO_CREATE_CHANNEL = "➕ Создать войс"
JOIN_TO_CREATE_CATEGORY = "🎙 Голосовые комнаты"
JOIN_TO_CREATE_USER_LIMIT = 5

VOICE_ROOM_CREATE_CHANNEL = "🎮-создание-комнат"
VOICE_ROOM_CHAT_CHANNELS = ["💬-чат-1", "💬-чат-2", "💬-чат-3", "💬-чат-4", "💬-чат-5", "💬-чат-6"]
VOICE_REPORT_CHANNEL = "🚨-жалобы"

GAME_MODE_NAMES = {
    "ranked": "⚔️ Рейтинг",
    "turbo": "⚡ Турбо",
    "lp": "🤡 Лоу Приорити",
    "unranked": "🎮 Без ранга",
}

STAFF_CATEGORY = "🛠 Модерация"
STAFF_ROLE_NAME = "Moderator"

PARTY_THREAD_ARCHIVE_MINUTES = 60

AUTO_PURGE_CHANNELS = {"💬-общий-чат": 6, LFG_CHANNEL: 2}  # имя канала -> часов хранения истории
PURGE_INTERVAL_MINUTES = 30
LFG_SLOWMODE_SECONDS = 15

# ---- новые фишки ----

# 👋 публичное приветствие при успешной верификации
WELCOME_CHANNEL = "👋-приветствия"

# 📊 голосовой канал-счётчик участников (переименовывается сам, зайти нельзя)
STATS_CATEGORY = "📊 Статистика сервера"
MEMBER_COUNT_CHANNEL_PREFIX = "👥 Участников"
VERIFIED_COUNT_CHANNEL_PREFIX = "✅ Верифицировано"
STATS_UPDATE_INTERVAL_MINUTES = 15  # Discord ограничивает частые переименования каналов

# 🐲 "Герой дня" — ежедневный случайный пост в объявления
HERO_OF_DAY_CHANNEL = "📢-объявления"
HERO_OF_DAY_TIME_UTC = dt_time(hour=9, minute=0)  # раз в сутки в это время по UTC

# 🏆 лидерборд по винрейту среди привязанных участников
LEADERBOARD_CHANNEL = "🏆-лидерборд"
LEADERBOARD_MIN_GAMES = 20  # не показывать игроков с совсем маленькой выборкой

# 🔁 ежедневная проверка: у всех участников без привязанного SteamID
# должна стоять роль Unverified (ловит случаи, когда роль сняли руками,
# участник был на сервере до установки бота, или прошлая выдача роли
# не сработала из-за временного сбоя)
DAILY_VERIFICATION_SWEEP_TIME_UTC = dt_time(hour=4, minute=0)
SWEEP_MEMBER_DELAY_SECONDS = 0.05  # пауза между участниками, чтобы не словить rate limit Discord

# 🟢 отдельный канал под автообновляемую доску "кто сейчас играет"
# (в v4 команда !dota_status_board создаёт её в ЛЮБОМ канале вручную —
# здесь она подключается автоматически именно в этот канал при !dota_server_setup)
STATUS_BOARD_CHANNEL = "🟢-кто-в-игре"

# 🎲 кнопка "случайный герой" — на случай, если лень выбирать в Al Pick
HERO_ROLL_CHANNEL = "🐲-бестиарий-героев"

# 🔔 самоназначаемая роль уведомлений (турниры/ивенты сервера)
NOTIFY_ROLE_NAME = "🔔 Уведомления"

# 📈 еженедельный дайджест меты — топ-5 героев по пикрейту, по понедельникам
WEEKLY_META_TIME_UTC = dt_time(hour=10, minute=0)
WEEKLY_META_CHANNEL = "📢-объявления"

# 📊 аналитика патчей — ежедневный дайджест изменений меты
PATCH_ANALYTICS_CHANNEL = "📊-аналитика-патчей"
PATCH_ANALYTICS_TIME_UTC = dt_time(hour=11, minute=0)

# 🔇 каналы, где обычным участникам нельзя писать текст — только кнопки/модалки бота
READ_ONLY_CHANNELS = ["🧠-советы-и-стратегии", "🏆-лидерборд", STATUS_BOARD_CHANNEL, SHOP_CHANNEL, PATCH_ANALYTICS_CHANNEL]

# 🧹 автоочистка чатов внутри КАЖДОЙ ранговой категории (все они называются
# одинаково "⚔-чат", поэтому чистятся не по имени, а перебором категорий)
RANK_CHAT_PURGE_HOURS = 8

# 📝 темы (описания под названием) для визуального оформления каналов
CHANNEL_TOPICS = {
    "📜-правила": "Обязательно к прочтению перед общением на сервере",
    "📢-объявления": "Новости сервера, патчи, турниры — публикует администрация и бот",
    "👋-приветствия": "Тут бот здоровается с новыми верифицированными участниками",
    "💬-общий-чат": "Общение на любые темы, связанные с Dota 2",
    "🖼-скриншоты": "Делитесь забавными/эпичными моментами из игр",
    "🎬-клипы-и-фейлы": "Видео-клипы: как эпичные плеи, так и фейлы",
    "😂-мемы": "Dota-мемы и не только",
    "🎉-ивенты": "Анонсы и обсуждение внутренних мероприятий сервера",
    "🔍-лфг": "Ищете пати? Жмите кнопку ниже — бот создаст отдельный тред",
    "🟢-кто-в-игре": "Автообновляемая доска — кто из участников сейчас играет в Dota 2",
    "🏆-лидерборд": "Топ сервера по винрейту — только кнопка, без флуда",
    "🛒-трейд-предметов": "Обмен/продажа предметов Steam-инвентаря между участниками",
    "🧠-советы-и-стратегии": "Панель статистики и стратегий — только кнопки, без текста",
    "🐲-бестиарий-героев": "Обсуждение героев, их сильных и слабых сторон",
    "🛒-магазин": "Магазин shards: ежедневный бонус, товары и баланс",
    PATCH_ANALYTICS_CHANNEL: "Аналитика патчей: победители, проигравшие, мета — кнопки, без флуда",
}

# ---- закреплённые сообщения: канал -> (заголовок, текст) ----
PINNED_INFO = {
    "📜-правила": (
        "📜 Правила сервера",
        "1. Уважайте других игроков — без оскорблений и токсичности.\n"
        "2. Репорт в игре ≠ репорт здесь — жалобы на игроков внутри Dota 2 решайте через Valve.\n"
        "3. Флуд и реклама сторонних серверов — бан без предупреждения.\n"
        "4. Роль по рангу выдаётся автоматически и обновляется раз в сутки — не просите поднять вручную.\n"
        "5. Спорные ситуации — в #🛠-mod-log или лично модератору."
    ),
    "📢-объявления": (
        "📢 Объявления",
        "Здесь бот и модераторы публикуют важные новости: патчи, турниры сервера, изменения ролей."
    ),
    "🔍-лфг": (
        "🔍 Как искать пати",
        "Нажмите кнопку «🔍 Создать пати» ниже — бот создаст отдельный тред под ваш сбор "
        "группы вместо флуда сообщениями в общем канале. Тред сам архивируется через час "
        "без активности."
    ),
    "🧠-советы-и-стратегии": (
        "🧠 Советы и стратегии",
        "На панели статистики: «🔥 Топ мета героев» — топ-10 самых популярных героев с "
        "винрейтом, «🛒 Предметы под героя» — рекомендованный билд по фазам игры для "
        "выбранного героя, «📦 Топ предметы (тренд)» — что чаще всего берут в топовых сборках."
    ),
    "🛒-трейд-предметов": (
        "🛒 Трейд предметов",
        "Обмен/продажа скинов и предметов Steam-инвентаря между участниками сервера. "
        "Сделки — на ваш страх и риск, сервер не гарант."
    ),
    "🏆-лидерборд": (
        "🏆 Лидерборд сервера",
        "Нажмите кнопку «🏆 Показать лидерборд» ниже — топ-10 привязанных участников по "
        f"винрейту (нужно минимум {LEADERBOARD_MIN_GAMES} игр на аккаунте, чтобы попасть в список). "
        "Писать текст в этом канале нельзя — только кнопка."
    ),
}

# ---- кастомные стикеры ----
# Discord требует PNG/APNG 320x320px, до 500KB. Положите СВОИ файлы (собственные
# рисунки/фан-арт, на которые у вас есть права) в папку stickers/ рядом со скриптом.
# ВАЖНО: официальные изображения героев/предметов Dota 2 — собственность Valve,
# не заливайте их как стикеры сервера без разрешения правообладателя.
STICKERS_DIR = Path(__file__).parent / "stickers"

DEBUG_LOG = True


# ---------------- вспомогательные функции ----------------

def tier_to_role_name(rank_tier):
    """rank_tier из OpenDota — двузначное число (десятки = ранг, единицы = звезда)."""
    if not rank_tier:
        return None
    major = rank_tier // 10
    return RANK_TIER_NAMES.get(major)


async def get_or_create_role(guild: discord.Guild, name: str, **kwargs) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        role = await guild.create_role(name=name, reason="Dota server setup", **kwargs)
    return role


async def post_pinned_info(channel: discord.TextChannel, title: str, description: str,
                            view: discord.ui.View = None):
    """Шлёт и закрепляет embed-справку в канале (опционально — с кнопками),
    если бот уже не отправлял туда именно ЭТО сообщение (проверяем по
    автору + заголовку embed, а не просто "есть ли в канале хоть один пин" —
    иначе любой посторонний закреп в канале ложно блокирует создание кнопки
    в этом канале).

    Если находим своё старое сообщение с тем же заголовком, но БЕЗ кнопок
    (например, раньше отправили без view, а теперь view появился в коде) —
    пересоздаём его, а не молча пропускаем навсегда."""
    try:
        pins = await channel.pins()
    except discord.Forbidden:
        return
    me = channel.guild.me
    existing = next(
        (p for p in pins if p.author.id == me.id and p.embeds and p.embeds[0].title == title),
        None,
    )
    if existing:
        needs_view = view is not None
        has_components = bool(existing.components)
        if not needs_view or has_components:
            return  # уже есть актуальная версия — ничего не делаем
        try:
            await existing.unpin()
        except discord.Forbidden:
            pass
        try:
            await existing.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

    embed = discord.Embed(title=title, description=description, color=0x8B4513)
    msg = await channel.send(embed=embed, view=view) if view else await channel.send(embed=embed)
    try:
        await msg.pin()
    except discord.Forbidden:
        pass


async def upload_custom_stickers(guild: discord.Guild):
    """Заливает .png файлы из STICKERS_DIR как стикеры сервера, если их там ещё нет.
    Требует, чтобы файлы уже лежали на диске (см. предупреждение у STICKERS_DIR)."""
    if not STICKERS_DIR.exists():
        return
    try:
        existing = {s.name for s in await guild.fetch_stickers()}
    except discord.HTTPException:
        return
    for file in STICKERS_DIR.glob("*.png"):
        name = file.stem
        if name in existing:
            continue
        try:
            await guild.create_sticker(
                name=name, description=f"Dota sticker: {name}",
                emoji="🎮", file=discord.File(file), reason="Dota server setup")
        except discord.HTTPException as e:
            if DEBUG_LOG:
                print(f"[STICKERS] не удалось загрузить {name}: {e}")


async def assign_rank_role(member: discord.Member, account_id: int) -> str:
    """Снимает старую ранговую роль и выдаёт актуальную по данным OpenDota.
    Возвращает имя выданной роли (для логов/сообщений)."""
    profile = await od.get(f"/players/{account_id}")
    rank_tier = (profile or {}).get("rank_tier")

    guild = member.guild
    rank_role_names = set(RANK_TIER_NAMES.values()) | {UNRANKED_ROLE}
    to_remove = [r for r in member.roles if r.name in rank_role_names]
    if to_remove:
        await member.remove_roles(*to_remove, reason="Обновление ранга Dota")

    role_name = tier_to_role_name(rank_tier) or UNRANKED_ROLE
    role = await get_or_create_role(guild, role_name)
    await member.add_roles(role, reason="Синхронизация ранга Dota")
    return role_name


# ---------------- верификация: modal + persistent view ----------------

class VerifyModal(discord.ui.Modal, title="Верификация — привяжите SteamID"):
    steam_id = discord.ui.TextInput(
        label="SteamID (64-бит или account_id)",
        placeholder="напр. 76561198012345678", max_length=20)

    def __init__(self, db: Storage):
        super().__init__()
        self.db = db

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raw = str(self.steam_id.value)
            account_id = to_account_id(raw)
            steam_id64 = to_steam64(raw)
        except ValueError:
            await interaction.response.send_message("Это не похоже на SteamID.", ephemeral=True)
            return

        profile = await od.get(f"/players/{account_id}")
        if not profile or not profile.get("profile"):
            await interaction.response.send_message(
                "Не нашёл такой SteamID в OpenDota. Профиль должен быть публичным.",
                ephemeral=True)
            return

        self.db.register(interaction.user.id, account_id, steam_id64)

        # Дублируем привязку в приватный канал-бэкап (см. dota_stats_v3.py) —
        # без этого вызова SteamID сохранялся только в SQLite и пропадал
        # при редеплое без персистентного диска. Сбой бэкапа не должен
        # мешать самой верификации/роли — поэтому исключение не поднимаем.
        dota_cog = interaction.client.get_cog("DotaStats")
        if dota_cog:
            try:
                await dota_cog.backup_player_to_channel(interaction.user.id, account_id, steam_id64)
            except Exception as e:
                print(f"[BACKUP] не удалось записать привязку {interaction.user.id} в канал-бэкап: {e!r}")

        member = interaction.user
        guild = interaction.guild
        unverified = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE)
        verified = await get_or_create_role(guild, VERIFIED_ROLE)

        try:
            if unverified and unverified in member.roles:
                await member.remove_roles(unverified, reason="Верификация пройдена")
            await member.add_roles(verified, reason="Верификация пройдена")
            rank_role_name = await assign_rank_role(member, account_id)
        except discord.Forbidden:
            # Самая частая причина: роль бота стоит НИЖЕ ролей Verified/ранговых
            # в иерархии Server Settings -> Roles, либо у бота нет права Manage Roles.
            await interaction.response.send_message(
                "SteamID сохранил, но не смог выдать роль — у бота не хватает прав "
                "(Manage Roles) или его роль стоит ниже роли Verified/ранговых в "
                "иерархии сервера. Попросите администратора поднять роль бота выше "
                "в Server Settings -> Roles и нажмите кнопку ещё раз.",
                ephemeral=True)
            log_ch = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL)
            if log_ch:
                await log_ch.send(
                    f"⚠️ Не удалось выдать роль {member.mention} — недостаточно прав "
                    f"у бота (проверьте позицию роли бота в иерархии).")
            return

        persona = profile["profile"].get("personaname", "игрок")
        note = ""
        if rank_role_name == UNRANKED_ROLE:
            note = ("\n\n(Ранг не определился — либо матчи скрыты в настройках приватности "
                    "Steam/Dota, либо статистика ещё не синхронизировалась. Роль обновится "
                    "автоматически при следующей синхронизации.)")
        await interaction.response.send_message(
            f"Готово! Привязал вас к **{persona}**, выдал роль **{rank_role_name}**. "
            f"Добро пожаловать на сервер.{note}",
            ephemeral=True)

        log_ch = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL)
        if log_ch:
            await log_ch.send(
                f"✅ {member.mention} верифицирован как **{persona}** (ранг: {rank_role_name})")

        # публичное приветствие для остальных участников сервера
        welcome_ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL)
        if welcome_ch:
            welcome_embed = discord.Embed(
                description=f"🎉 {member.mention} присоединился к серверу как **{rank_role_name}**! "
                            f"Добро пожаловать в бой.",
                color=0x8B4513)
            if profile["profile"].get("avatarfull"):
                welcome_embed.set_thumbnail(url=profile["profile"]["avatarfull"])
            await welcome_ch.send(embed=welcome_embed)


class VerificationView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Привязать SteamID и получить доступ", emoji="🔗",
                        style=discord.ButtonStyle.success, custom_id="verify:start")
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal(self.db))


# ---------------- ЛФГ: кнопка + модалка вместо команды с текстом ----------------

class PartyModal(discord.ui.Modal, title="Создать пати"):
    description = discord.ui.TextInput(
        label="Что ищете?", style=discord.TextStyle.paragraph,
        placeholder="напр. Нужен саппорт, ранкед, от 3к MMR", max_length=200, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        lfg_channel = discord.utils.get(interaction.guild.text_channels, name=LFG_CHANNEL)
        if not lfg_channel:
            await interaction.response.send_message(
                f"Канал #{LFG_CHANNEL} не найден. Обратитесь к администратору.", ephemeral=True)
            return
        text = str(self.description.value) or "Ищу пати"
        anchor = await lfg_channel.send(f"🎮 {interaction.user.mention}: {text}")
        thread = await anchor.create_thread(
            name=f"Пати — {interaction.user.display_name}",
            auto_archive_duration=PARTY_THREAD_ARCHIVE_MINUTES)
        await thread.send(
            f"Тред создан для {interaction.user.mention}. "
            f"Автоархивация через {PARTY_THREAD_ARCHIVE_MINUTES} мин без сообщений.")
        await interaction.response.send_message(
            f"Готово! Тред создан: {thread.mention}", ephemeral=True)


class LFGPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать пати", emoji="🔍",
                        style=discord.ButtonStyle.primary, custom_id="lfg:create_party")
    async def create_party_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PartyModal())


# ---------------- Лидерборд: кнопка вместо команды с текстом ----------------

class LeaderboardPanelView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Показать лидерборд", emoji="🏆",
                        style=discord.ButtonStyle.primary, custom_id="leaderboard:show")
    async def show_leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed = await build_leaderboard_embed(self.db, interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def build_leaderboard_embed(db: Storage, guild: discord.Guild) -> discord.Embed:
    """Общая логика подсчёта лидерборда — переиспользуется и кнопкой, и командой."""
    players = db.all_players()
    rows = []
    for discord_id, account_id, steam_id64 in players:
        member = guild.get_member(discord_id)
        if not member:
            continue
        wl = await od.get(f"/players/{account_id}/wl")
        if not wl:
            continue
        wins, loses = wl.get("win", 0), wl.get("lose", 0)
        total = wins + loses
        if total < LEADERBOARD_MIN_GAMES:
            continue
        wr = wins / total * 100
        rows.append((member.display_name, wr, total))
        await asyncio.sleep(0.3)  # не долбить OpenDota подряд

    if not rows:
        return discord.Embed(
            title="🏆 Лидерборд сервера по винрейту",
            description=f"Пока недостаточно данных (нужно от {LEADERBOARD_MIN_GAMES} игр на аккаунте).",
            color=0x8B4513)

    rows.sort(key=lambda x: x[1], reverse=True)
    lines = [f"**{i+1}.** {name} — {wr:.1f}% ({games} игр)"
             for i, (name, wr, games) in enumerate(rows[:10])]
    embed = discord.Embed(
        title="🏆 Лидерборд сервера по винрейту",
        description="\n".join(lines),
        color=0x8B4513)
    embed.set_footer(text=f"Учтены игроки с {LEADERBOARD_MIN_GAMES}+ матчами на аккаунте")
    return embed


# ---------------- 🎲 случайный герой (для ленивых в All Pick) ----------------

class HeroRollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Случайный герой", emoji="🎲",
                        style=discord.ButtonStyle.success, custom_id="hero:roll")
    async def roll_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await od.ensure_heroes()
        if not od.heroes_cache:
            await interaction.followup.send("Не смог получить список героев, попробуйте позже.",
                                             ephemeral=True)
            return
        hero_id = random.choice(list(od.heroes_cache.keys()))
        hero_name = od.heroes_cache[hero_id]
        embed = discord.Embed(
            title="🎲 Ваш герой на эту игру",
            description=f"# {hero_name}",
            color=0x8B4513)
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- 🔔 самоназначаемая роль уведомлений ----------------

class NotifyRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Включить/выключить уведомления", emoji="🔔",
                        style=discord.ButtonStyle.secondary, custom_id="notify:toggle")
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = await get_or_create_role(interaction.guild, NOTIFY_ROLE_NAME)
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role, reason="Отключил уведомления")
            await interaction.response.send_message("🔕 Уведомления отключены.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Включил уведомления")
            await interaction.response.send_message(
                "🔔 Уведомления включены — будете упомянуты при анонсах турниров/ивентов.",
                ephemeral=True)


# ---------------- 🎙 создание голосовых комнат с выбором параметров ----------------

RANK_LABELS = {
    "herald": "Herald", "guardian": "Guardian", "crusader": "Crusader",
    "archon": "Archon", "legend": "Legend", "ancient": "Ancient",
    "divine": "Divine", "immortal": "Immortal", "any": "Любой ранг",
}


class VoiceRoomModal(discord.ui.Modal, title="Создать голосовую комнату"):
    size = discord.ui.TextInput(
        label="Размер (2–10)", placeholder="напр. 5", max_length=2, default="5")
    mode = discord.ui.TextInput(
        label="Режим: ranked / turbo / lp / unranked", placeholder="ranked",
        max_length=10, default="ranked")
    rank = discord.ui.TextInput(
        label="Ранг (herald/guardian/.../immortal/any)", placeholder="any",
        max_length=10, default="any")

    def __init__(self, db: Storage):
        super().__init__()
        self.db = db

    async def on_submit(self, interaction: discord.Interaction):
        try:
            size = int(str(self.size.value))
            size = max(2, min(10, size))
        except ValueError:
            size = 5
        mode = str(self.mode.value).lower().strip()
        if mode not in GAME_MODE_NAMES:
            mode = "unranked"
        mode_label = GAME_MODE_NAMES[mode]

        rank_raw = str(self.rank.value).lower().strip()
        rank_label = RANK_LABELS.get(rank_raw, "Любой ранг")

        member = interaction.user
        category = interaction.channel.category
        ch_name = f"🎙 {member.display_name} ({mode_label} • {rank_label} • {size})"
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True),
            member: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, manage_channels=True),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True),
        }
        temp = await member.guild.create_voice_channel(
            name=ch_name, category=category, user_limit=size,
            overwrites=overwrites,
            reason=f"Создано {member}: {mode_label}, {rank_label}, {size} мест")
        self.db.register_voice_channel(temp.id, interaction.guild.id)
        await member.move_to(temp)
        await interaction.response.send_message(
            f"✅ Комната создана: {temp.mention} — {mode_label}, {rank_label}, до {size} игроков.",
            ephemeral=True)


class VoiceRoomCreateView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Создать комнату", emoji="🎙",
                        style=discord.ButtonStyle.success,
                        custom_id="voice:create_room")
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoiceRoomModal(self.db))


# ---------------- 🚨 жалобы на игроков в войсе ----------------

class VoiceReportModal(discord.ui.Modal, title="Жалоба на игрока"):
    target = discord.ui.TextInput(
        label="Кому жалуемся? (ID или @упоминание)", placeholder="@username или 123456789",
        max_length=50)
    reason = discord.ui.TextInput(
        label="Причина", placeholder="напр. Мешает, кричит в микрофон",
        max_length=200, style=discord.TextStyle.paragraph)

    def __init__(self):
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return

        target_str = str(self.target.value).strip()
        member = None

        if target_str.isdigit():
            member = guild.get_member(int(target_str))
        elif target_str.startswith("<@") and target_str.endswith(">"):
            uid = target_str.strip("<@!>")
            if uid.isdigit():
                member = guild.get_member(int(uid))

        if not member:
            await interaction.response.send_message(
                "Игрок не найден на сервере.", ephemeral=True)
            return

        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                f"{member.display_name} сейчас не в голосовом канале.", ephemeral=True)
            return

        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.moderate_members:
            await member.move_to(None, reason=f"Кик по жалобе от {interaction.user}")
            await interaction.response.send_message(
                f"✅ **{member.display_name}** кикнут из голосового. "
                f"Причина: {self.reason.value}", ephemeral=False)
            mod_log = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL)
            if mod_log:
                await mod_log.send(
                    f"🚨 **{interaction.user}** кикнул **{member}** из.voice. "
                    f"Причина: {self.reason.value}")
        else:
            mod_log = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL)
            await interaction.response.send_message(
                f"📋 Жалоба на **{member.display_name}** отправлена модераторам. "
                f"Причина: {self.reason.value}", ephemeral=False)
            if mod_log:
                await mod_log.send(
                    f"🚨 **Жалоба** от {interaction.user.mention}: "
                    f"**{member.display_name}** в `{member.voice.channel.name}` — "
                    f"{self.reason.value}")


class VoiceReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Пожаловаться", emoji="🚨",
                        style=discord.ButtonStyle.danger,
                        custom_id="voice:report")
    async def report_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoiceReportModal())


# ---------------- cog ----------------

class ServerManagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = db  # общий экземпляр с dota_stats_v3
        self.temp_voice_channels: set[int] = set()
        self.resync_ranks.start()
        self.auto_purge.start()
        self.update_stats_channels.start()
        self.hero_of_the_day.start()
        self.weekly_meta_digest.start()
        self.daily_patch_digest.start()
        self.daily_verification_sweep.start()

    def cog_unload(self):
        self.resync_ranks.cancel()
        self.auto_purge.cancel()
        self.update_stats_channels.cancel()
        self.hero_of_the_day.cancel()
        self.weekly_meta_digest.cancel()
        self.daily_patch_digest.cancel()
        self.daily_verification_sweep.cancel()

    async def cog_load(self):
        self.bot.add_view(VerificationView(self.db))
        self.bot.add_view(LFGPanelView())
        self.bot.add_view(LeaderboardPanelView(self.db))
        self.bot.add_view(HeroRollView())
        self.bot.add_view(NotifyRoleView())
        self.bot.add_view(VoiceRoomCreateView(self.db))
        self.bot.add_view(PatchAnalyticsView())
        self.bot.add_view(VoiceReportView())

    # ---------- вход нового участника ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        role = discord.utils.get(member.guild.roles, name=UNVERIFIED_ROLE)
        if role:
            await member.add_roles(role, reason="Новый участник — требуется верификация")

    # ---------- ежедневная проверка: все без SteamID должны быть Unverified ----------

    @tasks.loop(time=DAILY_VERIFICATION_SWEEP_TIME_UTC)
    async def daily_verification_sweep(self):
        for guild in self.bot.guilds:
            unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE)
            if not unverified_role:
                continue
            checked = 0
            fixed = 0
            for member in guild.members:
                if member.bot:
                    continue
                account_id = self.db.get_account_id(member.id)
                checked += 1
                if account_id is None and unverified_role not in member.roles:
                    try:
                        await member.add_roles(
                            unverified_role, reason="Ежедневная проверка: SteamID не привязан")
                        fixed += 1
                    except discord.Forbidden:
                        pass
                await asyncio.sleep(SWEEP_MEMBER_DELAY_SECONDS)
            if DEBUG_LOG:
                print(f"[VERIFY SWEEP] {guild.name}: проверено {checked}, "
                      f"выдана роль Unverified {fixed} участникам")

    @daily_verification_sweep.before_loop
    async def before_verification_sweep(self):
        await self.bot.wait_until_ready()

    # ---------- периодическая пересинхронизация рангов ----------

    @tasks.loop(hours=RANK_RESYNC_INTERVAL_HOURS)
    async def resync_ranks(self):
        for guild in self.bot.guilds:
            for discord_id, account_id, steam_id64 in self.db.all_players():
                member = guild.get_member(discord_id)
                if not member:
                    continue
                try:
                    await assign_rank_role(member, account_id)
                except Exception as e:
                    if DEBUG_LOG:
                        print(f"[RANK SYNC] ошибка для {discord_id}: {e}")
                await asyncio.sleep(1)  # не долбить OpenDota запросами подряд

    @resync_ranks.before_loop
    async def before_resync(self):
        await self.bot.wait_until_ready()

    # ---------- join-to-create голосовые комнаты ----------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                     before: discord.VoiceState, after: discord.VoiceState):
        # удаление опустевших временных каналов
        if before.channel and before.channel.id in self.temp_voice_channels:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Временный войс опустел")
                except discord.NotFound:
                    pass
                self.temp_voice_channels.discard(before.channel.id)

    # ---------- треды вместо спама в ЛФГ ----------

    @commands.command(name="party")
    async def create_party(self, ctx: commands.Context, *, description: str = "Ищу пати"):
        """Создаёт тред для сбора группы вместо флуда сообщениями в чате.
        Тред автоматически архивируется через час неактивности."""
        lfg_channel = discord.utils.get(ctx.guild.text_channels, name=LFG_CHANNEL)
        if not lfg_channel:
            await ctx.send(f"Канал #{LFG_CHANNEL} не найден. Запустите !dota_server_setup.")
            return
        anchor = await lfg_channel.send(f"🎮 {ctx.author.mention}: {description}")
        thread = await anchor.create_thread(
            name=f"Пати — {ctx.author.display_name}",
            auto_archive_duration=PARTY_THREAD_ARCHIVE_MINUTES)
        await thread.send(
            f"Тред создан для {ctx.author.mention}. "
            f"Автоархивация через {PARTY_THREAD_ARCHIVE_MINUTES} мин без сообщений — "
            f"дальнейшее обсуждение ведите здесь, а не в основном чате.")
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

    # ---------- автоочистка каналов ----------

    @tasks.loop(minutes=PURGE_INTERVAL_MINUTES)
    async def auto_purge(self):
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            # обычные именованные каналы (общий чат, ЛФГ)
            for ch_name, hours in AUTO_PURGE_CHANNELS.items():
                channel = discord.utils.get(guild.text_channels, name=ch_name)
                if not channel:
                    continue
                cutoff = now - timedelta(hours=hours)
                try:
                    deleted = await channel.purge(before=cutoff, limit=200)
                    if DEBUG_LOG and deleted:
                        print(f"[PURGE] #{channel.name}: удалено {len(deleted)} сообщений")
                except discord.Forbidden:
                    if DEBUG_LOG:
                        print(f"[PURGE] нет прав на очистку #{channel.name}")

            # ⚔-чат внутри КАЖДОЙ ранговой категории — одинаковое имя во всех
            # трёх категориях, поэтому обходим по категориям, а не по имени
            cutoff = now - timedelta(hours=RANK_CHAT_PURGE_HOURS)
            for group_name in RANK_GROUPS:
                category = discord.utils.get(guild.categories, name=group_name)
                if not category:
                    continue
                for channel in category.text_channels:
                    try:
                        deleted = await channel.purge(before=cutoff, limit=200)
                        if DEBUG_LOG and deleted:
                            print(f"[PURGE] #{channel.name} ({group_name}): "
                                  f"удалено {len(deleted)} сообщений")
                    except discord.Forbidden:
                        if DEBUG_LOG:
                            print(f"[PURGE] нет прав на очистку #{channel.name} ({group_name})")

    @auto_purge.before_loop
    async def before_purge(self):
        await self.bot.wait_until_ready()

    # ---------- 📊 голосовые каналы-счётчики (участники / верифицировано) ----------

    @tasks.loop(minutes=STATS_UPDATE_INTERVAL_MINUTES)
    async def update_stats_channels(self):
        for guild in self.bot.guilds:
            category = discord.utils.get(guild.categories, name=STATS_CATEGORY)
            if not category:
                continue
            verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
            verified_count = len(verified_role.members) if verified_role else 0

            member_ch_name = f"{MEMBER_COUNT_CHANNEL_PREFIX}: {guild.member_count}"
            verified_ch_name = f"{VERIFIED_COUNT_CHANNEL_PREFIX}: {verified_count}"

            member_ch = discord.utils.find(
                lambda c: c.name.startswith(MEMBER_COUNT_CHANNEL_PREFIX), category.voice_channels)
            if member_ch and member_ch.name != member_ch_name:
                try:
                    await member_ch.edit(name=member_ch_name)
                except discord.HTTPException:
                    pass  # Discord ограничивает частоту переименований — просто ждём следующий цикл

            verified_ch = discord.utils.find(
                lambda c: c.name.startswith(VERIFIED_COUNT_CHANNEL_PREFIX), category.voice_channels)
            if verified_ch and verified_ch.name != verified_ch_name:
                try:
                    await verified_ch.edit(name=verified_ch_name)
                except discord.HTTPException:
                    pass

    @update_stats_channels.before_loop
    async def before_stats(self):
        await self.bot.wait_until_ready()

    # ---------- 🐲 герой дня ----------

    @tasks.loop(time=HERO_OF_DAY_TIME_UTC)
    async def hero_of_the_day(self):
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=HERO_OF_DAY_CHANNEL)
            if not channel:
                continue
            await od.ensure_heroes()
            if not od.heroes_cache:
                continue
            hero_id = random.choice(list(od.heroes_cache.keys()))
            hero_name = od.heroes_cache[hero_id]
            recs = await od.item_recommendations(hero_id, per_phase=3)
            embed = discord.Embed(
                title=f"🐲 Герой дня: {hero_name}",
                description="Попробуйте его сегодня в своих играх!",
                color=0x8B4513)
            for phase, items in recs.items():
                embed.add_field(name=phase, value="\n".join(items), inline=True)
            await channel.send(embed=embed)

    @hero_of_the_day.before_loop
    async def before_hero_of_day(self):
        await self.bot.wait_until_ready()

    # ---------- 📈 еженедельный дайджест меты (топ-5 по пикрейту, по понедельникам) ----------

    @tasks.loop(time=WEEKLY_META_TIME_UTC)
    async def weekly_meta_digest(self):
        if discord.utils.utcnow().weekday() != 0:  # только по понедельникам
            return
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=WEEKLY_META_CHANNEL)
            if not channel:
                continue
            stats = await od.hero_stats()
            if not stats:
                continue

            def picks(h): return sum(h.get(f"{i}_pick", 0) for i in range(1, 9))
            def wins(h): return sum(h.get(f"{i}_win", 0) for i in range(1, 9))

            ranked = sorted(stats, key=picks, reverse=True)[:5]
            lines = []
            for h in ranked:
                pk, wn = picks(h), wins(h)
                wr = f"{(wn / pk * 100):.1f}%" if pk else "N/A"
                lines.append(f"**{h['localized_name']}** — WR {wr}, picks {pk}")
            embed = discord.Embed(
                title="📈 Мета недели: топ-5 героев по пикрейту",
                description="\n".join(lines),
                color=0x8B4513)
            notify_role = discord.utils.get(guild.roles, name=NOTIFY_ROLE_NAME)
            content = notify_role.mention if notify_role else None
            await channel.send(content=content, embed=embed)

    @weekly_meta_digest.before_loop
    async def before_weekly_meta(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=PATCH_ANALYTICS_TIME_UTC)
    async def daily_patch_digest(self):
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=PATCH_ANALYTICS_CHANNEL)
            if not channel:
                continue
            stats = await od.hero_stats()
            if not stats:
                continue

            from dota_stats_v3 import _trend_wr, _trend_pr

            total_picks = [0] * 7
            for h in stats:
                for i, p in enumerate(h.get("pub_pick_trend", [0] * 7)):
                    total_picks[i] += p

            deltas = []
            for h in stats:
                wr = _trend_wr(h)
                pr = _trend_pr(h, total_picks)
                if len(wr) < 2:
                    continue
                pk_now = h.get("pub_pick_trend", [0])[-1]
                if pk_now < 100:
                    continue
                deltas.append({
                    "name": h["localized_name"],
                    "wr_now": wr[-1],
                    "wr_delta": wr[-1] - wr[-2],
                    "pr": pr[-1] if pr else 0,
                    "pk_now": pk_now,
                })

            winners = sorted(deltas, key=lambda x: x["wr_delta"], reverse=True)[:3]
            losers = sorted(deltas, key=lambda x: x["wr_delta"])[:3]

            lines = ["**🏆 Winners:**"]
            for d in winners:
                sign = "+" if d["wr_delta"] >= 0 else ""
                lines.append(f"  {d['name']} — WR {d['wr_now']:.1f}% ({sign}{d['wr_delta']:.1f}%)")
            lines.append("\n**📉 Losers:**")
            for d in losers:
                sign = "+" if d["wr_delta"] >= 0 else ""
                lines.append(f"  {d['name']} — WR {d['wr_now']:.1f}% ({sign}{d['wr_delta']:.1f}%)")

            embed = discord.Embed(
                title="📊 Аналитика патча: кто выиграл, кто проиграл",
                description="\n".join(lines),
                color=0x8B4513)
            embed.set_footer(text="Ежедневный дайджест • Публичные ранговые матчи")
            notify_role = discord.utils.get(guild.roles, name=NOTIFY_ROLE_NAME)
            content = notify_role.mention if notify_role else None
            await channel.send(content=content, embed=embed)

    @daily_patch_digest.before_loop
    async def before_daily_patch(self):
        await self.bot.wait_until_ready()

    # ---------- 🏆 лидерборд ----------

    @commands.command(name="leaderboard")
    async def leaderboard(self, ctx: commands.Context):
        """Топ по винрейту среди привязанных участников этого сервера."""
        async with ctx.typing():
            embed = await build_leaderboard_embed(self.db, ctx.guild)
        await ctx.send(embed=embed)

    # ---------- 📊 аналитика патчей ----------

    @commands.command(name="dota_patch_panel")
    @commands.has_permissions(manage_messages=True)
    async def patch_panel(self, ctx: commands.Context):
        """Закрепляет панель аналитики патчей в текущем канале."""
        embed = discord.Embed(
            title="📊 Аналитика патчей",
            description=(
                "Нажмите кнопку ниже, чтобы узнать, какие герои выиграли/проиграли "
                "в последнем обновлении.\n\n"
                "**Победители** — кто получил больше WR\n"
                "**Проигравшие** — кто потерял больше WR\n"
                "**Растущие** — чей пикрейт вырос\n"
                "**Падающие** — чей пикрейт упал\n"
                "**Текущая мета** — топ герои прямо сейчас"
            ),
            color=0x8B4513)
        msg = await ctx.send(embed=embed, view=PatchAnalyticsView())
        try:
            await msg.pin()
        except discord.Forbidden:
            pass
        await ctx.send("✅ Панель аналитики патчей закреплена!", delete_after=5)

    # ---------- проверка базы привязанных игроков ----------

    @commands.command(name="dota_players")
    @commands.has_permissions(manage_roles=True)
    async def list_players(self, ctx: commands.Context):
        """Показывает всех привязанных в базе: есть ли ещё на сервере,
        совпадает ли выданная роль с тем, что сейчас говорит OpenDota.
        Полезно для диагностики верификации и рассинхрона рангов."""
        players = self.db.all_players()
        if not players:
            await ctx.send("В базе пока нет ни одного привязанного SteamID.")
            return

        await ctx.typing()
        lines = []
        left_server = 0
        mismatched = 0
        for discord_id, account_id, steam_id64 in players:
            member = ctx.guild.get_member(discord_id)
            if not member:
                left_server += 1
                lines.append(f"❔ <@{discord_id}> (account_id {account_id}) — покинул сервер")
                continue

            profile = await od.get(f"/players/{account_id}")
            expected_role = tier_to_role_name((profile or {}).get("rank_tier")) or UNRANKED_ROLE
            has_role = any(r.name == expected_role for r in member.roles)
            is_verified = any(r.name == VERIFIED_ROLE for r in member.roles)

            status = "✅" if (has_role and is_verified) else "⚠️"
            if not has_role:
                mismatched += 1
            lines.append(
                f"{status} {member.mention} — account_id {account_id}, "
                f"должна быть роль **{expected_role}**{'' if has_role else ' (НЕ выдана!)'}"
                f"{'' if is_verified else ', роли Verified нет'}"
            )
            await asyncio.sleep(0.3)  # не долбить OpenDota подряд

        header = (f"Всего привязано: {len(players)}, ушли с сервера: {left_server}, "
                  f"расхождение роли: {mismatched}\n\n")

        # Discord режет сообщения на 2000 символов — бьём на части
        chunk = header
        for line in lines:
            if len(chunk) + len(line) + 1 > 1900:
                await ctx.send(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            await ctx.send(chunk)

    # ---------- ручная привязка SteamID администратором ----------

    @commands.command(name="dota_link_player")
    @commands.has_permissions(manage_roles=True)
    async def link_player(self, ctx: commands.Context, member: discord.Member, *, raw_steam_id: str):
        """Привязывает SteamID к участнику от лица администратора — тот же
        результат, что и обычная верификация (роль по рангу, приветствие
        в WELCOME_CHANNEL, запись в канал-бэкап), но без участия самого
        игрока. Полезно, когда SteamID уже известен заранее.
        Использование: !dota_link_player @Игрок 76561198012345678"""
        try:
            account_id = to_account_id(raw_steam_id.strip())
            steam_id64 = to_steam64(raw_steam_id.strip())
        except ValueError:
            await ctx.send("Это не похоже на SteamID (ни 64-битный, ни account_id).")
            return

        async with ctx.typing():
            profile = await od.get(f"/players/{account_id}")
        if not profile or not profile.get("profile"):
            await ctx.send("Не нашёл такой SteamID в OpenDota. Профиль должен быть публичным.")
            return

        self.db.register(member.id, account_id, steam_id64)

        # Дублируем привязку в приватный канал-бэкап (см. dota_stats_v3.py),
        # как и при обычной верификации через VerifyModal.
        dota_cog = self.bot.get_cog("DotaStats")
        if dota_cog:
            try:
                await dota_cog.backup_player_to_channel(member.id, account_id, steam_id64)
            except Exception as e:
                print(f"[BACKUP] не удалось записать привязку {member.id} в канал-бэкап: {e!r}")

        guild = ctx.guild
        unverified = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE)
        verified = await get_or_create_role(guild, VERIFIED_ROLE)
        try:
            if unverified and unverified in member.roles:
                await member.remove_roles(unverified, reason="Верификация выполнена администратором")
            await member.add_roles(verified, reason="Верификация выполнена администратором")
            rank_role_name = await assign_rank_role(member, account_id)
        except discord.Forbidden:
            await ctx.send(
                "SteamID сохранил, но не смог выдать роль — у бота не хватает прав "
                "(Manage Roles) или его роль стоит ниже роли Verified/ранговых в "
                "иерархии сервера (Server Settings -> Roles).")
            return

        persona = profile["profile"].get("personaname", "игрок")
        note = ""
        if rank_role_name == UNRANKED_ROLE:
            note = ("\n\n(Ранг не определился — либо матчи скрыты в настройках приватности "
                    "Steam/Dota, либо статистика ещё не синхронизировалась. Роль обновится "
                    "автоматически при следующей синхронизации.)")
        await ctx.send(
            f"Готово: привязал {member.mention} к **{persona}**, выдал роль **{rank_role_name}**.{note}")

        welcome_ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL)
        if welcome_ch:
            welcome_embed = discord.Embed(
                description=f"🎉 {member.mention} присоединился к серверу как **{rank_role_name}**! "
                            f"Добро пожаловать в бой.",
                color=0x8B4513)
            if profile["profile"].get("avatarfull"):
                welcome_embed.set_thumbnail(url=profile["profile"]["avatarfull"])
            await welcome_ch.send(embed=welcome_embed)

    # ---------- разовая настройка сервера ----------

    @commands.command(name="dota_server_setup")
    @commands.has_permissions(administrator=True)
    async def server_setup(self, ctx: commands.Context):
        """Создаёт роли, категории по рангам, verification-канал и join-to-create.
        ВНИМАНИЕ: переписывает права @everyone на всех существующих каналах."""
        guild = ctx.guild
        everyone = guild.default_role

        unverified = await get_or_create_role(guild, UNVERIFIED_ROLE)
        verified = await get_or_create_role(guild, VERIFIED_ROLE)
        await get_or_create_role(guild, UNRANKED_ROLE)
        rank_roles = {name: await get_or_create_role(guild, name) for name in RANK_TIER_NAMES.values()}

        # канал верификации: виден всем, писать нельзя (только кнопка)
        verify_ch = discord.utils.get(guild.text_channels, name=VERIFICATION_CHANNEL)
        if not verify_ch:
            verify_ch = await guild.create_text_channel(VERIFICATION_CHANNEL)
        await verify_ch.set_permissions(everyone, view_channel=True, send_messages=False)
        await verify_ch.set_permissions(unverified, view_channel=True, send_messages=False)
        await verify_ch.set_permissions(verified, view_channel=False)

        # закрываем все остальные существующие каналы от @everyone
        for ch in guild.channels:
            if ch.id != verify_ch.id:
                try:
                    await ch.set_permissions(everyone, view_channel=False)
                except discord.Forbidden:
                    pass

        # ---- 📋 Начало (видно всем, для новичков) ----
        info_category = discord.utils.get(guild.categories, name=INFO_CATEGORY)
        if not info_category:
            info_category = await guild.create_category(INFO_CATEGORY)
        await info_category.set_permissions(everyone, view_channel=True, send_messages=False)
        await info_category.set_permissions(unverified, view_channel=True, send_messages=False)
        for ch_name in INFO_TEXT_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not ch:
                ch = await guild.create_text_channel(ch_name, category=info_category)
            elif ch.category != info_category:
                await ch.edit(category=info_category)

        # ---- 💬 Таверна (общение, только верифицированные) ----
        community_category = discord.utils.get(guild.categories, name=COMMUNITY_CATEGORY)
        if not community_category:
            community_category = await guild.create_category(COMMUNITY_CATEGORY)
        await community_category.set_permissions(everyone, view_channel=False)
        await community_category.set_permissions(verified, view_channel=True, send_messages=True)
        for ch_name in COMMUNITY_TEXT_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not ch:
                ch = await guild.create_text_channel(ch_name, category=community_category)
            elif ch.category != community_category:
                await ch.edit(category=community_category)

        # ---- 📊 Стратегия (лидерборд, статус, аналитика — только верифицированные) ----
        strategy_category = discord.utils.get(guild.categories, name=STRATEGY_CATEGORY)
        if not strategy_category:
            strategy_category = await guild.create_category(STRATEGY_CATEGORY)
        await strategy_category.set_permissions(everyone, view_channel=False)
        await strategy_category.set_permissions(verified, view_channel=True, send_messages=True)
        for ch_name in STRATEGY_TEXT_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not ch:
                ch = await guild.create_text_channel(ch_name, category=strategy_category)
            elif ch.category != strategy_category:
                await ch.edit(category=strategy_category)

        # ---- 🎮 Игровое (ЛФГ, трейд, бестиарий — только верифицированные) ----
        game_category = discord.utils.get(guild.categories, name=GAME_CATEGORY)
        if not game_category:
            game_category = await guild.create_category(GAME_CATEGORY)
        await game_category.set_permissions(everyone, view_channel=False)
        await game_category.set_permissions(verified, view_channel=True, send_messages=True)
        for ch_name in GAME_TEXT_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not ch:
                ch = await guild.create_text_channel(ch_name, category=game_category)
            elif ch.category != game_category:
                await ch.edit(category=game_category)
        lfg = discord.utils.get(guild.text_channels, name=LFG_CHANNEL)
        await lfg.edit(slowmode_delay=LFG_SLOWMODE_SECONDS)

        # ---- удаление старой категории "🎮 Игровая" (если осталась) ----
        old_game = discord.utils.get(guild.categories, name="🎮 Игровая")
        if old_game:
            for ch in old_game.channels:
                try:
                    await ch.delete(reason="Старая категория Игровая, заменена на Стратегия + Игровое")
                except discord.HTTPException:
                    pass
            try:
                await old_game.delete(reason="Старая категория Игровая")
            except discord.HTTPException:
                pass

        # ---- 🟢 доска "кто сейчас играет" — подключаем к движку из dota_stats_v3 (v4) ----
        status_ch = discord.utils.get(guild.text_channels, name=STATUS_BOARD_CHANNEL)
        if status_ch:
            already_registered = any(
                gid == guild.id for gid, _, _ in self.db.all_status_boards())
            if not already_registered:
                board_embed = discord.Embed(
                    title="🎮 Кто сейчас играет",
                    description="Обновляется автоматически...", color=0x8B4513)
                board_msg = await status_ch.send(embed=board_embed)
                self.db.set_status_board(guild.id, status_ch.id, board_msg.id)
                try:
                    await board_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 🎲 кнопка случайного героя ----
        hero_roll_ch = discord.utils.get(guild.text_channels, name=HERO_ROLL_CHANNEL)
        if hero_roll_ch:
            await post_pinned_info(
                hero_roll_ch, "🐲 Бестиарий героев",
                "Обсуждайте героев здесь, а если лень выбирать в All Pick — жмите кнопку "
                "ниже, бот выберет за вас.",
                view=HeroRollView())

        # ---- join-to-create ----
        jtc_category = discord.utils.get(guild.categories, name=JOIN_TO_CREATE_CATEGORY)
        if not jtc_category:
            jtc_category = await guild.create_category(JOIN_TO_CREATE_CATEGORY)
        jtc_hub = discord.utils.get(guild.voice_channels, name=JOIN_TO_CREATE_CHANNEL)
        if not jtc_hub:
            jtc_hub = await guild.create_voice_channel(JOIN_TO_CREATE_CHANNEL, category=jtc_category)
        # ВАЖНО: хаб сам почти всегда пустеет через секунду после захода
        # (человека сразу переносит в новый temp-канал) — без защиты общий
        # листенер автоудаления снёс бы сам хаб при первом же использовании
        self.db.protect_voice_target(jtc_hub.id, guild.id, "channel")
        await jtc_category.set_permissions(everyone, view_channel=False)
        await jtc_category.set_permissions(verified, view_channel=True, connect=True)

        # ---- канал создания голосовых комнат ----
        vr_create = discord.utils.get(guild.text_channels, name=VOICE_ROOM_CREATE_CHANNEL)
        if not vr_create:
            vr_create = await guild.create_text_channel(
                VOICE_ROOM_CREATE_CHANNEL, category=jtc_category, position=0)
        await vr_create.set_permissions(everyone, send_messages=False)
        await vr_create.set_permissions(verified, send_messages=False)
        vr_pinned = await vr_create.pins()
        vr_embed = discord.Embed(
            title="🎙 Создать голосовую комнату",
            description=(
                "Нажмите кнопку ниже, чтобы создать свою голосовую комнату.\n\n"
                "**Доступные режимы:**\n"
                "⚔️ Рейтинг — полноценный Ranked\n"
                "⚡ Турбо — быстрые игры\n"
                "🤡 Лоу Приорити — для наказанных\n"
                "🎮 Без ранга — обычный All Pick\n\n"
                "Размер: от 2 до 10 игроков."
            ),
            color=0x2B2D31)
        if not vr_pinned:
            vr_msg = await vr_create.send(embed=vr_embed, view=VoiceRoomCreateView(self.db))
            try:
                await vr_msg.pin()
            except discord.Forbidden:
                pass

        # ---- чаты для общения (4-6 каналов) ----
        for ch_name in VOICE_ROOM_CHAT_CHANNELS:
            chat_ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not chat_ch:
                chat_ch = await guild.create_text_channel(ch_name, category=jtc_category)
            await chat_ch.set_permissions(verified, view_channel=True, send_messages=True)

        # ---- канал жалоб ----
        report_ch = discord.utils.get(guild.text_channels, name=VOICE_REPORT_CHANNEL)
        if not report_ch:
            report_ch = await guild.create_text_channel(VOICE_REPORT_CHANNEL, category=jtc_category)
        await report_ch.set_permissions(everyone, send_messages=True, view_channel=True)
        report_pins = await report_ch.pins()
        if not report_pins:
            rp_embed = discord.Embed(
                title="🚨 Жалобы на игроков",
                description=(
                    "Если игрок мешает в голосовом канале — нажмите кнопку ниже.\n\n"
                    "**Администраторы** могут кикать игроков сразу.\n"
                    "**Все остальные** — жалоба уходит модераторам."
                ),
                color=0x2B2D31)
            rp_msg = await report_ch.send(embed=rp_embed, view=VoiceReportView())
            try:
                await rp_msg.pin()
            except discord.Forbidden:
                pass

        # ---- 📊 Статистика сервера (два "немых" голосовых-счётчика) ----
        stats_category = discord.utils.get(guild.categories, name=STATS_CATEGORY)
        if not stats_category:
            stats_category = await guild.create_category(STATS_CATEGORY)
        await stats_category.set_permissions(everyone, view_channel=True, connect=False)
        if not discord.utils.find(lambda c: c.name.startswith(MEMBER_COUNT_CHANNEL_PREFIX),
                                   stats_category.voice_channels):
            await guild.create_voice_channel(
                f"{MEMBER_COUNT_CHANNEL_PREFIX}: {guild.member_count}", category=stats_category)
        if not discord.utils.find(lambda c: c.name.startswith(VERIFIED_COUNT_CHANNEL_PREFIX),
                                   stats_category.voice_channels):
            await guild.create_voice_channel(
                f"{VERIFIED_COUNT_CHANNEL_PREFIX}: 0", category=stats_category)

        # ---- ранговые категории: текст + два голосовых (Radiant/Dire) ----
        for group_name, tier_names in RANK_GROUPS.items():
            category = discord.utils.get(guild.categories, name=group_name)
            if not category:
                category = await guild.create_category(group_name)
            overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}
            for tn in tier_names:
                overwrites[rank_roles[tn]] = discord.PermissionOverwrite(view_channel=True)
            await category.edit(overwrites=overwrites)
            if not category.text_channels:
                await guild.create_text_channel("⚔-чат", category=category)
            for vc_name in RANK_VOICE_NAMES:
                vc = discord.utils.get(category.voice_channels, name=vc_name)
                if not vc:
                    vc = await guild.create_voice_channel(vc_name, category=category)
                # ВАЖНО: это постоянные каналы, а не join-to-create — без защиты
                # их снесёт общий листенер автоудаления пустых войсов в
                # dota_stats_v3.py (on_voice_state_update) при первом же опустении
                self.db.protect_voice_target(vc.id, guild.id, "channel")

        # ---- 🛠 Модерация (staff-only) ----
        staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
        staff_category = discord.utils.get(guild.categories, name=STAFF_CATEGORY)
        if not staff_category:
            staff_category = await guild.create_category(STAFF_CATEGORY)
        overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True)
        await staff_category.edit(overwrites=overwrites)
        mod_log = discord.utils.get(guild.text_channels, name=MOD_LOG_CHANNEL)
        if not mod_log:
            await guild.create_text_channel(MOD_LOG_CHANNEL, category=staff_category)
        elif mod_log.category != staff_category:
            await mod_log.edit(category=staff_category)

        # ---- закреплённые справки (ЛФГ и лидерборд — сразу с кнопками) ----
        for ch_name, (title, text) in PINNED_INFO.items():
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not ch:
                continue
            if ch_name == LFG_CHANNEL:
                await post_pinned_info(ch, title, text, view=LFGPanelView())
            elif ch_name == LEADERBOARD_CHANNEL:
                await post_pinned_info(ch, title, text, view=LeaderboardPanelView(self.db))
            elif ch_name == WEEKLY_META_CHANNEL:
                await post_pinned_info(ch, title, text, view=NotifyRoleView())
            else:
                await post_pinned_info(ch, title, text)

        # ---- темы каналов (визуальное оформление) ----
        for ch_name, topic in CHANNEL_TOPICS.items():
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if ch and ch.topic != topic:
                try:
                    await ch.edit(topic=topic)
                except discord.HTTPException:
                    pass

        # ---- read-only каналы: только кнопки бота, участникам писать нельзя ----
        for ch_name in READ_ONLY_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if ch:
                await ch.set_permissions(everyone, send_messages=False)
                await ch.set_permissions(verified, send_messages=False)

        # ---- кастомные стикеры (если положены в stickers/) ----
        await upload_custom_stickers(guild)

        # ---- сообщение с кнопкой верификации ----
        embed = discord.Embed(
            title="🔐 ВЕРИФИКАЦИЯ",
            description=(
                "Для получения доступа к серверу привяжите свой Steam-аккаунт.\n\n"
                "После привязки вам автоматически будет выдана роль "
                "соответствующего ранга и откроется доступ ко всем каналам.\n\n"
                "**Нажмите кнопку ниже, чтобы начать.**"
            ),
            color=0x2B2D31)
        try:
            existing_pins = await verify_ch.pins()
        except discord.Forbidden:
            existing_pins = []
        if not existing_pins:
            verify_msg = await verify_ch.send(embed=embed, view=VerificationView(self.db))
            try:
                await verify_msg.pin()
            except discord.Forbidden:
                pass

        await ctx.send(
            "Готово: категории \"Начало/Таверна/Стратегия/Игровое\", ранговые каналы, "
            "доска \"кто в игре\", лидерборд, стратегии, войс-комнаты с выбором параметров, "
            "жалобы, ролл героя, уведомления и верификация настроены.")


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerManagement(bot))
