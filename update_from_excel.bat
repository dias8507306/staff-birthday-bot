@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo Перетащите Excel-файл сотрудников прямо на этот файл.
  pause
  exit /b 1
)
python import_excel.py "%~1" || (pause & exit /b 1)
git add data/birthdays.json
git commit -m "Обновил список сотрудников" >nul && git push -q && echo Готово: GitHub будет напоминать по новому списку.
pause
