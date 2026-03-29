@echo off
title Build Bot Cek Shortlink
echo ========================================
echo   Build Bot Cek Shortlink (Portable)
echo ========================================
echo.

:: 1. Build dengan PyInstaller (mode folder)
echo [1/3] Building exe dengan PyInstaller...
pyinstaller bot.spec --clean --noconfirm
if errorlevel 1 (
    echo GAGAL build PyInstaller!
    pause
    exit /b 1
)
echo      OK!
echo.

:: 2. Copy browser Chromium ke folder dist
echo [2/3] Menyalin browser Chromium...
set "BROWSERS_SRC=%LOCALAPPDATA%\ms-playwright"
set "BROWSERS_DST=dist\BotCekShortlink\browsers"

if not exist "%BROWSERS_SRC%\chromium-*" (
    echo Browser Chromium belum terinstall!
    echo Jalankan: playwright install chromium
    pause
    exit /b 1
)

:: Salin folder chromium
if exist "%BROWSERS_DST%" rmdir /s /q "%BROWSERS_DST%"
mkdir "%BROWSERS_DST%"

for /d %%D in ("%BROWSERS_SRC%\chromium-*") do (
    echo      Copying %%~nxD ...
    xcopy "%%D" "%BROWSERS_DST%\%%~nxD\" /E /I /Q /Y >nul
)
echo      OK!
echo.

:: 3. Buat RUN BOT.bat di folder dist
echo [3/3] Membuat RUN BOT.bat ...
(
echo @echo off
echo title Bot Cek Shortlink
echo echo ================================
echo echo   Bot Cek Shortlink
echo echo ================================
echo echo.
echo.
echo :: Cek apakah token sudah di-set di token.txt
echo if exist "%%~dp0token.txt" ^(
echo     set /p BOT_TOKEN=^<"%%~dp0token.txt"
echo ^) else ^(
echo     echo File token.txt tidak ditemukan!
echo     echo Buat file token.txt di folder ini, isi dengan token bot dari @BotFather
echo     echo Contoh isi: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
echo     pause
echo     exit /b 1
echo ^)
echo.
echo set "PLAYWRIGHT_BROWSERS_PATH=%%~dp0browsers"
echo.
echo :: GitHub repo untuk auto-update (format: username/repo-name)
echo if exist "%%~dp0github_repo.txt" ^(
echo     set /p GITHUB_REPO=^<"%%~dp0github_repo.txt"
echo ^)
echo.
echo echo Token: %%BOT_TOKEN:~0,10%%...
echo echo Browser: %%PLAYWRIGHT_BROWSERS_PATH%%
echo echo.
echo echo Memulai bot...
echo echo.
echo "%%~dp0BotCekShortlink\BotCekShortlink.exe"
echo pause
) > "dist\RUN BOT.bat"

:: Buat token.txt contoh
if not exist "dist\token.txt" (
    echo MASUKKAN_TOKEN_BOT_DISINI> "dist\token.txt"
)

:: Buat github_repo.txt contoh
if not exist "dist\github_repo.txt" (
    echo MASUKKAN_USERNAME/NAMA_REPO> "dist\github_repo.txt"
)

echo      OK!
echo.
echo ========================================
echo   BUILD SELESAI!
echo ========================================
echo.
echo Hasil ada di folder: dist\
echo.
echo Untuk distribusi, kirim SELURUH isi folder dist\:
echo   - BotCekShortlink\  (folder exe + library)
echo   - browsers\          (folder browser Chromium)
echo   - RUN BOT.bat        (launcher)
echo   - token.txt          (isi dengan token bot)
echo   - github_repo.txt    (isi dengan username/repo untuk auto-update)
echo.
echo Teman kamu tinggal:
echo   1. Isi token.txt dengan token dari @BotFather
echo   2. Isi github_repo.txt dengan username/nama-repo GitHub
echo   3. Double-click RUN BOT.bat
echo.
echo AUTO-UPDATE: Upload file .zip build ke GitHub Releases
echo   dengan tag versi (contoh: v1.1.0). Bot akan otomatis
echo   download dan update saat startup atau /update.
echo.
pause
