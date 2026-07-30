# 故障排查

## Windows 桌面版

### SmartScreen 显示“未知发布者”

当前 Windows 包尚未进行商业代码签名。请确认文件来自本项目 [GitHub Releases](https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio/releases/latest)，再选择“更多信息 → 仍要运行”。来源不明时不要绕过提示。

### 便携版无法启动或提示缺少文件

不要在 ZIP 预览窗口中直接运行。先把整个 `PDF-Markdown-Studio-0.9.0-Windows-x64-Portable.zip` 解压到普通目录，再双击 `PDF Markdown Studio.exe`；不要只复制单个 EXE。

如果安全软件隔离了 PyInstaller 运行文件，请先核对 Release 来源和 SHA-256，再检查隔离记录。不要通过关闭全部安全防护来解决。

### 双击后长时间没有窗口

首次启动需要解压或加载较多 Python 依赖，可能比后续启动慢。等待一段时间后仍无窗口：

1. 在任务管理器结束遗留的 `PDF Markdown Studio` 和内置服务进程。
2. 确认磁盘仍有足够空间，并把便携包解压到当前用户有写权限的目录。
3. 查看 `%LOCALAPPDATA%\PDF Markdown Studio\logs\` 中最新的日志文件。
4. 重新启动；若仍失败，保留日志并在 GitHub Issue 中附上 Windows 版本、包类型和复现步骤。

### 提示缺少 Microsoft Edge WebView2 Runtime

Windows 桌面窗口依赖 WebView2。Setup 会运行随包的微软 bootstrapper，Portable 目录也带有该文件；安装失败时，请完成 Windows Update 或从 Microsoft 官方渠道安装 WebView2 Runtime，再重启软件。企业网络可能需要管理员放行安装程序。

### 安装版与便携版该选哪一个

- 安装版：有开始菜单和卸载入口，适合日常使用。
- 便携版：不写入系统安装目录，适合临时使用或无安装权限场景；任务数据仍会写到当前用户的应用数据目录。

## macOS

### Releases 中找不到 macOS 安装包

当前尚未发布 macOS 二进制包。请使用 README 的源码本地网页版，或在 Apple Silicon Mac 上按照[构建说明](building.md#macos)自行生成。

### 自行构建的应用提示无法验证开发者

当前脚本生成 ad-hoc 签名、未公证的应用。确认应用确由自己从本仓库源码构建后，可在 Finder 中右键应用并选择“打开”。不要对来源不明的应用绕过 Gatekeeper。

## 本地网页版

### 提示“首次运行需要完成安装”

在项目根目录执行：

```bash
make setup
```

如果电脑中有多个 Python 版本，可明确指定：

```bash
PYTHON=python3.12 make setup
```

### Python 版本不受支持

项目要求 Python 3.11–3.13。确认版本后重新创建 `.venv` 并安装；不要在已有不兼容虚拟环境上继续叠加依赖。

```bash
python3 --version
```

### 页面没有自动打开

保持服务终端开启，手动访问 <http://127.0.0.1:8000>，并检查健康接口：

```bash
curl http://127.0.0.1:8000/api/health
```

返回包含 `"status":"ok"` 表示服务正常。

### 8000 端口被占用

macOS 的 `start.command` 可以改用其他端口：

```bash
PDFMD_PORT=8001 ./start.command
```

然后访问 <http://127.0.0.1:8001>。通用命令：

```bash
PDFMD_PORT=8001 .venv/bin/pdfmd-server
```

Windows PowerShell：

```powershell
$env:PDFMD_PORT = "8001"
.\.venv\Scripts\pdfmd-server.exe
```

### 直接打开 frontend/index.html 后无法转换

这是预期行为。页面需要通过 FastAPI 服务访问同源 API；请运行 `make run`，再打开 <http://127.0.0.1:8000>。

### 修改前端后页面没有变化

重新构建并重启服务：

```bash
npm --prefix frontend run build
```

桌面包还需要重新执行对应平台的完整打包，旧 `.app` 或 `.exe` 不会自动更新。

## 转换问题

### 首次转换很慢

Docling 首次使用可能下载并加载模型。保持网络连接和足够磁盘空间；后续使用通常会复用缓存。发布版若已封装离线模型，则无需下载，但首次加载仍可能较慢。

### 扫描件没有文字或质量较低

优先使用 `Docling` 和“自动识别”。需要更强中文扫描兜底时，从源码安装可选 OCR 依赖：

```bash
.venv/bin/python -m pip install -e '.[ocr]'
```

低分辨率、手写体和复杂跨页表格仍可能需要人工校对。

### 显示 Native 降级或“未通过”

这表示主引擎不可用或结果未通过质量门控，软件保留了可用的降级结果但没有把它标成高质量成功。查看“质量”页签中的逐页问题，必要时安装 Docling/OCR、调整选项后重新转换。

### 代码块仍然混乱

查看质量报告中的代码语法、围栏和行号问题。原生文字代码比截图 OCR 更可靠；低分辨率截图、复杂背景和跨栏布局可能需要人工修订。实验性视觉代码增强默认关闭，因为它可能让混合中英文课件退化。

### 批量转换中只有部分任务失败

成功任务仍可单独或批量下载。对失败项使用单独重试，并查看对应错误；不要重复提交已经成功的文件。

## 数据与隐私

本地服务默认只监听 `127.0.0.1`，不会把 PDF 上传到 GitHub。源码运行时数据默认位于项目的 `data/`；Windows 桌面版使用 `%LOCALAPPDATA%\PDF Markdown Studio`，macOS 桌面版使用 `~/Library/Application Support/PDF Markdown Studio`。

删除任务会同时删除对应上传 PDF、Markdown、JSON、质量报告、图片和 ZIP。运行中的任务不能删除，退出应用会把未完成任务标记为失败，需要重新提交。

报告问题时不要上传含隐私或机密信息的原始 PDF。请先制作可公开的最小复现文件，并附上版本、操作系统、所选引擎、质量报告摘要和完整复现步骤。
