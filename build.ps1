$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-PythonCommand {
    $candidates = @(
        @{ Command = "py"; Args = @("-3", "--version") },
        @{ Command = "python"; Args = @("--version") }
    )

    foreach ($candidate in $candidates) {
        try {
            $null = & $candidate.Command @($candidate.Args) 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate.Command
            }
        } catch {
        }
    }

    return $null
}

function Invoke-Python {
    param(
        [string]$PythonCommand,
        [string[]]$Arguments
    )

    if ($PythonCommand -eq "py") {
        & py -3 @Arguments
        return
    }

    & $PythonCommand @Arguments
}

function Get-PythonVersion {
    param([string]$PythonCommand)

    $script = 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")'
    $output = Invoke-Python -PythonCommand $PythonCommand -Arguments @("-c", $script) 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        throw "Failed to detect Python version."
    }

    return [Version]($output | Select-Object -First 1)
}

function Assert-MinimumPythonVersion {
    param(
        [string]$PythonCommand,
        [Version]$MinimumVersion = [Version]"3.10.0"
    )

    $currentVersion = Get-PythonVersion -PythonCommand $PythonCommand
    if ($currentVersion -lt $MinimumVersion) {
        throw "Python $($MinimumVersion.ToString(2))+ is required. Current version: $currentVersion. Install Python 3.10 or newer, then rerun this script."
    }

    Write-Host "Using Python $currentVersion" -ForegroundColor DarkGray
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$tauriAppDir = Join-Path $repoRoot "tauri-app"
$requirementsPath = Join-Path $repoRoot "pyserver\requirements.txt"
$pipelineScripts = @(
    "data_pipeline\01get_titles_parsed.py",
    "data_pipeline\02parsedtochunk.py",
    "data_pipeline\03buildindex.py"
)
$builtExePath = Join-Path $tauriAppDir "src-tauri\target\release\MineRAG.exe"
$rootExePath = Join-Path $repoRoot "MineRAG.exe"

Write-Step "Checking build prerequisites"

$pythonCommand = Find-PythonCommand
if (-not $pythonCommand) {
    Write-Error "Python was not found in PATH. Install Python first, then rerun this script."
}

Assert-MinimumPythonVersion -PythonCommand $pythonCommand

foreach ($tool in @("npm")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool was not found in PATH. Install Node.js/npm first, then rerun this script."
    }
}

Push-Location $repoRoot
try {
    Write-Step "Installing Python dependencies from pyserver\\requirements.txt"
    Invoke-Python -PythonCommand $pythonCommand -Arguments @("-m", "pip", "install", "-r", $requirementsPath)

    Write-Step "Running data pipeline scripts"
    foreach ($script in $pipelineScripts) {
        Write-Host "Running $script"
        Invoke-Python -PythonCommand $pythonCommand -Arguments @($script)
    }

    Write-Step "Building Tauri app"
    Push-Location $tauriAppDir
    try {
        & npm run tauri build
        if ($LASTEXITCODE -ne 0) {
            throw "npm run tauri build failed."
        }
    } finally {
        Pop-Location
    }

    Write-Step "Copying MineRAG.exe to repository root"
    if (-not (Test-Path $builtExePath)) {
        throw "Built executable not found: $builtExePath"
    }

    Copy-Item -Path $builtExePath -Destination $rootExePath -Force
    Write-Host "Build completed: $rootExePath" -ForegroundColor Green
} finally {
    Pop-Location
}
