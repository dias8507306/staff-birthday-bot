@echo off
chcp 65001 >nul
cd /d "%~dp0"
schtasks /query /tn "EduApp Birthday Bot" >nul 2>&1 || powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1"
schtasks /change /tn "EduApp Birthday Bot" /enable >nul
schtasks /run /tn "EduApp Birthday Bot" >nul
echo Бот работает в фоне (без окна) и сам запускается при входе в Windows.
echo Журнал: data\bot.log   Остановить: stop.bat
pause
