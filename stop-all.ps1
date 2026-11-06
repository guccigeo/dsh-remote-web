# Stop the DSH remote web proxy and its reverse tunnel.
#
# Primary path: PID files written by run-proxy.ps1 / run-tunnel.ps1.
#   Killing the launcher first is essential - otherwise its supervision loop
#   restarts the child 5 seconds later.
# Fallback: a best-effort command-line sweep via Get-CimInstance, which is
#   denied in some restricted contexts, so its failure is not fatal.
#
# Only this feature's processes are touched; other tunnels are
# left alone.
# ASCII-only on purpose: these files may be read under a non-UTF-8 codepage.
$here = $PSScriptRoot

function Stop-PidFile {
  param([string]$Name)
  $file = Join-Path $here $Name
  if (-not (Test-Path $file)) { return $false }
  $raw = (Get-Content -Path $file -Raw -ErrorAction SilentlyContinue)
  $procId = 0
  if (-not [int]::TryParse(($raw -replace '\s', ''), [ref]$procId)) {
    Write-Host ("{0}: unreadable PID file, removing" -f $Name)
    Remove-Item $file -Force -ErrorAction SilentlyContinue
    return $false
  }
  $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if ($null -eq $proc) {
    Write-Host ("{0}: PID {1} already gone" -f $Name, $procId)
    Remove-Item $file -Force -ErrorAction SilentlyContinue
    return $false
  }
  Write-Host ("stopping {0} -> PID {1} ({2})" -f $Name, $procId, $proc.ProcessName)
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  Remove-Item $file -Force -ErrorAction SilentlyContinue
  return $true
}

# Launchers first, so nothing gets restarted underneath us.
$stopped = 0
foreach ($f in @('proxy.launcher.pid', 'tunnel.launcher.pid', 'proxy.pid', 'tunnel.pid')) {
  if (Stop-PidFile -Name $f) { $stopped++ }
}

# Best-effort sweep for anything started outside the launchers.
try {
  # Tightened patterns: match the *full path* under this feature's directory, so a
  # caller whose command line merely mentions a file name (e.g. "node --check
  # dsh-remote-web.mjs", or a DSH tool-call runner wrapping that text) is not hit.
  # Real incident: the loose '*dsh-remote-web.mjs*' pattern killed the caller's own
  # tool-call process.
  $dir = $here.TrimEnd('\')
  $patterns = @(
    "$dir\run-proxy.ps1*",
    "$dir\run-tunnel.ps1*",
    "$dir\dsh-remote-web.mjs*",
    '*17933:127.0.0.1:19390*'
  )
  $self = $PID
  # Never touch ourselves nor any ancestor: the shell that called stop-all may
  # legitimately have the project path in its own command line.
  $protected = @{}
  $walk = $self
  for ($i = 0; $i -lt 12 -and $walk; $i++) {
    $protected[[int]$walk] = $true
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=$walk") -ErrorAction SilentlyContinue
    if (-not $proc) { break }
    $walk = $proc.ParentProcessId
  }
  $hits = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $cmd = $_.CommandLine
    if (-not $cmd) { return $false }
    if ($protected.ContainsKey([int]$_.ProcessId)) { return $false }
    if ($cmd -like '*stop-all.ps1*') { return $false }
    if ($_.Name -notin @('node.exe', 'powershell.exe', 'pwsh.exe', 'ssh.exe')) { return $false }
    foreach ($p in $patterns) { if ($cmd -like $p) { return $true } }
    return $false
  }
  foreach ($h in $hits) {
    Write-Host ("stopping stray PID {0} ({1})" -f $h.ProcessId, $h.Name)
    Stop-Process -Id $h.ProcessId -Force -ErrorAction SilentlyContinue
    $stopped++
  }
} catch {
  Write-Host ("note: command-line sweep unavailable ({0})" -f $_.Exception.Message.Trim())
}

if ($stopped -eq 0) { Write-Host 'no dsh-remote-web process was running.' }
else { Write-Host 'stopped.' }
