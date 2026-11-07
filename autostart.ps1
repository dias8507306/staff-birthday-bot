# Регистрирует бота в Планировщике заданий Windows:
# работает в фоне без окна, стартует при входе в Windows,
# а если процесс упал — поднимается снова в течение 5 минут.
$ErrorActionPreference = "Stop"
$TaskName = "EduApp Birthday Bot"
$BotDir = $PSScriptRoot
$Pythonw = & python -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$BotDir\bot.py`"" -WorkingDirectory $BotDir
# Повтор каждые 5 минут — сторож: если бот уже работает, новый запуск пропускается (IgnoreNew).
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $watchdog) `
        -Settings $settings -Principal $principal -Force | Out-Null
} catch {
    # Без прав администратора Windows может не дать триггер «при входе» — тогда хватит сторожа.
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $watchdog `
        -Settings $settings -Principal $principal -Force | Out-Null
}
Start-ScheduledTask -TaskName $TaskName
