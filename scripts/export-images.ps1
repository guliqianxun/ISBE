# Export all ISBE docker images to a single tar for offline server deploy.
#
# Output: dist/isbe-images.tar  (and dist/isbe-images.tar.gz if -Compress)
#
# Usage:
#   ./scripts/export-images.ps1                # build + pull + save
#   ./scripts/export-images.ps1 -Compress      # also gzip
#   ./scripts/export-images.ps1 -SkipBuild     # skip rebuild of radar-worker
#
# Target platform: linux/amd64. Run from repo root.

param(
    [switch]$Compress,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$images = @(
    "postgres:16-alpine",
    "qdrant/qdrant:v1.11.0",
    "minio/minio:RELEASE.2024-09-13T20-26-02Z",
    "langfuse/langfuse:2",
    "prefecthq/prefect:3-latest",
    "louislam/uptime-kuma:1",
    "isbe/radar:latest"
)

$dist = Join-Path $PSScriptRoot "..\dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$tar = Join-Path $dist "isbe-images.tar"

Write-Host "==> Pulling third-party images (linux/amd64)..." -ForegroundColor Cyan
foreach ($img in $images) {
    if ($img -eq "isbe/radar:latest") { continue }
    docker pull --platform linux/amd64 $img
    if ($LASTEXITCODE -ne 0) { throw "pull failed: $img" }
}

if (-not $SkipBuild) {
    Write-Host "==> Building isbe/radar:latest for linux/amd64..." -ForegroundColor Cyan
    Push-Location (Join-Path $PSScriptRoot "..")
    try {
        docker buildx build --platform linux/amd64 -t isbe/radar:latest --load .
        if ($LASTEXITCODE -ne 0) { throw "buildx build failed" }
    } finally {
        Pop-Location
    }
}

Write-Host "==> Saving images to $tar ..." -ForegroundColor Cyan
docker save -o $tar @images
if ($LASTEXITCODE -ne 0) { throw "docker save failed" }

$size = (Get-Item $tar).Length / 1MB
Write-Host ("==> Wrote {0} ({1:N1} MB)" -f $tar, $size) -ForegroundColor Green

if ($Compress) {
    $gz = "$tar.gz"
    Write-Host "==> Compressing to $gz ..." -ForegroundColor Cyan
    # Use 7zip if available, else fallback to .NET GzipStream
    $sevenZip = Get-Command 7z -ErrorAction SilentlyContinue
    if ($sevenZip) {
        & 7z a -tgzip $gz $tar | Out-Null
    } else {
        $in  = [System.IO.File]::OpenRead($tar)
        $out = [System.IO.File]::Create($gz)
        $gzs = New-Object System.IO.Compression.GzipStream($out, [System.IO.Compression.CompressionMode]::Compress)
        $in.CopyTo($gzs)
        $gzs.Dispose(); $out.Dispose(); $in.Dispose()
    }
    $gsize = (Get-Item $gz).Length / 1MB
    Write-Host ("==> Wrote {0} ({1:N1} MB)" -f $gz, $gsize) -ForegroundColor Green
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. scp dist/isbe-images.tar your-server:/path/to/isbe/"
Write-Host "  2. scp docker-compose.yml .env Dockerfile your-server:/path/to/isbe/"
Write-Host "  3. (also copy: alembic/ src/ pyproject.toml uv.lock if you need the worker)"
Write-Host "  4. On server: docker load -i isbe-images.tar"
Write-Host "  5. On server: docker compose -f docker-compose.yml up -d"
