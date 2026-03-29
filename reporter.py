"""
REPORTER MODULE
Mengirim hasil validasi ke Telegram (per link + ringkasan akhir)
"""

import json
import logging
import os
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# Batas karakter pesan Telegram
_TG_MAX_LENGTH = 4096

# File untuk simpan topic ID secara permanen
from config import BASE_DIR
_TOPIC_FILE = os.path.join(BASE_DIR, "topic_ids.json")

# Cache topic ID (di-load dari file saat pertama kali)
_topic_cache: dict[str, int] = {}
_topic_loaded = False


def _load_topic_cache():
    """Load topic ID dari file JSON."""
    global _topic_cache, _topic_loaded
    if _topic_loaded:
        return
    _topic_loaded = True
    if os.path.exists(_TOPIC_FILE):
        try:
            with open(_TOPIC_FILE, "r") as f:
                _topic_cache = json.load(f)
            logger.info(f"Topic cache di-load: {_topic_cache}")
        except Exception as e:
            logger.warning(f"Gagal load topic cache: {e}")


def _save_topic_cache():
    """Simpan topic ID ke file JSON."""
    try:
        with open(_TOPIC_FILE, "w") as f:
            json.dump(_topic_cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Gagal simpan topic cache: {e}")


async def get_or_create_topic(bot: Bot, chat_id: str, topic_name: str) -> int | None:
    """Cari atau buat forum topic di grup. Return message_thread_id.
    Topic ID disimpan ke file supaya tidak buat topik baru setiap restart."""
    _load_topic_cache()

    cache_key = f"{chat_id}:{topic_name}"
    if cache_key in _topic_cache:
        # Verifikasi topik masih valid dengan coba kirim pesan kosong
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=".",
                message_thread_id=_topic_cache[cache_key]
            )
            return _topic_cache[cache_key]
        except TelegramError:
            # Topic mungkin sudah dihapus, hapus dari cache dan buat baru
            logger.warning(f"Topic '{topic_name}' tidak valid lagi, buat baru...")
            del _topic_cache[cache_key]
            _save_topic_cache()

    try:
        result = await bot.create_forum_topic(
            chat_id=chat_id,
            name=topic_name
        )
        thread_id = result.message_thread_id
        _topic_cache[cache_key] = thread_id
        _save_topic_cache()
        logger.info(f"Topik '{topic_name}' dibuat: {thread_id}")
        return thread_id
    except TelegramError as e:
        logger.warning(f"Gagal buat topik '{topic_name}': {e}")
        return None


async def _send_long_message(bot: Bot, chat_id: str, text: str,
                              parse_mode: str = "Markdown", message_thread_id: int = None):
    """Kirim pesan panjang dengan otomatis split jika melebihi batas Telegram."""
    if len(text) <= _TG_MAX_LENGTH:
        await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=parse_mode, message_thread_id=message_thread_id
        )
        return

    # Split per baris, kirim dalam beberapa pesan
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > _TG_MAX_LENGTH - 50:  # sisakan margin
            if chunk:
                await bot.send_message(
                    chat_id=chat_id, text=chunk,
                    parse_mode=parse_mode, message_thread_id=message_thread_id
                )
            chunk = line
        else:
            chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        await bot.send_message(
            chat_id=chat_id, text=chunk,
            parse_mode=parse_mode, message_thread_id=message_thread_id
        )


def format_link_result(result: dict, total: int) -> str:
    """Format hasil validasi satu link jadi teks Telegram."""
    idx = result["index"]
    original = result["original_url"]
    final = result["final_url"]
    status = result["status_code"]
    elapsed = result["elapsed"]
    error = result.get("error")
    wa = result.get("whatsapp", {})

    # Status icon
    if error:
        icon = "❌"
    elif status >= 400:
        icon = "⚠️"
    else:
        icon = "✅"

    lines = [
        f"{icon} *Link {idx}/{total}*",
        f"🔗 Original: `{original}`",
        f"➡️ Final: `{final}`",
        f"📡 Status: `{status}`",
    ]

    # Redirect info
    chain = result.get("redirect_chain", [])
    if len(chain) > 1:
        lines.append(f"🔄 Redirect: *{len(chain) - 1}x*")

    # WhatsApp detection
    if wa.get("detected"):
        wa_num = wa.get("wa_number", "")
        if wa_num:
            lines.append(f"📱 *WhatsApp terdeteksi!* Nomor: `+{wa_num}`")
            # Status aktif/tidak
            wa_status = wa.get("wa_status", "")
            wa_active = wa.get("wa_active")
            if wa_active is True:
                lines.append(f"✅ Status: *{wa_status}*")
                if wa.get("wa_message_sent"):
                    lines.append(f"💬 Pesan \"Auditcek Wa\" *terkirim*")
            elif wa_active is False:
                lines.append(f"❌ Status: *{wa_status}*")
            elif wa_status:
                lines.append(f"❓ Status: *{wa_status}*")
        else:
            lines.append(f"📱 *WhatsApp terdeteksi!*")

    # Error info
    if error:
        lines.append(f"❌ Error: `{error}`")

    lines.append(f"⏱ Waktu: `{elapsed}s`")

    return "\n".join(lines)


async def send_link_result(bot: Bot, chat_id: str, result: dict, total: int, thread_id: int = None):
    """Kirim hasil validasi satu link ke Telegram (teks + screenshot)."""
    text = format_link_result(result, total)

    ss_path = result.get("screenshot_path")
    if ss_path and os.path.exists(ss_path):
        try:
            with open(ss_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id, photo=photo, caption=text,
                    parse_mode="Markdown", message_thread_id=thread_id
                )
            os.remove(ss_path)
        except Exception as e:
            logger.warning(f"Gagal kirim screenshot: {e}")
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", message_thread_id=thread_id)
    else:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", message_thread_id=thread_id)

    # Kirim screenshot WhatsApp kalau ada
    wa = result.get("whatsapp", {})
    wa_ss = wa.get("wa_screenshot")
    if wa_ss and os.path.exists(wa_ss):
        try:
            wa_num = wa.get("wa_number", "")
            wa_active = wa.get("wa_active")
            wa_status = wa.get("wa_status", "")
            msg_sent = wa.get("wa_message_sent")
            status_icon = "✅" if wa_active else ("❌" if wa_active is False else "❓")
            caption = f"{status_icon} *Cek WhatsApp* +{wa_num}\n{wa_status}"
            if msg_sent:
                caption += "\n💬 Pesan \"Auditcek Wa\" terkirim"
            with open(wa_ss, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id, photo=photo, caption=caption,
                    parse_mode="Markdown", message_thread_id=thread_id
                )
            os.remove(wa_ss)
        except Exception as e:
            logger.warning(f"Gagal kirim screenshot WA: {e}")


async def send_wa_check_result(bot: Bot, chat_id: str, wa_result: dict, thread_id: int = None):
    """Kirim hasil cek nomor WA ke Telegram."""
    number = wa_result.get("number", "")
    registered = wa_result.get("registered")
    msg_sent = wa_result.get("message_sent", False)
    status = wa_result.get("status", "")

    if registered:
        icon = "✅"
    elif registered is False:
        icon = "❌"
    else:
        icon = "❓"

    lines = [
        f"📱 *Cek WhatsApp: +{number}*",
        f"{icon} Status: *{status}*",
    ]
    if msg_sent:
        lines.append(f'💬 Pesan "Auditcek Wa" *terkirim*')

    text = "\n".join(lines)

    # Kirim screenshot kalau ada
    ss_path = wa_result.get("screenshot_path")
    if ss_path and os.path.exists(ss_path):
        try:
            with open(ss_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id, photo=photo, caption=text,
                    parse_mode="Markdown", message_thread_id=thread_id
                )
            os.remove(ss_path)
            return
        except Exception as e:
            logger.warning(f"Gagal kirim screenshot WA: {e}")

    await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", message_thread_id=thread_id)


async def send_wa_blok_topic(bot: Bot, chat_id: str, wa_result: dict, source_url: str):
    """Kirim info WA yang terblok/tidak terdaftar ke topik 'WA BLOK' di grup."""
    thread_id = await get_or_create_topic(bot, chat_id, "WA BLOK")
    if not thread_id:
        return

    number = wa_result.get("number", "")
    status = wa_result.get("status", "")

    text = (
        f"🚫 *WA BLOK / TIDAK AKTIF*\n"
        f"{'─' * 25}\n"
        f"📱 Nomor: `+{number}`\n"
        f"❌ Status: *{status}*\n"
        f"🌐 Sumber: `{source_url}`"
    )

    try:
        # Kirim screenshot kalau ada
        ss_path = wa_result.get("screenshot_path")
        if ss_path and os.path.exists(ss_path):
            with open(ss_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=text,
                    parse_mode="Markdown",
                    message_thread_id=thread_id
                )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                message_thread_id=thread_id
            )
    except Exception as e:
        logger.warning(f"Gagal kirim ke topik WA BLOK: {e}")


async def send_summary(bot: Bot, chat_id: str, source_url: str,
                        all_results: list, elapsed: float, thread_id: int = None):
    """Kirim ringkasan akhir ke Telegram."""
    total = len(all_results)
    success = sum(1 for r in all_results if not r.get("error") and r.get("status_code", 0) < 400)
    failed = sum(1 for r in all_results if r.get("error"))
    warning = total - success - failed
    wa_detected = [r for r in all_results if r.get("whatsapp", {}).get("detected")]

    # Format waktu
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

    lines = [
        "📊 *RINGKASAN CEK SHORTLINK*",
        f"{'─' * 30}",
        f"🌐 Sumber: `{source_url}`",
        f"🔗 Total link: *{total}*",
        f"✅ Berhasil: *{success}*",
    ]

    if warning > 0:
        lines.append(f"⚠️ Warning: *{warning}*")
    if failed > 0:
        lines.append(f"❌ Gagal: *{failed}*")

    # WhatsApp summary
    if wa_detected:
        lines.append(f"\n📱 *WhatsApp Terdeteksi: {len(wa_detected)} link*")
        for r in wa_detected:
            wa = r["whatsapp"]
            num = wa.get("wa_number", "")
            num_str = f" (+{num})" if num else ""
            wa_active = wa.get("wa_active")
            active_icon = "✅" if wa_active else ("❌" if wa_active is False else "❓")
            wa_status = wa.get("wa_status", "")
            status_str = f" — {active_icon} {wa_status}" if wa_status else ""
            lines.append(f"  • Link {r['index']}{num_str}{status_str}")

    lines.append(f"\n⏱ Total waktu: *{time_str}*")

    await _send_long_message(
        bot, chat_id, "\n".join(lines),
        parse_mode="Markdown", message_thread_id=thread_id
    )


async def send_rangkuman_perlink(bot: Bot, chat_id: str, source_url: str,
                                  all_results: list, wa_results: list, elapsed: float):
    """Kirim rangkuman lengkap per link ke topik RANGKUMAN PERLINK."""
    topic_thread_id = await get_or_create_topic(bot, chat_id, "RANGKUMAN PERLINK")
    if not topic_thread_id:
        return

    # Format waktu
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

    lines = [
        f"📋 *RANGKUMAN CEK SHORTLINK*",
        f"{'─' * 30}",
        f"🌐 Sumber: `{source_url}`",
        f"⏱ Waktu: *{time_str}*",
        f"📅 Tanggal: `{__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}`",
        "",
    ]

    # ── KATEGORI: LINK BIASA ──
    if all_results:
        lines.append(f"🔗 *LINK TUJUAN ({len(all_results)})*")
        for r in all_results:
            idx = r["index"]
            final = r["final_url"]
            error = r.get("error")

            if error:
                icon = "❌"
            elif r["status_code"] >= 400:
                icon = "⚠️"
            else:
                icon = "✅"

            lines.append(f"  {icon} {idx}. `{final}`")
        lines.append("")

    # ── KATEGORI: WHATSAPP ──
    if wa_results:
        lines.append(f"📱 *WHATSAPP ({len(wa_results)})*")
        for wa in wa_results:
            number = wa.get("number", "")
            registered = wa.get("registered")
            msg_sent = wa.get("message_sent", False)
            status = wa.get("status", "")

            if registered:
                icon = "✅"
            elif registered is False:
                icon = "❌"
            else:
                icon = "❓"

            lines.append(f"  {icon} +{number}")
            lines.append(f"       Status: {status}")
            if msg_sent:
                lines.append(f"       💬 Pesan \"Auditcek Wa\" terkirim")
            lines.append("")

    # ── STATISTIK ──
    total_links = len(all_results)
    success = sum(1 for r in all_results if not r.get("error") and r.get("status_code", 0) < 400)
    failed = sum(1 for r in all_results if r.get("error"))
    wa_aktif = sum(1 for w in wa_results if w.get("registered"))
    wa_blok = sum(1 for w in wa_results if not w.get("registered"))

    lines.append(f"{'─' * 30}")
    lines.append(f"📊 *STATISTIK*")
    lines.append(f"  🔗 Link: {success}✅ {failed}❌ / {total_links} total")
    if wa_results:
        lines.append(f"  📱 WA: {wa_aktif}✅ {wa_blok}❌ / {len(wa_results)} total")

    try:
        await _send_long_message(
            bot, chat_id, "\n".join(lines),
            parse_mode="Markdown", message_thread_id=topic_thread_id
        )
    except Exception as e:
        logger.warning(f"Gagal kirim rangkuman perlink: {e}")
