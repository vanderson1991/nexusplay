@echo off
chcp 65001 >nul 2>&1
title GameStore - Servidor
echo ==========================================
echo   GAME STORE - Iniciando servidor...
echo   Feche esta janela para desligar
echo ==========================================
echo.
echo   Site: http://localhost:5000
echo   Admin: http://localhost:5000/login
echo   Senha: admin123
echo.
echo ==========================================
echo.
cd /d "%~dp0"
python app.py
pause
