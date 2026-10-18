param([string]$RevitInstallDir = 'C:\Program Files\Autodesk\Revit 2026')
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:DOTNET_CLI_HOME = Join-Path $taskRoot '.build-runtime'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
Push-Location $taskRoot
try {
    dotnet build revit/Ac2Revit/Ac2Revit.csproj -c Release "-p:RevitInstallDir=$RevitInstallDir"
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    $packageDir = Join-Path $taskRoot 'dist/Revit2026'
    New-Item -ItemType Directory -Path $packageDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskRoot 'revit/Ac2Revit/bin/Release/net8.0-windows/Ac2Revit.dll') -Destination $packageDir
    $assetDir = Join-Path $packageDir 'assets'
    New-Item -ItemType Directory -Path $assetDir -Force | Out-Null
    foreach ($assetName in @('AC2R_RectangularColumn.rfa','AC2R_RectangularBeam.rfa','AC2R_StructuralColumn.rft','AC2R_Void.rft')) {
        Copy-Item -LiteralPath (Join-Path $taskRoot "revit/assets/$assetName") -Destination $assetDir
    }
    $assemblyPath = Join-Path $packageDir 'Ac2Revit.dll'
    $template = [IO.File]::ReadAllText((Join-Path $taskRoot 'revit/Ac2Revit.addin.template'))
    [IO.File]::WriteAllText((Join-Path $packageDir 'Ac2Revit.addin'), $template.Replace('__ASSEMBLY_PATH__', [Security.SecurityElement]::Escape($assemblyPath)))
    Write-Output "Package: $packageDir"
} finally { Pop-Location }
