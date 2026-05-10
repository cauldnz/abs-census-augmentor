# Renders one (or every) README demo GIF.
#
# Two rendering modes:
#
#   --local : run vhs natively (requires `vhs`, `ttyd`, `ffmpeg`,
#             and `column` on PATH). Fast; no Docker dependency.
#             On Windows hosts these tools rarely come pre-installed,
#             so this mode is mostly useful inside the dev container
#             or on developer machines that have them.
#
#   --docker: build a custom VHS Docker image and run it with the
#             repo and ABS cache mounted. Works on any host with
#             Docker reachable (typical Windows path).
#
# The default is auto: prefer --local if `vhs` is on PATH, else
# fall back to --docker. Pass either flag to force one mode.
#
# Usage (from the repo root):
#     .\tools\demo\render.ps1                                  # demo.tape -> docs/demo.gif (auto)
#     .\tools\demo\render.ps1 discover-datasets                # discover-datasets.tape -> docs/discover-datasets.gif
#     .\tools\demo\render.ps1 preset-features                  # preset-features.tape -> docs/preset-features.gif
#     .\tools\demo\render.ps1 --all                            # every *.tape in tools/demo/
#     .\tools\demo\render.ps1 --local --all
#     .\tools\demo\render.ps1 --docker preset-features

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

# ---- arg parsing -------------------------------------------------------

$mode = "auto"
$slug = ""
$all  = $false

foreach ($arg in $Args) {
    switch ($arg) {
        "--local"  { $mode = "local" }
        "--docker" { $mode = "docker" }
        "--all"    { $all = $true }
        default {
            if ($arg -like "--*") {
                Write-Host "Unknown flag: $arg" -ForegroundColor Red
                Write-Host "Valid flags: --local, --docker, --all"
                exit 2
            }
            $slug = $arg
        }
    }
}
if (-not $slug) { $slug = "demo" }

# ---- preflight (shared by all modes) -----------------------------------

if (-not (Test-Path "tools/demo/demo.tape")) {
    Write-Error "Run this script from the repo root (so 'tools/demo/' is visible)."
}

# ---- mode resolution ---------------------------------------------------

function Test-Tool { param([string]$Name) [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Resolve-Mode {
    if ($mode -eq "local") {
        $missing = @()
        foreach ($tool in @("vhs", "ttyd", "ffmpeg", "column")) {
            if (-not (Test-Tool $tool)) { $missing += $tool }
        }
        if ($missing.Count -gt 0) {
            Write-Error "Forced --local but these tools are missing: $($missing -join ', '). Install them or drop --local to fall back to Docker."
        }
        return "local"
    }

    if ($mode -eq "docker") {
        try { docker version --format '{{.Server.Version}}' | Out-Null } catch {
            Write-Error "Forced --docker but Docker isn't reachable. Start Docker Desktop and retry."
        }
        return "docker"
    }

    # auto: prefer local if all tools present
    if ((Test-Tool "vhs") -and (Test-Tool "ttyd") -and (Test-Tool "ffmpeg") -and (Test-Tool "column")) {
        return "local"
    }

    try { docker version --format '{{.Server.Version}}' | Out-Null; return "docker" } catch {
        Write-Error "Neither local vhs nor Docker is available. Install vhs and its deps, or start Docker Desktop, or open this repo in the .devcontainer/."
    }
}

$resolvedMode = Resolve-Mode
Write-Host "Render mode: $resolvedMode" -ForegroundColor Cyan

# ---- host cache pre-warm -----------------------------------------------

$hostCache = Join-Path $env:LOCALAPPDATA "census-augment\census-augment\Cache"

# Pick any demo config to drive `census-augment fetch` for the
# boundaries + GCP DataPack - these are the same regardless of which
# config a tape references.
$fetchConfig = "tools/demo/config.yaml"
if (-not (Test-Path $fetchConfig)) {
    $first = Get-ChildItem -Path tools/demo -Filter '*.yaml' | Select-Object -First 1
    if ($first) { $fetchConfig = $first.FullName }
}

if (-not (Test-Path (Join-Path $hostCache "data\boundaries")) -and (Test-Path $fetchConfig)) {
    Write-Host "ABS cache not yet populated - running 'census-augment fetch' on the host first..." -ForegroundColor Yellow
    uv run census-augment fetch --config $fetchConfig --boundaries --census
    if ($LASTEXITCODE -ne 0) {
        Write-Error "census-augment fetch failed. Resolve the error above and re-run."
    }
}

Write-Host "Pre-warming registered-dataset caches via host-side runs..." -ForegroundColor Cyan
Get-ChildItem -Path tools/demo -Filter '*.yaml' | ForEach-Object {
    Write-Host "  -> $($_.Name)" -ForegroundColor Cyan
    uv run census-augment run --config $_.FullName *> $null
    # Reset $LASTEXITCODE so a transient pre-warm failure doesn't
    # poison later checks.
    $global:LASTEXITCODE = 0
}

# ---- docker setup (only in docker mode) -------------------------------

if ($resolvedMode -eq "docker") {
    Write-Host "Building census-augment-vhs image (cached layers reused if source unchanged)..." -ForegroundColor Cyan
    docker build -f tools/demo/Dockerfile -t census-augment-vhs . *> $null
    if ($LASTEXITCODE -ne 0) {
        docker build -f tools/demo/Dockerfile -t census-augment-vhs .
        Write-Error "Docker build failed. See output above."
    }
}

New-Item -ItemType Directory -Force -Path docs | Out-Null
New-Item -ItemType Directory -Force -Path docs/frames | Out-Null

# ---- per-tape render ---------------------------------------------------

function Render-Tape {
    param([string]$Slug)

    $tapePath   = "tools/demo/${Slug}.tape"
    $outputPath = "docs/${Slug}.gif"

    if (-not (Test-Path $tapePath)) {
        Write-Host "Tape file not found: $tapePath" -ForegroundColor Red
        Write-Host "Available tapes:"
        Get-ChildItem -Path tools/demo -Filter '*.tape' | ForEach-Object { "  $($_.Name)" }
        throw "tape missing"
    }

    Write-Host "Rendering ${tapePath} -> ${outputPath} ..." -ForegroundColor Cyan

    if ($resolvedMode -eq "local") {
        vhs $tapePath
    } else {
        docker run --rm `
            -v "${PWD}:/vhs" `
            -v "${hostCache}:/root/.cache/census-augment" `
            census-augment-vhs `
            $tapePath
    }

    if ($LASTEXITCODE -ne 0) { Write-Error "vhs render failed for $Slug." }
}

# ---- dispatch ----------------------------------------------------------

if ($all) {
    $rendered = @()
    Get-ChildItem -Path tools/demo -Filter '*.tape' | ForEach-Object {
        $tapeSlug = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        Render-Tape -Slug $tapeSlug
        $rendered += "docs/${tapeSlug}.gif"
    }
    Write-Host ""
    Write-Host "Rendered $($rendered.Count) GIFs:" -ForegroundColor Green
    foreach ($gif in $rendered) { Write-Host "  - $gif" }
    Write-Host "Inspect each and 'git add' the ones you're happy with."
} else {
    Render-Tape -Slug $slug
    Write-Host "Done. Inspect docs/${slug}.gif and 'git add' it when you're happy." -ForegroundColor Green
}
