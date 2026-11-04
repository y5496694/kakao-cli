param(
    [string]$PythonExecutable,
    [switch]$InstallRea
)
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)." }
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    $PythonCandidates = @()
    if ($PythonExecutable) {
        $PythonCandidates += @{ Executable = $PythonExecutable; Arguments = @() }
    } else {
        $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($Launcher) { $PythonCandidates += @{ Executable = $Launcher.Source; Arguments = @('-3') } }
        $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($PythonCommand -and $PythonCommand.Source -notlike '*\WindowsApps\*') {
            $PythonCandidates += @{ Executable = $PythonCommand.Source; Arguments = @() }
        }
        $BundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $BundledPython -PathType Leaf) {
            $PythonCandidates += @{ Executable = $BundledPython; Arguments = @() }
        }
        $PythonInstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
        if (Test-Path -LiteralPath $PythonInstallRoot -PathType Container) {
            foreach ($Directory in Get-ChildItem -LiteralPath $PythonInstallRoot -Directory | Sort-Object Name -Descending) {
                $Candidate = Join-Path $Directory.FullName 'python.exe'
                if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
                    $PythonCandidates += @{ Executable = $Candidate; Arguments = @() }
                }
            }
        }
    }
    $SelectedPython = $null
    foreach ($Candidate in $PythonCandidates) {
        try {
            $Prefix = $Candidate.Arguments
            & $Candidate.Executable @Prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $SelectedPython = $Candidate; break }
        } catch { continue }
    }
    if (-not $SelectedPython) { throw 'Python 3.10 or newer is required. Use -PythonExecutable with its full path.' }
    $Prefix = $SelectedPython.Arguments
    & $SelectedPython.Executable @Prefix -m venv $VenvDir
    Assert-NativeSuccess 'Python environment creation'
}
& $VenvPython -m pip install -e $ProjectDir
Assert-NativeSuccess 'CLI installation'

if ($InstallRea) {
    $ReaDir = Join-Path $ProjectDir 'work\rea'
    New-Item -ItemType Directory -Path $ReaDir -Force | Out-Null
    $PackageJson = Join-Path $ReaDir 'package.json'
    if (-not (Test-Path -LiteralPath $PackageJson)) {
        [IO.File]::WriteAllText($PackageJson, '{"name":"kakao-cli-local-runtime","version":"1.0.0","private":true}', (New-Object Text.UTF8Encoding($false)))
    }
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $Pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($Npm) {
        & $Npm.Source install --prefix $ReaDir --save-exact 'rea-agents@6.8.0'
    } elseif ($Pnpm) {
        & $Pnpm.Source --dir $ReaDir add --save-exact --allow-build=frida 'rea-agents@6.8.0'
    } else {
        throw 'Node.js and npm, or pnpm with --allow-build support, are required for -InstallRea. No global configuration was changed.'
    }
    Assert-NativeSuccess 'REA/Frida installation'
}

Write-Host 'Installation complete.'
Write-Host "Check: & '$ProjectDir\kakaocli.cmd' doctor --json"
if (-not $InstallRea) {
    Write-Host 'For background commands, run install.ps1 -InstallRea or configure KAKAOCLI_REA_NODE and KAKAOCLI_REA_ENTRY.'
}

