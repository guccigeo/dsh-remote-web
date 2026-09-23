' Logon autostart for DSH remote web control: proxy first, then the reverse tunnel.
' Install: copy this file into shell:startup  (Win+R -> shell:startup)
' Remove : delete it from that folder (and run stop-all.bat to kill live processes).
' ASCII-only on purpose: wscript reads .vbs as ANSI, so UTF-8 Chinese would mangle.
Dim shell, here
Set shell = CreateObject("WScript.Shell")
here = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.dsh\remote-web"
shell.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\run-proxy.ps1""", 0, False
WScript.Sleep 2500
shell.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\run-tunnel.ps1""", 0, False
