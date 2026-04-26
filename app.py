import hashlib
import json
import os
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "")).expanduser() if os.environ.get("DATA_DIR") else None
DB_PATH = Path(os.environ.get("DB_PATH", "")).expanduser() if os.environ.get("DB_PATH") else (
    DATA_DIR / "barbearia_da_vinte_data.sqlite" if DATA_DIR else BASE_DIR / "barbearia_da_vinte_data.sqlite"
)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
INDEX_PATH = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
SESSION_COOKIE = "barbearia_vinte_session"
CLIENT_SESSION_COOKIE = "barbearia_vinte_client_session"
SESSION_DURATION = timedelta(hours=12)
CLIENT_SESSION_DURATION = timedelta(days=30)
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


def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
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
            CREATE TABLE IF NOT EXISTS monthly_plans (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
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
    with db_connection() as conn:
        settings = read_settings(conn)
        services = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, description, price, duration, active FROM services WHERE active = 1 ORDER BY id"
            ).fetchall()
        ]
        barbers = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, specialty, email, photo, active FROM barbers WHERE active = 1 ORDER BY id"
            ).fetchall()
        ]
        payload = {"settings": settings, "services": services, "barbers": barbers}
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
               a.status, a.created_at, a.customer_id, a.plan_booking,
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


def read_plans(conn):
    rows = conn.execute(
        """
        SELECT p.id, p.customer_id, p.start_date, p.end_date, p.note, p.active, p.created_at,
               c.name AS customer_name, c.phone AS customer_phone, c.email AS customer_email
        FROM monthly_plans p
        JOIN customers c ON c.id = p.customer_id
        ORDER BY p.active DESC, p.end_date DESC, p.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def read_customers(conn):
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
            SELECT id, start_date, end_date, note, active
            FROM monthly_plans
            WHERE customer_id = ? AND active = 1
            ORDER BY end_date DESC, id DESC
            LIMIT 1
            """,
            (customer["id"],),
        ).fetchone()
        customer["last_haircut_date"] = last_haircut["date"] if last_haircut else ""
        if customer["last_haircut_date"]:
            customer["days_since_last_haircut"] = (today - datetime.strptime(customer["last_haircut_date"], "%Y-%m-%d").date()).days
        else:
            customer["days_since_last_haircut"] = None
        customer["active_plan"] = dict(next_active_plan) if next_active_plan else None
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
    with db_connection() as conn:
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
    customer["active_plan"] = dict(plan) if plan else None
    return customer


def delete_customer_session(token):
    if token:
        with db_connection() as conn:
            conn.execute("DELETE FROM customer_sessions WHERE token = ?", (token,))


def get_active_plan_for_customer(conn, customer_id, target_date):
    return conn.execute(
        """
        SELECT id, customer_id, start_date, end_date, note, active, created_at
        FROM monthly_plans
        WHERE customer_id = ? AND active = 1 AND start_date <= ? AND end_date >= ?
        ORDER BY end_date DESC, id DESC
        LIMIT 1
        """,
        (customer_id, target_date, target_date),
    ).fetchone()


def save_monthly_plan(payload):
    customer_id = int(payload.get("customerId") or 0)
    start_date = str(payload.get("startDate", "")).strip()
    end_date = str(payload.get("endDate", "")).strip()
    note = str(payload.get("note", "")).strip()
    if not customer_id or not start_date or not end_date:
        raise ValueError("Escolha o cliente e as datas do plano.")
    with db_connection() as conn:
        conn.execute("UPDATE monthly_plans SET active = 0 WHERE customer_id = ? AND active = 1", (customer_id,))
        conn.execute(
            """
            INSERT INTO monthly_plans (customer_id, start_date, end_date, note, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (customer_id, start_date, end_date, note, datetime.now().strftime("%d/%m/%Y, %H:%M")),
        )


def normalize_money(value):
    try:
        return max(0, float(str(value).replace(",", ".")))
    except ValueError:
        return 0.0


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
        if plan_booking:
            if not customer_id:
                raise ValueError("Entre na sua conta para usar corte do plano.")
            if any("corte" not in row["name"].lower() for row in services):
                raise ValueError("O plano mensal so pode ser usado em servicos de corte.")
            active_plan = get_active_plan_for_customer(conn, customer_id, date_text)
            if not active_plan:
                raise ValueError("Seu plano mensal nao esta ativo para essa data.")
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
        return appointment_dict


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
        (appointment["id"], recipient, subject, body, status, error, datetime.now().strftime("%d/%m/%Y, %H:%M")),
    )


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
            return self.send_json(HTTPStatus.OK, {"logged": bool(customer), "customer": customer})
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
        if parsed.path == "/api/client/logout":
            return self.handle_client_logout()
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
        body = json.dumps({"logged": True, "customer": customer}, ensure_ascii=False).encode("utf-8")
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
        body = json.dumps({"logged": True, "customer": customer}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_client_cookie(token, expires_at)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_client_logout(self):
        delete_customer_session(self.get_client_session_token())
        body = json.dumps({"logged": False}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.clear_client_cookie()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Servidor pronto em http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()



