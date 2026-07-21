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

RANK_TO_CATEGORY = {}
for _cat, _ranks in RANK_GROUPS.items():
    for _r in _ranks:
        RANK_TO_CATEGORY[_r.lower()] = _cat
RANK_RESYNC_INTERVAL_HOURS = 24

# ---- тематические категории и каналы ----

INFO_CATEGORY = "📋 Начало"
INFO_TEXT_CHANNELS = ["📜-правила", "📢-объявления"]  # + VERIFICATION_CHANNEL создаётся отдельно

COMMUNITY_CATEGORY = "⚔️ Арена"
COMMUNITY_TEXT_CHANNELS = ["👋-приветствия", "💬-чат", "🎉-ивенты"]

STRATEGY_CATEGORY = "📊 Стратегия"
STRATEGY_TEXT_CHANNELS = ["🏆-лидерборд", "🟢-кто-в-игре", "🧠-стратегия"]

PATCH_ANALYTICS_CHANNEL = "📊-патчи"

GAME_CATEGORY = "🎮 Игровое"
LFG_CHANNEL = "🔍-лфг"
GAME_TEXT_CHANNELS = [LFG_CHANNEL, "🐲-бестиарий"]

GUEST_CATEGORY = "🎮 Гости"
GUEST_CHANNEL = "🎮-гости"

SHOP_CATEGORY = "🛒 Магазин"
SHOP_CHANNEL = "🛒-магазин"

RANK_VOICE_NAMES = []  # убраны — вместо них ранговые голосовые при создании комнат

JOIN_TO_CREATE_CATEGORY = "🎙 Голосовые комнаты"

VOICE_ROOM_CREATE_CHANNEL = "🎮-создание-комнат"
VOICE_ROOM_CHAT_CHANNELS = []
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

AUTO_PURGE_CHANNELS = {"💬-чат": 6, LFG_CHANNEL: 2}  # имя канала -> часов хранения истории
PURGE_INTERVAL_MINUTES = 30
LFG_SLOWMODE_SECONDS = 15
CHAT_SLOWMODE_SECONDS = 5  # общий чат — чтобы сообщения не тонули
EVENTS_SLOWMODE_SECONDS = 30  # ивенты — не спамить

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
HERO_ROLL_CHANNEL = "🐲-бестиарий"

# 🔔 самоназначаемая роль уведомлений (турниры/ивенты сервера)
NOTIFY_ROLE_NAME = "🔔 Уведомления"

# 🎭 роли интересов
INTEREST_ROLES = {
    "🎯 Ищу тиму": "🎯 Ищу тиму",
    "🎓 Коучусь": "🎓 Коучусь",
    "📹 Делаю контент": "📹 Делаю контент",
    "🏆 Турниры": "🏆 Турнирный игрок",
}

# 📈 прогресс MMR
MMR_PROGRESS_CHANNEL = "🏆-лидерборд"

# 📋 FAQ
FAQ_CHANNEL = "📜-правила"

# 🔔 токсичность
TOXICITY_TRIGGER_WORDS = ["IDIOT", "NOOB", "FEEDER", "ТВОЙ МАМА", "IDIOT", "N00B", "RETARD"]
TOXICITY_THRESHOLD = 3

# 📈 еженедельный дайджест меты — топ-5 героев по пикрейту, по понедельникам
WEEKLY_META_TIME_UTC = dt_time(hour=10, minute=0)
WEEKLY_META_CHANNEL = "📢-объявления"

# 📊 ежедневный дайджест аналитики патчей
PATCH_ANALYTICS_TIME_UTC = dt_time(hour=11, minute=0)

# 🔇 каналы, где обычным участникам нельзя писать текст — только кнопки/модалки бота
READ_ONLY_CHANNELS = ["🧠-стратегия", "🏆-лидерборд", STATUS_BOARD_CHANNEL, SHOP_CHANNEL, PATCH_ANALYTICS_CHANNEL]

# ⚡ быстрый матч
QUICK_MATCH_CHANNEL = "🎮-создание-комнат"
QUICK_MATCH_TIMEOUT_SECONDS = 60
QUICK_MATCH_ROLE_NAME = "⚡ Ищет игру"

# 🐲 квест дня
DAILY_QUEST_CHANNEL = "🎉-ивенты"
DAILY_QUEST_TIME_UTC = dt_time(hour=12, minute=0)
DAILY_QUEST_ROLE_NAME = "🐲 Знаток дня"

# 📺 стримы сервера
STREAMS_ROLE_NAME = "📺 Стример"
STREAMS_CHANNEL = "🎉-ивенты"

# 📋 навигация
NAVIGATION_CHANNEL = "📜-правила"

# 💬 напоминание о верификации
VERIFY_REMINDER_INTERVAL_HOURS = 6

# 🧹 автоочистка чатов внутри КАЖДОЙ ранговой категории (все они называются
# одинаково "⚔-чат", поэтому чистятся не по имени, а перебором категорий)
RANK_CHAT_PURGE_HOURS = 8

# 📝 темы (описания под названием) для визуального оформления каналов
CHANNEL_TOPICS = {
    "📜-правила": "Обязательно к прочтению перед общением на сервере + навигация по серверу",
    "📢-объявления": "Новости сервера, патчи, турниры — публикует администрация и бот",
    "👋-приветствия": "Бот здоровается с новыми верифицированными игроками",
    "💬-чат": "Общение на любые темы, связанные с Dota 2",
    "🎉-ивенты": "Анонсы, обсуждение мероприятий, квест дня и стримы",
    "🔍-лфг": "Ищете пати? Жмите кнопку — бот создаст тред",
    "🟢-кто-в-игре": "Автообновляемая доска — кто из участников сейчас играет",
    "🏆-лидерборд": "Топ сервера по винрейту — только кнопка",
    "🧠-стратегия": "Панель статистики и стратегий — только кнопки",
    "🐲-бестиарий": "Обсуждение героев и кнопка «случайный герой»",
    "🛒-магазин": "Магазин shards: ежедневный бонус, товары и баланс",
    "🎮-создание-комнат": "Создание голосовых комнат + быстрый матч",
    PATCH_ANALYTICS_CHANNEL: "Аналитика патчей: победители, проигравшие, мета",
    GUEST_CHANNEL: "Гостевая зона — создайте временную голосовую комнату для общения",
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
    "🧠-стратегия": (
        "🧠 Стратегия",
        "«🔥 Топ мета героев» — топ-10 самых популярных героев с винрейтом, "
        "«🛒 Предметы под героя» — рекомендованный билд по фазам игры, "
        "«📦 Топ предметы (тренд)» — что чаще всего берут в топовых сборках."
    ),
    "🏆-лидерборд": (
        "🏆 Лидерборд сервера",
        "Нажмите кнопку «🏆 Показать лидерборд» ниже — топ-10 привязанных участников по "
        f"винрейту (нужно минимум {LEADERBOARD_MIN_GAMES} игр на аккаунте, чтобы попасть в список). "
        "Писать текст в этом канале нельзя — только кнопка."
    ),
    "💬-чат": (
        "💬 Чат сервера",
        "Общайтесь на темы Dota 2, делитесь опытом и просто болтайте.\n"
        "Слоумод: 5 секунд между сообщениями."
    ),
    "🎉-ивенты": (
        "🎉 Ивенты сервера",
        "Анонсы турниров, внутренних соревнований и мероприятий.\n"
        "Слоумод: 30 секунд — только важные новости."
    ),
    "🐲-бестиарий": (
        "🐲 Бестиарий героев",
        "Обсуждайте героев, а если лень выбирать — жмите кнопку ниже, бот выберет случайного."
    ),
    "📊-патчи": (
        "📊 Аналитика патчей",
        "Кто выиграл, кто проиграл — нажмите кнопку, чтобы узнать."
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

        # приветствие с картой сервера
        welcome_text = (
            f"Готово! Привязал вас к **{persona}**, выдал роль **{rank_role_name}**.{note}\n\n"
            "**📋 Где что находится:**\n\n"
            "**📋 Начало** — правила, объявления, навигация\n"
            "**⚔️ Арена** — общение: чат, ивенты, приветствия\n"
            "**📊 Стратегия** — лидерборд, аналитика, патчи\n"
            "**🎮 Игровое** — поиск пати, бестиарий\n"
            "**🎙 Голосовые** — создайте войс-комнату или нажмите «Быстрый матч»\n"
            "**🛒 Магазин** — shards, бонусы, товары\n\n"
            "Все каналы работают через **кнопки** — просто нажмите!"
        )
        await interaction.response.send_message(welcome_text, ephemeral=True)

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

RANK_ORDER = ["herald", "guardian", "crusader", "archon",
              "legend", "ancient", "divine", "immortal"]

RANK_LABELS = {
    "herald": "Herald", "guardian": "Guardian", "crusader": "Crusader",
    "archon": "Archon", "legend": "Legend", "ancient": "Ancient",
    "divine": "Divine", "immortal": "Immortal", "any": "Любой ранг",
}


class VoiceRoomSetupView(discord.ui.View):
    """Пошаговое создание голосовой комнаты через выпадающие списки."""
    def __init__(self, db: Storage):
        super().__init__(timeout=120)
        self.db = db
        self.mode = "ranked"
        self.rank = "any"
        self.size = 5

    def _build_embed(self):
        m = GAME_MODE_NAMES[self.mode]
        r = RANK_LABELS[self.rank]
        return discord.Embed(
            title="🎙 Создать голосовую комнату",
            description=(
                f"**Режим:** {m}\n"
                f"**Ранг:** {r}\n"
                f"**Игроков:** {self.size}\n\n"
                "Выберите параметры ниже и нажмите «Создать»."
            ),
            color=0x2B2D31)

    @discord.ui.select(
        placeholder="Режим игры",
        options=[
            discord.SelectOption(label="⚔️ Рейтинг", value="ranked"),
            discord.SelectOption(label="⚡ Турбо", value="turbo"),
            discord.SelectOption(label="🤡 Лоу Приорити", value="lp"),
            discord.SelectOption(label="🎮 Без ранга", value="unranked"),
        ], row=0)
    async def mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.mode = select.values[0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._build_embed(), view=self)

    @discord.ui.select(
        placeholder="Ранг",
        options=[
            discord.SelectOption(label="Любой ранг", value="any", emoji="🎯"),
            discord.SelectOption(label="Herald", value="herald", emoji="1️⃣"),
            discord.SelectOption(label="Guardian", value="guardian", emoji="2️⃣"),
            discord.SelectOption(label="Crusader", value="crusader", emoji="3️⃣"),
            discord.SelectOption(label="Archon", value="archon", emoji="4️⃣"),
            discord.SelectOption(label="Legend", value="legend", emoji="5️⃣"),
            discord.SelectOption(label="Ancient", value="ancient", emoji="6️⃣"),
            discord.SelectOption(label="Divine", value="divine", emoji="7️⃣"),
            discord.SelectOption(label="Immortal", value="immortal", emoji="8️⃣"),
        ], row=1)
    async def rank_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.rank = select.values[0]
        await interaction.response.defer()
        await interaction.message.edit(embed=self._build_embed(), view=self)

    @discord.ui.select(
        placeholder="Игроков: 5",
        options=[
            discord.SelectOption(label=f"{n} игроков", value=str(n))
            for n in range(2, 11)
        ], row=2)
    async def size_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.size = int(select.values[0])
        await interaction.response.defer()
        await interaction.message.edit(embed=self._build_embed(), view=self)

    @discord.ui.button(label="Создать комнату", emoji="🎙",
                        style=discord.ButtonStyle.success, row=3)
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        guild = interaction.guild
        mode_label = GAME_MODE_NAMES[self.mode]
        rank_label = RANK_LABELS[self.rank]
        ch_name = f"🎙 {member.display_name} ({mode_label} • {rank_label} • {self.size})"

        if self.rank != "any" and self.rank in RANK_TO_CATEGORY:
            cat_name = RANK_TO_CATEGORY[self.rank]
        else:
            cat_name = JOIN_TO_CREATE_CATEGORY
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            category = interaction.channel.category

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
            member: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, manage_channels=True),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True),
        }

        if self.rank != "any" and self.rank in RANK_ORDER:
            idx = RANK_ORDER.index(self.rank)
            allowed_ranks = RANK_ORDER[max(0, idx - 1):idx + 1]
            for tier_num, tier_name in RANK_TIER_NAMES.items():
                if tier_name.lower() in allowed_ranks:
                    role = discord.utils.get(guild.roles, name=tier_name)
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True, connect=True, speak=True)

        temp = await guild.create_voice_channel(
            name=ch_name, category=category, user_limit=self.size,
            overwrites=overwrites,
            reason=f"Создано {member}: {mode_label}, {rank_label}, {self.size} мест")
        self.db.register_voice_channel(temp.id, guild.id)
        await member.move_to(temp)

        if self.rank == "any":
            note = "Могут зайти все верифицированные."
        else:
            allowed_names = [RANK_LABELS[r].title() for r in allowed_ranks]
            note = f"Могут зайти: {', '.join(allowed_names)}."

        await interaction.followup.send(
            f"✅ Комната: {temp.mention}\n"
            f"{mode_label} • {rank_label} • {self.size} мест\n{note}",
            ephemeral=True)
        self.stop()


class VoiceRoomCreateView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Создать комнату", emoji="🎙",
                        style=discord.ButtonStyle.success,
                        custom_id="voice:create_room")
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = VoiceRoomSetupView(self.db)
        await interaction.response.send_message(
            embed=view._build_embed(), view=view, ephemeral=True)


# ---------------- 🎮 гостевые комнаты (для неверифицированных) ----------------

class GuestVoiceView(discord.ui.View):
    """Создание временной голосовой комнаты для гостей (неверифицированных)."""
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Создать комнату", emoji="🎮",
                        style=discord.ButtonStyle.primary,
                        custom_id="voice:create_guest")
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        guild = interaction.guild
        ch_name = f"🎮 {member.display_name}"

        category = discord.utils.get(guild.categories, name=GUEST_CATEGORY)
        if not category:
            category = interaction.channel.category

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            member: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, manage_channels=True),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True),
        }

        temp = await guild.create_voice_channel(
            name=ch_name, category=category, user_limit=5,
            overwrites=overwrites,
            reason=f"Гостевая комната от {member}")
        self.db.register_voice_channel(temp.id, guild.id)
        await member.move_to(temp)
        await interaction.followup.send(
            f"✅ Гостевая комната: {temp.mention}\n"
            "Когда все выйдут — комната удалится автоматически.",
            ephemeral=True)


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


# ---------------- ⚡ быстрый матч ----------------

quick_match_queues: dict[int, list[discord.Member]] = {}
quick_match_locks: dict[int, asyncio.Lock] = {}


class QuickMatchView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Найти тиму", emoji="⚡",
                        style=discord.ButtonStyle.success,
                        custom_id="quick_match:start")
    async def find_match_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild_id
        if guild_id not in quick_match_queues:
            quick_match_queues[guild_id] = []
        if guild_id not in quick_match_locks:
            quick_match_locks[guild_id] = asyncio.Lock()

        async with quick_match_locks[guild_id]:
            queue = quick_match_queues[guild_id]
            if interaction.user in queue:
                await interaction.response.send_message(
                    "Вы уже в очереди! Дождитесь начала.", ephemeral=True)
                return
            queue.append(interaction.user)
            count = len(queue)

        embed = discord.Embed(
            title="⚡ Быстрый матч",
            description=(
                f"**Ищут игру:** {count}/5\n\n"
                + "\n".join(f"• {m.display_name}" for m in queue)
                + "\n\nКогда соберётся 5 — бот создаст войс-комнату."
            ),
            color=0x8B4513)
        await interaction.response.send_message(embed=embed)

        if count >= 5:
            await _start_quick_match(interaction.guild, self.db)


async def _start_quick_match(guild: discord.Guild, storage: Storage):
    guild_id = guild.id
    async with quick_match_locks.get(guild_id, asyncio.Lock()):
        queue = quick_match_queues.get(guild_id, [])
        if len(queue) < 5:
            return
        players = queue[:5]
        quick_match_queues[guild_id] = queue[5:]

    verified = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
    everyone = guild.default_role

    jtc_category = discord.utils.get(guild.categories, name=JOIN_TO_CREATE_CATEGORY)
    if not jtc_category:
        jtc_category = discord.utils.get(guild.categories, name="🎙 Голосовые комнаты")

    overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False, connect=False),
    }
    if verified:
        overwrites[verified] = discord.PermissionOverwrite(view_channel=True, connect=False)
    for p in players:
        overwrites[p] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)

    names = ", ".join(p.display_name for p in players)
    vc = await guild.create_voice_channel(
        f"⚡ Матч — {names}", category=jtc_category, user_limit=5,
        overwrites=overwrites, reason="Быстрый матч")
    storage.protect_voice_target(vc.id, guild.id, "voice")

    try:
        ch = discord.utils.get(guild.text_channels, name=VOICE_ROOM_CREATE_CHANNEL)
        if ch:
            embed = discord.Embed(
                title="⚡ Матч собран!",
                description=f"Комната: {vc.mention}\nУчастники: {names}",
                color=0x8B4513)
            await ch.send(embed=embed)
    except Exception:
        pass

    for p in players:
        try:
            await p.move_to(vc)
        except Exception:
            pass


# ---------------- 🐲 квест дня ----------------

DAILY_QUEST_QUESTIONS = [
    {"q": "Какой герой имеет самое высокое базовое здоровье?", "options": ["Pudge", "Mars", "Axe", "Abyssal Underlord"], "answer": 0},
    {"q": "Сколько золота даёт убийство героя (базовая награда)?", "options": ["100", "200", "300", "400"], "answer": 2},
    {"q": "Какой предмет даёт +5 секунд на каст?", "options": ["Aether Lens", "Octarine Core", "Kaya", "Blink Dagger"], "answer": 0},
    {"q": "Кто из героев — Strength?", "options": ["Anti-Mage", "Sniper", "Huskar", "Invoker"], "answer": 2},
    {"q": "Какой навык даёт невидимость на 5 секунд?", "options": ["Shadow Blade", "Silver Edge", "Glimmer Cape", "Blink Dagger"], "answer": 0},
    {"q": "Сколько тир курьеров существует?", "options": ["2", "3", "4", "5"], "answer": 1},
    {"q": "Какой предмет ломает крипов на 5?", "options": ["Battle Fury", "Maelstrom", "Radiance", "Shiva's Guard"], "answer": 0},
    {"q": "Какой герой первым получил аркану?", "options": ["Crystal Maiden", "Juggernaut", "Pudge", "Faceless Void"], "answer": 0},
    {"q": "Какой максимальный уровень героя?", "options": ["25", "30", "20", "35"], "answer": 0},
    {"q": "Какой атрибут даёт +HP и +damage?", "options": ["Strength", "Agility", "Intelligence", "Все"], "answer": 0},
    {"q": "Сколько секунд длится Glyph of Fortification?", "options": ["3", "5", "7", "10"], "answer": 2},
    {"q": "Какой герой может летать по умолчанию?", "options": ["Batrider", "Jakiro", "Io", "Visage"], "answer": 2},
    {"q": "Какой предмет даёт +100 к скорости атаки?", "options": ["Moon Shard", "Mjollnir", "Monkey King Bar", "Divine Rapier"], "answer": 0},
    {"q": "Какой нейтральный кэмп даёт больше всего золота?", "options": ["Ancient", "Hard Camp", "Medium Camp", "Small Camp"], "answer": 0},
    {"q": "Сколько стаков в башне?", "options": ["3", "4", "5", "6"], "answer": 1},
    {"q": "Какой предмет возвращает ману?", "options": ["Linken's Sphere", "Lotus Orb", "Arcane Boots", "Bottle"], "answer": 2},
    {"q": "Какой герой может стену ставить?", "options": ["Earthshaker", "Techies", "Pangolier", "Tusk"], "answer": 0},
    {"q": "Какой эффект даёт Black King Bar?", "options": ["Spell Immunity", "Magic Resistance", "Stun", "Slow"], "answer": 0},
    {"q": "Какой предмет даёт +200 к дальности заклинаний?", "options": ["Aether Lens", "Kaya and Sange", "Aghanim's Scepter", "Eul's Scepter"], "answer": 0},
    {"q": "Какой герой может телепортироваться к союзнику?", "options": ["Io", "Keeper of the Light", "Nature's Prophet", "Четыре варианта"], "answer": 0},
    {"q": "Какой предмет даёт +40 к урону и ломает крипов?", "options": ["Battle Fury", "Maelstrom", "Daedalus", "Desolator"], "answer": 0},
    {"q": "Сколько золота стоит Observer Ward?", "options": ["0", "25", "50", "75"], "answer": 0},
    {"q": "Какой герой может ставить ловушки?", "options": ["Techies", "Sniper", "Troll Warlord", "Windranger"], "answer": 0},
    {"q": "Какой предмет даёт +25 к всем атрибутам?", "options": ["Ultimate Orb", "Skadi", "Manta Style", "Eye of Skadi"], "answer": 0},
    {"q": "Какой навык даёт +100 к скорости передвижения?", "options": ["Surge", "Haste", "Phase Boots", "Yasha"], "answer": 0},
    {"q": "Какой герой может создавать копию себя?", "options": ["Terrorblade", "Phantom Lancer", "Naga Siren", "Chaos Knight"], "answer": 0},
    {"q": "Какой предмет даёт +40 к скорости атаки?", "options": ["Hyperstone", "Maelstrom", "Monkey King Bar", "Divine Rapier"], "answer": 0},
    {"q": "Какой герой может невидимость союзникам?", "options": ["Shadow Demon", "Oracle", "Nyx Assassin", "Riki"], "answer": 2},
    {"q": "Сколько секунд кулдаун у Buyback на макс. уровне?", "options": ["60", "90", "120", "180"], "answer": 1},
    {"q": "Какой предмет даёт +30 к броне?", "options": ["Assault Cuirass", "Shiva's Guard", "Pipe of Insight", "Crimson Guard"], "answer": 0},
    {"q": "Какой герой может телепортироваться в любую точку карты?", "options": ["Nature's Prophet", "Io", "Keeper of the Light", "Storm Spirit"], "answer": 0},
    {"q": "Какой предмет даёт +50% к крипам?", "options": ["Hand of Midas", "Battle Fury", "Maelstrom", "Radiance"], "answer": 0},
    {"q": "Какой герой может тянуть врагов к себе?", "options": ["Pudge", "Clockwerk", "Batrider", "Vengeful Spirit"], "answer": 0},
    {"q": "Какой предмет даёт +20 к всем характеристикам?", "options": ["Skadi", "Manta Style", "Eye of Skadi", "Butterfly"], "answer": 0},
    {"q": "Какой герой может ставить тотемы?", "options": ["Earthshaker", "Ogre Magi", "Shadow Shaman", "Jakiro"], "answer": 0},
    {"q": "Какой предмет даёт +100 к урону?", "options": ["Divine Rapier", "MKB", "Daedalus", "Desolator"], "answer": 0},
    {"q": "Какой герой может становиться невидимым?", "options": ["Riki", "Clinkz", "Weaver", "Все три"], "answer": 3},
    {"q": "Сколько золота даёт убийство крипа melee?", "options": ["36-42", "40-46", "44-50", "48-54"], "answer": 0},
    {"q": "Какой предмет даёт +35 к урону?", "options": ["Maelstrom", "Desolator", "Monkey King Bar", "Daedalus"], "answer": 0},
    {"q": "Какой герой может ставить башни?", "options": ["Shadow Shaman", "Techies", "Nature's Prophet", "Jakiro"], "answer": 0},
]


class DailyQuestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ответить на вопрос дня", emoji="🐲",
                        style=discord.ButtonStyle.primary,
                        custom_id="daily_quest:answer")
    async def answer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        import hashlib
        today = interaction.created_at.date().isoformat()
        idx = int(hashlib.md5(today.encode()).hexdigest(), 16) % len(DAILY_QUEST_QUESTIONS)
        q = DAILY_QUEST_QUESTIONS[idx]
        view = DailyQuestAnswerView(idx, q["answer"])
        options = [
            discord.SelectOption(label=opt, value=str(i))
            for i, opt in enumerate(q["options"])
        ]
        await interaction.response.send_message(
            embed=discord.Embed(title="🐲 Вопрос дня", description=q["q"],
                                color=0x8B4513),
            view=view, ephemeral=True)


class DailyQuestAnswerView(discord.ui.View):
    def __init__(self, question_idx: int, correct_idx: int):
        super().__init__(timeout=60)
        self.correct_idx = correct_idx
        options = [
            discord.SelectOption(label=DAILY_QUEST_QUESTIONS[question_idx]["options"][i],
                                 value=str(i))
            for i in range(len(DAILY_QUEST_QUESTIONS[question_idx]["options"]))
        ]
        self.select = discord.ui.Select(placeholder="Выберите ответ", options=options)
        self.select.callback = self.answer_callback
        self.add_item(self.select)

    async def answer_callback(self, interaction: discord.Interaction):
        chosen = int(self.select.values[0])
        if chosen == self.correct_idx:
            role = await get_or_create_role(interaction.guild, DAILY_QUEST_ROLE_NAME)
            member = interaction.user
            if role not in member.roles:
                await member.add_roles(role, reason="Правильный ответ на вопрос дня")
            await interaction.response.send_message(
                "✅ Правильно! Вы получили роль «🐲 Знаток дня».", ephemeral=True)
        else:
            correct = DAILY_QUEST_QUESTIONS[0]["options"][self.correct_idx]
            await interaction.response.send_message(
                f"❌ Неверно! Правильный ответ: **{correct}**.", ephemeral=True)
        self.stop()


# ---------------- 📺 стримы сервера ----------------

stream_messages: dict[int, int] = {}  # user_id -> message_id


class StreamButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Стримлю", emoji="📺",
                        style=discord.ButtonStyle.secondary,
                        custom_id="streams:toggle")
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = await get_or_create_role(interaction.guild, STREAMS_ROLE_NAME)
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role, reason="Выключил стрим")
            # удалить сообщение о стриме
            msg_id = stream_messages.pop(member.id, None)
            if msg_id:
                events_ch = discord.utils.get(interaction.guild.text_channels, name=STREAMS_CHANNEL)
                if events_ch:
                    try:
                        old_msg = await events_ch.fetch_message(msg_id)
                        await old_msg.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
            await interaction.response.send_message(
                "📺 Стрим завершён — роль убрана, объявление удалено.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Начал стрим")
            events_ch = discord.utils.get(interaction.guild.text_channels, name=STREAMS_CHANNEL)
            if events_ch:
                embed = discord.Embed(
                    title="📺 Стрим!",
                    description=f"{member.mention} начинает стримить!",
                    color=0x8B4513)
                msg = await events_ch.send(content=role.mention, embed=embed)
                stream_messages[member.id] = msg.id
            await interaction.response.send_message(
                "📺 Стрим начат! Роль «📺 Стример» выдана.", ephemeral=True)


# ---------------- 📋 панель навигации ----------------

class NavigationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Навигация по серверу", emoji="📋",
                        style=discord.ButtonStyle.success,
                        custom_id="navigation:show")
    async def show_nav(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📋 Навигация по серверу",
            description="Все каналы и их назначение:",
            color=0x8B4513)
        embed.add_field(
            name="📋 Начало",
            value=(
                "📜-правила — правила сервера\n"
                "📢-объявления — новости и обновления\n"
                "🔐-ВЕРИФИКАЦИЯ — привяжите Steam"
            ),
            inline=False)
        embed.add_field(
            name="⚔️ Арена",
            value=(
                "👋-приветствия — поздравления новичков\n"
                "💬-чат — общение на темы Dota\n"
                "🎉-ивенты — турниры и мероприятия"
            ),
            inline=False)
        embed.add_field(
            name="📊 Стратегия",
            value=(
                "🏆-лидерборд — топ игроков\n"
                "🟢-кто-в-игре — кто сейчас играет\n"
                "🧠-стратегия — аналитика и советы\n"
                "📊-патчи — аналитика патчей"
            ),
            inline=False)
        embed.add_field(
            name="🎮 Игровое",
            value=(
                "🔍-лфг — поиск пати\n"
                "🐲-бестиарий — случайный герой"
            ),
            inline=False)
        embed.add_field(
            name="🎙 Голосовые комнаты",
            value=(
                "🎮-создание-комнат — создайте войс\n"
                "⚡ Быстрый матч — кнопка «Найти тиму»"
            ),
            inline=False)
        embed.add_field(
            name="🛒 Магазин",
            value="🛒-магазин — shards, бонусы, товары",
            inline=False)
        embed.set_footer(text="Все кнопки работают — просто нажмите!")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- 📊 мои матчи ----------------

class MyMatchesView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Мои матчи", emoji="📊",
                        style=discord.ButtonStyle.secondary,
                        custom_id="matches:my")
    async def matches_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        account_id = self.db.get_account_id(interaction.user.id)
        if not account_id:
            await interaction.followup.send(
                "Сначала привяжите SteamID.", ephemeral=True)
            return
        await od.ensure_heroes()
        matches = await od.get(f"/players/{account_id}/matches?limit=5")
        if not matches:
            await interaction.followup.send(
                "Матчи не найдены.", ephemeral=True)
            return
        lines = []
        for m in matches:
            won = (m.get("player_slot", 0) < 128) == m.get("radiant_win", False)
            hero_name = od.heroes_cache.get(m.get("hero_id"), f"Hero#{m.get('hero_id')}")
            kda = f"{m.get('kills',0)}/{m.get('deaths',0)}/{m.get('assists',0)}"
            icon = "✅" if won else "❌"
            lines.append(f"{icon} **{hero_name}** — {kda}")
        embed = discord.Embed(
            title="📊 Последние матчи",
            description="\n".join(lines),
            color=0x8B4513)
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- 🤝 наставник ----------------

MENTOR_ROLE_NAME = "🤝 Наставник"

# 📅 календарь сервера
CALENDAR_CHANNEL = "🎉-ивенты"

# 🗳 опрос дня
DAILY_POLL_CHANNEL = "🎉-ивенты"
DAILY_POLL_TIME_UTC = dt_time(hour=14, minute=0)

# ⏰ напоминание о турнире (за 15 минут)
TOURNAMENT_REMINDER_MINUTES = 15


class MentorView(discord.ui.View):
    def __init__(self, db: Storage = None):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Стать наставником", emoji="🤝",
                        style=discord.ButtonStyle.primary,
                        custom_id="mentor:toggle")
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = await get_or_create_role(interaction.guild, MENTOR_ROLE_NAME)
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role, reason="Отказался от наставничества")
            await interaction.response.send_message(
                "🤝 Вы больше не наставник.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Стал наставником")
            if self.db:
                self.db.grant_achievement(member.id, "mentor")
            await interaction.response.send_message(
                "🤝 Вы стали наставником! Новые игроки смогут найти вас.", ephemeral=True)


class MentorListView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Список наставников", emoji="📋",
                        style=discord.ButtonStyle.secondary,
                        custom_id="mentor:list")
    async def list_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name=MENTOR_ROLE_NAME)
        if not role or not role.members:
            await interaction.response.send_message(
                "📋 Пока нет наставников.", ephemeral=True)
            return
        lines = [f"• {m.display_name}" for m in role.members]
        embed = discord.Embed(
            title="🤝 Наставники сервера",
            description="\n".join(lines),
            color=0x8B4513)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- 🗳 опрос дня ----------------

DAILY_POLL_QUESTIONS = [
    {"q": "Какой атрибут важнее для кэрри?", "options": ["Сила", "Ловкость", "Интеллект", "Все равно"]},
    {"q": "Лучший стартовый предмет для мида?", "options": ["Null Talisman", "Wraith Band", "Bracer", "Faerie Fire"]},
    {"q": "Какой ранг самый populated?", "options": ["Herald", "Guardian", "Crusader", "Archon"]},
    {"q": "Лучший герой для подъёма MMR?", "options": ["Pudge", "Invoker", "Faceless Void", "Snapfire"]},
    {"q": "Сколько длится кулдаун buyback на максимальном уровне?", "options": ["60 сек", "90 сек", "120 сек", "180 сек"]},
    {"q": "Какой предмет даёт True Sight?", "options": ["Dust", "Sentry Ward", "Observer Ward", "Gem"]},
    {"q": "Самый сильный герой в лейте?", "options": ["Anti-Mage", "Spectre", "Faceless Void", "Phantom Lancer"]},
    {"q": "Какой нейтральный предмет Tier 5 лучший?", "options": ["Pirate Hat", "Giant's Ring", "Book of the Dead", "Mirror Shield"]},
    {"q": "Какой ролью легче всего поднять MMR?", "options": ["Кэрри", "Мид", "Офлейн", "Саппорт"]},
    {"q": "Лучший бан в рейтинге сейчас?", "options": ["Pudge", "Invoker", "Meepo", "Phantom Assassin"]},
    {"q": "Стоит ли.buyback早早早?", "options": ["Да, всегда", "Только в лейте", "Нет, экономлю", "Зависит от ситуации"]},
    {"q": "Какой кэмп лучше стакать?", "options": ["Ancient", "Hard", "Medium", "Neutrals"]},
    {"q": "Лучший предмет первого тира?", "options": ["Fairy's Trinket", "Seer Stone", "Unwavering Condition", "Mind Breaker"]},
    {"q": "Самый переоценённый герой?", "options": ["Pudge", "Invoker", "Anti-Mage", "Techies"]},
    {"q": "Какой герой самый fun?", "options": ["Techies", "Invoker", "Pudge", "Meepo"]},
    {"q": "Стоит ли покупать Gem?", "options": ["Да, против невидимых", "Нет, слишком дорого", "Только саппорту", "Зависит от игры"]},
    {"q": "Лучшая стратегия в лейте?", "options": ["5м пуш", "Сплитпуш", "Ратт", "Дождаться BKB"]},
    {"q": "Какой герой лучше для новичков?", "options": ["Wraith King", "Dragon Knight", "Sniper", "Drow Ranger"]},
    {"q": "Стоит ли покупать wards?", "options": ["Да, всегда", "Только саппорту", "Нет, башня даёт", "Зависит от ранга"]},
    {"q": "Какой сервер лучше для Европы?", "options": ["EU West", "EU East", "Russia", "SEA"]},
]


class DailyPollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Проголосовать", emoji="🗳",
                        style=discord.ButtonStyle.primary,
                        custom_id="poll:vote")
    async def vote_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        import hashlib
        today = interaction.created_at.date().isoformat()
        idx = int(hashlib.md5(("poll_" + today).encode()).hexdigest(), 16) % len(DAILY_POLL_QUESTIONS)
        q = DAILY_POLL_QUESTIONS[idx]
        view = PollVoteView(idx, q["options"])
        options = [
            discord.SelectOption(label=opt, value=str(i))
            for i, opt in enumerate(q["options"])
        ]
        await interaction.response.send_message(
            embed=discord.Embed(title="🗳 Опрос дня", description=q["q"],
                                color=0x8B4513),
            view=view, ephemeral=True)


class PollVoteView(discord.ui.View):
    def __init__(self, question_idx: int, options: list[str]):
        super().__init__(timeout=120)
        self.question_idx = question_idx
        self.options = options
        self.votes: dict[int, int] = {}
        sel_options = [discord.SelectOption(label=opt, value=str(i)) for i, opt in enumerate(options)]
        self.select = discord.ui.Select(placeholder="Ваш ответ", options=sel_options)
        self.select.callback = self.vote_callback
        self.add_item(self.select)

    async def vote_callback(self, interaction: discord.Interaction):
        choice = int(self.select.values[0])
        self.votes[interaction.user.id] = choice
        await interaction.response.send_message(
            f"✅ Вы проголосовали за: **{self.options[choice]}**", ephemeral=True)


# ---------------- ⚠️ модерация: предупреждение + таймаут ----------------

class ModWarningView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Предупредить", emoji="⚠️",
                        style=discord.ButtonStyle.danger,
                        custom_id="mod:warning")
    async def warn_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ Нет прав.", ephemeral=True)
            return
        await interaction.response.send_modal(ModWarningModal())


class ModWarningModal(discord.ui.Modal, title="Предупреждение"):
    target = discord.ui.TextInput(label="Кому (ID или @mention)", max_length=100)
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        target_str = str(self.target.value).strip()
        member = None
        if target_str.isdigit():
            member = interaction.guild.get_member(int(target_str))
        elif target_str.startswith("<@") and target_str.endswith(">"):
            try:
                mid = int(target_str[2:-1])
                member = interaction.guild.get_member(mid)
            except ValueError:
                pass
        if not member:
            await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
            return
        db.add_warning(interaction.guild_id, member.id, interaction.user.id, str(self.reason))
        count = db.get_warning_count(interaction.guild_id, member.id)
        embed = discord.Embed(
            title="⚠️ Предупреждение",
            description=f"**{member.display_name}** получил предупреждение #{count}\nПричина: {self.reason.value}",
            color=0xFF0000)
        await interaction.response.send_message(embed=embed)
        mod_log = discord.utils.get(interaction.guild.text_channels, name=MOD_LOG_CHANNEL)
        if mod_log:
            await mod_log.send(
                f"⚠️ **{interaction.user}** предупредил **{member}**: {self.reason.value} "
                f"(всего предупреждений: {count})")


class ModTimeoutView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Таймаут", emoji="🔇",
                        style=discord.ButtonStyle.danger,
                        custom_id="mod:timeout")
    async def timeout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ Нет прав.", ephemeral=True)
            return
        await interaction.response.send_modal(ModTimeoutModal())


class ModTimeoutModal(discord.ui.Modal, title="Таймаут"):
    target = discord.ui.TextInput(label="Кому (ID или @mention)", max_length=100)
    duration = discord.ui.TextInput(label="Минут (1-1440)", max_length=5, default="10")
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        target_str = str(self.target.value).strip()
        member = None
        if target_str.isdigit():
            member = interaction.guild.get_member(int(target_str))
        elif target_str.startswith("<@") and target_str.endswith(">"):
            try:
                mid = int(target_str[2:-1])
                member = interaction.guild.get_member(mid)
            except ValueError:
                pass
        if not member:
            await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
            return
        try:
            mins = max(1, min(1440, int(self.duration.value)))
        except ValueError:
            mins = 10
        try:
            await member.timeout(discord.utils.utcnow() + timedelta(minutes=mins),
                                 reason=f"{interaction.user}: {self.reason.value}")
            embed = discord.Embed(
                title="🔇 Таймаут",
                description=f"**{member.display_name}** замьючен на {mins} мин\nПричина: {self.reason.value}",
                color=0xFF0000)
            await interaction.response.send_message(embed=embed)
            mod_log = discord.utils.get(interaction.guild.text_channels, name=MOD_LOG_CHANNEL)
            if mod_log:
                await mod_log.send(
                    f"🔇 **{interaction.user}** замьютил **{member}** на {mins} мин: {self.reason.value}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Не удалось замутить.", ephemeral=True)


# ---------------- 📅 календарь сервера ----------------

class CalendarView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Календарь", emoji="📅",
                        style=discord.ButtonStyle.primary,
                        custom_id="calendar:show")
    async def show_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📅 Календарь сервера",
            description="Ближайшие события и активности:",
            color=0x8B4513)
        embed.add_field(
            name="🎮 Еженедельные",
            value=(
                "• Пн 10:00 — Дайджест меты\n"
                "• Вт-Пт 12:00 — Квест дня\n"
                "• Сб — Турнир недели (если создан)"
            ),
            inline=False)
        embed.add_field(
            name="🎯 Как участвовать",
            value=(
                "• 🏆 Турниры — кнопка в #🏆-лидерборд\n"
                "• 🐲 Квест дня — кнопка в #🎉-ивенты\n"
                "• 📺 Стримы — кнопка в #🎉-ивенты"
            ),
            inline=False)
        embed.set_footer(text="Следите за объявлениями!")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- ❓ FAQ ----------------

FAQ_ANSWERS = {
    "Как привязать Steam?": "Зайдите в #🔐-ВЕРИФИКАЦИЯ и нажмите кнопку. Введите SteamID (64-бит или account_id).",
    "Как создать голосовую комнату?": "Зайдите в #🎮-создание-комнат, нажмите «🎙 Создать комнату» и выберите параметры.",
    "Как работает быстрый матч?": "Нажмите «⚡ Найти тиму» в #🎮-создание-комнат. Когда соберётся 5 человек — бот создаст войс.",
    "Что такое shards?": "Виртуальная валюта сервера. Получаете за матчи, достижения, ежедневный бонус. Тратите в #🛒-магазин.",
    "Как попасть в лидерборд?": "Нужно минимум 20 игр на аккаунте. Нажмите кнопку в #🏆-лидерборд.",
    "Как стать наставником?": "Нажмите «🤝 Стать наставником» в #📜-правила.",
    "Как стримить на сервере?": "Нажмите «📺 Стримлю» в #🎉-ивенты — получите роль и объявление.",
    "Где правила?": "#📜-правила — обязательно к прочтению.",
    "Как получить роль?": "Роль ранга выдаётся автоматически при верификации и обновляется раз в сутки.",
}


class FAQView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="❓ Частые вопросы", emoji="❓",
                        style=discord.ButtonStyle.primary,
                        custom_id="faq:show")
    async def show_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❓ Частые вопросы",
            color=0x8B4513)
        for q, a in FAQ_ANSWERS.items():
            embed.add_field(name=q, value=a, inline=False)
        embed.set_footer(text="Не нашли ответ? Спросите в #💬-чат!")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- 📈 прогресс MMR ----------------

class MMRProgressView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Мой прогресс", emoji="📈",
                        style=discord.ButtonStyle.secondary,
                        custom_id="mmr:progress")
    async def progress_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        account_id = self.db.get_account_id(interaction.user.id)
        if not account_id:
            await interaction.followup.send("Сначала привяжите SteamID.", ephemeral=True)
            return
        profile = await od.get(f"/players/{account_id}")
        if not profile:
            await interaction.followup.send("Не удалось загрузить профиль.", ephemeral=True)
            return
        rank_tier = profile.get("rank_tier")
        rank_name = "Unranked"
        if rank_tier:
            major = rank_tier // 10
            rank_name = {1: "Herald", 2: "Guardian", 3: "Crusader", 4: "Archon",
                         5: "Legend", 6: "Ancient", 7: "Divine", 8: "Immortal"}.get(major, "Unranked")
        wl = await od.get(f"/players/{account_id}/wl")
        wins = wl.get("win", 0) if wl else 0
        losses = wl.get("lose", 0) if wl else 0
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        embed = discord.Embed(
            title="📈 Мой прогресс",
            description=(
                f"**Ранг:** {rank_name}\n"
                f"**Побед:** {wins}\n"
                f"**Поражений:** {losses}\n"
                f"**Винрейт:** {wr:.1f}%\n"
                f"**Всего матчей:** {total}"
            ),
            color=0x8B4513)
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- 🎭 роли интересов ----------------

class InterestRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Выбрать роль", emoji="🎭",
                        style=discord.ButtonStyle.primary,
                        custom_id="interest:toggle")
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = [
            discord.SelectOption(label=name, value=name, emoji=emoji)
            for name, emoji in INTEREST_ROLES.items()
        ]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎭 Роли интересов",
                description="Выберите роль, чтобы найти единомышленников:",
                color=0x8B4513),
            view=InterestSelectView(),
            ephemeral=True)


class InterestSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(
        placeholder="Выберите роль",
        options=[discord.SelectOption(label=name, value=name)
                 for name in INTEREST_ROLES.keys()],
        min_values=1, max_values=len(INTEREST_ROLES))
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        added = []
        removed = []
        for role_name in select.values:
            role = await get_or_create_role(interaction.guild, role_name)
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                removed.append(role_name)
            else:
                await interaction.user.add_roles(role)
                added.append(role_name)
        parts = []
        if added:
            parts.append(f"Выданы: {', '.join(added)}")
        if removed:
            parts.append(f"Убраны: {', '.join(removed)}")
        await interaction.response.send_message(
            " | ".join(parts) if parts else "Без изменений.", ephemeral=True)
        self.stop()


# ---------------- 🔔 токсичность ----------------

class ToxicityAlertListener(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_counts: dict[int, int] = {}  # user_id -> count

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        content = message.content.upper()
        for word in TOXICITY_TRIGGER_WORDS:
            if word in content:
                uid = message.author.id
                self.msg_counts[uid] = self.msg_counts.get(uid, 0) + 1
                if self.msg_counts[uid] >= TOXICITY_THRESHOLD:
                    mod_log = discord.utils.get(message.guild.text_channels, name=MOD_LOG_CHANNEL)
                    if mod_log:
                        await mod_log.send(
                            f"🔔 **Токсичность:** {message.author.mention} "
                            f"написал подозрительное сообщение в {message.channel.mention}.\n"
                            f"Контекст: {message.content[:200]}")
                    self.msg_counts[uid] = 0
                break


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
        self.daily_quest_post.start()
        self.verify_reminder.start()
        self.daily_poll_post.start()
        self.tournament_reminder.start()

    def cog_unload(self):
        self.resync_ranks.cancel()
        self.auto_purge.cancel()
        self.update_stats_channels.cancel()
        self.hero_of_the_day.cancel()
        self.weekly_meta_digest.cancel()
        self.daily_patch_digest.cancel()
        self.daily_verification_sweep.cancel()
        self.daily_quest_post.cancel()
        self.verify_reminder.cancel()
        self.daily_poll_post.cancel()
        self.tournament_reminder.cancel()

    async def cog_load(self):
        self.bot.add_view(VerificationView(self.db))
        self.bot.add_view(LFGPanelView())
        self.bot.add_view(LeaderboardPanelView(self.db))
        self.bot.add_view(HeroRollView())
        self.bot.add_view(NotifyRoleView())
        self.bot.add_view(VoiceRoomCreateView(self.db))
        self.bot.add_view(GuestVoiceView(self.db))
        self.bot.add_view(PatchAnalyticsView())
        self.bot.add_view(VoiceReportView())
        self.bot.add_view(QuickMatchView(self.db))
        self.bot.add_view(DailyQuestView())
        self.bot.add_view(StreamButtonView())
        self.bot.add_view(NavigationView())
        self.bot.add_view(MyMatchesView(self.db))
        self.bot.add_view(MentorView(self.db))
        self.bot.add_view(MentorListView())
        self.bot.add_view(DailyPollView())
        self.bot.add_view(ModWarningView())
        self.bot.add_view(ModTimeoutView())
        self.bot.add_view(CalendarView())
        self.bot.add_view(FAQView())
        self.bot.add_view(MMRProgressView(self.db))
        self.bot.add_view(InterestRolesView())

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

    # ---------- ежедневный квест дня ----------

    @tasks.loop(time=DAILY_QUEST_TIME_UTC)
    async def daily_quest_post(self):
        import hashlib
        today = discord.utils.utcnow().date().isoformat()
        idx = int(hashlib.md5(today.encode()).hexdigest(), 16) % len(DAILY_QUEST_QUESTIONS)
        q = DAILY_QUEST_QUESTIONS[idx]
        for guild in self.bot.guilds:
            ch = discord.utils.get(guild.text_channels, name=DAILY_QUEST_CHANNEL)
            if not ch:
                continue
            # удалить старый квест дня
            try:
                pins = await ch.pins()
                for p in pins:
                    if p.embeds and p.embeds[0].title == "🐲 Вопрос дня":
                        await p.delete()
            except discord.Forbidden:
                pass
            embed = discord.Embed(
                title="🐲 Вопрос дня",
                description=q["q"],
                color=0x8B4513)
            options_text = "\n".join(f"**{i+1}.** {opt}" for i, opt in enumerate(q["options"]))
            embed.add_field(name="Варианты:", value=options_text, inline=False)
            embed.set_footer(text="Нажмите кнопку, чтобы ответить!")
            try:
                msg = await ch.send(embed=embed, view=DailyQuestView())
                await msg.pin()
            except discord.HTTPException:
                pass

    @daily_quest_post.before_loop
    async def before_daily_quest(self):
        await self.bot.wait_until_ready()

    # ---------- напоминание о верификации (DM) ----------

    @tasks.loop(hours=VERIFY_REMINDER_INTERVAL_HOURS)
    async def verify_reminder(self):
        for guild in self.bot.guilds:
            unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE)
            if not unverified_role:
                continue
            verify_ch = discord.utils.get(guild.text_channels, name=VERIFICATION_CHANNEL)
            if not verify_ch:
                continue
            for member in unverified_role.members:
                if member.bot:
                    continue
                account_id = self.db.get_account_id(member.id)
                if account_id is not None:
                    continue
                try:
                    embed = discord.Embed(
                        title="🔐 Верификация",
                        description=(
                            "Добро пожаловать на сервер Dota 2!\n\n"
                            "Для получения доступа к каналам пройдите верификацию — "
                            "привяжите свой Steam-аккаунт.\n\n"
                            f"Перейдите в {verify_ch.mention} и нажмите кнопку."
                        ),
                        color=0x8B4513)
                    await member.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await asyncio.sleep(1)

    @verify_reminder.before_loop
    async def before_verify_reminder(self):
        await self.bot.wait_until_ready()

    # ---------- ежедневный опрос ----------

    @tasks.loop(time=DAILY_POLL_TIME_UTC)
    async def daily_poll_post(self):
        import hashlib
        today = discord.utils.utcnow().date().isoformat()
        idx = int(hashlib.md5(("poll_" + today).encode()).hexdigest(), 16) % len(DAILY_POLL_QUESTIONS)
        q = DAILY_POLL_QUESTIONS[idx]
        for guild in self.bot.guilds:
            ch = discord.utils.get(guild.text_channels, name=DAILY_POLL_CHANNEL)
            if not ch:
                continue
            # удалить старый опрос
            try:
                pins = await ch.pins()
                for p in pins:
                    if p.embeds and p.embeds[0].title == "🗳 Опрос дня":
                        await p.delete()
            except discord.Forbidden:
                pass
            embed = discord.Embed(
                title="🗳 Опрос дня",
                description=q["q"],
                color=0x8B4513)
            options_text = "\n".join(f"**{i+1}.** {opt}" for i, opt in enumerate(q["options"]))
            embed.add_field(name="Варианты:", value=options_text, inline=False)
            embed.set_footer(text="Нажмите кнопку, чтобы проголосовать!")
            try:
                msg = await ch.send(embed=embed, view=DailyPollView())
                await msg.pin()
            except discord.HTTPException:
                pass

    @daily_poll_post.before_loop
    async def before_daily_poll(self):
        await self.bot.wait_until_ready()

    # ---------- напоминание о турнире ----------

    @tasks.loop(minutes=15)
    async def tournament_reminder(self):
        from datetime import datetime as dt, timezone
        now = dt.now(timezone.utc)
        if not hasattr(self, '_reminded_matches'):
            self._reminded_matches: set[str] = set()
        for guild in self.bot.guilds:
            active = self.db.conn.execute(
                "SELECT id, name FROM tournaments WHERE guild_id=? AND status='active'",
                (guild.id,)).fetchall()
            for tid, tname in active:
                matches = self.db.conn.execute(
                    "SELECT player1_id, player2_id, status FROM tournament_matches "
                    "WHERE tournament_id=? AND status='pending'", (tid,)).fetchall()
                for p1, p2, _ in matches:
                    key = f"{tid}:{p1}:{p2}"
                    if key in self._reminded_matches:
                        continue
                    self._reminded_matches.add(key)
                    for pid in [p1, p2]:
                        if not pid:
                            continue
                        member = guild.get_member(pid)
                        if not member:
                            continue
                        try:
                            await member.send(
                                f"🏆 **Напоминание!** У вас матч в турнире «{tname}».\n"
                                f"Зайдите в канал турнира, чтобы начать.")
                        except (discord.Forbidden, discord.HTTPException):
                            pass

    @tournament_reminder.before_loop
    async def before_tournament_reminder(self):
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
            stats_ch = discord.utils.get(guild.text_channels, name="📊-статистика")
            if not stats_ch:
                continue
            pins = await stats_ch.pins()
            if not pins:
                continue
            stats_msg = pins[0]
            verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
            verified_count = len(verified_role.members) if verified_role else 0
            stats_embed = discord.Embed(title="📊 Статистика сервера", color=0x8B4513)
            stats_embed.add_field(name="👥 Участников", value=str(guild.member_count), inline=True)
            stats_embed.add_field(name="✅ Верифицировано", value=str(verified_count), inline=True)
            stats_embed.add_field(name="🎭 Ролей", value=str(len(guild.roles) - 1), inline=True)
            stats_embed.add_field(name="📝 Каналов", value=str(len(guild.channels)), inline=True)
            try:
                await stats_msg.edit(embed=stats_embed)
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
        # (кроме верификации и модерации — их не трогаем)
        for ch in guild.channels:
            if ch.id == verify_ch.id:
                continue
            if ch.category and ch.category.name == STAFF_CATEGORY:
                continue
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

        # ---- 🎮 Гости (видно неверифицированным, только голосовые) ----
        guest_category = discord.utils.get(guild.categories, name=GUEST_CATEGORY)
        if not guest_category:
            guest_category = await guild.create_category(GUEST_CATEGORY)
        await guest_category.set_permissions(everyone, view_channel=True)
        await guest_category.set_permissions(unverified, view_channel=True, connect=True)
        await guest_category.set_permissions(verified, view_channel=False)
        guest_ch = discord.utils.get(guild.text_channels, name=GUEST_CHANNEL)
        if not guest_ch:
            guest_ch = await guild.create_text_channel(GUEST_CHANNEL, category=guest_category)
        await guest_ch.set_permissions(everyone, send_messages=False)
        await guest_ch.set_permissions(unverified, send_messages=False)
        guest_pins = await guest_ch.pins()
        if not guest_pins:
            guest_embed = discord.Embed(
                title="🎮 Гостевая зона",
                description=(
                    "Добро пожаловать! Здесь вы можете создать временную голосовую комнату "
                    "для общения с друзьями.\n\n"
                    "Для полного доступа к серверу пройдите верификацию в "
                    "канале #🔐-ВЕРИФИКАЦИЯ."
                ),
                color=0x2B2D31)
            guest_msg = await guest_ch.send(embed=guest_embed, view=GuestVoiceView(self.db))
            try:
                await guest_msg.pin()
            except discord.Forbidden:
                pass

        # ---- ⚔️ Арена (общение, только верифицированные) ----
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
        # slowmode на активных каналах
        chat_ch = discord.utils.get(guild.text_channels, name="💬-чат")
        if chat_ch:
            await chat_ch.edit(slowmode_delay=CHAT_SLOWMODE_SECONDS)
        events_ch = discord.utils.get(guild.text_channels, name="🎉-ивенты")
        if events_ch:
            await events_ch.edit(slowmode_delay=EVENTS_SLOWMODE_SECONDS)

        # ---- 🎭 роли интересов (в приветствия) ----
        welcome_ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL)
        if welcome_ch:
            interest_embed = discord.Embed(
                title="🎭 Роли интересов",
                description=(
                    "Выберите роль, чтобы найти единомышленников!\n"
                    "• 🎯 Ищу тиму\n• 🎓 Коучусь\n• 📹 Делаю контент\n• 🏆 Турниры"
                ),
                color=0x8B4513)
            w_pins = await welcome_ch.pins()
            has_interest = any(
                e.title == "🎭 Роли интересов" for p in w_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_interest:
                interest_msg = await welcome_ch.send(
                    embed=interest_embed, view=InterestRolesView())
                try:
                    await interest_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 📊 Стратегия (лидерборд, статус, аналитика — read-only + кнопки) ----
        strategy_category = discord.utils.get(guild.categories, name=STRATEGY_CATEGORY)
        if not strategy_category:
            strategy_category = await guild.create_category(STRATEGY_CATEGORY)
        await strategy_category.set_permissions(everyone, view_channel=False)
        await strategy_category.set_permissions(verified, view_channel=True, send_messages=False)
        for ch_name in STRATEGY_TEXT_CHANNELS:
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if not ch:
                ch = await guild.create_text_channel(ch_name, category=strategy_category)
            elif ch.category != strategy_category:
                await ch.edit(category=strategy_category)
        # канал аналитики патчей — тоже в стратегии
        pa_ch = discord.utils.get(guild.text_channels, name=PATCH_ANALYTICS_CHANNEL)
        if not pa_ch:
            pa_ch = await guild.create_text_channel(PATCH_ANALYTICS_CHANNEL, category=strategy_category)
        elif pa_ch.category != strategy_category:
            await pa_ch.edit(category=strategy_category)

        # ---- 🎮 Игровое (ЛФГ, бестиарий — только верифицированные) ----
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

        # ---- 🛒 Магазин (read-only + кнопки) ----
        shop_category = discord.utils.get(guild.categories, name=SHOP_CATEGORY)
        if not shop_category:
            shop_category = await guild.create_category(SHOP_CATEGORY)
        await shop_category.set_permissions(everyone, view_channel=False)
        await shop_category.set_permissions(verified, view_channel=True, send_messages=False)
        shop_ch = discord.utils.get(guild.text_channels, name=SHOP_CHANNEL)
        if not shop_ch:
            shop_ch = await guild.create_text_channel(SHOP_CHANNEL, category=shop_category)
        elif shop_ch.category != shop_category:
            await shop_ch.edit(category=shop_category)

        # ---- удаление старых категорий (если остались) ----
        for old_name in ["🎮 Игровая", "💬 Таверна"]:
            old_cat = discord.utils.get(guild.categories, name=old_name)
            if old_cat:
                for ch in old_cat.channels:
                    try:
                        await ch.delete(reason=f"Старая категория {old_name}")
                    except discord.HTTPException:
                        pass
                try:
                    await old_cat.delete(reason=f"Старая категория {old_name}")
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

        # ---- 🎙 Голосовые комнаты (категория + канал создания) ----
        jtc_category = discord.utils.get(guild.categories, name=JOIN_TO_CREATE_CATEGORY)
        if not jtc_category:
            jtc_category = await guild.create_category(JOIN_TO_CREATE_CATEGORY)
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
                "**Как работает:**\n"
                "1. Выберите режим и ранг\n"
                "2. Нажмите «Создать»\n"
                "3. Комната появится в вашей ранговой категории\n\n"
                "Комната удалится автоматически, когда все выйдут."
            ),
            color=0x2B2D31)
        if not vr_pinned:
            vr_msg = await vr_create.send(embed=vr_embed, view=VoiceRoomCreateView(self.db))
            try:
                await vr_msg.pin()
            except discord.Forbidden:
                pass

        # ---- ⚡ быстрый матч ----
        qm_embed = discord.Embed(
            title="⚡ Быстрый матч",
            description=(
                "Нажмите кнопку, чтобы найти тиму!\n\n"
                "Когда соберётся 5 человек — бот создаст войс-комнату\n"
                "и перенесёт всех туда."
            ),
            color=0x8B4513)
        qm_pins = await vr_create.pins()
        has_qm = any(
            e.title == "⚡ Быстрый матч" for p in qm_pins if p.embeds
            for e in [p.embeds[0]] if hasattr(e, 'title'))
        if not has_qm:
            qm_msg = await vr_create.send(embed=qm_embed, view=QuickMatchView(self.db))
            try:
                await qm_msg.pin()
            except discord.Forbidden:
                pass

        # ---- чаты для общения ----
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

        # ---- 📊 Статистика сервера (автообновляемый embed) ----
        stats_category = discord.utils.get(guild.categories, name=STATS_CATEGORY)
        if not stats_category:
            stats_category = await guild.create_category(STATS_CATEGORY)
        await stats_category.set_permissions(everyone, view_channel=True, send_messages=False)
        stats_ch = discord.utils.get(guild.text_channels, name="📊-статистика")
        if not stats_ch:
            stats_ch = await guild.create_text_channel("📊-статистика", category=stats_category)
        verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
        verified_count = len(verified_role.members) if verified_role else 0
        stats_embed = discord.Embed(title="📊 Статистика сервера", color=0x8B4513)
        stats_embed.add_field(name="👥 Участников", value=str(guild.member_count), inline=True)
        stats_embed.add_field(name="✅ Верифицировано", value=str(verified_count), inline=True)
        stats_embed.add_field(name="🎭 Ролей", value=str(len(guild.roles) - 1), inline=True)
        stats_embed.add_field(name="📝 Каналов", value=str(len(guild.channels)), inline=True)
        stats_msg = await stats_ch.send(embed=stats_embed)
        try:
            await stats_msg.pin()
        except discord.Forbidden:
            pass

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

        # ---- 📊 мои матчи (в лидерборд) ----
        lb_ch = discord.utils.get(guild.text_channels, name=LEADERBOARD_CHANNEL)
        if lb_ch:
            mm_embed = discord.Embed(
                title="📊 Мои матчи",
                description="Нажмите, чтобы увидеть последние 5 матчей.",
                color=0x8B4513)
            lb_pins = await lb_ch.pins()
            has_mm = any(
                e.title == "📊 Мои матчи" for p in lb_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_mm:
                mm_msg = await lb_ch.send(embed=mm_embed, view=MyMatchesView(self.db))
                try:
                    await mm_msg.pin()
                except discord.Forbidden:
                    pass

            # ---- 📈 прогресс MMR (в лидерборд) ----
            mmr_embed = discord.Embed(
                title="📈 Мой прогресс",
                description="Нажмите, чтобы увидеть ваш статистику и прогресс.",
                color=0x8B4513)
            has_mmr = any(
                e.title == "📈 Мой прогресс" for p in lb_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_mmr:
                mmr_msg = await lb_ch.send(embed=mmr_embed, view=MMRProgressView(self.db))
                try:
                    await mmr_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 🤝 наставники (в правилах) ----
        rules_ch = discord.utils.get(guild.text_channels, name="📜-правила")
        if rules_ch:
            mentor_embed = discord.Embed(
                title="🤝 Наставники",
                description=(
                    "Опытные игроки могут стать наставниками.\n"
                    "Новички — нажмите кнопку, чтобы увидеть список наставников."
                ),
                color=0x8B4513)
            rules_pins = await rules_ch.pins()
            has_mentor = any(
                e.title == "🤝 Наставники" for p in rules_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_mentor:
                mentor_view = discord.ui.View()
                mentor_view.add_item(discord.ui.Button(
                    label="Стать наставником", emoji="🤝",
                    style=discord.ButtonStyle.primary, custom_id="mentor:toggle"))
                mentor_view.add_item(discord.ui.Button(
                    label="Список наставников", emoji="📋",
                    style=discord.ButtonStyle.secondary, custom_id="mentor:list"))
                mentor_msg = await rules_ch.send(embed=mentor_embed, view=mentor_view)
                try:
                    await mentor_msg.pin()
                except discord.Forbidden:
                    pass

            # ---- ❓ FAQ (в правилах) ----
            faq_embed = discord.Embed(
                title="❓ Частые вопросы",
                description="Нажмите кнопку, чтобы увидеть ответы на частые вопросы.",
                color=0x8B4513)
            has_faq = any(
                e.title == "❓ Частые вопросы" for p in rules_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_faq:
                faq_msg = await rules_ch.send(embed=faq_embed, view=FAQView())
                try:
                    await faq_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 📅 календарь (в ивенты) ----
        ev_ch = discord.utils.get(guild.text_channels, name=STREAMS_CHANNEL)
        if ev_ch:
            cal_embed = discord.Embed(
                title="📅 Ближайшие события",
                description="Нажмите кнопку, чтобы увидеть календарь сервера.",
                color=0x8B4513)
            ev_pins = await ev_ch.pins()
            has_cal = any(
                e.title == "📅 Ближайшие события" for p in ev_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_cal:
                cal_msg = await ev_ch.send(embed=cal_embed, view=CalendarView())
                try:
                    await cal_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- ⚠️ модерация кнопки (staff-only канал) ----
        mod_tools_ch = discord.utils.get(guild.text_channels, name="🛠-инструменты")
        if not mod_tools_ch:
            mod_tools_ch = await guild.create_text_channel("🛠-инструменты", category=staff_category)
        if staff_role:
            await mod_tools_ch.set_permissions(staff_role, view_channel=True, send_messages=True)
        await mod_tools_ch.set_permissions(everyone, view_channel=False)
        mod_embed = discord.Embed(
            title="🛠 Панель модерации",
            description="Используйте кнопки ниже для предупреждений и таймаутов.",
            color=0xFF0000)
        mod_pins = await mod_tools_ch.pins()
        has_mod_btn = any(
            e.title == "🛠 Панель модерации" for p in mod_pins if p.embeds
            for e in [p.embeds[0]] if hasattr(e, 'title'))
        if not has_mod_btn:
            mod_view = discord.ui.View()
            mod_view.add_item(discord.ui.Button(
                label="Предупредить", emoji="⚠️",
                style=discord.ButtonStyle.danger, custom_id="mod:warning"))
            mod_view.add_item(discord.ui.Button(
                label="Таймаут", emoji="🔇",
                style=discord.ButtonStyle.danger, custom_id="mod:timeout"))
            mod_msg = await mod_tools_ch.send(embed=mod_embed, view=mod_view)
            try:
                await mod_msg.pin()
            except discord.Forbidden:
                pass

        # ---- 📋 навигация (в канал правил) ----
        nav_ch = discord.utils.get(guild.text_channels, name=NAVIGATION_CHANNEL)
        if nav_ch:
            nav_embed = discord.Embed(
                title="📋 Где что находится?",
                description="Нажмите кнопку, чтобы увидеть полную карту сервера.",
                color=0x8B4513)
            nav_pins = await nav_ch.pins()
            has_nav = any(
                e.title == "📋 Где что находится?" for p in nav_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_nav:
                nav_msg = await nav_ch.send(embed=nav_embed, view=NavigationView())
                try:
                    await nav_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 📺 стримы (в ивенты) ----
        events_ch = discord.utils.get(guild.text_channels, name=STREAMS_CHANNEL)
        if events_ch:
            stream_embed = discord.Embed(
                title="📺 Стримы сервера",
                description=(
                    "Начали стрим? Нажмите кнопку — получите роль «📺 Стример» "
                    "и вас увидят в этом канале!"
                ),
                color=0x8B4513)
            ev_pins = await events_ch.pins()
            has_stream = any(
                e.title == "📺 Стримы сервера" for p in ev_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_stream:
                stream_msg = await events_ch.send(embed=stream_embed, view=StreamButtonView())
                try:
                    await stream_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 🐲 квест дня (в ивенты) ----
        if events_ch:
            import hashlib
            today = discord.utils.utcnow().date().isoformat()
            idx = int(hashlib.md5(today.encode()).hexdigest(), 16) % len(DAILY_QUEST_QUESTIONS)
            q = DAILY_QUEST_QUESTIONS[idx]
            quest_embed = discord.Embed(
                title="🐲 Вопрос дня",
                description=q["q"],
                color=0x8B4513)
            options_text = "\n".join(f"**{i+1}.** {opt}" for i, opt in enumerate(q["options"]))
            quest_embed.add_field(name="Варианты:", value=options_text, inline=False)
            quest_embed.set_footer(text="Нажмите кнопку, чтобы ответить!")
            ev_pins = await events_ch.pins()
            has_quest = any(
                e.title == "🐲 Вопрос дня" for p in ev_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_quest:
                quest_msg = await events_ch.send(embed=quest_embed, view=DailyQuestView())
                try:
                    await quest_msg.pin()
                except discord.Forbidden:
                    pass

        # ---- 🗳 опрос дня (в ивенты) ----
        if events_ch:
            import hashlib
            today = discord.utils.utcnow().date().isoformat()
            pidx = int(hashlib.md5(("poll_" + today).encode()).hexdigest(), 16) % len(DAILY_POLL_QUESTIONS)
            pq = DAILY_POLL_QUESTIONS[pidx]
            poll_embed = discord.Embed(
                title="🗳 Опрос дня",
                description=pq["q"],
                color=0x8B4513)
            poll_options = "\n".join(f"**{i+1}.** {opt}" for i, opt in enumerate(pq["options"]))
            poll_embed.add_field(name="Варианты:", value=poll_options, inline=False)
            poll_embed.set_footer(text="Нажмите кнопку, чтобы проголосовать!")
            ev_pins = await events_ch.pins()
            has_poll = any(
                e.title == "🗳 Опрос дня" for p in ev_pins if p.embeds
                for e in [p.embeds[0]] if hasattr(e, 'title'))
            if not has_poll:
                poll_msg = await events_ch.send(embed=poll_embed, view=DailyPollView())
                try:
                    await poll_msg.pin()
                except discord.Forbidden:
                    pass

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

        # ---- удаление каналов, не относящихся к боту ----
        BOT_CATEGORY_NAMES = {
            INFO_CATEGORY, COMMUNITY_CATEGORY, STRATEGY_CATEGORY, GAME_CATEGORY,
            SHOP_CATEGORY, JOIN_TO_CREATE_CATEGORY, STATS_CATEGORY, STAFF_CATEGORY,
            GUEST_CATEGORY,
        }
        deleted_count = 0

        # удаление старых голосовых каналов-счётчиков (👥 Участников / ✅ Верифицировано)
        for ch in list(guild.voice_channels):
            if ch.name.startswith("👥 Участников") or ch.name.startswith("✅ Верифицировано"):
                try:
                    await ch.delete(reason="Старый канал-счётчик")
                    deleted_count += 1
                except discord.HTTPException:
                    pass

        # удаление голосовых каналов в категории Статистика (там должен быть только текстовый)
        stats_cat = discord.utils.get(guild.categories, name=STATS_CATEGORY)
        if stats_cat:
            for ch in list(stats_cat.voice_channels):
                try:
                    await ch.delete(reason="Старый голосовой в Статистике")
                    deleted_count += 1
                except discord.HTTPException:
                    pass

        for ch in list(guild.channels):
            if ch.category and ch.category.name in BOT_CATEGORY_NAMES:
                continue
            if ch.category and ch.category.name == STAFF_CATEGORY:
                continue
            if ch.id == verify_ch.id:
                continue
            if ch.type == discord.ChannelType.category:
                if ch.name not in BOT_CATEGORY_NAMES and ch.name != STAFF_CATEGORY:
                    for sub_ch in ch.channels:
                        try:
                            await sub_ch.delete(reason="Старый канал, не относится к боту")
                            deleted_count += 1
                        except discord.HTTPException:
                            pass
                    try:
                        await ch.delete(reason="Старая категория, не относится к боту")
                        deleted_count += 1
                    except discord.HTTPException:
                        pass
            elif not ch.category:
                try:
                    await ch.delete(reason="Старый канал без категории")
                    deleted_count += 1
                except discord.HTTPException:
                    pass

        await ctx.send(
            f"Готово! Настроены: Начало, Арена, Стратегия, Игровое, Магазин, "
            f"Гости, Голосовые комнаты, Статистика, Модерация, верификация.\n"
            f"Удалено лишних каналов: {deleted_count}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerManagement(bot))
    await bot.add_cog(ToxicityAlertListener(bot))
