# Gera o EngeCAD-Setup-<versao>.exe em dist\installer\
# Uso: .\packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$versionLine = Get-Content "$root\engecad\__init__.py" | Select-String '__version__\s*=\s*"([^"]+)"'
$version = $versionLine.Matches[0].Groups[1].Value
Write-Host "EngeCAD $version"

Write-Host "-- PyInstaller --"
& "$root\.venv\Scripts\pyinstaller.exe" "$root\packaging\engecad.spec" --distpath "$root\dist" --workpath "$root\build" --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

$isccCandidates = @(
    (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source
    "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe"
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup nao encontrado. winget install JRSoftware.InnoSetup ou https://jrsoftware.org/isdl.php"
}

Write-Host "-- Inno Setup --"
& $iscc "$root\packaging\installer.iss" "/DMyAppVersion=$version"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou" }

Write-Host "Pronto: dist\installer\EngeCAD-Setup-$version.exe"
