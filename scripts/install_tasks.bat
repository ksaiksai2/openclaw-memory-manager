@echo off
:: 注册 OpenClaw 记忆整理计划任务
:: 每天 23:30 执行，自动判断 daily/weekly/monthly 顺序执行
:: 以管理员运行可注册 SYSTEM 账户
echo === OpenClaw 记忆整理 - 计划任务注册 ===
echo.

set "SCRIPT_DIR=%~dp0"

:: 自动检测 pythonw.exe：优先同目录 python，回退 PATH
set "PYTHONW="
where pythonw >nul 2>&1 && set "PYTHONW=pythonw"
if not defined PYTHONW (
    where python >nul 2>&1 && set "PYTHONW=python"
)
if not defined PYTHONW (
    echo [错误] 未找到 python/pythonw，请先安装 Python 并加入 PATH
    pause
    exit /b 1
)
echo 使用 Python: %PYTHONW%

:: 检测管理员权限
set "USE_SYSTEM=0"
net session >nul 2>&1
if %errorlevel%==0 set "USE_SYSTEM=1"

if "%USE_SYSTEM%"=="1" (
    echo 管理员模式：SYSTEM 账户
    set "USER_OPT=/RU SYSTEM /RL HIGHEST"
) else (
    echo 普通模式：当前用户
    set "USER_OPT="
)

:: 注册任务：每天 23:30，超时 2 小时，不重复执行
echo 注册 OpenClaw-MemoryManager (每天 23:30)...
schtasks /Create /TN "OpenClaw-MemoryManager" /TR "\"%PYTHONW%\" \"%SCRIPT_DIR%run_all.py\"" /SC DAILY /ST 23:30 /F /DU 02:00 /RI 1440 %USER_OPT%
if %errorlevel%==0 (echo   OK) else (echo   失败)

echo.
echo === 完成 ===
echo.
echo 执行规则：
echo   每天 23:30 → daily
echo   周日 23:30 → daily → weekly
echo   每月1日 23:30 → daily → weekly（如周日）→ monthly
echo.
schtasks /Query /TN "OpenClaw-MemoryManager" /FO LIST 2>&1 | findstr "TaskName Status Next"
echo.
pause