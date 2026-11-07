@echo off
chcp 65001 >nul
schtasks /change /tn "EduApp Birthday Bot" /disable >nul
schtasks /end /tn "EduApp Birthday Bot" >nul
echo Бот остановлен и не будет запускаться сам. Включить снова: start.bat
pause
