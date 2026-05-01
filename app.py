import hashlib
import json
import os
import secrets
import shutil
import smtplib
import sqlite3
import tempfile
import threading
import time
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "")).expanduser() if os.environ.get("DATA_DIR") else None
LEGACY_DB_PATH = BASE_DIR / "barbearia_da_vinte_data.sqlite"
DEFAULT_DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", BASE_DIR)).expanduser() / "BarbeariaDaVinte"
    if os.name == "nt"
    else BASE_DIR / "data"
)


def resolve_db_path():
    if os.environ.get("DB_PATH"):
        candidate = Path(os.environ["DB_PATH"]).expanduser()
    elif DATA_DIR:
        candidate = DATA_DIR / "barbearia_da_vinte_data.sqlite"
    else:
        candidate = DEFAULT_DATA_DIR / "barbearia_da_vinte_data.sqlite"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "BarbeariaDaVinte" / "barbearia_da_vinte_data.sqlite"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


DB_PATH = resolve_db_path()
INDEX_PATH = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
REMINDER_LOOKAHEAD_MINUTES = 60
REMINDER_POLL_SECONDS = 60
PLAN_MAX_HAIRCUTS = 4
SESSION_COOKIE = "barbearia_vinte_session"
CLIENT_SESSION_COOKIE = "barbearia_vinte_client_session"
SESSION_DURATION = timedelta(hours=12)
CLIENT_SESSION_DURATION = timedelta(days=30)
PASSWORD_RESET_DURATION = timedelta(hours=1)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH = hashlib.sha256(os.environ.get("ADMIN_PASS", "1234").encode("utf-8")).hexdigest()


DEFAULT_SETTINGS = {
    "name": "Barbearia da Vinte",
    "tagline": "Seu estilo. Sua identidade.",
    "address": "Rua da Vinte, 20 - Centro",
    "phone": "(11) 99999-9999",
    "whatsapp": "5511999999999",
    "hours": "Ter - Dom: 09h as 20h",
    "instagram": "@barbeariadavinte",
    "about": "Uma barbearia feita para corte alinhado, barba bem desenhada e atendimento no horario certo.",
}

DEFAULT_SERVICES = [
    ("Corte", "", 30.0, 45),
    ("Barba", "", 20.0, 15),
    ("Barboterapia", "", 35.0, 30),
    ("Sobrancelha", "", 10.0, 15),
    ("Bigode/Limpeza", "", 5.0, 15),
    ("CartÃ£ozinho completo", "", 0.0, 60),
    ("Cavanhaque", "", 15.0, 15),
    ("JÃ¡ tenho mensal", "", 0.0, 60),
    ("Luzes", "", 60.0, 120),
    ("Pezinho", "", 10.0, 15),
    ("PigmentaÃ§Ã£o", "", 25.0, 30),
    ("PigmentaÃ§Ã£o colorida", "", 90.0, 120),
    ("Platinado/Nevou", "", 90.0, 30),
]

DEFAULT_BARBERS = [
    ("Yuri", "todas", "adm03h@gmail.com", ""),
    ("Alisson", "todas", "adm03h@gmail.com", ""),
    ("VenÃª", "todas", "adm03h@gmail.com", ""),
]

DEFAULT_TIMES = ["09:00", "10:00", "11:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00"]


def password_hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def prepare_db_path():
    if DB_PATH.exists() or DB_PATH == LEGACY_DB_PATH:
        return
    if not LEGACY_DB_PATH.exists():
        return
    try:
        shutil.copy2(LEGACY_DB_PATH, DB_PATH)
    except OSError:
        pass


def db_connection():
    prepare_db_path()
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def ensure_column(conn, table_name, column_name, column_sql):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def init_db():
    with db_connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                duration INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS barbers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                photo TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS barber_slots (
                id INTEGER PRIMARY KEY,
                barber_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                time TEXT NOT NULL,
                FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE,
                UNIQUE (barber_id, weekday, time)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                client_email TEXT NOT NULL DEFAULT '',
                customer_id INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                service_id INTEGER NOT NULL,
                barber_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                plan_booking INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id),
                FOREIGN KEY (service_id) REFERENCES services(id),
                FOREIGN KEY (barber_id) REFERENCES barbers(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_services (
                appointment_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                PRIMARY KEY (appointment_id, service_id),
                FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE,
                FOREIGN KEY (service_id) REFERENCES services(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                cpf TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_sessions (
                token TEXT PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token TEXT PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_plans (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                barber_id INTEGER,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS appointment_busy_slot
            ON appointments (barber_id, date, time)
            WHERE status = 'confirmed'
            """
        )
        ensure_column(conn, "appointments", "customer_id", "INTEGER")
        ensure_column(conn, "appointments", "plan_booking", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "appointments", "reminder_sent_at", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "monthly_plans", "barber_id", "INTEGER")
        ensure_column(conn, "monthly_plans", "plan_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "monthly_plans", "plan_price", "REAL NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_outbox (
                id INTEGER PRIMARY KEY,
                appointment_id INTEGER,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS barber_reviews (
                id INTEGER PRIMARY KEY,
                appointment_id INTEGER NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL,
                barber_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
            )
            """
        )

        if not conn.execute("SELECT id FROM settings WHERE id = 1").fetchone():
            conn.execute(
                "INSERT INTO settings (id, payload, updated_at) VALUES (1, ?, ?)",
                (json.dumps(DEFAULT_SETTINGS, ensure_ascii=False), utc_now().isoformat()),
            )

        if not conn.execute("SELECT id FROM services LIMIT 1").fetchone():
            conn.executemany(
                """
                INSERT INTO services (name, description, price, duration, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                DEFAULT_SERVICES,
            )

        if not conn.execute("SELECT id FROM barbers LIMIT 1").fetchone():
            conn.executemany(
                """
                INSERT INTO barbers (name, specialty, email, photo, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                DEFAULT_BARBERS,
            )
        conn.execute(
            """
            UPDATE barbers
            SET email = 'adm03h@gmail.com'
            WHERE TRIM(email) = ''
               OR email LIKE '%@barbeariadavinte.com'
            """
        )

        if not conn.execute("SELECT id FROM barber_slots LIMIT 1").fetchone():
            barber_ids = [row["id"] for row in conn.execute("SELECT id FROM barbers").fetchall()]
            rows = []
            for barber_id in barber_ids:
                for weekday in range(1, 7):
                    for time in DEFAULT_TIMES:
                        rows.append((barber_id, weekday, time))
            conn.executemany(
                "INSERT OR IGNORE INTO barber_slots (barber_id, weekday, time) VALUES (?, ?, ?)",
                rows,
            )


def read_settings(conn):
    row = conn.execute("SELECT payload FROM settings WHERE id = 1").fetchone()
    settings = DEFAULT_SETTINGS.copy()
    if row:
        settings.update(json.loads(row["payload"]))
    return settings


def read_public_data(include_admin=False):
    init_db()
    with db_connection() as conn:
        settings = read_settings(conn)
        review_summary = {
            int(row["barber_id"]): {
                "review_avg": round(float(row["avg_rating"] or 0), 1),
                "review_count": int(row["review_count"] or 0),
            }
            for row in conn.execute(
                """
                SELECT barber_id, AVG(rating) AS avg_rating, COUNT(*) AS review_count
                FROM barber_reviews
                GROUP BY barber_id
                """
            ).fetchall()
        }
        services = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, description, price, duration, active FROM services WHERE active = 1 ORDER BY id"
            ).fetchall()
        ]
        barbers = [
            {**dict(row), **review_summary.get(int(row["id"]), {"review_avg": 0, "review_count": 0})}
            for row in conn.execute(
                "SELECT id, name, specialty, email, photo, active FROM barbers WHERE active = 1 ORDER BY id"
            ).fetchall()
        ]
        payload = {"settings": settings, "services": services, "barbers": barbers, "reviews": read_barber_reviews(conn)[:8]}
        if include_admin:
            payload["services"] = [
                dict(row)
                for row in conn.execute("SELECT id, name, description, price, duration, active FROM services WHERE active = 1 ORDER BY id").fetchall()
            ]
            payload["barbers"] = [
                dict(row)
                for row in conn.execute("SELECT id, name, specialty, email, photo, active FROM barbers WHERE active = 1 ORDER BY id").fetchall()
            ]
            payload["slots"] = [
                dict(row)
                for row in conn.execute("SELECT barber_id, weekday, time FROM barber_slots ORDER BY barber_id, weekday, time").fetchall()
            ]
            payload["appointments"] = read_appointments(conn)
            payload["customers"] = read_customers(conn)
            payload["plans"] = read_plans(conn)
            payload["barberSummaries"] = read_barber_summaries(conn)
            payload["reviews"] = read_barber_reviews(conn)
            payload["emailOutbox"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT id, appointment_id, recipient, subject, status, error, created_at FROM email_outbox ORDER BY id DESC LIMIT 50"
                ).fetchall()
            ]
        return payload


def read_appointments(conn):
    rows = conn.execute(
        """
        SELECT a.id, a.client_name, a.client_phone, a.client_email, a.notes, a.date, a.time,
               a.status, a.created_at, a.customer_id, a.plan_booking, a.barber_id,
               COALESCE((
                   SELECT GROUP_CONCAT(s2.name, ' + ')
                   FROM appointment_services aps
                   JOIN services s2 ON s2.id = aps.service_id
                   WHERE aps.appointment_id = a.id
               ), s.name) AS service_name,
               COALESCE((
                   SELECT SUM(s2.price)
                   FROM appointment_services aps
                   JOIN services s2 ON s2.id = aps.service_id
                   WHERE aps.appointment_id = a.id
               ), s.price) AS service_price,
               COALESCE((
                   SELECT SUM(s2.duration)
                   FROM appointment_services aps
                   JOIN services s2 ON s2.id = aps.service_id
                   WHERE aps.appointment_id = a.id
               ), s.duration) AS service_duration,
               b.name AS barber_name, b.email AS barber_email
        FROM appointments a
        JOIN services s ON s.id = a.service_id
        JOIN barbers b ON b.id = a.barber_id
        ORDER BY a.date DESC, a.time DESC, a.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def read_barber_reviews(conn):
    rows = conn.execute(
        """
        SELECT r.id, r.appointment_id, r.customer_id, r.barber_id, r.rating, r.comment, r.created_at,
               c.name AS customer_name,
               b.name AS barber_name,
               a.date, a.time, a.service_id
        FROM barber_reviews r
        JOIN customers c ON c.id = r.customer_id
        JOIN barbers b ON b.id = r.barber_id
        JOIN appointments a ON a.id = r.appointment_id
        ORDER BY a.date DESC, a.time DESC, r.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def read_reviewable_appointments(conn, customer_id):
    rows = conn.execute(
        """
        SELECT a.id, a.date, a.time, a.barber_id, b.name AS barber_name,
               COALESCE((
                   SELECT GROUP_CONCAT(s2.name, ' + ')
                   FROM appointment_services aps
                   JOIN services s2 ON s2.id = aps.service_id
                   WHERE aps.appointment_id = a.id
               ), s.name) AS service_name
        FROM appointments a
        JOIN services s ON s.id = a.service_id
        JOIN barbers b ON b.id = a.barber_id
        LEFT JOIN barber_reviews r ON r.appointment_id = a.id
        WHERE a.customer_id = ?
          AND a.status = 'done'
          AND r.id IS NULL
        ORDER BY a.date DESC, a.time DESC, a.id DESC
        """
        ,
        (customer_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def read_customer_appointments(conn, customer_id):
    rows = conn.execute(
        """
        SELECT a.id, a.date, a.time, a.status, a.notes, a.plan_booking,
               b.name AS barber_name,
               COALESCE((
                   SELECT GROUP_CONCAT(s2.name, ' + ')
                   FROM appointment_services aps
                   JOIN services s2 ON s2.id = aps.service_id
                   WHERE aps.appointment_id = a.id
               ), s.name) AS service_name
        FROM appointments a
        JOIN services s ON s.id = a.service_id
        JOIN barbers b ON b.id = a.barber_id
        WHERE a.customer_id = ?
        ORDER BY a.date DESC, a.time DESC, a.id DESC
        """,
        (customer_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def read_plans(conn):
    deactivate_expired_plans(conn)
    rows = conn.execute(
        """
        SELECT p.id, p.customer_id, p.barber_id, p.plan_name, p.plan_price, p.start_date, p.end_date, p.note, p.active, p.created_at,
               c.name AS customer_name, c.phone AS customer_phone, c.email AS customer_email,
               COALESCE(b.name, '') AS barber_name
        FROM monthly_plans p
        JOIN customers c ON c.id = p.customer_id
        LEFT JOIN barbers b ON b.id = p.barber_id
        ORDER BY p.active DESC, p.end_date DESC, p.id DESC
        """
    ).fetchall()
    return [enrich_plan_usage(conn, row) for row in rows]


def read_customers(conn):
    deactivate_expired_plans(conn)
    customers = []
    rows = conn.execute(
        "SELECT id, name, email, phone, cpf, created_at FROM customers ORDER BY name COLLATE NOCASE"
    ).fetchall()
    today = datetime.now().date()
    for row in rows:
        customer = dict(row)
        last_haircut = conn.execute(
            """
            SELECT date
            FROM appointments a
            JOIN services s ON s.id = a.service_id
            WHERE a.customer_id = ? AND a.status IN ('confirmed','done') AND lower(s.name) LIKE '%corte%'
            ORDER BY date DESC, time DESC
            LIMIT 1
            """,
            (customer["id"],),
        ).fetchone()
        next_active_plan = conn.execute(
            """
            SELECT p.id, p.barber_id, p.start_date, p.end_date, p.note, p.active,
                   COALESCE(b.name, '') AS barber_name
            FROM monthly_plans p
            LEFT JOIN barbers b ON b.id = p.barber_id
            WHERE p.customer_id = ? AND p.active = 1
            ORDER BY p.end_date DESC, p.id DESC
            LIMIT 1
            """,
            (customer["id"],),
        ).fetchone()
        customer["last_haircut_date"] = last_haircut["date"] if last_haircut else ""
        if customer["last_haircut_date"]:
            customer["days_since_last_haircut"] = (today - datetime.strptime(customer["last_haircut_date"], "%Y-%m-%d").date()).days
        else:
            customer["days_since_last_haircut"] = None
        customer["active_plan"] = enrich_plan_usage(conn, {
            "id": next_active_plan["id"],
            "customer_id": customer["id"],
            "barber_id": next_active_plan["barber_id"],
            "barber_name": next_active_plan["barber_name"],
            "start_date": next_active_plan["start_date"],
            "end_date": next_active_plan["end_date"],
            "note": next_active_plan["note"],
            "active": next_active_plan["active"],
        }) if next_active_plan else None
        customers.append(customer)
    return customers


def sanitize_customer(row):
    if not row:
        return None
    customer = dict(row)
    customer.pop("password_hash", None)
    return customer


def digits_only(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def parse_appointment_datetime(date_text, time_text):
    return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")


def add_one_month(date_text):
    base = datetime.strptime(date_text, "%Y-%m-%d").date()
    year = base.year + (1 if base.month == 12 else 0)
    month = 1 if base.month == 12 else base.month + 1
    day = min(base.day, monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day).isoformat()


def deactivate_expired_plans(conn):
    today = datetime.now().date().isoformat()
    conn.execute(
        "UPDATE monthly_plans SET active = 0 WHERE active = 1 AND end_date < ?",
        (today,),
    )


def count_plan_haircuts_used(conn, customer_id, start_date, end_date):
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM appointments
        WHERE customer_id = ?
          AND plan_booking = 1
          AND status IN ('confirmed', 'done')
          AND date >= ?
          AND date <= ?
        """,
        (customer_id, start_date, end_date),
    ).fetchone()
    return int(row[0] if row else 0)


def enrich_plan_usage(conn, plan_row):
    if not plan_row:
        return None
    plan = dict(plan_row)
    used = count_plan_haircuts_used(conn, plan["customer_id"], plan["start_date"], plan["end_date"])
    plan["haircuts_used"] = used
    plan["haircuts_remaining"] = max(0, PLAN_MAX_HAIRCUTS - used)
    plan["haircuts_limit"] = PLAN_MAX_HAIRCUTS
    return plan


def read_barber_summaries(conn):
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    appointments = read_appointments(conn)
    plans = read_plans(conn)
    summaries = []
    for barber in conn.execute("SELECT id, name FROM barbers WHERE active = 1 ORDER BY id").fetchall():
        barber_id = int(barber["id"])
        barber_appointments = [
            item for item in appointments
            if item.get("status") in {"confirmed", "done"} and int(item.get("barber_id") or 0) == barber_id
        ]
        barber_plans = [item for item in plans if int(item.get("barber_id") or 0) == barber_id]

        def summarize_since(start_date):
            scoped_appointments = [
                item for item in barber_appointments
                if item.get("date") and datetime.strptime(item["date"], "%Y-%m-%d").date() >= start_date
            ]
            client_keys = {
                int(item["customer_id"]) if item.get("customer_id") else f"{item.get('client_phone','')}|{item.get('client_name','')}"
                for item in scoped_appointments
            }
            revenue = sum(float(item.get("service_price") or 0) for item in scoped_appointments)
            scoped_plans = [
                item for item in barber_plans
                if item.get("start_date") and datetime.strptime(item["start_date"], "%Y-%m-%d").date() >= start_date
            ]
            return {
                "clients": len(client_keys),
                "revenue": round(revenue, 2),
                "plans": len(scoped_plans),
            }

        summaries.append({
            "barber_id": barber_id,
            "barber_name": barber["name"],
            "weekly": summarize_since(week_start),
            "monthly": summarize_since(month_start),
        })
    return summaries


def time_to_minutes(time_text):
    hour, minute = [int(part) for part in time_text.split(":")]
    return hour * 60 + minute


def ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def get_selected_service_rows(conn, service_ids):
    clean_ids = [int(service_id) for service_id in service_ids if int(service_id or 0) > 0]
    if not clean_ids:
        return []
    placeholders = ",".join("?" for _ in clean_ids)
    rows = conn.execute(
        f"SELECT id, name, price, duration FROM services WHERE id IN ({placeholders}) AND active = 1",
        clean_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    return [by_id[service_id] for service_id in clean_ids if service_id in by_id]


def get_availability(barber_id, date_text, service_ids=None):
    try:
        appointment_date = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return []
    weekday = appointment_date.weekday()
    today = datetime.now().date()
    now_time = datetime.now().strftime("%H:%M")
    with db_connection() as conn:
        selected_services = get_selected_service_rows(conn, service_ids or [])
        required_duration = sum(int(row["duration"]) for row in selected_services) or 30
        slots = [
            row["time"]
            for row in conn.execute(
                """
                SELECT time FROM barber_slots
                WHERE barber_id = ? AND weekday = ?
                ORDER BY time
                """,
                (barber_id, weekday),
            ).fetchall()
        ]
        busy_rows = conn.execute(
            """
            SELECT a.time,
                   COALESCE((
                       SELECT SUM(s2.duration)
                       FROM appointment_services aps
                       JOIN services s2 ON s2.id = aps.service_id
                       WHERE aps.appointment_id = a.id
                   ), s.duration) AS busy_duration
            FROM appointments a
            JOIN services s ON s.id = a.service_id
            WHERE barber_id = ? AND date = ? AND status = 'confirmed'
            """,
            (barber_id, date_text),
        ).fetchall()
        busy_ranges = [
            (time_to_minutes(row["time"]), time_to_minutes(row["time"]) + int(row["busy_duration"]))
            for row in busy_rows
        ]
        closing_minutes = time_to_minutes(slots[-1]) + 60 if slots else 0
    return [
        {
            "time": time,
            "available": (
                (appointment_date > today or time > now_time)
                and (time_to_minutes(time) + required_duration) <= closing_minutes
                and not any(
                    ranges_overlap(time_to_minutes(time), time_to_minutes(time) + required_duration, start, end)
                    for start, end in busy_ranges
                )
            ),
        }
        for time in slots
    ]


def create_customer(payload):
    name = str(payload.get("name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    phone = str(payload.get("phone", "")).strip()
    password = str(payload.get("password", "")).strip()
    cpf = "".join(ch for ch in str(payload.get("cpf", "")).strip() if ch.isdigit())
    if not all([name, email, phone, password, cpf]):
        raise ValueError("Preencha nome, email, telefone, senha e CPF.")
    if len(cpf) != 11:
        raise ValueError("CPF invalido.")
    with db_connection() as conn:
        exists = conn.execute("SELECT id FROM customers WHERE email = ? OR cpf = ?", (email, cpf)).fetchone()
        if exists:
            raise ValueError("Ja existe cliente cadastrado com esse email ou CPF.")
        cursor = conn.execute(
            """
            INSERT INTO customers (name, email, phone, password_hash, cpf, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, email, phone, password_hash(password), cpf, datetime.now().strftime("%d/%m/%Y, %H:%M")),
        )
        return {
            "id": cursor.lastrowid,
            "name": name,
            "email": email,
            "phone": phone,
            "cpf": cpf,
            "created_at": datetime.now().strftime("%d/%m/%Y, %H:%M"),
        }


def get_customer_by_id(customer_id):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, name, email, phone, cpf, password_hash, created_at FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        return sanitize_customer(row)


def authenticate_customer(identifier, password):
    identifier_text = str(identifier).strip()
    identifier_digits = digits_only(identifier_text)
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, email, phone, cpf, password_hash, created_at
            FROM customers
            WHERE email = ? OR cpf = ?
            LIMIT 1
            """,
            (identifier_text.lower(), identifier_digits),
        ).fetchone()
        if not row and identifier_digits:
            rows = conn.execute(
                "SELECT id, name, email, phone, cpf, password_hash, created_at FROM customers"
            ).fetchall()
            for item in rows:
                if digits_only(item["phone"]) == identifier_digits:
                    row = item
                    break
        if not row or row["password_hash"] != password_hash(password):
            return None
        return sanitize_customer(row)


def create_customer_session(customer_id):
    token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + CLIENT_SESSION_DURATION
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO customer_sessions (token, customer_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, customer_id, now.isoformat(), expires_at.isoformat()),
        )
    return token, expires_at


def cleanup_customer_sessions(conn):
    conn.execute("DELETE FROM customer_sessions WHERE expires_at <= ?", (utc_now().isoformat(),))


def get_customer_by_session(token):
    if not token:
        return None
    with db_connection() as conn:
        cleanup_customer_sessions(conn)
        row = conn.execute(
            """
            SELECT c.id, c.name, c.email, c.phone, c.cpf, c.password_hash, c.created_at
            FROM customer_sessions s
            JOIN customers c ON c.id = s.customer_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        return sanitize_customer(row)


def enrich_customer_with_plan(customer):
    if not customer:
        return None
    init_db()
    with db_connection() as conn:
        deactivate_expired_plans(conn)
        plan = conn.execute(
            """
            SELECT id, start_date, end_date, note, active
            FROM monthly_plans
            WHERE customer_id = ? AND active = 1
            ORDER BY end_date DESC, id DESC
            LIMIT 1
            """,
            (customer["id"],),
        ).fetchone()
    customer = dict(customer)
    with db_connection() as conn:
        customer["active_plan"] = enrich_plan_usage(conn, {
            "id": plan["id"],
            "customer_id": customer["id"],
            "barber_id": plan["barber_id"],
            "barber_name": plan["barber_name"],
            "start_date": plan["start_date"],
            "end_date": plan["end_date"],
            "note": plan["note"],
            "active": plan["active"],
        }) if plan else None
        customer["reviewable_appointments"] = read_reviewable_appointments(conn, customer["id"])
        customer["appointments"] = read_customer_appointments(conn, customer["id"])
    return customer


def create_barber_review(payload, customer):
    if not customer:
        raise ValueError("Entre na sua conta para avaliar o barbeiro.")
    appointment_id = int(payload.get("appointmentId") or 0)
    rating = int(payload.get("rating") or 0)
    comment = str(payload.get("comment", "")).strip()
    if not appointment_id or rating < 1 or rating > 5:
        raise ValueError("Escolha uma nota de 1 a 5 para enviar a avaliacao.")
    with db_connection() as conn:
        appointment = conn.execute(
            """
            SELECT a.id, a.customer_id, a.barber_id, a.status, a.date, a.time, b.name AS barber_name
            FROM appointments a
            JOIN barbers b ON b.id = a.barber_id
            WHERE a.id = ?
            """,
            (appointment_id,),
        ).fetchone()
        if not appointment:
            raise ValueError("Agendamento nao encontrado.")
        if int(appointment["customer_id"] or 0) != int(customer["id"]):
            raise ValueError("Esse agendamento nao pertence a sua conta.")
        if appointment["status"] != "done":
            raise ValueError("A avaliacao so pode ser feita depois do atendimento concluido.")
        exists = conn.execute("SELECT id FROM barber_reviews WHERE appointment_id = ?", (appointment_id,)).fetchone()
        if exists:
            raise ValueError("Esse atendimento ja recebeu uma avaliacao.")
        conn.execute(
            """
            INSERT INTO barber_reviews (appointment_id, customer_id, barber_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                appointment_id,
                int(customer["id"]),
                int(appointment["barber_id"]),
                rating,
                comment,
                datetime.now().strftime("%d/%m/%Y, %H:%M"),
            ),
        )
    return {"ok": True}


def delete_customer_session(token):
    if token:
        with db_connection() as conn:
            conn.execute("DELETE FROM customer_sessions WHERE token = ?", (token,))


def cleanup_password_reset_tokens(conn):
    conn.execute(
        "DELETE FROM password_reset_tokens WHERE expires_at <= ? OR TRIM(COALESCE(used_at, '')) <> ''",
        (utc_now().isoformat(),),
    )


def create_password_reset_token(identifier, base_url):
    identifier_text = str(identifier or "").strip().lower()
    if not identifier_text:
        raise ValueError("Informe o e-mail cadastrado para recuperar a senha.")
    with db_connection() as conn:
        cleanup_password_reset_tokens(conn)
        customer = conn.execute(
            "SELECT id, name, email FROM customers WHERE lower(email) = ? LIMIT 1",
            (identifier_text,),
        ).fetchone()
        if not customer:
            raise ValueError("Nao encontrei cliente com esse e-mail.")
        token = secrets.token_urlsafe(32)
        now = utc_now()
        expires_at = now + PASSWORD_RESET_DURATION
        conn.execute(
            """
            INSERT INTO password_reset_tokens (token, customer_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, int(customer["id"]), now.isoformat(), expires_at.isoformat()),
        )
        link = f"{base_url}/?resetToken={token}"
        subject = "Recuperacao de senha - Barbearia da Vinte"
        body = (
            f"Ola, {customer['name']}!\n\n"
            f"Recebemos um pedido para redefinir sua senha.\n\n"
            f"Use este link para criar uma nova senha:\n{link}\n\n"
            f"Esse link expira em 1 hora.\n"
            f"Se voce nao pediu essa troca, pode ignorar este e-mail."
        )
        send_and_log_email(conn, None, customer["email"], subject, body)


def reset_customer_password(token, new_password):
    token_text = str(token or "").strip()
    password_text = str(new_password or "").strip()
    if not token_text or not password_text:
        raise ValueError("Informe o link de recuperacao e a nova senha.")
    if len(password_text) < 4:
        raise ValueError("A nova senha precisa ter pelo menos 4 caracteres.")
    with db_connection() as conn:
        cleanup_password_reset_tokens(conn)
        row = conn.execute(
            """
            SELECT token, customer_id, expires_at, used_at
            FROM password_reset_tokens
            WHERE token = ?
            LIMIT 1
            """,
            (token_text,),
        ).fetchone()
        if not row:
            raise ValueError("Esse link de recuperacao e invalido ou expirou.")
        conn.execute(
            "UPDATE customers SET password_hash = ? WHERE id = ?",
            (password_hash(password_text), int(row["customer_id"])),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token = ?",
            (utc_now().isoformat(), token_text),
        )
        conn.execute("DELETE FROM customer_sessions WHERE customer_id = ?", (int(row["customer_id"]),))


def get_active_plan_for_customer(conn, customer_id, target_date):
    deactivate_expired_plans(conn)
    row = conn.execute(
        """
        SELECT p.id, p.customer_id, p.barber_id, p.start_date, p.end_date, p.note, p.active, p.created_at,
               COALESCE(b.name, '') AS barber_name
        FROM monthly_plans p
        LEFT JOIN barbers b ON b.id = p.barber_id
        WHERE p.customer_id = ? AND p.active = 1 AND p.start_date <= ? AND p.end_date >= ?
        ORDER BY p.end_date DESC, p.id DESC
        LIMIT 1
        """,
        (customer_id, target_date, target_date),
    ).fetchone()
    return enrich_plan_usage(conn, row)


def save_monthly_plan(payload):
    customer_id = int(payload.get("customerId") or 0)
    barber_id = int(payload.get("barberId") or 0)
    plan_name = str(payload.get("planName", "")).strip()
    plan_price = normalize_money(payload.get("planPrice", 0))
    start_date = str(payload.get("startDate", "")).strip()
    end_date = str(payload.get("endDate", "")).strip()
    if start_date and not end_date:
        end_date = add_one_month(start_date)
    note = str(payload.get("note", "")).strip()
    if not customer_id or not barber_id or not plan_name or not start_date or not end_date:
        raise ValueError("Escolha o cliente, o barbeiro, o tipo do plano e as datas do plano.")
    with db_connection() as conn:
        deactivate_expired_plans(conn)
        barber_exists = conn.execute("SELECT id FROM barbers WHERE id = ? AND active = 1", (barber_id,)).fetchone()
        if not barber_exists:
            raise ValueError("Barbeiro invalido para esse plano.")
        conn.execute("UPDATE monthly_plans SET active = 0 WHERE customer_id = ? AND active = 1", (customer_id,))
        conn.execute(
            """
            INSERT INTO monthly_plans (customer_id, barber_id, plan_name, plan_price, start_date, end_date, note, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (customer_id, barber_id, plan_name, plan_price, start_date, end_date, note, datetime.now().strftime("%d/%m/%Y, %H:%M")),
        )


def normalize_money(value):
    try:
        return max(0, float(str(value).replace(",", ".")))
    except ValueError:
        return 0.0


def is_monthly_plan_service_name(name):
    normalized = str(name or "").strip().lower()
    return "ja tenho mensal" in normalized or "já tenho mensal" in normalized


def save_admin_data(payload):
    settings = DEFAULT_SETTINGS.copy()
    settings.update({key: str(payload.get("settings", {}).get(key, settings[key])).strip() for key in settings})
    services = payload.get("services", [])
    barbers = payload.get("barbers", [])
    slots = payload.get("slots", [])

    with db_connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "UPDATE settings SET payload = ?, updated_at = ? WHERE id = 1",
            (json.dumps(settings, ensure_ascii=False), utc_now().isoformat()),
        )

        incoming_service_ids = []
        for item in services:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            service_id = int(item.get("id") or 0)
            values = (
                name,
                str(item.get("description", "")).strip(),
                normalize_money(item.get("price", 0)),
                max(5, int(item.get("duration") or 30)),
                1 if item.get("active", True) else 0,
            )
            if service_id:
                cursor = conn.execute(
                    "UPDATE services SET name = ?, description = ?, price = ?, duration = ?, active = ? WHERE id = ?",
                    (*values, service_id),
                )
                if cursor.rowcount == 0:
                    conn.execute(
                        "INSERT INTO services (id, name, description, price, duration, active) VALUES (?, ?, ?, ?, ?, ?)",
                        (service_id, *values),
                    )
            else:
                cursor = conn.execute(
                    "INSERT INTO services (name, description, price, duration, active) VALUES (?, ?, ?, ?, ?)",
                    values,
                )
                service_id = cursor.lastrowid
            incoming_service_ids.append(service_id)

        if incoming_service_ids:
            placeholders = ",".join("?" for _ in incoming_service_ids)
            conn.execute(f"UPDATE services SET active = 0 WHERE id NOT IN ({placeholders})", incoming_service_ids)

        incoming_barber_ids = []
        for item in barbers:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            barber_id = int(item.get("id") or 0)
            values = (
                name,
                str(item.get("specialty", "")).strip(),
                str(item.get("email", "")).strip(),
                str(item.get("photo", "")).strip(),
                1 if item.get("active", True) else 0,
            )
            if barber_id:
                cursor = conn.execute(
                    "UPDATE barbers SET name = ?, specialty = ?, email = ?, photo = ?, active = ? WHERE id = ?",
                    (*values, barber_id),
                )
                if cursor.rowcount == 0:
                    conn.execute(
                        "INSERT INTO barbers (id, name, specialty, email, photo, active) VALUES (?, ?, ?, ?, ?, ?)",
                        (barber_id, *values),
                    )
            else:
                cursor = conn.execute(
                    "INSERT INTO barbers (name, specialty, email, photo, active) VALUES (?, ?, ?, ?, ?)",
                    values,
                )
                barber_id = cursor.lastrowid
            incoming_barber_ids.append(barber_id)

        if incoming_barber_ids:
            placeholders = ",".join("?" for _ in incoming_barber_ids)
            conn.execute(f"UPDATE barbers SET active = 0 WHERE id NOT IN ({placeholders})", incoming_barber_ids)

        conn.execute("DELETE FROM barber_slots")
        for item in slots:
            try:
                barber_id = int(item.get("barber_id"))
                weekday = int(item.get("weekday"))
                time = str(item.get("time", "")).strip()
            except (TypeError, ValueError):
                continue
            if barber_id in incoming_barber_ids and 0 <= weekday <= 6 and len(time) == 5:
                conn.execute(
                    "INSERT OR IGNORE INTO barber_slots (barber_id, weekday, time) VALUES (?, ?, ?)",
                    (barber_id, weekday, time),
                )


def create_appointment(payload, customer=None):
    name = customer["name"] if customer else str(payload.get("clientName", "")).strip()
    phone = customer["phone"] if customer else str(payload.get("clientPhone", "")).strip()
    email = customer["email"] if customer else str(payload.get("clientEmail", "")).strip()
    notes = str(payload.get("notes", "")).strip()
    date_text = str(payload.get("date", "")).strip()
    time = str(payload.get("time", "")).strip()
    service_ids = payload.get("serviceIds")
    if not isinstance(service_ids, list):
        service_ids = [payload.get("serviceId")]
    service_ids = [int(service_id or 0) for service_id in service_ids if int(service_id or 0) > 0]
    service_id = service_ids[0] if service_ids else 0
    barber_id = int(payload.get("barberId") or 0)
    plan_booking = bool(payload.get("planBooking"))

    if not all([name, phone, date_text, time, service_id, barber_id]):
        raise ValueError("Preencha nome, WhatsApp, servico, barbeiro, data e horario.")

    available = get_availability(barber_id, date_text, service_ids)
    if not any(slot["time"] == time and slot["available"] for slot in available):
        raise ValueError("Esse horario acabou de ficar indisponivel. Escolha outro horario.")

    created_at = datetime.now().strftime("%d/%m/%Y, %H:%M")
    with db_connection() as conn:
        customer_id = int(customer["id"]) if customer else None
        services = get_selected_service_rows(conn, service_ids)
        if not services:
            raise ValueError("Escolha pelo menos um servico valido.")
        service = services[0]
        total_price = sum(float(row["price"]) for row in services)
        total_duration = sum(int(row["duration"]) for row in services)
        service_names = " + ".join(row["name"] for row in services)
        active_plan = get_active_plan_for_customer(conn, customer_id, date_text) if customer_id else None
        monthly_plan_services = [row for row in services if is_monthly_plan_service_name(row["name"])]
        if active_plan:
            if len(services) != 1 or len(monthly_plan_services) != 1:
                raise ValueError("Quem tem plano ativo precisa marcar usando somente o servico Ja tenho mensal.")
            if int(active_plan.get("barber_id") or 0) != barber_id:
                raise ValueError(f"Esse plano mensal so pode ser usado com o barbeiro {active_plan.get('barber_name') or 'do plano'}.")
            plan_booking = True
        if plan_booking:
            if not customer_id:
                raise ValueError("Entre na sua conta para usar corte do plano.")
            if len(services) != 1 or len(monthly_plan_services) != 1:
                raise ValueError("O plano mensal so pode ser usado somente com o servico Ja tenho mensal.")
            active_plan = active_plan or get_active_plan_for_customer(conn, customer_id, date_text)
            if not active_plan:
                raise ValueError("Seu plano mensal nao esta ativo para essa data.")
            if int(active_plan.get("barber_id") or 0) != barber_id:
                raise ValueError(f"Esse plano mensal so pode ser usado com o barbeiro {active_plan.get('barber_name') or 'do plano'}.")
            if int(active_plan["haircuts_used"]) >= PLAN_MAX_HAIRCUTS:
                raise ValueError("Esse plano ja usou os 4 cortes disponiveis.")
        try:
            cursor = conn.execute(
                """
                INSERT INTO appointments
                (client_name, client_phone, client_email, customer_id, notes, service_id, barber_id, date, time, plan_booking, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
                """,
                (name, phone, email, customer_id, notes, service_id, barber_id, date_text, time, 1 if plan_booking else 0, created_at),
            )
            appointment_id = cursor.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO appointment_services (appointment_id, service_id) VALUES (?, ?)",
                [(appointment_id, row["id"]) for row in services],
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Esse horario ja foi ocupado por outro cliente.") from exc

        appointment = conn.execute(
            """
            SELECT a.id, a.client_name, a.client_phone, a.client_email, a.notes, a.date, a.time, a.created_at, a.customer_id, a.plan_booking,
                   b.name AS barber_name, b.email AS barber_email
            FROM appointments a
            JOIN barbers b ON b.id = a.barber_id
            WHERE a.id = ?
            """,
            (appointment_id,),
        ).fetchone()
        appointment_dict = dict(appointment)
        appointment_dict["service_name"] = service_names
        appointment_dict["service_price"] = total_price
        appointment_dict["service_duration"] = total_duration
        notify_barber(conn, appointment_dict)
        notify_customer_confirmation(conn, appointment_dict)
        return appointment_dict


def send_and_log_email(conn, appointment_id, recipient, subject, body, reply_to=""):
    recipient = str(recipient or "").strip()
    if not recipient:
        return
    status = "queued"
    error = ""
    try:
        send_email(recipient, subject, body, reply_to=reply_to)
        status = "sent"
    except Exception as exc:
        error = str(exc)[:500]
    conn.execute(
        """
        INSERT INTO email_outbox (appointment_id, recipient, subject, body, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (appointment_id, recipient, subject, body, status, error, datetime.now().strftime("%d/%m/%Y, %H:%M")),
    )


def notify_barber(conn, appointment):
    recipient = appointment.get("barber_email", "").strip()
    if not recipient:
        return
    reply_to = appointment.get("client_email", "").strip()
    subject = f"{appointment['client_name']} marcou horario em {appointment['date']} as {appointment['time']}"
    body = (
        f"Novo agendamento para {appointment['barber_name']}\n\n"
        f"Resumo rapido:\n"
        f"Cliente: {appointment['client_name']}\n"
        f"Data: {appointment['date']}\n"
        f"Horario: {appointment['time']}\n\n"
        f"Detalhes completos:\n"
        f"WhatsApp: {appointment['client_phone']}\n"
        f"E-mail: {appointment['client_email'] or 'nao informado'}\n"
        f"Servico: {appointment['service_name']}\n"
        f"Valor: R$ {appointment['service_price']:.2f}\n"
        f"Barbeiro: {appointment['barber_name']}\n"
        f"Observacoes: {appointment['notes'] or 'nenhuma'}\n"
    )
    send_and_log_email(conn, appointment["id"], recipient, subject, body, reply_to=reply_to)


def notify_customer_confirmation(conn, appointment):
    recipient = appointment.get("client_email", "").strip()
    if not recipient:
        return
    subject = f"Seu horario na Barbearia da Vinte foi confirmado: {appointment['date']} as {appointment['time']}"
    body = (
        f"Ola, {appointment['client_name']}!\n\n"
        f"Seu agendamento foi confirmado com sucesso.\n\n"
        f"Barbeiro: {appointment['barber_name']}\n"
        f"Servico: {appointment['service_name']}\n"
        f"Data: {appointment['date']}\n"
        f"Horario: {appointment['time']}\n"
        f"Valor: R$ {appointment['service_price']:.2f}\n"
        f"Observacoes: {appointment['notes'] or 'nenhuma'}\n\n"
        f"Se precisar remarcar, fale com a Barbearia da Vinte."
    )
    send_and_log_email(conn, appointment["id"], recipient, subject, body)


def send_email(recipient, subject, body, reply_to=""):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", user or "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not host or not sender:
        raise RuntimeError("SMTP nao configurado. Defina SMTP_HOST, SMTP_USER, SMTP_PASS e SMTP_FROM.")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=12) as smtp:
        smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)


def send_due_reminders():
    now = datetime.now()
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.client_name, a.client_phone, a.client_email, a.notes, a.date, a.time,
                   COALESCE((
                       SELECT GROUP_CONCAT(s2.name, ' + ')
                       FROM appointment_services aps
                       JOIN services s2 ON s2.id = aps.service_id
                       WHERE aps.appointment_id = a.id
                   ), s.name) AS service_name,
                   COALESCE((
                       SELECT SUM(s2.price)
                       FROM appointment_services aps
                       JOIN services s2 ON s2.id = aps.service_id
                       WHERE aps.appointment_id = a.id
                   ), s.price) AS service_price,
                   b.name AS barber_name
            FROM appointments a
            JOIN services s ON s.id = a.service_id
            JOIN barbers b ON b.id = a.barber_id
            WHERE a.status = 'confirmed'
              AND TRIM(COALESCE(a.client_email, '')) <> ''
              AND TRIM(COALESCE(a.reminder_sent_at, '')) = ''
            ORDER BY a.date, a.time, a.id
            """
        ).fetchall()
        for row in rows:
            appointment = dict(row)
            try:
                starts_at = parse_appointment_datetime(appointment["date"], appointment["time"])
            except ValueError:
                continue
            delta = starts_at - now
            seconds_until = delta.total_seconds()
            if seconds_until <= 0 or seconds_until > (REMINDER_LOOKAHEAD_MINUTES * 60):
                continue
            subject = f"Lembrete: seu horario na Barbearia da Vinte e hoje as {appointment['time']}"
            body = (
                f"Ola, {appointment['client_name']}!\n\n"
                f"Passando para lembrar que voce tem horario marcado daqui a 1 hora.\n\n"
                f"Barbeiro: {appointment['barber_name']}\n"
                f"Servico: {appointment['service_name']}\n"
                f"Data: {appointment['date']}\n"
                f"Horario: {appointment['time']}\n"
                f"Valor: R$ {appointment['service_price']:.2f}\n\n"
                f"Te esperamos na Barbearia da Vinte."
            )
            send_and_log_email(conn, appointment["id"], appointment["client_email"], subject, body)
            conn.execute(
                "UPDATE appointments SET reminder_sent_at = ? WHERE id = ?",
                (datetime.now().strftime("%d/%m/%Y, %H:%M"), appointment["id"]),
            )


def reminder_worker():
    while True:
        try:
            send_due_reminders()
        except Exception as exc:
            print(f"Reminder worker error: {exc}")
        time.sleep(REMINDER_POLL_SECONDS)


def start_background_workers():
    threading.Thread(target=reminder_worker, name="appointment-reminders", daemon=True).start()


def create_session():
    token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + SESSION_DURATION
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now.isoformat(), expires_at.isoformat()),
        )
    return token, expires_at


def cleanup_sessions(conn):
    conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (utc_now().isoformat(),))


def session_is_valid(token):
    if not token:
        return False
    with db_connection() as conn:
        cleanup_sessions(conn)
        return conn.execute("SELECT token FROM admin_sessions WHERE token = ?", (token,)).fetchone() is not None


def delete_session(token):
    if token:
        with db_connection() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))


class AppHandler(BaseHTTPRequestHandler):
    server_version = "BarbeariaDaVinte/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html", "/admin"}:
            return self.serve_file(INDEX_PATH, "text/html; charset=utf-8")
        if parsed.path.startswith("/static/"):
            return self.serve_static(parsed.path)
        if parsed.path == "/api/public-data":
            return self.send_json(HTTPStatus.OK, read_public_data(include_admin=False))
        if parsed.path == "/api/availability":
            query = parse_qs(parsed.query)
            barber_id = int(query.get("barberId", ["0"])[0] or 0)
            date_text = query.get("date", [""])[0]
            service_ids = []
            for raw_value in query.get("serviceIds", []) + query.get("serviceId", []):
                service_ids.extend([item for item in raw_value.split(",") if item])
            return self.send_json(HTTPStatus.OK, {"slots": get_availability(barber_id, date_text, service_ids)})
        if parsed.path == "/api/client/session":
            customer = enrich_customer_with_plan(self.get_current_customer())
            return self.send_json(HTTPStatus.OK, {
                "logged": bool(customer),
                "customer": customer,
                "reviewableAppointments": customer.get("reviewable_appointments", []) if customer else [],
            })
        if parsed.path == "/api/admin/session":
            return self.send_json(HTTPStatus.OK, {"logged": self.is_authenticated()})
        if parsed.path == "/api/admin/data":
            if not self.require_auth():
                return
            return self.send_json(HTTPStatus.OK, read_public_data(include_admin=True))
        if parsed.path == "/api/admin/export":
            if not self.require_auth():
                return
            return self.send_download(read_public_data(include_admin=True))
        return self.send_error_json(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/appointments":
            return self.handle_create_appointment()
        if parsed.path == "/api/client/register":
            return self.handle_client_register()
        if parsed.path == "/api/client/login":
            return self.handle_client_login()
        if parsed.path == "/api/client/forgot-password":
            return self.handle_client_forgot_password()
        if parsed.path == "/api/client/reset-password":
            return self.handle_client_reset_password()
        if parsed.path == "/api/client/logout":
            return self.handle_client_logout()
        if parsed.path == "/api/client/review":
            return self.handle_client_review()
        if parsed.path == "/api/admin/login":
            return self.handle_login()
        if parsed.path == "/api/admin/logout":
            return self.handle_logout()
        if parsed.path == "/api/admin/save":
            if not self.require_auth():
                return
            return self.handle_save()
        if parsed.path == "/api/admin/appointment-status":
            if not self.require_auth():
                return
            return self.handle_appointment_status()
        if parsed.path == "/api/admin/plan":
            if not self.require_auth():
                return
            return self.handle_save_plan()
        return self.send_error_json(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")

    def serve_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, request_path):
        relative = request_path.removeprefix("/static/").replace("/", os.sep)
        path = (STATIC_DIR / relative).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists():
            return self.send_error_json(HTTPStatus.NOT_FOUND, "Arquivo nao encontrado.")
        content_type = "application/octet-stream"
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            content_type = "image/jpeg"
        elif path.suffix.lower() == ".png":
            content_type = "image/png"
        elif path.suffix.lower() == ".css":
            content_type = "text/css; charset=utf-8"
        return self.serve_file(path, content_type)

    def handle_create_appointment(self):
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            appointment = create_appointment(payload, customer=self.get_current_customer())
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        return self.send_json(HTTPStatus.CREATED, {"appointment": appointment})

    def handle_client_register(self):
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            customer = create_customer(payload)
            token, expires_at = create_customer_session(customer["id"])
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        customer = enrich_customer_with_plan(customer)
        body = json.dumps(
            {
                "logged": True,
                "customer": customer,
                "reviewableAppointments": customer.get("reviewable_appointments", []),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus.CREATED)
        self.send_client_cookie(token, expires_at)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_client_login(self):
        payload = self.read_json_body()
        if payload is None:
            return
        customer = authenticate_customer(payload.get("identifier") or payload.get("email", ""), payload.get("password", ""))
        if not customer:
            return self.send_error_json(HTTPStatus.UNAUTHORIZED, "E-mail, telefone, CPF ou senha invalidos.")
        customer = enrich_customer_with_plan(customer)
        token, expires_at = create_customer_session(customer["id"])
        body = json.dumps(
            {
                "logged": True,
                "customer": customer,
                "reviewableAppointments": customer.get("reviewable_appointments", []),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_client_cookie(token, expires_at)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_client_forgot_password(self):
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            create_password_reset_token(payload.get("email"), self.get_base_url())
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        return self.send_json(HTTPStatus.OK, {"ok": True, "message": "Enviamos o link de recuperacao para o e-mail informado."})

    def handle_client_reset_password(self):
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            reset_customer_password(payload.get("token"), payload.get("password"))
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        return self.send_json(HTTPStatus.OK, {"ok": True, "message": "Senha redefinida com sucesso. Agora e so entrar na conta."})

    def handle_client_logout(self):
        delete_customer_session(self.get_client_session_token())
        body = json.dumps({"logged": False}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.clear_client_cookie()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_client_review(self):
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            create_barber_review(payload, self.get_current_customer())
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        customer = enrich_customer_with_plan(self.get_current_customer())
        return self.send_json(HTTPStatus.OK, {
            "logged": bool(customer),
            "customer": customer,
            "reviewableAppointments": customer.get("reviewable_appointments", []) if customer else [],
        })

    def handle_login(self):
        payload = self.read_json_body()
        if payload is None:
            return
        user = str(payload.get("user", "")).strip()
        password = str(payload.get("pass", "")).strip()
        password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        if user != ADMIN_USER or password_hash != ADMIN_PASS_HASH:
            return self.send_error_json(HTTPStatus.UNAUTHORIZED, "Login invalido.")
        token, expires_at = create_session()
        body = json.dumps({"logged": True}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_cookie(token, expires_at)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_logout(self):
        delete_session(self.get_session_token())
        body = json.dumps({"logged": False}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.clear_cookie()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_save(self):
        payload = self.read_json_body()
        if payload is None:
            return
        save_admin_data(payload)
        return self.send_json(HTTPStatus.OK, read_public_data(include_admin=True))

    def handle_appointment_status(self):
        payload = self.read_json_body()
        if payload is None:
            return
        appointment_id = int(payload.get("id") or 0)
        status = str(payload.get("status", "")).strip()
        if status not in {"confirmed", "canceled", "done"}:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, "Status invalido.")
        with db_connection() as conn:
            conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
        return self.send_json(HTTPStatus.OK, read_public_data(include_admin=True))

    def handle_save_plan(self):
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            save_monthly_plan(payload)
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        return self.send_json(HTTPStatus.OK, read_public_data(include_admin=True))

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "JSON invalido.")
            return None

    def get_session_token(self):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def get_client_session_token(self):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(CLIENT_SESSION_COOKIE)
        return morsel.value if morsel else None

    def get_current_customer(self):
        return get_customer_by_session(self.get_client_session_token())

    def is_authenticated(self):
        return session_is_valid(self.get_session_token())

    def require_auth(self):
        if self.is_authenticated():
            return True
        self.send_error_json(HTTPStatus.UNAUTHORIZED, "Sessao expirada ou nao autenticada.")
        return False

    def get_base_url(self):
        proto = self.headers.get("X-Forwarded-Proto", "http")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"{HOST}:{PORT}"
        return f"{proto}://{host}"

    def send_cookie(self, token, expires_at):
        expires_http = expires_at.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Expires={expires_http}")

    def send_client_cookie(self, token, expires_at):
        expires_http = expires_at.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.send_header("Set-Cookie", f"{CLIENT_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Expires={expires_http}")

    def clear_cookie(self):
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

    def clear_client_cookie(self):
        self.send_header("Set-Cookie", f"{CLIENT_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json(status, {"error": message})

    def send_download(self, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="barbearia-da-vinte-dados.json"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def run():
    init_db()
    start_background_workers()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Servidor pronto em http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()



