"""
🔍 SCRAPER MODULE
Mengunjungi URL utama dan mengumpulkan semua link aktif di halaman
"""

import re
import logging
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from config import TIMEOUT_SECONDS, USE_PROXY, PROXY_URL, MAX_LINKS_PER_PAGE

logger = logging.getLogger(__name__)

# Link yang diabaikan (bukan destination link)
IGNORED_EXTENSIONS = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".ttf")
IGNORED_SCHEMES = ("mailto:", "tel:", "javascript:", "#")
IGNORED_DOMAINS = ("google.com/fonts", "googleapis.com", "gstatic.com", "facebook.net")

# Parameter tracking yang diabaikan saat deduplikasi
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "s", "from"
}


def normalize_url(url: str) -> str:
    """
    Normalisasi URL untuk deduplikasi yang akurat.
    Menangani kasus:
    - Trailing slash: https://site.com/ == https://site.com
    - Case scheme/netloc: HTTP://Site.COM == https://site.com
    - Fragment (#section): dihapus
    - Tracking params (utm_*, fbclid, dll): dihapus
    """
    try:
        parsed = urlparse(url.strip())

        # Lowercase scheme dan netloc
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Hapus trailing slash dari path (kecuali path hanya "/")
        path = parsed.path.rstrip("/") or "/"

        # Hapus fragment (#...)
        fragment = ""

        # Hapus tracking params, urutkan sisanya agar konsisten
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            filtered = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
            query = urlencode(sorted(filtered.items()), doseq=True)
        else:
            query = ""

        return urlunparse((scheme, netloc, path, parsed.params, query, fragment))
    except Exception:
        return url.strip().lower()


def is_valid_destination_link(href: str, base_url: str) -> bool:
    """Filter apakah link layak untuk dicek."""
    if not href:
        return False
    for scheme in IGNORED_SCHEMES:
        if href.startswith(scheme):
            return False
    for ext in IGNORED_EXTENSIONS:
        if href.lower().endswith(ext):
            return False
    for domain in IGNORED_DOMAINS:
        if domain in href:
            return False
    # Harus berupa URL valid
    try:
        parsed = urlparse(href if href.startswith("http") else urljoin(base_url, href))
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


async def scrape_links(url: str) -> dict:
    """
    Kunjungi URL utama dan kumpulkan semua link destination.
    Return: {
        "success": bool,
        "links": [...],
        "page_title": str,
        "error": str or None
    }
    """
    logger.info(f"🔍 Scraping: {url}")
    
    proxy_config = None
    if USE_PROXY and PROXY_URL:
        proxy_config = {"server": PROXY_URL}
        logger.info(f"🌐 Menggunakan proxy: {PROXY_URL.split('@')[-1]}")  # Log tanpa credential

    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
            viewport={"width": 1366, "height": 768},
            locale="id-ID",
            timezone_id="Asia/Jakarta"
        )
        page = await context.new_page()

        try:
            await page.goto(url, timeout=TIMEOUT_SECONDS * 1000, wait_until="networkidle")
        except PWTimeout:
            try:
                await page.goto(url, timeout=TIMEOUT_SECONDS * 1000, wait_until="domcontentloaded")
            except Exception as e:
                await browser.close()
                return {"success": False, "links": [], "page_title": "", "error": str(e)}
        except Exception as e:
            await browser.close()
            return {"success": False, "links": [], "page_title": "", "error": str(e)}

        # Scroll ke bawah untuk load lazy content
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        page_title = await page.title()

        # Ambil link utama dari konten shortlink (skip header/footer/nav)
        raw_links = await page.evaluate("""
            () => {
                // Skip link di dalam nav, header, footer
                const skipParents = ['nav', 'header', 'footer'];

                function isInSkipArea(el) {
                    let parent = el.parentElement;
                    while (parent) {
                        const tag = parent.tagName.toLowerCase();
                        if (skipParents.includes(tag)) return true;
                        const role = parent.getAttribute('role');
                        if (role === 'navigation' || role === 'banner' || role === 'contentinfo') return true;
                        parent = parent.parentElement;
                    }
                    return false;
                }

                const anchors = document.querySelectorAll('a[href]');
                return Array.from(anchors)
                    .filter(a => {
                        // Harus visible
                        if (a.offsetParent === null && a.offsetWidth === 0) return false;
                        // Skip link di area navigasi
                        if (isInSkipArea(a)) return false;
                        // Skip link tanpa teks (icon-only links kecil)
                        const text = a.textContent.trim();
                        if (text.length === 0 && !a.querySelector('img')) return false;
                        return true;
                    })
                    .map(a => ({
                        href: a.href,
                        text: a.textContent.trim().substring(0, 100),
                        visible: true
                    }));
            }
        """)

        # Scan konten halaman untuk nomor WA tersembunyi (tombol floating, JS, dll)
        page_content = ""
        try:
            page_content = await page.content()
        except Exception:
            pass

        # Cari pola wa.me/NOMOR atau phone=NOMOR di seluruh HTML
        import re
        wa_patterns_in_page = re.findall(r'wa\.me/\+?(\d{7,15})', page_content)
        wa_patterns_in_page += re.findall(r'phone=\+?(\d{7,15})', page_content)
        wa_patterns_in_page += re.findall(r'whatsapp\.com/send\?.*?phone=\+?(\d{7,15})', page_content)

        # Tambahkan ke raw_links kalau belum ada
        existing_hrefs = {item.get("href", "") for item in raw_links}
        for wa_num in set(wa_patterns_in_page):
            wa_url = f"https://wa.me/{wa_num}"
            if wa_url not in existing_hrefs:
                raw_links.append({
                    "href": wa_url,
                    "text": f"WA {wa_num}",
                    "visible": True
                })
                logger.info(f"📱 Nomor WA ditemukan di HTML: {wa_num}")

        await browser.close()

    # Filter dan deduplikasi dengan normalisasi URL
    seen_normalized = set()   # Untuk cek duplikat (pakai URL ternormalisasi)
    links = []
    duplicates_removed = 0

    for item in raw_links:
        href = item.get("href", "").strip()

        # Abaikan link ke halaman yang sama (domain sama)
        if urlparse(href).netloc == urlparse(url).netloc:
            continue

        if not is_valid_destination_link(href, url):
            continue

        # Normalisasi untuk deduplikasi
        norm = normalize_url(href)

        if norm in seen_normalized:
            duplicates_removed += 1
            logger.debug(f"  🔁 Duplikat dibuang: {href}")
            continue

        seen_normalized.add(norm)
        links.append({
            "url": href,
            "anchor_text": item.get("text", ""),
            "visible": item.get("visible", True)
        })

        if len(links) >= MAX_LINKS_PER_PAGE:
            break

    if duplicates_removed > 0:
        logger.info(f"🔁 {duplicates_removed} link duplikat dibuang")
    logger.info(f"✅ Ditemukan {len(links)} link unik di halaman")
    return {
        "success": True,
        "links": links,
        "page_title": page_title,
        "duplicates_removed": duplicates_removed,
        "error": None
    }
