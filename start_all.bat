@echo off
chcp 65001 >nul
title Character-distill 一键启动

:: ============================================================
:: Character-distill 一键启动脚本
:: 启动顺序：GPT-SoVITS → FunASR(可选) → 前端Build → FastAPI后端
:: 使用方式：双击 start_all.bat 或在项目根目录运行
:: ============================================================

echo.
echo ╔══════════════════════════════════════════════╗
echo ║       Character-distill 一键启动             ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ---- 配置区（按需修改） ----
set "PROJECT_DIR=%~dp0"
set "GPTSOVITS_DIR=E:\Program Files (x86)\GPT-SoVITS-v4-20250422fix\GPT-SoVITS-v4-20250422fix"
set "GPTSOVITS_PORT=9880"
set "BACKEND_PORT=7860"
set "FRONTEND_DEV=false"

:: ---- 1. 启动 GPT-SoVITS（音色克隆服务） ----
echo [1/4] 检查 GPT-SoVITS...
if exist "%GPTSOVITS_DIR%\runtime\python.exe" (
    echo       启动 GPT-SoVITS API ^(端口 %GPTSOVITS_PORT%^)...
    start "GPT-SoVITS" cmd /c "cd /d "%GPTSOVITS_DIR%" && runtime\python.exe api_v2.py -a 127.0.0.1 -p %GPTSOVITS_PORT% 2>&1"
    echo       等待 GPT-SoVITS 加载模型（约30秒）...
    timeout /t 10 /nobreak >nul
) else (
    echo       [跳过] GPT-SoVITS 未安装，语音克隆功能不可用
    echo       路径: %GPTSOVITS_DIR%
)

:: ---- 2. 检查 FunASR（语音识别，需要 Docker） ----
echo.
echo [2/4] 检查 FunASR...
docker ps 2>nul | findstr "funasr" >nul
if %errorlevel% equ 0 (
    echo       FunASR 容器已在运行 ✓
) else (
    echo       [跳过] FunASR 未运行，语音输入功能不可用
    echo       启动方式: docker start [容器ID]
)

:: ---- 3. 构建前端 ----
echo.
echo [3/4] 构建前端...
cd /d "%PROJECT_DIR%web\frontend"

if "%FRONTEND_DEV%"=="true" (
    echo       启动 Vite 开发服务器...
    start "Frontend-Dev" cmd /c "npx vite --port 5173"
) else (
    echo       执行 npm run build...
    call npx vite build >nul 2>&1
    if %errorlevel% equ 0 (
        echo       前端构建成功 ✓
    ) else (
        echo       [警告] 前端构建失败，请检查 node_modules
        echo       尝试: cd web\frontend ^&^& npm install ^&^& npm run build
    )
)

:: ---- 4. 启动 FastAPI 后端 ----
echo.
echo [4/4] 启动 FastAPI 后端...
cd /d "%PROJECT_DIR%"

:: 加载 .env（如果存在）
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "%%a=%%b" 2>nul
    )
    echo       .env 已加载 ✓
)

echo.
echo ══════════════════════════════════════════════
echo   服务地址:
echo   - 主应用:       http://127.0.0.1:%BACKEND_PORT%
echo   - GPT-SoVITS:   http://127.0.0.1:%GPTSOVITS_PORT%/docs
echo   - FunASR:       ws://127.0.0.1:10095
echo ══════════════════════════════════════════════
echo   按 Ctrl+C 停止后端服务
echo ══════════════════════════════════════════════
echo.

python -m uvicorn web.server:app --host 0.0.0.0 --port %BACKEND_PORT% --reload

:: 如果后端退出，暂停让用户看到错误信息
echo.
echo [!] 后端服务已停止
pause
