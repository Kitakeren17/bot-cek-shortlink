"""
Konfigurasi Bot Cek Shortlink
Semua setting diambil dari environment variable atau pakai default.
"""

import os

# ── TELEGRAM ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_CHAT_IDS = os.getenv("ALLOWED_CHAT_IDS", "")  # Pisahkan dengan koma, kosong = semua boleh

# ── SCRAPER ──────────────────────────────────────────────
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))
MAX_LINKS_PER_PAGE = int(os.getenv("MAX_LINKS_PER_PAGE", "50"))

# ── PROXY ────────────────────────────────────────────────
USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "")

# ── VALIDATOR ────────────────────────────────────────────
REDIRECT_TIMEOUT = int(os.getenv("REDIRECT_TIMEOUT", "20"))
SCREENSHOT_ENABLED = os.getenv("SCREENSHOT_ENABLED", "true").lower() == "true"

# ── PIPELINE ─────────────────────────────────────────────
DELAY_BETWEEN_CHECKS = float(os.getenv("DELAY_BETWEEN_CHECKS", "2.0"))

# Domain yang di-skip saat validasi (pisahkan dengan koma)
_DEFAULT_SKIP_DOMAINS = (
    "youtube.com,youtu.be,instagram.com,facebook.com,fb.com,fb.me,"
    "microsoft.com,getmicrosoft.com,metacareers.com,meta.com,"
    "twitter.com,x.com,tiktok.com,linkedin.com,t.me"
)
SKIP_DOMAINS = [d.strip() for d in os.getenv("SKIP_DOMAINS", _DEFAULT_SKIP_DOMAINS).split(",") if d.strip()]
