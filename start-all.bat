@echo off
rem Manual start with visible windows (for debugging).
rem Day-to-day you do NOT need this: dsh_remote_web.vbs autostarts both at logon.
rem NOTE: ASCII-only on purpose - these files may be read under a non-UTF-8 codepage.
start "dsh-remote-web proxy" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-proxy.ps1"
timeout /t 2 /nobreak >nul
start "dsh-remote-web tunnel" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-tunnel.ps1"
echo Started proxy and tunnel. See README.md for the phone URL.
