<#
.SYNOPSIS
    LiveBridge installer wrapper for Windows.

.DESCRIPTION
    Finds Python 3 (the "py" launcher first, then python / python3 on PATH and the usual
    per-user install folders), prefers the newest 3.x (3.10+ recommended; 3.9 is enough for the
    installer itself, which looks for a 3.10+ interpreter for the MCP server on its own) and runs
    installers\install.py with every argument passed through unchanged.

    Set $env:LIVEBRIDGE_PYTHON to force an interpreter.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installers\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installers\install.ps1 --network

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installers\install.ps1 --pair 192.168.1.30 --token TOKEN

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installers\install.ps1 --uninstall --dry-run
#>

# No param() block on purpose: every argument (--network, --pair ...) lands in $args untouched.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $scriptDir 'install.py'
$passArgs = @($args)
if ($passArgs.Count -gt 0 -and $passArgs[0] -eq '--uninstall') {
    $target = Join-Path $scriptDir 'uninstall.py'
    $passArgs = @($passArgs | Select-Object -Skip 1)
}

function Split-Command([object[]]$Command) {
    $rest = @()
    if ($Command.Count -gt 1) { $rest = @($Command[1..($Command.Count - 1)]) }
    return , $rest
}

# Returns the minor version (e.g. 12 for 3.12) of a Python 3 command, or -1 when it does not run.
function Get-PythonMinor([object[]]$Command) {
    $exe = $Command[0]
    $rest = Split-Command $Command
    try {
        $output = & $exe @rest -c "import sys; print('%d %d' % sys.version_info[:2])" 2>$null
    } catch {
        return -1
    }
    if ($LASTEXITCODE -ne 0 -or -not $output) { return -1 }
    $parts = ("$output".Trim()) -split '\s+'
    if ($parts.Count -lt 2 -or $parts[0] -ne '3') { return -1 }
    $minor = 0
    if (-not [int]::TryParse($parts[1], [ref]$minor)) { return -1 }
    return $minor
}

$candidates = @()
if ($env:LIVEBRIDGE_PYTHON) {
    $candidates += , @($env:LIVEBRIDGE_PYTHON)
} else {
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @('py', '-3') }
    foreach ($name in @('python', 'python3')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        # The Microsoft Store alias in ...\WindowsApps\ is tried last: without a real Store
        # Python it only prints a hint and fails (the probe below then skips it).
        if ($found -and $found.Source -notlike '*\WindowsApps\*') { $candidates += , @($found.Source) }
    }
    # Per-user (python.org default) and all-users installs.
    $pythonRoots = @()
    if ($env:LOCALAPPDATA) { $pythonRoots += (Join-Path $env:LOCALAPPDATA 'Programs\Python') }
    if ($env:ProgramFiles) { $pythonRoots += $env:ProgramFiles }
    foreach ($root in $pythonRoots) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exePath = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exePath) { $script:candidates += , @($exePath) }
            }
    }
    foreach ($name in @('python', 'python3')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found -and $found.Source -like '*\WindowsApps\*') { $candidates += , @($found.Source) }
    }
}

$best = $null
$bestMinor = -1
foreach ($candidate in $candidates) {
    $minor = Get-PythonMinor $candidate
    if ($minor -ge 9 -and $minor -gt $bestMinor) {
        $best = $candidate
        $bestMinor = $minor
    }
}

if ($null -eq $best) {
    Write-Host 'LiveBridge needs Python 3 (3.10 or newer recommended) and none was found.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install one of:'
    Write-Host '  - https://www.python.org/downloads/windows/  (tick "Add python.exe to PATH")'
    Write-Host '  - winget install Python.Python.3.12'
    Write-Host '  - uv: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex", then re-run with --uv'
    Write-Host ''
    Write-Host 'Then open a new PowerShell window and run this script again.'
    exit 1
}

if ($bestMinor -lt 10) {
    Write-Host "Note: $($best -join ' ') is Python 3.$bestMinor; the installer will look for Python 3.10+ for the MCP server."
}

$exe = $best[0]
$prefix = Split-Command $best
& $exe @prefix $target @passArgs
exit $LASTEXITCODE
