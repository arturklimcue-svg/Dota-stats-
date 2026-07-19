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
  - категории "Herald–Crusader" / "Archon–Legend" / "Ancient–Immortal"
    с текстовым + голосовым каналом внутри, видимые только
    соответствующим ранговым ролям
  - категорию "Voice Rooms" с join-to-create каналом
  - закроет все остальные существующие каналы от @everyone

ВАЖНО: закрытие каналов от @everyone необратимо переписывает права —
запускайте команду на чистом/тестовом сервере или будьте готовы,
что существующие permission overwrites на каналах будут заменены.
"""

import asyncio
from datetime import timedelta
from pathlib import Path

import discord
from discord.ext import commands, tasks

from dota_stats_v3 import od, to_account_id, to_steam64, Storage, DB_PATH

# ---------------- конфиг ----------------

UNVERIFIED_ROLE = "Unverified"
VERIFIED_ROLE = "Verified"
UNRANKED_ROLE = "Unranked"
VERIFICATION_CHANNEL = "🔐-verification"
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

COMMUNITY_CATEGORY = "💬 Таверна"
COMMUNITY_TEXT_CHANNELS = ["💬-общий-чат", "🖼-скриншоты", "🎬-клипы-и-фейлы", "😂-мемы", "🎉-ивенты"]

GAME_CATEGORY = "🎮 Игровая"
LFG_CHANNEL = "🔍-лфг"
GAME_TEXT_CHANNELS = [LFG_CHANNEL, "🛒-трейд-предметов", "🧠-советы-и-стратегии", "🐲-бестиарий-героев"]

RANK_VOICE_NAMES = ["🔊 Radiant", "🔊 Dire"]  # по 2 голосовых в каждой ранговой категории

JOIN_TO_CREATE_CHANNEL = "➕ Создать войс"
JOIN_TO_CREATE_CATEGORY = "🎙 Голосовые комнаты"
JOIN_TO_CREATE_USER_LIMIT = 5

STAFF_CATEGORY = "🛠 Модерация"
STAFF_ROLE_NAME = "Moderator"

PARTY_THREAD_ARCHIVE_MINUTES = 60

AUTO_PURGE_CHANNELS = {"💬-общий-чат": 6, LFG_CHANNEL: 2}  # имя канала -> часов хранения истории
PURGE_INTERVAL_MINUTES = 30
LFG_SLOWMODE_SECONDS = 15

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
        "Команда `!party <описание>` создаёт отдельный тред под ваш сбор группы вместо флуда "
        "сообщениями в общем канале. Тред сам архивируется через час без активности — "
        "не нужно ничего чистить руками."
    ),
    "🧠-советы-и-стратегии": (
        "🧠 Советы и стратегии",
        "Нажмите «⚔️ Мой live-матч» на панели статистики — бот покажет матчапы против героев "
        "вашей текущей игры, рекомендованные предметы по фазам и краткую стратегию."
    ),
    "🛒-трейд-предметов": (
        "🛒 Трейд предметов",
        "Обмен/продажа скинов и предметов Steam-инвентаря между участниками сервера. "
        "Сделки — на ваш страх и риск, сервер не гарант."
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


async def post_pinned_info(channel: discord.TextChannel, title: str, description: str):
    """Шлёт и закрепляет embed-справку в канале, если там ещё нет закрепа
    (чтобы повторный запуск !dota_server_setup не плодил дубликаты)."""
    try:
        pins = await channel.pins()
    except discord.Forbidden:
        return
    if pins:
        return
    embed = discord.Embed(title=title, description=description, color=0x8B4513)
    msg = await channel.send(embed=embed)
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

        member = interaction.user
        guild = interaction.guild
        unverified = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE)
        verified = await get_or_create_role(guild, VERIFIED_ROLE)

        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason="Верификация пройдена")
        await member.add_roles(verified, reason="Верификация пройдена")

        rank_role_name = await assign_rank_role(member, account_id)

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


class VerificationView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Привязать SteamID и получить доступ", emoji="🔗",
                        style=discord.ButtonStyle.success, custom_id="verify:start")
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal(self.db))


# ---------------- cog ----------------

class ServerManagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = Storage(DB_PATH)  # общая база с dota_stats_v3 — те же привязанные SteamID
        self.temp_voice_channels: set[int] = set()
        self.resync_ranks.start()
        self.auto_purge.start()

    def cog_unload(self):
        self.resync_ranks.cancel()
        self.auto_purge.cancel()

    async def cog_load(self):
        self.bot.add_view(VerificationView(self.db))

    # ---------- вход нового участника ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        role = discord.utils.get(member.guild.roles, name=UNVERIFIED_ROLE)
        if role:
            await member.add_roles(role, reason="Новый участник — требуется верификация")

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
        # создание временного канала при заходе в "хаб"
        if after.channel and after.channel.name == JOIN_TO_CREATE_CHANNEL:
            category = after.channel.category
            temp = await member.guild.create_voice_channel(
                name=f"{member.display_name}'s Lobby",
                category=category,
                user_limit=JOIN_TO_CREATE_USER_LIMIT,
                reason="Join-to-create")
            await member.move_to(temp)
            self.temp_voice_channels.add(temp.id)

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

    @auto_purge.before_loop
    async def before_purge(self):
        await self.bot.wait_until_ready()

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

        # категории по группам рангов
        for group_name, tier_names in RANK_GROUPS.items():
            category = discord.utils.get(guild.categories, name=group_name)
            if not category:
                category = await guild.create_category(group_name)
            overwrites = {everyone: discord.PermissionOverwrite(view_channel=False)}
            for tn in tier_names:
                overwrites[rank_roles[tn]] = discord.PermissionOverwrite(view_channel=True)
            await category.edit(overwrites=overwrites)
            if not category.text_channels:
                await guild.create_text_channel("чат", category=category)
            if not category.voice_channels:
                await guild.create_voice_channel("голосовой", category=category)

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

        # ---- 🎮 Игровая (ЛФГ, трейд, гайды — только верифицированные) ----
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

        # ---- join-to-create ----
        jtc_category = discord.utils.get(guild.categories, name=JOIN_TO_CREATE_CATEGORY)
        if not jtc_category:
            jtc_category = await guild.create_category(JOIN_TO_CREATE_CATEGORY)
        if not discord.utils.get(guild.voice_channels, name=JOIN_TO_CREATE_CHANNEL):
            await guild.create_voice_channel(JOIN_TO_CREATE_CHANNEL, category=jtc_category)
        await jtc_category.set_permissions(everyone, view_channel=False)
        await jtc_category.set_permissions(verified, view_channel=True, connect=True)

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
                if not discord.utils.get(category.voice_channels, name=vc_name):
                    await guild.create_voice_channel(vc_name, category=category)

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

        # ---- закреплённые справки ----
        for ch_name, (title, text) in PINNED_INFO.items():
            ch = discord.utils.get(guild.text_channels, name=ch_name)
            if ch:
                await post_pinned_info(ch, title, text)

        # ---- кастомные стикеры (если положены в stickers/) ----
        await upload_custom_stickers(guild)

        # ---- сообщение с кнопкой верификации ----
        embed = discord.Embed(
            title="🔐 Верификация",
            description="Привяжите SteamID, чтобы получить доступ к серверу и роль по рангу.",
            color=0x8B4513)
        await verify_ch.send(embed=embed, view=VerificationView(self.db))

        await ctx.send(
            "Готово: тематические категории, ранговые каналы, закреплённые справки, "
            "join-to-create и verification настроены.")


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerManagement(bot))
