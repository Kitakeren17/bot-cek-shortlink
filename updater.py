"""
🔄 AUTO-UPDATER MODULE
Cek update dari GitHub Releases dan otomatis download + replace.
"""

import os
import sys
import json
import shutil
import zipfile
import logging
import subprocess
import urllib.request
import urllib.error
from version import VERSION
from config import GITHUB_REPO

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def get_base_dir() -> str:
    """Dapatkan base directory (folder tempat exe/script berada)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def parse_version(v: str) -> tuple:
    """Parse version string '1.2.3' jadi tuple (1, 2, 3) untuk perbandingan."""
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except Exception:
        return (0, 0, 0)


def fetch_latest_release() -> dict | None:
    """Ambil info release terbaru dari GitHub API."""
    if not GITHUB_REPO:
        logger.warning("⚠️ GITHUB_REPO belum di-set, skip update check.")
        return None

    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "BotCekShortlink-Updater"
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.info("Belum ada release di GitHub.")
        else:
            logger.warning(f"GitHub API error: {e.code}")
        return None
    except Exception as e:
        logger.warning(f"Gagal cek update: {e}")
        return None


def find_asset(release: dict, name_contains: str = ".zip") -> dict | None:
    """Cari asset download dari release (cari file .zip)."""
    for asset in release.get("assets", []):
        if name_contains in asset.get("name", "").lower():
            return asset
    return None


def download_file(url: str, dest: str, token: str = None):
    """Download file dari URL ke path tujuan."""
    headers = {"User-Agent": "BotCekShortlink-Updater"}
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = int(downloaded / total * 100)
                    if pct % 20 == 0:
                        logger.info(f"  📥 Download: {pct}% ({downloaded // 1024}KB / {total // 1024}KB)")

    logger.info(f"  ✅ Download selesai: {dest}")


def apply_update_and_restart(zip_path: str):
    """
    Extract update zip dan restart aplikasi.
    Karena exe sedang jalan (locked di Windows), pakai batch script
    yang menunggu proses selesai, lalu replace file dan jalankan ulang.
    """
    base_dir = get_base_dir()
    update_dir = os.path.join(base_dir, "_update_temp")
    bat_path = os.path.join(base_dir, "_do_update.bat")

    # Extract zip ke folder temp
    if os.path.exists(update_dir):
        shutil.rmtree(update_dir)
    os.makedirs(update_dir, exist_ok=True)

    logger.info("📦 Mengekstrak update...")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(update_dir)

    # Cari folder hasil extract (mungkin ada subfolder)
    extracted_items = os.listdir(update_dir)
    source_dir = update_dir
    if len(extracted_items) == 1:
        candidate = os.path.join(update_dir, extracted_items[0])
        if os.path.isdir(candidate):
            source_dir = candidate

    # Tentukan exe path
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        exe_path = os.path.join(base_dir, "BotCekShortlink.exe")

    # Buat batch script untuk replace files setelah proses exit
    # Script ini: tunggu proses mati → copy file baru → hapus temp → jalankan ulang
    bat_content = f"""@echo off
echo ================================
echo   Mengupdate Bot Cek Shortlink
echo ================================
echo.
echo Menunggu proses lama selesai...
timeout /t 3 /noq >nul

:: Copy semua file baru (overwrite)
echo Menyalin file update...
xcopy "{source_dir}\\*" "{base_dir}\\" /E /Y /Q >nul 2>&1

:: Hapus file temp
echo Membersihkan...
rmdir /s /q "{update_dir}" >nul 2>&1
del /q "{zip_path}" >nul 2>&1

:: Jalankan ulang
echo.
echo Update selesai! Menjalankan ulang bot...
echo.
start "" "{exe_path}"

:: Hapus script ini sendiri
(goto) 2>nul & del "%~f0"
"""

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    logger.info("🔄 Menjalankan updater, bot akan restart...")

    # Jalankan batch script (detached) lalu exit
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True
    )

    # Exit proses saat ini
    sys.exit(0)


def check_and_update() -> str:
    """
    Cek apakah ada versi baru di GitHub Releases.
    Return: pesan status update.

    Flow:
    1. Cek GitHub API untuk release terbaru
    2. Bandingkan versi
    3. Kalau lebih baru → download zip → extract → restart
    """
    if not GITHUB_REPO:
        return f"v{VERSION} (auto-update tidak aktif — GITHUB_REPO belum di-set)"

    logger.info(f"🔍 Cek update... (versi saat ini: v{VERSION})")

    release = fetch_latest_release()
    if not release:
        return f"v{VERSION} (gagal cek update)"

    latest_tag = release.get("tag_name", "0.0.0")
    latest_ver = parse_version(latest_tag)
    current_ver = parse_version(VERSION)

    if latest_ver <= current_ver:
        logger.info(f"✅ Sudah versi terbaru: v{VERSION}")
        return f"v{VERSION} (terbaru)"

    logger.info(f"🆕 Update tersedia: v{VERSION} → {latest_tag}")

    # Cari file zip di assets
    asset = find_asset(release, ".zip")
    if not asset:
        logger.warning("⚠️ Tidak ada file .zip di release, skip update.")
        return f"v{VERSION} (update {latest_tag} tersedia tapi tidak ada file zip)"

    # Download
    download_url = asset.get("browser_download_url", "")
    if not download_url:
        return f"v{VERSION} (URL download tidak ditemukan)"

    base_dir = get_base_dir()
    zip_path = os.path.join(base_dir, "_update.zip")

    logger.info(f"📥 Mendownload update {latest_tag}...")
    logger.info(f"   Ukuran: {asset.get('size', 0) // 1024 // 1024}MB")

    try:
        download_file(download_url, zip_path)
    except Exception as e:
        logger.error(f"❌ Gagal download update: {e}")
        return f"v{VERSION} (gagal download update)"

    # Apply update dan restart
    try:
        apply_update_and_restart(zip_path)
    except Exception as e:
        logger.error(f"❌ Gagal apply update: {e}")
        # Cleanup
        if os.path.exists(zip_path):
            os.remove(zip_path)
        return f"v{VERSION} (gagal install update: {e})"

    # Tidak akan sampai sini karena apply_update_and_restart() exit
    return f"v{VERSION} → {latest_tag} (updating...)"
