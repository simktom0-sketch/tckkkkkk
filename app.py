from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA = ROOT / "data"
DB_PATH = DATA / "poetry.db"
VPN_BLOCKLIST = DATA / "vpn_blocklist.txt"
AVATAR_UPLOADS = PUBLIC / "uploads" / "avatars"
PASSWORD_ITERATIONS = 180_000
MAX_AVATAR_BYTES = 3 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VISITOR_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,96}$")

FORBIDDEN_WORDS = [
    "террор",
    "террорист",
    "террористический",
    "экстремизм",
    "экстремист",
    "наркотики",
    "прон",
    "казино",
    "суицид",
    "убийство",
    "мат",
    "оскорбление",
    "война",
    "политика",
    "путин",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def one(cursor: sqlite3.Cursor) -> dict | None:
    item = cursor.fetchone()
    return dict(item) if item else None


def cert(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{datetime.now().year}-{digest}"


def scan_forbidden(text: str) -> list[str]:
    lowered = normalize_text(text)
    hits: list[str] = []
    for word in sorted({normalize_text(word) for word in FORBIDDEN_WORDS} | set(EXTRA_FORBIDDEN_TERMS)):
        if word and word in lowered:
            hits.append(word)
    return sorted(set(hits))


def safe_snippet(text: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] + ("..." if len(compact) > limit else "")


def title_from_body(body: str, limit: int = 56) -> str:
    compact = re.sub(r"\s+", " ", body).strip()
    if not compact:
        return "Без названия"
    if len(compact) <= limit:
        return compact
    head = compact[: limit + 1]
    cut = head.rfind(" ")
    if cut < 24:
        cut = limit
    stem = compact[:cut].rstrip(" ,.;:!?-").strip()
    if not stem:
        stem = compact[:limit].strip()
    return (stem or "Без названия") + ("..." if stem else "")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).replace("ё", "е").strip()


def clean_visitor_id(value: object) -> str:
    text = str(value or "").strip()[:96]
    if not text or not VISITOR_RE.fullmatch(text):
        return ""
    return text


def viewer_key(user_id: int, visitor_id: object = "") -> str:
    if user_id > 0:
        return f"user:{int(user_id)}"
    visitor = clean_visitor_id(visitor_id)
    return f"visitor:{visitor}" if visitor else ""


SOCIAL_LINK_LABELS = {
    "telegram": "Telegram",
    "vk": "VK",
    "tiktok": "TikTok",
}


def load_social_links(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        raw_links = value
    else:
        try:
            raw_links = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            raw_links = {}
    if not isinstance(raw_links, dict):
        return {}
    return {
        key: str(raw_links.get(key, "")).strip()
        for key in SOCIAL_LINK_LABELS
        if str(raw_links.get(key, "")).strip()
    }


def normalize_social_link(key: str, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw[:220]
    if raw.startswith("@"):
        raw = raw[1:]
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        handle = raw.strip().strip("/")
        if key == "telegram":
            raw = f"https://t.me/{handle}"
        elif key == "vk":
            raw = f"https://vk.com/{handle}"
        elif key == "tiktok":
            raw = f"https://www.tiktok.com/@{handle.lstrip('@')}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Проверьте ссылки на соцсети")
    return raw


def clean_social_links(payload: dict) -> dict[str, str]:
    source = payload.get("social_links") if isinstance(payload.get("social_links"), dict) else payload
    links: dict[str, str] = {}
    for key in SOCIAL_LINK_LABELS:
        value = normalize_social_link(key, source.get(key, ""))
        if value:
            links[key] = value
    return links


def normalize_email(value: object) -> str:
    email = str(value or "").strip().casefold()
    if not email or not EMAIL_RE.fullmatch(email):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Введите корректную почту")
    return email


def save_avatar_image(user_id: int, image_data: object) -> str:
    raw = str(image_data or "").strip()
    match = re.fullmatch(r"data:(image/(?:png|jpe?g|webp|gif));base64,(.+)", raw, re.DOTALL)
    if not match:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Загрузите PNG, JPG, WEBP или GIF")
    mime_type, encoded = match.groups()
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Не удалось прочитать изображение")
    if not data or len(data) > MAX_AVATAR_BYTES:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Аватар должен быть не больше 3 МБ")
    signatures = {
        "image/png": b"\x89PNG\r\n\x1a\n",
        "image/jpeg": b"\xff\xd8\xff",
        "image/jpg": b"\xff\xd8\xff",
        "image/webp": b"RIFF",
        "image/gif": b"GIF",
    }
    if not data.startswith(signatures[mime_type]):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Файл не похож на выбранный формат изображения")
    ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }[mime_type]
    AVATAR_UPLOADS.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:12]
    filename = f"user-{int(user_id)}-{digest}.{ext}"
    target = (AVATAR_UPLOADS / filename).resolve()
    if not str(target).startswith(str(AVATAR_UPLOADS.resolve())):
        raise ApiError(HTTPStatus.FORBIDDEN, "Недопустимый путь загрузки")
    target.write_bytes(data)
    return f"/uploads/avatars/{filename}"


def hash_password(password: object, salt: str | None = None) -> tuple[str, str]:
    raw = str(password or "")
    if len(raw) < 8:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Пароль должен быть не короче 8 символов")
    clean_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw.encode("utf-8"),
        clean_salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return clean_salt, digest


def verify_password(password: object, salt: str, expected_hash: str) -> bool:
    if not salt or not expected_hash:
        return False
    try:
        _, digest = hash_password(password, salt)
    except ApiError:
        return False
    return secrets.compare_digest(digest, expected_hash)


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_sessions(token, user_id, created_at) VALUES(?,?,?)",
        (token, user_id, now_iso()),
    )
    return token


MODERATION_RULES = {
    "profanity": [
        "бляд",
        "сук",
        "хуй",
        "пизд",
        "еб",
        "мудак",
        "дебил",
        "ублюд",
        "шлюх",
        "проститут",
        "гандон",
        "тварь",
        "идиот",
        "тупиц",
    ],
    "violence": [
        "террор",
        "террорист",
        "экстремизм",
        "экстремист",
        "бомба",
        "взрывчат",
        "оружие",
        "пистолет",
        "автомат",
        "нож",
    ],
    "drugs": [
        "наркот",
        "героин",
        "кокаин",
        "мефедрон",
        "спайс",
        "закладк",
        "дилер",
        "травк",
        "план",
    ],
    "fraud_spam": [
        "фишинг",
        "скам",
        "обнал",
        "база данных",
        "куплю базу",
        "продам базу",
        "слив",
        "ботферм",
        "массовая рассылка",
    ],
    "gambling": [
        "казино",
        "ставк",
        "букмекер",
        "беттинг",
        "фрибет",
    ],
    "privacy": [
        "паспорт",
        "снилс",
        "адрес",
        "телефон",
        "докс",
        "домашний адрес",
        "номер карты",
    ],
    "adult": [
        "порно",
        "порн",
        "эротик",
        "секс",
        "интим",
        "эскорт",
    ],
    "self_harm": [
        "суицид",
        "самоубий",
        "убей себя",
        "режь вены",
    ],
    "hate": [
        "нацист",
        "фашист",
        "расовая ненависть",
        "пропаганда ненависти",
    ],
    "public_risk": [
        "война",
        "политика",
        "путин",
    ],
}

EXTRA_FORBIDDEN_TERMS = sorted(
    {
        normalize_text(term)
        for terms in MODERATION_RULES.values()
        for term in terms
    }
)


TRANSLIT_MAP = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def slugify(value: str) -> str:
    mapped = normalize_text(value).translate(TRANSLIT_MAP)
    slug = re.sub(r"[^a-z0-9]+", "-", mapped)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "author"


def unique_handle(conn: sqlite3.Connection, value: str) -> str:
    base = slugify(value)
    handle = base
    suffix = 2
    while conn.execute("SELECT 1 FROM users WHERE handle = ?", (handle,)).fetchone():
        handle = f"{base}-{suffix}"
        suffix += 1
    return handle


def normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    result = []
    for item in items:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def parse_date_field(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    dot_match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if dot_match:
        day, month, year = dot_match.groups()
        return f"{year}-{month}-{day}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Неверный формат даты")
    return text


def prune_audit_log(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=31)).replace(microsecond=0).isoformat()
    conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))


def attach_genres(conn: sqlite3.Connection, poems: list[dict]) -> list[dict]:
    if not poems:
        return poems
    ids = [poem["id"] for poem in poems]
    placeholders = ",".join("?" for _ in ids)
    mapping: dict[int, list[str]] = {poem_id: [] for poem_id in ids}
    for row in conn.execute(
        f"SELECT poem_id, genre FROM poem_genres WHERE poem_id IN ({placeholders}) ORDER BY rowid",
        ids,
    ):
        mapping.setdefault(row["poem_id"], []).append(row["genre"])
    for poem in poems:
        genres = mapping.get(poem["id"], [])
        if not genres and poem.get("genre"):
            genres = [poem["genre"]]
        poem["genres"] = genres
        poem["genre_primary"] = genres[0] if genres else poem.get("genre", "")
    return poems


def attach_user_poem_flags(
    conn: sqlite3.Connection,
    poems: list[dict],
    user_id: int,
    viewer_key_value: str = "",
) -> list[dict]:
    if not poems:
        return poems
    for poem in poems:
        poem["favorited_by_me"] = 0
        poem["viewed_by_me"] = 0
    ids = [poem["id"] for poem in poems]
    placeholders = ",".join("?" for _ in ids)
    if user_id > 0:
        favorite_ids = {
            row["poem_id"]
            for row in conn.execute(
                f"SELECT poem_id FROM favorites WHERE user_id = ? AND poem_id IN ({placeholders})",
                [user_id, *ids],
            )
        }
        for poem in poems:
            poem["favorited_by_me"] = 1 if poem["id"] in favorite_ids else 0
    if viewer_key_value:
        seen_ids = {
            row["poem_id"]
            for row in conn.execute(
                f"SELECT poem_id FROM poem_seen WHERE viewer_key = ? AND poem_id IN ({placeholders})",
                [viewer_key_value, *ids],
            )
        }
        for poem in poems:
            poem["viewed_by_me"] = 1 if poem["id"] in seen_ids else 0
    return poems


def poem_views_count_sql(alias: str = "poems") -> str:
    return f"(SELECT COUNT(*) FROM poem_seen WHERE poem_seen.poem_id = {alias}.id) AS views_count"


def display_name(user: dict) -> str:
    pseudo = (user.get("pseudonym") or "").strip()
    if pseudo and pseudo.lower() not in {user.get("name", "").strip().lower(), user.get("handle", "").strip().lower()}:
        return f"{user.get('name', '')} · {pseudo}"
    return user.get("name", "")


def create_author_user(
    conn: sqlite3.Connection,
    *,
    name: str,
    email: str = "",
    password: str = "",
    pseudonym: str = "",
    birth_date: str = "",
    death_date: str = "",
    bio: str = "",
    private_access: int = 0,
    role: str = "author",
) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Имя обязательно")
    clean_email = ""
    password_salt = ""
    password_hash = ""
    if email:
        clean_email = normalize_email(email)
        if one(conn.execute("SELECT id FROM users WHERE email = ?", (clean_email,))):
            raise ApiError(HTTPStatus.CONFLICT, "Пользователь с такой почтой уже есть")
        password_salt, password_hash = hash_password(password)
    clean_pseudonym = str(pseudonym or "").strip()
    clean_birth = parse_date_field(birth_date)
    clean_death = parse_date_field(death_date)
    handle_source = clean_pseudonym or clean_name
    handle = unique_handle(conn, handle_source)
    created = now_iso()
    certificate = cert("AUTHOR", handle, created, clean_name, clean_pseudonym, clean_birth)
    cur = conn.execute(
        """
        INSERT INTO users(name, handle, role, blocked, bio, author_certificate, created_at, pseudonym, birth_date, death_date, private_access, email, password_salt, password_hash)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            clean_name,
            handle,
            role,
            0,
            bio or "",
            certificate,
            created,
            clean_pseudonym,
            clean_birth,
            clean_death,
            int(private_access),
            clean_email,
            password_salt,
            password_hash,
        ),
    )
    return get_user(conn, cur.lastrowid)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  handle TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL CHECK(role IN ('reader','author','moderator','admin')),
  blocked INTEGER NOT NULL DEFAULT 0,
  bio TEXT NOT NULL DEFAULT '',
  author_certificate TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  password_salt TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL DEFAULT '',
  pseudonym TEXT NOT NULL DEFAULT '',
  birth_date TEXT NOT NULL DEFAULT '',
  death_date TEXT NOT NULL DEFAULT '',
  private_access INTEGER NOT NULL DEFAULT 0,
  avatar_url TEXT NOT NULL DEFAULT '',
  social_links TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poems (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  untitled INTEGER NOT NULL DEFAULT 0,
  genre TEXT NOT NULL,
  style TEXT NOT NULL,
  section TEXT NOT NULL CHECK(section IN ('classic','modern','foreign')),
  author_id INTEGER NOT NULL REFERENCES users(id),
  created_by INTEGER NOT NULL REFERENCES users(id),
  certificate TEXT NOT NULL,
  comments_enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK(status IN ('published','pending','rejected')),
  deleted INTEGER NOT NULL DEFAULT 0,
  deleted_at TEXT,
  deleted_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poem_genres (
  poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  genre TEXT NOT NULL,
  PRIMARY KEY(poem_id, genre)
);

CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id INTEGER NOT NULL REFERENCES poems(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('published','pending','rejected','deleted')),
  created_at TEXT NOT NULL,
  deleted_at TEXT,
  deleted_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS likes (
  user_id INTEGER NOT NULL REFERENCES users(id),
  poem_id INTEGER NOT NULL REFERENCES poems(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, poem_id)
);

CREATE TABLE IF NOT EXISTS favorites (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, poem_id)
);

CREATE TABLE IF NOT EXISTS shares (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poem_views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  visitor_id TEXT NOT NULL DEFAULT '',
  viewer_key TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poem_seen (
  poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
  viewer_key TEXT NOT NULL,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  visitor_id TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  views_count INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(poem_id, viewer_key)
);

CREATE TABLE IF NOT EXISTS subscriptions (
  follower_id INTEGER NOT NULL REFERENCES users(id),
  author_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY(follower_id, author_id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poem_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  poem_id INTEGER NOT NULL REFERENCES poems(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open','dismissed','deleted')) DEFAULT 'open',
  created_at TEXT NOT NULL,
  resolved_by INTEGER REFERENCES users(id),
  resolved_at TEXT,
  resolution TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS author_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('published','deleted')) DEFAULT 'published',
  created_at TEXT NOT NULL,
  deleted_at TEXT,
  deleted_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS news (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  event_date TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS moderation_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_type TEXT NOT NULL CHECK(item_type IN ('poem','comment')),
  item_id INTEGER NOT NULL,
  submitted_by INTEGER NOT NULL REFERENCES users(id),
  hits TEXT NOT NULL,
  snippet TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open','approved','rejected')) DEFAULT 'open',
  created_at TEXT NOT NULL,
  resolved_by INTEGER REFERENCES users(id),
  resolved_at TEXT,
  resolution TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS private_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS private_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


SEED_USERS = [
    ("Алексей Северин", "admin", "admin", "Администратор платформы, отвечает за правила, доступы и безопасность."),
    ("Марина Вереск", "marina", "moderator", "Модератор новостей и литературных подборок."),
    ("Лина Март", "lina", "author", "Современная поэзия, городская лирика, свободный стих."),
    ("Николай Рейн", "rein", "author", "Классическая рифма, философская и любовная лирика."),
    ("Ирина Соль", "sol", "reader", "Читатель, подписана на современных авторов."),
]

SEED_POEMS = [
    (
        "Северная строка",
        "Я слышу город в переплете крыш,\nгде дождь листает окна осторожно.\nИ если ты сегодня промолчишь,\nя допишу молчание возможно.",
        "философия",
        "белый стих",
        "modern",
        3,
        3,
        1,
        "published",
    ),
    (
        "Письмо у реки",
        "Ты говорила: вода унесет имена,\nно берег помнит шаги терпеливо.\nИ осень, как поздняя глубина,\nнам возвращает почти невозможное.",
        "романтика",
        "классическая рифма",
        "modern",
        4,
        4,
        1,
        "published",
    ),
    (
        "Архивное небо",
        "Над старой площадью меркнет заря,\nи строки ложатся в спокойные книги.\nТак память работает, тихо горя,\nснимая с мгновения хрупкие сдвиги.",
        "гражданская лирика",
        "классическая рифма",
        "classic",
        2,
        2,
        0,
        "published",
    ),
    (
        "Перевод с ветра",
        "В чужом языке просыпается сад,\nи ветви касаются края страницы.\nМы слышим не буквы, а дальний уклад,\nгде смыслу позволено снова родиться.",
        "перевод",
        "верлибр",
        "foreign",
        2,
        2,
        1,
        "published",
    ),
]

SEED_NEWS = [
    ("Вечер современной лирики", "Открыта регистрация авторов на онлайн-чтения с редакторской обратной связью.", "2026-08-02"),
    ("Конкурс переводов", "Принимаются переводы зарубежной поэзии с обязательным указанием источника.", "2026-08-12"),
    ("Правила комментариев", "Модерация обновила правила уважительного обсуждения произведений.", "2026-08-20"),
]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def migrate_schema(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "users", "email", "email TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "password_salt", "password_salt TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "password_hash", "password_hash TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "pseudonym", "pseudonym TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "birth_date", "birth_date TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "death_date", "death_date TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "private_access", "private_access INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "users", "avatar_url", "avatar_url TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "users", "social_links", "social_links TEXT NOT NULL DEFAULT '{}'")
    ensure_column(conn, "poems", "deleted", "deleted INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "poems", "deleted_at", "deleted_at TEXT")
    ensure_column(conn, "poems", "deleted_by", "deleted_by INTEGER REFERENCES users(id)")
    ensure_column(conn, "poems", "untitled", "untitled INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poem_genres (
          poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
          genre TEXT NOT NULL,
          PRIMARY KEY(poem_id, genre)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS private_notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
          token TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poem_reports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          poem_id INTEGER NOT NULL REFERENCES poems(id),
          user_id INTEGER NOT NULL REFERENCES users(id),
          body TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('open','dismissed','deleted')) DEFAULT 'open',
          created_at TEXT NOT NULL,
          resolved_by INTEGER REFERENCES users(id),
          resolved_at TEXT,
          resolution TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS author_comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          body TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('published','deleted')) DEFAULT 'published',
          created_at TEXT NOT NULL,
          deleted_at TEXT,
          deleted_by INTEGER REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS private_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          body TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL,
          PRIMARY KEY(user_id, poem_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shares (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
          poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poem_views (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
          user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
          visitor_id TEXT NOT NULL DEFAULT '',
          viewer_key TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poem_seen (
          poem_id INTEGER NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
          viewer_key TEXT NOT NULL,
          user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
          visitor_id TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          views_count INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(poem_id, viewer_key)
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email <> ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_poem_views_poem ON poem_views(poem_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_poem_views_viewer ON poem_views(viewer_key, poem_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_poem_seen_viewer ON poem_seen(viewer_key, poem_id)")
    conn.execute("UPDATE users SET pseudonym = handle WHERE pseudonym = ''")
    conn.execute("UPDATE users SET private_access = 1 WHERE handle IN ('lina')")
    sample_salt, sample_hash = hash_password("demo12345")
    for row in conn.execute("SELECT id, handle, email, password_hash FROM users"):
        if not row["email"]:
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (f"{row['handle']}@tochkapoeta.local", row["id"]))
        if not row["password_hash"]:
            conn.execute("UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?", (sample_salt, sample_hash, row["id"]))
    for poem in conn.execute("SELECT id, genre FROM poems"):
        if poem["genre"]:
            conn.execute(
                "INSERT OR IGNORE INTO poem_genres(poem_id, genre) VALUES(?,?)",
                (poem["id"], poem["genre"]),
            )
    has_users = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"] > 0
    has_private_notes = conn.execute("SELECT COUNT(*) AS count FROM private_notes").fetchone()["count"] > 0
    if has_users and not has_private_notes:
        created = now_iso()
        conn.executemany(
            "INSERT INTO private_notes(title, body, created_by, created_at) VALUES(?,?,?,?)",
            [
                (
                    "Закрытый редакционный круг",
                    "Эта страница видна только конкретным пользователям из allowlist, модераторам и администраторам. Обычные читатели и авторы не увидят вкладку и получат отказ при прямом переходе.",
                    1,
                    created,
                ),
                (
                    "Пример приватной подборки",
                    "Сюда можно вынести закрытые анонсы, внутренние правила, приватные чтения или доступ к материалам только для выбранных людей.",
                    1,
                    created,
                ),
            ],
        )
    prune_audit_log(conn)


def ensure_seed_author(conn: sqlite3.Connection, handle: str, name: str, pseudonym: str, birth_date: str, bio: str) -> int:
    existing = one(conn.execute("SELECT id FROM users WHERE handle = ?", (handle,)))
    if existing:
        return int(existing["id"])
    created = now_iso()
    salt, password_hash = hash_password("demo12345")
    cur = conn.execute(
        """
        INSERT INTO users(name, handle, role, blocked, bio, author_certificate, created_at, pseudonym, birth_date, email, password_salt, password_hash)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            name,
            handle,
            "author",
            0,
            bio,
            cert("AUTHOR", handle, created, name),
            created,
            pseudonym,
            birth_date,
            f"{handle}@tochkapoeta.local",
            salt,
            password_hash,
        ),
    )
    return int(cur.lastrowid)


def ensure_seed_library(conn: sqlite3.Connection) -> None:
    published_count = one(conn.execute(
        "SELECT COUNT(*) AS count FROM poems WHERE status = 'published' AND COALESCE(deleted, 0) = 0"
    ))["count"]
    if published_count >= 100:
        return
    author_specs = [
        ("vera-list", "Вера Лист", "Лист", "1988-04-11", "Тонкая пейзажная лирика и миниатюры."),
        ("arseniy-berg", "Арсений Берг", "Берг", "1977-10-02", "Философские тексты и строгая рифма."),
        ("mira-ven", "Мира Вен", "Вен", "1992-06-18", "Городская романтика, верлибр, ночные маршруты."),
        ("timur-sneg", "Тимур Снег", "Снег", "1983-01-29", "Мистика, северные мотивы и белый стих."),
        ("alisa-kedr", "Алиса Кедр", "Кедр", "1995-09-09", "Современная лирика о природе и памяти."),
        ("roman-tihiy", "Роман Тихий", "Тихий", "1971-03-21", "Классическая рифма, сонеты, камерные циклы."),
        ("elena-var", "Елена Вар", "Вар", "1980-07-14", "Переводы и мягкая интеллектуальная поэзия."),
        ("mark-orlov", "Марк Орлов", "Орлов", "1990-11-05", "Гражданская и философская лирика без лозунгов."),
        ("sofia-luch", "София Луч", "Луч", "1998-12-22", "Романтические тексты, короткая форма, музыка строки."),
        ("denis-yal", "Денис Ял", "Ял", "1986-05-30", "Верлибр, поездные станции, влажный свет."),
        ("nora-kiro", "Нора Киро", "Киро", "1993-08-17", "Зарубежные мотивы и переводная интонация."),
        ("pavel-rov", "Павел Ров", "Ров", "1979-02-08", "Сдержанная мужская лирика и пейзажные циклы."),
    ]
    author_ids = [ensure_seed_author(conn, *spec) for spec in author_specs]
    genres = ["романтика", "философия", "гражданская лирика", "перевод", "мистика", "пейзажная лирика"]
    styles = ["классическая рифма", "белый стих", "верлибр", "сонет", "миниатюра"]
    sections = ["classic", "modern", "foreign"]
    nouns = ["берег", "город", "снег", "фонарь", "сад", "письмо", "мост", "окно", "ветер", "архив"]
    titles = [
        "Тихий переплет", "Сад после дождя", "Ночной маршрут", "Письмо на стекле", "Северный сонет",
        "Голоса площади", "Лунная пристань", "Чернила августа", "Дом у воды", "Перевод тишины",
        "Пять строк о свете", "Карта зимы", "Окно над рекой", "Медленный поезд", "Память бумаги",
        "Верлибр для утра", "Классическая пауза", "Сумеречный сад", "Городская соль", "Страница ветра",
    ]
    lines = [
        "В {noun}е дышит тонкая прохлада,",
        "и свет ложится бережно на край.",
        "Я собираю день без лишней брани,",
        "чтобы строка не торопила май.",
        "Там, где молчит усталая дорога,",
        "мы слышим сад, оставленный дождем.",
        "И даже ночь становится не строгой,",
        "когда ее по имени зовем.",
    ]
    need = max(0, 104 - int(published_count))
    for index in range(need):
        author_id = author_ids[index % len(author_ids)]
        genre_a = genres[index % len(genres)]
        genre_b = genres[(index + 2) % len(genres)]
        style = styles[index % len(styles)]
        section = sections[index % len(sections)]
        title = f"{titles[index % len(titles)]} {index + 1}"
        if one(conn.execute("SELECT id FROM poems WHERE title = ?", (title,))):
            continue
        noun = nouns[index % len(nouns)]
        body = "\n".join(line.format(noun=noun) for line in lines[index % 4:index % 4 + 4])
        if body.count("\n") < 3:
            body = "\n".join(line.format(noun=noun) for line in lines[:4])
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=index * 13)).replace(microsecond=0).isoformat()
        certificate = cert("POEM", title, author_id, created_at, secrets.token_hex(4))
        cur = conn.execute(
            """
            INSERT INTO poems(title, body, genre, style, section, author_id, created_by, certificate, comments_enabled, status, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (title, body, genre_a, style, section, author_id, author_id, certificate, 1, "published", created_at),
        )
        poem_id = int(cur.lastrowid)
        for genre in {genre_a, genre_b}:
            conn.execute("INSERT OR IGNORE INTO poem_genres(poem_id, genre) VALUES(?,?)", (poem_id, genre))


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if not VPN_BLOCKLIST.exists():
      VPN_BLOCKLIST.write_text("# Add one IP per line for VPN/proxy blocking.\n203.0.113.9\n", encoding="utf-8")
    with connect() as conn:
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        existing = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        if existing:
            ensure_seed_library(conn)
            return
        created = now_iso()
        for name, handle, role, bio in SEED_USERS:
            conn.execute(
                "INSERT INTO users(name, handle, role, bio, author_certificate, created_at) VALUES(?,?,?,?,?,?)",
                (name, handle, role, bio, cert("AUTHOR", handle, created), created),
            )
        for title, body, genre, style, section, author_id, created_by, comments_enabled, status in SEED_POEMS:
            conn.execute(
                """
                INSERT INTO poems(title, body, genre, style, section, author_id, created_by, certificate, comments_enabled, status, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (title, body, genre, style, section, author_id, created_by, cert("POEM", title, author_id), comments_enabled, status, created),
            )
        conn.executemany(
            "INSERT INTO news(title, body, event_date, created_by, created_at) VALUES(?,?,?,?,?)",
            [(title, body, date, 2, created) for title, body, date in SEED_NEWS],
        )
        conn.executemany(
            "INSERT INTO likes(user_id, poem_id, created_at) VALUES(?,?,?)",
            [(5, 1, created), (5, 2, created), (3, 2, created), (4, 1, created)],
        )
        conn.executemany(
            "INSERT INTO subscriptions(follower_id, author_id, created_at) VALUES(?,?,?)",
            [(5, 3, created), (5, 4, created), (3, 4, created)],
        )
        conn.executemany(
            "INSERT INTO comments(poem_id, user_id, body, status, created_at) VALUES(?,?,?,?,?)",
            [
                (1, 5, "Строгий и очень точный образ города. Спасибо за публикацию.", "published", created),
                (2, 3, "Теплая рифма, особенно последняя строка.", "published", created),
            ],
        )
        migrate_schema(conn)
        ensure_seed_library(conn)
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, details, created_at) VALUES(?,?,?,?,?)",
            (1, "seed", "database", "Initial seed data created", created),
        )


def audit(conn: sqlite3.Connection, actor_id: int | None, action: str, target: str, details: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(actor_id, action, target, details, created_at) VALUES(?,?,?,?,?)",
        (actor_id, action, target, details, now_iso()),
    )


def get_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    return one(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)))


def guest_user() -> dict:
    return {
        "id": 0,
        "name": "Гость",
        "handle": "guest",
        "role": "reader",
        "blocked": 0,
        "bio": "",
        "author_certificate": "",
        "email": "",
        "password_salt": "",
        "password_hash": "",
        "pseudonym": "",
        "birth_date": "",
        "death_date": "",
        "private_access": 0,
        "avatar_url": "",
        "created_at": "",
    }


def public_user(user: dict) -> dict:
    clean = dict(user)
    clean.pop("password_hash", None)
    clean.pop("password_salt", None)
    clean["social_links"] = load_social_links(clean.get("social_links"))
    return clean


def require_user(conn: sqlite3.Connection, payload: dict | None = None, query: dict | None = None) -> dict:
    raw = None
    token = None
    if payload:
        raw = payload.get("user_id")
        token = payload.get("auth_token")
    if raw is None and query:
        raw_values = query.get("user_id", ["0"])
        raw = raw_values[0]
    if token is None and query:
        token_values = query.get("auth_token", [""])
        token = token_values[0]
    if token:
        session = one(conn.execute(
            """
            SELECT users.*
            FROM auth_sessions JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token = ?
            """,
            (str(token),),
        ))
        if not session:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Сессия не найдена, войдите заново")
        return session
    try:
        user_id = int(raw or 0)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Некорректный user_id")
    if user_id == 0:
        return guest_user()
    user = get_user(conn, user_id)
    if not user:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "Пользователь не найден")
    return user


def ensure_role(user: dict, roles: set[str]) -> None:
    if user["role"] not in roles:
        raise ApiError(HTTPStatus.FORBIDDEN, "Недостаточно прав")


def ensure_not_blocked(user: dict) -> None:
    if user["blocked"]:
        raise ApiError(HTTPStatus.FORBIDDEN, "Пользователь заблокирован")


def ensure_registered(user: dict) -> None:
    if user["id"] == 0:
        raise ApiError(HTTPStatus.FORBIDDEN, "Нужна регистрация")


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class PoetryHandler(BaseHTTPRequestHandler):
    server_version = "PoetrySovereign/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'")
        super().end_headers()

    def do_GET(self) -> None:
        if self.block_vpn_if_needed():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api("GET", parsed.path, parse_qs(parsed.query))
            return
        self.serve_static_or_app(parsed.path)

    def do_POST(self) -> None:
        if self.block_vpn_if_needed():
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.json_response({"error": "Некорректный JSON"}, HTTPStatus.BAD_REQUEST)
            return
        self.handle_api("POST", parsed.path, parse_qs(parsed.query), payload)

    def block_vpn_if_needed(self) -> bool:
        client_ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        suspected = self.headers.get("X-VPN-Suspected", "").lower() in {"1", "true", "yes"}
        blocklist = set()
        if VPN_BLOCKLIST.exists():
            blocklist = {
                line.strip()
                for line in VPN_BLOCKLIST.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
        if suspected or client_ip in blocklist:
            self.json_response(
                {
                    "error": "Доступ ограничен",
                    "reason": "Подключение похоже на VPN/proxy или IP находится в блок-листе.",
                },
                HTTPStatus.FORBIDDEN,
            )
            return True
        return False

    def serve_static_or_app(self, path: str) -> None:
        if path in {"/", ""} or "." not in Path(path).name:
            target = PUBLIC / "index.html"
        else:
            target = (PUBLIC / path.lstrip("/")).resolve()
            if not str(target).startswith(str(PUBLIC.resolve())):
                self.json_response({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
                return
        if not target.exists() or not target.is_file():
            self.json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self, method: str, path: str, query: dict, payload: dict | None = None) -> None:
        try:
            with connect() as conn:
                prune_audit_log(conn)
                if method == "GET" and path == "/api/bootstrap":
                    user = require_user(conn, query=query)
                    self.json_response(self.api_bootstrap(conn, user))
                elif method == "GET" and path == "/api/feed":
                    user = require_user(conn, query=query)
                    self.json_response({"poems": self.api_feed(conn, user, query)})
                elif method == "GET" and path == "/api/poems":
                    self.json_response({"poems": self.api_poems(conn, query)})
                elif method == "GET" and path == "/api/poem":
                    self.json_response(self.api_poem(conn, query))
                elif method == "GET" and path == "/api/author":
                    self.json_response(self.api_author(conn, query))
                elif method == "GET" and path == "/api/news":
                    self.json_response({"news": rows(conn.execute("SELECT news.*, users.name AS author_name FROM news JOIN users ON users.id = news.created_by ORDER BY event_date DESC"))})
                elif method == "GET" and path == "/api/admin":
                    user = require_user(conn, query=query)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_admin(conn, user))
                elif method == "GET" and path == "/api/moderation":
                    user = require_user(conn, query=query)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_moderation(conn))
                elif method == "GET" and path == "/api/private":
                    user = require_user(conn, query=query)
                    self.json_response(self.api_private(conn, user))
                elif method == "GET" and path == "/api/profile":
                    user = require_user(conn, query=query)
                    ensure_registered(user)
                    self.json_response(self.api_profile(conn, user))
                elif method == "GET" and path == "/api/favorites":
                    user = require_user(conn, query=query)
                    ensure_registered(user)
                    self.json_response({"poems": self.api_favorites(conn, user)})
                elif method == "POST" and path == "/api/register":
                    self.json_response(self.api_register(conn, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/login":
                    self.json_response(self.api_login(conn, payload or {}))
                elif method == "POST" and path == "/api/poems":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_create_poem(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/reports":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_create_report(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/comments":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_create_comment(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/author/comment":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_create_author_comment(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/avatar":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_update_avatar(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/profile/socials":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_update_social_links(conn, user, payload or {}))
                elif method == "POST" and path == "/api/private/messages":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_create_private_message(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/private/notes":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_create_private_note(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/poems/comments":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_toggle_poem_comments(conn, user, payload or {}))
                elif method == "POST" and path == "/api/like":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_like(conn, user, payload or {}))
                elif method == "POST" and path == "/api/favorite":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_favorite(conn, user, payload or {}))
                elif method == "POST" and path == "/api/share":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_share(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/views":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_record_view(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/subscribe":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_subscribe(conn, user, payload or {}))
                elif method == "POST" and path == "/api/news":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_create_news(conn, user, payload or {}), HTTPStatus.CREATED)
                elif method == "POST" and path == "/api/news/delete":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_delete_news(conn, user, payload or {}))
                elif method == "POST" and path == "/api/comments/delete":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_delete_comment(conn, user, payload or {}))
                elif method == "POST" and path == "/api/admin/role":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin"})
                    self.json_response(self.api_set_role(conn, user, payload or {}))
                elif method == "POST" and path == "/api/admin/block":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_block_user(conn, user, payload or {}))
                elif method == "POST" and path == "/api/admin/private-access":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin"})
                    self.json_response(self.api_set_private_access(conn, user, payload or {}))
                elif method == "POST" and path == "/api/admin/delete-poem":
                    user = require_user(conn, payload=payload)
                    self.json_response(self.api_delete_poem(conn, user, payload or {}))
                elif method == "POST" and path == "/api/moderation/resolve":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_resolve_moderation(conn, user, payload or {}))
                elif method == "POST" and path == "/api/moderation/report":
                    user = require_user(conn, payload=payload)
                    ensure_role(user, {"admin", "moderator"})
                    self.json_response(self.api_resolve_report(conn, user, payload or {}))
                else:
                    self.json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ApiError as exc:
            self.json_response({"error": exc.message}, exc.status)
        except Exception as exc:
            self.json_response({"error": "Внутренняя ошибка сервера", "details": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def json_response(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api_bootstrap(self, conn: sqlite3.Connection, user: dict) -> dict:
        return {
            "me": public_user(user),
            "users": [public_user(user) for user in rows(conn.execute("SELECT id, name, handle, role, blocked, bio, author_certificate, email, pseudonym, birth_date, death_date, private_access, avatar_url, social_links, created_at FROM users ORDER BY role DESC, name"))],
            "genres": ["романтика", "философия", "гражданская лирика", "перевод", "мистика", "пейзажная лирика"],
            "styles": ["классическая рифма", "белый стих", "верлибр", "сонет", "миниатюра"],
            "sections": [
                {"id": "classic", "title": "Поэзия классиков"},
                {"id": "modern", "title": "Поэзия современности"},
                {"id": "foreign", "title": "Зарубежная поэзия"},
            ],
            "forbiddenWords": sorted({normalize_text(word) for word in FORBIDDEN_WORDS} | set(EXTRA_FORBIDDEN_TERMS)),
            "moderationRules": MODERATION_RULES,
            "canAccessPrivate": bool(user.get("private_access")) or user["role"] in {"admin", "moderator"},
        }

    def api_feed(self, conn: sqlite3.Connection, user: dict, query: dict) -> list[dict]:
        q = (query.get("q", [""])[0] or "").strip().lower()
        genre = (query.get("genre", [""])[0] or "").strip()
        style = (query.get("style", [""])[0] or "").strip()
        mode = (query.get("mode", ["recommended"])[0] or "recommended").strip()
        try:
            cycle = int(query.get("cycle", ["0"])[0] or 0)
        except ValueError:
            cycle = 0
        liked_genres = {
            row["genre"]
            for row in conn.execute(
                """
                SELECT DISTINCT poem_genres.genre
                FROM likes
                JOIN poem_genres ON poem_genres.poem_id = likes.poem_id
                WHERE likes.user_id = ?
                """,
                (user["id"],),
            )
        }
        followed_authors = {
            row["author_id"]
            for row in conn.execute("SELECT author_id FROM subscriptions WHERE follower_id = ?", (user["id"],))
        }
        poem_rows = self.api_poems(conn, query)
        current_viewer_key = viewer_key(user["id"], query.get("visitor_id", [""])[0])
        if current_viewer_key:
            seen_ids = {
                row["poem_id"]
                for row in conn.execute(
                    "SELECT poem_id FROM poem_seen WHERE viewer_key = ?",
                    (current_viewer_key,),
                )
            }
            poem_rows = [poem for poem in poem_rows if poem["id"] not in seen_ids]
        if mode == "following":
            poem_rows = [poem for poem in poem_rows if poem["author_id"] in followed_authors]
        scored = []
        for poem in poem_rows:
            poem_genres = set(poem.get("genres") or [poem.get("genre", "")])
            text = " ".join([poem["title"], poem["body"], " ".join(poem_genres), poem["style"], poem["author_name"]]).lower()
            views_count = int(poem.get("views_count") or 0)
            score = poem["likes_count"] * 10 + poem["comments_count"] * 4
            score += min(views_count, 250) * 0.35
            if views_count == 0:
                score += 18
            if mode != "community":
                if liked_genres & poem_genres:
                    score += 80
                if poem["author_id"] in followed_authors:
                    score += 120
                if genre and genre in poem_genres:
                    score += 130
                if style and poem["style"] == style:
                    score += 90
                if q and q in text:
                    score += 200
            score += ((poem["id"] * 37 + cycle * 53) % 31)
            poem["recommendation_score"] = round(score, 2)
            poem["recommendation_reason"] = self.reason(poem, liked_genres, followed_authors, bool(q))
            scored.append(poem)
        return sorted(scored, key=lambda item: item["recommendation_score"], reverse=True)

    @staticmethod
    def reason(poem: dict, liked_genres: set[str], followed_authors: set[int], has_query: bool) -> str:
        reasons = []
        poem_genres = set(poem.get("genres") or [poem.get("genre", "")])
        if poem["author_id"] in followed_authors:
            reasons.append("автор в подписках")
        if liked_genres & poem_genres:
            reasons.append("жанр часто нравится")
        if has_query:
            reasons.append("совпадение с поиском")
        if poem["likes_count"] > 0:
            reasons.append("есть отклик читателей")
        return ", ".join(reasons[:3]) or "новая публикация"

    def api_poems(self, conn: sqlite3.Connection, query: dict) -> list[dict]:
        section = (query.get("section", [""])[0] or "").strip()
        try:
            current_user_id = int(query.get("user_id", ["0"])[0] or 0)
        except ValueError:
            current_user_id = 0
        current_viewer_key = viewer_key(current_user_id, query.get("visitor_id", [""])[0])
        params: list[object] = []
        where = "poems.status = 'published' AND COALESCE(poems.deleted, 0) = 0"
        if section:
            where += " AND poems.section = ?"
            params.append(section)
        sql = f"""
            SELECT poems.*, users.name AS author_name, users.handle AS author_handle, users.pseudonym AS author_pseudonym, users.avatar_url AS author_avatar_url,
              (SELECT COUNT(*) FROM likes WHERE likes.poem_id = poems.id) AS likes_count,
              (SELECT COUNT(*) FROM comments WHERE comments.poem_id = poems.id AND comments.status = 'published') AS comments_count,
              (SELECT COUNT(*) FROM favorites WHERE favorites.poem_id = poems.id) AS favorite_count,
              (SELECT COUNT(*) FROM shares WHERE shares.poem_id = poems.id) AS share_count,
              {poem_views_count_sql()}
            FROM poems
            JOIN users ON users.id = poems.author_id
            WHERE {where}
            ORDER BY poems.created_at DESC, poems.id DESC
        """
        poems = attach_genres(conn, rows(conn.execute(sql, params)))
        return attach_user_poem_flags(conn, poems, current_user_id, current_viewer_key)

    def api_poem(self, conn: sqlite3.Connection, query: dict) -> dict:
        poem_id = int(query.get("id", ["0"])[0] or 0)
        poem = one(conn.execute(
            f"""
            SELECT poems.*, users.name AS author_name, users.handle AS author_handle, users.pseudonym AS author_pseudonym, users.avatar_url AS author_avatar_url,
              (SELECT COUNT(*) FROM likes WHERE likes.poem_id = poems.id) AS likes_count,
              (SELECT COUNT(*) FROM comments WHERE comments.poem_id = poems.id AND comments.status = 'published') AS comments_count,
              (SELECT COUNT(*) FROM favorites WHERE favorites.poem_id = poems.id) AS favorite_count,
              (SELECT COUNT(*) FROM shares WHERE shares.poem_id = poems.id) AS share_count,
              {poem_views_count_sql()}
            FROM poems JOIN users ON users.id = poems.author_id
            WHERE poems.id = ? AND poems.status = 'published' AND COALESCE(poems.deleted, 0) = 0
            """,
            (poem_id,),
        ))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        attach_genres(conn, [poem])
        try:
            current_user_id = int(query.get("user_id", ["0"])[0] or 0)
        except ValueError:
            current_user_id = 0
        current_viewer_key = viewer_key(current_user_id, query.get("visitor_id", [""])[0])
        attach_user_poem_flags(conn, [poem], current_user_id, current_viewer_key)
        comments = rows(conn.execute(
            """
            SELECT comments.*, users.name AS user_name, users.handle AS user_handle
            FROM comments JOIN users ON users.id = comments.user_id
            WHERE comments.poem_id = ? AND comments.status = 'published'
            ORDER BY comments.created_at ASC
            """,
            (poem_id,),
        ))
        poem["comments"] = comments
        return {"poem": poem}

    def api_author(self, conn: sqlite3.Connection, query: dict) -> dict:
        raw = query.get("id", [""])[0]
        try:
            current_user_id = int(query.get("user_id", ["0"])[0] or 0)
        except ValueError:
            current_user_id = 0
        current_viewer_key = viewer_key(current_user_id, query.get("visitor_id", [""])[0])
        if raw.isdigit():
            author = one(conn.execute("SELECT * FROM users WHERE id = ?", (int(raw),)))
        else:
            author = one(conn.execute("SELECT * FROM users WHERE handle = ?", (raw,)))
        if not author:
            raise ApiError(HTTPStatus.NOT_FOUND, "Автор не найден")
        poems = rows(conn.execute(
            f"""
            SELECT poems.*,
              users.name AS author_name,
              users.handle AS author_handle,
              users.pseudonym AS author_pseudonym,
              users.avatar_url AS author_avatar_url,
              (SELECT COUNT(*) FROM likes WHERE likes.poem_id = poems.id) AS likes_count,
              (SELECT COUNT(*) FROM comments WHERE comments.poem_id = poems.id AND comments.status = 'published') AS comments_count,
              (SELECT COUNT(*) FROM favorites WHERE favorites.poem_id = poems.id) AS favorite_count,
              (SELECT COUNT(*) FROM shares WHERE shares.poem_id = poems.id) AS share_count,
              {poem_views_count_sql()}
            FROM poems JOIN users ON users.id = poems.author_id
            WHERE poems.author_id = ? AND poems.status = 'published' AND COALESCE(poems.deleted, 0) = 0
            ORDER BY poems.created_at DESC
            """,
            (author["id"],),
        ))
        followers = one(conn.execute("SELECT COUNT(*) AS count FROM subscriptions WHERE author_id = ?", (author["id"],)))["count"]
        following_count = one(conn.execute("SELECT COUNT(*) AS count FROM subscriptions WHERE follower_id = ?", (author["id"],)))["count"]
        is_subscribed = 0
        if current_user_id > 0:
            is_subscribed = 1 if one(conn.execute(
                "SELECT 1 FROM subscriptions WHERE follower_id = ? AND author_id = ?",
                (current_user_id, author["id"]),
            )) else 0
        stats = one(conn.execute(
            """
            SELECT
              COUNT(DISTINCT poems.id) AS poems_count,
              COUNT(DISTINCT likes.user_id || ':' || likes.poem_id) AS likes_total,
              COUNT(DISTINCT comments.id) AS comments_total,
              COUNT(DISTINCT poem_seen.poem_id || ':' || poem_seen.viewer_key) AS views_total
            FROM poems
            LEFT JOIN likes ON likes.poem_id = poems.id
            LEFT JOIN comments ON comments.poem_id = poems.id AND comments.status = 'published'
            LEFT JOIN poem_seen ON poem_seen.poem_id = poems.id
            WHERE poems.author_id = ? AND poems.status = 'published' AND COALESCE(poems.deleted, 0) = 0
            """,
            (author["id"],),
        ))
        author_comments = rows(conn.execute(
            """
            SELECT author_comments.*, users.name AS user_name, users.handle AS user_handle
            FROM author_comments JOIN users ON users.id = author_comments.user_id
            WHERE author_comments.author_id = ? AND author_comments.status = 'published'
            ORDER BY author_comments.created_at DESC
            LIMIT 30
            """,
            (author["id"],),
        ))
        return {
            "author": public_user(author),
            "poems": attach_user_poem_flags(conn, attach_genres(conn, poems), current_user_id, current_viewer_key),
            "followers": followers,
            "following": following_count,
            "isSubscribedByMe": is_subscribed,
            "stats": stats,
            "authorComments": author_comments,
        }

    def api_admin(self, conn: sqlite3.Connection, user: dict) -> dict:
        audit_rows = rows(conn.execute(
            """
            SELECT audit_log.*, users.name AS actor_name
            FROM audit_log LEFT JOIN users ON users.id = audit_log.actor_id
            ORDER BY audit_log.id DESC LIMIT 40
            """
        ))
        poem_rows = rows(conn.execute(
            """
            SELECT poems.*, users.name AS author_name, users.handle AS author_handle, users.pseudonym AS author_pseudonym, users.avatar_url AS author_avatar_url,
              creators.name AS created_by_name,
              (SELECT COUNT(*) FROM comments WHERE comments.poem_id = poems.id AND comments.status = 'published') AS comments_count
            FROM poems
            JOIN users ON users.id = poems.author_id
            LEFT JOIN users AS creators ON creators.id = poems.created_by
            WHERE COALESCE(poems.deleted, 0) = 0
            ORDER BY poems.id DESC
            LIMIT 80
            """
        ))
        return {
            "users": rows(conn.execute(
                """
                SELECT id, name, handle, email, role, blocked, author_certificate, created_at, pseudonym, birth_date, death_date, private_access, avatar_url, social_links,
                  (SELECT COUNT(*) FROM poems WHERE poems.author_id = users.id AND COALESCE(poems.deleted, 0) = 0) AS poems_count
                FROM users
                ORDER BY id
                """
            )),
            "poems": attach_genres(conn, poem_rows),
            "audit": audit_rows,
            "canManageRoles": user["role"] == "admin",
            "canManagePrivateAccess": user["role"] == "admin",
        }

    def api_moderation(self, conn: sqlite3.Connection) -> dict:
        items = rows(conn.execute(
            """
            SELECT moderation_queue.*, users.name AS submitted_by_name
            FROM moderation_queue JOIN users ON users.id = moderation_queue.submitted_by
            WHERE moderation_queue.status = 'open'
            ORDER BY moderation_queue.created_at ASC
            """
        ))
        reports = rows(conn.execute(
            """
            SELECT poem_reports.*, poems.title AS poem_title, poems.certificate AS poem_certificate,
              reporters.name AS reporter_name, authors.name AS author_name
            FROM poem_reports
            JOIN poems ON poems.id = poem_reports.poem_id
            JOIN users AS reporters ON reporters.id = poem_reports.user_id
            JOIN users AS authors ON authors.id = poems.author_id
            WHERE poem_reports.status = 'open' AND COALESCE(poems.deleted, 0) = 0
            ORDER BY poem_reports.created_at ASC
            """
        ))
        return {
            "items": items,
            "reports": reports,
            "forbiddenWords": sorted({normalize_text(word) for word in FORBIDDEN_WORDS} | set(EXTRA_FORBIDDEN_TERMS)),
            "moderationRules": MODERATION_RULES,
        }

    def api_private(self, conn: sqlite3.Connection, user: dict) -> dict:
        if not (user["role"] in {"admin", "moderator"} or user.get("private_access")):
            raise ApiError(HTTPStatus.FORBIDDEN, "Нет доступа к закрытому разделу")
        return {
            "notes": rows(conn.execute(
                """
                SELECT private_notes.*, users.name AS created_by_name
                FROM private_notes LEFT JOIN users ON users.id = private_notes.created_by
                ORDER BY private_notes.id DESC
                """
            )),
            "allowedUsers": rows(conn.execute(
                """
                SELECT id, name, handle, role, pseudonym, private_access, avatar_url
                FROM users
                WHERE private_access = 1 OR role IN ('admin','moderator')
                ORDER BY role DESC, name
                """
            )),
            "messages": rows(conn.execute(
                """
                SELECT private_messages.*, users.name AS user_name, users.handle AS user_handle
                FROM private_messages JOIN users ON users.id = private_messages.user_id
                ORDER BY private_messages.id DESC
                LIMIT 40
                """
            )),
        }

    def api_profile(self, conn: sqlite3.Connection, user: dict) -> dict:
        ensure_registered(user)
        if user["role"] in {"author", "admin", "moderator"}:
            profile = self.api_author(conn, {"id": [str(user["id"])], "user_id": [str(user["id"])]})
        else:
            profile = {
                "author": public_user(user),
                "poems": [],
                "followers": 0,
                "following": one(conn.execute("SELECT COUNT(*) AS count FROM subscriptions WHERE follower_id = ?", (user["id"],)))["count"],
                "isSubscribedByMe": 0,
                "stats": {
                    "poems_count": 0,
                    "likes_total": 0,
                    "comments_total": 0,
                    "views_total": 0,
                },
                "authorComments": [],
            }
        profile["favorites"] = self.api_favorites(conn, user)
        return profile

    def api_favorites(self, conn: sqlite3.Connection, user: dict) -> list[dict]:
        ensure_registered(user)
        poems = rows(conn.execute(
            f"""
            SELECT poems.*, users.name AS author_name, users.handle AS author_handle, users.pseudonym AS author_pseudonym, users.avatar_url AS author_avatar_url,
              (SELECT COUNT(*) FROM likes WHERE likes.poem_id = poems.id) AS likes_count,
              (SELECT COUNT(*) FROM comments WHERE comments.poem_id = poems.id AND comments.status = 'published') AS comments_count,
              (SELECT COUNT(*) FROM favorites AS all_favorites WHERE all_favorites.poem_id = poems.id) AS favorite_count,
              (SELECT COUNT(*) FROM shares WHERE shares.poem_id = poems.id) AS share_count,
              {poem_views_count_sql()}
            FROM favorites
            JOIN poems ON poems.id = favorites.poem_id
            JOIN users ON users.id = poems.author_id
            WHERE favorites.user_id = ? AND poems.status = 'published' AND COALESCE(poems.deleted, 0) = 0
            ORDER BY favorites.created_at DESC, poems.id DESC
            """,
            (user["id"],),
        ))
        poems = attach_genres(conn, poems)
        attach_user_poem_flags(conn, poems, user["id"], viewer_key(user["id"]))
        return poems

    def api_register(self, conn: sqlite3.Connection, payload: dict) -> dict:
        name = str(payload.get("name", "")).strip()
        email = payload.get("email", "")
        password = payload.get("password", "")
        pseudonym = str(payload.get("pseudonym", "")).strip()
        hits = scan_forbidden(f"{name}\n{pseudonym}")
        if hits:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Имя или псевдоним требуют ручной проверки")
        user = create_author_user(
            conn,
            name=name,
            email=email,
            password=password,
            pseudonym=pseudonym,
            bio="Автор зарегистрировался самостоятельно.",
            role="author",
        )
        token = create_session(conn, user["id"])
        audit(conn, user["id"], "register_author", f"user:{user['id']}", user["handle"])
        return {"user": public_user(user), "auth_token": token}

    def api_login(self, conn: sqlite3.Connection, payload: dict) -> dict:
        email = normalize_email(payload.get("email", ""))
        password = str(payload.get("password", ""))
        user = one(conn.execute("SELECT * FROM users WHERE email = ?", (email,)))
        if not user or not verify_password(password, user.get("password_salt", ""), user.get("password_hash", "")):
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Неверная почта или пароль")
        if user["blocked"]:
            raise ApiError(HTTPStatus.FORBIDDEN, "Пользователь заблокирован")
        token = create_session(conn, user["id"])
        audit(conn, user["id"], "login", f"user:{user['id']}", email)
        return {"user": public_user(user), "auth_token": token}

    def api_create_poem(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_not_blocked(user)
        ensure_role(user, {"author", "admin", "moderator"})
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        untitled = payload.get("untitled") is True or str(payload.get("untitled", "")).strip().lower() in {"1", "true", "on", "yes"}
        genres = normalize_list(payload.get("genres") or payload.get("genre"))
        if not genres:
            genres = ["философия"]
        genre = genres[0]
        style = str(payload.get("style", "белый стих")).strip()
        section = str(payload.get("section", "modern")).strip()
        comments_enabled = 1 if payload.get("comments_enabled", True) else 0
        if not body:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Нужен текст стихотворения")
        if untitled:
            title = title_from_body(body)
        elif not title:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Нужно название стихотворения или отметка «Без названия»")
        author_mode = str(payload.get("author_mode", "existing")).strip()
        if user["role"] in {"admin", "moderator"} and author_mode == "new":
            author = create_author_user(
                conn,
                name=str(payload.get("new_author_name", "")).strip(),
                pseudonym=str(payload.get("new_author_pseudonym", "")).strip(),
                death_date=payload.get("new_author_death_date", ""),
                bio="Автор добавлен редакцией при публикации произведения.",
                role="author",
            )
            author_id = author["id"]
            audit(conn, user["id"], "create_author_for_poem", f"user:{author_id}", author["handle"])
        else:
            author_id = int(payload.get("author_id") or user["id"])
        if author_id != user["id"]:
            ensure_role(user, {"admin", "moderator"})
        if section not in {"classic", "modern", "foreign"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Некорректный раздел")
        author = get_user(conn, author_id)
        if not author:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Автор не найден")
        hits = scan_forbidden(f"{title}\n{body}")
        status = "pending" if hits else "published"
        certificate = cert("POEM", title, author_id, time.time(), secrets.token_hex(4))
        cur = conn.execute(
            """
            INSERT INTO poems(title, body, untitled, genre, style, section, author_id, created_by, certificate, comments_enabled, status, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (title, body, 1 if untitled else 0, genre, style, section, author_id, user["id"], certificate, comments_enabled, status, now_iso()),
        )
        poem_id = cur.lastrowid
        for item in genres:
            conn.execute(
                "INSERT OR IGNORE INTO poem_genres(poem_id, genre) VALUES(?,?)",
                (poem_id, item),
            )
        if hits:
            conn.execute(
                "INSERT INTO moderation_queue(item_type, item_id, submitted_by, hits, snippet, created_at) VALUES(?,?,?,?,?,?)",
                ("poem", poem_id, user["id"], ", ".join(hits), safe_snippet(body), now_iso()),
            )
        audit(conn, user["id"], "create_poem", f"poem:{poem_id}", f"status={status}; author={author_id}; genres={','.join(genres)}")
        return {"id": poem_id, "status": status, "certificate": certificate, "hits": hits, "genres": genres, "author_id": author_id, "title": title, "untitled": untitled}

    def api_create_report(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        ensure_not_blocked(user)
        poem_id = int(payload.get("poem_id") or 0)
        body = str(payload.get("body", "")).strip()
        if len(body) < 10:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Опишите жалобу подробнее")
        poem = one(conn.execute(
            "SELECT id, title FROM poems WHERE id = ? AND status = 'published' AND COALESCE(deleted, 0) = 0",
            (poem_id,),
        ))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).replace(microsecond=0).isoformat()
        recent = one(conn.execute(
            "SELECT id, created_at FROM poem_reports WHERE user_id = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
            (user["id"], cutoff),
        ))
        if recent:
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "Жалобу можно отправлять раз в 60 минут")
        cur = conn.execute(
            "INSERT INTO poem_reports(poem_id, user_id, body, created_at) VALUES(?,?,?,?)",
            (poem_id, user["id"], body, now_iso()),
        )
        audit(conn, user["id"], "create_report", f"report:{cur.lastrowid}", f"poem:{poem_id}")
        return {"id": cur.lastrowid, "status": "open"}

    def api_create_author_comment(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        ensure_not_blocked(user)
        author_id = int(payload.get("author_id") or 0)
        body = str(payload.get("body", "")).strip()
        if len(body) < 3:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Комментарий пустой")
        if not get_user(conn, author_id):
            raise ApiError(HTTPStatus.NOT_FOUND, "Автор не найден")
        hits = scan_forbidden(body)
        if hits:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Комментарий об авторе содержит слова из словаря проверки")
        cur = conn.execute(
            "INSERT INTO author_comments(author_id, user_id, body, created_at) VALUES(?,?,?,?)",
            (author_id, user["id"], body, now_iso()),
        )
        audit(conn, user["id"], "create_author_comment", f"author_comment:{cur.lastrowid}", f"user:{author_id}")
        return {"id": cur.lastrowid}

    def api_update_avatar(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        avatar_url = save_avatar_image(user["id"], payload.get("image_data"))
        conn.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (avatar_url, user["id"]))
        audit(conn, user["id"], "update_avatar", f"user:{user['id']}", avatar_url)
        updated = get_user(conn, user["id"])
        return {"user": public_user(updated), "avatar_url": avatar_url}

    def api_update_social_links(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        links = clean_social_links(payload)
        encoded = json.dumps(links, ensure_ascii=False, sort_keys=True)
        conn.execute("UPDATE users SET social_links = ? WHERE id = ?", (encoded, user["id"]))
        audit(conn, user["id"], "update_social_links", f"user:{user['id']}", ",".join(links.keys()))
        updated = get_user(conn, user["id"])
        return {"user": public_user(updated), "social_links": links}

    def api_create_private_message(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        ensure_not_blocked(user)
        if not (user["role"] in {"admin", "moderator"} or user.get("private_access")):
            raise ApiError(HTTPStatus.FORBIDDEN, "Нет доступа к закрытому разделу")
        body = str(payload.get("body", "")).strip()
        if len(body) < 3:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Сообщение пустое")
        cur = conn.execute(
            "INSERT INTO private_messages(user_id, body, created_at) VALUES(?,?,?)",
            (user["id"], body, now_iso()),
        )
        audit(conn, user["id"], "create_private_message", f"private_message:{cur.lastrowid}")
        return {"id": cur.lastrowid}

    def api_create_private_note(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not title or not body:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Нужны заголовок и текст")
        cur = conn.execute(
            "INSERT INTO private_notes(title, body, created_by, created_at) VALUES(?,?,?,?)",
            (title, body, user["id"], now_iso()),
        )
        audit(conn, user["id"], "create_private_note", f"private_note:{cur.lastrowid}", title)
        return {"id": cur.lastrowid}

    def api_create_comment(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        ensure_not_blocked(user)
        poem_id = int(payload.get("poem_id") or 0)
        body = str(payload.get("body", "")).strip()
        if not body:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Комментарий пустой")
        poem = one(conn.execute("SELECT * FROM poems WHERE id = ? AND status = 'published' AND COALESCE(deleted, 0) = 0", (poem_id,)))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        if not poem["comments_enabled"]:
            raise ApiError(HTTPStatus.CONFLICT, "Автор отключил комментарии")
        hits = scan_forbidden(body)
        status = "pending" if hits else "published"
        cur = conn.execute(
            "INSERT INTO comments(poem_id, user_id, body, status, created_at) VALUES(?,?,?,?,?)",
            (poem_id, user["id"], body, status, now_iso()),
        )
        comment_id = cur.lastrowid
        if hits:
            conn.execute(
                "INSERT INTO moderation_queue(item_type, item_id, submitted_by, hits, snippet, created_at) VALUES(?,?,?,?,?,?)",
                ("comment", comment_id, user["id"], ", ".join(hits), safe_snippet(body), now_iso()),
            )
        audit(conn, user["id"], "create_comment", f"comment:{comment_id}", f"status={status}; poem={poem_id}")
        count = one(conn.execute(
            "SELECT COUNT(*) AS count FROM comments WHERE poem_id = ? AND status = 'published'",
            (poem_id,),
        ))["count"]
        return {"id": comment_id, "status": status, "hits": hits, "comments_count": count}

    def api_toggle_poem_comments(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        poem_id = int(payload.get("poem_id") or 0)
        enabled = 1 if payload.get("enabled", True) else 0
        poem = one(conn.execute("SELECT * FROM poems WHERE id = ?", (poem_id,)))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        if user["role"] not in {"admin", "moderator"} and poem["author_id"] != user["id"]:
            raise ApiError(HTTPStatus.FORBIDDEN, "Комментарии может переключать автор, модератор или администратор")
        conn.execute("UPDATE poems SET comments_enabled = ? WHERE id = ?", (enabled, poem_id))
        audit(conn, user["id"], "toggle_comments", f"poem:{poem_id}", f"enabled={enabled}")
        return {"ok": True, "comments_enabled": bool(enabled)}

    def api_like(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        poem_id = int(payload.get("poem_id") or 0)
        existing = one(conn.execute("SELECT 1 FROM likes WHERE user_id = ? AND poem_id = ?", (user["id"], poem_id)))
        if existing:
            conn.execute("DELETE FROM likes WHERE user_id = ? AND poem_id = ?", (user["id"], poem_id))
            liked = False
        else:
            conn.execute("INSERT OR IGNORE INTO likes(user_id, poem_id, created_at) VALUES(?,?,?)", (user["id"], poem_id, now_iso()))
            liked = True
        count = one(conn.execute("SELECT COUNT(*) AS count FROM likes WHERE poem_id = ?", (poem_id,)))["count"]
        return {"liked": liked, "likes_count": count}

    def api_favorite(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        poem_id = int(payload.get("poem_id") or 0)
        poem = one(conn.execute(
            "SELECT id FROM poems WHERE id = ? AND status = 'published' AND COALESCE(deleted, 0) = 0",
            (poem_id,),
        ))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        existing = one(conn.execute("SELECT 1 FROM favorites WHERE user_id = ? AND poem_id = ?", (user["id"], poem_id)))
        if existing:
            conn.execute("DELETE FROM favorites WHERE user_id = ? AND poem_id = ?", (user["id"], poem_id))
            favorited = False
        else:
            conn.execute("INSERT OR IGNORE INTO favorites(user_id, poem_id, created_at) VALUES(?,?,?)", (user["id"], poem_id, now_iso()))
            favorited = True
        count = one(conn.execute("SELECT COUNT(*) AS count FROM favorites WHERE poem_id = ?", (poem_id,)))["count"]
        return {"favorited": favorited, "favorite_count": count}

    def api_share(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        poem_id = int(payload.get("poem_id") or 0)
        poem = one(conn.execute(
            "SELECT id FROM poems WHERE id = ? AND status = 'published' AND COALESCE(deleted, 0) = 0",
            (poem_id,),
        ))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        user_id = user["id"] if user.get("id", 0) else None
        conn.execute(
            "INSERT INTO shares(user_id, poem_id, created_at) VALUES(?,?,?)",
            (user_id, poem_id, now_iso()),
        )
        count = one(conn.execute("SELECT COUNT(*) AS count FROM shares WHERE poem_id = ?", (poem_id,)))["count"]
        return {"share_count": count}

    def api_record_view(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        poem_id = int(payload.get("poem_id") or 0)
        poem = one(conn.execute(
            "SELECT id FROM poems WHERE id = ? AND status = 'published' AND COALESCE(deleted, 0) = 0",
            (poem_id,),
        ))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Произведение не найдено")
        visitor_id = clean_visitor_id(payload.get("visitor_id", ""))
        current_viewer_key = viewer_key(user["id"], visitor_id)
        now = now_iso()
        user_id = user["id"] if user.get("id", 0) else None
        if current_viewer_key:
            existing_seen = one(conn.execute(
                "SELECT 1 FROM poem_seen WHERE poem_id = ? AND viewer_key = ?",
                (poem_id, current_viewer_key),
            ))
            if existing_seen:
                conn.execute(
                    "UPDATE poem_seen SET last_seen_at = ? WHERE poem_id = ? AND viewer_key = ?",
                    (now, poem_id, current_viewer_key),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO poem_views(poem_id, user_id, visitor_id, viewer_key, source, created_at)
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        poem_id,
                        user_id,
                        visitor_id,
                        current_viewer_key,
                        str(payload.get("source", ""))[:120],
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO poem_seen(poem_id, viewer_key, user_id, visitor_id, first_seen_at, last_seen_at, views_count)
                    VALUES(?,?,?,?,?,?,1)
                    """,
                    (poem_id, current_viewer_key, user_id, visitor_id, now, now),
                )
        else:
            conn.execute(
                """
                INSERT INTO poem_views(poem_id, user_id, visitor_id, viewer_key, source, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    poem_id,
                    user_id,
                    visitor_id,
                    current_viewer_key,
                    str(payload.get("source", ""))[:120],
                    now,
                ),
            )
        count = one(conn.execute("SELECT COUNT(*) AS count FROM poem_seen WHERE poem_id = ?", (poem_id,)))["count"]
        return {"ok": True, "views_count": count, "viewed_by_me": 1 if current_viewer_key else 0}

    def api_subscribe(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        ensure_registered(user)
        author_id = int(payload.get("author_id") or 0)
        if author_id == user["id"]:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Нельзя подписаться на себя")
        existing = one(conn.execute("SELECT 1 FROM subscriptions WHERE follower_id = ? AND author_id = ?", (user["id"], author_id)))
        if existing:
            conn.execute("DELETE FROM subscriptions WHERE follower_id = ? AND author_id = ?", (user["id"], author_id))
            subscribed = False
        else:
            conn.execute("INSERT OR IGNORE INTO subscriptions(follower_id, author_id, created_at) VALUES(?,?,?)", (user["id"], author_id, now_iso()))
            subscribed = True
        return {"subscribed": subscribed}

    def api_create_news(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        event_date = str(payload.get("event_date", "")).strip() or datetime.now().date().isoformat()
        if not title or not body:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Нужны заголовок и текст новости")
        cur = conn.execute(
            "INSERT INTO news(title, body, event_date, created_by, created_at) VALUES(?,?,?,?,?)",
            (title, body, event_date, user["id"], now_iso()),
        )
        audit(conn, user["id"], "create_news", f"news:{cur.lastrowid}", title)
        return {"id": cur.lastrowid}

    def api_delete_news(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        news_id = int(payload.get("news_id") or 0)
        item = one(conn.execute("SELECT * FROM news WHERE id = ?", (news_id,)))
        if not item:
            raise ApiError(HTTPStatus.NOT_FOUND, "Новость не найдена")
        conn.execute("DELETE FROM news WHERE id = ?", (news_id,))
        audit(conn, user["id"], "delete_news", f"news:{news_id}", item["title"])
        return {"ok": True}

    def api_delete_comment(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        comment_id = int(payload.get("comment_id") or 0)
        comment = one(conn.execute(
            """
            SELECT comments.*, poems.author_id
            FROM comments JOIN poems ON poems.id = comments.poem_id
            WHERE comments.id = ?
            """,
            (comment_id,),
        ))
        if not comment:
            raise ApiError(HTTPStatus.NOT_FOUND, "Комментарий не найден")
        if user["role"] not in {"admin", "moderator"} and comment["author_id"] != user["id"]:
            raise ApiError(HTTPStatus.FORBIDDEN, "Комментарий может удалить модерация или автор стихотворения")
        conn.execute(
            "UPDATE comments SET status = 'deleted', deleted_at = ?, deleted_by = ? WHERE id = ?",
            (now_iso(), user["id"], comment_id),
        )
        audit(conn, user["id"], "delete_comment", f"comment:{comment_id}")
        return {"ok": True}

    def api_set_role(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        target_id = int(payload.get("target_id") or 0)
        role = str(payload.get("role", "")).strip()
        if role not in {"reader", "author", "moderator", "admin"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Некорректная роль")
        if target_id == user["id"] and role != "admin":
            raise ApiError(HTTPStatus.BAD_REQUEST, "Администратор не может снять роль с себя")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, target_id))
        audit(conn, user["id"], "set_role", f"user:{target_id}", role)
        return {"ok": True}

    def api_block_user(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        target_id = int(payload.get("target_id") or 0)
        blocked = 1 if payload.get("blocked", True) else 0
        target = get_user(conn, target_id)
        if not target:
            raise ApiError(HTTPStatus.NOT_FOUND, "Пользователь не найден")
        if target["role"] in {"admin", "moderator"} and user["role"] != "admin":
            raise ApiError(HTTPStatus.FORBIDDEN, "Модератор не может блокировать модераторов и администраторов")
        if target_id == user["id"]:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Нельзя заблокировать себя")
        conn.execute("UPDATE users SET blocked = ? WHERE id = ?", (blocked, target_id))
        audit(conn, user["id"], "block_user" if blocked else "unblock_user", f"user:{target_id}")
        return {"ok": True}

    def api_set_private_access(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        target_id = int(payload.get("target_id") or 0)
        enabled = 1 if payload.get("enabled", True) else 0
        target = get_user(conn, target_id)
        if not target:
            raise ApiError(HTTPStatus.NOT_FOUND, "Пользователь не найден")
        conn.execute("UPDATE users SET private_access = ? WHERE id = ?", (enabled, target_id))
        audit(conn, user["id"], "set_private_access", f"user:{target_id}", f"enabled={enabled}")
        return {"ok": True, "private_access": bool(enabled)}

    def api_delete_poem(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        poem_id = int(payload.get("poem_id") or 0)
        poem = one(conn.execute("SELECT * FROM poems WHERE id = ? AND COALESCE(deleted, 0) = 0", (poem_id,)))
        if not poem:
            raise ApiError(HTTPStatus.NOT_FOUND, "Публикация не найдена")
        if user["role"] not in {"admin", "moderator"} and int(poem["author_id"]) != int(user["id"]):
            raise ApiError(HTTPStatus.FORBIDDEN, "Можно удалить только свою публикацию")
        deleted_at = now_iso()
        conn.execute(
            "UPDATE poems SET deleted = 1, deleted_at = ?, deleted_by = ? WHERE id = ?",
            (deleted_at, user["id"], poem_id),
        )
        conn.execute(
            "UPDATE comments SET status = 'deleted', deleted_at = ?, deleted_by = ? WHERE poem_id = ? AND status = 'published'",
            (deleted_at, user["id"], poem_id),
        )
        audit(conn, user["id"], "delete_poem", f"poem:{poem_id}", poem["title"])
        return {"ok": True}

    def api_resolve_report(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        report_id = int(payload.get("report_id") or 0)
        decision = str(payload.get("decision", "")).strip()
        if decision not in {"dismissed", "deleted"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Некорректное решение по жалобе")
        report = one(conn.execute(
            """
            SELECT poem_reports.*, poems.title AS poem_title
            FROM poem_reports JOIN poems ON poems.id = poem_reports.poem_id
            WHERE poem_reports.id = ? AND poem_reports.status = 'open'
            """,
            (report_id,),
        ))
        if not report:
            raise ApiError(HTTPStatus.NOT_FOUND, "Жалоба не найдена")
        if decision == "deleted":
            self.api_delete_poem(conn, user, {"poem_id": report["poem_id"]})
        conn.execute(
            "UPDATE poem_reports SET status = ?, resolved_by = ?, resolved_at = ?, resolution = ? WHERE id = ?",
            (decision, user["id"], now_iso(), str(payload.get("resolution", "")), report_id),
        )
        audit(conn, user["id"], "resolve_report", f"report:{report_id}", decision)
        return {"ok": True, "decision": decision}

    def api_resolve_moderation(self, conn: sqlite3.Connection, user: dict, payload: dict) -> dict:
        item_id = int(payload.get("queue_id") or 0)
        decision = str(payload.get("decision", "")).strip()
        if decision not in {"approved", "rejected"}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Некорректное решение")
        item = one(conn.execute("SELECT * FROM moderation_queue WHERE id = ? AND status = 'open'", (item_id,)))
        if not item:
            raise ApiError(HTTPStatus.NOT_FOUND, "Заявка не найдена")
        new_status = "published" if decision == "approved" else "rejected"
        if item["item_type"] == "poem":
            conn.execute("UPDATE poems SET status = ? WHERE id = ?", (new_status, item["item_id"]))
        else:
            conn.execute("UPDATE comments SET status = ? WHERE id = ?", (new_status, item["item_id"]))
        conn.execute(
            "UPDATE moderation_queue SET status = ?, resolved_by = ?, resolved_at = ?, resolution = ? WHERE id = ?",
            (decision, user["id"], now_iso(), str(payload.get("resolution", "")), item_id),
        )
        audit(conn, user["id"], "resolve_moderation", f"queue:{item_id}", decision)
        return {"ok": True}


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8780"))
    server = ThreadingHTTPServer(("127.0.0.1", port), PoetryHandler)
    print(f"Poetry Sovereign running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
