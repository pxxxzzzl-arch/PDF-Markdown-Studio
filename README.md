# PDF Markdown Studio 0.9.1

把 PDF 拖进本地软件，转换为结构清晰的 Markdown。文件、任务记录和转换结果默认只保存在你的电脑上。

## 下载与安装

| 系统 | 推荐下载 | 启动方式 |
| --- | --- | --- |
| **Windows 10/11 x64** | [打开最新 Release](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/latest) | 安装版或免安装便携版，支持 Windows 10 1809+ |
| **macOS 14+ Apple Silicon** | [下载 macOS 0.9.1](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/download/v0.9.1/PDF-Markdown-Studio-0.9.1-macOS-arm64.zip) | 完整解压后拖入“应用程序”，首次启动请右键选择“打开” |
| Linux / 其他系统 | [下载源码](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/archive/refs/heads/main.zip) | 运行本地网页版 |

只想在浏览器里使用？直接看[本地网页版启动说明](#从源码运行本地网页版)。网页和转换服务都运行在你的电脑上，不需要部署服务器或注册账号。

### Windows 10/11 x64

在 [Releases](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/latest) 页面选择一种：

- **安装版（推荐）**：下载 `PDF-Markdown-Studio-0.9.1-Windows-x64-Setup.exe`，双击后按向导安装。
- **便携版**：下载 `PDF-Markdown-Studio-0.9.1-Windows-x64-Portable.zip`，完整解压后双击 `PDF Markdown Studio.exe`，无需安装。

两种包都内含应用所需的 Python 服务、Docling 运行时和网页界面，不需要另外安装 Python、Node.js 或 Git。首次使用 Docling 时仍需联网下载模型；退出桌面窗口后，本地服务也会随之关闭。
便携版不会注册卸载项；更新时建议解压到新目录，不要覆盖正在运行的旧版本。

> 当前 Windows 包尚未进行商业代码签名，SmartScreen 可能显示“未知发布者”。请只从本项目 Releases 下载，并在核对来源后选择“更多信息 → 仍要运行”。若页面尚未出现上述文件，说明相应版本尚未完成发布，请先使用源码版。

### macOS

macOS 14 或更高版本的 Apple Silicon Mac（M1/M2/M3/M4 等）可直接使用：

1. 下载 [`PDF-Markdown-Studio-0.9.1-macOS-arm64.zip`](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/download/v0.9.1/PDF-Markdown-Studio-0.9.1-macOS-arm64.zip)，不要在 ZIP 预览窗口中运行。
2. 完整解压，把 `PDF Markdown Studio.app` 拖到“应用程序”文件夹。
3. 首次启动时在 Finder 中右键应用并选择“打开”。若 macOS 仍阻止启动，先尝试打开一次，再进入“系统设置 → 隐私与安全”，在安全提示旁选择“仍要打开”。

这是约 1.3 GB 的完整离线包，已包含应用所需的 Python 服务、网页界面、Docling 2.114.0 运行时和固定版本的离线模型，不需要另装 Python、Node.js 或 Git。可同时下载 [`PDF-Markdown-Studio-0.9.1-macOS-arm64-SHA256SUMS.txt`](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/download/v0.9.1/PDF-Markdown-Studio-0.9.1-macOS-arm64-SHA256SUMS.txt)，在下载目录执行以下命令并与校验文件比较：

```bash
shasum -a 256 PDF-Markdown-Studio-0.9.1-macOS-arm64.zip
```

> 当前公开包使用 ad-hoc 签名且尚未 Apple 公证，因此会出现上述 Gatekeeper 提示。请只从本项目 [v0.9.1 Release](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/tag/v0.9.1) 下载。Intel Mac 暂不支持，可改用下方本地网页版；开发者自行构建请参阅[构建与发布](docs/building.md#macos)。

## 4 步完成转换

1. 打开软件，把一个或多个 PDF 拖入上传区域。
2. 一般保持 `Docling` 和“自动识别”；简单文本 PDF 可选择更轻量的 `Native`。
3. 点击“开始转换”或“开始批量转换”，等待质量检查完成。
4. 查看“预览 / 源码 / 质量”，下载 `.md`、完整结果包，或勾选多个任务批量下载 ZIP。

最近任务保存在本机。删除任务前软件会再次确认；确认后，对应的上传文件、结果和资源也会从本机任务目录移除。

## 核心能力

- **本地优先**：Web 与桌面服务只监听回环地址，预览不会主动请求外部图片。
- **多类型 PDF**：识别原生文本、扫描件和混合文档，支持 Docling、Native 与可选 OCR 兜底。
- **结构恢复**：处理标题、段落、列表、表格、公式、图片，以及带缩进和跨页连续性的代码。
- **质量门控**：检查空页、覆盖率、乱码、重复、代码语法、表格结构和资源完整性，并按页选择更好的解析结果。
- **批量工作流**：单批最多 20 份 PDF，独立显示成功与失败，可全选或按需合并下载。
- **完整产物**：同时生成 Markdown、统一结构 JSON、逐页质量报告、清单和图片资源。

## 从源码运行本地网页版

本地网页版不是在线网站：它会在电脑上启动服务，再由浏览器访问
`http://127.0.0.1:8000`。PDF 和转换结果不会由本程序上传到 GitHub；使用期间需要保持
启动服务的终端窗口开启。

### macOS、Linux 或 WSL

首次使用需要 Python 3.11–3.13、Node.js 20.19+ 或 22.12+（推荐 Node.js 22 LTS）、
Git 和 Make：

```bash
git clone https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio.git
cd PDF-Markdown-Studio
make setup
make run
```

浏览器访问 <http://127.0.0.1:8000>。首次安装只需执行一次 `make setup`；以后进入项目目录运行 `make run` 即可。macOS 用户也可以直接双击根目录的 `start.command`，它会启动服务并自动打开浏览器。

### Windows PowerShell 源码版

普通 Windows 用户推荐直接下载前面的安装版或便携版。需要从源码运行网页时，请先安装
Python 3.11–3.13、Node.js 20.19+ 或 22.12+（推荐 Node.js 22 LTS）和 Git，然后在
PowerShell 中执行：

```powershell
git clone https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio.git
cd PDF-Markdown-Studio
npm --prefix frontend ci
npm --prefix frontend run build
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[primary]"
.\.venv\Scripts\pdfmd-server.exe --host 127.0.0.1 --port 8000
```

如果电脑没有 `py` 命令，可把 `py -3.12` 换成指向受支持版本的 `python`。安装完成后，
以后只需进入项目目录并运行最后一条 `pdfmd-server.exe` 命令。

启动成功后访问 <http://127.0.0.1:8000>；按 `Ctrl+C` 停止服务。若页面打不开，可先访问
<http://127.0.0.1:8000/api/health> 检查服务状态，或按照[故障排查](docs/troubleshooting.md#本地网页版)处理。

不要直接双击 `frontend/index.html`，它必须通过本地服务访问转换 API。Windows 原生开发环境、前后端分离调试、CLI、Docker、REST API 和配置项见[开发指南](docs/development.md)。

## 输出内容

完整结果包包含：

```text
document.md
document.json
quality-report.json
manifest.json
assets/
```

普通表格优先输出 GFM；复杂合并表格会使用 HTML table。图片使用相对路径。质量检查未通过时仍会保留结果，并明确提示需要人工复核。

## 构建与测试

桌面包必须在目标系统上构建：

```powershell
# Windows 10/11 x64
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_app.ps1
```

```bash
# Apple Silicon macOS
make macos-app
```

提交前至少运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
npm --prefix frontend run build -- --mode desktop
```

Windows PowerShell 对应命令、安装器与便携包制作、模型封装、签名和 Release 检查清单见[构建与发布](docs/building.md)。常见启动或转换问题见[故障排查](docs/troubleshooting.md)。

## 已知限制

- PDF 是固定版面格式，缺少语义标记时无法保证原始阅读顺序完全正确。
- 手写体、嵌套或跨页表格、低分辨率扫描件仍可能需要人工校对。
- `Native` 是无需大型模型的降级引擎，不替代版面分析或 OCR。
- 当前任务队列面向本地单机；多用户部署应使用隔离的转换进程队列。
- Windows 包尚未进行商业代码签名；macOS Release 与自行构建包目前仅为 ad-hoc 签名且未公证。

## 开源许可与致谢

项目源码采用 [MIT License](LICENSE)。

核心解析依赖与设计参考包括 [Docling](https://github.com/docling-project/docling)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)、[pypdf](https://github.com/py-pdf/pypdf)、[MinerU](https://github.com/opendatalab/MinerU)、[Marker](https://github.com/datalab-to/marker) 和 [PyMuPDF4LLM](https://github.com/pymupdf/pymupdf4llm)。第三方代码与模型适用各自许可证；发布或商用前请核对实际打包内容。
桌面发行包包含的主要组件及许可证摘要见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 更多文档

- [开发指南](docs/development.md)
- [构建与发布](docs/building.md)
- [故障排查](docs/troubleshooting.md)
