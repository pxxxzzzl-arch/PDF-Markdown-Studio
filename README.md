# PDF Markdown Studio 0.8.0

一个本地优先、带质量门控的 PDF 转 Markdown 工具。它不会把“进程未报错”等同于“转换正确”：每次转换同时产出 Markdown、统一结构 JSON、逐页质量报告和资源文件，并可对低质量页面切换解析引擎重新处理。

## 当前能力

- 原生文本、扫描件和混合 PDF 分类
- 文件头、大小、页数、加密和损坏文件检查
- Docling 主解析、可选的 PaddleOCR/PP-StructureV3 OCR 兜底
- 无 AI 依赖时使用 pypdf 的轻量 Native 引擎
- 标题、段落、列表、表格、公式、代码和图片的统一结构模型
- 原生 PDF 代码版面恢复：支持稳定行号栏和高置信度无行号代码，保留缩进、空行与跨页连续性，并拆开误并入代码的对齐表格
- 嵌入截图代码离线恢复：用包内 RapidOCR 模型按坐标重建行号、空行和缩进
- Docling 视觉代码增强作为实验选项提供；默认关闭，避免混合中英文课件出现二次 OCR 退化
- 对 Docling、Native、截图 OCR、跨页代码和 Raw Markdown 围栏统一执行 Python/JSON 语法检查
- 代码质量指标单独展示逻辑代码组、已验证代码、异常代码、未标注语言和有效率
- 根据编号深度恢复语义标题层级，规范列表、题注与中文换行空格
- 无编号视觉续行自动拼回原代码行，避免长代码被 PDF 换行破坏语义
- GFM 表格会校验转义、行列数与结构元数据；不可靠时自动使用 HTML table
- 非空页空输出、文本覆盖率、乱码、重复文本、代码语法、围栏闭合、代码塌缩、标题扁平化、表格结构和资源异常检测
- 总分综合页面均分与最低 10% 页面均分，避免长文档中单页严重损坏被平均值掩盖
- 按页比较主引擎和兜底引擎结果，保留质量较高版本
- 同一 React 界面同时服务浏览器和 macOS 原生窗口；桌面版采用固定工具栏、任务侧栏、设置/结果分栏和分区内滚动
- 异步进度轮询和持久化任务历史；应用重启会中止未完成任务，需要重新提交
- Markdown 安全预览和失败不跳页的结果下载
- 单文件与批量转换共用一个文件队列；每批最多 20 份、限流上传、独立失败与总体进度监控
- 批量结果可逐项勾选或全选，由本机合并为一个 ZIP 下载，任务目录和清单彼此隔离
- 历史任务请求隔离、批量提交失败项单独重试、转换失败不影响其他任务
- 降级状态提示和键盘/减弱动画支持
- 任务结果可二次确认后从本机删除；API 不返回任何本机绝对路径
- 主引擎瞬态中断自动重试一次；仍失败时降级到 Native 并明确标记“未通过”，不再用高分掩盖降级
- 前端不加载外部字体，Markdown 中的外部图片也不会自动联网请求
- CLI、REST API、SQLite 任务记录和 Docker 部署入口

## 最快开始

### macOS 桌面版（推荐）

Apple Silicon 且系统为 macOS 14 或更高版本时：

1. 解压 `dist/PDF-Markdown-Studio-0.8.0-macOS-arm64.zip`。
2. 将 `PDF Markdown Studio.app` 拖入“应用程序”文件夹。
3. 双击应用；后台服务会自动选择本机空闲端口，关闭应用时也会自动退出。

完整桌面包已经包含 Python、前端、Docling 运行环境以及约 1.1 GB 的版面、表格和
代码公式模型，不需要另外安装 Python、Node.js，也不需要在第一次转换时联网下载
模型。应用本体解压后约 2 GB；任务、上传文件和转换结果位于
`~/Library/Application Support/PDF Markdown Studio`。

当前生成的是本地测试用的 ad-hoc 签名包，适合在本机直接使用，但尚未用 Apple
Developer ID 签名和公证。要分发给其他用户，应先完成正式签名与 notarization，
避免触发 macOS 的“无法验证开发者”安全提示。

### 源码启动

在 macOS、Linux 或 WSL 中执行：

```bash
make setup
make run
```

浏览器打开 <http://127.0.0.1:8000>。macOS 首次完成 `make setup` 后，也可以直接双击项目根目录的 `start.command`；关闭它打开的终端窗口即可停止服务。

`make setup` 会依次构建前端、创建 `.venv`、安装测试工具和 Docling。首次使用 Docling 时可能下载模型文件。

## 构建桌面安装包

在 Apple Silicon Mac 上准备好 Python 3.11–3.13、Node.js 20+ 和 Xcode Command
Line Tools 后执行：

```bash
make macos-app
```

产物会写入 `dist/`。默认构建完整版本；只需要 Native 轻量解析时可以执行：

```bash
PDFMD_DESKTOP_EDITION=lite make macos-app
```

轻量版体积更小，但不包含 Docling，复杂版式、表格和扫描件的转换质量会明显低于完整版本。

完整版本默认把当前构建环境中的 Docling 模型缓存一并封装，并在运行时强制离线
读取。构建前应至少成功执行过一次启用 Docling 的转换。缓存不在默认位置时可通过
`PDFMD_MODEL_CACHE_SOURCE` 指定 Hugging Face 根目录；只想生成允许首次联网下载
模型的较小安装包时，可执行：

```bash
PDFMD_BUNDLE_MODELS=0 make macos-app
```

需要反复离线重建时，可先在当前虚拟环境安装打包工具，然后复用这套已经测试过的依赖：

```bash
.venv/bin/python -m pip install -e '.[desktop-build]'
PDFMD_DESKTOP_REUSE_SOURCE_ENV=1 make macos-app
```

## 设计

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

最终 Markdown 不直接依赖任何上游引擎的内部对象。更换或升级引擎时，只需更新对应适配器和回归样本。

## 环境要求

- Python 3.11–3.13
- Node.js 20 或更高版本（仅构建前端需要）
- 推荐 8 GB 以上内存；大型 AI 模型建议 16 GB
- Docling 和 PaddleOCR 首次运行可能下载模型

## 本地安装

不使用 Make 时，先构建将随安装包分发的前端：

```bash
cd frontend
npm ci
npm run build
cd ..
```

再创建虚拟环境并安装主引擎：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[primary,dev]'
```

如需中文扫描件兜底，再安装 PaddleOCR。该依赖体积较大，建议在确认设备环境后单独安装：

```bash
python -m pip install -e '.[ocr]'
```

启动本地应用：

```bash
source .venv/bin/activate
pdfmd-server --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。API 文档位于 <http://127.0.0.1:8000/docs>。

开发时也可以分别启动后端和 Vite：

```bash
source .venv/bin/activate
uvicorn pdfmd.main:app --reload --port 8000
```

```bash
cd frontend
npm run dev
```

### Docker

容器入口仅绑定本机 `127.0.0.1:8000`，任务数据保存在 Docker volume：

```bash
docker compose up --build
```

## CLI

```bash
pdfmd input.pdf -o output/result
```

常用选项：

```text
--engine docling|native|paddleocr
--fallback-engine paddleocr|native
--ocr auto|always|never
--code-enrichment  # 实验性视觉代码增强；默认关闭
--password PDF_PASSWORD
--no-images
--page-markers
--no-page-markers  # 兼容旧版；不输出分页注释现为默认行为
--no-quality-fallback
--minimum-quality-score 0-100
--engine-status
--debug
```

示例：

```bash
pdfmd contract.pdf -o output/contract --engine docling --ocr auto
```

CLI 在质量检查未通过时仍会保留全部结果，但返回退出码 `2`，便于批处理系统阻止低质量文档静默进入下游。

## REST API

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
curl -X POST http://127.0.0.1:8000/api/jobs/archive \
  -H 'Content-Type: application/json' \
  -d '{"job_ids":["JOB_ID_1","JOB_ID_2"]}' \
  -o pdf-markdown-batch-2.zip
curl -X DELETE http://127.0.0.1:8000/api/jobs/JOB_ID
```

运行中的任务不能删除。完成或失败的任务删除后，其上传 PDF、结果、图片和 ZIP 会一起从本机移除。

## 输出说明

```text
output/
├── document.md            # 最终 Markdown
├── document.json          # 带页码、类型、坐标和来源引擎的统一结构
├── quality-report.json    # 总分、逐页分数、问题和兜底记录
├── manifest.json          # 文件摘要、引擎和产物清单
└── assets/                # 有图片时生成，保存提取资源
```

复杂合并单元格以及未通过 GFM 行列/转义校验的表格使用 HTML table，验证通过的普通表格保留为 GFM。这遵循 Docling 的建议：Markdown 无法完整表达跨行、跨列表格时使用 HTML 或结构化 JSON。图片始终使用相对路径。清洁输出默认不含分页注释；需要页级定位时可启用 `--page-markers`，以 `<!-- page: N -->` 写入且不影响 Markdown 阅读器渲染。

### 代码质量门控

- 先按 `continues_previous` 合并逻辑代码，再渲染 Markdown；分页注释放在围栏外，不会切断代码。
- 语言判断综合 Docling 来源、完整跨页文本和保守语法信号，续页开头的局部字典不会再把整段 Python 误判为 JSON。
- Python 使用 `ast.parse`，JSON 使用标准解析器；检查覆盖结构化代码块和 Raw Markdown 围栏。
- `AIMessage(...)` 等 SDK 返回对象被识别为输出展示，不冒充可执行源代码；截图 OCR 仍保留人工复核提示。
- Docling 置信度、页面均分、最低 10% 页面均分和代码有效率同时写入质量报告。置信度高不等于代码字符一定正确。
- 只有具有括号上下文和相邻语句证据时才修复 OCR 的闭合符号，避免把合法的 `c`/`C` 变量误删。

## 配置

环境变量：

| 变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `PDFMD_HOST` | `127.0.0.1` | 本地服务监听地址 |
| `PDFMD_PORT` | `8000` | 本地服务监听端口 |
| `PDFMD_DATA_DIR` | `./data` | SQLite、上传文件和任务结果目录 |
| `PDFMD_MAX_FILE_SIZE` | `209715200` | 最大上传字节数（200 MB） |
| `PDFMD_MAX_PAGES` | `500` | 单文件最大页数 |
| `PDFMD_MAX_BATCH_FILES` | `20` | Web 界面单批最大文件数（最大可设为 100） |
| `PDFMD_MAX_WORKERS` | `1` | 同时运行的转换任务数 |
| `PDFMD_JOB_TTL_HOURS` | `72` | 预留的任务保留时间配置 |

生产环境建议保持单 worker 起步；模型加载会占用较多内存。需要扩容时，优先增加独立转换进程，而不是在同一进程中无限增加线程。

如果双击 `start.command` 后页面没有打开，先在终端确认健康接口：

```bash
curl http://127.0.0.1:8000/api/health
```

返回 `{"status":"ok", ...}` 说明服务正常，可以手动访问 <http://127.0.0.1:8000>。如果 8000 端口被其他程序占用，启动脚本会显示占用进程；也可以换一个端口启动：

```bash
PDFMD_PORT=8001 ./start.command
```

## 测试

```bash
source .venv/bin/activate
python -m pytest -q
ruff check src tests scripts
cd frontend && npm run build
```

自动化套件动态生成带文本、表格、图片、分页、中文和加密场景的 PDF，覆盖：

- PDF 类型与安全限制
- 统一模型、语义标题/列表规范化与 Markdown 渲染
- 带行号代码的缩进、空行、碎片合并、跨页连续性和截图坐标 OCR 恢复
- 空输出、文本覆盖、Python/JSON 语法、跨页代码、围栏闭合、代码塌缩、行号污染、标题扁平化、表格和图片等质量规则
- Native 端到端转换
- SQLite 不保存 PDF 密码
- 上传大小限制、非法引擎、文件名净化和资源路径穿越防护
- 上传、轮询、质量报告、白名单 ZIP 下载、DELETE CORS 和任务删除 API
- 连续多文件排队、任务 ID/输出目录隔离和本地同源批量提交
- 输出目录复用、OCR 临时文件失败清理和本地启动脚本兼容性
- CLI 成功/失败退出码、无堆栈错误信息和版本输出
- 主引擎不可用、瞬态重试、诚实降级、按页兜底采用和拒绝等质量路径

另有两个真实 Docling 集成样本：

```bash
python scripts/generate_sample_pdf.py tmp/pdfs/visual-regression.pdf
python scripts/generate_scanned_sample_pdf.py tmp/pdfs/scanned-regression.pdf
pdfmd tmp/pdfs/visual-regression.pdf -o output/docling --engine docling
pdfmd tmp/pdfs/scanned-regression.pdf -o output/scanned-docling --engine docling --ocr auto
```

扫描样本只有整页图片、没有 PDF 文本层，用于确认 OCR、中文、表格、阅读顺序和页面末尾均不会静默丢失。

构建可分发 wheel 前必须先执行前端构建。Hatch 会把 `frontend/dist` 收进 `pdfmd/web`，安装后的 `pdfmd-server` 因而包含完整界面：

```bash
python -m pip wheel --no-deps --wheel-dir dist .
```

在升级 Docling、PaddleOCR 或 pypdf 前，应先把实际失败文件匿名化后加入回归集，再运行全量测试。公开基准可参考 [OmniDocBench](https://github.com/opendatalab/OmniDocBench)，但真实业务样本仍是最终验收依据。

## 引擎与许可证

- [Docling](https://github.com/docling-project/docling)：代码 MIT；模型许可证需分别核对。
- [Marker](https://github.com/datalab-to/marker)：可参考其独立 CodeProcessor/TextProcessor 后处理架构。
- [MinerU](https://github.com/opendatalab/MinerU)：可参考其段落拆分和按内容类型生成 Markdown 的分层流程。
- [PyMuPDF4LLM](https://github.com/pymupdf/pymupdf4llm)：可参考其面向 LLM 的版面顺序与 Markdown 输出设计。
- [Docling 表格序列化说明](https://docling-project.github.io/docling/concepts/serialization/)：复杂表格优先 HTML/JSON。
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)：Apache-2.0。
- [pypdf](https://github.com/py-pdf/pypdf)：BSD-3-Clause。

源码和 wheel 不直接捆绑第三方大型模型权重；完整 macOS 桌面包会按构建脚本复制指定版本的 Docling 离线模型，并收录 RapidOCR 随包模型。发布闭源或收费产品前，仍应对实际打包的代码、模型和字体进行一次许可证清单审计。

## 已知边界

- PDF 是固定版面格式，未标记 PDF 可能没有可靠的语义层和阅读顺序。
- 手写体、嵌套表格、跨页合并表格和低分辨率扫描件仍可能需要人工校对。
- Native 引擎用于降级运行，不替代版面分析或 OCR。
- 当前任务队列适合本地单机；多用户服务应将模型推理迁移到隔离的进程队列。
