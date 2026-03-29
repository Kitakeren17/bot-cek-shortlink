"""
VALIDATOR MODULE
Mengikuti redirect shortlink dan mengumpulkan info:
- Final URL setelah redirect
- Status code
- Screenshot halaman tujuan
- Deteksi link WhatsApp
"""

import re
import logging
import os
import time
import uuid
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from config import REDIRECT_TIMEOUT, SCREENSHOT_ENABLED, USE_PROXY, PROXY_URL

logger = logging.getLogger(__name__)

# Pattern untuk deteksi WhatsApp
WA_PATTERNS = [
    r"wa\.me/",
    r"api\.whatsapp\.com/",
    r"chat\.whatsapp\.com/",
    r"whatsapp\.com/",
    r"wa\.link/",
]

from config import BASE_DIR
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")


def detect_whatsapp(url: str, page_content: str = "") -> dict:
    """Deteksi apakah URL atau konten halaman mengandung link WhatsApp."""
    combined = f"{url} {page_content}".lower()

    for pattern in WA_PATTERNS:
        match = re.search(pattern, combined)
        if match:
            # Coba ekstrak nomor WA
            wa_number = ""
            num_match = re.search(r"wa\.me/\+?(\d+)", combined)
            if not num_match:
                num_match = re.search(r"api\.whatsapp\.com/send\?phone=\+?(\d+)", combined)
            if num_match:
                wa_number = num_match.group(1)

            return {
                "detected": True,
                "wa_number": wa_number,
                "wa_url": match.group(0)
            }

    return {"detected": False, "wa_number": "", "wa_url": ""}


async def check_wa_active(wa_number: str) -> dict:
    """
    Cek apakah nomor WhatsApp aktif dengan membuka wa.me/{nomor}.
    Return: {
        "active": bool,
        "status": str,
        "screenshot_path": str or None
    }
    """
    if not wa_number:
        return {"active": False, "status": "Nomor tidak tersedia", "screenshot_path": None}

    wa_url = f"https://wa.me/{wa_number}"
    logger.info(f"  Cek WA aktif: {wa_url}")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True, channel="msedge",
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
        except Exception:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        result = {"active": False, "status": "Tidak diketahui", "screenshot_path": None}

        try:
            await page.goto(wa_url, timeout=15000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            content = await page.content()
            page_text = await page.inner_text("body")
            current_url = page.url

            # Deteksi status berdasarkan konten halaman
            text_lower = page_text.lower()
            content_lower = content.lower()

            if any(kw in text_lower for kw in ["continue to chat", "lanjutkan ke chat", "message", "kirim pesan"]):
                result["active"] = True
                result["status"] = "Aktif - bisa di-chat"
            elif any(kw in text_lower for kw in ["phone number shared via url is invalid", "nomor tidak valid", "invalid"]):
                result["active"] = False
                result["status"] = "Tidak aktif - nomor tidak valid"
            elif "api.whatsapp.com" in current_url or "web.whatsapp.com" in current_url:
                result["active"] = True
                result["status"] = "Aktif - redirect ke WhatsApp"
            elif any(kw in content_lower for kw in ["action-button", "click-to-chat", "send_button"]):
                result["active"] = True
                result["status"] = "Aktif - tombol chat tersedia"
            else:
                result["status"] = "Tidak dapat dipastikan"

            # Screenshot halaman wa.me
            try:
                os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                ss_path = os.path.join(SCREENSHOT_DIR, f"wa_check_{wa_number}.png")
                await page.screenshot(path=ss_path, full_page=False)
                result["screenshot_path"] = ss_path
            except Exception as e:
                logger.warning(f"  Screenshot WA gagal: {e}")

        except PWTimeout:
            result["status"] = "Timeout - tidak bisa mengakses wa.me"
        except Exception as e:
            result["status"] = f"Error: {str(e)[:100]}"
        finally:
            await browser.close()

        logger.info(f"  WA +{wa_number}: {result['status']}")
        return result


async def validate_link(url: str, index: int) -> dict:
    """
    Validasi satu link:
    1. Follow redirect sampai final URL
    2. Ambil screenshot
    3. Deteksi WhatsApp
    """
    logger.info(f"  [{index}] Validasi: {url}")
    start = time.time()

    result = {
        "index": index,
        "original_url": url,
        "final_url": url,
        "status_code": 0,
        "redirect_chain": [],
        "whatsapp": {"detected": False, "wa_number": "", "wa_url": ""},
        "screenshot_path": None,
        "error": None,
        "elapsed": 0,
    }

    proxy_config = None
    if USE_PROXY and PROXY_URL:
        proxy_config = {"server": PROXY_URL}

    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"]
        }
        if proxy_config:
            launch_args["proxy"] = proxy_config

        try:
            launch_args["channel"] = "msedge"
            browser = await p.chromium.launch(**launch_args)
        except Exception:
            launch_args.pop("channel", None)
            browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        # Tangkap redirect chain
        redirect_chain = []

        def on_response(response):
            if response.request.resource_type == "document":
                redirect_chain.append({
                    "url": response.url,
                    "status": response.status
                })

        page.on("response", on_response)

        try:
            response = await page.goto(
                url,
                timeout=REDIRECT_TIMEOUT * 1000,
                wait_until="domcontentloaded"
            )

            # Tunggu sebentar untuk redirect JS
            await page.wait_for_timeout(2000)

            result["final_url"] = page.url
            result["status_code"] = response.status if response else 0
            result["redirect_chain"] = redirect_chain

            # Deteksi WhatsApp di URL final + konten halaman
            page_content = ""
            try:
                page_content = await page.content()
            except Exception:
                pass

            wa_info = detect_whatsapp(page.url, page_content)
            result["whatsapp"] = wa_info

            # Cek apakah nomor WA terdaftar (via WhatsApp Web)
            if wa_info["detected"] and wa_info["wa_number"]:
                from wa_checker import is_logged_in, check_number
                if is_logged_in():
                    wa_check = await check_number(wa_info["wa_number"])
                    result["whatsapp"]["wa_active"] = wa_check["registered"]
                    result["whatsapp"]["wa_status"] = wa_check["status"]
                    result["whatsapp"]["wa_message_sent"] = wa_check.get("message_sent", False)
                    result["whatsapp"]["wa_screenshot"] = wa_check["screenshot_path"]
                else:
                    # Fallback: cek via wa.me
                    wa_check = await check_wa_active(wa_info["wa_number"])
                    result["whatsapp"]["wa_active"] = wa_check["active"]
                    result["whatsapp"]["wa_status"] = wa_check["status"] + " (login WA untuk hasil akurat)"
                    result["whatsapp"]["wa_screenshot"] = wa_check["screenshot_path"]

            # Screenshot
            if SCREENSHOT_ENABLED:
                try:
                    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                    ss_path = os.path.join(SCREENSHOT_DIR, f"link_{index}_{uuid.uuid4().hex[:8]}.png")
                    await page.screenshot(path=ss_path, full_page=False)
                    result["screenshot_path"] = ss_path
                except Exception as e:
                    logger.warning(f"  Screenshot gagal: {e}")

        except PWTimeout:
            result["error"] = "Timeout - halaman terlalu lama dimuat"
            result["final_url"] = page.url or url
        except Exception as e:
            result["error"] = str(e)[:200]
        finally:
            await browser.close()

    result["elapsed"] = round(time.time() - start, 2)
    logger.info(f"  [{index}] Selesai: {result['final_url']} ({result['elapsed']}s)")
    return result
