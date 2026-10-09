param([ValidateSet('lint','validate','inspect','render','preview')][string]$Action = 'render')
$ErrorActionPreference = 'Stop'
$env:HYPERFRAMES_NO_TELEMETRY = '1'
$env:HYPERFRAMES_BROWSER_PATH = 'C:\Users\15827\AppData\Local\ms-playwright\chromium_headless_shell-1223\chrome-headless-shell-win64\chrome-headless-shell.exe'
$env:HYPERFRAMES_FFMPEG_PATH = Join-Path $PSScriptRoot '../.cache/video-tools/node_modules/ffmpeg-static/ffmpeg.exe'
$env:HYPERFRAMES_FFPROBE_PATH = Join-Path $PSScriptRoot '../.cache/video-tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe'
$cli = Join-Path $PSScriptRoot '../.cache/npm/_npx/702923228c2ce1e6/node_modules/hyperframes/bin/hyperframes.mjs'
Push-Location $PSScriptRoot
try {
  if ($Action -eq 'render') { node $cli render --output 'ahp-tutorial-15s.mp4' --fps 30 --quality high --workers 2 }
  elseif ($Action -eq 'inspect') { node $cli inspect --at 2.8,6.6,10.7,14.6 }
  elseif ($Action -eq 'preview') { node $cli preview --port 3017 }
  else { node $cli $Action }
  if ($LASTEXITCODE -ne 0) { throw "HyperFrames $Action failed ($LASTEXITCODE)" }
} finally { Pop-Location }
