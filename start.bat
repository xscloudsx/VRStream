@echo off
title VRStream PRO - Inicializador Turbo
echo ===================================================
echo   VRStream - By LLM Inds
echo   Inicializacao Instantanea (Sem Delay do .exe)
echo ===================================================

:: Verifica se instalou as dependencias basicas caso seja o primeiro uso
echo Checando pacotes essenciais...
pip install -r requirements.txt >nul 2>&1

echo.
echo Iniciando o sistema de Livestream!
start python launcher.py
exit
