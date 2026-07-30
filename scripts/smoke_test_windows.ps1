[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDirectory,

    [Parameter(Mandatory = $true)]
    [string]$SamplePdf,

    [Parameter(Mandatory = $true)]
    [string]$WorkDirectory,

    [bool]$RequireDocling = $true,

    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "Windows 冒烟测试只能在 Windows 上运行。"
}

$appDirectoryPath = [System.IO.Path]::GetFullPath($AppDirectory)
$samplePdfPath = [System.IO.Path]::GetFullPath($SamplePdf)
$workDirectoryPath = [System.IO.Path]::GetFullPath($WorkDirectory)
$executable = Join-Path $appDirectoryPath "PDF Markdown Studio.exe"

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "缺少 Windows 应用入口：$executable"
}
if (-not (Test-Path -LiteralPath $samplePdfPath -PathType Leaf)) {
    throw "缺少冒烟测试 PDF：$samplePdfPath"
}
if ([System.IO.Path]::GetExtension($samplePdfPath).ToLowerInvariant() -ne ".pdf") {
    throw "冒烟测试输入必须是 PDF 文件。"
}

if ($RequireDocling) {
    $doclingPackage = Join-Path $appDirectoryPath "_internal\docling"
    if (-not (Test-Path -LiteralPath $doclingPackage -PathType Container)) {
        throw "Full Windows 包缺少 Docling 运行时：$doclingPackage"
    }
}

# IMAGE_SUBSYSTEM_WINDOWS_GUI (2) proves that double-clicking the executable
# does not create a console window.
$executableBytes = [System.IO.File]::ReadAllBytes($executable)
if ($executableBytes.Length -lt 256) {
    throw "Windows 应用入口不是有效的 PE 文件。"
}
$peOffset = [System.BitConverter]::ToInt32($executableBytes, 0x3c)
$subsystemOffset = $peOffset + 24 + 68
if ($subsystemOffset + 2 -gt $executableBytes.Length) {
    throw "无法读取 Windows 应用入口的 PE Subsystem。"
}
$subsystem = [System.BitConverter]::ToUInt16($executableBytes, $subsystemOffset)
if ($subsystem -ne 2) {
    throw "Windows 应用不是无控制台 GUI 可执行文件（Subsystem=$subsystem）。"
}

if (Test-Path -LiteralPath $workDirectoryPath) {
    Remove-Item -LiteralPath $workDirectoryPath -Recurse -Force
}
$null = New-Item -ItemType Directory -Path $workDirectoryPath
$smokeOutput = Join-Path $workDirectoryPath "result"
$appHome = Join-Path $workDirectoryPath "用户 数据"

$savedEnvironment = @{
    PDFMD_WINDOWS_HOME = $env:PDFMD_WINDOWS_HOME
    HF_HUB_OFFLINE = $env:HF_HUB_OFFLINE
    TRANSFORMERS_OFFLINE = $env:TRANSFORMERS_OFFLINE
}

try {
    $env:PDFMD_WINDOWS_HOME = $appHome
    # The default Full installer intentionally includes Docling itself but not
    # Hugging Face model snapshots. The Native smoke conversion must therefore
    # remain offline and deterministic.
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"

    $quotedPdf = '"' + $samplePdfPath.Replace('"', '\"') + '"'
    $quotedOutput = '"' + $smokeOutput.Replace('"', '\"') + '"'
    $arguments = (
        "--smoke-test {0} --smoke-output {1} --smoke-timeout {2}" -f
        $quotedPdf,
        $quotedOutput,
        $TimeoutSeconds
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $executable
    $startInfo.Arguments = $arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "无法启动 Windows 应用冒烟测试。"
    }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try {
            $process.Kill()
        }
        catch {
            Write-Warning "无法终止超时的冒烟测试进程：$($_.Exception.Message)"
        }
        throw "Windows 应用冒烟测试在 $TimeoutSeconds 秒内未退出。"
    }
    if ($process.ExitCode -ne 0) {
        $logFile = Join-Path $appHome "logs\desktop.log"
        throw "Windows 应用冒烟测试失败（退出码 $($process.ExitCode)）；日志：$logFile"
    }

    $markdown = @(Get-ChildItem -LiteralPath $smokeOutput -Filter "*.md" -File)
    $archives = @(
        Get-ChildItem -LiteralPath $smokeOutput -Filter "*-markdown.zip" -File
    )
    if ($markdown.Count -ne 1 -or $markdown[0].Length -eq 0) {
        throw "冒烟测试没有生成唯一且非空的 Markdown 文件。"
    }
    if ($archives.Count -ne 1 -or $archives[0].Length -lt 4) {
        throw "冒烟测试没有生成唯一且有效的 ZIP 结果包。"
    }
    $zipHeader = [System.IO.File]::ReadAllBytes($archives[0].FullName)[0..1]
    if ($zipHeader[0] -ne 0x50 -or $zipHeader[1] -ne 0x4B) {
        throw "冒烟测试结果包不是 ZIP 文件。"
    }
}
finally {
    $env:PDFMD_WINDOWS_HOME = $savedEnvironment.PDFMD_WINDOWS_HOME
    $env:HF_HUB_OFFLINE = $savedEnvironment.HF_HUB_OFFLINE
    $env:TRANSFORMERS_OFFLINE = $savedEnvironment.TRANSFORMERS_OFFLINE
}

Write-Host "Windows packaged smoke test passed:"
Write-Host "  App: $executable"
Write-Host "  Markdown: $($markdown[0].FullName)"
Write-Host "  Archive: $($archives[0].FullName)"
