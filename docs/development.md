# 开发指南

本文面向从源码运行、调试或集成 PDF Markdown Studio 的开发者。普通 Windows 用户应优先下载 README 中的安装版或便携版。

## 环境要求

- Python 3.11–3.13
- Node.js 20 或更高版本
- Git
- macOS / Linux：Make
- 推荐至少 8 GB 内存；完整 Docling 模型建议 16 GB

Docling 和 PaddleOCR 首次运行时可能下载模型。只使用 `Native` 引擎时不需要大型模型，但复杂版式、扫描件和表格的效果会降低。

## 一键安装与启动

macOS、Linux 或 WSL：

```bash
git clone https://github.com/pxxxzzzl-arch/PDF-Markdown-Studio.git
cd PDF-Markdown-Studio
make setup
make run
```

`make setup` 会构建前端、创建 `.venv`，并安装开发依赖和 Docling 主引擎。浏览器访问 <http://127.0.0.1:8000>。

macOS 安装完成后，也可以双击根目录的 `start.command`。脚本会执行健康检查、打开浏览器，并在终端窗口关闭时停止服务。

## Windows PowerShell 源码环境

在 PowerShell 中进入项目根目录后执行：

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[primary,dev]"
.\.venv\Scripts\pdfmd-server.exe
```

浏览器访问 <http://127.0.0.1:8000>。如果没有 `py` 启动器，可把 `py -3.12` 换成一个实际指向 Python 3.11–3.13 的 `python` 命令。

## 可选 OCR 依赖

中文扫描件需要额外兜底时，可安装 PaddleOCR。该依赖较大，建议按需安装：

```bash
.venv/bin/python -m pip install -e '.[ocr]'
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"
```

## 前后端分离调试

后端热重载：

```bash
source .venv/bin/activate
uvicorn pdfmd.main:app --reload --port 8000
```

前端开发服务器：

```bash
npm --prefix frontend run dev
```

打开 <http://127.0.0.1:5173>。Vite 会把 `/api` 请求代理到本机 8000 端口。

前端源码变化不会自动进入已构建的 wheel 或桌面包；发行前必须重新运行桌面模式构建：

```bash
npm --prefix frontend run build -- --mode desktop
```

## CLI

安装后可直接转换：

```bash
pdfmd input.pdf -o output/result
```

常用选项：

```text
--engine docling|native|paddleocr
--fallback-engine paddleocr|native
--ocr auto|always|never
--code-enrichment
--password PDF_PASSWORD
--no-images
--page-markers
--no-quality-fallback
--minimum-quality-score 0-100
--engine-status
--debug
```

质量门控未通过时，CLI 会保留全部结果并返回退出码 `2`；普通执行错误返回 `1`。

## REST API

服务启动后，交互式文档位于 <http://127.0.0.1:8000/docs>。

提交文件：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -F 'file=@document.pdf' \
  -F 'options_json={"primary_engine":"docling","ocr_mode":"auto"}'
```

查询任务和下载结果：

```bash
curl http://127.0.0.1:8000/api/jobs/JOB_ID
curl -O http://127.0.0.1:8000/api/jobs/JOB_ID/archive
curl -X DELETE http://127.0.0.1:8000/api/jobs/JOB_ID
```

批量结果：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/archive \
  -H 'Content-Type: application/json' \
  -d '{"job_ids":["JOB_ID_1","JOB_ID_2"]}' \
  -o pdf-markdown-batch.zip
```

运行中的任务不能删除。完成或失败的任务删除后，其上传 PDF、结果和资源会一并移除。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `PDFMD_HOST` | `127.0.0.1` | 本地服务监听地址 |
| `PDFMD_PORT` | `8000` | 本地服务监听端口 |
| `PDFMD_DATA_DIR` | `./data` | SQLite、上传文件和任务结果目录 |
| `PDFMD_MAX_FILE_SIZE` | `209715200` | 单文件最大字节数（200 MB） |
| `PDFMD_MAX_PAGES` | `500` | 单文件最大页数 |
| `PDFMD_MAX_BATCH_FILES` | `20` | Web 单批文件数，最大可设为 100 |
| `PDFMD_MAX_WORKERS` | `1` | 同时运行的转换任务数 |
| `PDFMD_JOB_TTL_HOURS` | `72` | 预留的任务保留时间配置 |

模型加载占用较多内存。单机建议从一个 worker 开始，需要扩容时优先增加隔离进程。

## 输出与处理流程

```text
PDF
 └─ 安全检查与页面分类
     └─ Parser Adapter（Docling / PaddleOCR / Native）
         └─ ParsedDocument 统一 JSON
             ├─ Quality Gate → 低质量页重新解析
             └─ Markdown Renderer
                 ├─ document.md
                 ├─ document.json
                 ├─ quality-report.json
                 ├─ manifest.json
                 └─ assets/
```

最终 Markdown 不依赖上游引擎的内部对象。普通表格通过 GFM 行列和转义校验后输出为 Markdown；复杂合并表格或不可靠结构使用 HTML table。图片使用相对路径。

质量门控会检查非空页空输出、文本覆盖率、乱码、重复文本、代码语法和围栏、标题层级、表格结构及资源异常。总分同时考虑页面均分和低分页面，避免严重坏页被长文档平均值掩盖。主引擎结果较差时会按页比较兜底引擎，但降级不会被伪装成成功。

## Docker

```bash
docker compose up --build
```

浏览器访问 <http://127.0.0.1:8000>。任务数据保存在 `pdfmd-data` volume，端口仅映射到本机回环地址。

## 测试与检查

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
npm --prefix frontend run build -- --mode desktop
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests scripts
npm --prefix frontend run build -- --mode desktop
```

真实 Docling 回归样本：

```bash
python scripts/generate_sample_pdf.py tmp/pdfs/visual-regression.pdf
python scripts/generate_scanned_sample_pdf.py tmp/pdfs/scanned-regression.pdf
pdfmd tmp/pdfs/visual-regression.pdf -o output/docling --engine docling
pdfmd tmp/pdfs/scanned-regression.pdf -o output/scanned --engine docling --ocr auto
```

构建 wheel 前必须先构建前端：

```bash
npm --prefix frontend run build
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
```

依赖升级前，应把真实失败样本匿名化后加入回归集，再运行完整测试。
