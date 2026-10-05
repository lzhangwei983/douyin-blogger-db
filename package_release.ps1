$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root
$version = '1.1.0'
$buildEnv = Join-Path $root '.release-package-venv'
$buildPython = Join-Path $buildEnv 'Scripts\python.exe'
$releaseDir = Join-Path $root 'release'
$stage = Join-Path $releaseDir "windows-v$version"
$exe = Join-Path $root 'dist\DouyinBlogDB.exe'
$bundleZip = Join-Path $releaseDir "DouyinBlogDB-Windows-v$version.zip"
$sourceZip = Join-Path $releaseDir "DouyinBlogDB-source-v$version.zip"

if ($env:DYDB_BUILD_PYTHON) {
    $basePython = $env:DYDB_BUILD_PYTHON
    if (-not (Test-Path $basePython)) { throw 'DYDB_BUILD_PYTHON 指向的 Python 解释器不存在。' }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $basePython = (Get-Command python).Source
} else {
    throw '未找到 Python。请安装 Python 3.10 或更高版本后重试。'
}
& $basePython -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw '打包需要 Python 3.10 或更高版本。' }

$gitStatus = & git status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0) { throw '无法读取 Git 状态。' }
if ($gitStatus) { throw '请先提交当前变更，再从干净的 Git 提交生成可复现发行包。' }
if (-not (Select-String -Path (Join-Path $root 'app.py') -Pattern '__version__\s*=\s*["'']1\.1\.0["'']' -Quiet)) {
    throw 'app.py 版本不是 1.1.0，拒绝生成错误版本号的发行包。'
}

if (-not (Test-Path $buildPython)) {
    & $basePython -m venv $buildEnv
    if ($LASTEXITCODE -ne 0) { throw '无法创建隔离的打包环境。' }
}
& $buildPython -m pip install -r requirements.txt pyinstaller==6.22.3 pyinstaller-hooks-contrib==2026.8 pip-licenses==5.5.5
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络和 Python 版本。' }
& $buildPython -m PyInstaller --clean --noconfirm DouyinBlogDB.spec
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $exe)) { throw 'PyInstaller 打包失败。' }

$smokeId = [guid]::NewGuid().ToString('N')
$smokeRoot = Join-Path $env:TEMP "DouyinBlogDB-release-smoke-$smokeId"
$smokeReport = Join-Path $smokeRoot 'smoke_result.json'
$originalDataHome = Get-Item Env:\DYDB_HOME -ErrorAction SilentlyContinue
$originalSmokeReport = Get-Item Env:\DYDB_SMOKE_REPORT -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $smokeRoot -Force | Out-Null
try {
    $env:DYDB_HOME = $smokeRoot
    $env:DYDB_SMOKE_REPORT = $smokeReport
    $smokeProcess = Start-Process -FilePath $exe -ArgumentList '--smoke-test' -Wait -WindowStyle Hidden -PassThru
    if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path $smokeReport)) { throw '发行版 EXE 冒烟启动失败。' }
    $smoke = Get-Content -Raw -Path $smokeReport | ConvertFrom-Json
    if (-not $smoke.ready) { throw '发行版 EXE 未能启动本地服务。' }
    foreach ($path in @('/', '/api/bloggers', '/api/search?q=AI', '/api/settings', '/api/cookies/status')) {
        $routeStatus = $smoke.statuses.PSObject.Properties[$path].Value
        if ([int]$routeStatus -ne 200) { throw "发行版本地路由检查失败：$path" }
    }
    if ([int]$smoke.collection_start.status -ne 400 -or [int]$smoke.video_collection_start.status -ne 400) {
        throw '发行版采集路由未按预期提示先完成本机登录。'
    }
}
finally {
    if ($null -eq $originalDataHome) { Remove-Item Env:\DYDB_HOME -ErrorAction SilentlyContinue }
    else { $env:DYDB_HOME = $originalDataHome.Value }
    if ($null -eq $originalSmokeReport) { Remove-Item Env:\DYDB_SMOKE_REPORT -ErrorAction SilentlyContinue }
    else { $env:DYDB_SMOKE_REPORT = $originalSmokeReport.Value }
    $resolvedTemp = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    $resolvedSmoke = [System.IO.Path]::GetFullPath($smokeRoot)
    if ($resolvedSmoke.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedSmoke -Recurse -Force -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $stage 'DouyinBlogDB.exe') -Force
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination $stage -Force
Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination $stage -Force
Copy-Item -LiteralPath (Join-Path $root 'DISCLAIMER.md') -Destination $stage -Force
Copy-Item -LiteralPath (Join-Path $root 'AGENT_GUIDE.md') -Destination $stage -Force
$notices = Join-Path $stage 'THIRD_PARTY_NOTICES.md'
& (Join-Path $buildEnv 'Scripts\pip-licenses.exe') --format=plain-vertical --with-urls --with-license-file --no-license-path --output-file $notices
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $notices)) { throw '无法生成第三方依赖许可清单。' }
$noticeText = [System.IO.File]::ReadAllText($notices)
$noticeHeader = "# Windows 发行包第三方依赖许可`r`n`r`n以下清单由隔离打包环境生成，包含依赖包的版本、许可标记、上游链接和可取得的许可证全文。若包元数据显示 UNKNOWN，请以该条目后附许可证全文为准。`r`n`r`n"
[System.IO.File]::WriteAllText($notices, $noticeHeader + $noticeText, [System.Text.UTF8Encoding]::new($false))

Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $bundleZip -Force
& git archive --format=zip --output=$sourceZip HEAD
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $sourceZip)) { throw '无法生成基于当前提交的干净源码包。' }

$hashes = @($bundleZip, $sourceZip) | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_
    "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($_))"
}
[System.IO.File]::WriteAllLines((Join-Path $releaseDir 'SHA256SUMS.txt'), $hashes, [System.Text.UTF8Encoding]::new($false))
Write-Host "Windows 应用包：$bundleZip"
Write-Host "干净源码包：$sourceZip"
Write-Host "SHA-256：$(Join-Path $releaseDir 'SHA256SUMS.txt')"
