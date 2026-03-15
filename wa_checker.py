"""
WA CHECKER MODULE
Login ke WhatsApp Web via Playwright dan cek apakah nomor terdaftar.
"""

import asyncio
import logging
import os
import time as _time
from collections import OrderedDict
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

WA_SESSION_DIR = os.path.join(os.path.dirname(__file__), "wa_session")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")

# Singleton state
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None
_playwright = None
_logged_in: bool = False

# Cache hasil cek per nomor (LRU dengan batas ukuran dan TTL)
_MAX_CACHE_SIZE = 500
_CACHE_TTL = 3600  # 1 jam
_checked_numbers: OrderedDict[str, dict] = OrderedDict()


async def _ensure_browser():
    """Pastikan browser terbuka dengan session WA."""
    global _browser, _context, _page, _playwright

    if _context and _page:
        return

    os.makedirs(WA_SESSION_DIR, exist_ok=True)

    _playwright = await async_playwright().start()

    # Pakai Edge kalau ada, fallback ke Chromium bawaan Playwright
    launch_args = {
        "user_data_dir": WA_SESSION_DIR,
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled",
                 "--no-first-run"],
        "viewport": {"width": 1366, "height": 768},
    }

    try:
        launch_args["channel"] = "msedge"
        launch_args["user_agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        )
        _context = await _playwright.chromium.launch_persistent_context(**launch_args)
        logger.info("WhatsApp Web: menggunakan Microsoft Edge")
    except Exception:
        launch_args.pop("channel", None)
        launch_args["user_agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        _context = await _playwright.chromium.launch_persistent_context(**launch_args)
        logger.info("WhatsApp Web: menggunakan Chromium (Edge tidak tersedia)")
    _browser = _context
    _page = _context.pages[0] if _context.pages else await _context.new_page()


def _get_storage_path() -> str:
    return os.path.join(WA_SESSION_DIR, "storage_state.json")


def is_logged_in() -> bool:
    return _logged_in


def get_cached_result(number: str) -> dict | None:
    """Ambil hasil cek dari cache (supaya nomor sama tidak dicek ulang)."""
    entry = _checked_numbers.get(number)
    if entry is None:
        return None
    # Cek TTL — hapus kalau sudah expired
    if _time.time() - entry.get("_cached_at", 0) > _CACHE_TTL:
        _checked_numbers.pop(number, None)
        return None
    # Pindah ke akhir (LRU)
    _checked_numbers.move_to_end(number)
    return entry


def clear_cache():
    """Bersihkan cache hasil cek."""
    _checked_numbers.clear()


def _add_to_cache(number: str, result: dict):
    """Tambah ke cache dengan LRU eviction dan TTL."""
    result["_cached_at"] = _time.time()
    _checked_numbers[number] = result
    _checked_numbers.move_to_end(number)
    # Evict entri terlama kalau melebihi batas
    while len(_checked_numbers) > _MAX_CACHE_SIZE:
        _checked_numbers.popitem(last=False)


async def auto_restore_session():
    """Coba restore session WA dari data sebelumnya saat bot startup."""
    global _logged_in

    if not os.path.exists(WA_SESSION_DIR):
        return False

    try:
        await _ensure_browser()
        await _page.goto("https://web.whatsapp.com", timeout=30000, wait_until="domcontentloaded")
        await _page.wait_for_timeout(8000)

        logged_in = await _check_logged_in()
        if logged_in:
            _logged_in = True
            logger.info("Session WhatsApp Web berhasil di-restore!")
            return True
        else:
            logger.info("Session WA expired, perlu login ulang.")
            return False
    except Exception as e:
        logger.warning(f"Gagal restore session WA: {e}")
        return False


async def login_wa() -> dict:
    """
    Buka WhatsApp Web dan ambil screenshot QR code.
    Return: {
        "success": bool,
        "qr_screenshot": str (path) atau None,
        "already_logged_in": bool,
        "error": str atau None
    }
    """
    global _logged_in

    await _ensure_browser()

    result = {
        "success": False,
        "qr_screenshot": None,
        "already_logged_in": False,
        "error": None
    }

    try:
        await _page.goto("https://web.whatsapp.com", timeout=30000, wait_until="domcontentloaded")
        await _page.wait_for_timeout(5000)

        # Cek apakah sudah login (ada elemen chat list)
        logged_in = await _check_logged_in()
        if logged_in:
            _logged_in = True
            result["success"] = True
            result["already_logged_in"] = True
            # Simpan session
            await _save_session()
            return result

        # Belum login — cari QR code
        # Tunggu QR code muncul
        try:
            await _page.wait_for_selector('canvas, [data-ref], div[data-testid="qrcode"]', timeout=15000)
        except Exception:
            pass

        await _page.wait_for_timeout(2000)

        # Screenshot QR code
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        qr_path = os.path.join(SCREENSHOT_DIR, "wa_qr_code.png")
        await _page.screenshot(path=qr_path, full_page=False)

        result["success"] = True
        result["qr_screenshot"] = qr_path
        return result

    except Exception as e:
        result["error"] = str(e)[:200]
        return result


async def wait_for_login(timeout: int = 60) -> bool:
    """
    Tunggu user scan QR code.
    Return True kalau berhasil login.
    """
    global _logged_in

    if not _page:
        return False

    try:
        # Tunggu sampai elemen chat muncul (tanda sudah login)
        for i in range(timeout // 3):
            logged_in = await _check_logged_in()
            if logged_in:
                _logged_in = True
                await _save_session()
                return True
            await asyncio.sleep(3)

        return False
    except Exception as e:
        logger.error(f"Wait for login error: {e}")
        return False


async def _check_logged_in() -> bool:
    """Cek apakah WhatsApp Web sudah login."""
    try:
        # Cek beberapa selector yang muncul setelah login
        selectors = [
            'div[data-testid="chat-list"]',
            'div[aria-label="Chat list"]',
            'div[aria-label="Daftar chat"]',
            '#pane-side',
            'div[data-testid="default-user"]',
        ]
        for sel in selectors:
            el = await _page.query_selector(sel)
            if el:
                return True

        # Cek dari URL
        if "web.whatsapp.com" in _page.url and "/send" not in _page.url:
            # Cek apakah QR code masih ada
            qr = await _page.query_selector('canvas, div[data-testid="qrcode"]')
            if not qr:
                # Tidak ada QR dan tidak ada error = kemungkinan sudah login
                content = await _page.inner_text("body")
                if len(content) > 100:  # Halaman ada isinya
                    return True

        return False
    except Exception:
        return False


async def _save_session():
    """Session otomatis tersimpan di user_data_dir (persistent context)."""
    logger.info("Session WhatsApp Web disimpan (persistent context)")


async def check_number(number: str) -> dict:
    """
    Cek apakah nomor terdaftar di WhatsApp.
    Return: {
        "number": str,
        "registered": bool,
        "status": str,
        "screenshot_path": str atau None
    }
    """
    global _logged_in

    # Cek cache dulu
    cached = get_cached_result(number)
    if cached:
        logger.info(f"  WA {number}: dari cache — {cached['status']}")
        return cached

    result = {
        "number": number,
        "registered": False,
        "message_sent": False,
        "status": "Tidak diketahui",
        "screenshot_path": None
    }

    if not _logged_in or not _page:
        result["status"] = "WhatsApp Web belum login — kirim /loginwa dulu"
        return result

    invalid_keywords = [
        "phone number shared via url is invalid",
        "nomor telepon yang dibagikan melalui url tidak valid",
    ]

    not_found_keywords = [
        "is not on whatsapp",
        "tidak ada di whatsapp",
        "not on whatsapp",
    ]

    try:
        # Buka chat ke nomor target
        wa_url = f"https://web.whatsapp.com/send?phone={number}"
        await _page.goto(wa_url, timeout=30000, wait_until="domcontentloaded")

        # Tunggu halaman fully loaded — Edge lebih lambat
        await _page.wait_for_timeout(12000)

        # Strategi 1: Coba tunggu #main langsung (paling cepat kalau nomor terdaftar)
        try:
            await _page.wait_for_selector('#main', timeout=15000)
            logger.info(f"  WA +{number}: #main muncul langsung")
        except Exception:
            logger.info(f"  WA +{number}: #main belum muncul, lanjut deteksi...")

        # Coba deteksi berulang (max 6x dengan jeda)
        for attempt in range(6):
            page_text = ""
            page_html = ""
            try:
                page_text = await _page.inner_text("body")
                page_html = await _page.content()
            except Exception:
                pass
            text_lower = page_text.lower()
            html_lower = page_html.lower()

            logger.info(f"  WA +{number}: Attempt {attempt+1}, text length={len(page_text)}, html length={len(page_html)}")

            # Cek invalid / tidak terdaftar
            if any(kw in text_lower for kw in invalid_keywords):
                result["registered"] = False
                result["status"] = "Nomor tidak valid"
                break

            if any(kw in text_lower for kw in not_found_keywords):
                result["registered"] = False
                result["status"] = "Tidak terdaftar di WhatsApp"
                break

            # Cek popup/dialog "phone number shared via url is invalid"
            popup_selectors = [
                'div[data-testid="popup"]',
                'div[role="dialog"]',
                'div[data-animate-modal-popup="true"]',
            ]
            for psel in popup_selectors:
                popup = await _page.query_selector(psel)
                if popup:
                    popup_text = ""
                    try:
                        popup_text = (await popup.inner_text()).lower()
                    except Exception:
                        pass
                    if any(kw in popup_text for kw in invalid_keywords + not_found_keywords):
                        result["registered"] = False
                        result["status"] = "Tidak terdaftar di WhatsApp"
                        break
            if result["status"] != "Tidak diketahui":
                break

            # Cek apakah panel chat (#main) muncul — tanda nomor terdaftar
            main_selectors = [
                '#main',
                'div[data-testid="conversation-panel-wrapper"]',
                'div[data-testid="conversation-panel"]',
                'div[data-testid="msg-input"]',
                'footer div[contenteditable="true"]',
            ]
            main_panel = None
            for msel in main_selectors:
                main_panel = await _page.query_selector(msel)
                if main_panel:
                    logger.info(f"  WA +{number}: Panel ditemukan via {msel}")
                    break

            # Strategi tambahan: cek HTML untuk elemen compose box
            if not main_panel and ('contenteditable="true"' in html_lower and 'compose' in html_lower):
                main_panel = True
                logger.info(f"  WA +{number}: Compose box terdeteksi di HTML")

            if main_panel:
                result["registered"] = True
                result["status"] = "Terdaftar & aktif di WhatsApp"

                # Kirim pesan "Auditcek Wa"
                try:
                    compose_selectors = [
                        '#main footer div[contenteditable="true"]',
                        '#main div[data-testid="conversation-compose-box-input"]',
                        'div[data-testid="conversation-compose-box-input"]',
                        '#main div[role="textbox"]',
                        'footer div[contenteditable="true"]',
                        'div[contenteditable="true"][data-tab="10"]',
                    ]

                    input_clicked = False
                    for sel in compose_selectors:
                        input_box = await _page.query_selector(sel)
                        if input_box:
                            await input_box.click()
                            input_clicked = True
                            logger.info(f"  WA: Input box ditemukan: {sel}")
                            break

                    if not input_clicked:
                        footer = await _page.query_selector('#main footer')
                        if footer:
                            await footer.click()
                            input_clicked = True

                    if input_clicked:
                        await _page.wait_for_timeout(500)
                        await _page.keyboard.type("Auditcek Wa", delay=50)
                        await _page.wait_for_timeout(1000)
                        await _page.keyboard.press("Enter")
                        await _page.wait_for_timeout(3000)

                        result["message_sent"] = True
                        result["status"] = "Terdaftar & aktif — pesan terkirim"
                        logger.info(f"  WA +{number}: Pesan 'Auditcek Wa' terkirim")
                    else:
                        logger.warning(f"  WA +{number}: Compose box tidak ditemukan")
                        result["status"] = "Terdaftar & aktif — gagal kirim pesan"

                except Exception as e:
                    logger.warning(f"  Gagal kirim pesan WA: {e}")
                    result["status"] = "Terdaftar & aktif — gagal kirim pesan"

                break

            # Belum terdeteksi, tunggu lagi
            if attempt < 5:
                logger.info(f"  WA +{number}: Percobaan {attempt + 1}/6, tunggu lagi...")
                await _page.wait_for_timeout(5000)

        # Screenshot bukti
        try:
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            ss_path = os.path.join(SCREENSHOT_DIR, f"wa_check_{number}.png")
            await _page.screenshot(path=ss_path, full_page=False)
            result["screenshot_path"] = ss_path
        except Exception as e:
            logger.warning(f"Screenshot gagal: {e}")

        # Kembali ke halaman utama WA
        try:
            await _page.goto("https://web.whatsapp.com", timeout=10000, wait_until="domcontentloaded")
            await _page.wait_for_timeout(2000)
        except Exception:
            pass

    except Exception as e:
        result["status"] = f"Error: {str(e)[:100]}"

    # Simpan ke cache (LRU dengan TTL)
    _add_to_cache(number, result)
    logger.info(f"  WA +{number}: {result['status']}")
    return result


async def logout_wa():
    """Logout dan tutup browser."""
    global _browser, _context, _page, _playwright, _logged_in

    _logged_in = False
    _checked_numbers.clear()

    try:
        if _context:
            await _context.close()
        if _playwright:
            await _playwright.stop()

        # Hapus session folder
        import shutil
        if os.path.exists(WA_SESSION_DIR):
            shutil.rmtree(WA_SESSION_DIR, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Logout error: {e}")
    finally:
        _browser = None
        _context = None
        _page = None
        _playwright = None
