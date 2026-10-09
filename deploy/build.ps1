param([switch]$NoZip)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$serverRoot = Join-Path $projectRoot 'server'
$python = Join-Path $serverRoot '.venv/Scripts/python.exe'
$buildRoot = Join-Path $PSScriptRoot '.build'
$releaseRoot = Join-Path $projectRoot 'release'
$packageRoot = Join-Path $releaseRoot '文档工作室'
$uv = (Get-Command uv -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "命令执行失败：$Command（退出码 $LASTEXITCODE）" }
}

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
Invoke-Checked $uv @('sync', '--project', $serverRoot, '--frozen', '--inexact')
Invoke-Checked $uv @('pip', 'install', '--python', $python, '--index-url', 'https://pypi.org/simple', 'pyinstaller==6.22.3')
Push-Location (Join-Path $projectRoot 'web')
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) {
        Invoke-Checked $npm @('ci', '--no-audit', '--no-fund')
    }
    Invoke-Checked $npm @('run', 'build')
} finally { Pop-Location }

# Resolve browser versions from the installed Playwright, never guess a revision.
$browserManifest = Join-Path $serverRoot '.venv/Lib/site-packages/playwright/driver/package/browsers.json'
$browserCache = Join-Path $env:LOCALAPPDATA 'ms-playwright'
$browsers = (Get-Content -LiteralPath $browserManifest -Raw | ConvertFrom-Json).browsers |
    Where-Object { $_.name -in @('chromium-headless-shell', 'ffmpeg', 'winldd') }
$browserFolders = @($browsers | ForEach-Object { $_.name.Replace('-', '_') + '-' + $_.revision })
if (@($browserFolders | Where-Object { -not (Test-Path -LiteralPath (Join-Path $browserCache $_)) }).Count) {
    $previousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
    try {
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserCache
        Invoke-Checked $python @('-m', 'playwright', 'install', '--only-shell', 'chromium')
    } finally { $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserPath }
}
$previousTokenCache = $env:TIKTOKEN_CACHE_DIR
try {
    $env:TIKTOKEN_CACHE_DIR = Join-Path $buildRoot 'tiktoken-cache'
    Invoke-Checked $python @('-c', "import tiktoken; [tiktoken.get_encoding(name) for name in ('cl100k_base', 'o200k_base')]")
} finally { $env:TIKTOKEN_CACHE_DIR = $previousTokenCache }

Invoke-Checked $python @('-m', 'PyInstaller', '--noconfirm', '--clean',
    '--distpath', (Join-Path $buildRoot 'dist'), '--workpath', (Join-Path $buildRoot 'work'),
    (Join-Path $PSScriptRoot '文档工作室.spec'))
$stagingRoot = Join-Path $buildRoot 'dist/文档工作室'
$stagingBrowsers = Join-Path $stagingRoot '_internal/ms-playwright'
New-Item -ItemType Directory -Path $stagingBrowsers -Force | Out-Null
foreach ($folder in $browserFolders) {
    $source = Join-Path $browserCache $folder
    if (-not (Test-Path -LiteralPath $source)) { throw "Playwright 浏览器资源缺失：$folder" }
    Copy-Item -LiteralPath $source -Destination $stagingBrowsers -Recurse -Force
}

# Replace only program files. SQLite, uploads and .env stay beside the executable.
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
$oldInternal = Join-Path $packageRoot '_internal'
if (Test-Path -LiteralPath $oldInternal) {
    $resolvedInternal = (Resolve-Path -LiteralPath $oldInternal).Path
    $expectedInternal = [IO.Path]::GetFullPath((Join-Path $releaseRoot '文档工作室/_internal'))
    if ($resolvedInternal -ne $expectedInternal) { throw '拒绝清理发布目录以外的路径。' }
    Remove-Item -LiteralPath $resolvedInternal -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $stagingRoot '文档工作室.exe') -Destination $packageRoot -Force
Copy-Item -LiteralPath (Join-Path $stagingRoot '_internal') -Destination $packageRoot -Recurse -Force
Get-ChildItem -LiteralPath $PSScriptRoot -File -Filter '*.md' |
    Copy-Item -Destination $packageRoot -Force
if (-not $NoZip) {
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'snapshot_data.py'), '--target', $packageRoot, '--execute')
    $zip = Join-Path $releaseRoot '文档工作室-Windows.zip'
    Compress-Archive -LiteralPath $packageRoot -DestinationPath $zip -Force
    Write-Host "部署包已生成：$zip"
} else {
    Write-Host "程序已构建（尚未复制当前数据和配置）：$packageRoot"
}
