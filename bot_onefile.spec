# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Playwright driver path
pw_driver = os.path.join(
    os.path.dirname(sys.executable),
    'Lib', 'site-packages', 'playwright', 'driver'
)
# Fallback
if not os.path.exists(pw_driver):
    import playwright
    pw_driver = os.path.join(os.path.dirname(playwright.__file__), 'driver')

a = Analysis(
    ['bot.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('topic_ids.json', '.'),
        (pw_driver, 'playwright/driver'),
    ],
    hiddenimports=[
        'telegram',
        'telegram.ext',
        'playwright',
        'playwright.async_api',
        'playwright._impl',
        'playwright._impl._driver',
        'asyncio',
        'config',
        'version',
        'updater',
        'pipeline',
        'scraper',
        'validator',
        'reporter',
        'wa_checker',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Mode SINGLE FILE — pakai Edge yang sudah ada di Windows
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BotCekShortlink',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)
