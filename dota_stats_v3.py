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
           - каналов/категорий из списка исключений в БД — управляется
             командами !dota_voice_protect / !dota_voice_unprotect /
             !dota_voice_protected_list (удобно для ваших собственных
             "статичных" каналов — общий холл и т.п.);
           - "постоянных" каналов/категорий самого бота — поранговых комнат
             (🐎/🛡/👑), 📊 Статистика сервера и ➕ Создать войс — они
             защищены ЖЁСТКО В КОДЕ по названию (см.
             ALWAYS_PROTECTED_VOICE_CATEGORY_NAMES /
             ALWAYS_PROTECTED_VOICE_CHANNEL_NAMES выше), а не только через
             БД — поэтому не "слетают" при сбросе/потере базы (например,
             редеплой без персистентного диска) и не требуют команды
             !dota_voice_protect после каждой настройки сервера;
           - системного AFK-канала сервера, если он настроен.
         Голосовой канал дуэли (см. пункт 6) регистрируется в этом же
         механизме автоматически — отдельно защищать не нужно.
         ⚠️ Учтите: включив это на сервере с уже настроенными ДРУГИМИ
         голосовыми комнатами (не из списка выше), сразу защитите их
         командой !dota_voice_protect — иначе они удалятся при первом же
         опустении.

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
import os
import re
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
def _resolve_db_path() -> Path:
    """На bothost.ru (тариф Basic/Pro) папка /app/data — персистентное
    хранилище: bothost специально исключает её из git-синка и НЕ трогает
    при обновлении из репозитория/перезапуске контейнера (см.
    bothost.ru/docs/database-storage). Раньше база лежала рядом с кодом
    (Path(__file__).parent) — а это как раз то, что перезаписывается при
    каждом git-деплое, отсюда и терялись привязанные игроки при живых
    Discord-ролях (роль в Discord не пропадает, а строка в SQLite — да).
    Переменная окружения DB_PATH позволяет переопределить путь вручную,
    если хостинг другой."""
    env_path = os.environ.get("DB_PATH", "").strip()
    if env_path:
        return Path(env_path)
    bothost_data_dir = Path("/app/data")
    try:
        bothost_data_dir.mkdir(parents=True, exist_ok=True)
        return bothost_data_dir / "dota_stats.db"
    except OSError:
        return Path(__file__).parent / "dota_stats.db"  # локальный запуск не на bothost


DB_PATH = _resolve_db_path()


def _resolve_backup_channel_id() -> int:
    """ID приватного текстового канала, который служит "источником правды"
    для привязок discord_id <-> SteamID (см. класс PlayerBackup ниже).

    Идея: локальная SQLite (DB_PATH) — это просто быстрый кэш поверх этого
    канала. Если SQLite пропадёт целиком (редеплой без персистентного
    диска, смена хостинга, ручное удаление файла и т.п.) — при следующем
    старте бот перечитает историю канала и восстановит всех привязанных
    игроков, ничего не спрашивая у пользователей заново.

    ID канала намеренно берётся из переменной окружения, а НЕ хранится в
    самой SQLite/файле рядом с кодом — иначе получилась бы курица и яйцо:
    та же самая база, которую мы страхуем, хранила бы адрес своей же
    страховки.

    Как получить ID канала: в Discord включите Режим разработчика
    (Настройки -> Расширенные), затем ПКМ по каналу -> "Копировать ID".
    Канал должен быть приватным (права видят только бот и, по желанию,
    админы сервера) — туда попадают Steam-профили игроков."""
    raw = os.environ.get("PLAYER_BACKUP_CHANNEL_ID", "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        print(f"[WARN] PLAYER_BACKUP_CHANNEL_ID='{raw}' — не похоже на ID канала (число). "
              f"Бэкап привязок в Discord отключён, работаем только с локальной SQLite.")
        return 0


PLAYER_BACKUP_CHANNEL_ID = _resolve_backup_channel_id()


# ---------------- голосовые каналы/категории, защищённые от автоудаления ----------------
# Раньше защита жила ТОЛЬКО в SQLite (voice_protected, через !dota_voice_protect) —
# из-за этого она "слетала" при любом сбросе базы (редеплой без персистентного
# диска, смена хостинга, ручное удаление файла и т.п., см. _resolve_db_path выше).
# Эти каналы/категории — часть постоянной структуры сервера (создаются в
# server_management.py), поэтому они защищены прямо в коде и НЕ зависят от
# содержимого БД. Совпадение — по названию категории/канала, а не по ID: так
# защита переживает даже пересоздание категории/канала (новый ID).
# ВАЖНО: если переименуете эти категории/каналы в server_management.py
# (RANK_GROUPS / STATS_CATEGORY / JOIN_TO_CREATE_CHANNEL), обновите и здесь.
ALWAYS_PROTECTED_VOICE_CATEGORY_NAMES = set()
ALWAYS_PROTECTED_VOICE_CHANNEL_NAMES = {"🎮 Гостевая"}

# Маркер в начале сообщения-бэкапа — по нему бот отличает "свои" служебные
# сообщения от любых других (например, если админ что-то написал в тот же
# канал руками) при сканировании истории канала.
_BACKUP_MARKER = "🔗 DOTA_LINK"
_BACKUP_FIELD_RE = re.compile(r"^(discord_id|account_id|steam_id64):\s*(\d+)\s*$", re.MULTILINE)


def _format_backup_message(discord_id: int, account_id: int, steam_id64: int) -> str:
    """Текст сообщения-бэкапа. Специально простой текст (не embed) — так
    его проще и надёжнее парсить обратно построчным регэкспом, плюс он
    без проблем читается админом сервера глазами прямо в канале."""
    return (
        f"{_BACKUP_MARKER}\n"
        f"Игрок: <@{discord_id}>\n"
        f"discord_id: {discord_id}\n"
        f"account_id: {account_id}\n"
        f"steam_id64: {steam_id64}"
    )


def _parse_backup_message(content: str) -> dict | None:
    """Обратный парсинг _format_backup_message(). Возвращает None, если
    сообщение не наше (нет маркера) или в нём не хватает полей —
    такие сообщения при синхронизации просто игнорируются, а не роняют
    весь процесс восстановления."""
    if _BACKUP_MARKER not in content:
        return None
    fields = dict(_BACKUP_FIELD_RE.findall(content))
    if not {"discord_id", "account_id", "steam_id64"} <= fields.keys():
        return None
    try:
        return {k: int(v) for k, v in fields.items()}
    except ValueError:
        return None


# --- бэкап экономики (балансы + покупки) ---
_ECONOMY_FIELD_RE = re.compile(r"^(discord_id|balance|total_earned|total_spent|titles|role_color):\s*(.+?)\s*$", re.MULTILINE)

def _format_economy_backup(discord_id: int, balance: int, total_earned: int,
                            total_spent: int, titles: list[str], role_color: str | None) -> str:
    titles_str = ",".join(titles) if titles else ""
    lines = [
        _ECONOMY_BACKUP_MARKER,
        f"discord_id: {discord_id}",
        f"balance: {balance}",
        f"total_earned: {total_earned}",
        f"total_spent: {total_spent}",
        f"titles: {titles_str}",
        f"role_color: {role_color or ''}",
    ]
    return "\n".join(lines)

def _parse_economy_backup(content: str) -> dict | None:
    if _ECONOMY_BACKUP_MARKER not in content:
        return None
    fields = dict(_ECONOMY_FIELD_RE.findall(content))
    if "discord_id" not in fields:
        return None
    try:
        return {
            "discord_id": int(fields["discord_id"]),
            "balance": int(fields.get("balance", 0)),
            "total_earned": int(fields.get("total_earned", 0)),
            "total_spent": int(fields.get("total_spent", 0)),
            "titles": [t for t in fields.get("titles", "").split(",") if t],
            "role_color": fields.get("role_color") or None,
        }
    except (ValueError, KeyError):
        return None


# --- бэкап достижений ---
_ACHIEVEMENT_FIELD_RE = re.compile(r"^(discord_id|achievements):\s*(.+?)\s*$", re.MULTILINE)

def _format_achievement_backup(discord_id: int, achievements: list[str]) -> str:
    return (
        f"{_ACHIEVEMENT_BACKUP_MARKER}\n"
        f"discord_id: {discord_id}\n"
        f"achievements: {','.join(achievements)}"
    )

def _parse_achievement_backup(content: str) -> dict | None:
    if _ACHIEVEMENT_BACKUP_MARKER not in content:
        return None
    fields = dict(_ACHIEVEMENT_FIELD_RE.findall(content))
    if "discord_id" not in fields:
        return None
    try:
        return {
            "discord_id": int(fields["discord_id"]),
            "achievements": [a for a in fields.get("achievements", "").split(",") if a],
        }
    except (ValueError, KeyError):
        return None


# --- бэкап турниров ---
_TOURNAMENT_FIELD_RE = re.compile(r"^(id|guild_id|name|creator_id|status|max_players|winner_id|participants|matches):\s*(.+?)\s*$", re.MULTILINE)

def _format_tournament_backup(t: dict) -> str:
    participants = ",".join(str(p) for p in (t.get("participants") or []))
    match_parts = []
    for m in (t.get("matches") or []):
        p1 = m.get("player1_id", "?")
        p2 = m.get("player2_id", "?")
        w = m.get("winner_id") or "pending"
        match_parts.append(f"R{m['round']}S{m['slot']}:{p1}v{p2}:{w}")
    matches_str = ",".join(match_parts)
    lines = [
        _TOURNAMENT_BACKUP_MARKER,
        f"id: {t['id']}",
        f"guild_id: {t['guild_id']}",
        f"name: {t.get('name', '')}",
        f"creator_id: {t.get('creator_id', 0)}",
        f"status: {t.get('status', 'signup')}",
        f"max_players: {t.get('max_players', 16)}",
        f"winner_id: {t.get('winner_id') or 'null'}",
        f"participants: {participants}",
        f"matches: {matches_str}",
    ]
    return "\n".join(lines)

def _parse_tournament_backup(content: str) -> dict | None:
    if _TOURNAMENT_BACKUP_MARKER not in content:
        return None
    fields = dict(_TOURNAMENT_FIELD_RE.findall(content))
    if "id" not in fields:
        return None
    try:
        winner_id_str = fields.get("winner_id", "null")
        winner_id = None if winner_id_str == "null" else int(winner_id_str)
        participants = [int(p) for p in fields.get("participants", "").split(",") if p.strip()]
        matches = []
        for part in fields.get("matches", "").split(","):
            if not part.strip():
                continue
            try:
                rnd_s = part.split(":")
                rs = rnd_s[0]
                players = rnd_s[1].split("v")
                winner_str = rnd_s[2]
                round_num = int(rs[1:rs.index("S")])
                slot = int(rs[rs.index("S") + 1:])
                matches.append({
                    "round": round_num,
                    "slot": slot,
                    "player1_id": int(players[0]),
                    "player2_id": int(players[1]),
                    "winner_id": None if winner_str == "pending" else int(winner_str),
                })
            except (IndexError, ValueError):
                continue
        return {
            "id": int(fields["id"]),
            "guild_id": int(fields.get("guild_id", 0)),
            "name": fields.get("name", ""),
            "creator_id": int(fields.get("creator_id", 0)),
            "status": fields.get("status", "signup"),
            "max_players": int(fields.get("max_players", 16)),
            "winner_id": winner_id,
            "participants": participants,
            "matches": matches,
        }
    except (ValueError, KeyError):
        return None


# --- настройте под себя ---
# Ключи теперь читаются ТОЛЬКО из переменных окружения (не хранятся в коде/репозитории).
# Задайте их на хостинге так же, как DISCORD_BOT_TOKEN — см. README.md.
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "").strip()   # https://steamcommunity.com/dev/apikey (обязательно)

# Разбор матча (пункт 4) может писать текстовый комментарий через LLM.
LLM_PROVIDER = "groq"  # "groq" | "deepseek" | "none" (none = только жёсткие правила, без текста)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()     # https://console.groq.com/keys (бесплатно, без карты)
GROQ_MODEL = "llama-3.3-70b-versatile"       # актуальную модель проверьте на console.groq.com/docs/models
GROQ_API_BASE = "https://api.groq.com/openai/v1"  # OpenAI-совместимый эндпоинт

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()  # https://platform.deepseek.com/api_keys (платно)
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
DUELIST_ROLE_NAME = "⚔️ Дуэлянт"  # роль — только её обладатели могут участвовать в недельных дуэлях

# --- достижения ---
ACHIEVEMENTS = {
    "first_win":     ("🏆", "Первая победа"),
    "streak_5":      ("🔥", "Серия 5 побед"),
    "streak_10":     ("⚡", "Серия 10 побед"),
    "games_100":     ("💯", "Ветеран"),
    "games_500":     ("🏅", "Полководец"),
    "duel_win3":     ("🥊", "Боец"),
    "duel_win10":    ("👑", "Дуэлянт"),
    "wr_above_60":   ("💎", "Стратег"),
    "hero_master":   ("🎭", "Мастер героя"),
    "shards_1000":   ("✨", "Коллекционер"),
    "stream_1":      ("📺", "Стример"),
    "quest_5":       ("🐲", "Знаток"),
    "mentor":        ("🤝", "Наставник"),
    "stack_10":      ("⚡", "Командный игрок"),
    "tournament_1":  ("🏅", "Турнирный игрок"),
}

# --- виртуальная валюта (shards) ---
SHARD_WIN_MATCH = 10
SHARD_LOSS_MATCH = 2
SHARD_WIN_DUEL = 50
SHARD_DAILY_BONUS = 5
SHARD_ACHIEVEMENT = 25
SHARD_TOURNAMENT_WIN = 200
SHARD_DAILY_CAP = 500
SHARD_BET_MIN = 10
SHARD_BET_MAX = 500

DAILY_BONUS_COOLDOWN_HOURS = 24
BALANCE_BACKUP_INTERVAL_MINUTES = 5

# --- бэкап-каналы для данных, которые нельзя пересчитать ---
def _resolve_currency_backup_channel_id() -> int:
    raw = os.environ.get("CURRENCY_BACKUP_CHANNEL_ID", "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        print(f"[WARN] CURRENCY_BACKUP_CHANNEL_ID='{raw}' — не число. Бэкап экономики отключён.")
        return 0

def _resolve_tournament_backup_channel_id() -> int:
    raw = os.environ.get("TOURNAMENT_BACKUP_CHANNEL_ID", "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        print(f"[WARN] TOURNAMENT_BACKUP_CHANNEL_ID='{raw}' — не число. Бэкап турниров отключён.")
        return 0

CURRENCY_BACKUP_CHANNEL_ID = _resolve_currency_backup_channel_id()
TOURNAMENT_BACKUP_CHANNEL_ID = _resolve_tournament_backup_channel_id()

# маркеры сообщений-бэкапов
_ECONOMY_BACKUP_MARKER = "💰 ECONOMY_BACKUP"
_ACHIEVEMENT_BACKUP_MARKER = "🏅 ACHIEVEMENT_BACKUP"
_TOURNAMENT_BACKUP_MARKER = "🏆 TOURNAMENT_BACKUP"

# --- стартовые товары магазина ---
DEFAULT_SHOP_ITEMS = [
    ("Красный ник",       "Изменяет цвет роли на красный",       500,  "role_color", "#E74C3C", "🔴"),
    ("Синий ник",         "Изменяет цвет роли на синий",         500,  "role_color", "#3498DB", "🔵"),
    ("Зелёный ник",       "Изменяет цвет роли на зелёный",       500,  "role_color", "#2ECC71", "🟢"),
    ("Золотой ник",       "Изменяет цвет роли на золотой",       800,  "role_color", "#F1C40F", "🟡"),
    ("Фиолетовый ник",    "Изменяет цвет роли на фиолетовый",    700,  "role_color", "#9B59B6", "🟣"),
    ("Титул Легенда",     "Титул в профиле: Легенда",            300,  "title",      "Легенда", "👑"),
    ("Титул Воин",        "Титул в профиле: Воин",               200,  "title",      "Воин",    "⚔️"),
    ("Титул Мудрец",      "Титул в профиле: Мудрец",             250,  "title",      "Мудрец",  "🧙"),
]


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
        try:
            c.execute("ALTER TABLE players ADD COLUMN dm_muted INTEGER DEFAULT 0")
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
        # discord_id -> id сообщения в приватном канале-бэкапе (см.
        # PLAYER_BACKUP_CHANNEL_ID). Это just кэш для быстрого edit()
        # вместо поиска по всей истории канала — если строки тут нет
        # (или она "протухла", сообщение удалено), код просто ищет
        # по содержимому истории канала заново, это не критично.
        c.execute("""CREATE TABLE IF NOT EXISTS backup_messages (
            discord_id INTEGER PRIMARY KEY,
            message_id INTEGER NOT NULL
        )""")

        # --- достижения ---
        c.execute("""CREATE TABLE IF NOT EXISTS achievements (
            discord_id INTEGER NOT NULL,
            achievement_key TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            PRIMARY KEY (discord_id, achievement_key)
        )""")

        # --- виртуальная валюта ---
        c.execute("""CREATE TABLE IF NOT EXISTS currency (
            discord_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0,
            total_earned INTEGER NOT NULL DEFAULT 0,
            total_spent INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS currency_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            duel_id INTEGER NOT NULL,
            bettor_id INTEGER NOT NULL,
            bet_on_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            UNIQUE(duel_id, bettor_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            cost INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            value TEXT NOT NULL,
            emoji TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_purchases (
            discord_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            purchased_at TEXT NOT NULL,
            PRIMARY KEY (discord_id, item_id)
        )""")

        # --- турниры ---
        c.execute("""CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            creator_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'signup',
            max_players INTEGER NOT NULL DEFAULT 16,
            created_at TEXT NOT NULL,
            winner_id INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS tournament_participants (
            tournament_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            seed INTEGER,
            PRIMARY KEY (tournament_id, discord_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            player1_id INTEGER,
            player2_id INTEGER,
            winner_id INTEGER,
            duel_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending'
        )""")

        # --- seed магазина при первом запуске ---
        existing_items = c.execute("SELECT COUNT(*) FROM shop_items").fetchone()[0]
        if existing_items == 0:
            for name, desc, cost, itype, val, emoji in DEFAULT_SHOP_ITEMS:
                c.execute("INSERT INTO shop_items (name, description, cost, item_type, value, emoji) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (name, desc, cost, itype, val, emoji))

        # --- предупреждения (модерация) ---
        c.execute("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
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

    def get_dm_muted(self, discord_id: int) -> bool:
        row = self.conn.execute(
            "SELECT dm_muted FROM players WHERE discord_id=?", (discord_id,)).fetchone()
        return bool(row[0]) if row else False

    def set_dm_muted(self, discord_id: int, muted: bool):
        self.conn.execute(
            "UPDATE players SET dm_muted=? WHERE discord_id=?", (int(muted), discord_id))
        self.conn.commit()

    def get_backup_message_id(self, discord_id: int):
        row = self.conn.execute(
            "SELECT message_id FROM backup_messages WHERE discord_id=?", (discord_id,)).fetchone()
        return row[0] if row else None

    def set_backup_message_id(self, discord_id: int, message_id: int):
        self.conn.execute(
            "INSERT INTO backup_messages (discord_id, message_id) VALUES (?, ?) "
            "ON CONFLICT(discord_id) DO UPDATE SET message_id=excluded.message_id",
            (discord_id, message_id))
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

    # ==================== предупреждения ====================

    def add_warning(self, guild_id: int, discord_id: int, moderator_id: int, reason: str):
        self.conn.execute(
            "INSERT INTO warnings (guild_id, discord_id, moderator_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, discord_id, moderator_id, reason,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def get_warnings(self, guild_id: int, discord_id: int) -> list[tuple]:
        return self.conn.execute(
            "SELECT id, moderator_id, reason, created_at FROM warnings "
            "WHERE guild_id=? AND discord_id=? ORDER BY created_at DESC",
            (guild_id, discord_id)).fetchall()

    def get_warning_count(self, guild_id: int, discord_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND discord_id=?",
            (guild_id, discord_id)).fetchone()
        return row[0] if row else 0

    def remove_warning(self, warning_id: int):
        self.conn.execute("DELETE FROM warnings WHERE id=?", (warning_id,))
        self.conn.commit()

    # ==================== достижения ====================

    def grant_achievement(self, discord_id: int, key: str) -> bool:
        """Выдаёт достижение. Возвращает True если оно было новым."""
        try:
            self.conn.execute(
                "INSERT INTO achievements (discord_id, achievement_key, unlocked_at) VALUES (?, ?, ?)",
                (discord_id, key, datetime.now(timezone.utc).isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_achievements(self, discord_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT achievement_key FROM achievements WHERE discord_id=?",
            (discord_id,)).fetchall()
        return [r[0] for r in rows]

    def get_achievement_count(self, discord_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM achievements WHERE discord_id=?", (discord_id,)).fetchone()
        return row[0]

    # ==================== виртуальная валюта ====================

    def _ensure_currency_row(self, discord_id: int):
        self.conn.execute(
            "INSERT INTO currency (discord_id) VALUES (?) ON CONFLICT(discord_id) DO NOTHING",
            (discord_id,))
        self.conn.commit()

    def get_balance(self, discord_id: int) -> int:
        self._ensure_currency_row(discord_id)
        row = self.conn.execute(
            "SELECT balance FROM currency WHERE discord_id=?", (discord_id,)).fetchone()
        return row[0] if row else 0

    def add_shards(self, discord_id: int, amount: int, reason: str) -> int:
        """Начисляет shards. Возвращает новый баланс."""
        self._ensure_currency_row(discord_id)
        self.conn.execute(
            "UPDATE currency SET balance = balance + ?, total_earned = total_earned + ? "
            "WHERE discord_id=?", (amount, amount, discord_id))
        self.conn.execute(
            "INSERT INTO currency_transactions (discord_id, amount, reason, created_at) "
            "VALUES (?, ?, ?, ?)",
            (discord_id, amount, reason, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        return self.get_balance(discord_id)

    def spend_shards(self, discord_id: int, amount: int, reason: str) -> int | None:
        """Тратит shards. Возвращает новый баланс или None если недостаточно."""
        balance = self.get_balance(discord_id)
        if balance < amount:
            return None
        self.conn.execute(
            "UPDATE currency SET balance = balance - ?, total_spent = total_spent + ? "
            "WHERE discord_id=?", (amount, amount, discord_id))
        self.conn.execute(
            "INSERT INTO currency_transactions (discord_id, amount, reason, created_at) "
            "VALUES (?, ?, ?, ?)",
            (discord_id, -amount, reason, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        return self.get_balance(discord_id)

    def can_spend(self, discord_id: int, amount: int) -> bool:
        return self.get_balance(discord_id) >= amount

    def get_daily_earned(self, discord_id: int) -> int:
        """Сколько shards заработано за последние 24 часа."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS)).isoformat()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM currency_transactions "
            "WHERE discord_id=? AND amount > 0 AND created_at > ?",
            (discord_id, cutoff)).fetchone()
        return row[0] if row else 0

    def get_last_daily_bonus_time(self, discord_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT created_at FROM currency_transactions "
            "WHERE discord_id=? AND reason='daily_bonus' "
            "ORDER BY created_at DESC LIMIT 1", (discord_id,)).fetchone()
        return row[0] if row else None

    def get_transaction_history(self, discord_id: int, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT amount, reason, created_at FROM currency_transactions "
            "WHERE discord_id=? ORDER BY created_at DESC LIMIT ?",
            (discord_id, limit)).fetchall()
        return [{"amount": r[0], "reason": r[1], "created_at": r[2]} for r in rows]

    def get_all_balances(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT discord_id, balance, total_earned, total_spent FROM currency").fetchall()

    def set_balance_raw(self, discord_id: int, balance: int, total_earned: int, total_spent: int):
        self.conn.execute(
            "INSERT INTO currency (discord_id, balance, total_earned, total_spent) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(discord_id) DO UPDATE SET balance=?, total_earned=?, total_spent=?",
            (discord_id, balance, total_earned, total_spent, balance, total_earned, total_spent))
        self.conn.commit()

    # ==================== ставки ====================

    def place_bet(self, duel_id: int, guild_id: int, bettor_id: int, bet_on_id: int, amount: int) -> bool:
        """Ставка на дуэль. Возвращает True если успешно."""
        try:
            self.conn.execute(
                "INSERT INTO bets (guild_id, duel_id, bettor_id, bet_on_id, amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, duel_id, bettor_id, bet_on_id, amount,
                 datetime.now(timezone.utc).isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_bets_for_duel(self, duel_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, bettor_id, bet_on_id, amount, status FROM bets WHERE duel_id=?",
            (duel_id,)).fetchall()
        return [{"id": r[0], "bettor_id": r[1], "bet_on_id": r[2],
                 "amount": r[3], "status": r[4]} for r in rows]

    def resolve_bets(self, duel_id: int, winner_id: int):
        """Выплачивает выигрыш победителям ставок, помечает проигравших."""
        bets = self.get_bets_for_duel(duel_id)
        for bet in bets:
            if bet["status"] != "pending":
                continue
            if bet["bet_on_id"] == winner_id:
                self.conn.execute(
                    "UPDATE bets SET status='won' WHERE id=?", (bet["id"],))
                self.add_shards(bet["bettor_id"], bet["amount"], f"bet_win:{duel_id}")
            else:
                self.conn.execute(
                    "UPDATE bets SET status='lost' WHERE id=?", (bet["id"],))
        self.conn.commit()

    def void_bets(self, duel_id: int):
        """Аннулирует все ставки на дуэль (возврат средств)."""
        bets = self.get_bets_for_duel(duel_id)
        for bet in bets:
            if bet["status"] == "pending":
                self.conn.execute(
                    "UPDATE bets SET status='void' WHERE id=?", (bet["id"],))
        self.conn.commit()

    # ==================== магазин ====================

    def get_shop_items(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, description, cost, item_type, value, emoji FROM shop_items").fetchall()
        return [{"id": r[0], "name": r[1], "description": r[2], "cost": r[3],
                 "item_type": r[4], "value": r[5], "emoji": r[6]} for r in rows]

    def get_shop_item(self, item_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, description, cost, item_type, value, emoji "
            "FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "description": row[2], "cost": row[3],
                "item_type": row[4], "value": row[5], "emoji": row[6]}

    def buy_item(self, discord_id: int, item_id: int) -> bool:
        item = self.get_shop_item(item_id)
        if not item:
            return False
        if not self.can_spend(discord_id, item["cost"]):
            return False
        try:
            self.conn.execute(
                "INSERT INTO user_purchases (discord_id, item_id, purchased_at) VALUES (?, ?, ?)",
                (discord_id, item_id, datetime.now(timezone.utc).isoformat()))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass
        self.spend_shards(discord_id, item["cost"], f"shop:{item['name']}")
        return True

    def get_user_purchases(self, discord_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT up.item_id, si.name, si.item_type, si.value, si.emoji "
            "FROM user_purchases up JOIN shop_items si ON up.item_id = si.id "
            "WHERE up.discord_id=?", (discord_id,)).fetchall()
        return [{"item_id": r[0], "name": r[1], "item_type": r[2],
                 "value": r[3], "emoji": r[4]} for r in rows]

    def get_user_titles(self, discord_id: int) -> list[str]:
        purchases = self.get_user_purchases(discord_id)
        return [p["value"] for p in purchases if p["item_type"] == "title"]

    def get_user_color(self, discord_id: int) -> str | None:
        purchases = self.get_user_purchases(discord_id)
        colors = [p["value"] for p in purchases if p["item_type"] == "role_color"]
        return colors[-1] if colors else None

    # ==================== турниры ====================

    def create_tournament(self, guild_id: int, name: str, creator_id: int,
                           max_players: int = 16) -> int | None:
        try:
            cur = self.conn.execute(
                "INSERT INTO tournaments (guild_id, name, creator_id, max_players, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (guild_id, name, creator_id, max_players,
                 datetime.now(timezone.utc).isoformat()))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def get_tournament(self, tournament_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)).description]
        return dict(zip(cols, row))

    def get_active_tournament(self, guild_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tournaments WHERE guild_id=? AND status IN ('signup', 'in_progress') "
            "ORDER BY id DESC LIMIT 1", (guild_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tournaments WHERE guild_id=? AND status IN ('signup', 'in_progress') "
            "ORDER BY id DESC LIMIT 1", (guild_id,)).description]
        return dict(zip(cols, row))

    def join_tournament(self, tournament_id: int, discord_id: int) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO tournament_participants (tournament_id, discord_id) VALUES (?, ?)",
                (tournament_id, discord_id))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def leave_tournament(self, tournament_id: int, discord_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM tournament_participants WHERE tournament_id=? AND discord_id=?",
            (tournament_id, discord_id))
        self.conn.commit()
        return cur.rowcount > 0

    def get_tournament_participants(self, tournament_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT discord_id FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,)).fetchall()
        return [r[0] for r in rows]

    def get_tournament_participant_count(self, tournament_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=?",
            (tournament_id,)).fetchone()
        return row[0]

    def set_tournament_status(self, tournament_id: int, status: str):
        self.conn.execute(
            "UPDATE tournaments SET status=? WHERE id=?", (status, tournament_id))
        self.conn.commit()

    def set_tournament_winner(self, tournament_id: int, winner_id: int):
        self.conn.execute(
            "UPDATE tournaments SET winner_id=?, status='finished' WHERE id=?",
            (winner_id, tournament_id))
        self.conn.commit()

    def set_tournament_seeds(self, tournament_id: int, seeds: dict[int, int]):
        """seeds: {discord_id: seed_number}"""
        for discord_id, seed in seeds.items():
            self.conn.execute(
                "UPDATE tournament_participants SET seed=? "
                "WHERE tournament_id=? AND discord_id=?",
                (seed, tournament_id, discord_id))
        self.conn.commit()

    def create_tournament_match(self, tournament_id: int, round_num: int,
                                 slot: int, player1_id: int, player2_id: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO tournament_matches "
            "(tournament_id, round, slot, player1_id, player2_id, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (tournament_id, round_num, slot, player1_id, player2_id))
        self.conn.commit()
        return cur.lastrowid

    def get_tournament_match(self, match_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tournament_matches WHERE id=?", (match_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tournament_matches WHERE id=?", (match_id,)).description]
        return dict(zip(cols, row))

    def get_tournament_matches_by_round(self, tournament_id: int, round_num: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? AND round=? "
            "ORDER BY slot", (tournament_id, round_num)).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? AND round=? "
            "ORDER BY slot", (tournament_id, round_num)).description]
        return [dict(zip(cols, r)) for r in rows]

    def get_tournament_matches(self, tournament_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? ORDER BY round, slot",
            (tournament_id,)).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? ORDER BY round, slot",
            (tournament_id,)).description]
        return [dict(zip(cols, r)) for r in rows]

    def set_tournament_match_winner(self, match_id: int, winner_id: int):
        self.conn.execute(
            "UPDATE tournament_matches SET winner_id=?, status='finished' WHERE id=?",
            (winner_id, match_id))
        self.conn.commit()

    def set_tournament_match_duel(self, match_id: int, duel_id: int):
        self.conn.execute(
            "UPDATE tournament_matches SET duel_id=? WHERE id=?",
            (duel_id, match_id))
        self.conn.commit()

    def all_tournaments_by_status(self, statuses: list[str], guild_id: int | None = None) -> list[dict]:
        if guild_id:
            q = f"SELECT * FROM tournaments WHERE guild_id=? AND status IN ({','.join('?' * len(statuses))})"
            rows = self.conn.execute(q, (guild_id, *statuses)).fetchall()
        else:
            q = f"SELECT * FROM tournaments WHERE status IN ({','.join('?' * len(statuses))})"
            rows = self.conn.execute(q, statuses).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM tournaments LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]


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

    async def matchups(self, hero_id: int):
        return await self.get(f"/heroes/{hero_id}/matchups") or []

    async def player_hero_stats(self, account_id: int, limit: int = 100) -> list[dict]:
        """Агрегированная статистика по героям из последних матчей игрока.
        Возвращает список словарей, отсортированных по количеству игр."""
        matches = await self.get(f"/players/{account_id}/matches?limit={limit}") or []
        if not matches:
            return []
        heroes: dict[int, dict] = {}
        for m in matches:
            hid = m.get("hero_id")
            if not hid:
                continue
            won = (m.get("player_slot", 0) < 128) == m.get("radiant_win")
            k = m.get("kills", 0)
            d = m.get("deaths", 0)
            a = m.get("assists", 0)
            if hid not in heroes:
                heroes[hid] = {"hero_id": hid, "games": 0, "wins": 0,
                               "total_kills": 0, "total_deaths": 0, "total_assists": 0}
            h = heroes[hid]
            h["games"] += 1
            if won:
                h["wins"] += 1
            h["total_kills"] += k
            h["total_deaths"] += d
            h["total_assists"] += a
        result = []
        for h in heroes.values():
            g = h["games"]
            result.append({
                "hero_id": h["hero_id"],
                "hero_name": await self.hero_name(h["hero_id"]),
                "games": g,
                "wins": h["wins"],
                "wr": h["wins"] / g * 100 if g else 0,
                "avg_kda": f"{h['total_kills']/g:.1f}/{h['total_deaths']/g:.1f}/{h['total_assists']/g:.1f}" if g else "0/0/0",
            })
        result.sort(key=lambda x: x["games"], reverse=True)
        return result

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
db = Storage(DB_PATH)  # единый экземпляр БД для всех cog'ов


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
                if DEBUG_LOG:
                    body = await r.text()
                    key_hint = "ПУСТОЙ" if not self.api_key else f"задан, длина {len(self.api_key)}"
                    print(f"[STEAM] GetPlayerSummaries вернул статус {r.status}, ключ: {key_hint}, "
                          f"ответ: {body[:300]}")
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

    # Проверка вардов: только если ОБА поля точно есть и оба == 0,
    # при этом матч dàiше 25 минут и герой НЕ carry/mid (варды — ответственность саппортов/офлейнеров)
    hero_id_int = int(facts.get("hero_id", 0)) if facts.get("hero_id") else 0
    # Список hero_id для кэрри/мида (обычно не ставят варды)
    carry_mid_hero_ids = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 18, 19, 22, 23, 24, 25, 26, 27, 28, 29, 34, 35, 36, 38, 40, 41, 42, 44, 46, 48, 50, 51, 52, 54, 55, 56, 57, 58, 59, 62, 63, 65, 67, 69, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 119, 120, 121, 123}
    is_carry_mid = hero_id_int in carry_mid_hero_ids
    if (not is_carry_mid and facts["duration_min"] > 25
            and facts["obs_placed"] == 0 and facts["sen_placed"] == 0):
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


    async def self_test(self) -> None:
        """Разовая проверка ключа при старте бота — чтобы не ждать реального
        матча, чтобы узнать, работает ли LLM. Печатает результат в консоль."""
        if self.provider == "none":
            print("[LLM] LLM_PROVIDER = \"none\" — текстовый разбор отключён, это ок.")
            return
        key = GROQ_API_KEY if self.provider == "groq" else DEEPSEEK_API_KEY
        if not key:
            print(f"[LLM] ⚠️ LLM_PROVIDER = \"{self.provider}\", но соответствующий "
                  f"ключ пустой — переменная окружения не задана или .env не подхватился.")
            return
        masked = f"{key[:4]}...{key[-4:]} (длина {len(key)})" if len(key) > 8 else "слишком короткий, подозрительно"
        result = await self.write_review(
            {"hero": "Test", "won": True, "kills": 0, "deaths": 0, "assists": 0,
             "duration_min": 1, "gpm": 0, "gpm_median": 0, "xpm": 0, "xpm_median": 0,
             "lh_per_min": 0, "lh_per_min_median": 0, "hero_damage": 0, "tower_damage": 0,
             "hero_healing": 0, "obs_placed": 0, "sen_placed": 0, "stages": []},
            ["тестовый запрос при старте бота"])
        if result:
            print(f"[LLM] ✅ Провайдер \"{self.provider}\" работает, ключ: {masked}")
        else:
            print(f"[LLM] ❌ Провайдер \"{self.provider}\" НЕ отвечает корректно, ключ: {masked}. "
                  f"Смотрите строку выше со статусом ответа API — там точная причина "
                  f"(401 = сам ключ неверный/отозван, не проблема кода).")


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

        # Дублируем привязку в приватный канал-бэкап (если он настроен) —
        # это и есть "источник правды" на случай потери локальной SQLite.
        # Делаем это до ответа пользователю, но не даём сбою бэкапа
        # сломать саму привязку: она уже сохранена в SQLite выше.
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        if cog:
            try:
                await cog.backup_player_to_channel(interaction.user.id, account_id, steam_id64)
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[BACKUP] не удалось записать привязку {interaction.user.id} в канал-бэкап: {e!r}")

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


# ---------------- модалки для валюты, контр-пиков, сравнения ----------------

class BetModal(discord.ui.Modal, title="Сделать ставку"):
    amount = discord.ui.TextInput(
        label=f"Сумма ставки (min {SHARD_BET_MIN}, max {SHARD_BET_MAX})",
        placeholder="напр. 50", max_length=10)

    def __init__(self, db: Storage, duel_id: int, bet_on_id: int):
        super().__init__()
        self.db = db
        self.duel_id = duel_id
        self.bet_on_id = bet_on_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount.value))
        except ValueError:
            await interaction.response.send_message("Введите число.", ephemeral=True)
            return
        if amount < SHARD_BET_MIN:
            await interaction.response.send_message(f"Минимум {SHARD_BET_MIN} shards.", ephemeral=True)
            return
        if amount > SHARD_BET_MAX:
            await interaction.response.send_message(f"Максимум {SHARD_BET_MAX} shards.", ephemeral=True)
            return
        if self.bet_on_id == interaction.user.id:
            await interaction.response.send_message("Нельзя ставить на себя.", ephemeral=True)
            return
        if not self.db.can_spend(interaction.user.id, amount):
            balance = self.db.get_balance(interaction.user.id)
            await interaction.response.send_message(
                f"Недостаточно shards. Баланс: {balance}.", ephemeral=True)
            return
        self.db.spend_shards(interaction.user.id, amount, f"bet:{self.duel_id}")
        placed = self.db.place_bet(
            self.duel_id, interaction.guild.id, interaction.user.id,
            self.bet_on_id, amount)
        if not placed:
            await interaction.response.send_message("Не удалось поставить ставку.", ephemeral=True)
            return
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        if cog:
            cog._dirty_economy.add(interaction.user.id)
        await interaction.response.send_message(
            f"💰 Ставка {amount} shards на <@{self.bet_on_id}> принята!", ephemeral=True)


class ShopModal(discord.ui.Modal, title="Магазин Shards"):
    item_id = discord.ui.TextInput(
        label="ID товара (число из списка ниже)",
        placeholder="напр. 1", max_length=5)

    def __init__(self, db: Storage):
        super().__init__()
        self.db = db

    async def on_submit(self, interaction: discord.Interaction):
        try:
            iid = int(str(self.item_id.value))
        except ValueError:
            await interaction.response.send_message("Введите число.", ephemeral=True)
            return
        item = self.db.get_shop_item(iid)
        if not item:
            await interaction.response.send_message("Товар не найден.", ephemeral=True)
            return
        balance = self.db.get_balance(interaction.user.id)
        if balance < item["cost"]:
            await interaction.response.send_message(
                f"Недостаточно shards. Нужно: {item['cost']}, у вас: {balance}.", ephemeral=True)
            return
        success = self.db.buy_item(interaction.user.id, iid)
        if not success:
            await interaction.response.send_message("Ошибка покупки.", ephemeral=True)
            return
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        if cog:
            cog._dirty_economy.add(interaction.user.id)
        await interaction.response.send_message(
            f"✅ Куплено: {item['emoji'] or ''} **{item['name']}** за {item['cost']} shards!", ephemeral=True)


class CounterPickModal(discord.ui.Modal, title="Контр-пик советник"):
    hero = discord.ui.TextInput(label="Имя героя (ваш)", placeholder="напр. Pudge")

    async def on_submit(self, interaction: discord.Interaction):
        hero_id = await od.find_hero_id(str(self.hero.value))
        if not hero_id:
            await interaction.response.send_message("Герой не найден.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        matchups = await od.matchups(hero_id)
        if not matchups:
            await interaction.followup.send("Нет данных по матчапам для этого героя.", ephemeral=True)
            return
        hero_name = await od.hero_name(hero_id)
        rows = []
        for m in matchups:
            games = m.get("games_played", 0)
            if games < 100:
                continue
            wr = m.get("wins", 0) / games * 100
            name = await od.hero_name(m["hero_id"])
            rows.append((name, wr, games))
        rows.sort(key=lambda x: x[1])
        embed = discord.Embed(
            title=f"🛡 Контр-пики для {hero_name}",
            description="Худшие матчапы (самый низкий WR — опасные герои):",
            color=0x8B4513)
        lines = []
        for name, wr, games in rows[:10]:
            bar_len = max(1, int((100 - wr) / 5))
            bar = "🟥" * bar_len + "⬜" * (20 - bar_len)
            lines.append(f"**{name}** — WR {wr:.1f}% ({games} игр)\n`{bar}`")
        embed.add_field(name="Худшие противники", value="\n".join(lines) or "Недостаточно данных",
                         inline=False)
        recs = await od.item_recommendations(hero_id, per_phase=3)
        for phase, items in recs.items():
            embed.add_field(name=f"🛒 {phase}", value="\n".join(items), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)


class CompareModal(discord.ui.Modal, title="Сравнить двух игроков"):
    player1 = discord.ui.TextInput(
        label="Игрок 1 (SteamID или @упоминание)", placeholder="напр. 76561198012345678", max_length=40)
    player2 = discord.ui.TextInput(
        label="Игрок 2 (SteamID или @упоминание)", placeholder="напр. 76561198098765432", max_length=40)

    def __init__(self, db: Storage):
        super().__init__()
        self.db = db

    async def _resolve_account(self, raw: str) -> int | None:
        raw = raw.strip()
        if raw.startswith("<@") and raw.endswith(">"):
            mention_id = int(raw[2:-1].replace("!", ""))
            return self.db.get_account_id(mention_id)
        try:
            return to_account_id(raw)
        except ValueError:
            return None

    async def on_submit(self, interaction: discord.Interaction):
        acc1 = await self._resolve_account(str(self.player1.value))
        acc2 = await self._resolve_account(str(self.player2.value))
        if not acc1 or not acc2:
            await interaction.response.send_message(
                "Не удалось найти одного из игроков. Укажите SteamID или @упоминание.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        p1, wl1, recent1 = await asyncio.gather(
            od.get(f"/players/{acc1}"), od.get(f"/players/{acc1}/wl"),
            od.get(f"/players/{acc1}/recentMatches"))
        p2, wl2, recent2 = await asyncio.gather(
            od.get(f"/players/{acc2}"), od.get(f"/players/{acc2}/wl"),
            od.get(f"/players/{acc2}/recentMatches"))
        n1 = (p1 or {}).get("profile", {}).get("personaname", f"ID:{acc1}")
        n2 = (p2 or {}).get("profile", {}).get("personaname", f"ID:{acc2}")

        def _stats(wl_data, recent_data):
            w = (wl_data or {}).get("win", 0)
            l = (wl_data or {}).get("lose", 0)
            total = w + l
            wr = f"{w/total*100:.1f}%" if total else "N/A"
            rank = (wl_data or {}).get("rank_tier")
            rank_name = RANK_TIER_NAMES.get(rank // 10, "N/A") if rank else "N/A"
            return {"wr": wr, "games": total, "rank": rank_name}

        s1 = _stats(wl1, recent1)
        s2 = _stats(wl2, recent2)

        embed = discord.Embed(title="⚔️ Сравнение игроков", color=0x8B4513)
        embed.add_field(name="Параметр", value="Ранг\nWR\nМатчей", inline=True)
        embed.add_field(name=n1[:50], value=f"{s1['rank']}\n{s1['wr']}\n{s1['games']}", inline=True)
        embed.add_field(name=n2[:50], value=f"{s2['rank']}\n{s2['wr']}\n{s2['games']}", inline=True)

        def _last_hero(recent_data):
            if not recent_data:
                return "нет данных"
            m = recent_data[0]
            hero = m.get("hero_id", 0)
            won = (m.get("player_slot", 0) < 128) == m.get("radiant_win")
            return f"{'✅' if won else '❌'} {hero}"

        embed.set_footer(text=f"Последний матч: {_last_hero(recent1)} vs {_last_hero(recent2)}")
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- persistent views — по каналам ----------------

REGISTER_CHANNEL = "🔐-ВЕРИФИКАЦИЯ"
WELCOME_CHANNEL = "👋-приветствия"
LEADERBOARD_CHANNEL_DASH = "🏆-лидерборд"
STRATEGY_CHANNEL = "🧠-стратегия"
SHOP_CHANNEL = "🛒-магазин"


class RegisterView(discord.ui.View):
    """Кнопка привязки SteamID — в канале верификации."""
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Привязать SteamID", emoji="🔗",
                        style=discord.ButtonStyle.primary, custom_id="dota:register")
    async def register_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RegisterModal(self.db))


class ProfileView(discord.ui.View):
    """Профиль + уведомления — в канале приветствий."""
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Профиль", emoji="📊", style=discord.ButtonStyle.secondary,
                        custom_id="dota:profile")
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
            embed.add_field(name="🥊 Дуэли", value=f"{duel_wins}W / {duel_losses}L")
        if recent:
            last = recent[0]
            hero = await od.hero_name(last["hero_id"])
            won = (last["player_slot"] < 128) == last["radiant_win"]
            embed.add_field(name="Последний матч",
                             value=f"{hero} — {'Победа' if won else 'Поражение'}", inline=False)
        balance = self.db.get_balance(interaction.user.id)
        embed.add_field(name="💎 Shards", value=str(balance), inline=True)
        achievements = self.db.get_achievements(interaction.user.id)
        total_achievements = len(ACHIEVEMENTS)
        if achievements:
            emojis = " ".join(ACHIEVEMENTS.get(a, ("❓",))[0] for a in achievements if a in ACHIEVEMENTS)
            embed.add_field(name=f"🏅 Достижения ({len(achievements)}/{total_achievements})",
                             value=emojis or "пока нет", inline=False)
        hero_stats = await od.player_hero_stats(account_id, limit=100)
        if hero_stats:
            lines = []
            for i, h in enumerate(hero_stats[:3], 1):
                lines.append(f"**{i}. {h['hero_name']}** — {h['games']} игр, "
                             f"{h['wins']}W ({h['wr']:.1f}%), KDA {h['avg_kda']}")
            embed.add_field(name="🎭 Топ герои", value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Уведомления", emoji="🔔", style=discord.ButtonStyle.secondary,
                        custom_id="dota:dm_toggle")
    async def dm_toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        muted = self.db.get_dm_muted(interaction.user.id)
        self.db.set_dm_muted(interaction.user.id, not muted)
        status = "выключены 🔇" if not muted else "включены 🔔"
        embed = discord.Embed(
            title="Настройки уведомлений",
            description=f"Рассылки бота (матч-ревью) теперь **{status}**.",
            color=0x8B4513)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CompetitionView(discord.ui.View):
    """Лидерборд + дуэли + турнир — в канале лидерборда."""
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Лидерборд", emoji="🏆", style=discord.ButtonStyle.success,
                        custom_id="dota:leaderboard")
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        rows = await cog.compute_weekly_leaderboard(interaction.guild)
        lines = [f"**{i}.** {name} — {games} игр, {wins}W, WR {wr:.1f}%"
                 for i, (_, name, games, wins, wr) in enumerate(rows[:10], 1)]
        embed = discord.Embed(
            title=f"🏆 Лидерборд — {interaction.guild.name}",
            description="\n".join(lines) or "Нет данных за последние 7 дней",
            color=0x8B4513)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Дуэли", emoji="🥊", style=discord.ButtonStyle.success,
                        custom_id="dota:duel_top")
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
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Турнир", emoji="⚔️", style=discord.ButtonStyle.success,
                        custom_id="dota:tournament")
    async def tournament_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.db.get_active_tournament(interaction.guild.id)
        embed = discord.Embed(title="🏆 Турнир", color=0x8B4513)
        if t:
            participants = self.db.get_tournament_participant_count(t["id"])
            embed.description = (
                f"**{t['name']}**\n"
                f"Статус: `{t['status']}`\n"
                f"Участников: {participants}/{t['max_players']}"
            )
            if t["status"] == "in_progress":
                matches = self.db.get_tournament_matches(t["id"])
                unfinished = [m for m in matches if m["status"] != "finished"]
                embed.add_field(name="Матчи", value=f"Осталось: {len(unfinished)}", inline=True)
                embed.add_field(name="Раунд", value=f"Текущий: {max((m['round'] for m in matches), default=1)}", inline=True)
            elif t["winner_id"]:
                member = interaction.guild.get_member(t["winner_id"])
                name = member.display_name if member else f"<@{t['winner_id']}>"
                embed.add_field(name="Победитель", value=name, inline=False)
        else:
            embed.description = "Активного турнира нет. Создайте новый!"
        view = TournamentHubView(self.db, interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class StrategyView(discord.ui.View):
    """Мета + контр-пики + сравнение + разбор — в канале стратегии."""
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Мета героев", emoji="🔥", style=discord.ButtonStyle.secondary,
                        custom_id="dota:meta")
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
        embed = discord.Embed(title="🔥 Топ-10 героев по пикрейту", description="\n".join(lines), color=0x8B4513)
        trend = await od.item_trend()
        if trend:
            item_lines = [f"**{name}** — {count}" for name, count in trend[:5]]
            embed.add_field(name="📦 Топ предметы", value="\n".join(item_lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Контр-пики", emoji="🛡", style=discord.ButtonStyle.secondary,
                        custom_id="dota:counterpick")
    async def counterpick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CounterPickModal())

    @discord.ui.button(label="Сравнить", emoji="⚔️", style=discord.ButtonStyle.secondary,
                        custom_id="dota:compare")
    async def compare_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CompareModal(self.db))

    @discord.ui.button(label="Разбор игры", emoji="📋", style=discord.ButtonStyle.secondary,
                        custom_id="dota:last_review")
    async def last_review_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        account_id = self.db.get_account_id(interaction.user.id)
        if not account_id:
            await interaction.response.send_message("Сначала привяжите SteamID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        recent = await od.get(f"/players/{account_id}/recentMatches")
        if not recent:
            await interaction.followup.send("Не нашёл матчей в истории OpenDota.", ephemeral=True)
            return
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        embed = await cog.build_match_review(interaction.user.id, account_id, recent[0]["match_id"])
        if not embed:
            await interaction.followup.send(
                "Не получилось собрать разбор. Попробуйте позже.", ephemeral=True)
            return
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------- 📊 аналитика патчей ----------------

def _trend_wr(hero: dict) -> list[float]:
    """Возвращает список win-rate для каждого из последних 7 патчей (pub ranked)."""
    picks = hero.get("pub_pick_trend", [])
    wins = hero.get("pub_win_trend", [])
    return [
        (w / p * 100) if p else 0.0
        for p, w in zip(picks, wins)
    ]


def _trend_pr(hero: dict, total_picks_per_patch: list[int]) -> list[float]:
    """Возвращает pick-rate (%) героя относительно общего числа пиков за каждый патч."""
    picks = hero.get("pub_pick_trend", [])
    return [
        (p / t * 100) if t else 0.0
        for p, t in zip(picks, total_picks_per_patch)
    ]


class PatchAnalyticsView(discord.ui.View):
    """Аналитика патчей — победители, проигравшие, растущие/падающие герои."""
    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    def _compute_hero_deltas(stats: list[dict]):
        """Считает дельты win-rate и pick-rate между двумя последними патчами."""
        total_picks = [0] * 7
        for h in stats:
            for i, p in enumerate(h.get("pub_pick_trend", [0] * 7)):
                total_picks[i] += p

        results = []
        for h in stats:
            wr = _trend_wr(h)
            pr = _trend_pr(h, total_picks)
            if len(wr) < 2 or len(pr) < 2:
                continue
            wr_delta = wr[-1] - wr[-2]
            pr_delta = pr[-1] - pr[-2]
            pk_now = h.get("pub_pick_trend", [0])[-1]
            if pk_now < 100:
                continue
            results.append({
                "name": h["localized_name"],
                "img": h.get("img", ""),
                "wr_now": wr[-1],
                "wr_prev": wr[-2],
                "wr_delta": wr_delta,
                "pr_now": pr[-1],
                "pr_prev": pr[-2],
                "pr_delta": pr_delta,
                "pk_now": pk_now,
            })
        return results

    @discord.ui.button(label="Победители", emoji="🏆", style=discord.ButtonStyle.success,
                        custom_id="dota:patch_winners")
    async def winners_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        stats = await od.hero_stats()
        deltas = self._compute_hero_deltas(stats)
        top = sorted(deltas, key=lambda x: x["wr_delta"], reverse=True)[:10]
        lines = []
        for i, d in enumerate(top, 1):
            sign = "+" if d["wr_delta"] >= 0 else ""
            lines.append(
                f"**{i}. {d['name']}** — WR {d['wr_now']:.1f}% "
                f"({sign}{d['wr_delta']:.1f}%), пиков {d['pk_now']}")
        embed = discord.Embed(
            title="🏆 Топ победители патча (рост WR)",
            description="\n".join(lines) or "Нет данных",
            color=0x2ECC71)
        embed.set_footer(text="Сравнение: текущий vs предыдущий патч (публичные ранговые)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Проигравшие", emoji="📉", style=discord.ButtonStyle.danger,
                        custom_id="dota:patch_losers")
    async def losers_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        stats = await od.hero_stats()
        deltas = self._compute_hero_deltas(stats)
        top = sorted(deltas, key=lambda x: x["wr_delta"])[:10]
        lines = []
        for i, d in enumerate(top, 1):
            sign = "+" if d["wr_delta"] >= 0 else ""
            lines.append(
                f"**{i}. {d['name']}** — WR {d['wr_now']:.1f}% "
                f"({sign}{d['wr_delta']:.1f}%), пиков {d['pk_now']}")
        embed = discord.Embed(
            title="📉 Топ проигравшие патча (падение WR)",
            description="\n".join(lines) or "Нет данных",
            color=0xE74C3C)
        embed.set_footer(text="Сравнение: текущий vs предыдущий патч (публичные ранговые)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Растущие", emoji="📈", style=discord.ButtonStyle.secondary,
                        custom_id="dota:patch_rising")
    async def rising_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        stats = await od.hero_stats()
        deltas = self._compute_hero_deltas(stats)
        top = sorted(deltas, key=lambda x: x["pr_delta"], reverse=True)[:10]
        lines = []
        for i, d in enumerate(top, 1):
            sign = "+" if d["pr_delta"] >= 0 else ""
            lines.append(
                f"**{i}. {d['name']}** — pick-rate {d['pr_now']:.2f}% "
                f"({sign}{d['pr_delta']:.2f}%), WR {d['wr_now']:.1f}%")
        embed = discord.Embed(
            title="📈 Растущие герои (рост пикрейта)",
            description="\n".join(lines) or "Нет данных",
            color=0x3498DB)
        embed.set_footer(text="Сравнение: текущий vs предыдущий патч (публичные ранговые)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Падающие", emoji="⬇️", style=discord.ButtonStyle.secondary,
                        custom_id="dota:patch_falling")
    async def falling_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        stats = await od.hero_stats()
        deltas = self._compute_hero_deltas(stats)
        top = sorted(deltas, key=lambda x: x["pr_delta"])[:10]
        lines = []
        for i, d in enumerate(top, 1):
            sign = "+" if d["pr_delta"] >= 0 else ""
            lines.append(
                f"**{i}. {d['name']}** — pick-rate {d['pr_now']:.2f}% "
                f"({sign}{d['pr_delta']:.2f}%), WR {d['wr_now']:.1f}%")
        embed = discord.Embed(
            title="⬇️ Падающие герои (снижение пикрейта)",
            description="\n".join(lines) or "Нет данных",
            color=0x95A5A6)
        embed.set_footer(text="Сравнение: текущий vs предыдущий патч (публичные ранговые)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Текущая мета", emoji="🔥", style=discord.ButtonStyle.primary,
                        custom_id="dota:patch_meta")
    async def meta_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        stats = await od.hero_stats()
        total_picks = [0] * 7
        for h in stats:
            for i, p in enumerate(h.get("pub_pick_trend", [0] * 7)):
                total_picks[i] += p
        heroes = []
        for h in stats:
            pk = h.get("pub_pick_trend", [0])[-1]
            if pk < 200:
                continue
            wr = _trend_wr(h)
            pr = _trend_pr(h, total_picks)
            heroes.append({
                "name": h["localized_name"],
                "pk": pk,
                "wr": wr[-1] if wr else 0,
                "pr": pr[-1] if pr else 0,
            })
        top_wr = sorted(heroes, key=lambda x: x["wr"], reverse=True)[:5]
        top_pk = sorted(heroes, key=lambda x: x["pk"], reverse=True)[:5]
        lines_wr = []
        for i, d in enumerate(top_wr, 1):
            lines_wr.append(f"**{i}. {d['name']}** — WR {d['wr']:.1f}%, пиков {d['pk']}")
        lines_pk = []
        for i, d in enumerate(top_pk, 1):
            lines_pk.append(f"**{i}. {d['name']}** — WR {d['wr']:.1f}%, пиков {d['pk']}")
        embed = discord.Embed(title="🔥 Текущая мета", color=0xE67E22)
        embed.add_field(name="🏆 Топ по Win Rate", value="\n".join(lines_wr) or "—", inline=True)
        embed.add_field(name="🎯 Топ по Pick Rate", value="\n".join(lines_pk) or "—", inline=True)
        embed.set_footer(text="Публичные ранговые матчи, текущий патч")
        await interaction.followup.send(embed=embed, ephemeral=True)


class ShopView(discord.ui.View):
    """Shards + бонус + магазин — в канале магазина."""
    def __init__(self, db: Storage):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="Shards", emoji="💎", style=discord.ButtonStyle.success,
                        custom_id="dota:balance")
    async def balance_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        db = cog.db
        balance = db.get_balance(interaction.user.id)
        history = db.get_transaction_history(interaction.user.id, limit=5)
        embed = discord.Embed(title="💎 Shards", color=0x8B4513)
        embed.add_field(name="Баланс", value=f"**{balance}** shards", inline=True)
        if history:
            lines = []
            for t in history:
                sign = "+" if t["amount"] > 0 else ""
                lines.append(f"{sign}{t['amount']} — {t['reason']}")
            embed.add_field(name="История", value="\n".join(lines), inline=False)
        last_bonus = db.get_last_daily_bonus_time(interaction.user.id)
        can_claim = True
        if last_bonus:
            last_dt = datetime.fromisoformat(last_bonus)
            if datetime.now(timezone.utc) - last_dt < timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS):
                can_claim = False
        view = BalanceActionsView(db, can_claim)
        await interaction.response.send_message(embed=embed, ephemeral=True, view=view)

    @discord.ui.button(label="Магазин", emoji="🛒", style=discord.ButtonStyle.primary,
                        custom_id="dota:shop_hub")
    async def shop_hub_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        db = cog.db
        items = db.get_shop_items()
        balance = db.get_balance(interaction.user.id)
        embed = discord.Embed(title="🛒 Магазин", color=0x8B4513)
        embed.set_footer(text=f"Ваш баланс: {balance} shards")
        lines = []
        for item in items:
            can_buy = "✅" if balance >= item["cost"] else "❌"
            lines.append(f"{can_buy} **{item['id']}.** {item['emoji'] or ''} {item['name']} "
                         f"— {item['cost']} shards\n   {item['description']}")
        embed.description = "\n".join(lines)
        embed.add_field(name="Как купить", value="Введите ID товара в модалке ниже", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True, view=ShopBuyView(db))


class BalanceActionsView(discord.ui.View):
    def __init__(self, db: Storage, can_claim_daily: bool):
        super().__init__(timeout=60)
        self.db = db

    @discord.ui.button(label="📅 Забрать бонус", emoji="📅", style=discord.ButtonStyle.success,
                        custom_id="dota:daily_bonus")
    async def daily_bonus_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        db = cog.db
        last_bonus = db.get_last_daily_bonus_time(interaction.user.id)
        if last_bonus:
            last_dt = datetime.fromisoformat(last_bonus)
            if datetime.now(timezone.utc) - last_dt < timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS):
                remaining = timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS) - (datetime.now(timezone.utc) - last_dt)
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                await interaction.response.send_message(
                    f"Бонус уже получен. Следующий через {hours}ч {minutes}м.", ephemeral=True)
                return
        new_bal = db.add_shards(interaction.user.id, SHARD_DAILY_BONUS, "daily_bonus")
        cog._dirty_economy.add(interaction.user.id)
        await interaction.response.send_message(
            f"📅 +{SHARD_DAILY_BONUS} shards! Баланс: {new_bal}", ephemeral=True)

    @discord.ui.button(label="🛒 Магазин", emoji="🛒", style=discord.ButtonStyle.primary,
                        custom_id="dota:shop")
    async def shop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        db = cog.db
        items = db.get_shop_items()
        balance = db.get_balance(interaction.user.id)
        embed = discord.Embed(title="🛒 Магазин", color=0x8B4513)
        embed.set_footer(text=f"Ваш баланс: {balance} shards")
        lines = []
        for item in items:
            can_buy = "✅" if balance >= item["cost"] else "❌"
            lines.append(f"{can_buy} **{item['id']}.** {item['emoji'] or ''} {item['name']} "
                         f"— {item['cost']} shards\n   {item['description']}")
        embed.description = "\n".join(lines)
        embed.add_field(name="Как купить", value="Введите ID товара в модалке ниже", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True, view=ShopBuyView(db))

    @discord.ui.button(label="Назад", emoji="↩️", style=discord.ButtonStyle.secondary,
                        custom_id="dota:balance_back")
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()
        await interaction.response.defer()


class ShopBuyView(discord.ui.View):
    def __init__(self, db: Storage):
        super().__init__(timeout=60)
        self.db = db

    @discord.ui.button(label="Купить товар", emoji="🛒", style=discord.ButtonStyle.success,
                        custom_id="dota:shop_buy")
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ShopModal(self.db))


class TournamentHubView(discord.ui.View):
    def __init__(self, db: Storage, guild_id: int):
        super().__init__(timeout=60)
        self.db = db
        self.guild_id = guild_id

    @discord.ui.button(label="Создать турнир", emoji="🆕", style=discord.ButtonStyle.success)
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TournamentCreateModal(self.db))

    @discord.ui.button(label="Записаться", emoji="🎯", style=discord.ButtonStyle.primary)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        t = self.db.get_active_tournament(self.guild_id)
        if not t:
            await interaction.response.send_message("Нет активного турнира. Сначала создайте.", ephemeral=True)
            return
        if t["status"] != "signup":
            await interaction.response.send_message("Запись закрыта.", ephemeral=True)
            return
        count = self.db.get_tournament_participant_count(t["id"])
        if count >= t["max_players"]:
            await interaction.response.send_message("Турнир заполнен.", ephemeral=True)
            return
        success = self.db.join_tournament(t["id"], interaction.user.id)
        if not success:
            await interaction.response.send_message("Вы уже записаны.", ephemeral=True)
            return
        self.db.conn.commit()
        cog._dirty_tournaments.add(t["id"])
        new_count = self.db.get_tournament_participant_count(t["id"])
        await interaction.response.send_message(
            f"✅ Записаны на **{t['name']}**! ({new_count}/{t['max_players']})", ephemeral=True)

    @discord.ui.button(label="Выйти", emoji="🚪", style=discord.ButtonStyle.danger)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.db.get_active_tournament(self.guild_id)
        if not t or t["status"] != "signup":
            await interaction.response.send_message("Нельзя выйти.", ephemeral=True)
            return
        success = self.db.leave_tournament(t["id"], interaction.user.id)
        if success:
            self.db.conn.commit()
            count = self.db.get_tournament_participant_count(t["id"])
            await interaction.response.send_message(f"✅ Вы вышли. Осталось: {count}/{t['max_players']}", ephemeral=True)
        else:
            await interaction.response.send_message("Вы не записаны.", ephemeral=True)

    @discord.ui.button(label="Сетка", emoji="📋", style=discord.ButtonStyle.secondary)
    async def bracket_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.db.get_active_tournament(self.guild_id)
        if not t:
            await interaction.response.send_message("Нет активного турнира.", ephemeral=True)
            return
        view = TournamentBracketView(self.db, t["id"])
        embed = view.build_bracket_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Начать турнир", emoji="▶️", style=discord.ButtonStyle.success, row=1)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        t = self.db.get_active_tournament(self.guild_id)
        if not t:
            await interaction.response.send_message("Нет активного турнира.", ephemeral=True)
            return
        if t["status"] != "signup":
            await interaction.response.send_message("Турнир уже начат.", ephemeral=True)
            return
        if t["creator_id"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Только создатель или админ.", ephemeral=True)
            return
        participants = self.db.get_tournament_participants(t["id"])
        if len(participants) < 4:
            await interaction.response.send_message("Нужно минимум 4 участника.", ephemeral=True)
            return
        import math
        import random as _random
        bracket_size = 2 ** math.ceil(math.log2(len(participants)))
        _random.shuffle(participants)
        seeds = {p: i + 1 for i, p in enumerate(participants)}
        self.db.set_tournament_seeds(t["id"], seeds)
        self.db.set_tournament_status(t["id"], "in_progress")
        num_matches = bracket_size // 2
        for i in range(num_matches):
            p1 = participants[i] if i < len(participants) else None
            p2 = participants[bracket_size - 1 - i] if (bracket_size - 1 - i) < len(participants) else None
            if p1 and p2:
                self.db.create_tournament_match(t["id"], 1, i + 1, p1, p2)
            elif p1:
                self.db.create_tournament_match(t["id"], 1, i + 1, p1, None)
                self.db.conn.execute(
                    "UPDATE tournament_matches SET winner_id=?, status='finished' "
                    "WHERE tournament_id=? AND round=1 AND slot=? AND player2_id IS NULL",
                    (p1, t["id"], i + 1))
                self.db.conn.commit()
        self.db.conn.commit()
        cog._dirty_tournaments.add(t["id"])
        embed = TournamentBracketView(self.db, t["id"]).build_bracket_embed(interaction.guild)
        await interaction.response.send_message(f"🏆 Турнир **{t['name']}** начинается!", embed=embed)
        await cog.try_advance_tournament(t["id"])


class TournamentCreateModal(discord.ui.Modal, title="Новый турнир"):
    name = discord.ui.TextInput(label="Название турнира", placeholder="напр. Dota Cup", max_length=50)
    size = discord.ui.TextInput(label="Макс. участников (8 или 16)", placeholder="16", max_length=2, default="16")

    def __init__(self, db: Storage):
        super().__init__()
        self.db = db

    async def on_submit(self, interaction: discord.Interaction):
        max_p = 16
        try:
            max_p = int(str(self.size.value))
            if max_p not in (8, 16):
                max_p = 16
        except ValueError:
            max_p = 16
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        t = self.db.get_active_tournament(interaction.guild.id)
        if t:
            await interaction.response.send_message(
                f"Уже есть активный турнир: **{t['name']}** ({t['status']}).", ephemeral=True)
            return
        tid = self.db.create_tournament(interaction.guild.id, str(self.name.value), interaction.user.id, max_p)
        if not tid:
            await interaction.response.send_message("Не удалось создать.", ephemeral=True)
            return
        cog._dirty_tournaments.add(tid)
        embed = discord.Embed(
            title=f"🏆 Турнир: {self.name.value}",
            description=f"Создал: {interaction.user.mention}\n"
                        f"Максимум: **{max_p}** участников\n"
                        f"Запись открыта!",
            color=0x8B4513)
        view = TournamentSignupView(self.db, tid)
        await interaction.response.send_message(embed=embed, view=view)


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
        self.bet_btn.custom_id = f"duel:bet:{duel_id}"

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

    @discord.ui.button(label="💰 Сделать ставку", style=discord.ButtonStyle.primary)
    async def bet_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        duel = self.db.get_duel(self.duel_id)
        if not duel or duel["status"] != "pending":
            await interaction.response.send_message("Дуэль уже неактуальна.", ephemeral=True)
            return
        if interaction.user.id in (duel["player1_id"], duel["player2_id"]):
            await interaction.response.send_message("Нельзя ставить на свою дуэль.", ephemeral=True)
            return
        existing = self.db.get_bets_for_duel(self.duel_id)
        user_bet = next((b for b in existing if b["bettor_id"] == interaction.user.id), None)
        if user_bet and user_bet["status"] == "pending":
            await interaction.response.send_message(
                "Вы уже сделали ставку на эту дуэль.", ephemeral=True)
            return
        view = BetChoiceView(self.db, self.duel_id, duel["player1_id"], duel["player2_id"])
        await interaction.response.send_message(
            "На кого ставите?", ephemeral=True, view=view)


class BetChoiceView(discord.ui.View):
    def __init__(self, db: Storage, duel_id: int, p1_id: int, p2_id: int):
        super().__init__(timeout=60)
        self.db = db
        self.duel_id = duel_id
        self.p1_btn.custom_id = f"duel:bet_choice:{duel_id}:p1"
        self.p2_btn.custom_id = f"duel:bet_choice:{duel_id}:p2"

    @discord.ui.button(label="Игрок 1", style=discord.ButtonStyle.secondary)
    async def p1_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        duel = self.db.get_duel(self.duel_id)
        if not duel:
            await interaction.response.send_message("Дуэль не найдена.", ephemeral=True)
            return
        await interaction.response.send_modal(
            BetModal(self.db, self.duel_id, duel["player1_id"]))

    @discord.ui.button(label="Игрок 2", style=discord.ButtonStyle.secondary)
    async def p2_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        duel = self.db.get_duel(self.duel_id)
        if not duel:
            await interaction.response.send_message("Дуэль не найдена.", ephemeral=True)
            return
        await interaction.response.send_modal(
            BetModal(self.db, self.duel_id, duel["player2_id"]))


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


# ---------------- турнир: view'ы ----------------

class TournamentSignupView(discord.ui.View):
    def __init__(self, db: Storage, tournament_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.tournament_id = tournament_id
        self.signup_btn.custom_id = f"tournament:signup:{tournament_id}"

    @discord.ui.button(label="🎯 Записаться на турнир", style=discord.ButtonStyle.success)
    async def signup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = self.db.get_tournament(self.tournament_id)
        if not t or t["status"] != "signup":
            await interaction.response.send_message("Запись на этот турнир закрыта.", ephemeral=True)
            return
        count = self.db.get_tournament_participant_count(self.tournament_id)
        if count >= t["max_players"]:
            await interaction.response.send_message("Турнир заполнен.", ephemeral=True)
            return
        success = self.db.join_tournament(self.tournament_id, interaction.user.id)
        if not success:
            await interaction.response.send_message("Вы уже записаны.", ephemeral=True)
            return
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        cog._dirty_tournaments.add(self.tournament_id)
        new_count = self.db.get_tournament_participant_count(self.tournament_id)
        await interaction.response.send_message(
            f"✅ Вы записаны! Участников: {new_count}/{t['max_players']}", ephemeral=True)


class TournamentBracketView(discord.ui.View):
    def __init__(self, db: Storage, tournament_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.tournament_id = tournament_id

    def build_bracket_embed(self, guild: discord.Guild) -> discord.Embed:
        t = self.db.get_tournament(self.tournament_id)
        if not t:
            return discord.Embed(title="Турнир не найден", color=0xE74C3C)
        matches = self.db.get_tournament_matches(self.tournament_id)
        embed = discord.Embed(title=f"🏆 {t['name']}", color=0x8B4513)
        embed.set_footer(text=f"Статус: {t['status']} | Участников: "
                              f"{self.db.get_tournament_participant_count(self.tournament_id)}/{t['max_players']}")
        if t["winner_id"]:
            member = guild.get_member(t["winner_id"])
            name = member.display_name if member else f"<@{t['winner_id']}>"
            embed.description = f"🏆 Победитель: **{name}**"
            return embed
        rounds = {}
        for m in matches:
            rounds.setdefault(m["round"], []).append(m)
        round_names = {1: "Quarter-final", 2: "Semi-final", 3: "Final"}
        for rnd in sorted(rounds.keys()):
            lines = []
            for m in rounds[rnd]:
                p1 = f"<@{m['player1_id']}>" if m["player1_id"] else "?"
                p2 = f"<@{m['player2_id']}>" if m["player2_id"] else "?"
                if m["winner_id"]:
                    winner = f"<@{m['winner_id']}> ✅"
                    lines.append(f"M{m['id']}: {p1} vs {p2} → {winner}")
                else:
                    lines.append(f"M{m['id']}: {p1} vs {p2} → pending")
            round_label = round_names.get(rnd, f"Round {rnd}")
            embed.add_field(name=f"═══ {round_label} ═══", value="\n".join(lines), inline=False)
        if not matches:
            embed.description = "Сетка ещё не создана. Ожидание старта."
        return embed


class TournamentMatchView(discord.ui.View):
    def __init__(self, db: Storage, match_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.match_id = match_id
        self.report_win.custom_id = f"tmatch:win:{match_id}"
        self.report_loss.custom_id = f"tmatch:loss:{match_id}"

    @discord.ui.button(label="🏆 Я победил", style=discord.ButtonStyle.success)
    async def report_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._report(interaction, "win")

    @discord.ui.button(label="💀 Я проиграл", style=discord.ButtonStyle.secondary)
    async def report_loss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._report(interaction, "loss")

    async def _report(self, interaction: discord.Interaction, result: str):
        m = self.db.get_tournament_match(self.match_id)
        if not m or m["status"] == "finished":
            await interaction.response.send_message("Матч уже завершён.", ephemeral=True)
            return
        if interaction.user.id not in (m["player1_id"], m["player2_id"]):
            await interaction.response.send_message("Это не ваш матч.", ephemeral=True)
            return
        if result == "win":
            self.db.set_tournament_match_winner(self.match_id, interaction.user.id)
        else:
            opponent = m["player2_id"] if interaction.user.id == m["player1_id"] else m["player1_id"]
            self.db.set_tournament_match_winner(self.match_id, opponent)
        await interaction.response.send_message(
            f"✅ Результат зафиксирован: {'победа' if result == 'win' else 'поражение'}.", ephemeral=True)
        cog: "DotaStats" = interaction.client.get_cog("DotaStats")
        cog._dirty_tournaments.add(m["tournament_id"])
        await cog.try_advance_tournament(m["tournament_id"])


# ---------------- cog ----------------

class DotaStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = db  # единый экземпляр, определённый на уровне модуля
        self._dirty_economy: set[int] = set()  # discord_id кого нужно записать в канал-бэкап
        self._dirty_achievements: set[int] = set()
        self._dirty_tournaments: set[int] = set()  # tournament_id
        # ВАЖНО: фоновые опросы (статус, новые матчи и т.д.) читают
        # self.db.all_players() — если запустить их до того, как локальная
        # SQLite успеет восстановиться из канала-бэкапа (после чистого
        # старта/редеплоя), первый цикл-другой отработает по пустому
        # списку игроков. Поэтому старт циклов не здесь, а в конце
        # _startup_sequence(), уже после sync_players_from_backup_channel().

    def cog_unload(self):
        self.poll_status.cancel()
        self.poll_new_matches.cancel()
        self.check_weekly_leaderboard.cancel()
        self.check_duel_expiry.cancel()
        self.backup_dirty_data.cancel()

    async def cog_load(self):
        self.bot.add_view(RegisterView(self.db))
        self.bot.add_view(ProfileView(self.db))
        self.bot.add_view(CompetitionView(self.db))
        self.bot.add_view(StrategyView(self.db))
        self.bot.add_view(PatchAnalyticsView())
        self.bot.add_view(ShopView(self.db))
        # переподключаем кнопки активных дуэлей после рестарта бота —
        # custom_id зашит в duel_id, поэтому старые сообщения снова оживают
        for duel in self.db.duels_by_status(["pending"]):
            self.bot.add_view(DuelOfferView(self.db, duel["id"]))
        for duel in self.db.duels_by_status(["accepted"]):
            self.bot.add_view(DuelReportView(self.db, duel["id"]))
        for duel in self.db.duels_by_status(["disputed"]):
            self.bot.add_view(DuelAdminResolveView(self.db, duel["id"]))
        for t in self.db.all_tournaments_by_status(["in_progress"]):
            for m in self.db.get_tournament_matches(t["id"]):
                if m["status"] != "finished" and m["player1_id"] and m["player2_id"]:
                    self.bot.add_view(TournamentMatchView(self.db, m["id"]))
            self.bot.add_view(TournamentSignupView(self.db, t["id"]))
        self.bot.loop.create_task(self._startup_sequence())

    async def _startup_sequence(self):
        """Единая последовательность при старте бота — порядок важен:
        1) дождаться подключения к Discord (кэш каналов/гильдий готов);
        2) восстановить players из приватного канала-бэкапа (если он
           настроен и/или локальная SQLite только что создана с нуля);
        3) и только теперь запускать фоновые опросы, которые читают
           self.db.all_players() — иначе первый цикл-другой отработает
           по пустому/неполному списку игроков."""
        await self.bot.wait_until_ready()

        try:
            restored = await self.sync_players_from_backup_channel()
            if PLAYER_BACKUP_CHANNEL_ID and DEBUG_LOG:
                print(f"[BACKUP] синхронизация при старте: {restored} привязок из канала")
        except Exception as e:
            print(f"[BACKUP] ошибка синхронизации из канала-бэкапа при старте: {e!r}")

        try:
            restored = await self.sync_economy_from_backup()
            if CURRENCY_BACKUP_CHANNEL_ID and DEBUG_LOG:
                print(f"[BACKUP] экономика: восстановлено {restored} балансов")
        except Exception as e:
            print(f"[BACKUP] ошибка восстановления экономики: {e!r}")

        try:
            restored = await self.sync_achievements_from_backup()
            if TOURNAMENT_BACKUP_CHANNEL_ID and DEBUG_LOG:
                print(f"[BACKUP] достижения: восстановлено {restored} профилей")
        except Exception as e:
            print(f"[BACKUP] ошибка восстановления достижений: {e!r}")

        try:
            restored = await self.sync_tournaments_from_backup()
            if TOURNAMENT_BACKUP_CHANNEL_ID and DEBUG_LOG:
                print(f"[BACKUP] турниры: восстановлено {restored} турниров")
        except Exception as e:
            print(f"[BACKUP] ошибка восстановления турниров: {e!r}")

        self.poll_status.start()
        self.poll_new_matches.start()
        self.check_weekly_leaderboard.start()
        self.check_duel_expiry.start()
        self.backup_dirty_data.start()

        # чтобы не приходилось руками гонять !dota_setup после каждого
        # обновления кода — при каждом рестарте бота уже существующая
        # панель сама перерисовывается с актуальным набором кнопок
        await self._refresh_dashboard_panels_on_start()
        await match_reviewer.self_test()

    async def _refresh_dashboard_panels_on_start(self):
        """Обновляет старые панели DashboardView (если есть) —
        заменяет на CompetitionView для совместимости."""
        for guild_id, channel_id, message_id in self.db.all_dashboards():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(message_id)
                embed = discord.Embed(
                    title="🏆 Соревнования",
                    description="Лидерборд, дуэли и турниры — нажмите кнопку ниже.",
                    color=0x8B4513)
                await msg.edit(embed=embed, view=CompetitionView(self.db))
                if DEBUG_LOG:
                    print(f"[DASHBOARD] старая панель в {channel_id} обновлена → CompetitionView")
            except discord.NotFound:
                pass
            except discord.Forbidden:
                if DEBUG_LOG:
                    print(f"[DASHBOARD] нет прав редактировать панель в {channel_id}")

    # ---------- бэкап привязок discord_id <-> SteamID в приватный канал ----------
    #
    # Локальная SQLite (self.db) — единственное место, которое хостинг может
    # стереть при редеплое (см. комментарий у _resolve_db_path). Чтобы это
    # не значило "все заново привязывайте SteamID", таблица players
    # дублируется в приватный Discord-канал: по одному сообщению на игрока,
    # с discord_id/account_id/steam_id64 в разбираемом виде (см.
    # _format_backup_message/_parse_backup_message выше). Канал задаётся
    # переменной окружения PLAYER_BACKUP_CHANNEL_ID.
    #
    # Канал — источник правды, SQLite — быстрый кэш поверх него:
    #   - при регистрации: пишем и в SQLite, и (если канал настроен) в канал;
    #   - при каждом старте бота: читаем историю канала и заливаем в SQLite
    #     ДО того, как запускаются фоновые опросы (см. _startup_sequence);
    #   - командой !dota_backup_resync это можно повторить вручную в любой
    #     момент, не перезапуская бота.

    async def _get_backup_channel(self) -> discord.abc.Messageable | None:
        if not PLAYER_BACKUP_CHANNEL_ID:
            return None
        channel = self.bot.get_channel(PLAYER_BACKUP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(PLAYER_BACKUP_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden) as e:
                print(f"[BACKUP] не могу получить канал-бэкап {PLAYER_BACKUP_CHANNEL_ID}: {e!r} "
                      f"— проверьте ID и что боту выдан доступ к каналу.")
                return None
        return channel

    async def backup_player_to_channel(self, discord_id: int, account_id: int, steam_id64: int):
        """Дублирует одну привязку в канал-бэкап. Если для игрока уже есть
        сообщение — редактирует его (без дублей при повторной привязке)."""
        channel = await self._get_backup_channel()
        if not channel:
            return  # бэкап не настроен — молча работаем только с SQLite, как раньше
        text = _format_backup_message(discord_id, account_id, steam_id64)

        cached_id = self.db.get_backup_message_id(discord_id)
        if cached_id:
            try:
                msg = await channel.fetch_message(cached_id)
                await msg.edit(content=text)
                return
            except (discord.NotFound, discord.Forbidden):
                pass  # сообщение удалили руками или SQLite это ID не знала — ищем/создаём ниже

        # локального ID сообщения нет или он "протух" — ищем по содержимому
        # истории канала (например, после того как сама SQLite была
        # восстановлена из этого же канала и связь discord_id->message_id
        # локально не сохранилась)
        async for msg in channel.history(limit=None):
            if msg.author.id != self.bot.user.id:
                continue
            parsed = _parse_backup_message(msg.content)
            if parsed and parsed["discord_id"] == discord_id:
                await msg.edit(content=text)
                self.db.set_backup_message_id(discord_id, msg.id)
                return

        sent = await channel.send(text)
        self.db.set_backup_message_id(discord_id, sent.id)

    async def sync_players_from_backup_channel(self) -> int:
        """Перечитывает всю историю канала-бэкапа и заливает найденные
        привязки в локальную SQLite (upsert — не трогает last_match_id уже
        существующих игроков). Возвращает число обработанных привязок.
        Безопасно вызывать многократно (в т.ч. вручную, !dota_backup_resync) —
        операция идемпотентна."""
        channel = await self._get_backup_channel()
        if not channel:
            return 0
        restored = 0
        async for msg in channel.history(limit=None):
            if msg.author.id != self.bot.user.id:
                continue
            parsed = _parse_backup_message(msg.content)
            if not parsed:
                continue
            self.db.register(parsed["discord_id"], parsed["account_id"], parsed["steam_id64"])
            self.db.set_backup_message_id(parsed["discord_id"], msg.id)
            restored += 1
        return restored

    # ---------- бэкап экономики (балансы + покупки) ----------

    async def _get_currency_backup_channel(self) -> discord.abc.Messageable | None:
        if not CURRENCY_BACKUP_CHANNEL_ID:
            return None
        channel = self.bot.get_channel(CURRENCY_BACKUP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(CURRENCY_BACKUP_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden):
                return None
        return channel

    async def sync_economy_from_backup(self) -> int:
        channel = await self._get_currency_backup_channel()
        if not channel:
            return 0
        restored = 0
        async for msg in channel.history(limit=None):
            if msg.author.id != self.bot.user.id:
                continue
            parsed = _parse_economy_backup(msg.content)
            if not parsed:
                continue
            self.db.set_balance_raw(
                parsed["discord_id"], parsed["balance"],
                parsed["total_earned"], parsed["total_spent"])
            restored += 1
        return restored

    async def backup_economy_to_channel(self, discord_id: int):
        channel = await self._get_currency_backup_channel()
        if not channel:
            return
        balance = self.db.get_balance(discord_id)
        row = self.db.conn.execute(
            "SELECT total_earned, total_spent FROM currency WHERE discord_id=?",
            (discord_id,)).fetchone()
        total_earned = row[0] if row else 0
        total_spent = row[1] if row else 0
        titles = self.db.get_user_titles(discord_id)
        color = self.db.get_user_color(discord_id)
        text = _format_economy_backup(discord_id, balance, total_earned, total_spent, titles, color)
        await self._upsert_backup_msg(channel, discord_id, text, "economy")

    # ---------- бэкап достижений ----------

    async def _get_tournament_backup_channel(self) -> discord.abc.Messageable | None:
        if not TOURNAMENT_BACKUP_CHANNEL_ID:
            return None
        channel = self.bot.get_channel(TOURNAMENT_BACKUP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(TOURNAMENT_BACKUP_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden):
                return None
        return channel

    async def sync_achievements_from_backup(self) -> int:
        channel = await self._get_tournament_backup_channel()
        if not channel:
            return 0
        restored = 0
        async for msg in channel.history(limit=None):
            if msg.author.id != self.bot.user.id:
                continue
            parsed = _parse_achievement_backup(msg.content)
            if not parsed:
                continue
            for key in parsed["achievements"]:
                self.db.grant_achievement(parsed["discord_id"], key)
            restored += 1
        return restored

    async def backup_achievements_to_channel(self, discord_id: int):
        channel = await self._get_tournament_backup_channel()
        if not channel:
            return
        achievements = self.db.get_achievements(discord_id)
        if not achievements:
            return
        text = _format_achievement_backup(discord_id, achievements)
        await self._upsert_backup_msg(channel, discord_id, text, "achievement")

    # ---------- бэкап турниров ----------

    async def sync_tournaments_from_backup(self) -> int:
        channel = await self._get_tournament_backup_channel()
        if not channel:
            return 0
        restored = 0
        async for msg in channel.history(limit=None):
            if msg.author.id != self.bot.user.id:
                continue
            parsed = _parse_tournament_backup(msg.content)
            if not parsed:
                continue
            existing = self.db.get_tournament(parsed["id"])
            if existing:
                continue
            self.db.conn.execute(
                "INSERT INTO tournaments (id, guild_id, name, creator_id, status, max_players, winner_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (parsed["id"], parsed["guild_id"], parsed["name"],
                 parsed["creator_id"], parsed["status"], parsed["max_players"],
                 parsed["winner_id"]))
            for p_id in parsed["participants"]:
                self.db.conn.execute(
                    "INSERT OR IGNORE INTO tournament_participants (tournament_id, discord_id) VALUES (?, ?)",
                    (parsed["id"], p_id))
            for m in parsed["matches"]:
                self.db.conn.execute(
                    "INSERT INTO tournament_matches "
                    "(tournament_id, round, slot, player1_id, player2_id, winner_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (parsed["id"], m["round"], m["slot"], m["player1_id"],
                     m["player2_id"], m["winner_id"],
                     "finished" if m["winner_id"] else "pending"))
            self.db.conn.commit()
            restored += 1
        return restored

    async def backup_tournament_to_channel(self, tournament_id: int):
        channel = await self._get_tournament_backup_channel()
        if not channel:
            return
        t = self.db.get_tournament(tournament_id)
        if not t:
            return
        t["participants"] = self.db.get_tournament_participants(tournament_id)
        t["matches"] = self.db.get_tournament_matches(tournament_id)
        text = _format_tournament_backup(t)
        await self._upsert_backup_msg(channel, tournament_id, text, "tournament")

    # ---------- универсальный upsert сообщения-бэкапа ----------

    async def _upsert_backup_msg(self, channel, entity_id: int, text: str, kind: str):
        """Ищет существующее сообщение по entity_id и kind в локальном кэше
        backup_messages. Если не находит — ищет по содержимому истории канала.
        Создаёт или редактирует."""
        cache_key = f"{kind}_{entity_id}"
        cache_row = self.db.conn.execute(
            "SELECT message_id FROM backup_messages WHERE discord_id=?",
            (cache_key,)).fetchone() if kind != "economy" else None

        # для economy используем discord_id как ключ напрямую
        if kind == "economy":
            cache_row = self.db.conn.execute(
                "SELECT message_id FROM backup_messages WHERE discord_id=?",
                (entity_id,)).fetchone()

        msg_id = cache_row[0] if cache_row else None

        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(content=text)
                return
            except (discord.NotFound, discord.Forbidden):
                pass

        # ищем по содержимому
        marker = text.split("\n")[0]
        async for msg in channel.history(limit=100):
            if msg.author.id != self.bot.user.id:
                continue
            if msg.content.startswith(marker) and str(entity_id) in msg.content:
                await msg.edit(content=text)
                if kind == "economy":
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO backup_messages (discord_id, message_id) VALUES (?, ?)",
                        (entity_id, msg.id))
                self.db.conn.commit()
                return

        sent = await channel.send(text)
        if kind == "economy":
            self.db.conn.execute(
                "INSERT OR REPLACE INTO backup_messages (discord_id, message_id) VALUES (?, ?)",
                (entity_id, sent.id))
        self.db.conn.commit()

    # ---------- достижения ----------

    async def check_achievements(self, discord_id: int):
        """Проверяет все условия достижений и выдаёт новые. Начисляет shards за каждое новое."""
        existing = set(self.db.get_achievements(discord_id))
        account_id = self.db.get_account_id(discord_id)
        if not account_id:
            return

        # first_win — первый выигранный матч
        if "first_win" not in existing:
            recent = await od.get(f"/players/{account_id}/recentMatches")
            if recent:
                for m in recent[:50]:
                    if (m.get("player_slot", 0) < 128) == m.get("radiant_win"):
                        if self.db.grant_achievement(discord_id, "first_win"):
                            self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, "achievement:first_win")
                            self._dirty_achievements.add(discord_id)
                            self._dirty_economy.add(discord_id)
                        break

        # streak_5 / streak_10 — победы подряд
        recent = await od.get(f"/players/{account_id}/recentMatches")
        if recent:
            streak = 0
            for m in recent:
                won = (m.get("player_slot", 0) < 128) == m.get("radiant_win")
                if won:
                    streak += 1
                else:
                    break
            if streak >= 10 and "streak_10" not in existing:
                if self.db.grant_achievement(discord_id, "streak_10"):
                    self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, "achievement:streak_10")
                    self._dirty_achievements.add(discord_id)
                    self._dirty_economy.add(discord_id)
            if streak >= 5 and "streak_5" not in existing:
                if self.db.grant_achievement(discord_id, "streak_5"):
                    self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, "achievement:streak_5")
                    self._dirty_achievements.add(discord_id)
                    self._dirty_economy.add(discord_id)

        # games_100 / games_500
        wl = await od.get(f"/players/{account_id}/wl")
        if wl:
            total = wl.get("win", 0) + wl.get("lose", 0)
            for key, threshold in [("games_100", 100), ("games_500", 500)]:
                if total >= threshold and key not in existing:
                    if self.db.grant_achievement(discord_id, key):
                        self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, f"achievement:{key}")
                        self._dirty_achievements.add(discord_id)
                        self._dirty_economy.add(discord_id)

        # wr_above_60 — винрейт > 60% при 50+ играх
        if wl:
            wins = wl.get("win", 0)
            total = wins + wl.get("lose", 0)
            if total >= 50 and wins / total > 0.6 and "wr_above_60" not in existing:
                if self.db.grant_achievement(discord_id, "wr_above_60"):
                    self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, "achievement:wr_above_60")
                    self._dirty_achievements.add(discord_id)
                    self._dirty_economy.add(discord_id)

        # hero_master — 30+ побед на одном герое
        hero_stats = await od.player_hero_stats(account_id, limit=100)
        for h in hero_stats:
            if h["wins"] >= 30 and "hero_master" not in existing:
                if self.db.grant_achievement(discord_id, "hero_master"):
                    self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, "achievement:hero_master")
                    self._dirty_achievements.add(discord_id)
                    self._dirty_economy.add(discord_id)
                break

        # duel_win3 / duel_win10
        duel_wins, _ = self.db.get_duel_stats(discord_id)
        for key, threshold in [("duel_win3", 3), ("duel_win10", 10)]:
            if duel_wins >= threshold and key not in existing:
                if self.db.grant_achievement(discord_id, key):
                    self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, f"achievement:{key}")
                    self._dirty_achievements.add(discord_id)
                    self._dirty_economy.add(discord_id)

        # shards_1000
        balance = self.db.get_balance(discord_id)
        if balance >= 1000 and "shards_1000" not in existing:
            if self.db.grant_achievement(discord_id, "shards_1000"):
                self.db.add_shards(discord_id, SHARD_ACHIEVEMENT, "achievement:shards_1000")
                self._dirty_achievements.add(discord_id)
                self._dirty_economy.add(discord_id)

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
        known_account_ids = {acc: did for did, acc, _ in self.db.all_players()}
        facts = await build_match_facts(account_id, match_id, known_account_ids)
        if not facts:
            return
        issues = detect_issues(facts)
        review_text = await match_reviewer.write_review(facts, issues)
        embed = self._build_review_embed(facts, issues, review_text)

        # начисление shards за матч
        if facts["won"]:
            new_bal = self.db.add_shards(discord_id, SHARD_WIN_MATCH, f"match_win:{match_id}")
        else:
            new_bal = self.db.add_shards(discord_id, SHARD_LOSS_MATCH, f"match_loss:{match_id}")
        self._dirty_economy.add(discord_id)

        if self.db.get_dm_muted(discord_id):
            if DEBUG_LOG:
                print(f"[MATCH REVIEW] DM muted для {discord_id}, пропускаю отправку")
        else:
            user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
            try:
                await user.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                for guild_id, channel_id, _ in self.db.all_dashboards():
                    guild = self.bot.get_guild(guild_id)
                    if not guild or not guild.get_member(discord_id):
                        continue
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await channel.send(content=f"<@{discord_id}>", embed=embed)
                    break

        # проверка достижений после матча
        try:
            await self.check_achievements(discord_id)
        except Exception as e:
            if DEBUG_LOG:
                print(f"[ACHIEVEMENTS] ошибка проверки для {discord_id}: {e!r}")

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

        # фильтрация: только обладатели роли «⚔️ Дуэлянт»
        duelist_role = discord.utils.get(guild.roles, name=DUELIST_ROLE_NAME)
        if duelist_role:
            p1_member = guild.get_member(p1_id)
            p2_member = guild.get_member(p2_id)
            p1_has = duelist_role in p1_member.roles if p1_member else False
            p2_has = duelist_role in p2_member.roles if p2_member else False
            if not p1_has or not p2_has:
                return  # один из топ-2 не дуэлянт — дуэль не создаётся

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

        # публичное объявление БЕЗ кнопок — просто анонс
        await channel.send(
            content=f"<@{p1_id}> <@{p2_id}>",
            embed=embed)

        # приватные приглашения с кнопками — каждому игроку отдельно
        p1_member = guild.get_member(p1_id)
        p2_member = guild.get_member(p2_id)

        offer_embed_p1 = discord.Embed(
            title="⚔️ Вам бросили вызов!",
            description=(
                f"Вас вызывает **{p2_name}** на дуэль!\n\n"
                f"Дедлайн: <t:{int(deadline.timestamp())}:R>\n\n"
                "Нажмите кнопку ниже, чтобы принять или отклонить."
            ),
            color=0xE67E22)
        offer_embed_p2 = discord.Embed(
            title="⚔️ Вам бросили вызов!",
            description=(
                f"Вас вызывает **{p1_name}** на дуэль!\n\n"
                f"Дедлайн: <t:{int(deadline.timestamp())}:R>\n\n"
                "Нажмите кнопку ниже, чтобы принять или отклонить."
            ),
            color=0xE67E22)

        view1 = DuelOfferView(self.db, duel_id)
        view2 = DuelOfferView(self.db, duel_id)

        if p1_member:
            try:
                msg1 = await p1_member.send(embed=offer_embed_p1, view=view1)
                self.db.set_offer_message(duel_id, msg1.channel.id, msg1.id)
            except (discord.Forbidden, discord.HTTPException):
                pass
        if p2_member:
            try:
                msg2 = await p2_member.send(embed=offer_embed_p2, view=view2)
            except (discord.Forbidden, discord.HTTPException):
                pass

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
            self.db.void_bets(duel["id"])
        else:
            winner_id = p1_id if winner_slot == 1 else p2_id
            loser_id = p2_id if winner_slot == 1 else p1_id
            self.db.set_winner(duel["id"], winner_id, "confirmed")
            self.db.bump_duel_stats(winner_id, won=True)
            self.db.bump_duel_stats(loser_id, won=False)

            # начисление shards за дуэль
            self.db.add_shards(winner_id, SHARD_WIN_DUEL, f"duel_win:{duel['id']}")
            self.db.add_shards(loser_id, SHARD_LOSS_MATCH, f"duel_loss:{duel['id']}")
            self._dirty_economy.add(winner_id)
            self._dirty_economy.add(loser_id)

            # выплата ставок
            self.db.resolve_bets(duel["id"], winner_id)

            # проверка достижений
            try:
                await self.check_achievements(winner_id)
                await self.check_achievements(loser_id)
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[ACHIEVEMENTS] ошибка проверки после дуэли: {e!r}")

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

    # ---------- бэкап "грязных" данных в Discord-каналы ----------

    @tasks.loop(minutes=BALANCE_BACKUP_INTERVAL_MINUTES)
    async def backup_dirty_data(self):
        for discord_id in list(self._dirty_economy):
            self._dirty_economy.discard(discord_id)
            try:
                await self.backup_economy_to_channel(discord_id)
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[BACKUP] ошибка бэкапа экономики {discord_id}: {e!r}")
            await asyncio.sleep(0.5)

        for discord_id in list(self._dirty_achievements):
            self._dirty_achievements.discard(discord_id)
            try:
                await self.backup_achievements_to_channel(discord_id)
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[BACKUP] ошибка бэкапа достижений {discord_id}: {e!r}")
            await asyncio.sleep(0.5)

        for tid in list(self._dirty_tournaments):
            self._dirty_tournaments.discard(tid)
            try:
                await self.backup_tournament_to_channel(tid)
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[BACKUP] ошибка бэкапа турнира {tid}: {e!r}")
            await asyncio.sleep(0.5)

    @backup_dirty_data.before_loop
    async def before_backup(self):
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
            управляется командами !dota_voice_protect / !dota_voice_unprotect /
            !dota_voice_protected_list;
          - "постоянные" каналы/категории сервера — поранговые комнаты,
            📊 Статистика сервера, ➕ Создать войс — они защищены жёстко в
            коде по названию (см. ALWAYS_PROTECTED_VOICE_* выше) и не зависят
            от БД, поэтому не "слетают" при сбросе/потере базы;
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
        category_name = refreshed.category.name if refreshed.category else None
        if (self.db.is_voice_protected(refreshed.id, category_id, guild.id)
                or refreshed.name in ALWAYS_PROTECTED_VOICE_CHANNEL_NAMES
                or (category_name is not None
                    and category_name in ALWAYS_PROTECTED_VOICE_CATEGORY_NAMES)):
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
        """Расставляет виджеты по каналам автоматически по названиям:
        🔐-ВЕРИФИКАЦИЯ → RegisterView, 👋-приветствия → ProfileView,
        🏆-лидерборд → CompetitionView, 🧠-советы-и-стратегии → StrategyView,
        🛒-магазин → ShopView."""
        views_map = {
            "🔐-ВЕРИФИКАЦИЯ": ("🔗 Верификация",
                "Привяжите SteamID, чтобы получить доступ к серверу.",
                RegisterView),
            "👋-приветствия": ("👋 Профиль и настройки",
                "Просмотрите профиль и настройте уведомления.",
                ProfileView),
            "🏆-лидерборд": ("🏆 Соревнования",
                "Лидерборд, дуэли и турниры сервера.",
                CompetitionView),
            "🧠-стратегия": ("🧠 Стратегия и аналитика",
                "Мета героев, контр-пики, сравнение и разбор игр.",
                StrategyView),
            "🛒-магазин": ("🛒 Магазин Shards",
                "Ежедневный бонус, товары и баланс.",
                ShopView),
        }
        placed = 0
        for ch_name, (title, desc, view_cls) in views_map.items():
            ch = discord.utils.get(ctx.guild.text_channels, name=ch_name)
            if not ch:
                continue
            embed = discord.Embed(title=title, description=desc, color=0x8B4513)
            # удаляем старое закреплённое сообщение от бота (если есть)
            try:
                pins = await ch.pins()
                for p in pins:
                    if p.author.id == ctx.bot.user.id:
                        try:
                            await p.unpin()
                            await p.delete()
                        except discord.HTTPException:
                            pass
            except discord.Forbidden:
                pass
            msg = await ch.send(embed=embed, view=view_cls(self.db))
            try:
                await msg.pin()
            except discord.Forbidden:
                pass
            placed += 1
        await ctx.send(f"✅ Расставлено {placed} виджетов по каналам.")

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

    @commands.command(name="dota_backup_status")
    @commands.has_permissions(manage_channels=True)
    async def backup_status(self, ctx: commands.Context):
        """Показывает, настроен ли канал-бэкап привязок и сколько игроков
        привязано локально прямо сейчас."""
        players_count = len(self.db.all_players())
        if not PLAYER_BACKUP_CHANNEL_ID:
            await ctx.send(
                f"⚠️ Канал-бэкап привязок **не настроен** (нет переменной окружения "
                f"`PLAYER_BACKUP_CHANNEL_ID`). Сейчас в SQLite привязано игроков: {players_count}. "
                f"Если хостинг сотрёт базу при редеплое — все привязки пропадут безвозвратно.\n"
                f"Чтобы включить бэкап: создайте приватный текстовый канал, скопируйте его ID "
                f"(Режим разработчика -> ПКМ по каналу -> Копировать ID) и задайте его в "
                f"`PLAYER_BACKUP_CHANNEL_ID` на хостинге.")
            return
        channel = await self._get_backup_channel()
        if not channel:
            await ctx.send(
                f"❌ `PLAYER_BACKUP_CHANNEL_ID={PLAYER_BACKUP_CHANNEL_ID}` задан, но бот не может "
                f"получить доступ к этому каналу. Проверьте, что ID верный и у бота есть права "
                f"видеть канал/читать историю/писать сообщения.")
            return
        await ctx.send(
            f"✅ Канал-бэкап: {channel.mention}. В SQLite привязано игроков: {players_count}. "
            f"Команда `!dota_backup_resync` перечитает канал и восстановит SQLite вручную.")

    @commands.command(name="dota_backup_resync")
    @commands.has_permissions(manage_channels=True)
    async def backup_resync(self, ctx: commands.Context):
        """Принудительно пересобирает локальную SQLite из канала-бэкапа,
        не дожидаясь рестарта бота. Полезно, если подозреваете, что
        локальные данные и канал разошлись."""
        if not PLAYER_BACKUP_CHANNEL_ID:
            await ctx.send("⚠️ Канал-бэкап не настроен (см. `!dota_backup_status`) — нечего синхронизировать.")
            return
        async with ctx.typing():
            try:
                restored = await self.sync_players_from_backup_channel()
            except Exception as e:
                await ctx.send(f"❌ Ошибка синхронизации: `{e}`")
                return
        await ctx.send(f"✅ Синхронизировано {restored} привязок из канала-бэкапа в локальную SQLite.")

    @commands.command(name="dota_backup_economy")
    @commands.has_permissions(manage_channels=True)
    async def backup_economy_cmd(self, ctx: commands.Context):
        """Принудительно записать экономику в канал-бэкап."""
        async with ctx.typing():
            count = 0
            try:
                for p in self.db.all_players():
                    did = p[0]
                    if did:
                        await self.backup_economy_to_channel(did)
                        count += 1
            except Exception as e:
                await ctx.send(f"❌ Ошибка: `{e}`")
                return
        await ctx.send(f"✅ Записано {count} записей экономики в канал-бэкап.")

    @commands.command(name="dota_backup_tournaments")
    @commands.has_permissions(manage_channels=True)
    async def backup_tournaments_cmd(self, ctx: commands.Context):
        """Принудительно записать все турниры в канал-бэкап."""
        async with ctx.typing():
            count = 0
            try:
                for t in self.db.all_tournaments_by_status(["in_progress", "signup", "finished"]):
                    await self.backup_tournament_to_channel(t["id"])
                    count += 1
            except Exception as e:
                await ctx.send(f"❌ Ошибка: `{e}`")
                return
        await ctx.send(f"✅ Записано {count} турниров в канал-бэкап.")

    # ==================== турнир ====================

    @commands.command(name="tournament_create")
    async def tournament_create(self, ctx: commands.Context, *, name: str = "Dota Cup"):
        """Создать турнир. Формат: !tournament_create [имя] [--size=8|16]"""
        max_players = 16
        if "--size=8" in name:
            max_players = 8
            name = name.replace("--size=8", "").strip()
        elif "--size=16" in name:
            max_players = 16
            name = name.replace("--size=16", "").strip()
        active = self.db.get_active_tournament(ctx.guild.id)
        if active:
            await ctx.send(f"⚠️ Уже есть активный турнир: **{active['name']}** "
                           f"(статус: {active['status']}). Дождитесь его завершения или отмены.")
            return
        tid = self.db.create_tournament(ctx.guild.id, name, ctx.author.id, max_players)
        if not tid:
            await ctx.send("Не удалось создать турнир.")
            return
        embed = discord.Embed(
            title=f"🏆 Турнир: {name}",
            description=f"Создал: {ctx.author.mention}\n"
                        f"Максимум участников: **{max_players}**\n"
                        f"Запись открыта! Нажмите кнопку ниже или `!tournament_join`.",
            color=0x8B4513)
        view = TournamentSignupView(self.db, tid)
        msg = await ctx.send(embed=embed, view=view)
        self._dirty_tournaments.add(tid)

    @commands.command(name="tournament_join")
    async def tournament_join(self, ctx: commands.Context):
        """Записаться на активный турнир."""
        t = self.db.get_active_tournament(ctx.guild.id)
        if not t:
            await ctx.send("Нет активного турнира. Создайте: `!tournament_create`")
            return
        if t["status"] != "signup":
            await ctx.send("Запись на этот турнир закрыта.")
            return
        count = self.db.get_tournament_participant_count(t["id"])
        if count >= t["max_players"]:
            await ctx.send("Турнир заполнен.")
            return
        success = self.db.join_tournament(t["id"], ctx.author.id)
        if not success:
            await ctx.send("Вы уже записаны.")
            return
        count += 1
        self._dirty_tournaments.add(t["id"])
        await ctx.send(f"✅ Вы записаны на **{t['name']}**! ({count}/{t['max_players']})")

    @commands.command(name="tournament_leave")
    async def tournament_leave(self, ctx: commands.Context):
        """Выйти из турнира (только во время записи)."""
        t = self.db.get_active_tournament(ctx.guild.id)
        if not t:
            await ctx.send("Нет активного турнира.")
            return
        if t["status"] != "signup":
            await ctx.send("Турнир уже начался — выйти нельзя.")
            return
        success = self.db.leave_tournament(t["id"], ctx.author.id)
        if success:
            self._dirty_tournaments.add(t["id"])
            count = self.db.get_tournament_participant_count(t["id"])
            await ctx.send(f"✅ Вы вышли из турнира. Осталось: {count}/{t['max_players']}")
        else:
            await ctx.send("Вы не записаны на этот турнир.")

    @commands.command(name="tournament_start")
    async def tournament_start(self, ctx: commands.Context):
        """Начать турнир (создатель или админ). Случайный сидинг, создание сетки."""
        t = self.db.get_active_tournament(ctx.guild.id)
        if not t:
            await ctx.send("Нет активного турнира.")
            return
        if t["status"] != "signup":
            await ctx.send("Турнир уже начат.")
            return
        if t["creator_id"] != ctx.author.id and not ctx.author.guild_permissions.administrator:
            await ctx.send("Только создатель турнира или администратор может начать.")
            return
        participants = self.db.get_tournament_participants(t["id"])
        min_players = 4
        if len(participants) < min_players:
            await ctx.send(f"Нужно минимум {min_players} участников для старта.")
            return
        # округляем до ближайшей степени двойки
        import math
        bracket_size = 2 ** math.ceil(math.log2(len(participants)))
        # рандомный сидинг
        import random as _random
        _random.shuffle(participants)
        seeds = {p: i + 1 for i, p in enumerate(participants)}
        self.db.set_tournament_seeds(t["id"], seeds)
        self.db.set_tournament_status(t["id"], "in_progress")
        # первый раунд: bracket_size / 2 матчей
        num_matches = bracket_size // 2
        for i in range(num_matches):
            p1 = participants[i] if i < len(participants) else None
            p2 = participants[bracket_size - 1 - i] if (bracket_size - 1 - i) < len(participants) else None
            if p1 and p2:
                self.db.create_tournament_match(t["id"], 1, i + 1, p1, p2)
            elif p1:
                # bye — автоматический проход
                self.db.create_tournament_match(t["id"], 1, i + 1, p1, None)
                self.db.conn.execute(
                    "UPDATE tournament_matches SET winner_id=?, status='finished' "
                    "WHERE tournament_id=? AND round=1 AND slot=? AND player2_id IS NULL",
                    (p1, t["id"], i + 1))
                self.db.conn.commit()
        self.db.conn.commit()
        self._dirty_tournaments.add(t["id"])
        embed = TournamentBracketView(self.db, t["id"]).build_bracket_embed(ctx.guild)
        await ctx.send(f"🏆 Турнир **{t['name']}** начинается!", embed=embed)
        await self.try_advance_tournament(t["id"])

    @commands.command(name="tournament_bracket")
    async def tournament_bracket(self, ctx: commands.Context):
        """Показать текущую сетку турнира."""
        t = self.db.get_active_tournament(ctx.guild.id)
        if not t:
            await ctx.send("Нет активного турнира.")
            return
        view = TournamentBracketView(self.db, t["id"])
        embed = view.build_bracket_embed(ctx.guild)
        await ctx.send(embed=embed)

    @commands.command(name="tournament_cancel")
    async def tournament_cancel(self, ctx: commands.Context):
        """Отменить турнир (создатель или админ)."""
        t = self.db.get_active_tournament(ctx.guild.id)
        if not t:
            await ctx.send("Нет активного турнира.")
            return
        if t["creator_id"] != ctx.author.id and not ctx.author.guild_permissions.administrator:
            await ctx.send("Только создатель или администратор может отменить.")
            return
        self.db.set_tournament_status(t["id"], "cancelled")
        self._dirty_tournaments.add(t["id"])
        await ctx.send(f"❌ Турнир **{t['name']}** отменён.")

    async def try_advance_tournament(self, tournament_id: int):
        """Проверяет, завершены ли все матчи текущего раунда, и создаёт следующий."""
        t = self.db.get_tournament(tournament_id)
        if not t or t["status"] != "in_progress":
            return
        matches = self.db.get_tournament_matches(tournament_id)
        if not matches:
            return
        max_round = max(m["round"] for m in matches)
        current_round_matches = [m for m in matches if m["round"] == max_round]
        unfinished = [m for m in current_round_matches if m["status"] != "finished"]
        if unfinished:
            return  # ещё не все матчи раунда завершены
        winners = [m["winner_id"] for m in current_round_matches if m["winner_id"]]
        if len(winners) == 1:
            # финал завершён — объявляем победителя
            self.db.set_tournament_winner(tournament_id, winners[0])
            self.db.add_shards(winners[0], SHARD_TOURNAMENT_WIN, f"tournament_win:{tournament_id}")
            self._dirty_tournaments.add(tournament_id)
            self._dirty_economy.add(winners[0])
            try:
                await self.check_achievements(winners[0])
            except Exception:
                pass
            return
        if len(winners) < 2:
            return
        # создаём матчи следующего раунда
        next_round = max_round + 1
        for i in range(0, len(winners), 2):
            p1 = winners[i]
            p2 = winners[i + 1] if i + 1 < len(winners) else None
            if p2:
                self.db.create_tournament_match(tournament_id, next_round, i // 2 + 1, p1, p2)
            else:
                # bye
                self.db.create_tournament_match(tournament_id, next_round, i // 2 + 1, p1, None)
                self.db.conn.execute(
                    "UPDATE tournament_matches SET winner_id=?, status='finished' "
                    "WHERE tournament_id=? AND round=? AND slot=? AND player2_id IS NULL",
                    (p1, tournament_id, next_round, i // 2 + 1))
                self.db.conn.commit()
        self.db.conn.commit()
        self._dirty_tournaments.add(tournament_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(DotaStats(bot))
