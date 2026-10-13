# -AllowCredentialText 表.列：人工确认后，允许该表列中出现与测试密码相同的独立词（见 README）。
param([switch]$NoZip, [string[]]$AllowCredentialText = @())

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

# Assemble the package from an allowlist in a freshly emptied directory, so files left in
# release/ by earlier builds or local runs never ship; only the freshly generated private config is included.
$programEntries = @('文档工作室.exe', '_internal')
$snapshotEntries = @('data', '.env', '.env.example', '使用说明.txt', '部署校验.json')

function Remove-Under([string]$Path, [string]$Parent) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($Parent).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "拒绝清理 $Parent 以外的路径：$full" }
    if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
}

function Assert-Allowlisted([string]$Path, [string[]]$Allowed) {
    $unexpected = @(Get-ChildItem -LiteralPath $Path -Force | Where-Object { $_.Name -notin $Allowed } | ForEach-Object { $_.Name })
    if ($unexpected.Count) { throw "发布目录含有不在允许清单中的文件，拒绝打包：$($unexpected -join '、')" }
}

if ($NoZip) {
    # Local trial run: refresh program files only; data and .env beside the exe are kept.
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    Remove-Under (Join-Path $packageRoot '_internal') $packageRoot
    foreach ($entry in $programEntries) {
        Copy-Item -LiteralPath (Join-Path $stagingRoot $entry) -Destination $packageRoot -Recurse -Force
    }
    Write-Host "程序已构建（尚未复制当前数据和配置）：$packageRoot"
    return
}

$zipStage = Join-Path $buildRoot 'package'
Remove-Under $zipStage $buildRoot
$zipPackage = Join-Path $zipStage '文档工作室'
New-Item -ItemType Directory -Path $zipPackage -Force | Out-Null
foreach ($entry in $programEntries) {
    Copy-Item -LiteralPath (Join-Path $stagingRoot $entry) -Destination $zipPackage -Recurse -Force
}
$snapshotArguments = @((Join-Path $PSScriptRoot 'snapshot_data.py'), '--target', $zipPackage, '--execute', '--include-ai-config')
foreach ($column in $AllowCredentialText) { $snapshotArguments += @('--allow-credential-text', $column) }
Invoke-Checked $python $snapshotArguments
Assert-Allowlisted $zipPackage ($programEntries + $snapshotEntries)
foreach ($private in @('初始管理员密码.txt', 'logs')) {
    if (Test-Path -LiteralPath (Join-Path $zipPackage $private)) { throw "发布目录含有本机私密文件 $private，拒绝打包。" }
}
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
$zip = Join-Path $releaseRoot '文档工作室-Windows.zip'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath $zipPackage -DestinationPath $zip
Write-Host "部署包已生成：$zip"
Write-Host '此为包含当前数据和 AI 密钥的私有迁移包，请勿公开分享。'
