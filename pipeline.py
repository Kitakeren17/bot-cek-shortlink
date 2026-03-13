"""
🔄 PIPELINE MODULE
Orkestrasi seluruh alur: Scrape → Validate → Report ke Telegram
"""

import asyncio
import logging
import time
from telegram import Bot
from telegram.error import ChatMigrated
from scraper import scrape_links
from validator import validate_link
from reporter import send_link_result, send_summary
from config import DELAY_BETWEEN_CHECKS

logger = logging.getLogger(__name__)


async def run_pipeline(bot: Bot, chat_id: str, source_url: str, thread_id: int = None):
    """
    Pipeline lengkap:
    1. Scrape semua link dari URL sumber
    2. Untuk setiap link: validasi + screenshot + detect WA
    3. Kirim hasil ke Telegram per link
    4. Kirim ringkasan akhir
    """
    start_time = time.time()

    # Helper untuk kirim pesan di topik yang benar
    _chat_id = [chat_id]  # Pakai list supaya bisa diubah dari inner function

    async def send_msg(text, parse_mode="Markdown"):
        try:
            await bot.send_message(
                chat_id=_chat_id[0], text=text,
                parse_mode=parse_mode, message_thread_id=thread_id
            )
        except ChatMigrated as e:
            _chat_id[0] = str(e.new_chat_id)
            await bot.send_message(
                chat_id=_chat_id[0], text=text,
                parse_mode=parse_mode, message_thread_id=thread_id
            )

    try:
        # ── LANGKAH 1: SCRAPING ──────────────────────────────────
        await send_msg("🔍 *Langkah 1/3:* Scraping halaman...")

        scrape_result = await scrape_links(source_url)

        if not scrape_result["success"]:
            await send_msg(f"❌ *Gagal scraping!*\nError: `{scrape_result['error']}`")
            return

        links = scrape_result["links"]
        page_title = scrape_result.get("page_title", "-")

        if not links:
            await send_msg(
                f"⚠️ *Tidak ada link ditemukan!*\n"
                f"Halaman: _{page_title}_\n"
                f"URL: `{source_url}`"
            )
            return

        duplicates_removed = scrape_result.get("duplicates_removed", 0)
        dup_info = f"\n🔁 Duplikat dibuang: *{duplicates_removed}*" if duplicates_removed > 0 else ""

        await send_msg(
            f"✅ *Scraping selesai!*\n"
            f"📄 Halaman: _{page_title}_\n"
            f"🔗 Ditemukan *{len(links)}* link unik"
            f"{dup_info}\n\n"
            f"⏳ *Langkah 2/3:* Mulai validasi..."
        )

        # ── LANGKAH 2: PISAHKAN LINK BIASA DAN LINK WA ────────────
        import re
        WA_DOMAINS = ["wa.me", "whatsapp.com", "wa.link"]
        SKIP_DOMAINS = [
            "youtube.com", "youtu.be",
            "instagram.com",
            "facebook.com", "fb.com", "fb.me",
            "microsoft.com", "getmicrosoft.com",
            "metacareers.com", "meta.com",
            "twitter.com", "x.com",
            "tiktok.com",
            "linkedin.com",
            "t.me",
        ]

        normal_links = []
        wa_numbers = set()

        for link_info in links:
            url = link_info["url"].lower()

            # Skip link sosmed & platform besar
            if any(domain in url for domain in SKIP_DOMAINS):
                continue

            is_wa = any(domain in url for domain in WA_DOMAINS)

            if is_wa:
                # Ekstrak nomor WA
                num_match = re.search(r'wa\.me/(\d+)', url)
                if not num_match:
                    num_match = re.search(r'phone=(\d+)', url)
                if num_match:
                    wa_numbers.add(num_match.group(1))
            else:
                normal_links.append(link_info)

        total_links = len(normal_links)
        all_results = []

        # Validasi link biasa
        for i, link_info in enumerate(normal_links, 1):
            url = link_info["url"]

            # Progress update setiap 5 link
            if i == 1 or i % 5 == 0:
                progress_pct = int((i / total_links) * 100) if total_links > 0 else 100
                bar = "█" * (progress_pct // 10) + "░" * (10 - progress_pct // 10)
                await send_msg(f"⏳ Progress: `[{bar}]` {progress_pct}% ({i}/{total_links})")

            # Validasi link
            result = await validate_link(url, i)
            all_results.append(result)

            # Cek apakah final URL redirect ke WhatsApp
            final_url = result.get("final_url", "").lower()
            if any(domain in final_url for domain in WA_DOMAINS):
                num_match = re.search(r'phone=(\d{10,15})', final_url)
                if not num_match:
                    num_match = re.search(r'wa\.me/(\d{10,15})', final_url)
                if num_match and num_match.group(1) not in wa_numbers:
                    wa_numbers.add(num_match.group(1))
                    logger.info(f"📱 Nomor WA ditemukan dari redirect: {num_match.group(1)}")

            # ── LANGKAH 3: KIRIM HASIL KE TELEGRAM ──────────────
            await send_link_result(bot, chat_id, result, total_links, thread_id=thread_id)

            # Jeda antar link
            if i < total_links:
                await asyncio.sleep(DELAY_BETWEEN_CHECKS)

        # ── CEK NOMOR WA TERPISAH ────────────────────────────────
        all_wa_results = []
        if wa_numbers:
            await send_msg(
                f"📱 *Ditemukan {len(wa_numbers)} nomor WhatsApp unik*\n"
                f"Mulai pengecekan..."
            )

            from wa_checker import is_logged_in, check_number
            from reporter import send_wa_check_result

            for wa_num in wa_numbers:
                if is_logged_in():
                    wa_result = await check_number(wa_num)
                else:
                    # Fallback tanpa login
                    from validator import check_wa_active
                    wa_check = await check_wa_active(wa_num)
                    wa_result = {
                        "number": wa_num,
                        "registered": wa_check["active"],
                        "message_sent": False,
                        "status": wa_check["status"] + " (login WA untuk hasil akurat)",
                        "screenshot_path": wa_check["screenshot_path"],
                    }

                await send_wa_check_result(bot, chat_id, wa_result, thread_id=thread_id)

                # Kirim ke topik "WA BLOK" kalau nomor tidak terdaftar
                if not wa_result.get("registered"):
                    from reporter import send_wa_blok_topic
                    await send_wa_blok_topic(bot, chat_id, wa_result, source_url)

                all_wa_results.append(wa_result)
                await asyncio.sleep(1)

        # ── LANGKAH AKHIR: RINGKASAN ─────────────────────────────
        elapsed = time.time() - start_time
        await send_msg("📊 *Langkah 3/3:* Membuat ringkasan...")
        await send_summary(bot, chat_id, source_url, all_results, elapsed, thread_id=thread_id)

        # ── KIRIM KE TOPIK RANGKUMAN PERLINK ────────────────────
        from reporter import send_rangkuman_perlink
        await send_rangkuman_perlink(bot, chat_id, source_url, all_results, all_wa_results, elapsed)

    except asyncio.CancelledError:
        logger.info(f"Pipeline dibatalkan untuk: {source_url}")
        await send_msg("🛑 *Proses dibatalkan oleh pengguna.*")
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        await send_msg(f"💥 *Terjadi error tidak terduga:*\n`{str(e)[:200]}`")
