@echo off
rem Stop the DSH remote web proxy and its reverse tunnel.
rem Real logic lives in stop-all.ps1 (batch quoting + self-match are traps).
rem NOTE: ASCII-only on purpose - see tunnel_keepalive.bat.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-all.ps1"
