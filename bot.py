"""
BOT CEK SHORTLINK - Entry Point
Bot Telegram untuk mengecek dan memvalidasi shortlink secara otomatis.

Cara pakai:
  1. Set environment variable BOT_TOKEN dengan token dari @BotFather
  2. Jalankan: python bot.py
  3. Kirim URL ke bot di Telegram, contoh:
     /cek https://linktr.ee/contoh
"""

import asyncio
import sys
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from config import BOT_TOKEN, ALLOWED_CHAT_IDS
from pipeline import run_pipeline
from wa_checker import login_wa, wait_for_login, logout_wa, is_logged_in, auto_restore_session

# ── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Track task yang sedang berjalan per chat (untuk /stop)
active_tasks: dict[str, asyncio.Task] = {}
_tasks_lock = asyncio.Lock()

# Cache topic ID "REQUEST CEK"
_request_topic_cache: dict[str, int] = {}

TOPIC_REQUEST_CEK = "REQUEST CEK"


def is_allowed(chat_id: str) -> bool:
    """Cek apakah chat_id diizinkan menggunakan bot."""
    if not ALLOWED_CHAT_IDS:
        return True
    allowed = [cid.strip() for cid in ALLOWED_CHAT_IDS.split(",")]
    return str(chat_id) in allowed


def get_thread_id(update: Update) -> int | None:
    """Ambil thread_id dari pesan (untuk reply di topik yang sama)."""
    if update.message and update.message.message_thread_id:
        return update.message.message_thread_id
    return None


# ── HANDLER: /start ──────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wa_status = "✅ Terhubung" if is_logged_in() else "❌ Belum login"
    await update.message.reply_text(
        "Halo! Saya *Bot Cek Shortlink*.\n\n"
        "Kirim perintah:\n"
        "  /cek <URL> — Cek semua link di halaman\n"
        "  /loginwa — Login WhatsApp Web (scan QR)\n"
        "  /logoutwa — Logout WhatsApp Web\n"
        "  /statuswa — Cek status login WA\n"
        "  /stop — Hentikan proses yang sedang berjalan\n"
        "  /help — Bantuan\n\n"
        f"📱 WhatsApp Web: {wa_status}\n\n"
        "Contoh:\n"
        "`/cek https://linktr.ee/contoh`",
        parse_mode="Markdown"
    )


# ── HANDLER: /help ───────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Cara Pakai Bot Cek Shortlink:*\n\n"
        "1. Kirim `/cek <URL>`\n"
        "2. Bot akan scrape semua link di halaman tersebut\n"
        "3. Setiap link dicek: redirect, status, screenshot, deteksi WA\n"
        "4. Hasil dikirim per link + ringkasan akhir\n\n"
        "*Perintah:*\n"
        "  /cek <URL> — Mulai cek\n"
        "  /loginwa — Login WhatsApp Web\n"
        "  /logoutwa — Logout WhatsApp Web\n"
        "  /statuswa — Cek status login WA\n"
        "  /stop — Batalkan proses\n"
        "  /start — Pesan selamat datang\n"
        "  /help — Bantuan ini",
        parse_mode="Markdown"
    )


# ── HANDLER: /loginwa ────────────────────────────────────
async def cmd_loginwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not is_allowed(chat_id):
        await update.message.reply_text("Maaf, Anda tidak punya akses.")
        return

    if is_logged_in():
        await update.message.reply_text("✅ WhatsApp Web sudah terhubung!")
        return

    await update.message.reply_text("⏳ Membuka WhatsApp Web, tunggu sebentar...")

    result = await login_wa()

    if not result["success"]:
        await update.message.reply_text(
            f"❌ Gagal membuka WhatsApp Web.\nError: `{result['error']}`",
            parse_mode="Markdown"
        )
        return

    if result["already_logged_in"]:
        await update.message.reply_text("✅ WhatsApp Web sudah terhubung! (dari sesi sebelumnya)")
        return

    # Kirim QR code
    qr_path = result["qr_screenshot"]
    if qr_path:
        import os
        try:
            with open(qr_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption="📱 *Scan QR code ini dari WhatsApp di HP kamu:*\n\n"
                            "1. Buka WhatsApp di HP\n"
                            "2. Tap Menu (⋮) → *Linked Devices*\n"
                            "3. Tap *Link a Device*\n"
                            "4. Scan QR code di atas\n\n"
                            "⏳ Menunggu scan... (60 detik)",
                    parse_mode="Markdown"
                )
            os.remove(qr_path)
        except Exception as e:
            await update.message.reply_text(f"❌ Gagal kirim QR: {e}")
            return

    # Tunggu scan
    success = await wait_for_login(timeout=60)

    if success:
        await update.message.reply_text(
            "✅ *WhatsApp Web berhasil terhubung!*\n\n"
            "Sekarang bot bisa cek apakah nomor WA dari shortlink "
            "benar-benar terdaftar dan aktif.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "⏰ *Waktu habis!* QR code tidak di-scan.\n"
            "Kirim /loginwa lagi untuk coba ulang.",
            parse_mode="Markdown"
        )


# ── HANDLER: /logoutwa ──────────────────────────────────
async def cmd_logoutwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not is_allowed(chat_id):
        return

    await logout_wa()
    await update.message.reply_text("✅ WhatsApp Web telah di-logout dan sesi dihapus.")


# ── HANDLER: /statuswa ──────────────────────────────────
async def cmd_statuswa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_logged_in():
        await update.message.reply_text("📱 WhatsApp Web: ✅ *Terhubung*", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "📱 WhatsApp Web: ❌ *Belum login*\n"
            "Kirim /loginwa untuk menghubungkan.",
            parse_mode="Markdown"
        )


# ── HANDLER: /cek <url> ─────────────────────────────────
async def cmd_cek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not is_allowed(chat_id):
        await update.message.reply_text("Maaf, Anda tidak punya akses ke bot ini.")
        return

    # Ambil URL dari argumen
    if not context.args:
        await update.message.reply_text(
            "Kirim URL yang mau dicek.\n"
            "Contoh: `/cek https://linktr.ee/contoh`",
            parse_mode="Markdown"
        )
        return

    source_url = context.args[0]

    # Validasi URL
    if not source_url.startswith(("http://", "https://")):
        await update.message.reply_text(
            "URL harus diawali `http://` atau `https://`",
            parse_mode="Markdown"
        )
        return

    # Validasi domain — harus punya domain yang valid
    from urllib.parse import urlparse
    parsed = urlparse(source_url)
    if not parsed.netloc or "." not in parsed.netloc:
        await update.message.reply_text(
            "URL tidak valid. Contoh: `https://linktr.ee/contoh`",
            parse_mode="Markdown"
        )
        return

    # Cek apakah sudah ada proses berjalan (dengan lock untuk hindari race condition)
    async with _tasks_lock:
        if chat_id in active_tasks and not active_tasks[chat_id].done():
            await update.message.reply_text(
                "Masih ada proses berjalan. Kirim /stop dulu untuk membatalkan."
            )
            return

        # Jalankan pipeline sebagai async task
        bot = context.bot
        thread_id = get_thread_id(update)

        async def run():
            try:
                await run_pipeline(bot, chat_id, source_url, thread_id=thread_id)
            except asyncio.CancelledError:
                pass
            finally:
                async with _tasks_lock:
                    active_tasks.pop(chat_id, None)

        task = asyncio.create_task(run())
        active_tasks[chat_id] = task


# ── HANDLER: /stop ───────────────────────────────────────
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    async with _tasks_lock:
        task = active_tasks.get(chat_id)
        if task and not task.done():
            task.cancel()
            active_tasks.pop(chat_id, None)
            await update.message.reply_text("Proses dibatalkan.")
        else:
            await update.message.reply_text("Tidak ada proses yang sedang berjalan.")


# ── HANDLER: Pesan biasa (URL tanpa command) ─────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.startswith(("http://", "https://")):
        # Treat sebagai /cek
        context.args = [text]
        await cmd_cek(update, context)
    else:
        await update.message.reply_text(
            "Kirim URL atau gunakan /cek <URL>\n"
            "Ketik /help untuk bantuan."
        )


# ── MAIN ─────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("ERROR: Set environment variable BOT_TOKEN terlebih dahulu!")
        print("Contoh: export BOT_TOKEN='123456:ABC-DEF...'")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("loginwa", cmd_loginwa))
    app.add_handler(CommandHandler("logoutwa", cmd_logoutwa))
    app.add_handler(CommandHandler("statuswa", cmd_statuswa))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Coba restore session WA dari sebelumnya
    async def on_startup(app):
        restored = await auto_restore_session()
        if restored:
            logger.info("WhatsApp Web: session restored!")
        else:
            logger.info("WhatsApp Web: belum login, kirim /loginwa")

    app.post_init = on_startup

    logger.info("Bot dimulai! Tekan Ctrl+C untuk berhenti.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
