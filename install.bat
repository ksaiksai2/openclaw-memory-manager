@echo off
chcp 936 >nul
setlocal EnableDelayedExpansion
title OMM 一键安装

echo ==============================================
echo   OpenClaw Memory Manager - 一键安装
echo ==============================================
echo.

set "SRC=%~dp0"
set "DST=%USERPROFILE%\.openclaw\openclaw-memory-manager"

:: ── 1. 检查 OpenClaw ──
if not exist "%USERPROFILE%\.openclaw" (
    echo [错误] 未找到 ~\.openclaw 目录，请先安装 OpenClaw 再运行本脚本
    echo.
    pause
    exit /b 1
)

:: ── 2. 已安装则备份 ──
if exist "%DST%\console\main.js" (
    echo [1/4] 检测到已安装版本，备份现有文件...
    set "BK=%DST%\backup-%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%"
    if exist "!BK!" rd /s /q "!BK!"
    mkdir "!BK!" >nul 2>&1
    xcopy "%DST%\scripts" "!BK!\scripts" /E /I /Q /Y >nul 2>&1
    xcopy "%DST%\console" "!BK!\console" /E /I /Q /Y >nul 2>&1
    echo       备份到: !BK!
) else (
    echo [1/4] 全新安装...
)

:: ── 3. 复制主体（排除 config.json，保留已有密钥）──
echo [2/4] 复制程序文件...
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%scripts" "%DST%\scripts" /E /XF config.json /XD __pycache__ /NFL /NDL /NJH /NJS /R:1 /W:1 >nul
if %errorlevel% GEQ 8 echo       [警告] scripts 复制失败
robocopy "%SRC%console" "%DST%\console" /E /XF config.json /XD node_modules __pycache__ _asar_extract /NFL /NDL /NJH /NJS /R:1 /W:1 >nul
if %errorlevel% GEQ 8 echo       [警告] console 复制失败
copy /Y "%SRC%README.md" "%DST%\README.md" >nuldel "%EXC%" >nul 2>&1

:: ── 4. 桌面快捷方式 ──
echo [3/4] 创建桌面「OMM控制台」快捷方式...
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $lnk=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\OMM控制台.lnk'); $lnk.TargetPath='wscript.exe'; $lnk.Arguments='\"%DST%\console\start.vbs\"'; $lnk.WorkingDirectory='%DST%\console'; $lnk.IconLocation='%DST%\console\assets\icon.ico'; $lnk.Description='OpenClaw Memory Manager 控制台'; $lnk.Save()"
if exist "%USERPROFILE%\Desktop\OMM控制台.lnk" (echo       快捷方式已创建) else (echo       [警告] 快捷方式创建失败，可手动打开 %DST%\console\start.vbs)

:: ── 5. 控制台依赖（Node.js）──
echo [4/4] 检查控制台依赖...
if not exist "%DST%\console\node_modules" (
    where node >nul 2>&1
    if !errorlevel!==0 (
        pushd "%DST%\console"
        call npm install --no-audit --no-fund
        popd
    ) else (
        echo       [警告] 未找到 Node.js，控制台暂不可用（安装 Node 后运行 %DST%\console\start.bat 即可）
    )
) else (
    echo       依赖已存在
)

:: ── 6. 可选：定时任务 ──
echo.
set /p REGTASK=是否注册每日自动整理任务（每天 23:30）？[Y/N，默认 N]:
if /i "!REGTASK!"=="Y" (
    set "PY=pythonw"
    where pythonw >nul 2>&1 || set "PY=python"
    schtasks /Create /TN "OpenClaw-MemoryManager" /TR "\"%PY%\" \"%DST%\scripts\run_all.py\"" /SC DAILY /ST 23:30 /F /DU 02:00 >nul
    if !errorlevel!==0 (echo       定时任务已注册：每天 23:30) else (echo       [警告] 定时任务注册失败，可稍后手动运行 scripts\install_tasks.bat)
)

:: ── 完成 ──
echo.
echo ==============================================
echo   安装完成！
echo.
echo   下一步：打开桌面「OMM控制台」
echo         选择服务商 → 填 API Key → 保存
echo         即可开始自动整理记忆
echo ==============================================
echo.
pause