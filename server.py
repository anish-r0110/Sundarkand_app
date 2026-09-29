"""
Sundarkand Seva Team - Backend Server
Single Python 3 file using standard library + pywebpush for push notifications.
Database: SQLite3 in WAL mode.
"""

import sys
import os
import json
import sqlite3
import hashlib
import hmac
import secrets
import mimetypes
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# Optional / External dependency for Push Notifications
try:
    import pywebpush
    from py_vapid import Vapid, utils as vapid_utils
    from cryptography.hazmat.primitives import serialization
    HAS_WEBPUSH = True
except ImportError:
    HAS_WEBPUSH = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, 'app.db'))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

# Thread-safe SSE client listeners
sse_clients_lock = threading.Lock()
sse_clients = set()

# Rate limiting for login: {ip_or_email: [timestamps]}
rate_limit_lock = threading.Lock()
login_attempts = {}

# Load or initialize config with Environment Variable support (for Render / Docker / Cloud)
def load_config():
    cfg = {
        "port": 8000,
        "admin_email": "admin@sundarkand.org",
        "session_secret": secrets.token_hex(32),
        "vapid": {
            "public_key": "",
            "private_key": "",
            "claim_email": "admin@sundarkand.org"
        }
    }

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                cfg.update(loaded)
        except Exception as e:
            print(f"[WARN] Error reading config.json: {e}")

    # Environment variables override or provide defaults (Render / Heroku / Cloud)
    if os.environ.get("ADMIN_EMAIL"):
        cfg["admin_email"] = os.environ.get("ADMIN_EMAIL").strip().lower()
    if os.environ.get("SESSION_SECRET"):
        cfg["session_secret"] = os.environ.get("SESSION_SECRET").strip()
    if os.environ.get("PORT") and os.environ.get("PORT").isdigit():
        cfg["port"] = int(os.environ.get("PORT"))

    vapid_cfg = cfg.setdefault("vapid", {})
    if os.environ.get("VAPID_PUBLIC_KEY"):
        vapid_cfg["public_key"] = os.environ.get("VAPID_PUBLIC_KEY").strip()
    if os.environ.get("VAPID_PRIVATE_KEY"):
        raw_priv = os.environ.get("VAPID_PRIVATE_KEY")
        vapid_cfg["private_key"] = raw_priv.replace("\\n", "\n")
    if os.environ.get("VAPID_CLAIM_EMAIL"):
        vapid_cfg["claim_email"] = os.environ.get("VAPID_CLAIM_EMAIL").strip()
    else:
        vapid_cfg["claim_email"] = cfg["admin_email"]

    # If VAPID keys are missing, generate automatically so deployment works out-of-the-box
    if not vapid_cfg.get("public_key") or not vapid_cfg.get("private_key"):
        pub, priv = generate_vapid_keys()
        if pub and priv:
            vapid_cfg["public_key"] = pub
            vapid_cfg["private_key"] = priv
            try:
                save_config(cfg)
            except Exception:
                pass

    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[WARN] Could not write config.json (read-only filesystem?): {e}")

def generate_vapid_keys():
    if not HAS_WEBPUSH:
        print("[ERROR] pywebpush / cryptography is not installed. Run: pip install pywebpush")
        return None, None
    v = Vapid()
    v.generate_keys()
    raw_pub = v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
    b64_pub = vapid_utils.b64urlencode(raw_pub)
    priv_pem = v.private_pem().decode('utf-8')
    return b64_pub, priv_pem

# Database Initialization
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db(config):
    conn = get_db()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE COLLATE NOCASE NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invited (
                email TEXT PRIMARY KEY COLLATE NOCASE NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY NOT NULL,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                venue TEXT NOT NULL,
                address TEXT NOT NULL,
                notes TEXT,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS rsvps (
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('going', 'maybe', 'not_going')),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (event_id, user_id),
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notif_prefs (
                user_id INTEGER PRIMARY KEY NOT NULL,
                new_events INTEGER NOT NULL DEFAULT 1,
                event_changes INTEGER NOT NULL DEFAULT 1,
                reminders INTEGER NOT NULL DEFAULT 1,
                chat INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reminders_sent (
                event_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (event_id, kind),
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
            );
        """)

        # Ensure admin email from config is invited automatically on setup
        admin_email = config.get("admin_email", "").strip().lower()
        if admin_email:
            cur = conn.execute("SELECT email FROM invited WHERE email = ?", (admin_email,))
            if not cur.fetchone():
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute("INSERT OR IGNORE INTO invited (email, created_at) VALUES (?, ?)", (admin_email, now_iso))
                print(f"[INIT] Pre-invited admin email: {admin_email}")

    conn.close()

# Password Hashing Utilities using hashlib.scrypt
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        parts = stored_hash.split('$', 1)
        if len(parts) != 2:
            return False
        salt = bytes.fromhex(parts[0])
        expected_hash = parts[1]
        h = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(h.hex(), expected_hash)
    except Exception:
        return False

# Rate limiting helper
def check_rate_limit(key: str, max_attempts: int = 5, window_seconds: int = 300) -> bool:
    now = time.time()
    with rate_limit_lock:
        timestamps = login_attempts.get(key, [])
        # filter out timestamps older than window
        timestamps = [t for t in timestamps if now - t < window_seconds]
        if len(timestamps) >= max_attempts:
            login_attempts[key] = timestamps
            return False
        timestamps.append(now)
        login_attempts[key] = timestamps
        return True

def clear_rate_limit(key: str):
    with rate_limit_lock:
        if key in login_attempts:
            del login_attempts[key]

# Push Notification Dispatcher
def send_push_to_users(user_ids, payload_dict, config):
    if not HAS_WEBPUSH:
        return
    vapid_cfg = config.get("vapid", {})
    private_key = vapid_cfg.get("private_key")
    claim_email = vapid_cfg.get("claim_email") or config.get("admin_email", "admin@sundarkand.org")
    if not private_key:
        return

    def _worker():
        try:
            conn = get_db()
            placeholders = ",".join("?" for _ in user_ids)
            if not placeholders:
                conn.close()
                return
            rows = conn.execute(
                f"SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id IN ({placeholders})",
                tuple(user_ids)
            ).fetchall()

            dead_sub_ids = []
            payload_str = json.dumps(payload_dict)

            for sub in rows:
                sub_id, endpoint, p256dh, auth = sub["id"], sub["endpoint"], sub["p256dh"], sub["auth"]
                sub_info = {
                    "endpoint": endpoint,
                    "keys": {
                        "p256dh": p256dh,
                        "auth": auth
                    }
                }
                try:
                    pywebpush.webpush(
                        subscription_info=sub_info,
                        data=payload_str,
                        vapid_private_key=private_key,
                        vapid_claims={"sub": f"mailto:{claim_email}"},
                        ttl=3600
                    )
                except pywebpush.WebPushException as ex:
                    # If endpoint is invalid or unregistered (HTTP 404/410), mark for deletion
                    resp = getattr(ex, 'response', None)
                    if resp is not None and resp.status_code in (404, 410):
                        dead_sub_ids.append(sub_id)
                    else:
                        print(f"[PUSH] Warning sending to {endpoint[:30]}... : {ex}")
                except Exception as ex:
                    print(f"[PUSH] Error sending to {endpoint[:30]}... : {ex}")

            if dead_sub_ids:
                with conn:
                    del_placeholders = ",".join("?" for _ in dead_sub_ids)
                    conn.execute(f"DELETE FROM push_subscriptions WHERE id IN ({del_placeholders})", tuple(dead_sub_ids))
                    print(f"[PUSH] Cleaned up {len(dead_sub_ids)} expired push subscriptions.")
            conn.close()
        except Exception as e:
            print(f"[PUSH] Background dispatch failed: {e}")

    threading.Thread(target=_worker, daemon=True).start()

# SSE Realtime Broadcast
def broadcast_sse(event_type: str, data: dict):
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with sse_clients_lock:
        dead_clients = []
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead_clients.append(q)
        for dq in dead_clients:
            sse_clients.discard(dq)

# Background Reminder Worker
def reminder_worker(config):
    print("[WORKER] Started background event reminder worker (1 min loop).")
    while True:
        try:
            time.sleep(60)
            conn = get_db()
            now_utc = datetime.now(timezone.utc)
            # Find all events in the future (next 48h)
            cur = conn.execute("SELECT id, title, event_date, start_time, venue FROM events")
            events = cur.fetchall()

            for ev in events:
                ev_id = ev["id"]
                title = ev["title"]
                ev_date = ev["event_date"] # YYYY-MM-DD
                ev_time = ev["start_time"] # HH:MM (24-hour or local)
                venue = ev["venue"]

                # Parse event datetime assuming naive local time
                # To be robust, parse date and time string
                try:
                    event_dt_str = f"{ev_date} {ev_time}"
                    # Try common formats
                    try:
                        ev_dt = datetime.strptime(event_dt_str, "%Y-%m-%d %H:%M")
                    except ValueError:
                        ev_dt = datetime.strptime(event_dt_str, "%Y-%m-%d %I:%M %p")
                    
                    # Assume local server timezone for comparison
                    now_local = datetime.now()
                    diff = (ev_dt - now_local).total_seconds()

                    # 24 Hour Reminder (between 23h and 25h, or <= 24h and not yet sent)
                    if 0 < diff <= 24 * 3600:
                        # Check if 24h reminder already sent
                        sent = conn.execute("SELECT 1 FROM reminders_sent WHERE event_id = ? AND kind = '24h'", (ev_id,)).fetchone()
                        if not sent:
                            # Send to Going, Maybe, and non-responders with reminders=1
                            # All active users with reminders=1 minus those who RSVP'd 'not_going'
                            recipients = conn.execute("""
                                SELECT u.id FROM users u
                                JOIN notif_prefs np ON u.id = np.user_id
                                LEFT JOIN rsvps r ON r.event_id = ? AND r.user_id = u.id
                                WHERE np.reminders = 1 AND (r.status IS NULL OR r.status IN ('going', 'maybe'))
                            """, (ev_id,)).fetchall()
                            user_ids = [r["id"] for r in recipients]
                            if user_ids:
                                send_push_to_users(user_ids, {
                                    "title": f"Reminder: {title} Tomorrow",
                                    "body": f"Sundarkand Seva at {venue} at {ev_time}. Please ensure your RSVP is updated!",
                                    "url": f"#/events/{ev_id}"
                                }, config)
                            with conn:
                                conn.execute("INSERT OR IGNORE INTO reminders_sent (event_id, kind) VALUES (?, '24h')", (ev_id,))
                            print(f"[REMINDER] Sent 24h reminder for event #{ev_id} ({title}) to {len(user_ids)} users.")

                    # 2 Hour Reminder (between 0 and 2 hours before event)
                    if 0 < diff <= 2 * 3600:
                        sent2 = conn.execute("SELECT 1 FROM reminders_sent WHERE event_id = ? AND kind = '2h'", (ev_id,)).fetchone()
                        if not sent2:
                            recipients = conn.execute("""
                                SELECT u.id FROM users u
                                JOIN notif_prefs np ON u.id = np.user_id
                                LEFT JOIN rsvps r ON r.event_id = ? AND r.user_id = u.id
                                WHERE np.reminders = 1 AND (r.status IS NULL OR r.status IN ('going', 'maybe'))
                            """, (ev_id,)).fetchall()
                            user_ids = [r["id"] for r in recipients]
                            if user_ids:
                                send_push_to_users(user_ids, {
                                    "title": f"Starting Soon (2h): {title}",
                                    "body": f"We begin at {ev_time} at {venue}. Jai Siya Ram!",
                                    "url": f"#/events/{ev_id}"
                                }, config)
                            with conn:
                                conn.execute("INSERT OR IGNORE INTO reminders_sent (event_id, kind) VALUES (?, '2h')", (ev_id,))
                            print(f"[REMINDER] Sent 2h reminder for event #{ev_id} ({title}) to {len(user_ids)} users.")

                except Exception as parse_ex:
                    # Ignore parsing error for invalid date/time strings
                    continue

            conn.close()
        except Exception as e:
            print(f"[WORKER] Error in reminder check: {e}")

# HTTP Request Handler
class AppRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def address_string(self):
        # Avoid slow reverse DNS lookup on localhost/Windows
        return self.client_address[0]

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    def get_cookie(self, name):
        cookie_header = self.headers.get("Cookie", "")
        for item in cookie_header.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                k, v = item.split("=", 1)
                if k.strip() == name:
                    return v.strip()
        return None

    def get_current_user(self):
        token = self.get_cookie("session_token")
        if not token:
            return None
        conn = get_db()
        now_iso = datetime.now(timezone.utc).isoformat()
        row = conn.execute("""
            SELECT u.id, u.email, u.display_name, u.role, s.expires_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > ?
        """, (token, now_iso)).fetchone()
        conn.close()
        if row:
            return {
                "id": row["id"],
                "email": row["email"],
                "display_name": row["display_name"],
                "role": row["role"]
            }
        return None

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def send_json(self, data, status=200, headers=None):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, message, status=400):
        self.send_json({"error": message}, status=status)

    def set_session_cookie(self, token, max_age=30*86400):
        # Format cookie
        # If client is accessed via HTTPS or behind reverse proxy, allow Secure flag
        is_secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        secure_flag = "; Secure" if is_secure else ""
        cookie_val = f"session_token={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure_flag}"
        return ("Set-Cookie", cookie_val)

    def clear_session_cookie(self):
        is_secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        secure_flag = "; Secure" if is_secure else ""
        return ("Set-Cookie", f"session_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure_flag}")

    # ROUTING
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Health check endpoint for Render / Uptime monitors
        if path == "/health" or path == "/api/health":
            return self.send_json({"status": "ok", "app": "sundarkand-seva"})

        # Static assets
        if path == "/" or not path.startswith("/api/"):
            return self.serve_static(path)

        # API Routes
        # 1. Auth check
        if path == "/api/auth/check":
            user = self.get_current_user()
            return self.send_json({"user": user})

        # 2. VAPID public key
        if path == "/api/push/vapid-public-key":
            vapid_pub = self.server.config.get("vapid", {}).get("public_key", "")
            return self.send_json({"public_key": vapid_pub})

        # All following routes require authenticated user
        user = self.get_current_user()
        if not user:
            return self.send_error_json("Authentication required", status=401)

        # 3. Events List
        if path == "/api/events":
            return self.handle_get_events(user)

        # 4. Single Event Details
        if path.startswith("/api/events/"):
            event_id_str = path[len("/api/events/"):]
            if event_id_str.isdigit():
                return self.handle_get_event_detail(int(event_id_str), user)

        # 5. Attendance for Event
        if path.startswith("/api/attendance/"):
            event_id_str = path[len("/api/attendance/"):]
            if event_id_str.isdigit():
                return self.handle_get_attendance(int(event_id_str), user)

        # 6. Chat messages list
        if path == "/api/chat":
            before_id = query.get("before_id", [None])[0]
            return self.handle_get_chat_messages(before_id)

        # 7. Chat SSE stream
        if path == "/api/chat/stream":
            return self.handle_chat_stream(user)

        # 8. User Profile & Notification preferences
        if path == "/api/profile":
            return self.handle_get_profile(user)

        # 9. Admin Members & Invites list
        if path == "/api/admin/members":
            if user["role"] != "admin":
                return self.send_error_json("Admin access required", status=403)
            return self.handle_admin_members()

        self.send_error_json("Not Found", status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        client_ip = self.client_address[0]

        # 1. Status for email (checks if invited / already registered)
        if path == "/api/auth/status-for-email":
            body = self.read_json_body()
            email = body.get("email", "").strip().lower()
            if not email:
                return self.send_error_json("Email is required")
            conn = get_db()
            inv = conn.execute("SELECT 1 FROM invited WHERE email = ?", (email,)).fetchone()
            usr = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
            conn.close()
            return self.send_json({
                "email": email,
                "invited": bool(inv),
                "registered": bool(usr)
            })

        # 2. Register (Invited email setting password for first time)
        if path == "/api/auth/register":
            body = self.read_json_body()
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")
            display_name = body.get("display_name", "").strip()

            if not email or not password or not display_name:
                return self.send_error_json("Email, display name, and password are required")
            if len(password) < 6:
                return self.send_error_json("Password must be at least 6 characters")

            conn = get_db()
            # Must be invited
            inv = conn.execute("SELECT 1 FROM invited WHERE email = ?", (email,)).fetchone()
            if not inv:
                conn.close()
                return self.send_error_json("This email has not been invited yet. Please contact the team admin for an invite.", status=403)

            # Check if user already exists
            usr = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
            if usr:
                conn.close()
                return self.send_error_json("An account already exists for this email. Please log in.")

            # Determine role: admin if matches config or first user
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            admin_email = self.server.config.get("admin_email", "").strip().lower()
            role = "admin" if (email == admin_email or user_count == 0) else "member"

            pwd_hash = hash_password(password)
            now_iso = datetime.now(timezone.utc).isoformat()

            with conn:
                cur = conn.execute("""
                    INSERT INTO users (email, display_name, password_hash, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (email, display_name, pwd_hash, role, now_iso))
                new_user_id = cur.lastrowid
                # Default notification preferences
                conn.execute("""
                    INSERT INTO notif_prefs (user_id, new_events, event_changes, reminders, chat)
                    VALUES (?, 1, 1, 1, 0)
                """, (new_user_id,))

                # Create session token
                token = secrets.token_hex(32)
                expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                             (token, new_user_id, expires_at))

            conn.close()

            user_data = {
                "id": new_user_id,
                "email": email,
                "display_name": display_name,
                "role": role
            }
            cookie_header = self.set_session_cookie(token)
            return self.send_json({"user": user_data}, headers={cookie_header[0]: cookie_header[1]})

        # 3. Login
        if path == "/api/auth/login":
            body = self.read_json_body()
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")

            if not email or not password:
                return self.send_error_json("Email and password are required")

            # Check rate limiting
            rate_key = f"{client_ip}:{email}"
            if not check_rate_limit(rate_key):
                return self.send_error_json("Too many failed attempts. Please wait 5 minutes.", status=429)

            conn = get_db()
            row = conn.execute("SELECT id, email, display_name, password_hash, role FROM users WHERE email = ?", (email,)).fetchone()
            if not row or not verify_password(password, row["password_hash"]):
                conn.close()
                return self.send_error_json("Invalid email or password", status=401)

            clear_rate_limit(rate_key)

            token = secrets.token_hex(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            with conn:
                conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                             (token, row["id"], expires_at))
            conn.close()

            user_data = {
                "id": row["id"],
                "email": row["email"],
                "display_name": row["display_name"],
                "role": row["role"]
            }
            cookie_header = self.set_session_cookie(token)
            return self.send_json({"user": user_data}, headers={cookie_header[0]: cookie_header[1]})

        # 4. Logout
        if path == "/api/auth/logout":
            token = self.get_cookie("session_token")
            if token:
                conn = get_db()
                with conn:
                    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.close()
            cookie_header = self.clear_session_cookie()
            return self.send_json({"success": True}, headers={cookie_header[0]: cookie_header[1]})

        # AUTH CHECK FOR SUBSEQUENT POSTs
        user = self.get_current_user()
        if not user:
            return self.send_error_json("Authentication required", status=401)

        # 5. Create Event (Admin only)
        if path == "/api/events":
            if user["role"] != "admin":
                return self.send_error_json("Admin access required", status=403)
            return self.handle_create_event(user)

        # 6. RSVP for Event
        if path.startswith("/api/events/") and path.endswith("/rsvp"):
            parts = path.split("/")
            if len(parts) == 5 and parts[3].isdigit():
                event_id = int(parts[3])
                return self.handle_set_rsvp(event_id, user)

        # 7. Post Chat Message
        if path == "/api/chat":
            return self.handle_post_chat(user)

        # 8. Push: Subscribe
        if path == "/api/push/subscribe":
            return self.handle_push_subscribe(user)

        # 9. Push: Unsubscribe
        if path == "/api/push/unsubscribe":
            return self.handle_push_unsubscribe(user)

        # 10. Push: Test notification
        if path == "/api/push/test":
            return self.handle_push_test(user)

        # 11. Admin: Invite email
        if path == "/api/admin/invites":
            if user["role"] != "admin":
                return self.send_error_json("Admin access required", status=403)
            return self.handle_admin_invite()

        # 12. Admin: Change role
        if path.startswith("/api/admin/members/") and path.endswith("/role"):
            parts = path.split("/")
            if len(parts) == 6 and parts[4].isdigit():
                target_user_id = int(parts[4])
                if user["role"] != "admin":
                    return self.send_error_json("Admin access required", status=403)
                return self.handle_admin_change_role(target_user_id, user)

        self.send_error_json("Not Found", status=404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        user = self.get_current_user()
        if not user:
            return self.send_error_json("Authentication required", status=401)

        # 1. Edit Event (Admin only)
        if path.startswith("/api/events/"):
            event_id_str = path[len("/api/events/"):]
            if event_id_str.isdigit():
                if user["role"] != "admin":
                    return self.send_error_json("Admin access required", status=403)
                return self.handle_edit_event(int(event_id_str), user)

        # 2. Update Display Name
        if path == "/api/profile/name":
            body = self.read_json_body()
            name = body.get("display_name", "").strip()
            if not name:
                return self.send_error_json("Display name cannot be empty")
            conn = get_db()
            with conn:
                conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, user["id"]))
            conn.close()
            broadcast_sse("user_updated", {"id": user["id"], "display_name": name})
            return self.send_json({"success": True, "display_name": name})

        # 3. Update Notification Preferences
        if path == "/api/profile/notifications":
            body = self.read_json_body()
            new_events = 1 if body.get("new_events", True) else 0
            event_changes = 1 if body.get("event_changes", True) else 0
            reminders = 1 if body.get("reminders", True) else 0
            chat = 1 if body.get("chat", False) else 0
            conn = get_db()
            with conn:
                conn.execute("""
                    INSERT INTO notif_prefs (user_id, new_events, event_changes, reminders, chat)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        new_events = excluded.new_events,
                        event_changes = excluded.event_changes,
                        reminders = excluded.reminders,
                        chat = excluded.chat
                """, (user["id"], new_events, event_changes, reminders, chat))
            conn.close()
            return self.send_json({"success": True})

        self.send_error_json("Not Found", status=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        user = self.get_current_user()
        if not user:
            return self.send_error_json("Authentication required", status=401)

        # 1. Delete Event (Admin only)
        if path.startswith("/api/events/"):
            event_id_str = path[len("/api/events/"):]
            if event_id_str.isdigit():
                if user["role"] != "admin":
                    return self.send_error_json("Admin access required", status=403)
                return self.handle_delete_event(int(event_id_str))

        # 2. Delete Chat Message (Admin or sender)
        if path.startswith("/api/chat/"):
            msg_id_str = path[len("/api/chat/"):]
            if msg_id_str.isdigit():
                return self.handle_delete_chat(int(msg_id_str), user)

        # 3. Admin: Remove Invite
        if path.startswith("/api/admin/invites/"):
            raw_email = path[len("/api/admin/invites/"):]
            target_email = unquote(raw_email).strip().lower()
            if user["role"] != "admin":
                return self.send_error_json("Admin access required", status=403)
            conn = get_db()
            with conn:
                conn.execute("DELETE FROM invited WHERE email = ?", (target_email,))
            conn.close()
            return self.send_json({"success": True})

        # 4. Admin: Remove Member
        if path.startswith("/api/admin/members/"):
            member_id_str = path[len("/api/admin/members/"):]
            if member_id_str.isdigit():
                if user["role"] != "admin":
                    return self.send_error_json("Admin access required", status=403)
                target_user_id = int(member_id_str)
                if target_user_id == user["id"]:
                    return self.send_error_json("Cannot delete your own account")
                conn = get_db()
                with conn:
                    conn.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
                conn.close()
                return self.send_json({"success": True})

        self.send_error_json("Not Found", status=404)

    # EVENT HANDLERS
    def handle_get_events(self, user):
        conn = get_db()
        # Fetch events along with the current user's RSVP status
        query = """
            SELECT e.id, e.title, e.event_date, e.start_time, e.venue, e.address, e.notes,
                   e.created_by, e.created_at,
                   r.status AS my_rsvp
            FROM events e
            LEFT JOIN rsvps r ON r.event_id = e.id AND r.user_id = ?
            ORDER BY e.event_date ASC, e.start_time ASC
        """
        rows = conn.execute(query, (user["id"],)).fetchall()

        # Split into upcoming and past based on today's local date
        today_str = datetime.now().strftime("%Y-%m-%d")
        upcoming = []
        past = []

        for row in rows:
            ev = dict(row)
            if ev["event_date"] >= today_str:
                upcoming.append(ev)
            else:
                past.append(ev)

        # Past events descending (most recent past first)
        past.reverse()

        conn.close()
        return self.send_json({"upcoming": upcoming, "past": past})

    def handle_get_event_detail(self, event_id: int, user):
        conn = get_db()
        row = conn.execute("""
            SELECT e.id, e.title, e.event_date, e.start_time, e.venue, e.address, e.notes,
                   e.created_by, e.created_at,
                   r.status AS my_rsvp
            FROM events e
            LEFT JOIN rsvps r ON r.event_id = e.id AND r.user_id = ?
            WHERE e.id = ?
        """, (user["id"], event_id)).fetchone()
        conn.close()
        if not row:
            return self.send_error_json("Event not found", status=404)
        return self.send_json({"event": dict(row)})

    def handle_create_event(self, user):
        body = self.read_json_body()
        title = body.get("title", "").strip()
        event_date = body.get("event_date", "").strip() # YYYY-MM-DD
        start_time = body.get("start_time", "").strip() # HH:MM or similar
        venue = body.get("venue", "").strip()
        address = body.get("address", "").strip()
        notes = body.get("notes", "").strip()

        if not title or not event_date or not start_time or not venue:
            return self.send_error_json("Title, date, start time, and venue are required")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        with conn:
            cur = conn.execute("""
                INSERT INTO events (title, event_date, start_time, venue, address, notes, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, event_date, start_time, venue, address, notes, user["id"], now_iso))
            event_id = cur.lastrowid

            # Query users subscribed to new_events
            sub_users = conn.execute("""
                SELECT u.id FROM users u
                JOIN notif_prefs np ON u.id = np.user_id
                WHERE np.new_events = 1 AND u.id != ?
            """, (user["id"],)).fetchall()
            user_ids = [r["id"] for r in sub_users]

        conn.close()

        # Send push notification for new event
        if user_ids:
            send_push_to_users(user_ids, {
                "title": f"New Seva: {title}",
                "body": f"Scheduled for {event_date} at {venue} ({start_time}). Tap to view and RSVP.",
                "url": f"#/events/{event_id}"
            }, self.server.config)

        # Broadcast live event creation to all clients
        broadcast_sse("event_created", {"id": event_id})

        return self.send_json({"success": True, "id": event_id})

    def handle_edit_event(self, event_id: int, user):
        body = self.read_json_body()
        title = body.get("title", "").strip()
        event_date = body.get("event_date", "").strip()
        start_time = body.get("start_time", "").strip()
        venue = body.get("venue", "").strip()
        address = body.get("address", "").strip()
        notes = body.get("notes", "").strip()

        if not title or not event_date or not start_time or not venue:
            return self.send_error_json("Title, date, start time, and venue are required")

        conn = get_db()
        old_ev = conn.execute("SELECT event_date, start_time, venue FROM events WHERE id = ?", (event_id,)).fetchone()
        if not old_ev:
            conn.close()
            return self.send_error_json("Event not found", status=404)

        has_critical_changes = (
            old_ev["event_date"] != event_date or
            old_ev["start_time"] != start_time or
            old_ev["venue"] != venue
        )

        with conn:
            conn.execute("""
                UPDATE events
                SET title = ?, event_date = ?, start_time = ?, venue = ?, address = ?, notes = ?
                WHERE id = ?
            """, (title, event_date, start_time, venue, address, notes, event_id))

            # If date or time changed, clear old reminder records so appropriate new reminders fire
            if old_ev["event_date"] != event_date or old_ev["start_time"] != start_time:
                conn.execute("DELETE FROM reminders_sent WHERE event_id = ?", (event_id,))

            sub_users = conn.execute("""
                SELECT u.id FROM users u
                JOIN notif_prefs np ON u.id = np.user_id
                WHERE np.event_changes = 1 AND u.id != ?
            """, (user["id"],)).fetchall()
            user_ids = [r["id"] for r in sub_users]

        conn.close()

        # Push notification on event date/time/venue changes
        if has_critical_changes and user_ids:
            send_push_to_users(user_ids, {
                "title": f"Update: {title}",
                "body": f"Event details changed to {event_date} at {venue} ({start_time}).",
                "url": f"#/events/{event_id}"
            }, self.server.config)

        # Broadcast live event update to all clients
        broadcast_sse("event_updated", {"id": event_id})

        return self.send_json({"success": True})

    def handle_delete_event(self, event_id: int):
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.close()

        # Broadcast live event deletion to all clients
        broadcast_sse("event_deleted", {"id": event_id})

        return self.send_json({"success": True})

    def handle_set_rsvp(self, event_id: int, user):
        body = self.read_json_body()
        status = body.get("status")
        if status not in ("going", "maybe", "not_going"):
            return self.send_error_json("Invalid RSVP status. Must be 'going', 'maybe', or 'not_going'")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        with conn:
            conn.execute("""
                INSERT INTO rsvps (event_id, user_id, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(event_id, user_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
            """, (event_id, user["id"], status, now_iso))
        conn.close()

        # Broadcast live RSVP update to all connected clients
        broadcast_sse("rsvp", {
            "event_id": event_id,
            "user_id": user["id"],
            "display_name": user["display_name"],
            "status": status
        })

        return self.send_json({"success": True, "status": status})

    # ATTENDANCE HANDLER
    def handle_get_attendance(self, event_id: int, user):
        conn = get_db()
        # Verify event exists
        ev = conn.execute("SELECT id, title, event_date, start_time, venue FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            conn.close()
            return self.send_error_json("Event not found", status=404)

        # Get all users and their RSVP status for this event
        query = """
            SELECT u.id, u.email, u.display_name, u.role, r.status
            FROM users u
            LEFT JOIN rsvps r ON r.event_id = ? AND r.user_id = u.id
            ORDER BY u.display_name COLLATE NOCASE ASC
        """
        rows = conn.execute(query, (event_id,)).fetchall()
        conn.close()

        counts = {
            "going": 0,
            "maybe": 0,
            "not_going": 0,
            "no_response": 0,
            "total_members": len(rows)
        }

        going_list = []
        maybe_list = []
        not_going_list = []
        no_response_list = []

        for r in rows:
            st = r["status"]
            person = {
                "id": r["id"],
                "display_name": r["display_name"],
                "email": r["email"],
                "role": r["role"]
            }
            if st == "going":
                counts["going"] += 1
                going_list.append(person)
            elif st == "maybe":
                counts["maybe"] += 1
                maybe_list.append(person)
            elif st == "not_going":
                counts["not_going"] += 1
                not_going_list.append(person)
            else:
                counts["no_response"] += 1
                no_response_list.append(person)

        # Make attendance roster visible for everybody so anyone can see who is going and who is not
        res = {
            "event": dict(ev),
            "counts": counts,
            "roster": {
                "going": going_list,
                "maybe": maybe_list,
                "not_going": not_going_list,
                "no_response": no_response_list
            }
        }

        return self.send_json(res)

    # CHAT HANDLERS
    def handle_get_chat_messages(self, before_id=None):
        conn = get_db()
        limit = 50
        if before_id and str(before_id).isdigit():
            rows = conn.execute("""
                SELECT m.id, m.content, m.created_at, m.user_id, u.display_name, u.role
                FROM messages m
                JOIN users u ON m.user_id = u.id
                WHERE m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
            """, (int(before_id), limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT m.id, m.content, m.created_at, m.user_id, u.display_name, u.role
                FROM messages m
                JOIN users u ON m.user_id = u.id
                ORDER BY m.id DESC
                LIMIT ?
            """, (limit,)).fetchall()
        conn.close()

        # Reverse to chronological order (oldest to newest)
        messages = [dict(r) for r in reversed(rows)]
        return self.send_json({"messages": messages})

    def handle_post_chat(self, user):
        body = self.read_json_body()
        content = body.get("content", "").strip()
        if not content:
            return self.send_error_json("Message cannot be empty")
        if len(content) > 2000:
            return self.send_error_json("Message exceeds 2000 characters")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        with conn:
            cur = conn.execute("""
                INSERT INTO messages (user_id, content, created_at)
                VALUES (?, ?, ?)
            """, (user["id"], content, now_iso))
            msg_id = cur.lastrowid

            # Find users with chat notifications enabled (opt-in, excluding sender)
            sub_users = conn.execute("""
                SELECT u.id FROM users u
                JOIN notif_prefs np ON u.id = np.user_id
                WHERE np.chat = 1 AND u.id != ?
            """, (user["id"],)).fetchall()
            push_user_ids = [r["id"] for r in sub_users]

        conn.close()

        msg_obj = {
            "id": msg_id,
            "user_id": user["id"],
            "display_name": user["display_name"],
            "role": user["role"],
            "content": content,
            "created_at": now_iso
        }

        # Broadcast realtime message to all active SSE listeners
        broadcast_sse("message", msg_obj)

        # Send push notification to users with chat=1
        if push_user_ids:
            preview = (content[:80] + "...") if len(content) > 80 else content
            send_push_to_users(push_user_ids, {
                "title": f"Chat: {user['display_name']}",
                "body": preview,
                "url": "#/chat"
            }, self.server.config)

        return self.send_json({"success": True, "message": msg_obj})

    def handle_delete_chat(self, msg_id: int, user):
        conn = get_db()
        row = conn.execute("SELECT user_id FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            conn.close()
            return self.send_error_json("Message not found", status=404)

        if user["role"] != "admin" and row["user_id"] != user["id"]:
            conn.close()
            return self.send_error_json("Cannot delete another member's message", status=403)

        with conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.close()

        # Broadcast deletion to SSE listeners
        broadcast_sse("delete", {"id": msg_id})
        return self.send_json({"success": True, "id": msg_id})

    def handle_chat_stream(self, user):
        # Server-Sent Events handler
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        client_queue = queue.Queue(maxsize=100)
        with sse_clients_lock:
            sse_clients.add(client_queue)

        # Send initial connected event
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
        except Exception:
            with sse_clients_lock:
                sse_clients.discard(client_queue)
            return

        try:
            while True:
                try:
                    # Wait for next event or 20 second heartbeat timeout
                    raw_msg = client_queue.get(timeout=20)
                    self.wfile.write(raw_msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Send keep-alive ping
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with sse_clients_lock:
                sse_clients.discard(client_queue)

    # PROFILE & ADMIN HANDLERS
    def handle_get_profile(self, user):
        conn = get_db()
        usr = conn.execute("SELECT id, email, display_name, role, created_at FROM users WHERE id = ?", (user["id"],)).fetchone()
        prefs = conn.execute("SELECT new_events, event_changes, reminders, chat FROM notif_prefs WHERE user_id = ?", (user["id"],)).fetchone()
        conn.close()

        p_dict = {
            "new_events": bool(prefs["new_events"]) if prefs else True,
            "event_changes": bool(prefs["event_changes"]) if prefs else True,
            "reminders": bool(prefs["reminders"]) if prefs else True,
            "chat": bool(prefs["chat"]) if prefs else False
        }
        return self.send_json({
            "user": dict(usr),
            "notif_prefs": p_dict
        })

    def handle_admin_members(self):
        conn = get_db()
        users = conn.execute("""
            SELECT id, email, display_name, role, created_at
            FROM users
            ORDER BY role ASC, display_name COLLATE NOCASE ASC
        """).fetchall()

        invites = conn.execute("""
            SELECT email, created_at
            FROM invited
            WHERE email NOT IN (SELECT email FROM users)
            ORDER BY created_at DESC
        """).fetchall()
        conn.close()

        return self.send_json({
            "members": [dict(u) for u in users],
            "pending_invites": [dict(i) for i in invites]
        })

    def handle_admin_invite(self):
        body = self.read_json_body()
        email = body.get("email", "").strip().lower()
        if not email or "@" not in email:
            return self.send_error_json("A valid email address is required")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        with conn:
            conn.execute("INSERT OR IGNORE INTO invited (email, created_at) VALUES (?, ?)", (email, now_iso))
        conn.close()
        return self.send_json({"success": True, "email": email})

    def handle_admin_change_role(self, target_user_id: int, current_user):
        body = self.read_json_body()
        new_role = body.get("role")
        if new_role not in ("admin", "member"):
            return self.send_error_json("Invalid role. Must be 'admin' or 'member'")

        if target_user_id == current_user["id"] and new_role != "admin":
            return self.send_error_json("You cannot demote yourself from admin")

        conn = get_db()
        with conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, target_user_id))
        conn.close()
        return self.send_json({"success": True, "role": new_role})

    # PUSH SUBSCRIPTION HANDLERS
    def handle_push_subscribe(self, user):
        body = self.read_json_body()
        endpoint = body.get("endpoint")
        keys = body.get("keys", {})
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")

        if not endpoint or not p256dh or not auth:
            return self.send_error_json("Invalid subscription format")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        with conn:
            conn.execute("""
                INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    user_id = excluded.user_id,
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    created_at = excluded.created_at
            """, (user["id"], endpoint, p256dh, auth, now_iso))
        conn.close()
        return self.send_json({"success": True})

    def handle_push_unsubscribe(self, user):
        body = self.read_json_body()
        endpoint = body.get("endpoint")
        if endpoint:
            conn = get_db()
            with conn:
                conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?", (endpoint, user["id"]))
            conn.close()
        return self.send_json({"success": True})

    def handle_push_test(self, user):
        send_push_to_users([user["id"]], {
            "title": "Sundarkand Seva Team",
            "body": f"Jai Siya Ram, {user['display_name']}! Push notifications are successfully active.",
            "url": "#/profile"
        }, self.server.config)
        return self.send_json({"success": True, "message": "Test notification queued."})

    # STATIC FILES HANDLER
    def serve_static(self, req_path):
        if req_path == "/" or req_path == "":
            rel_path = "index.html"
        elif req_path.startswith("/static/"):
            rel_path = req_path[len("/static/"):]
        else:
            rel_path = req_path.lstrip("/")

        safe_path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not safe_path.startswith(STATIC_DIR) or not os.path.isfile(safe_path):
            # Fallback for SPA routing to index.html if not found and not an asset
            if not os.path.splitext(rel_path)[1]:
                safe_path = os.path.join(STATIC_DIR, "index.html")
            else:
                self.send_error(404, "File not found")
                return

        mime_type, _ = mimetypes.guess_type(safe_path)
        if not mime_type:
            if safe_path.endswith(".webmanifest"):
                mime_type = "application/manifest+json"
            else:
                mime_type = "application/octet-stream"

        try:
            with open(safe_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            # Service worker must not be aggressively cached
            if rel_path.endswith("sw.js"):
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            else:
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def log_message(self, format, *args):
        # Concise logging
        # Suppress chat stream polling from filling stdout
        if len(args) > 0 and "/api/chat/stream" in str(args[0]):
            return
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]} -> {args[2]}\n")

class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            # Client abruptly closed connection or navigated away; suppress noisy terminal traceback
            return
        super().handle_error(request, client_address)

def run_server(port=None):
    config = load_config()
    
    # Priority for port: explicit CLI argument -> PORT env var (Render default) -> config.json -> default 8000
    env_port = os.environ.get("PORT")
    if port is not None:
        server_port = port
    elif env_port and env_port.isdigit():
        server_port = int(env_port)
    else:
        server_port = config.get("port", 8000)

    # Initialize DB
    init_db(config)

    # Start background reminder worker thread
    threading.Thread(target=reminder_worker, args=(config,), daemon=True).start()

    server = QuietThreadingHTTPServer(("0.0.0.0", server_port), AppRequestHandler)
    server.config = config
    print(f"\n==========================================")
    print(f" Sundarkand Seva Team PWA Server")
    print(f" Local URL: http://localhost:{server_port}")
    print(f" Admin Email: {config.get('admin_email')}")
    print(f" WebPush Enabled: {HAS_WEBPUSH}")
    print(f"==========================================\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        server.server_close()

def main():
    if "--generate-vapid" in sys.argv:
        print("Generating fresh VAPID keys for Web Push...")
        pub, priv = generate_vapid_keys()
        if pub and priv:
            cfg = load_config()
            if "vapid" not in cfg:
                cfg["vapid"] = {}
            cfg["vapid"]["public_key"] = pub
            cfg["vapid"]["private_key"] = priv
            save_config(cfg)
            print("Successfully generated and saved VAPID keys into config.json!")
            print(f"Public Key: {pub}\n")
        sys.exit(0)

    port = None
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    run_server(port)

if __name__ == "__main__":
    main()
