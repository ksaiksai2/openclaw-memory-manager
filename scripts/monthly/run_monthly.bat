@echo off
:: 每月记忆整理 - 静默运行包装脚本
:: 由 Windows 计划任务调用，无控制台弹窗
cd /d "%~dp0"
pythonw.exe "%~dp0main.py" 2>nul
