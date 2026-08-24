@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel% equ 0 (
    python onekey_deploy.py
) else (
    py -3 onekey_deploy.py
)
pause
