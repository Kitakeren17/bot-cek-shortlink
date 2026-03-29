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
import os
import shutil
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
from version import VERSION
from pipeline import run_pipeline
from wa_checker import login_wa, wait_for_login, logout_wa, is_logged_in, auto_restore_session
from updater import check_and_update

# ── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Track task yang sedang berjalan per chat (untuk /stop)
active_tasks: dict[str, asyncio.Task] = {}
_tasks_lock = asyncio.Lock()

# Antrian URL per chat
url_queues: dict[str, asyncio.Queue] = {}
queue_workers: dict[str, asyncio.Task] = {}

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
        f"Halo! Saya *Bot Cek Shortlink* v{VERSION}\n\n"
        "Kirim perintah:\n"
        "  /cek <URL> — Cek link (bisa banyak sekaligus)\n"
        "  /loginwa — Login WhatsApp Web (scan QR)\n"
        "  /logoutwa — Logout WhatsApp Web\n"
        "  /statuswa — Cek status login WA\n"
        "  /update — Cek & install update terbaru\n"
        "  /stop — Hentikan proses & antrian\n"
        "  /help — Bantuan\n\n"
        f"📱 WhatsApp Web: {wa_status}\n\n"
        "Contoh satu URL:\n"
        "`/cek https://linktr.ee/contoh`\n\n"
        "Contoh banyak URL:\n"
        "/cek\n"
        "`https://linktr.ee/contoh1`\n"
        "`https://linktr.ee/contoh2`\n"
        "`https://linktr.ee/contoh3`",
        parse_mode="Markdown"
    )


# ── HANDLER: /help ───────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Cara Pakai Bot Cek Shortlink:*\n\n"
        "1. Kirim `/cek <URL>` (satu atau banyak)\n"
        "2. Bot akan scrape semua link di halaman\n"
        "3. Setiap link dicek: redirect, status, screenshot, deteksi WA\n"
        "4. Hasil dikirim per link + ringkasan akhir\n\n"
        "*Banyak URL sekaligus:*\n"
        "Kirim beberapa URL, bot akan proses satu per satu:\n"
        "/cek\n"
        "`https://linktr.ee/contoh1`\n"
        "`https://linktr.ee/contoh2`\n\n"
        "*Perintah:*\n"
        "  /cek <URL> — Mulai cek (bisa multi-URL)\n"
        "  /loginwa — Login WhatsApp Web\n"
        "  /logoutwa — Logout WhatsApp Web\n"
        "  /statuswa — Cek status login WA\n"
        "  /stop — Batalkan proses & antrian\n"
        "  /start — Pesan selamat datang\n"
        "  /help — Bantuan ini",
        parse_mode="Markdown"
    )


# ── HANDLER: /update ─────────────────────────────────────
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not is_allowed(chat_id):
        return

    await update.message.reply_text(
        f"🔍 Versi saat ini: *v{VERSION}*\nMengecek update...",
        parse_mode="Markdown"
    )

    # Jalankan di thread terpisah supaya tidak block bot
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, check_and_update)

    # Kalau sampai sini berarti tidak ada update (atau gagal)
    await update.message.reply_text(f"📦 Status: *{result}*", parse_mode="Markdown")


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


# ── HELPER: Validasi URL ──────────────────────────────────
def parse_urls(text: str) -> list[str]:
    """Ambil semua URL valid dari teks (bisa multi-line)."""
    from urllib.parse import urlparse
    urls = []
    for word in text.split():
        word = word.strip()
        if not word.startswith(("http://", "https://")):
            continue
        parsed = urlparse(word)
        if parsed.netloc and "." in parsed.netloc:
            urls.append(word)
    return urls


# ── QUEUE WORKER ──────────────────────────────────────────
async def queue_worker(bot, chat_id: str, thread_id: int | None):
    """Worker yang proses antrian URL satu per satu."""
    queue = url_queues.get(chat_id)
    if not queue:
        return

    while True:
        try:
            url, position, total = await asyncio.wait_for(queue.get(), timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            break

        try:
            if total > 1:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📋 *Antrian {position}/{total}:* `{url}`",
                    parse_mode="Markdown",
                    message_thread_id=thread_id
                )

            async with _tasks_lock:
                task = asyncio.current_task()
                active_tasks[chat_id] = task

            await run_pipeline(bot, chat_id, url, thread_id=thread_id)

        except asyncio.CancelledError:
            await bot.send_message(
                chat_id=chat_id,
                text="🛑 *Antrian dibatalkan.*",
                parse_mode="Markdown",
                message_thread_id=thread_id
            )
            # Kosongkan sisa antrian
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            break
        except Exception as e:
            logger.error(f"Queue worker error: {e}", exc_info=True)
        finally:
            queue.task_done()

    # Cleanup
    async with _tasks_lock:
        active_tasks.pop(chat_id, None)
    queue_workers.pop(chat_id, None)
    url_queues.pop(chat_id, None)


# ── HANDLER: /cek <url> ─────────────────────────────────
async def cmd_cek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not is_allowed(chat_id):
        await update.message.reply_text("Maaf, Anda tidak punya akses ke bot ini.")
        return

    # Ambil semua URL dari pesan (support multi-URL)
    full_text = update.message.text or ""
    # Hapus /cek dari awal
    if full_text.startswith("/cek"):
        full_text = full_text[4:]

    urls = parse_urls(full_text)

    if not urls:
        await update.message.reply_text(
            "Kirim URL yang mau dicek.\n\n"
            "*Satu URL:*\n"
            "`/cek https://linktr.ee/contoh`\n\n"
            "*Banyak URL sekaligus:*\n"
            "/cek\n"
            "`https://linktr.ee/contoh1`\n"
            "`https://linktr.ee/contoh2`\n"
            "`https://linktr.ee/contoh3`",
            parse_mode="Markdown"
        )
        return

    bot = context.bot
    thread_id = get_thread_id(update)
    total = len(urls)

    # Cek apakah sudah ada worker jalan
    async with _tasks_lock:
        existing_worker = queue_workers.get(chat_id)
        if existing_worker and not existing_worker.done():
            # Tambahkan ke antrian yang sudah ada
            queue = url_queues[chat_id]
            old_size = queue.qsize()
            for i, url in enumerate(urls, old_size + 1):
                await queue.put((url, i, old_size + total))
            await update.message.reply_text(
                f"➕ *{total} URL ditambahkan ke antrian!*\n"
                f"Total antrian sekarang: *{queue.qsize()}*",
                parse_mode="Markdown"
            )
            return

    # Buat antrian baru
    queue = asyncio.Queue()
    url_queues[chat_id] = queue

    for i, url in enumerate(urls, 1):
        await queue.put((url, i, total))

    if total > 1:
        url_list = "\n".join(f"  {i}. `{u}`" for i, u in enumerate(urls, 1))
        await update.message.reply_text(
            f"📋 *{total} URL masuk antrian:*\n{url_list}\n\n"
            f"Proses dimulai...",
            parse_mode="Markdown"
        )

    # Jalankan worker
    worker = asyncio.create_task(queue_worker(bot, chat_id, thread_id))
    queue_workers[chat_id] = worker


# ── HANDLER: /stop ───────────────────────────────────────
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    stopped = False

    # Stop queue worker
    worker = queue_workers.get(chat_id)
    if worker and not worker.done():
        worker.cancel()
        queue_workers.pop(chat_id, None)
        url_queues.pop(chat_id, None)
        stopped = True

    # Stop active task
    async with _tasks_lock:
        task = active_tasks.get(chat_id)
        if task and not task.done():
            task.cancel()
            active_tasks.pop(chat_id, None)
            stopped = True

    if stopped:
        await update.message.reply_text("🛑 Proses dan antrian dibatalkan.")
    else:
        await update.message.reply_text("Tidak ada proses yang sedang berjalan.")


# ── HANDLER: Pesan biasa (URL tanpa command) ─────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    urls = parse_urls(text)
    if urls:
        # Treat sebagai /cek
        await cmd_cek(update, context)
    else:
        await update.message.reply_text(
            "Kirim URL atau gunakan /cek <URL>\n"
            "Ketik /help untuk bantuan."
        )


# ── MAIN ─────────────────────────────────────────────────
def check_edge():
    """Cek apakah Microsoft Edge tersedia."""
    edge_path = shutil.which("msedge") or shutil.which("microsoft-edge")
    if edge_path:
        return True
    # Cek lokasi default Windows
    default_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    return any(os.path.exists(p) for p in default_paths)


def main():
    if not BOT_TOKEN:
        print("ERROR: Set environment variable BOT_TOKEN terlebih dahulu!")
        print("Isi token bot di file token.txt")
        sys.exit(1)

    if not check_edge():
        print("=" * 50)
        print("  ERROR: Microsoft Edge tidak ditemukan!")
        print("=" * 50)
        print()
        print("Bot ini membutuhkan Microsoft Edge untuk berjalan.")
        print("Download di: https://www.microsoft.com/edge")
        print()
        input("Tekan Enter untuk keluar...")
        sys.exit(1)

    print("Microsoft Edge: OK")

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("loginwa", cmd_loginwa))
    app.add_handler(CommandHandler("logoutwa", cmd_logoutwa))
    app.add_handler(CommandHandler("statuswa", cmd_statuswa))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Cek update saat startup
    update_status = check_and_update()
    logger.info(f"📦 Versi: {update_status}")

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
