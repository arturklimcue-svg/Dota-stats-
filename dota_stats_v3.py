"""
Dota статистика v4 — без слэш-команд, всё через кнопки + модалки.

ЧТО ИЗМЕНИЛОСЬ ПО СРАВНЕНИЮ С ПРЕДЫДУЩЕЙ ВЕРСИЕЙ:
  - Убран live-матч (Steam GetRealtimeStats, контры по вражескому драфту).
  - Добавлено:
      1) Статус "в игре / не в игре" — бесплатно, через Steam GetPlayerSummaries
         (gameid == "570"). Никакого конфига от игрока не требуется.
      2) Автообновляемая доска "Кто сейчас играет" — одно сообщение в канале,
         которое бот сам редактирует раз в STATUS_POLL_INTERVAL_SECONDS.
      3) Детект совместного лобби/пати — реализовано через party_id из
         OpenDota в деталях ЗАВЕРШЁННОГО матча (не в реальном времени).
         Изначальная идея использовать live-поле Steam "lobbysteamid"
         не сработала на практике — это недокументированное поле, и по
         реальным логам оно почти никогда не приходит от GetPlayerSummaries.
         party_id из OpenDota куда надёжнее и виден прямо в разборе матча.
      4) Разбор матча после игры — бот следит за recentMatches каждого
         привязанного игрока (обычная публичная статистика OpenDota, не
         требует ни файла, ни дружбы). Как только матч закончился и попал
         в базу — считает факты (KDA, GPM/XPM/CS против медианы по герою,
         урон, вардение), ВСЕГДА показывает список конкретных проблем по
         жёстким правилам (работает без всякого LLM), и ДОПОЛНИТЕЛЬНО, если
         настроен LLM-ключ, добавляет связный текстовый разбор поверх тех же
         фактов. Результат шлётся в личку игроку (с fallback в общий канал,
         если ЛС закрыты).
      5) Еженедельный лидерборд — раз в неделю (по понедельникам) в канал
         с панелью автоматически постится топ участников по количеству игр
         и винрейту за последние 7 дней. Плюс есть кнопка для лидерборда
         "по запросу" в любой момент.

Кнопки на панели:
  🔗 Привязать SteamID     -> модалка с вводом SteamID
  📊 Мой профиль            -> винрейт/ранг/последний матч (ephemeral)
  🔥 Топ мета героев        -> топ-10 по пикрейту
  🛒 Предметы под героя     -> модалка "введи имя героя" -> билд по фазам игры
  🛒 Топ предметы (тренд)   -> агрегированный тренд по топ-героям (кэш 1 час)
  🏆 Лидерборд недели       -> топ сервера за последние 7 дней "по запросу"

Отдельные команды администратора (не слэш, обычные текстовые, разово):
  !dota_setup           -> ставит панель с кнопками в текущем канале
  !dota_status_board    -> ставит автообновляемую доску "кто сейчас играет"

------------------------------------------------------------------
РАЗБОР МАТЧА — ЧТО ИМЕННО СЧИТАЕТСЯ И ОТКУДА:

  - Данные по конкретному матчу: OpenDota /matches/{id} — KDA, GPM, XPM,
    last hits, урон герою/башням, лечение, вардение.
  - "Медиана по герою" для сравнения: OpenDota /benchmarks?hero_id=X —
    официальная агрегированная статистика по игрокам на этом герое.
  - Список ошибок (⚠️ Возможные проблемы) строится ЖЁСТКИМИ правилами
    в detect_issues() — это работает ВСЕГДА, даже без LLM-ключа.
  - Текстовый разбор от LLM (если настроен GEMINI_API_KEY) получает на
    вход ТОЛЬКО эти же посчитанные факты и просто связно их пересказывает —
    модель не ищет ничего в интернете и не должна придумывать цифры сверх
    переданных.
------------------------------------------------------------------

Установка:
  pip install aiohttp discord.py
  Впишите STEAM_API_KEY (обязательно для статуса/доски/пати) и
  GEMINI_API_KEY (опционально, для текстового разбора матчей) ниже.
  await bot.load_extension("dota_stats_v3")
"""

import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks

OPENDOTA_BASE = "https://api.opendota.com/api"
STEAM_API_BASE = "https://api.steampowered.com"
STEAM64_OFFSET = 76561197960265728
DB_PATH = Path(__file__).parent / "dota_stats.db"

# --- настройте под себя ---
STEAM_API_KEY = "C5BD806939B9711D9722489FB77DF417"   # https://steamcommunity.com/dev/apikey (обязательно)

# Разбор матча (пункт 4) может писать текстовый комментарий через LLM.
# Выберите провайдера — переключение одной строкой, промт общий для обоих.
LLM_PROVIDER = "grok"  # "gemini" | "grok" | "none" (none = только жёсткие правила, без текста)

GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"     # https://aistudio.google.com/apikey (бесплатно)
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

GROK_API_KEY = "xai-rCzP0lopP9zahCuUz8TdlJgjCnwOZvFym1VxHcsJcKvf8wXpXFEhutYsyp5ZUPTGrHvbA2BuyDUN07Ob"          # https://console.x.ai (платно, есть бесплатные кредиты на старте)
GROK_MODEL = "grok-4.3"                     # актуальный флагман xAI на середину 2026 — проверьте на x.ai/api
GROK_API_BASE = "https://api.x.ai/v1"       # OpenAI-совместимый эндпоинт

ENABLE_LLM_REVIEW = True                    # выключите, если не нужен текстовый разбор от LLM

STATUS_POLL_INTERVAL_SECONDS = 40           # как часто обновлять доску "кто играет"
MATCH_POLL_INTERVAL_MINUTES = 5             # как часто проверять новые завершённые матчи
DEBUG_LOG = True                             # печатать сырые ответы API в консоль для отладки


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
            steam_id64 INTEGER NOT NULL,
            last_match_id INTEGER DEFAULT 0
        )""")
        # миграция для БД, созданных предыдущей версией (без last_match_id)
        try:
            c.execute("ALTER TABLE players ADD COLUMN last_match_id INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # колонка уже есть

        c.execute("""CREATE TABLE IF NOT EXISTS dashboard (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS status_board (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS leaderboard_posts (
            guild_id INTEGER,
            week_key TEXT,
            PRIMARY KEY (guild_id, week_key)
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
        """-> [(discord_id, account_id, steam_id64), ...]
        Сохранено для обратной совместимости с server_management.py."""
        return self.conn.execute(
            "SELECT discord_id, account_id, steam_id64 FROM players").fetchall()

    def all_players_full(self):
        """-> [(discord_id, account_id, steam_id64, last_match_id), ...]"""
        return self.conn.execute(
            "SELECT discord_id, account_id, steam_id64, last_match_id FROM players").fetchall()

    def update_last_match(self, discord_id: int, match_id: int):
        self.conn.execute(
            "UPDATE players SET last_match_id=? WHERE discord_id=?", (match_id, discord_id))
        self.conn.commit()

    def set_dashboard(self, guild_id: int, channel_id: int, message_id: int):
        self.conn.execute(
            "INSERT INTO dashboard (guild_id, channel_id, message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, message_id=excluded.message_id",
            (guild_id, channel_id, message_id))
        self.conn.commit()

    def all_dashboards(self):
        return self.conn.execute("SELECT guild_id, channel_id, message_id FROM dashboard").fetchall()

    def set_status_board(self, guild_id: int, channel_id: int, message_id: int):
        self.conn.execute(
            "INSERT INTO status_board (guild_id, channel_id, message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, message_id=excluded.message_id",
            (guild_id, channel_id, message_id))
        self.conn.commit()

    def all_status_boards(self):
        return self.conn.execute("SELECT guild_id, channel_id, message_id FROM status_board").fetchall()

    def leaderboard_posted(self, guild_id: int, week_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM leaderboard_posts WHERE guild_id=? AND week_key=?",
            (guild_id, week_key)).fetchone()
        return row is not None

    def mark_leaderboard_posted(self, guild_id: int, week_key: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO leaderboard_posts (guild_id, week_key) VALUES (?, ?)",
            (guild_id, week_key))
        self.conn.commit()


# ---------------- OpenDota client (профиль, мета, предметы, разбор матчей) ----------------

class OpenDota:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self.heroes_cache: dict[int, str] = {}
        self.hero_name_to_id: dict[str, int] = {}
        self.items_cache: dict[int, str] = {}
        self.item_trend_cache: tuple[float, list] = (0.0, [])
        self.benchmarks_cache: dict[int, tuple[float, dict]] = {}

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

    async def match_details(self, match_id: int):
        return await self.get(f"/matches/{match_id}")

    async def hero_benchmarks(self, hero_id: int) -> dict:
        """Медианные (и другие) значения GPM/XPM/CS и т.д. по игрокам на
        этом герое — официальная агрегированная статистика OpenDota.
        Кэш на час, чтобы не долбить API при каждом разборе матча."""
        cached_at, data = self.benchmarks_cache.get(hero_id, (0.0, None))
        if data and time.time() - cached_at < 3600:
            return data
        data = await self.get(f"/benchmarks?hero_id={hero_id}") or {}
        self.benchmarks_cache[hero_id] = (time.time(), data)
        return data

    async def player_matches_since(self, account_id: int, days: int = 7, limit: int = 100):
        return await self.get(f"/players/{account_id}/matches?date={days}&limit={limit}") or []


od = OpenDota()


# ---------------- Steam Web API client (статус "в игре", детект пати) ----------------

class SteamAPI:
    """Только GetPlayerSummaries — публичный, официальный, бесплатный метод.
    Показывает: запущена ли игра (gameid == '570' для Dota 2) и
    lobbysteamid (если игрок сейчас в лобби/пати — совпадающий id у
    нескольких привязанных участников значит они играют вместе).
    НЕ даёт составы команд/героев — для этого раньше использовался
    GetRealtimeStats, который убрали по вашей просьбе."""

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


steam_client = SteamAPI(STEAM_API_KEY)


# ---------------- разбор матча: факты + жёсткие правила + LLM поверх них ----------------

def _closest_percentile_value(benchmark_list: list[dict], target: float = 0.5):
    if not benchmark_list:
        return None
    closest = min(benchmark_list, key=lambda x: abs(x.get("percentile", 0) - target))
    return closest.get("value")


async def build_match_facts(account_id: int, match_id: int,
                             known_account_ids: dict[int, int] | None = None) -> dict | None:
    """Собирает объективные цифры по матчу конкретного игрока + сравнение
    с медианой по герою (OpenDota benchmarks). Никакого LLM тут нет —
    только факты.

    known_account_ids: {account_id: discord_id} всех привязанных на сервере —
    нужно, чтобы найти, кто из них был в той же пати (party_id) в этом матче.
    Это надёжная замена недокументированному Steam-полю lobbysteamid, которое
    на практике почти никогда не приходит в GetPlayerSummaries."""
    match = await od.match_details(match_id)
    if not match:
        return None
    player = next((p for p in match.get("players", []) if p.get("account_id") == account_id), None)
    if not player:
        return None

    hero_id = player.get("hero_id")
    won = (player.get("player_slot", 0) < 128) == match.get("radiant_win")
    duration_min = max(match.get("duration", 60) / 60, 1)

    bench = await od.hero_benchmarks(hero_id)
    result = bench.get("result", {}) if bench else {}
    gpm_median = _closest_percentile_value(result.get("gold_per_min", []))
    xpm_median = _closest_percentile_value(result.get("xp_per_min", []))
    lh_per_min_median = _closest_percentile_value(result.get("last_hits_per_min", []))
    hero_dmg_median = _closest_percentile_value(result.get("hero_damage_per_min", []))

    last_hits = player.get("last_hits", 0)

    party_teammates: list[int] = []  # discord_id других привязанных игроков в этой же пати
    party_id = player.get("party_id")
    if party_id and known_account_ids:
        for p in match.get("players", []):
            other_acc = p.get("account_id")
            if (other_acc and other_acc != account_id and p.get("party_id") == party_id
                    and other_acc in known_account_ids):
                party_teammates.append(known_account_ids[other_acc])

    return {
        "match_id": match_id,
        "hero": await od.hero_name(hero_id),
        "won": won,
        "kills": player.get("kills", 0),
        "deaths": player.get("deaths", 0),
        "assists": player.get("assists", 0),
        "duration_min": round(duration_min, 1),
        "gpm": player.get("gold_per_min", 0),
        "gpm_median": gpm_median,
        "xpm": player.get("xp_per_min", 0),
        "xpm_median": xpm_median,
        "last_hits": last_hits,
        "lh_per_min": round(last_hits / duration_min, 1),
        "lh_per_min_median": lh_per_min_median,
        "hero_damage": player.get("hero_damage", 0),
        "hero_damage_per_min_median": hero_dmg_median,
        "party_teammates": party_teammates,
        "tower_damage": player.get("tower_damage", 0),
        "hero_healing": player.get("hero_healing", 0),
        "obs_placed": player.get("obs_placed", 0),
        "sen_placed": player.get("sen_placed", 0),
    }


def detect_issues(facts: dict) -> list[str]:
    """Жёсткие правила, без LLM — работают всегда, даже без API-ключа.
    Каждая строка — конкретная, объективно проверяемая проблема."""
    issues = []

    if facts["deaths"] >= 8:
        issues.append(f"❗ Много смертей ({facts['deaths']}) — это стабильные потери золота "
                       f"и времени на респавн, стоит осторожнее играть на карте.")

    if facts["gpm_median"] and facts["gpm"] < facts["gpm_median"] * 0.75:
        issues.append(f"💰 GPM {facts['gpm']} заметно ниже медианы для этого героя "
                       f"({facts['gpm_median']:.0f}) — не хватало фарма/эффективности линии.")

    if facts["lh_per_min_median"] and facts["lh_per_min"] < facts["lh_per_min_median"] * 0.75:
        issues.append(f"🌾 Добиваний в минуту ({facts['lh_per_min']}) меньше медианы "
                       f"({facts['lh_per_min_median']:.1f}) — терялся крип-фарм.")

    if facts["obs_placed"] == 0 and facts["sen_placed"] == 0 and facts["duration_min"] > 15:
        issues.append("👁 За весь матч не поставлено ни одного варда — обзор карты страдал "
                       "весь матч, это касается всех, не только саппортов.")

    if not facts["won"] and (facts["kills"] + facts["assists"]) < facts["deaths"]:
        issues.append("⚔️ Участие в килах ниже количества смертей — низкий полезный "
                       "вклад в командные стычки в проигранной игре.")

    if facts["xpm_median"] and facts["xpm"] < facts["xpm_median"] * 0.75:
        issues.append(f"📈 XPM {facts['xpm']} ниже медианы ({facts['xpm_median']:.0f}) — "
                       f"герой развивался медленнее обычного для этой роли.")

    if not issues:
        issues.append("✅ По ключевым метрикам матч в пределах нормы для этого героя, "
                       "явных проблем не обнаружено.")
    return issues


class MatchReviewWriter:
    """Дополняет факты + список проблем связным текстом через LLM.
    Поддерживает два провайдера (переключаются константой LLM_PROVIDER):
      - "gemini": Google Gemini REST API (бесплатно, но недоступен в части регионов)
      - "grok":   xAI Grok, эндпоинт OpenAI-совместимый (api.x.ai/v1/chat/completions),
                  платный (есть стартовые бесплатные кредиты)
    Если провайдер "none", ключ не задан, или запрос упал — просто не
    добавляет текстовый блок: embed с фактами и detect_issues() всё равно
    уходит игроку, это никогда не блокирует основную функцию."""

    def __init__(self, provider: str):
        self.provider = provider
        self.session: aiohttp.ClientSession | None = None

        if provider == "gemini":
            self.enabled = ENABLE_LLM_REVIEW and bool(GEMINI_API_KEY) and "YOUR_" not in GEMINI_API_KEY
        elif provider == "grok":
            self.enabled = ENABLE_LLM_REVIEW and bool(GROK_API_KEY) and "YOUR_" not in GROK_API_KEY
        else:
            self.enabled = False

    async def _s(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    @staticmethod
    def _build_prompt(facts: dict, issues: list[str]) -> str:
        result_word = "Победа" if facts["won"] else "Поражение"
        return (
            "Ты — тренер по Dota 2. Ниже РЕАЛЬНЫЕ факты одного конкретного матча игрока "
            "(посчитаны программой из OpenDota, не придумывай цифры сверх них) и уже "
            "найденные автоматически проблемы. Твоя задача — написать связный, подробный "
            "разбор на русском (8-12 предложений): что получилось, в чём конкретно ошибки "
            "(опираясь на переданный список), и 2-3 практических совета на будущее. "
            "Пиши прямо и по делу, как тренер, а не как отчёт.\n\n"
            f"Герой: {facts['hero']}, результат: {result_word}, "
            f"KDA {facts['kills']}/{facts['deaths']}/{facts['assists']}, "
            f"длительность {facts['duration_min']} мин.\n"
            f"GPM: {facts['gpm']} (медиана по герою {facts['gpm_median']})\n"
            f"XPM: {facts['xpm']} (медиана {facts['xpm_median']})\n"
            f"Добивания/мин: {facts['lh_per_min']} (медиана {facts['lh_per_min_median']})\n"
            f"Урон герою: {facts['hero_damage']}, урон башням: {facts['tower_damage']}, "
            f"лечение: {facts['hero_healing']}\n"
            f"Вардов поставлено: {facts['obs_placed']} obs / {facts['sen_placed']} sentry\n\n"
            "Уже найденные автоматически проблемы:\n" + "\n".join(issues)
        )

    async def write_review(self, facts: dict, issues: list[str]) -> str | None:
        if not self.enabled:
            return None
        prompt = self._build_prompt(facts, issues)
        if self.provider == "gemini":
            return await self._write_gemini(prompt)
        if self.provider == "grok":
            return await self._write_grok(prompt)
        return None

    async def _write_gemini(self, prompt: str) -> str | None:
        url = f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            s = await self._s()
            async with s.post(url, params={"key": GEMINI_API_KEY}, json=payload,
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    if DEBUG_LOG:
                        print(f"[LLM/gemini] статус {r.status}: {await r.text()}")
                    return None
                data = await r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts).strip() or None
        except Exception as e:
            if DEBUG_LOG:
                print(f"[LLM/gemini] ошибка: {e}")
            return None

    async def _write_grok(self, prompt: str) -> str | None:
        # OpenAI-совместимый формат: POST /chat/completions, Bearer-токен,
        # messages вместо contents. Модель и базовый URL — см. константы вверху файла.
        url = f"{GROK_API_BASE}/chat/completions"
        headers = {"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": GROK_MODEL,
            "messages": [
                {"role": "system", "content": "Ты — тренер по Dota 2, отвечаешь на русском."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        }
        try:
            s = await self._s()
            async with s.post(url, headers=headers, json=payload,
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    if DEBUG_LOG:
                        print(f"[LLM/grok] статус {r.status}: {await r.text()}")
                    return None
                data = await r.json()
            choices = data.get("choices", [])
            if not choices:
                return None
            return choices[0].get("message", {}).get("content", "").strip() or None
        except Exception as e:
            if DEBUG_LOG:
                print(f"[LLM/grok] ошибка: {e}")
            return None


match_reviewer = MatchReviewWriter(LLM_PROVIDER)


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
            f"Привязал вас к **{name}**. Разбор матчей и статус \"в игре\" заработают "
            f"автоматически.", ephemeral=True)


class HeroPickModal(discord.ui.Modal, title="Предметы под героя"):
    hero = discord.ui.TextInput(label="Имя героя (можно частично)", placeholder="напр. Pudge")

    async def on_submit(self, interaction: discord.Interaction):
        hero_id = await od.find_hero_id(str(self.hero.value))
        if not hero_id:
            await interaction.response.send_message("Герой не найден.", ephemeral=True)
            return
        pop = await od.item_popularity(hero_id)
        if not pop:
            await interaction.response.send_message("Нет данных по предметам.", ephemeral=True)
            return

        phase_titles = {
            "start_game_items": "Старт", "early_game_items": "Ранняя игра",
            "mid_game_items": "Мид-гейм", "late_game_items": "Late-гейм",
        }
        embed = discord.Embed(title=f"Предметы — {await od.hero_name(hero_id)}", color=0x8B4513)
        for key, title in phase_titles.items():
            phase = pop.get(key, {})
            top = sorted(phase.items(), key=lambda x: x[1], reverse=True)[:5]
            if not top:
                continue
            lines = [f"{await od.item_name(int(iid))} ({cnt})" for iid, cnt in top]
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

    @discord.ui.button(label="Лидерборд недели", emoji="🏆", style=discord.ButtonStyle.success, custom_id="dota:leaderboard")
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        rows = await cog.compute_weekly_leaderboard(interaction.guild)
        lines = [f"**{i}.** {name} — {games} игр, {wins}W, WR {wr:.1f}%"
                 for i, (_, name, games, wins, wr) in enumerate(rows[:10], 1)]
        embed = discord.Embed(
            title=f"🏆 Лидерборд недели — {interaction.guild.name}",
            description="\n".join(lines) or "Нет данных за последние 7 дней",
            color=0x8B4513)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Разбор последней игры", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="dota:last_review")
    async def last_review_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        account_id = self.db.get_account_id(interaction.user.id)
        if not account_id:
            await interaction.response.send_message("Сначала привяжите SteamID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        recent = await od.get(f"/players/{account_id}/recentMatches")
        if not recent:
            await interaction.followup.send("Не нашёл матчей у вас в истории OpenDota.", ephemeral=True)
            return

        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        embed = await cog.build_match_review(interaction.user.id, account_id, recent[0]["match_id"])
        if not embed:
            await interaction.followup.send(
                "Не получилось собрать разбор — матч мог быть без полной статистики "
                "(например, ещё не распарсился в OpenDota). Попробуйте чуть позже.",
                ephemeral=True)
            return
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- cog ----------------

class DotaStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = Storage(DB_PATH)
        self.poll_status.start()
        self.poll_new_matches.start()
        self.check_weekly_leaderboard.start()

    def cog_unload(self):
        self.poll_status.cancel()
        self.poll_new_matches.cancel()
        self.check_weekly_leaderboard.cancel()

    async def cog_load(self):
        self.bot.add_view(DashboardView(self.db))

    # ---------- 1+2+3: статус "в игре", доска, детект пати ----------

    @tasks.loop(seconds=STATUS_POLL_INTERVAL_SECONDS)
    async def poll_status(self):
        players = self.db.all_players()
        if not players:
            return

        steam_ids = [p[2] for p in players]
        summaries: list[dict] = []
        for i in range(0, len(steam_ids), 100):  # Steam API — максимум 100 id за запрос
            summaries += await steam_client.get_player_summaries(steam_ids[i:i + 100])
        if DEBUG_LOG:
            in_dota = sum(1 for s in summaries if s.get("gameid") == "570")
            print(f"[STEAM] опрошено {len(summaries)} игроков, в Dota 2 сейчас: {in_dota}")

        summary_by_steamid = {int(s["steamid"]): s for s in summaries if "steamid" in s}

        # Раньше тут же детектилась совместная пати по lobbysteamid, но это
        # недокументированное поле Steam на практике почти никогда не приходит
        # (см. реальные логи) — детект пати перенесён в разбор матча после
        # игры, где используется надёжный party_id из OpenDota.
        playing_ids = [
            discord_id for discord_id, account_id, steam_id64 in players
            if (s := summary_by_steamid.get(steam_id64)) and s.get("gameid") == "570"
        ]

        self._playing_ids = playing_ids
        await self._refresh_status_boards()

    @poll_status.before_loop
    async def before_poll_status(self):
        await self.bot.wait_until_ready()

    async def _refresh_status_boards(self):
        for guild_id, channel_id, message_id in self.db.all_status_boards():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            channel = guild.get_channel(channel_id)
            if not channel:
                continue

            playing_members = [guild.get_member(did) for did in getattr(self, "_playing_ids", [])]
            playing_members = [m for m in playing_members if m]
            playing_lines = [f"🟢 {m.mention}" for m in playing_members] or ["Сейчас никто не играет"]

            embed = discord.Embed(title="🎮 Кто сейчас играет", color=0x8B4513,
                                   timestamp=datetime.now(timezone.utc))
            embed.add_field(name=f"В игре ({len(playing_members)})",
                             value="\n".join(playing_lines), inline=False)
            embed.set_footer(text=f"Обновляется автоматически каждые ~{STATUS_POLL_INTERVAL_SECONDS} сек. "
                                   f"Кто с кем в пати — смотрите в разборе матча после игры")

            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=embed)
            except discord.NotFound:
                msg = await channel.send(embed=embed)
                self.db.set_status_board(guild_id, channel_id, msg.id)
            except discord.Forbidden:
                if DEBUG_LOG:
                    print(f"[STATUS BOARD] нет прав в канале {channel_id}")

    # ---------- 4: разбор завершённых матчей ----------

    @tasks.loop(minutes=MATCH_POLL_INTERVAL_MINUTES)
    async def poll_new_matches(self):
        for discord_id, account_id, steam_id64, last_match_id in self.db.all_players_full():
            try:
                recent = await od.get(f"/players/{account_id}/recentMatches")
                if not recent:
                    continue
                newest = recent[0]
                if newest["match_id"] == last_match_id:
                    continue
                self.db.update_last_match(discord_id, newest["match_id"])
                if last_match_id == 0:
                    continue  # первый прогон после привязки — не шлём разбор старого матча
                await self._send_match_review(discord_id, account_id, newest["match_id"])
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[MATCH REVIEW] ошибка для {discord_id}: {e}")
            await asyncio.sleep(1)  # не долбить OpenDota подряд

    @poll_new_matches.before_loop
    async def before_poll_matches(self):
        await self.bot.wait_until_ready()

    def _build_review_embed(self, facts: dict, issues: list[str], review_text: str | None) -> discord.Embed:
        result_word = "🟢 Победа" if facts["won"] else "🔴 Поражение"
        embed = discord.Embed(
            title=f"Разбор матча — {facts['hero']} ({result_word})",
            url=f"https://www.dotabuff.com/matches/{facts['match_id']}",
            color=0x2ecc71 if facts["won"] else 0xe74c3c,
        )
        embed.add_field(name="KDA", value=f"{facts['kills']}/{facts['deaths']}/{facts['assists']}")
        embed.add_field(name="Длительность", value=f"{facts['duration_min']} мин")
        gpm_med = f" (медиана {facts['gpm_median']:.0f})" if facts["gpm_median"] else ""
        xpm_med = f" (медиана {facts['xpm_median']:.0f})" if facts["xpm_median"] else ""
        embed.add_field(name="GPM / XPM", value=f"{facts['gpm']}{gpm_med} / {facts['xpm']}{xpm_med}", inline=False)
        embed.add_field(name="Добивания", value=f"{facts['last_hits']} ({facts['lh_per_min']}/мин)")
        embed.add_field(name="Урон герою / башням", value=f"{facts['hero_damage']} / {facts['tower_damage']}")
        embed.add_field(name="⚠️ Возможные проблемы", value="\n".join(issues), inline=False)
        if facts["party_teammates"]:
            mentions = ", ".join(f"<@{did}>" for did in facts["party_teammates"])
            embed.add_field(name="🎉 Играли вместе (пати)", value=mentions, inline=False)
        if review_text:
            embed.add_field(name="🧠 Разбор от тренера (LLM)", value=review_text[:1000], inline=False)
        return embed

    async def build_match_review(self, discord_id: int, account_id: int, match_id: int) -> discord.Embed | None:
        """Считает факты + issues + LLM-текст и собирает embed. Переиспользуется
        и автопостом (poll_new_matches), и кнопкой "разбор последней игры"."""
        known_account_ids = {acc: did for did, acc, _ in self.db.all_players()}
        facts = await build_match_facts(account_id, match_id, known_account_ids)
        if not facts:
            return None
        issues = detect_issues(facts)
        review_text = await match_reviewer.write_review(facts, issues)
        return self._build_review_embed(facts, issues, review_text)

    async def _send_match_review(self, discord_id: int, account_id: int, match_id: int):
        embed = await self.build_match_review(discord_id, account_id, match_id)
        if not embed:
            return

        user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
        try:
            await user.send(embed=embed)
            return
        except (discord.Forbidden, discord.HTTPException):
            pass

        # fallback: если ЛС закрыты — постим в канал с панелью на любом сервере, где есть этот игрок
        for guild_id, channel_id, _ in self.db.all_dashboards():
            guild = self.bot.get_guild(guild_id)
            if not guild or not guild.get_member(discord_id):
                continue
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.send(content=f"<@{discord_id}>", embed=embed)
            break

    # ---------- 5: еженедельный лидерборд ----------

    async def compute_weekly_leaderboard(self, guild: discord.Guild) -> list[tuple]:
        rows = []
        for discord_id, account_id, steam_id64 in self.db.all_players():
            if guild and not guild.get_member(discord_id):
                continue
            matches = await od.player_matches_since(account_id, days=7)
            if not matches:
                continue
            games = len(matches)
            wins = sum(1 for m in matches if (m["player_slot"] < 128) == m["radiant_win"])
            wr = wins / games * 100
            member = guild.get_member(discord_id) if guild else None
            name = member.display_name if member else str(discord_id)
            rows.append((discord_id, name, games, wins, wr))
            await asyncio.sleep(0.3)  # не долбить OpenDota подряд
        rows.sort(key=lambda r: (r[2], r[4]), reverse=True)  # сначала по кол-ву игр, потом по WR
        return rows

    @tasks.loop(hours=24)
    async def check_weekly_leaderboard(self):
        now = datetime.now(timezone.utc)
        if now.weekday() != 0:  # постим только по понедельникам
            return
        week_key = now.strftime("%G-W%V")
        for guild_id, channel_id, _ in self.db.all_dashboards():
            if self.db.leaderboard_posted(guild_id, week_key):
                continue
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild else None
            if not channel:
                continue
            rows = await self.compute_weekly_leaderboard(guild)
            lines = [f"**{i}.** {name} — {games} игр, {wins}W, WR {wr:.1f}%"
                     for i, (_, name, games, wins, wr) in enumerate(rows[:10], 1)]
            embed = discord.Embed(
                title="🏆 Итоги недели",
                description="\n".join(lines) or "Недостаточно данных за эту неделю",
                color=0x8B4513)
            await channel.send(embed=embed)
            self.db.mark_leaderboard_posted(guild_id, week_key)

    @check_weekly_leaderboard.before_loop
    async def before_leaderboard(self):
        await self.bot.wait_until_ready()

    # ---------- разовая настройка ----------

    @commands.command(name="dota_setup")
    @commands.has_permissions(manage_channels=True)
    async def setup_panel(self, ctx: commands.Context):
        """Разово ставит/обновляет панель с кнопками в текущем канале."""
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

    @commands.command(name="dota_status_board")
    @commands.has_permissions(manage_channels=True)
    async def setup_status_board(self, ctx: commands.Context):
        """Разово создаёт автообновляемую доску "кто сейчас играет" в текущем канале."""
        embed = discord.Embed(title="🎮 Кто сейчас играет",
                               description="Обновляется автоматически...", color=0x8B4513)
        msg = await ctx.send(embed=embed)
        self.db.set_status_board(ctx.guild.id, ctx.channel.id, msg.id)
        try:
            await msg.pin()
        except discord.Forbidden:
            pass
        await ctx.send("Доска создана, дальше бот сам будет её обновлять.")


async def setup(bot: commands.Bot):
    await bot.add_cog(DotaStats(bot))
