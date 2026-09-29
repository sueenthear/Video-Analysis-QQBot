@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -WindowStyle Hidden -Command "$p = Start-Process -FilePath 'uv.exe' -ArgumentList @('run','python','LoginCenter.py') -WorkingDirectory '%~dp0' -WindowStyle Hidden -PassThru; exit 0"

endlocal
exit /b 0
