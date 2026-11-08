param([string]$PayloadPath)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$work = Join-Path $root 'build'
$dist = Join-Path $root 'dist'
$python = Join-Path $root '.venv\Scripts\python.exe'
$compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework 4.x compiler was not found.' }
New-Item -ItemType Directory -Path $work,$dist,(Join-Path $work 'temp') -Force | Out-Null
$oldTemp, $oldTmp, $oldConfig, $oldGpu = $env:TEMP, $env:TMP, $env:PYINSTALLER_CONFIG_DIR, $env:PYGFX_DATA_DIR
Push-Location $root
try {
    $env:TEMP = $env:TMP = Join-Path $work 'temp'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $work 'pyinstaller-cache'
    $env:PYGFX_DATA_DIR = Join-Path $work 'pygfx'
    New-Item -ItemType Directory -Path $env:PYGFX_DATA_DIR -Force | Out-Null
    if (-not $PayloadPath) {
        & $python -B -m PyInstaller --noconfirm --clean --workpath (Join-Path $work 'pyinstaller') --distpath (Join-Path $work 'payload') (Join-Path $PSScriptRoot 'PointLabStudio.spec')
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
        $releaseVersion = & $python -B -c "from core import __version__; print(__version__)"
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the application version.' }
        $PayloadPath = Join-Path $work "payload\PointLabStudio-v$releaseVersion-Windows-x64.exe"
    }
    $payload = Get-Item -LiteralPath $PayloadPath
    try {
        $null = [Reflection.AssemblyName]::GetAssemblyName($payload.FullName)
        throw 'Use the native PyInstaller payload, not an already wrapped release EXE.'
    } catch [BadImageFormatException] { }
    $version = $payload.VersionInfo.FileVersion
    if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'The payload has no valid release version.' }
    $name = "PointLabStudio-v$version-Windows-x64.exe"
    $metadata = Join-Path $work 'LauncherVersion.cs'
    @"
using System.Reflection;
[assembly: AssemblyTitle("PointLab Studio")]
[assembly: AssemblyProduct("PointLab Studio")]
[assembly: AssemblyVersion("$version.0")]
[assembly: AssemblyFileVersion("$version")]
[assembly: AssemblyInformationalVersion("$version")]
"@ | Set-Content -LiteralPath $metadata -Encoding UTF8
    $iconPath = Join-Path $work 'pyinstaller\PointLabStudio\app.ico'
    if (-not (Test-Path -LiteralPath $iconPath)) { throw 'Multi-resolution app.ico is missing. Run a full build first.' }
    $staging = Join-Path $work $name
    & $compiler /nologo /codepage:65001 /target:winexe /platform:x64 /optimize+ "/out:$staging" "/win32icon:$iconPath" "/resource:$($payload.FullName),PointLabPayload" $metadata (Join-Path $PSScriptRoot 'Launcher.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }
    Move-Item -LiteralPath $staging -Destination (Join-Path $dist $name) -Force
    # Reusing the release filename otherwise leaves Explorer's old icon cached.
    if (-not ('PointLabShell' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PointLabShell {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern void SHChangeNotify(uint eventId, uint flags, string path, IntPtr unused);
}
'@
    }
    [PointLabShell]::SHChangeNotify(0x2000, 0x1005, (Join-Path $dist $name), [IntPtr]::Zero)
    [PointLabShell]::SHChangeNotify(0x8000000, 0x1000, $null, [IntPtr]::Zero)
    Write-Output "Built: $(Join-Path $dist $name)"
} finally {
    $env:TEMP, $env:TMP, $env:PYINSTALLER_CONFIG_DIR, $env:PYGFX_DATA_DIR = $oldTemp, $oldTmp, $oldConfig, $oldGpu
    Pop-Location
}
