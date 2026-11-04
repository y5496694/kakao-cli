param(
    [switch]$Runtime,
    [string]$OutputDirectory,
    [string]$Executable
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot ('work\rea-evidence\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$ReaNode = $env:KAKAOCLI_REA_NODE
if (-not $ReaNode) {
    $Node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($Node) { $ReaNode = $Node.Source }
}
if (-not $ReaNode) {
    $ReaNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
}
$ReaEntry = $env:KAKAOCLI_REA_ENTRY
if (-not $ReaEntry) {
    foreach ($Relative in @('work\rea\node_modules\rea-agents\scripts\rea.mjs', 'tools\rea\node_modules\rea-agents\scripts\rea.mjs', 'node_modules\rea-agents\scripts\rea.mjs')) {
        $Candidate = Join-Path $ProjectRoot $Relative
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $ReaEntry = $Candidate; break }
    }
}
if (-not $Executable) {
    foreach ($BasePath in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
        if (-not $BasePath) { continue }
        $Candidate = Join-Path $BasePath 'Kakao\KakaoTalk\KakaoTalk.exe'
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $Executable = $Candidate; break }
    }
}
foreach ($RequiredFile in @($ReaNode, $ReaEntry, $Executable)) {
    if (-not $RequiredFile -or -not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw 'REA/Node/KakaoTalk was not found. Run install.ps1 -InstallRea or set KAKAOCLI_REA_NODE, KAKAOCLI_REA_ENTRY and -Executable.'
    }
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

& $ReaNode $ReaEntry inspect-pe-imports $Executable --format json |
    Set-Content -LiteralPath (Join-Path $OutputDirectory 'imports.json') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'REA PE inspection failed.' }
if ($env:REA_RIZIN_COMMAND) {
    & $ReaNode $ReaEntry execute-rizin-command $Executable 'iI; iS' --format json |
        Set-Content -LiteralPath (Join-Path $OutputDirectory 'native-layout.json') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'REA Rizin inspection failed.' }
}

if ($Runtime) {
    $KakaoProcess = @(Get-Process -Name KakaoTalk -ErrorAction Stop | Where-Object { $_.Path -eq $Executable })
    if ($KakaoProcess.Count -ne 1) { throw 'Expected one running KakaoTalk process for the selected executable.' }
    & $ReaNode $ReaEntry instrument-with-frida attach $KakaoProcess[0].Id `
        --script-file (Join-Path $PSScriptRoot 'rea-observe.js') --duration-ms 200 --format json |
        Set-Content -LiteralPath (Join-Path $OutputDirectory 'runtime-controls.json') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'REA runtime inspection failed. Inspect the retained result before retrying.' }
}
Write-Output $OutputDirectory
