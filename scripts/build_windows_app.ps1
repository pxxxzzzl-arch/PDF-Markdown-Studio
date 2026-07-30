[CmdletBinding()]
param(
    [ValidateSet("full", "lite")]
    [string]$Edition = "full",

    [string]$Python = "python",

    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "Windows 应用只能在 Windows 上构建；PyInstaller 不支持跨系统构建。"
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$buildRoot = Join-Path $projectRoot "build\windows-app"
$buildVenv = Join-Path $buildRoot "venv"
$buildPython = Join-Path $buildVenv "Scripts\python.exe"
$wheelDirectory = Join-Path $buildRoot "wheels"
$stageRoot = Join-Path $buildRoot "stage"
$appDirectory = Join-Path $stageRoot "PDF Markdown Studio"
$pyinstallerWork = Join-Path $buildRoot "pyinstaller"
$smokeDirectory = Join-Path $buildRoot "smoke-中文 空格"
$samplePdf = Join-Path $buildRoot "sample.pdf"
$artifactDirectory = Join-Path $projectRoot "dist\windows"
$webViewBootstrapperName = "MicrosoftEdgeWebview2Setup.exe"
$webViewBootstrapper = Join-Path $appDirectory $webViewBootstrapperName
$webViewBootstrapperUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$maxReleaseAssetBytes = 2GB

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败（退出码 $LASTEXITCODE）：$FilePath $($ArgumentList -join ' ')"
    }
}

function Resolve-Iscc {
    if ($env:ISCC_PATH -and (Test-Path -LiteralPath $env:ISCC_PATH -PathType Leaf)) {
        return [System.IO.Path]::GetFullPath($env:ISCC_PATH)
    }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "未找到 Inno Setup 6。请安装后重试，或设置 ISCC_PATH。"
}

function Assert-ReleaseAssetSize {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path
    if ($item.Length -ge $maxReleaseAssetBytes) {
        $gib = [Math]::Round($item.Length / 1GB, 2)
        throw "Release 资产必须小于 2 GiB：$($item.Name) 当前为 $gib GiB。"
    }
}

Push-Location $projectRoot
try {
    $pythonVersion = (& $Python -c (
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    )).Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.12") {
        throw "Windows 发行构建需要 Python 3.12，当前为：$pythonVersion"
    }

    $version = (& $Python -c (
        "import sys; sys.path.insert(0, 'src'); import pdfmd; print(pdfmd.__version__)"
    )).Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch "^\d+\.\d+\.\d+(?:[-+].+)?$") {
        throw "无法读取有效的项目版本：$version"
    }

    Write-Host "构建 PDF Markdown Studio $version Windows x64 ($Edition)…"
    Invoke-Checked "npm.cmd" "--prefix" "frontend" "ci"
    Invoke-Checked "npm.cmd" "--prefix" "frontend" "run" "build" "--" "--mode" "desktop"

    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
    $null = New-Item -ItemType Directory -Path $wheelDirectory
    $null = New-Item -ItemType Directory -Path $artifactDirectory -Force

    Invoke-Checked $Python "-m" "venv" $buildVenv
    Invoke-Checked $buildPython "-m" "pip" "install" "--disable-pip-version-check" `
        "--upgrade" "pip"
    Invoke-Checked $buildPython "-m" "pip" "install" "--disable-pip-version-check" `
        "build>=1.2,<2"
    Invoke-Checked $buildPython "-m" "build" "--wheel" "--outdir" $wheelDirectory "."

    $wheels = @(Get-ChildItem -LiteralPath $wheelDirectory -Filter "*.whl" -File)
    if ($wheels.Count -ne 1) {
        throw "预期生成一个 wheel，实际为 $($wheels.Count) 个。"
    }
    $wheelPath = $wheels[0].FullName
    $extras = if ($Edition -eq "full") {
        "primary,desktop-build,windows-desktop"
    }
    else {
        "desktop-build,windows-desktop"
    }
    $packageTarget = "${wheelPath}[$extras]"
    $pipArguments = @(
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        $packageTarget
    )
    if ($Edition -eq "full") {
        # Docling recommends this CPU wheel index when no CUDA runtime is
        # required. It avoids shipping an accidental GPU-oriented PyTorch stack.
        $pipArguments += @(
            "--extra-index-url",
            "https://download.pytorch.org/whl/cpu"
        )
    }
    Invoke-Checked $buildPython @pipArguments
    if ($Edition -eq "full") {
        Invoke-Checked $buildPython "-c" (
            "import torch; " +
            "assert torch.version.cuda is None, " +
            "f'Expected CPU-only torch, found CUDA {torch.version.cuda}'"
        )
    }
    Invoke-Checked $buildPython "-m" "pip" "install" "--disable-pip-version-check" `
        "reportlab>=4.2,<5"

    Invoke-Checked $buildPython "scripts\prepare_windows_assets.py" `
        "--build-root" $buildRoot "--version" $version

    $env:PDFMD_WINDOWS_BUILD_ROOT = $buildRoot
    $env:PDFMD_WINDOWS_EDITION = $Edition
    try {
        Invoke-Checked $buildPython "-m" "PyInstaller" "--noconfirm" "--clean" `
            "--distpath" $stageRoot "--workpath" $pyinstallerWork `
            "scripts\windows_app.spec"
    }
    finally {
        Remove-Item Env:PDFMD_WINDOWS_BUILD_ROOT -ErrorAction SilentlyContinue
        Remove-Item Env:PDFMD_WINDOWS_EDITION -ErrorAction SilentlyContinue
    }

    $appExecutable = Join-Path $appDirectory "PDF Markdown Studio.exe"
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "PyInstaller 未生成应用入口：$appExecutable"
    }

    Copy-Item -LiteralPath "LICENSE" -Destination (Join-Path $appDirectory "LICENSE.txt")
    if (Test-Path -LiteralPath "THIRD_PARTY_NOTICES.md" -PathType Leaf) {
        Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" `
            -Destination (Join-Path $appDirectory "THIRD_PARTY_NOTICES.md")
    }
    Copy-Item -LiteralPath (Join-Path $buildRoot "generated\PDF Markdown Studio.ico") `
        -Destination (Join-Path $appDirectory "PDF Markdown Studio.ico")

    Write-Host "下载并验证 Microsoft WebView2 Evergreen Bootstrapper…"
    Invoke-WebRequest -Uri $webViewBootstrapperUrl -OutFile $webViewBootstrapper `
        -UseBasicParsing
    $bootstrapperItem = Get-Item -LiteralPath $webViewBootstrapper
    if ($bootstrapperItem.Length -lt 100KB) {
        throw "WebView2 Bootstrapper 下载结果异常：$($bootstrapperItem.Length) bytes"
    }
    $signature = Get-AuthenticodeSignature -FilePath $webViewBootstrapper
    if (
        $signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch "Microsoft"
    ) {
        throw "WebView2 Bootstrapper 的 Microsoft Authenticode 签名验证失败。"
    }

    Invoke-Checked $buildPython "scripts\generate_sample_pdf.py" $samplePdf
    & (Join-Path $projectRoot "scripts\smoke_test_windows.ps1") `
        -AppDirectory $appDirectory `
        -SamplePdf $samplePdf `
        -WorkDirectory $smokeDirectory `
        -RequireDocling ($Edition -eq "full")

    $portableName = "PDF-Markdown-Studio-$version-Windows-x64-Portable.zip"
    $setupName = "PDF-Markdown-Studio-$version-Windows-x64-Setup.exe"
    $checksumName = "PDF-Markdown-Studio-$version-Windows-x64-SHA256SUMS.txt"
    $portablePath = Join-Path $artifactDirectory $portableName
    $setupPath = Join-Path $artifactDirectory $setupName
    $checksumPath = Join-Path $artifactDirectory $checksumName
    foreach ($oldArtifact in ($portablePath, $setupPath, $checksumPath)) {
        if (Test-Path -LiteralPath $oldArtifact) {
            Remove-Item -LiteralPath $oldArtifact -Force
        }
    }

    Compress-Archive -LiteralPath $appDirectory -DestinationPath $portablePath `
        -CompressionLevel Optimal
    Assert-ReleaseAssetSize $portablePath

    $releaseAssets = @($portablePath)
    if (-not $SkipInstaller) {
        $iscc = Resolve-Iscc
        Invoke-Checked $iscc `
            "/DMyAppVersion=$version" `
            "/DSourceDir=$appDirectory" `
            "/DOutputDir=$artifactDirectory" `
            "desktop\windows\installer.iss"
        if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
            throw "Inno Setup 未生成预期安装包：$setupPath"
        }
        Assert-ReleaseAssetSize $setupPath
        $releaseAssets += $setupPath
    }

    $checksumLines = foreach ($asset in $releaseAssets) {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $asset).Hash.ToLowerInvariant()
        "$hash  $([System.IO.Path]::GetFileName($asset))"
    }
    [System.IO.File]::WriteAllLines(
        $checksumPath,
        [string[]]$checksumLines,
        [System.Text.UTF8Encoding]::new($false)
    )
    Assert-ReleaseAssetSize $checksumPath

    Write-Host ""
    Write-Host "Windows 构建完成："
    foreach ($asset in ($releaseAssets + $checksumPath)) {
        $item = Get-Item -LiteralPath $asset
        Write-Host ("  {0} ({1:N1} MiB)" -f $item.FullName, ($item.Length / 1MB))
    }
    Write-Host "  Edition: $Edition"
    Write-Host "  Full 版含 Docling 运行时，但不含 Hugging Face 离线模型。"
}
finally {
    Pop-Location
}
