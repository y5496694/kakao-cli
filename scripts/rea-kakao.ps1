param(
    [switch]$Runtime,
    [string]$OutputDirectory,
    [string]$Executable = 'C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot ('work\rea\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$ReaNode = 'D:\Codex\tools\node-v24.22.1\node.exe'
$ReaEntry = 'D:\Codex\tools\rea-6.8.0\node_modules\rea-agents\scripts\rea.mjs'
foreach ($RequiredFile in @($ReaNode, $ReaEntry, $Executable)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) { throw "Missing: $RequiredFile" }
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$env:TEMP = 'D:\Codex\tmp'
$env:TMP = 'D:\Codex\tmp'
$env:REA_RIZIN_COMMAND = 'D:\Codex\tools\rizin-0.9.1\rizin-win-installer-clang_cl-64\bin\rizin.exe'
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

& $ReaNode $ReaEntry inspect-pe-imports $Executable --json |
    Set-Content -LiteralPath (Join-Path $OutputDirectory 'imports.json') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'REA PE inspection failed.' }
& $ReaNode $ReaEntry execute-rizin-command $Executable 'iI; iS' --json |
    Set-Content -LiteralPath (Join-Path $OutputDirectory 'native-layout.json') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'REA Rizin inspection failed.' }

if ($Runtime) {
    $KakaoProcess = @(Get-Process -Name KakaoTalk -ErrorAction Stop | Where-Object { $_.Path -eq $Executable })
    if ($KakaoProcess.Count -ne 1) { throw 'Expected one running KakaoTalk process for the selected executable.' }
    & $ReaNode $ReaEntry instrument-with-frida attach $KakaoProcess[0].Id `
        --script-file (Join-Path $PSScriptRoot 'rea-observe.js') --duration-ms 200 --json |
        Set-Content -LiteralPath (Join-Path $OutputDirectory 'runtime-controls.json') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'REA runtime inspection failed. Inspect the retained result before retrying.' }
}
Write-Output $OutputDirectory
