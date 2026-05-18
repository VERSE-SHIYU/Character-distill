@echo off
chcp 65001 >nul
title Character-distill 停止所有服务

echo.
echo 正在停止所有服务...
echo.

:: 停止 uvicorn
taskkill /f /im "python.exe" /fi "WINDOWTITLE eq Character-distill*" 2>nul
taskkill /f /im "uvicorn.exe" 2>nul

:: 停止 GPT-SoVITS
taskkill /f /fi "WINDOWTITLE eq GPT-SoVITS*" 2>nul

:: 停止 Vite dev server
taskkill /f /fi "WINDOWTITLE eq Frontend-Dev*" 2>nul

echo.
echo 所有服务已停止 ✓
timeout /t 3
