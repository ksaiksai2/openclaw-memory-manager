@echo off
:: 注册 OpenClaw 记忆整理计划任务
:: 每天 22:30 执行，自动判断 daily/weekly/monthly 顺序执行
:: 以管理员运行可注册 SYSTEM 账户
echo === OpenClaw 记忆整理 - 计划任务注册 ===
echo.

set "SCRIPT_DIR=%~dp0"

:: 自动检测 pythonw.exe（优先 PATH，回退 D:\Scripts）
where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%i in ('where pythonw.exe') do set "PYTHONW=%%i"
) else if exist "D:\Scripts\pythonw.exe" (
    set "PYTHONW=D:\Scripts\pythonw.exe"
) else (
    echo 错误: 找不到 pythonw.exe，请确保 Python 已加入 PATH
    pause
    exit /b 1
)
echo Python: %PYTHONW%

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

:: 注册任务：每天 22:30，超时 2 小时，StartWhenAvailable 补跑错过的任务
echo 注册 OpenClaw-MemoryManager (每天 22:30)...
schtasks /Create /TN "OpenClaw-MemoryManager" /TR "\"%PYTHONW%\" \"%SCRIPT_DIR%run_all.py\"" /SC DAILY /ST 22:30 /F /DU 02:00 %USER_OPT%
if %errorlevel%==0 (echo   OK) else (echo   失败)

echo.
echo === 完成 ===
echo.
echo 执行规则：
echo   每天 22:30 → daily
echo   周日 22:30 → daily → weekly
echo   每月1日 22:30 → daily → weekly（如周日）→ monthly
echo.
echo 注意：如需 "错过补跑" 功能，请在任务计划程序中手动启用：
echo   OpenClaw-MemoryManager → 属性 → 设置 → "如果计划时间已过，尽快启动任务"
echo.
schtasks /Query /TN "OpenClaw-MemoryManager" /FO LIST 2>&1 | findstr "TaskName Status Next"
echo.
pause
