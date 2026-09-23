# DSH remote web reverse-tunnel launcher (supervised).
#
# local 127.0.0.1:<localPort>  ->  your server 0.0.0.0:<remotePort>
# Reconnects forever: 5 fast retries at 5s, then 60s backoff. Records PIDs so
# stop-all.ps1 does not need Get-CimInstance command-line matching.
#
# Tunnel parameters come from remote.config.json (see remote.config.example.json):
#   tunnel.host / tunnel.remotePort / tunnel.localPort / tunnel.identityFile
# host/remotePort fall back to values derived from publicBaseUrl.
#
# ASCII-only on purpose: these files may be read under a non-UTF-8 codepage.
$here = $PSScriptRoot
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'

# ---- read config ------------------------------------------------------------
$cfgPath = Join-Path $here 'remote.config.json'
if (-not (Test-Path $cfgPath)) {
  Write-Host "[dsh-remote-web] missing $cfgPath (copy remote.config.example.json first)"
  exit 1
}
$cfg = Get-Content $cfgPath -Raw -Encoding utf8 | ConvertFrom-Json

$publicBaseUrl = [string]$cfg.publicBaseUrl   # e.g. http://<your-server>:17933
$publicUri = $null
if ($publicBaseUrl) { $publicUri = [Uri]$publicBaseUrl }

$hostTarget = [string]$cfg.tunnel.host
if (-not $hostTarget -and $publicUri) { $hostTarget = 'root@' + $publicUri.Host }

$remotePort = 0
if ($cfg.tunnel.remotePort) { $remotePort = [int]$cfg.tunnel.remotePort }
elseif ($publicUri) { $remotePort = $publicUri.Port }

$localPort = 19390
if ($cfg.tunnel.localPort) { $localPort = [int]$cfg.tunnel.localPort }
elseif ($cfg.listenPort) { $localPort = [int]$cfg.listenPort }

$key = [string]$cfg.tunnel.identityFile
if (-not $key) { $key = '~/.ssh/id_ed25519' }
if ($key.StartsWith('~')) { $key = $key -replace '^~', $env:USERPROFILE }

if (-not $hostTarget -or -not $remotePort) {
  Write-Host '[dsh-remote-web] cannot derive tunnel host/remotePort; set tunnel.* in remote.config.json'
  exit 1
}

$sshArgs = @(
  '-N',
  '-o', 'ExitOnForwardFailure=yes',
  '-o', 'ServerAliveInterval=25',
  '-o', 'ServerAliveCountMax=4',
  '-o', 'StrictHostKeyChecking=accept-new',
  '-o', 'BatchMode=yes',
  '-i', $key,
  '-R', "0.0.0.0:${remotePort}:127.0.0.1:${localPort}",
  $hostTarget
)

Set-Content -Path (Join-Path $here 'tunnel.launcher.pid') -Value $PID -Encoding ascii

$tries = 0
while ($true) {
  $proc = Start-Process -FilePath $ssh -ArgumentList $sshArgs -PassThru -NoNewWindow
  Set-Content -Path (Join-Path $here 'tunnel.pid') -Value $proc.Id -Encoding ascii
  $proc.WaitForExit()
  $tries++
  if ($tries -lt 5) { Start-Sleep -Seconds 5 } else { Start-Sleep -Seconds 60 }
}
