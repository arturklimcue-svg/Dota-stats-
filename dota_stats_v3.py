"""
Dota статистика v3 — без слэш-команд, всё через кнопки + модалки.
Live-контры работают для ЛЮБОГО ранга через официальный Steam Web API
(без конфига на компьютере игрока, без ручного выбора героев).

НОВОЕ в этой версии:
  - Кнопка "Мой live-матч" теперь в ОДНОМ ответе показывает:
      1) винрейт вашего героя против каждого вражеского (OpenDota, тысячи матчей)
      2) рекомендованные предметы под вашего героя по фазам игры (OpenDota)
      3) короткую текстовую стратегию, которую генерирует LLM (Claude, Anthropic API)
         СТРОГО на основе пунктов 1 и 2 — модель не выдумывает статистику,
         а интерпретирует уже реальные цифры из OpenDota.
  - Результат LLM кэшируется на LLM_STRATEGY_CACHE_TTL_SECONDS для одной и той же
    комбинации (мой герой + вражеские герои), чтобы не тратить лишние токены/деньги
    при повторных нажатиях кнопки разными игроками в одном матче.

Один раз ставите панель командой !dota_setup в нужном канале —
дальше игроки жмут кнопки, всё индивидуально (ephemeral-ответы,
видны только нажавшему).

------------------------------------------------------------------
КАК РАБОТАЕТ АВТООПРЕДЕЛЕНИЕ LIVE-МАТЧА (без конфига, для любого MMR):

  1. Раз в STEAM_POLL_INTERVAL_SECONDS бот пачкой спрашивает Steam Web API
     (ISteamUser/GetPlayerSummaries) про всех привязанных игроков:
     "запущена ли Dota 2 и есть ли gameserversteamid" (это официальный,
     публичный признак "игрок сейчас на игровом сервере").
  2. Если сервер найден — бот запрашивает IDOTA2MatchStats_570/GetRealtimeStats
     по этому server_steam_id. Это ДРУГОЙ метод, чем OpenDota /live — он
     отдаёт данные по КОНКРЕТНОМУ серверу, а не только по топовым
     транслируемым матчам, поэтому работает независимо от MMR.
  3. Из ответа достаются герои обеих команд, бот считает винрейты
     матчапов вашего героя против каждого вражеского через OpenDota.

  Ограничения, о которых стоит знать:
  - Нужен собственный бесплатный ключ Steam Web API:
    https://steamcommunity.com/dev/apikey
  - Обновление раз в ~45 секунд, а не мгновенно (не push, а поллинг).
  - Сработает только если приватность профиля Steam у игрока не скрывает
    статус "сейчас в игре" (по умолчанию у большинства открыто).
  - Точные названия полей в ответе GetRealtimeStats могут отличаться —
    включите STEAM_DEBUG_LOG=True и посмотрите реальный payload в консоли
    перед боевым использованием, если контры не будут находиться.
------------------------------------------------------------------
LLM-СТРАТЕГИЯ — ЧТО ЭТО И ЧЕГО ЭТО НЕ ДЕЛАЕТ:

  - Модель НЕ ищет данные в интернете, не читает Dotabuff/D2PT и т.п.
  - Ей на вход подаётся ТОЛЬКО то, что бот уже сам посчитал из OpenDota:
    список вражеских героев с вашим винрейтом против них (тысячи матчей
    в базе OpenDota) и топ предметов под вашего героя по фазам игры.
  - Задача модели — просто связно и коротко пересказать эти цифры в виде
    тактических советов, а не придумать новую статистику.
  - Используется бесплатный Google Gemini API (REST, через уже подключённый
    aiohttp — отдельный SDK не нужен). Если GEMINI_API_KEY не задан или
    запрос упал — бот просто покажет embed без секции стратегии, ничего
    не сломается.
------------------------------------------------------------------

Установка:
  pip install aiohttp discord.py
  Впишите STEAM_API_KEY и GEMINI_API_KEY ниже.
  await bot.load_extension("dota_stats_v3")
"""

import asyncio
import sqlite3
import time
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks

OPENDOTA_BASE = "https://api.opendota.com/api"
STEAM_API_BASE = "https://api.steampowered.com"
STEAM64_OFFSET = 76561197960265728
DB_PATH = Path(__file__).parent / "dota_stats.db"

# --- настройте под себя ---
STEAM_API_KEY ="C5BD806939B9711D9722489FB77DF417"        # https://steamcommunity.com/dev/apikey
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"          # https://aistudio.google.com/apikey (бесплатно)
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
ENABLE_LLM_STRATEGY = True                       # выключите, если не нужна текстовая стратегия
STEAM_POLL_INTERVAL_SECONDS = 45
STEAM_DEBUG_LOG = True  # печатать сырые ответы Steam API в консоль для сверки схемы
LIVE_STATE_TTL_SECONDS = 300  # сколько считаем данные о матче ещё актуальными
LLM_STRATEGY_CACHE_TTL_SECONDS = 1800  # чтобы не пересчитывать стратегию на каждое нажатие


def to_account_id(steam_id) -> int:
    steam_id = int(steam_id)
    return steam_id - STEAM64_OFFSET if steam_id > STEAM64_OFFSET else steam_id


def to_steam64(steam_id) -> int:
    steam_id = int(steam_id)
    return steam_id if steam_id > STEAM64_OFFSET else steam_id + STEAM64_OFFSET


# ---------------- storage ----------------

class Storage:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS players (
            discord_id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            steam_id64 INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS dashboard (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER
        )""")
        c.commit()

    def register(self, discord_id: int, account_id: int, steam_id64: int):
        self.conn.execute(
            "INSERT INTO players (discord_id, account_id, steam_id64) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_id) DO UPDATE SET account_id=excluded.account_id, "
            "steam_id64=excluded.steam_id64",
            (discord_id, account_id, steam_id64))
        self.conn.commit()

    def get_account_id(self, discord_id: int):
        row = self.conn.execute(
            "SELECT account_id FROM players WHERE discord_id=?", (discord_id,)).fetchone()
        return row[0] if row else None

    def all_players(self):
        """-> [(discord_id, account_id, steam_id64), ...]"""
        return self.conn.execute(
            "SELECT discord_id, account_id, steam_id64 FROM players").fetchall()

    def set_dashboard(self, guild_id: int, channel_id: int, message_id: int):
        self.conn.execute(
            "INSERT INTO dashboard (guild_id, channel_id, message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, message_id=excluded.message_id",
            (guild_id, channel_id, message_id))
        self.conn.commit()

    def all_dashboards(self):
        return self.conn.execute("SELECT guild_id, channel_id, message_id FROM dashboard").fetchall()


# ---------------- OpenDota client (профиль, мета, предметы, матчапы) ----------------

class OpenDota:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self.heroes_cache: dict[int, str] = {}
        self.hero_name_to_id: dict[str, int] = {}
        self.items_cache: dict[int, str] = {}
        self.item_trend_cache: tuple[float, list] = (0.0, [])

    async def _s(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get(self, path: str):
        s = await self._s()
        async with s.get(f"{OPENDOTA_BASE}{path}") as r:
            return await r.json() if r.status == 200 else None

    async def ensure_heroes(self):
        if self.heroes_cache:
            return
        heroes = await self.get("/heroes")
        if heroes:
            self.heroes_cache = {h["id"]: h["localized_name"] for h in heroes}
            self.hero_name_to_id = {h["localized_name"].lower(): h["id"] for h in heroes}

    async def hero_name(self, hero_id: int) -> str:
        await self.ensure_heroes()
        return self.heroes_cache.get(hero_id, f"Hero#{hero_id}")

    async def find_hero_id(self, name: str):
        await self.ensure_heroes()
        name = name.lower().strip()
        if name in self.hero_name_to_id:
            return self.hero_name_to_id[name]
        for n, hid in self.hero_name_to_id.items():
            if name in n:
                return hid
        return None

    async def ensure_items(self):
        if self.items_cache:
            return
        items = await self.get("/constants/items")
        if items:
            for v in items.values():
                if "id" in v:
                    self.items_cache[v["id"]] = v.get("dname", f"item_{v['id']}")

    async def item_name(self, item_id: int) -> str:
        await self.ensure_items()
        return self.items_cache.get(item_id, f"item_{item_id}")

    async def matchups(self, hero_id: int):
        return await self.get(f"/heroes/{hero_id}/matchups") or []

    async def item_popularity(self, hero_id: int):
        return await self.get(f"/heroes/{hero_id}/itemPopularity")

    async def hero_stats(self):
        return await self.get("/heroStats") or []

    async def item_trend(self):
        """Приблизительный общий тренд предметов: агрегация itemPopularity
        по топ-15 самым пикаемым героям. Это ОЦЕНКА, не точная глобальная
        статистика (OpenDota не отдаёт готовый global item winrate)."""
        cached_at, data = self.item_trend_cache
        if time.time() - cached_at < 3600 and data:
            return data

        stats = await self.hero_stats()

        def picks(h):
            return sum(h.get(f"{i}_pick", 0) for i in range(1, 9))

        top_heroes = sorted(stats, key=picks, reverse=True)[:15]

        totals: dict[int, int] = {}
        for h in top_heroes:
            pop = await self.item_popularity(h["id"])
            if not pop:
                continue
            for phase in pop.values():
                for item_id_str, count in phase.items():
                    iid = int(item_id_str)
                    totals[iid] = totals.get(iid, 0) + count

        ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]
        result = [(await self.item_name(iid), count) for iid, count in ranked]
        self.item_trend_cache = (time.time(), result)
        return result

    PHASE_TITLES = {
        "start_game_items": "Старт", "early_game_items": "Ранняя игра",
        "mid_game_items": "Мид-гейм", "late_game_items": "Late-гейм",
    }

    async def item_recommendations(self, hero_id: int, per_phase: int = 5) -> dict[str, list[str]]:
        """-> {"Старт": ["Tango (1234)", ...], "Ранняя игра": [...], ...}
        Те же данные, что и в кнопке 'Предметы под героя', вынесены в
        переиспользуемую функцию, чтобы не дублировать логику."""
        pop = await self.item_popularity(hero_id)
        result: dict[str, list[str]] = {}
        if not pop:
            return result
        for key, title in self.PHASE_TITLES.items():
            phase = pop.get(key, {})
            top = sorted(phase.items(), key=lambda x: x[1], reverse=True)[:per_phase]
            if not top:
                continue
            result[title] = [f"{await self.item_name(int(iid))} ({cnt})" for iid, cnt in top]
        return result


od = OpenDota()


# ---------------- Steam Web API client (детект live-матча, любой MMR) ----------------

class SteamAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session: aiohttp.ClientSession | None = None

    async def _s(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_player_summaries(self, steam_ids64: list[int]) -> list[dict]:
        if not steam_ids64:
            return []
        s = await self._s()
        url = f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v0002/"
        params = {"key": self.api_key, "steamids": ",".join(str(i) for i in steam_ids64)}
        async with s.get(url, params=params) as r:
            if r.status != 200:
                return []
            data = await r.json()
            return data.get("response", {}).get("players", [])

    async def get_realtime_stats(self, server_steam_id: str):
        s = await self._s()
        url = f"{STEAM_API_BASE}/IDOTA2MatchStats_570/GetRealtimeStats/v1/"
        params = {"server_steam_id": server_steam_id}
        async with s.get(url, params=params) as r:
            if r.status != 200:
                return None
            return await r.json()


steam_client = SteamAPI(STEAM_API_KEY)


# ---------------- LLM-стратегия (Anthropic API) поверх реальных данных OpenDota ----------------

class StrategyWriter:
    """Генерирует короткий текстовый разбор ТОЛЬКО на основе уже посчитанных
    ботом цифр (матчапы + предметы). Модель ничего не ищет и не выдумывает —
    она получает готовые факты и просто формулирует их связно.
    Использует бесплатный Google Gemini API (REST, через aiohttp — без
    дополнительного SDK)."""

    def __init__(self, api_key: str, model: str):
        self.enabled = ENABLE_LLM_STRATEGY and bool(api_key) and "YOUR_" not in api_key
        self.model = model
        self.api_key = api_key
        self.session: aiohttp.ClientSession | None = None
        self.cache: dict[tuple, tuple[float, str]] = {}

    async def _s(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    @staticmethod
    def _cache_key(my_hero_id: int, enemy_hero_ids: list[int]) -> tuple:
        return (my_hero_id, tuple(sorted(enemy_hero_ids)))

    async def write(self, my_hero_name: str, matchup_rows: list[tuple[str, float, int]],
                     item_recs: dict[str, list[str]], my_hero_id: int, enemy_hero_ids: list[int]) -> str | None:
        if not self.enabled:
            return None

        key = self._cache_key(my_hero_id, enemy_hero_ids)
        cached = self.cache.get(key)
        if cached and time.time() - cached[0] < LLM_STRATEGY_CACHE_TTL_SECONDS:
            return cached[1]

        matchup_lines = "\n".join(
            f"- против {name}: винрейт {wr:.1f}% (выборка {games} матчей, база OpenDota)"
            for name, wr, games in matchup_rows
        ) or "нет данных по матчапам"

        item_lines = "\n".join(
            f"{phase}: {', '.join(items)}" for phase, items in item_recs.items()
        ) or "нет данных по предметам"

        prompt = (
            f"Ты — помощник по Dota 2. Вот реальные статистические данные из OpenDota "
            f"(агрегированы по тысячам матчей), НЕ придумывай ничего сверх них.\n\n"
            f"Мой герой: {my_hero_name}\n\n"
            f"Винрейт против героев противника (по всей базе OpenDota):\n{matchup_lines}\n\n"
            f"Популярные предметы под {my_hero_name} по фазам игры (по базе OpenDota):\n{item_lines}\n\n"
            f"Напиши на русском 4-6 коротких предложений тактического совета: "
            f"на каких вражеских героев обратить особое внимание (с самым низким винрейтом), "
            f"и как это связать с порядком сборки предметов. Никаких вымышленных фактов, "
            f"только интерпретация приведённых цифр."
        )

        url = f"{GEMINI_API_BASE}/models/{self.model}:generateContent"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        try:
            s = await self._s()
            async with s.post(url, params={"key": self.api_key}, json=payload,
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    if STEAM_DEBUG_LOG:
                        print(f"[LLM] Gemini вернул статус {r.status}: {await r.text()}")
                    return None
                data = await r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
        except Exception as e:
            if STEAM_DEBUG_LOG:
                print(f"[LLM] ошибка генерации стратегии: {e}")
            return None

        if text:
            self.cache[key] = (time.time(), text)
        return text or None


strategy_writer = StrategyWriter(GEMINI_API_KEY, GEMINI_MODEL)


# ---------------- Modals ----------------

class RegisterModal(discord.ui.Modal, title="Привязать SteamID"):
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
                "Не нашёл такой SteamID в OpenDota. Убедитесь, что профиль публичный.", ephemeral=True)
            return
        self.db.register(interaction.user.id, account_id, steam_id64)
        name = profile["profile"].get("personaname", "игрок")
        await interaction.response.send_message(
            f"Привязал вас к **{name}**. Live-контры заработают автоматически, "
            f"как только начнётся матч (обновление раз в ~{STEAM_POLL_INTERVAL_SECONDS} сек).",
            ephemeral=True)


class HeroPickModal(discord.ui.Modal, title="Предметы под героя"):
    hero = discord.ui.TextInput(label="Имя героя (можно частично)", placeholder="напр. Pudge")

    async def on_submit(self, interaction: discord.Interaction):
        hero_id = await od.find_hero_id(str(self.hero.value))
        if not hero_id:
            await interaction.response.send_message("Герой не найден.", ephemeral=True)
            return
        recs = await od.item_recommendations(hero_id)
        if not recs:
            await interaction.response.send_message("Нет данных по предметам.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Предметы — {await od.hero_name(hero_id)}", color=0x8B4513)
        for title, lines in recs.items():
            embed.add_field(name=title, value="\n".join(lines), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- persistent dashboard view ----------------

class DashboardView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Привязать SteamID", emoji="🔗", style=discord.ButtonStyle.primary, custom_id="dota:register")
    async def register_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RegisterModal(self.db))

    @discord.ui.button(label="Мой профиль", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="dota:profile")
    async def profile_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        account_id = self.db.get_account_id(interaction.user.id)
        if not account_id:
            await interaction.response.send_message("Сначала привяжите SteamID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        profile, wl, recent = await asyncio.gather(
            od.get(f"/players/{account_id}"),
            od.get(f"/players/{account_id}/wl"),
            od.get(f"/players/{account_id}/recentMatches"),
        )
        p = (profile or {}).get("profile", {})
        wins, loses = (wl or {}).get("win", 0), (wl or {}).get("lose", 0)
        wr = f"{(wins / (wins + loses) * 100):.1f}%" if (wins + loses) else "N/A"
        embed = discord.Embed(title=p.get("personaname", "Игрок"), color=0x8B4513,
                               url=f"https://www.dotabuff.com/players/{account_id}")
        embed.set_thumbnail(url=p.get("avatarfull"))
        embed.add_field(name="Winrate", value=f"{wr} ({wins}W/{loses}L)")
        if recent:
            last = recent[0]
            hero = await od.hero_name(last["hero_id"])
            won = (last["player_slot"] < 128) == last["radiant_win"]
            embed.add_field(name="Последний матч",
                             value=f"{hero} — {'Победа' if won else 'Поражение'}", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Топ мета героев", emoji="🔥", style=discord.ButtonStyle.secondary, custom_id="dota:meta")
    async def meta_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        stats = await od.hero_stats()

        def picks(h): return sum(h.get(f"{i}_pick", 0) for i in range(1, 9))
        def wins(h): return sum(h.get(f"{i}_win", 0) for i in range(1, 9))

        ranked = sorted(stats, key=picks, reverse=True)[:10]
        lines = []
        for h in ranked:
            pk, wn = picks(h), wins(h)
            wr = f"{(wn / pk * 100):.1f}%" if pk else "N/A"
            lines.append(f"**{h['localized_name']}** — WR {wr}, picks {pk}")
        embed = discord.Embed(title="Топ-10 героев по пикрейту", description="\n".join(lines), color=0x8B4513)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Предметы под героя", emoji="🛒", style=discord.ButtonStyle.secondary, custom_id="dota:items_hero")
    async def items_hero_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HeroPickModal())

    @discord.ui.button(label="Топ предметы (тренд)", emoji="📦", style=discord.ButtonStyle.secondary, custom_id="dota:items_trend")
    async def items_trend_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        trend = await od.item_trend()
        lines = [f"**{name}** — {count} упоминаний в топ-билдах" for name, count in trend]
        embed = discord.Embed(
            title="Тренд предметов (по топ-15 популярных героев)",
            description="\n".join(lines) or "Нет данных",
            color=0x8B4513)
        embed.set_footer(text="Оценка на основе популярных сборок, не точный глобальный винрейт")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Мой live-матч", emoji="⚔️", style=discord.ButtonStyle.danger, custom_id="dota:live")
    async def live_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        state = cog.live_state.get(interaction.user.id) if cog else None
        if not state or (time.time() - state["updated_at"] > LIVE_STATE_TTL_SECONDS):
            await interaction.response.send_message(
                "Не вижу вас в текущем матче. Проверьте:\n"
                "— вы привязали SteamID\n"
                "— профиль Steam не скрывает статус \"сейчас в игре\"\n"
                "— с начала матча прошло больше минуты (данные обновляются раз в "
                f"~{STEAM_POLL_INTERVAL_SECONDS} сек)",
                ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        my_hero_id = state["my_hero_id"]
        enemy_hero_ids = state["enemy_hero_ids"]

        # 1) матчапы против врагов текущего матча
        matchups = await od.matchups(my_hero_id)
        by_id = {m["hero_id"]: m for m in matchups}
        rows = []
        for eid in enemy_hero_ids:
            m = by_id.get(eid)
            if not m or not m.get("games_played"):
                continue
            wr = m["wins"] / m["games_played"] * 100
            rows.append((await od.hero_name(eid), wr, m["games_played"]))
        rows.sort(key=lambda x: x[1])  # снизу — самые опасные для вас контрпики

        # 2) рекомендованные предметы под вашего героя
        item_recs = await od.item_recommendations(my_hero_id, per_phase=4)

        my_name = await od.hero_name(my_hero_id)

        embed = discord.Embed(
            title=f"Live-матч — вы играете {my_name}",
            color=0x8B4513,
        )

        matchup_lines = [f"{name} — ваш WR против него **{wr:.1f}%** ({games} игр)"
                          for name, wr, games in rows]
        embed.add_field(
            name="⚔️ Матчапы против врагов",
            value="\n".join(matchup_lines) or "Данных недостаточно",
            inline=False,
        )

        for phase, lines in item_recs.items():
            embed.add_field(name=f"🛒 {phase}", value="\n".join(lines), inline=True)

        embed.set_footer(text="Внизу списка матчапов — герои, против которых у вас хуже всего винрейт")

        # 3) LLM-стратегия поверх этих же цифр (если включена и настроен ключ)
        strategy_text = await strategy_writer.write(
            my_hero_name=my_name,
            matchup_rows=rows,
            item_recs=item_recs,
            my_hero_id=my_hero_id,
            enemy_hero_ids=enemy_hero_ids,
        )
        if strategy_text:
            # Discord ограничивает поле embed 1024 символами — режем с запасом
            embed.add_field(name="🧠 Стратегия (LLM на основе данных выше)",
                             value=strategy_text[:1000], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- cog ----------------

class DotaStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = Storage(DB_PATH)
        self.live_state: dict[int, dict] = {}  # discord_id -> {my_hero_id, enemy_hero_ids, updated_at}
        self.poll_live_matches.start()

    def cog_unload(self):
        self.poll_live_matches.cancel()

    async def cog_load(self):
        self.bot.add_view(DashboardView(self.db))

    @tasks.loop(seconds=STEAM_POLL_INTERVAL_SECONDS)
    async def poll_live_matches(self):
        players = self.db.all_players()
        if not players:
            return

        steam_ids = [p[2] for p in players]
        # Steam API принимает до 100 steamid за один запрос — бьём на пачки
        summaries: list[dict] = []
        for i in range(0, len(steam_ids), 100):
            summaries += await steam_client.get_player_summaries(steam_ids[i:i + 100])
        if STEAM_DEBUG_LOG and summaries:
            print(f"[STEAM] сводки: {summaries[:2]} ...")

        summary_by_steamid = {int(s["steamid"]): s for s in summaries if "steamid" in s}

        # группируем игроков по server_steam_id, чтобы не дублировать запросы к одному матчу
        server_to_entries: dict[str, list[tuple[int, int]]] = {}
        for discord_id, account_id, steam_id64 in players:
            s = summary_by_steamid.get(steam_id64)
            if not s or s.get("gameid") != "570":
                continue
            server_id = s.get("gameserversteamid")
            if not server_id:
                continue
            server_to_entries.setdefault(server_id, []).append((discord_id, account_id))

        for server_id, entries in server_to_entries.items():
            stats = await steam_client.get_realtime_stats(server_id)
            if not stats:
                continue
            if STEAM_DEBUG_LOG:
                print(f"[STEAM] realtime stats keys для сервера {server_id}: {list(stats.keys())}")

            # ПРОВЕРЬТЕ через STEAM_DEBUG_LOG, что структура ниже совпадает с реальным ответом —
            # схема Valve может отличаться по версии.
            teams = stats.get("teams", [])
            acc_info: dict[int, tuple] = {}
            for team in teams:
                team_number = team.get("team_number")
                for pl in team.get("players", []):
                    acc = pl.get("accountid")
                    hero_id = pl.get("heroid")
                    if acc is not None and hero_id:
                        acc_info[acc] = (team_number, hero_id)

            for discord_id, account_id in entries:
                info = acc_info.get(account_id)
                if not info:
                    continue
                my_team, my_hero_id = info
                enemy_ids = [hid for acc, (tn, hid) in acc_info.items()
                             if tn != my_team and acc != account_id]
                if enemy_ids:
                    self.live_state[discord_id] = {
                        "my_hero_id": my_hero_id,
                        "enemy_hero_ids": enemy_ids,
                        "updated_at": time.time(),
                    }

    @poll_live_matches.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()

    @commands.command(name="dota_setup")
    @commands.has_permissions(manage_channels=True)
    async def setup_panel(self, ctx: commands.Context):
        """Разово ставит/обновляет панель в текущем канале."""
        embed = discord.Embed(
            title="🎮 Dota Stats",
            description="Нажмите кнопку ниже, чтобы получить свою статистику. "
                        "Ответы видны только вам.",
            color=0x8B4513,
        )
        view = DashboardView(self.db)
        msg = await ctx.send(embed=embed, view=view)
        self.db.set_dashboard(ctx.guild.id, ctx.channel.id, msg.id)
        try:
            await msg.pin()
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(DotaStats(bot))
