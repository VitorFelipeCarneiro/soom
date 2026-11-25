@echo off
title Desinstalando o Soom
echo Isso remove o Soom deste computador (o modelo de voz baixado continua, para reinstalar rapido).
choice /c SN /m "Continuar"
if errorlevel 2 exit /b
copy /y "%~dp0desinstalar.ps1" "%TEMP%\soom-desinstalar.ps1" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\soom-desinstalar.ps1" %*
echo.
pause
