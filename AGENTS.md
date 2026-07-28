# PDF Markdown Studio Agent Guide

## 项目定位

本项目是本地优先的 PDF 转 Markdown 工具，提供 CLI、FastAPI、React Web 界面和
macOS 原生应用壳，并用结构化文档模型与质量门控减少静默转换错误。

## 启动与验证

- 首次安装：`make setup`
- 本地服务：`make run`，默认访问 `http://127.0.0.1:8000`
- macOS 源码便捷启动：双击 `start.command`
- 测试：`.venv/bin/python -m pytest -q`
- Python lint：`.venv/bin/ruff check src tests scripts`
- 桌面前端构建：`npm --prefix frontend run build -- --mode desktop`
- macOS 发行包：`make macos-app`

## 技术栈

- Python 3.11–3.13、FastAPI、Pydantic、pypdf、Docling；PaddleOCR 为可选依赖
- React 19、TypeScript、Vite
- macOS AppKit/WebKit Objective-C 壳，内置 PyInstaller 服务

## 目录与约定

- `src/pdfmd/`：转换、质量门控、API、CLI 与任务状态的权威实现
- `frontend/src/`：共享 Web/桌面界面；`frontend/dist/` 会同时进入 wheel 和 `.app`
- `desktop/macos/`、`scripts/build_macos_app.sh`：原生窗口和发行链
- `tests/`：现有行为合同；变更后至少运行测试、lint 和桌面模式前端构建
- 前端源码变化不会自动进入既有 `.app`；发行前必须重建并核对 wheel、应用与 ZIP
- 保留原生 `<input type="file" multiple>` 和链接导航下载，macOS 文件/保存面板依赖它们
- Web 服务保持回环地址；桌面壳使用随机回环端口，用户数据位于
  `~/Library/Application Support/PDF Markdown Studio`
- 安全预览不得请求外部图片，公开 API 不得返回本机绝对路径
- `data/`、`output/`、`tmp/`、`build/`、`dist/` 是运行或构建产物，不是项目文档
- 不覆盖用户任务结果，不在未确认时删除构建缓存、历史包或回归产物

## 当前状态与下一步

- 当前版本为 0.8.0；界面支持单文件/批量转换、批量选择下载和任务历史
- macOS 默认窗口为 1180×780，最小 960×680；完整包包含离线 Docling 模型
- 当前发行物仅为 ad-hoc 签名，尚未 Developer ID 签名或公证
- Git 尚无首个提交；任何清理前先建立可恢复基线
- 下一步优先完成正式签名/公证，并补上传队列、批量下载和结果页签的前端组件测试
