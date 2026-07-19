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
         настроен LLM-ключ (DeepSeek), добавляет связный текстовый разбор
         поверх тех же фактов. Результат шлётся в личку игроку (с fallback
         в общий канал, если ЛС закрыты).
      5) Еженедельный лидерборд — раз в неделю (по понедельникам) в канал
         с панелью автоматически постится топ участников по количеству игр
         и винрейту за последние 7 дней. Плюс есть кнопка для лидерборда
         "по запросу" в любой момент.
      6) Дуэль недели (топ-1 vs топ-2) — сразу после публикации лидерборда
         бот предлагает двум лидерам сыграть 1x1 в течение суток (кнопки
         "Принять"/"Отклонить"). После принятия обоими — создаётся отдельный
         текстовый канал на день: участники могут в нём писать, все
         остальные (зрители) только читают. Итог фиксируется взаимным
         подтверждением обоих игроков (каждый жмёт "Я победил"/"Я проиграл");
         при совпадающих отчётах результат сразу уходит в статистику дуэлей
         (кнопка "Топ дуэлянтов" + отдельное поле в профиле). При несовпадении
         отчётов или молчании кого-то к дедлайну — зовутся модераторы
         (право Manage Server) и решают исход вручную. Канал дуэли
         удаляется по истечении суток независимо от исхода.
         Подробности и обоснование механики верификации — в комментарии
         перед классами DuelOfferView/DuelReportView/DuelAdminResolveView.
      7) Автоудаление опустевших голосовых каналов — как только из
         голосового канала выходит последний человек, бот удаляет канал
         сам. Это касается ЛЮБЫХ голосовых каналов сервера (включая уже
         существующие, не только созданные ботом для дуэлей), КРОМЕ:
           - каналов/категорий из списка исключений (например, поранговые
             комнаты) — управляется командами !dota_voice_protect /
             !dota_voice_unprotect / !dota_voice_protected_list;
           - системного AFK-канала сервера, если он настроен.
         Голосовой канал дуэли (см. пункт 6) регистрируется в этом же
         механизме автоматически — отдельно защищать не нужно.
         ⚠️ Учтите: включив это на сервере с уже настроенными голосовыми
         комнатами, сразу защитите постоянные "статичные" каналы (общий
         холл, AFK и т.п.) командой !dota_voice_protect — иначе они
         удалятся при первом же опустении.

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
  !dota_voice_protect [#канал|ID категории]   -> исключить из автоудаления
  !dota_voice_unprotect [#канал|ID категории] -> вернуть в автоудаление
  !dota_voice_protected_list                  -> список исключений

------------------------------------------------------------------
РАЗБОР МАТЧА — ЧТО ИМЕННО СЧИТАЕТСЯ И ОТКУДА:

  - Данные по конкретному матчу: OpenDota /matches/{id} — KDA, GPM, XPM,
    last hits, урон герою/башням, лечение, вардение.
  - "Медиана по герою" для сравнения: OpenDota /benchmarks?hero_id=X —
    официальная агрегированная статистика по игрокам на этом герое.
  - Список ошибок (⚠️ Возможные проблемы) строится ЖЁСТКИМИ правилами
    в detect_issues() — это работает ВСЕГДА, даже без LLM-ключа.
  - Текстовый разбор от LLM (если настроен GROQ_API_KEY или DEEPSEEK_API_KEY)
    получает на вход ТОЛЬКО эти же посчитанные факты и просто связно их
    пересказывает — модель не ищет ничего в интернете и не должна
    придумывать цифры сверх переданных.
------------------------------------------------------------------

Установка:
  pip install aiohttp discord.py
  Впишите STEAM_API_KEY (обязательно для статуса/доски/пати) и
  GROQ_API_KEY (опционально, бесплатно, для текстового разбора матчей) ниже.
  await bot.load_extension("dota_stats_v3")
"""

import asyncio
import sqlite3
import time
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks

OPENDOTA_BASE = "https://api.opendota.com/api"
STEAM_API_BASE = "https://api.steampowered.com"
STEAM64_OFFSET = 76561197960265728
DB_PATH = Path(__file__).parent / "dota_stats.db"

# --- настройте под себя ---
STEAM_API_KEY = "C5BD806939B9711D9722489FB77DF41"   # https://steamcommunity.com/dev/apikey (обязательно)

# Разбор матча (пункт 4) может писать текстовый комментарий через LLM.
LLM_PROVIDER = "groq"  # "groq" | "deepseek" | "none" (none = только жёсткие правила, без текста)

GROQ_API_KEY = "gsk_SV21LUFhGHMmGQxO5M2hWGdyb3FYcDdIPrOA8rMKrkvT4UFt0ZA"           # https://console.groq.com/keys (бесплатно, без карты)
GROQ_MODEL = "llama-3.3-70b-versatile"       # актуальную модель проверьте на console.groq.com/docs/models
GROQ_API_BASE = "https://api.groq.com/openai/v1"  # OpenAI-совместимый эндпоинт

DEEPSEEK_API_KEY = "YOUR_DEEPSEEK_API_KEY"   # https://platform.deepseek.com/api_keys (платно)
DEEPSEEK_MODEL = "deepseek-chat"             # актуальная модель — проверьте на platform.deepseek.com/docs
DEEPSEEK_API_BASE = "https://api.deepseek.com"  # OpenAI-совместимый эндпоинт

ENABLE_LLM_REVIEW = True                    # выключите, если не нужен текстовый разбор от LLM

STATUS_POLL_INTERVAL_SECONDS = 40           # как часто обновлять доску "кто играет"
MATCH_POLL_INTERVAL_MINUTES = 5             # как часто проверять новые завершённые матчи
DEBUG_LOG = True                             # печатать сырые ответы API в консоль для отладки

# --- дуэль лидеров недели (топ-1 vs топ-2) ---
DUEL_OFFER_HOURS = 24        # сколько часов действует предложение + сам канал дуэли
                              # ("в пределах дня" — сутки с момента, когда лидерборд опубликован)
DUEL_CHECK_INTERVAL_MINUTES = 15  # как часто проверять истечение дедлайна дуэли


def to_account_id(steam_id) -> int:
    steam_id = int(steam_id)
    return steam_id - STEAM64_OFFSET if steam_id > STEAM64_OFFSET else steam_id


def to_steam64(steam_id) -> int:
    steam_id = int(steam_id)
    return steam_id if steam_id > STEAM64_OFFSET else steam_id + STEAM64_OFFSET


def _truncate_for_embed(text: str, limit: int = 1024) -> str:
    """Обрезает текст под лимит поля эмбеда Discord (1024 символа), стараясь
    не рвать текст посреди слова/предложения — обрезает по последней точке
    или пробелу перед лимитом, если это не теряет слишком много текста."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    last_dot = cut.rfind(". ")
    if last_dot > limit * 0.6:
        return cut[:last_dot + 1]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.6:
        return cut[:last_space] + "…"
    return cut + "…"


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
        c.execute("""CREATE TABLE IF NOT EXISTS duels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            week_key TEXT NOT NULL,
            player1_id INTEGER NOT NULL,
            player2_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            offer_channel_id INTEGER,
            offer_message_id INTEGER,
            duel_channel_id INTEGER,
            voice_channel_id INTEGER,
            report_message_id INTEGER,
            accept1 INTEGER DEFAULT 0,
            accept2 INTEGER DEFAULT 0,
            report1 TEXT,
            report2 TEXT,
            winner_id INTEGER,
            created_at TEXT NOT NULL,
            deadline TEXT NOT NULL,
            UNIQUE(guild_id, week_key)
        )""")
        # миграция для БД, созданных до добавления голосового канала дуэли
        try:
            c.execute("ALTER TABLE duels ADD COLUMN voice_channel_id INTEGER")
        except sqlite3.OperationalError:
            pass  # колонка уже есть
        c.execute("""CREATE TABLE IF NOT EXISTS duel_stats (
            discord_id INTEGER PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            disputes INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS managed_voice_channels (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            duel_id INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS voice_protected (
            target_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            kind TEXT NOT NULL
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

    # ---------- дуэль лидеров недели ----------

    def create_duel(self, guild_id: int, week_key: str, player1_id: int, player2_id: int,
                     deadline_iso: str) -> int | None:
        """Создаёт предложение дуэли на эту неделю. Возвращает id, либо None,
        если дуэль на этой неделе на этом сервере уже создавалась."""
        try:
            cur = self.conn.execute(
                "INSERT INTO duels (guild_id, week_key, player1_id, player2_id, status, "
                "created_at, deadline) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (guild_id, week_key, player1_id, player2_id,
                 datetime.now(timezone.utc).isoformat(), deadline_iso))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # уже есть дуэль на этой неделе

    def get_duel(self, duel_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM duels WHERE id=?", (duel_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM duels WHERE id=?", (duel_id,)).description]
        return dict(zip(cols, row))

    def duels_by_status(self, statuses: list[str]) -> list[dict]:
        q = f"SELECT * FROM duels WHERE status IN ({','.join('?' * len(statuses))})"
        cur = self.conn.execute(q, statuses)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def duels_with_open_channel(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM duels WHERE duel_channel_id IS NOT NULL")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def set_offer_message(self, duel_id: int, channel_id: int, message_id: int):
        self.conn.execute(
            "UPDATE duels SET offer_channel_id=?, offer_message_id=? WHERE id=?",
            (channel_id, message_id, duel_id))
        self.conn.commit()

    def set_accept(self, duel_id: int, slot: int):
        col = "accept1" if slot == 1 else "accept2"
        self.conn.execute(f"UPDATE duels SET {col}=1 WHERE id=?", (duel_id,))
        self.conn.commit()

    def set_duel_channel(self, duel_id: int, channel_id: int, report_message_id: int):
        self.conn.execute(
            "UPDATE duels SET duel_channel_id=?, report_message_id=?, status='accepted' WHERE id=?",
            (channel_id, report_message_id, duel_id))
        self.conn.commit()

    def clear_duel_channel(self, duel_id: int):
        self.conn.execute("UPDATE duels SET duel_channel_id=NULL WHERE id=?", (duel_id,))
        self.conn.commit()

    def set_duel_voice_channel(self, duel_id: int, voice_channel_id: int):
        self.conn.execute("UPDATE duels SET voice_channel_id=? WHERE id=?", (voice_channel_id, duel_id))
        self.conn.commit()

    def clear_duel_voice_channel(self, duel_id: int):
        self.conn.execute("UPDATE duels SET voice_channel_id=NULL WHERE id=?", (duel_id,))
        self.conn.commit()

    # ---------- реестр "временных" голосовых каналов ----------
    # Любой голосовой канал, зарегистрированный тут, бот сам удалит, как
    # только из него выйдет последний человек. Работает не только для
    # дуэлей — это общий механизм, use-кейсы просто регистрируют канал.

    def register_voice_channel(self, channel_id: int, guild_id: int, duel_id: int | None = None):
        self.conn.execute(
            "INSERT OR REPLACE INTO managed_voice_channels (channel_id, guild_id, duel_id) VALUES (?, ?, ?)",
            (channel_id, guild_id, duel_id))
        self.conn.commit()

    def unregister_voice_channel(self, channel_id: int):
        self.conn.execute("DELETE FROM managed_voice_channels WHERE channel_id=?", (channel_id,))
        self.conn.commit()

    def is_managed_voice_channel(self, channel_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM managed_voice_channels WHERE channel_id=?", (channel_id,)).fetchone()
        return row is not None

    def all_managed_voice_channels(self) -> list[tuple]:
        """-> [(channel_id, guild_id, duel_id), ...]"""
        return self.conn.execute("SELECT channel_id, guild_id, duel_id FROM managed_voice_channels").fetchall()

    # ---------- исключения из автоудаления (например, поранговые комнаты) ----------

    def protect_voice_target(self, target_id: int, guild_id: int, kind: str):
        """kind: 'channel' — конкретный голосовой канал, 'category' — вся категория целиком."""
        self.conn.execute(
            "INSERT OR REPLACE INTO voice_protected (target_id, guild_id, kind) VALUES (?, ?, ?)",
            (target_id, guild_id, kind))
        self.conn.commit()

    def unprotect_voice_target(self, target_id: int):
        self.conn.execute("DELETE FROM voice_protected WHERE target_id=?", (target_id,))
        self.conn.commit()

    def is_voice_protected(self, channel_id: int, category_id: int | None, guild_id: int) -> bool:
        ids = [channel_id] + ([category_id] if category_id else [])
        q = (f"SELECT 1 FROM voice_protected WHERE guild_id=? "
             f"AND target_id IN ({','.join('?' * len(ids))})")
        row = self.conn.execute(q, (guild_id, *ids)).fetchone()
        return row is not None

    def list_protected_voice(self, guild_id: int) -> list[tuple]:
        """-> [(target_id, kind), ...]"""
        return self.conn.execute(
            "SELECT target_id, kind FROM voice_protected WHERE guild_id=?", (guild_id,)).fetchall()

    def set_status(self, duel_id: int, status: str):
        self.conn.execute("UPDATE duels SET status=? WHERE id=?", (status, duel_id))
        self.conn.commit()

    def set_report(self, duel_id: int, slot: int, result: str):
        col = "report1" if slot == 1 else "report2"
        self.conn.execute(f"UPDATE duels SET {col}=? WHERE id=?", (result, duel_id))
        self.conn.commit()

    def set_winner(self, duel_id: int, winner_id: int, status: str):
        self.conn.execute(
            "UPDATE duels SET winner_id=?, status=? WHERE id=?", (winner_id, status, duel_id))
        self.conn.commit()

    def bump_duel_stats(self, discord_id: int, won: bool):
        col = "wins" if won else "losses"
        self.conn.execute(
            "INSERT INTO duel_stats (discord_id, wins, losses) VALUES (?, 0, 0) "
            "ON CONFLICT(discord_id) DO NOTHING", (discord_id,))
        self.conn.execute(f"UPDATE duel_stats SET {col} = {col} + 1 WHERE discord_id=?", (discord_id,))
        self.conn.commit()

    def bump_duel_disputes(self, discord_id: int):
        self.conn.execute(
            "INSERT INTO duel_stats (discord_id, wins, losses, disputes) VALUES (?, 0, 0, 0) "
            "ON CONFLICT(discord_id) DO NOTHING", (discord_id,))
        self.conn.execute("UPDATE duel_stats SET disputes = disputes + 1 WHERE discord_id=?", (discord_id,))
        self.conn.commit()

    def get_duel_stats(self, discord_id: int) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT wins, losses FROM duel_stats WHERE discord_id=?", (discord_id,)).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def top_duelists(self, limit: int = 10) -> list[tuple]:
        return self.conn.execute(
            "SELECT discord_id, wins, losses FROM duel_stats WHERE (wins + losses) > 0 "
            "ORDER BY wins DESC, losses ASC LIMIT ?", (limit,)).fetchall()


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


# Границы стадий в минутах — общепринятое деление матча Dota на фазы
STAGE_BOUNDARIES = [("Ранняя игра (0-10 мин)", 0, 10),
                     ("Мид-гейм (10-25 мин)", 10, 25),
                     ("Поздняя игра (25+ мин)", 25, None)]


def _stage_rate(cumulative: list, start_min: int, end_min: int | None, duration_min: float) -> float | None:
    """cumulative — массив накопленных значений (gold_t/xp_t/lh_t из OpenDota,
    по минутам матча). Считает среднюю скорость набора за отрезок [start, end)."""
    if not cumulative:
        return None
    end_min = duration_min if end_min is None else min(end_min, duration_min)
    if end_min <= start_min or start_min >= len(cumulative):
        return None
    start_idx = int(start_min)
    end_idx = min(int(end_min), len(cumulative) - 1)
    if end_idx <= start_idx:
        return None
    return (cumulative[end_idx] - cumulative[start_idx]) / (end_idx - start_idx)


def _build_stage_breakdown(player: dict, duration_min: float) -> list[dict]:
    """Разбивает матч на 3 стадии и считает GPM/XPM/добивания-в-минуту
    в каждой отдельно — на основе поминутных массивов gold_t/xp_t/lh_t,
    которые отдаёт OpenDota /matches/{id}. Без этого LLM пришлось бы
    угадывать, где именно игрок отставал — так у него на входе реальные
    цифры по каждой фазе, а не только средние по матчу целиком."""
    gold_t = player.get("gold_t") or []
    xp_t = player.get("xp_t") or []
    lh_t = player.get("lh_t") or []

    stages = []
    for title, start, end in STAGE_BOUNDARIES:
        if start >= duration_min:
            continue
        gpm = _stage_rate(gold_t, start, end, duration_min)
        xpm = _stage_rate(xp_t, start, end, duration_min)
        lhpm = _stage_rate(lh_t, start, end, duration_min)
        if gpm is None and xpm is None and lhpm is None:
            continue
        stages.append({
            "title": title,
            "gpm": round(gpm) if gpm is not None else None,
            "xpm": round(xpm) if xpm is not None else None,
            "lh_per_min": round(lhpm, 1) if lhpm is not None else None,
        })
    return stages


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
        "stages": _build_stage_breakdown(player, duration_min),
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
    Поддерживает два провайдера (переключаются константой LLM_PROVIDER),
    оба через OpenAI-совместимый /chat/completions:
      - "groq":     бесплатно, без карты, быстрый (console.groq.com)
      - "deepseek": платно (по факту очень дёшево)
    Если провайдер "none", ключ не задан, или запрос упал — просто не
    добавляет текстовый блок: embed с фактами и detect_issues() всё равно
    уходит игроку, это никогда не блокирует основную функцию."""

    def __init__(self, provider: str):
        self.provider = provider
        self.session: aiohttp.ClientSession | None = None

        if provider == "groq":
            self.enabled = ENABLE_LLM_REVIEW and bool(GROQ_API_KEY) and "YOUR_" not in GROQ_API_KEY
        elif provider == "deepseek":
            self.enabled = ENABLE_LLM_REVIEW and bool(DEEPSEEK_API_KEY) and "YOUR_" not in DEEPSEEK_API_KEY
        else:
            self.enabled = False

    async def _s(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    @staticmethod
    def _build_prompt(facts: dict, issues: list[str]) -> str:
        result_word = "Победа" if facts["won"] else "Поражение"

        stage_lines = []
        for st in facts.get("stages", []):
            parts = [st["title"] + ":"]
            if st["gpm"] is not None:
                parts.append(f"GPM {st['gpm']}")
            if st["xpm"] is not None:
                parts.append(f"XPM {st['xpm']}")
            if st["lh_per_min"] is not None:
                parts.append(f"добиваний/мин {st['lh_per_min']}")
            stage_lines.append(" ".join(parts))
        stages_block = "\n".join(stage_lines) if stage_lines else "Нет данных по стадиям (короткий матч)."

        return (
            "Ты — тренер по Dota 2, разбираешь матч со своим учеником. Ниже РЕАЛЬНЫЕ "
            "факты одного конкретного матча (посчитаны программой из OpenDota — не "
            "придумывай цифры и события сверх переданных) и уже найденные автоматически "
            "проблемы.\n\n"
            "Напиши разбор от первого лица, как будто сам игрок вспоминает и оценивает "
            "свою игру («в начале я...», «к середине матча стало...», «в конце игры я...»), "
            "но опирайся СТРОГО на переданные цифры, а не на выдуманные события боя. "
            "Структура ответа — по стадиям, каждая отдельным коротким абзацем (2-3 "
            "предложения на стадию):\n"
            "1. Ранняя игра — что показывают GPM/XPM/добивания на этом отрезке относительно "
            "медианы по герою, была ли это уже проблемная зона.\n"
            "2. Мид-гейм — то же самое, отметь, стало ли лучше или хуже по сравнению с "
            "ранней игрой.\n"
            "3. Поздняя игра — то же самое, плюс как итоговый результат (победа/поражение) "
            "и KDA сходятся с этой динамикой.\n"
            "В конце — короткий блок «Что подтянуть» из 2-3 конкретных практических советов, "
            "явно привязанных к стадии, где была найдена проблема (например: "
            "«в ранней игре — на 0-10 минуте фокусироваться на линии и добиваниях»).\n"
            "Не повторяй сухие цифры построчно — уже показаны отдельно в эмбеде, здесь нужна "
            "именно связная интерпретация. Пиши на русском, прямо и по делу.\n\n"
            f"Герой: {facts['hero']}, результат: {result_word}, "
            f"KDA {facts['kills']}/{facts['deaths']}/{facts['assists']}, "
            f"длительность {facts['duration_min']} мин.\n"
            f"GPM за матч: {facts['gpm']} (медиана по герою {facts['gpm_median']})\n"
            f"XPM за матч: {facts['xpm']} (медиана {facts['xpm_median']})\n"
            f"Добивания/мин за матч: {facts['lh_per_min']} (медиана {facts['lh_per_min_median']})\n"
            f"Урон герою: {facts['hero_damage']}, урон башням: {facts['tower_damage']}, "
            f"лечение: {facts['hero_healing']}\n"
            f"Вардов поставлено: {facts['obs_placed']} obs / {facts['sen_placed']} sentry\n\n"
            f"Разбивка по стадиям игры (реальные цифры по временным отрезкам матча):\n"
            f"{stages_block}\n\n"
            "Уже найденные автоматически проблемы:\n" + "\n".join(issues)
        )

    async def write_review(self, facts: dict, issues: list[str]) -> str | None:
        if not self.enabled:
            return None
        prompt = self._build_prompt(facts, issues)
        if self.provider == "groq":
            return await self._write_groq(prompt)
        if self.provider == "deepseek":
            return await self._write_deepseek(prompt)
        return None

    async def _write_groq(self, prompt: str) -> str | None:
        # OpenAI-совместимый формат: POST /chat/completions, Bearer-токен,
        # messages. Модель и базовый URL — см. константы вверху файла.
        url = f"{GROQ_API_BASE}/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": GROQ_MODEL,
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
                        print(f"[LLM/groq] статус {r.status}: {await r.text()}")
                    return None
                data = await r.json()
            choices = data.get("choices", [])
            if not choices:
                return None
            return choices[0].get("message", {}).get("content", "").strip() or None
        except Exception as e:
            if DEBUG_LOG:
                print(f"[LLM/groq] ошибка: {e}")
            return None

    async def _write_deepseek(self, prompt: str) -> str | None:
        # OpenAI-совместимый формат: POST /chat/completions, Bearer-токен,
        # messages. Модель и базовый URL — см. константы вверху файла.
        url = f"{DEEPSEEK_API_BASE}/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": DEEPSEEK_MODEL,
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
                        print(f"[LLM/deepseek] статус {r.status}: {await r.text()}")
                    return None
                data = await r.json()
            choices = data.get("choices", [])
            if not choices:
                return None
            return choices[0].get("message", {}).get("content", "").strip() or None
        except Exception as e:
            if DEBUG_LOG:
                print(f"[LLM/deepseek] ошибка: {e}")
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
        duel_wins, duel_losses = self.db.get_duel_stats(interaction.user.id)
        if duel_wins + duel_losses > 0:
            embed.add_field(name="🥊 Дуэли лидеров", value=f"{duel_wins}W / {duel_losses}L")
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

    @discord.ui.button(label="Топ дуэлянтов", emoji="🥊", style=discord.ButtonStyle.success, custom_id="dota:duel_top")
    async def duel_top_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        rows = self.db.top_duelists(limit=10)
        lines = []
        for i, (discord_id, wins, losses) in enumerate(rows, 1):
            member = interaction.guild.get_member(discord_id) if interaction.guild else None
            name = member.display_name if member else f"<@{discord_id}>"
            lines.append(f"**{i}.** {name} — {wins}W/{losses}L")
        embed = discord.Embed(
            title="🥊 Топ дуэлянтов",
            description="\n".join(lines) or "Дуэлей ещё не было",
            color=0x8B4513)
        embed.set_footer(text="Дуэль недели: топ-1 против топ-2 еженедельного лидерборда")
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


# ---------------- дуэль лидеров недели: views ----------------
#
# Как считается итог дуэли ("open dota лобби честно не покажет"):
#   OpenDota не умеет надёжно отличить приватный 1x1-лоббик от обычной игры,
#   поэтому автоматической проверки через API нет. Вместо неё — ВЗАИМНОЕ
#   подтверждение: после игры оба участника независимо жмут "Я победил" /
#   "Я проиграл" в канале дуэли. Если ответы совпадают логически (один
#   победил, другой проиграл) — результат сразу засчитывается в статистику.
#   Если оба нажали "победил", оба "проиграл", либо дедлайн прошёл без
#   отчёта от кого-то одного — статус уходит в "спорный", и в канал
#   зовутся модераторы (Manage Server), которые вручную решают исход
#   кнопками ниже. Так результат нельзя подделать в одиночку: соврать
#   можно только сговорившись вдвоём, а несовпадение отчётов сразу видно.


def _duel_slot(duel: dict, discord_id: int) -> int | None:
    if discord_id == duel["player1_id"]:
        return 1
    if discord_id == duel["player2_id"]:
        return 2
    return None


class DuelOfferView(discord.ui.View):
    """Кнопки принятия/отклонения вызова. custom_id жёстко зашивает duel_id,
    поэтому view пересоздаётся и заново регистрируется при старте бота
    (см. DotaStats.cog_load) — так кнопки остаются рабочими и после рестарта."""

    def __init__(self, db: Storage, duel_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.duel_id = duel_id
        self.accept_btn.custom_id = f"duel:accept:{duel_id}"
        self.decline_btn.custom_id = f"duel:decline:{duel_id}"

    async def _check_slot(self, interaction: discord.Interaction) -> tuple[dict, int] | None:
        duel = self.db.get_duel(self.duel_id)
        if not duel or duel["status"] != "pending":
            await interaction.response.send_message("Это предложение дуэли уже неактуально.", ephemeral=True)
            return None
        slot = _duel_slot(duel, interaction.user.id)
        if slot is None:
            await interaction.response.send_message("Эта дуэль не про вас — кнопки только для вызванных игроков.",
                                                      ephemeral=True)
            return None
        return duel, slot

    @discord.ui.button(label="✅ Принять вызов", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        checked = await self._check_slot(interaction)
        if not checked:
            return
        duel, slot = checked
        self.db.set_accept(self.duel_id, slot)
        duel = self.db.get_duel(self.duel_id)

        if duel["accept1"] and duel["accept2"]:
            cog: "DotaStats" = interaction.client.get_cog("DotaStats")
            await interaction.response.send_message("Вызов принят! Создаю канал дуэли...", ephemeral=True)
            await cog.create_duel_channel(duel)
        else:
            await interaction.response.send_message(
                "Вызов принят, ждём подтверждения от соперника.", ephemeral=True)

    @discord.ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger)
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        checked = await self._check_slot(interaction)
        if not checked:
            return
        duel, slot = checked
        self.db.set_status(self.duel_id, "declined")
        await interaction.response.send_message("Вызов отклонён.", ephemeral=False)
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        await cog.update_offer_message(duel["id"])


class DuelReportView(discord.ui.View):
    """Кнопки самоотчёта о результате внутри канала дуэли."""

    def __init__(self, db: Storage, duel_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.duel_id = duel_id
        self.win_btn.custom_id = f"duel:report:{duel_id}:win"
        self.loss_btn.custom_id = f"duel:report:{duel_id}:loss"

    async def _report(self, interaction: discord.Interaction, result: str):
        duel = self.db.get_duel(self.duel_id)
        if not duel or duel["status"] != "accepted":
            await interaction.response.send_message("Отчёт по этой дуэли уже закрыт.", ephemeral=True)
            return
        slot = _duel_slot(duel, interaction.user.id)
        if slot is None:
            await interaction.response.send_message("Отчитываться могут только участники этой дуэли.",
                                                      ephemeral=True)
            return
        self.db.set_report(self.duel_id, slot, result)
        await interaction.response.send_message(f"Принял ваш отчёт: «{result}». Ждём соперника (если ещё не).",
                                                  ephemeral=True)
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        await cog.try_resolve_duel(self.duel_id)

    @discord.ui.button(label="🏆 Я победил", style=discord.ButtonStyle.success)
    async def win_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._report(interaction, "win")

    @discord.ui.button(label="💀 Я проиграл", style=discord.ButtonStyle.secondary)
    async def loss_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._report(interaction, "loss")


class DuelAdminResolveView(discord.ui.View):
    """Появляется только при спорном исходе (несовпадающие самоотчёты
    или тишина от одного из игроков к дедлайну) — решают модераторы
    сервера (право Manage Server)."""

    def __init__(self, db: Storage, duel_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.duel_id = duel_id
        self.p1_btn.custom_id = f"duel:admin:{duel_id}:1"
        self.p2_btn.custom_id = f"duel:admin:{duel_id}:2"
        self.void_btn.custom_id = f"duel:admin:{duel_id}:void"

    async def _is_mod(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Решать спорный исход может только модератор (Manage Server).",
                                                      ephemeral=True)
            return False
        return True

    async def _resolve(self, interaction: discord.Interaction, winner_slot: int | None):
        if not await self._is_mod(interaction):
            return
        duel = self.db.get_duel(self.duel_id)
        if not duel:
            return
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        await cog.finalize_duel(duel, winner_slot, resolved_by_admin=True)
        await interaction.response.send_message("Спор разрешён, результат зафиксирован.", ephemeral=False)

    @discord.ui.button(label="Победил игрок 1", style=discord.ButtonStyle.success)
    async def p1_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, 1)

    @discord.ui.button(label="Победил игрок 2", style=discord.ButtonStyle.success)
    async def p2_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, 2)

    @discord.ui.button(label="Отменить (не считать)", style=discord.ButtonStyle.danger)
    async def void_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, None)


# ---------------- cog ----------------

class DotaStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = Storage(DB_PATH)
        self.poll_status.start()
        self.poll_new_matches.start()
        self.check_weekly_leaderboard.start()
        self.check_duel_expiry.start()

    def cog_unload(self):
        self.poll_status.cancel()
        self.poll_new_matches.cancel()
        self.check_weekly_leaderboard.cancel()
        self.check_duel_expiry.cancel()

    async def cog_load(self):
        self.bot.add_view(DashboardView(self.db))
        # переподключаем кнопки активных дуэлей после рестарта бота —
        # custom_id зашит в duel_id, поэтому старые сообщения снова оживают
        for duel in self.db.duels_by_status(["pending"]):
            self.bot.add_view(DuelOfferView(self.db, duel["id"]))
        for duel in self.db.duels_by_status(["accepted"]):
            self.bot.add_view(DuelReportView(self.db, duel["id"]))
        for duel in self.db.duels_by_status(["disputed"]):
            self.bot.add_view(DuelAdminResolveView(self.db, duel["id"]))

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
            embed.add_field(name="🧠 Разбор от тренера (LLM)", value=_truncate_for_embed(review_text, 1024),
                             inline=False)
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
            if len(rows) >= 2:
                await self.offer_weekly_duel(guild, channel, rows[0], rows[1], week_key)

    @check_weekly_leaderboard.before_loop
    async def before_leaderboard(self):
        await self.bot.wait_until_ready()

    # ---------- дуэль лидеров недели (топ-1 vs топ-2) ----------

    async def offer_weekly_duel(self, guild: discord.Guild, channel: discord.abc.Messageable,
                                 top1_row: tuple, top2_row: tuple, week_key: str):
        p1_id, p1_name = top1_row[0], top1_row[1]
        p2_id, p2_name = top2_row[0], top2_row[1]
        deadline = datetime.now(timezone.utc) + timedelta(hours=DUEL_OFFER_HOURS)

        duel_id = self.db.create_duel(guild.id, week_key, p1_id, p2_id, deadline.isoformat())
        if duel_id is None:
            return  # уже предлагали на этой неделе

        embed = discord.Embed(
            title="⚔️ Дуэль недели",
            description=(f"Лидеры недели встречаются 1x1!\n\n"
                          f"🥇 <@{p1_id}> ({p1_name}) vs 🥈 <@{p2_id}> ({p2_name})\n\n"
                          f"Чтобы дуэль состоялась, оба должны нажать «Принять вызов» "
                          f"в течение {DUEL_OFFER_HOURS} ч. После принятия откроется "
                          f"отдельный канал для игры и зрителей."),
            color=0xE67E22)
        embed.add_field(name="Дедлайн", value=f"<t:{int(deadline.timestamp())}:R>")
        view = DuelOfferView(self.db, duel_id)
        msg = await channel.send(content=f"<@{p1_id}> <@{p2_id}>", embed=embed, view=view)
        self.db.set_offer_message(duel_id, msg.channel.id, msg.id)

    async def update_offer_message(self, duel_id: int):
        """Перерисовывает embed оффера после отклонения/истечения — оставляет
        историю в канале вместо тихого исчезновения кнопок."""
        duel = self.db.get_duel(duel_id)
        if not duel or not duel["offer_channel_id"]:
            return
        channel = self.bot.get_channel(duel["offer_channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(duel["offer_message_id"])
        except (discord.NotFound, discord.HTTPException):
            return
        status_text = {"declined": "❌ Вызов отклонён.", "expired": "⌛ Никто не принял вызов вовремя."}
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        embed.add_field(name="Итог", value=status_text.get(duel["status"], duel["status"]), inline=False)
        await msg.edit(embed=embed, view=None)

    async def create_duel_channel(self, duel: dict):
        guild = self.bot.get_guild(duel["guild_id"])
        if not guild:
            return
        p1 = guild.get_member(duel["player1_id"])
        p2 = guild.get_member(duel["player2_id"])
        if not p1 or not p2:
            return

        offer_channel = guild.get_channel(duel["offer_channel_id"]) if duel["offer_channel_id"] else None
        category = offer_channel.category if isinstance(offer_channel, discord.TextChannel) else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            p1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            p2: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        chan_name = f"⚔-duel-{p1.display_name}-vs-{p2.display_name}"[:95]
        try:
            duel_channel = await guild.create_text_channel(
                chan_name, category=category, overwrites=overwrites,
                topic=f"Дуэль недели: {p1.display_name} vs {p2.display_name}. "
                      f"Зрители видят чат, писать могут только участники.")
        except discord.Forbidden:
            if DEBUG_LOG:
                print(f"[DUEL] нет прав создать канал в {guild.id}")
            return

        deadline_ts = int(datetime.fromisoformat(duel["deadline"]).timestamp())
        embed = discord.Embed(
            title="⚔️ Дуэль недели началась",
            description=(f"{p1.mention} vs {p2.mention}\n\n"
                          f"Договоритесь и сыграйте 1x1 в течение дня. Канал только для чтения "
                          f"для зрителей — писать могут только участники дуэли.\n\n"
                          f"После игры **каждый** нажимает свою кнопку ниже. Если отчёты совпадут — "
                          f"результат зафиксируется автоматически. Если нет — позовём модераторов.\n\n"
                          f"Канал закроется <t:{deadline_ts}:R>."),
            color=0xE67E22)

        # голосовой канал для игроков — приватный (зрители его не видят),
        # автоматически удаляется, как только оба игрока из него выйдут
        voice_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            p1: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            p2: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
        }
        voice_channel = None
        try:
            voice_channel = await guild.create_voice_channel(
                f"🔊-duel-{p1.display_name}-vs-{p2.display_name}"[:95],
                category=category, overwrites=voice_overwrites)
            self.db.set_duel_voice_channel(duel["id"], voice_channel.id)
            self.db.register_voice_channel(voice_channel.id, guild.id, duel_id=duel["id"])
            embed.add_field(name="🔊 Голосовой канал",
                             value=f"{voice_channel.mention} — только для игроков. "
                                   f"Удалится сам, как только оба выйдут.", inline=False)
        except discord.Forbidden:
            if DEBUG_LOG:
                print(f"[DUEL] нет прав создать голосовой канал в {guild.id}")

        view = DuelReportView(self.db, duel["id"])
        report_msg = await duel_channel.send(content=f"{p1.mention} {p2.mention}", embed=embed, view=view)
        self.db.set_duel_channel(duel["id"], duel_channel.id, report_msg.id)
        await self.update_offer_message(duel["id"])

    async def try_resolve_duel(self, duel_id: int):
        duel = self.db.get_duel(duel_id)
        if not duel or duel["status"] != "accepted":
            return
        r1, r2 = duel["report1"], duel["report2"]
        if not r1 or not r2:
            return  # ждём второй отчёт

        if r1 == "win" and r2 == "loss":
            await self.finalize_duel(duel, winner_slot=1)
        elif r1 == "loss" and r2 == "win":
            await self.finalize_duel(duel, winner_slot=2)
        else:
            # оба заявили победу или оба поражение — самостоятельно не разрешить честно
            await self.escalate_duel_dispute(duel)

    async def escalate_duel_dispute(self, duel: dict):
        self.db.set_status(duel["id"], "disputed")
        self.db.bump_duel_disputes(duel["player1_id"])
        self.db.bump_duel_disputes(duel["player2_id"])
        if not duel["duel_channel_id"]:
            return
        channel = self.bot.get_channel(duel["duel_channel_id"])
        if not channel:
            return
        mods = [m for m in channel.guild.members if not m.bot and m.guild_permissions.manage_guild]
        mention = " ".join(m.mention for m in mods) if mods else "@here"
        embed = discord.Embed(
            title="⚠️ Спорный результат дуэли",
            description="Отчёты игроков не совпали (или один из них не отчитался вовремя) — "
                         "нужно ручное решение модератора.",
            color=0xE74C3C)
        view = DuelAdminResolveView(self.db, duel["id"])
        await channel.send(content=mention, embed=embed, view=view)

    async def finalize_duel(self, duel: dict, winner_slot: int | None, resolved_by_admin: bool = False):
        """winner_slot: 1/2 — победитель, None — дуэль аннулирована (в статистику не идёт)."""
        p1_id, p2_id = duel["player1_id"], duel["player2_id"]
        if winner_slot is None:
            self.db.set_status(duel["id"], "voided")
        else:
            winner_id = p1_id if winner_slot == 1 else p2_id
            loser_id = p2_id if winner_slot == 1 else p1_id
            self.db.set_winner(duel["id"], winner_id, "confirmed")
            self.db.bump_duel_stats(winner_id, won=True)
            self.db.bump_duel_stats(loser_id, won=False)

        if not duel["duel_channel_id"]:
            return
        channel = self.bot.get_channel(duel["duel_channel_id"])
        if not channel:
            return
        if winner_slot is None:
            text = "Результат аннулирован модератором — в статистику дуэлей не идёт."
        else:
            winner_id = p1_id if winner_slot == 1 else p2_id
            text = f"🏆 Победитель дуэли: <@{winner_id}>" + (" (решено модератором)" if resolved_by_admin else "")
        embed = discord.Embed(title="Дуэль завершена", description=text, color=0x2ECC71)
        embed.set_footer(text="Канал закроется по истечении дедлайна дуэли.")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @tasks.loop(minutes=DUEL_CHECK_INTERVAL_MINUTES)
    async def check_duel_expiry(self):
        now = datetime.now(timezone.utc)

        # предложения, которые никто не успел принять
        for duel in self.db.duels_by_status(["pending"]):
            if datetime.fromisoformat(duel["deadline"]) <= now:
                self.db.set_status(duel["id"], "expired")
                await self.update_offer_message(duel["id"])

        # дуэли, где канал открыт, но к дедлайну нет обоюдного подтверждения
        for duel in self.db.duels_by_status(["accepted"]):
            if datetime.fromisoformat(duel["deadline"]) <= now:
                await self.escalate_duel_dispute(self.db.get_duel(duel["id"]))

        # канал живёт ровно "день дуэли" — удаляем по дедлайну независимо от исхода
        for duel in self.db.duels_with_open_channel():
            if datetime.fromisoformat(duel["deadline"]) <= now:
                channel = self.bot.get_channel(duel["duel_channel_id"])
                if channel:
                    try:
                        await channel.delete(reason="Дуэль недели: истёк день дуэли")
                    except discord.HTTPException:
                        pass
                self.db.clear_duel_channel(duel["id"])
                if duel["voice_channel_id"]:
                    await self._delete_managed_voice_channel(duel["voice_channel_id"])
                    self.db.clear_duel_voice_channel(duel["id"])

    @check_duel_expiry.before_loop
    async def before_duel_expiry(self):
        await self.bot.wait_until_ready()

    async def _delete_managed_voice_channel(self, channel_id: int):
        """Используется для принудительного удаления голосового канала дуэли
        по дедлайну (независимо от того, пуст он или нет) — обычное
        опустение отдельно ловит on_voice_state_update ниже."""
        channel = self.bot.get_channel(channel_id)
        if channel:
            try:
                await channel.delete(reason="Дуэль недели: истёк день дуэли")
            except discord.HTTPException:
                pass
        self.db.unregister_voice_channel(channel_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                     after: discord.VoiceState):
        """Удаляет ЛЮБОЙ голосовой канал сервера, как только из него выходит
        последний человек — не только созданные ботом (дуэльные), но и
        обычные, уже существующие голосовые комнаты.

        Не трогает:
          - каналы/категории из списка исключений (voice_protected) —
            например, поранговые комнаты; управляется командами
            !dota_voice_protect / !dota_voice_unprotect / !dota_voice_protected_list;
          - системный AFK-канал сервера (Server Settings -> Overview -> Afk channel),
            если он настроен — его удаление сломало бы настройки сервера.
        """
        left_channel = before.channel
        if left_channel is None or left_channel == after.channel:
            return  # это не выход из канала (например, просто mute/deafen)
        if not isinstance(left_channel, discord.VoiceChannel):
            return  # трогаем только обычные голосовые каналы, не Stage

        # даём Discord'у долю секунды обновить список участников канала
        await asyncio.sleep(1)
        refreshed = self.bot.get_channel(left_channel.id)
        if refreshed is None:
            self.db.unregister_voice_channel(left_channel.id)
            return
        if len(refreshed.members) > 0:
            return  # кто-то ещё остался — не трогаем

        guild = refreshed.guild
        if guild.afk_channel and refreshed.id == guild.afk_channel.id:
            return
        category_id = refreshed.category_id
        if self.db.is_voice_protected(refreshed.id, category_id, guild.id):
            return

        try:
            await refreshed.delete(reason="Голосовой канал опустел")
        except discord.HTTPException:
            if DEBUG_LOG:
                print(f"[VOICE AUTODELETE] не смог удалить канал {refreshed.id} в {guild.id}")
            return
        self.db.unregister_voice_channel(refreshed.id)
        if DEBUG_LOG:
            print(f"[VOICE AUTODELETE] удалён опустевший канал «{refreshed.name}» ({guild.name})")

    # ---------- защита голосовых каналов/категорий от автоудаления ----------

    @commands.command(name="dota_voice_protect")
    @commands.has_permissions(manage_channels=True)
    async def voice_protect(self, ctx: commands.Context,
                             target: typing.Union[discord.VoiceChannel, discord.CategoryChannel] = None):
        """Исключает голосовой канал (или ЦЕЛУЮ КАТЕГОРИЮ — удобно для
        поранговых комнат, если они сгруппированы в одну категорию) из
        автоудаления при опустении.
        Использование: !dota_voice_protect #канал  или  !dota_voice_protect 123456789012345 (ID категории)
        Без аргумента — защищает голосовой канал, в котором сейчас
        находится сам автор команды."""
        if target is None:
            if ctx.author.voice and ctx.author.voice.channel:
                target = ctx.author.voice.channel
            else:
                await ctx.send("Укажите голосовой канал или категорию (упоминание/ID), либо зайдите "
                                "в нужный голосовой канал и повторите команду без аргумента.")
                return
        kind = "category" if isinstance(target, discord.CategoryChannel) else "channel"
        self.db.protect_voice_target(target.id, ctx.guild.id, kind)
        kind_word = "категория" if kind == "category" else "канал"
        await ctx.send(f"Готово: {kind_word} «{target.name}» больше не будет удаляться при опустении.")

    @commands.command(name="dota_voice_unprotect")
    @commands.has_permissions(manage_channels=True)
    async def voice_unprotect(self, ctx: commands.Context,
                               target: typing.Union[discord.VoiceChannel, discord.CategoryChannel]):
        """Снимает защиту с канала/категории — они снова будут удаляться при опустении."""
        self.db.unprotect_voice_target(target.id)
        await ctx.send(f"Снял защиту с «{target.name}».")

    @commands.command(name="dota_voice_protected_list")
    @commands.has_permissions(manage_channels=True)
    async def voice_protected_list(self, ctx: commands.Context):
        """Показывает текущий список исключений из автоудаления."""
        rows = self.db.list_protected_voice(ctx.guild.id)
        lines = []
        for target_id, kind in rows:
            obj = ctx.guild.get_channel(target_id)
            name = obj.name if obj else f"(канал/категория удалены, id {target_id})"
            lines.append(f"{'📁 категория' if kind == 'category' else '🔊 канал'} — {name}")
        await ctx.send("**Защищены от автоудаления:**\n" + ("\n".join(lines) if lines else "пока никто"))

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
