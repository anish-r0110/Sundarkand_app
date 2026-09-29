# Sundarkand Seva Team (सुन्दरकाण्ड सेवा टीम)

A private, invite-only, mobile-first installable Progressive Web App (PWA) designed for a dedicated group that performs Sundarkand Ramayan musical recitals and seva.

---

## 🌟 Key Highlights & Architecture

- **Ultra-Lightweight Backend**: A single Python 3 file (`server.py`) using only Python's standard library (`http.server.ThreadingHTTPServer`, `sqlite3`, `hashlib`, `hmac`, `secrets`, `json`). The only third-party package is `pywebpush` for Web Push notifications.
- **SQLite with WAL Mode**: Single database file (`app.db`) initialized automatically on first run with `PRAGMA journal_mode=WAL;` and foreign keys enabled. No heavy ORMs.
- **Vanilla Frontend (No Build Step, No Node/npm)**: Pure HTML5, modern CSS custom properties, and vanilla JavaScript (ES modules) served directly from `/static`.
- **Real-Time Group Chat**: Server-Sent Events (`GET /api/chat/stream`) with zero external broker dependencies (no Redis, no websockets).
- **Hardened Security & Roles**:
  - No public signup: Members must be invited by an admin first.
  - Password hashing via `hashlib.scrypt` with individual cryptographically secure 16-byte random salts.
  - Sessions via random 256-bit tokens in `HttpOnly; SameSite=Lax` cookies with server-side validation and expiration.
  - Login rate-limiting and 100% parameterized SQL queries.
  - Role-based access control (`admin` vs `member`).
- **Devotional Aesthetics**: Sacred Saffron (`#D97706`) and Deep Maroon (`#701A1E`) color palette, soft cream background (`#FDFBF7`), dark earth brown text (`#2D1810`), elegant serif headings with system sans-serif body, custom Diya iconography, 44px touch targets, and mobile-centered layout (360–430px) on desktop viewports.
- **PWA & Web Push**:
  - Offline app shell caching via `static/sw.js` and `manifest.webmanifest`.
  - Push notifications on new events, schedule edits, 24-hour and 2-hour pre-event reminders, and opt-in chat alerts.
  - Dead push subscription pruning (HTTP 404/410).
  - iOS (Safari Share > Add to Home Screen) and Android install guides.

---

## 📁 Project Structure (Under 10 Files)

```
Sundarkand_app/
├── server.py              # Single-file Python backend (HTTP + SSE + DB + Push worker)
├── config.json            # Configuration (port, admin_email, session_secret, VAPID keys)
├── app.db                 # SQLite database in WAL mode (created automatically)
├── test_e2e.py            # Automated end-to-end integration test suite
├── README.md              # Documentation & deployment guide
└── static/                # Static assets served directly
    ├── index.html         # Single-page PWA shell with bottom tab bar & modal sheets
    ├── style.css          # Devotional design system, responsive styles, animations
    ├── app.js             # Vanilla JS ES module (Router, Auth, Events, Chat SSE, Push)
    ├── sw.js              # Service worker (Offline caching, Push events, Clicks)
    ├── manifest.webmanifest # PWA configuration
    ├── icon.svg           # Scalable sacred Diya SVG icon
    ├── icon-192.png       # 192x192 PWA icon
    ├── icon-512.png       # 512x512 PWA icon
    └── icon-maskable.png  # 512x512 Android maskable PWA icon
```

---

## 🚀 Local Quickstart

### 1. Requirements
- Python 3.10+
- `pip install pywebpush`

### 2. Generate VAPID Keys
To generate fresh VAPID keys for push notifications and save them automatically into `config.json`:
```bash
python server.py --generate-vapid
```

### 3. Run the Server
```bash
python server.py --port 8000
```
Visit: **`http://localhost:8000`** (or `http://127.0.0.1:8000`).

### 4. First-Time Admin Signup
The email set as `admin_email` in `config.json` (defaults to `admin@sundarkand.org`) is automatically pre-invited on first startup:
1. Open `http://localhost:8000`.
2. Enter `admin@sundarkand.org` and click **Continue**.
3. Set your display name and password to activate your Admin account.

### 5. Run Automated Tests
A comprehensive test script tests all endpoints (auth, invitations, events, RSVP, attendance roles, chat SSE, push endpoints):
```bash
python test_e2e.py
```

---

## 🐙 Push to GitHub

1. Initialize git and commit your files:
```bash
git init
git add .
git commit -m "feat: Sundarkand Seva Team PWA"
git branch -M main
```

2. Create a new repository on [GitHub](https://github.com/new) (keep it Private if you prefer).

3. Link and push to your GitHub repo:
```bash
git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>.git
git push -u origin main
```

*(Note: `.gitignore` is already configured to automatically keep private database records and logs out of your repository).*

---

## ☁️ Deploy to Render (Free Tier Plan)

Render provides free hosting with automatic HTTPS (`https://*.onrender.com`), which is required by iOS and Android for PWA installation and Web Push notifications.

### Option 1: 1-Click Blueprint (Easiest)
1. Go to your [Render Dashboard](https://dashboard.render.com).
2. Click **New +** > **Blueprint**.
3. Connect your GitHub repository.
4. Render will detect `render.yaml` automatically.
5. Click **Apply** to deploy!

### Option 2: Manual Web Service
1. In [Render Dashboard](https://dashboard.render.com), click **New +** > **Web Service**.
2. Select **"Build and deploy from a Git repository"** and select your repository.
3. Configure the settings:
   - **Name**: `sundarkand-seva` (or your choice)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python server.py`
   - **Instance Type**: `Free`
4. Under **Advanced / Environment Variables**, add:
   - `ADMIN_EMAIL`: Your personal email (e.g. `yourname@gmail.com`)
   - `PYTHONUNBUFFERED`: `1`
5. Click **Deploy Web Service**.

### Accessing & Installing the PWA from Render
1. Once deployed, Render gives you a live URL: **`https://your-service-name.onrender.com`**.
2. Open that URL on your phone:
   - **iPhone (Safari)**: Tap Share (`⎋`) > **"Add to Home Screen"**.
   - **Android (Chrome)**: Tap **"Install"** on the banner.
3. On first login, enter your `ADMIN_EMAIL`. You will be prompted to set your password and activate your admin account!

> 💡 **Tip for Render Free Tier (Keep Awake)**:  
> Render's free tier spins down after 15 minutes of inactivity. When a user opens the app, it spins up in ~30 seconds.  
> To keep it responsive 24/7 without delays, set up a free HTTP ping to `https://your-service-name.onrender.com/health` every 10–14 minutes using [cron-job.org](https://cron-job.org) or [UptimeRobot](https://uptimerobot.com).

---

## 🔒 Self-Hosted Deployment (Caddy + Systemd)

### 1. Reverse Proxy with Caddy (Auto HTTPS)

Caddy automatically provisions and renews Let's Encrypt TLS certificates and handles HTTP/2 / Server-Sent Events seamlessly.

Install Caddy on Debian/Ubuntu:
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Edit `/etc/caddy/Caddyfile`:
```caddy
seva.yourdomain.org {
    encode gzip zstd

    # Disable proxy buffering for Server-Sent Events (/api/chat/stream)
    @sse path /api/chat/stream
    handle @sse {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }

    # Standard reverse proxy
    handle {
        reverse_proxy 127.0.0.1:8000 {
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-Proto https
        }
    }
}
```

Reload Caddy:
```bash
sudo systemctl reload caddy
```

---

### 2. Systemd Service (`sundarkand.service`)

Create `/etc/systemd/system/sundarkand.service`:
```ini
[Unit]
Description=Sundarkand Seva Team PWA
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/var/www/sundarkand
ExecStart=/usr/bin/python3 /var/www/sundarkand/server.py --port 8000
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# Security hardening
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable sundarkand
sudo systemctl start sundarkand
sudo systemctl status sundarkand
```

---

### 3. Nightly Hot-Backups for `app.db`

Because SQLite is running in **WAL mode** (`PRAGMA journal_mode=WAL;`), simple file copies of `app.db` could capture inconsistent state. Use SQLite's online **`VACUUM INTO`** or **`.backup`** command to create an atomic, hot snapshot without stopping the server.

#### Create Backup Script `/usr/local/bin/backup-sundarkand.sh`:
```bash
#!/bin/bash
set -euo pipefail

BACKUP_DIR="/var/backups/sundarkand"
DB_PATH="/var/www/sundarkand/app.db"
TIMESTAMP=$(date +"%Y-%m-%d_%H%M%S")
DEST="$BACKUP_DIR/sundarkand_$TIMESTAMP.db"

mkdir -p "$BACKUP_DIR"

# Perform safe hot backup into atomic file
sqlite3 "$DB_PATH" "VACUUM INTO '$DEST';"

# Compress backup
gzip -9 "$DEST"

# Keep last 30 days of backups
find "$BACKUP_DIR" -name "sundarkand_*.db.gz" -mtime +30 -delete

echo "[$TIMESTAMP] Sundarkand DB backup completed: $DEST.gz"
```

Make it executable:
```bash
sudo chmod +x /usr/local/bin/backup-sundarkand.sh
```

#### Add Nightly Cron Job (Runs every night at 2:00 AM):
```bash
sudo crontab -e
```
Add the following line:
```cron
0 2 * * * /usr/local/bin/backup-sundarkand.sh >> /var/log/sundarkand-backup.log 2>&1
```

---

## 📱 PWA Installation & Push Notifications

### iPhone / iPad (iOS 16.4+)
1. Open the website in **Safari**.
2. Tap the **Share** button (box with upward arrow `⎋`) in the browser toolbar.
3. Select **"Add to Home Screen"** and tap **Add**.
4. Open the installed app from your home screen.
5. In **Profile**, click **"Enable Push Notifications"** and allow permissions.
   > **Note:** Apple requires PWAs on iOS to be installed to the Home Screen before the Web Push API (`PushManager`) is accessible.

### Android
1. Open the site in **Chrome**.
2. Tap **"Install"** on the banner, or tap the three dots (`⋮`) > **"Install app"**.
3. Open the app and grant notification permissions.

---

## 👥 Roles & Permissions

| Feature | Member | Admin |
|---|:---:|:---:|
| View Upcoming & Past Events | ✅ | ✅ |
| RSVP (Going / Maybe / Not Going) | ✅ | ✅ |
| View Attendance Counts | ✅ (Counts only) | ✅ (Full roster + Non-responders) |
| Create / Edit / Delete Events | ❌ | ✅ |
| Realtime Chat (SSE) | ✅ | ✅ |
| Delete Chat Messages | ✅ (Own messages) | ✅ (Any message) |
| Invite New Members | ❌ | ✅ |
| Promote / Demote Member Roles | ❌ | ✅ |
| Remove Members | ❌ | ✅ |
| Push Notifications (Events, Reminders, Chat) | ✅ | ✅ |
| Edit Display Name | ✅ | ✅ |

---

## 🕉️ Devotional Greetings & Seva

*श्री राम जय राम जय जय राम*  
*मंगल भवन अमंगल हारी, द्रवहु सुदसरथ अजिर बिहारी।*
