# DSH remote web proxy launcher (supervised).
#
# Restarts the proxy if it exits. Records PIDs so stop-all.ps1 can stop this
# without depending on Get-CimInstance command-line matching (which is denied
# in restricted/sandboxed contexts).
#
# ASCII-only on purpose: these files may be read under a non-UTF-8 codepage.
$here = $PSScriptRoot

# Resolve node: prefer PATH, fall back to the runtime DSH ships.
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) {
  $fallback = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
  if (Test-Path $fallback) { $node = $fallback }
}
if (-not $node) {
  Write-Host '[dsh-remote-web] node.exe not found. Install Node.js or fix the fallback path in run-proxy.ps1'
  exit 1
}

Set-Content -Path (Join-Path $here 'proxy.launcher.pid') -Value $PID -Encoding ascii

while ($true) {
  $proc = Start-Process -FilePath $node -ArgumentList @((Join-Path $here 'dsh-remote-web.mjs')) -PassThru -NoNewWindow
  Set-Content -Path (Join-Path $here 'proxy.pid') -Value $proc.Id -Encoding ascii
  $proc.WaitForExit()
  Start-Sleep -Seconds 5
}
