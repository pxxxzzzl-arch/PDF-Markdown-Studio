# 构建与发布

桌面包包含 React 前端、Python 转换服务和原生窗口壳。PyInstaller 产物与目标操作系统绑定，因此 Windows 包必须在 Windows 构建，macOS 包必须在 macOS 构建。

## Windows 10/11 x64

### 构建环境

- Windows 10 1809+ 或 Windows 11 x64
- Python 3.12 x64
- Node.js 20.19+ 或 22.12+（推荐 Node.js 22 LTS）
- PowerShell
- 用于生成安装版的 Inno Setup 6

首次准备源码环境：

```powershell
npm --prefix frontend ci
npm --prefix frontend run build -- --mode desktop
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[primary,desktop-build,windows-desktop]"
```

构建桌面应用、便携包和安装器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_app.ps1
```

0.9.1 Release 应包含：

```text
PDF-Markdown-Studio-0.9.1-Windows-x64-Setup.exe
PDF-Markdown-Studio-0.9.1-Windows-x64-Portable.zip
PDF-Markdown-Studio-0.9.1-Windows-x64-SHA256SUMS.txt
```

安装版适合普通用户；便携版必须完整解压后运行，不能直接在 ZIP 预览窗口内启动。两种版本都应从同一次已验证构建产生，避免前端、后端或版本号不一致。

默认 `full` 版本包含 Docling 运行时，但不封装 Hugging Face 离线模型，因此第一次使用 Docling 转换仍需联网下载模型。Setup 会运行随包的 WebView2 bootstrapper；Portable 目录也带有该 bootstrapper，可在运行时缺失时手动执行。

Windows 构建当前未使用商业代码签名证书。发布前应在干净的 Windows 10/11 x64 环境验证 SmartScreen 提示、安装、卸载、便携启动、文件选择、单文件/批量转换、下载和退出。

## macOS

### 构建环境

- Apple Silicon Mac
- macOS 14 或更高版本
- Python 3.11–3.13
- Node.js 20.19+ 或 22.12+（推荐 Node.js 22 LTS）
- Xcode Command Line Tools

准备源码环境后执行：

```bash
make setup
make macos-app
```

输出：

```text
dist/PDF Markdown Studio.app
dist/PDF-Markdown-Studio-<version>-macOS-arm64.zip
```

0.9.1 GitHub Release 应包含：

```text
PDF-Markdown-Studio-0.9.1-macOS-arm64.zip
PDF-Markdown-Studio-0.9.1-macOS-arm64-SHA256SUMS.txt
```

默认构建 `full` 版本并封装 Docling。只需要 Native 轻量引擎时：

```bash
PDFMD_DESKTOP_EDITION=lite make macos-app
```

完整版本默认要求本机已有经过验证的 Docling 模型缓存，并把模型复制进应用以供离线使用。缓存不在默认位置时：

```bash
PDFMD_MODEL_CACHE_SOURCE=/path/to/huggingface make macos-app
```

如需生成较小、首次使用时允许下载模型的包：

```bash
PDFMD_BUNDLE_MODELS=0 make macos-app
```

反复离线构建时可复用已测试的源码环境：

```bash
.venv/bin/python -m pip install -e '.[desktop-build]'
PDFMD_DESKTOP_REUSE_SOURCE_ENV=1 make macos-app
```

脚本目前只执行 ad-hoc 签名，未使用 Apple Developer ID，也未公证。对外分发前应补齐正式签名、notarization 和干净机器验证。

`.github/workflows/macos-release.yml` 使用固定的 `macos-14` 原生 arm64 runner、Python 3.12、Node.js 22 和 Docling 2.114.0。它会下载并验证三个固定版本的离线模型，构建 `full` 包，检查应用版本、Mach-O 架构、ad-hoc 签名、内置模型、内置服务健康状态和 2 GiB Release 限制，再生成 SHA-256 文件。带有 `[desktop-release]` 的 `main` 提交会同时触发 Windows 与 macOS 0.9.1 构建；普通 `main` 提交不会执行桌面发行任务。两个平台使用独立并发组，并在 Release 已由另一平台创建时安全复用，避免互相取消或覆盖资产。

公开 Release 当前仍是 ad-hoc 签名且未公证的预览构建。正式分发还需要 Developer ID Application、Hardened Runtime、`notarytool` 公证和凭证装订；在这些凭据配置前，不得把包描述为 Apple 已验证版本。

## 前端与 wheel

前端源码不会自动进入已经存在的 wheel、`.app` 或 `.exe`。每次发行都必须先构建桌面模式前端：

```bash
npm --prefix frontend run build -- --mode desktop
```

构建 wheel：

```bash
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
```

Hatch 会把 `frontend/dist` 放入 wheel 的 `pdfmd/web`，因此必须确认 wheel 中包含当前界面。

## Release 检查清单

1. 确认 `pyproject.toml`、`src/pdfmd/__init__.py`、前端包和安装包版本一致。
2. 运行 Python 测试、Ruff 和桌面模式前端构建。
3. 分别在目标 Windows 与 macOS 机器构建，不能把另一平台的 PyInstaller 产物改名发布。
4. 在干净账户或虚拟机执行安装版和便携版烟雾测试。
5. 验证软件只监听回环地址，公开 API 不返回本机绝对路径，外部图片不会在预览中自动加载。
6. 验证单文件、20 文件队列、部分批量下载、全选下载、失败重试和任务删除。
7. 核对安装包内前端、服务、模型和版本；计算并发布 SHA-256。
8. 创建版本 tag 与 GitHub Release，上传二进制资产；不要把 `dist/` 提交到 Git。
9. Release 真正可下载后，再确认 README 的下载入口和资产名一致。

GitHub 自动生成的 “Source code” ZIP 只是源码快照，不是 Windows 或 macOS 桌面安装包。
