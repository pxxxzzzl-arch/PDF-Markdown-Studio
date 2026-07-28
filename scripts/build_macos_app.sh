#!/bin/zsh
set -euo pipefail
unsetopt BG_NICE

PROJECT_DIR="${0:A:h:h}"
cd "$PROJECT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS 应用只能在 macOS 上构建。"
  exit 1
fi

SOURCE_PYTHON="${PDFMD_BUILD_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$SOURCE_PYTHON" ]]; then
  echo "缺少可用的 Python 3.11–3.13：$SOURCE_PYTHON"
  echo "请先执行 make setup，或设置 PDFMD_BUILD_PYTHON。"
  exit 1
fi

PYTHON_VERSION="$("$SOURCE_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! "$SOURCE_PYTHON" -c 'import sys; raise SystemExit(not ((3, 11) <= sys.version_info[:2] < (3, 14)))'; then
  echo "构建需要 Python 3.11–3.13，当前为 $PYTHON_VERSION。"
  exit 1
fi

VERSION="$("$SOURCE_PYTHON" -c 'import pdfmd; print(pdfmd.__version__)')"
ARCH="$(uname -m)"
EDITION="${PDFMD_DESKTOP_EDITION:-full}"
REUSE_SOURCE_ENV="${PDFMD_DESKTOP_REUSE_SOURCE_ENV:-0}"
BUNDLE_MODELS="${PDFMD_BUNDLE_MODELS:-}"
if [[ -z "$BUNDLE_MODELS" ]]; then
  if [[ "$EDITION" == "full" ]]; then
    BUNDLE_MODELS=1
  else
    BUNDLE_MODELS=0
  fi
fi
if [[ "$EDITION" != "full" && "$EDITION" != "lite" ]]; then
  echo "PDFMD_DESKTOP_EDITION 只能是 full 或 lite。"
  exit 1
fi
if [[ "$REUSE_SOURCE_ENV" != "0" && "$REUSE_SOURCE_ENV" != "1" ]]; then
  echo "PDFMD_DESKTOP_REUSE_SOURCE_ENV 只能是 0 或 1。"
  exit 1
fi
if [[ "$BUNDLE_MODELS" != "0" && "$BUNDLE_MODELS" != "1" ]]; then
  echo "PDFMD_BUNDLE_MODELS 只能是 0 或 1。"
  exit 1
fi
BUILD_ROOT="$PROJECT_DIR/build/macos-app"
BUILD_VENV="$BUILD_ROOT/venv"
SERVER_ROOT="$BUILD_ROOT/server"
APP_BUNDLE="$PROJECT_DIR/dist/PDF Markdown Studio.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_EXECUTABLE="$APP_CONTENTS/MacOS/PDF Markdown Studio"
ZIP_PATH="$PROJECT_DIR/dist/PDF-Markdown-Studio-$VERSION-macOS-$ARCH.zip"

echo "构建前端界面…"
npm --prefix frontend run build -- --mode desktop

echo "构建 Python 安装包…"
"$SOURCE_PYTHON" -m hatchling build -t wheel
WHEEL_PATH="$PROJECT_DIR/dist/pdf_markdown_studio-$VERSION-py3-none-any.whl"

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"
export PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-config"

echo "准备独立桌面运行环境…"
PACKAGING_PYTHON="$BUILD_VENV/bin/python"
PYINSTALLER="$BUILD_VENV/bin/pyinstaller"
if [[ "$REUSE_SOURCE_ENV" == "1" ]]; then
  PACKAGING_PYTHON="$SOURCE_PYTHON"
  PYINSTALLER="${SOURCE_PYTHON:h}/pyinstaller"
  if [[ ! -x "$PYINSTALLER" ]]; then
    echo "复用源码环境需要先安装 desktop-build 依赖："
    echo "  $SOURCE_PYTHON -m pip install -e '.[desktop-build]'"
    exit 1
  fi
  if [[ "$EDITION" == "full" ]] && ! "$SOURCE_PYTHON" -c 'import docling' 2>/dev/null; then
    echo "完整版本需要源码环境已安装 Docling。"
    exit 1
  fi
  echo "复用已测试的源码环境（跳过依赖下载）。"
else
  "$SOURCE_PYTHON" -m venv "$BUILD_VENV"
  PACKAGE_TARGET="$WHEEL_PATH"
  RUNTIME_PACKAGES=()
  if [[ "$EDITION" == "full" ]]; then
    TESTED_DOCLING_VERSION="$("$SOURCE_PYTHON" -c \
      'from importlib.metadata import version; print(version("docling"))' \
      2>/dev/null || true)"
    if [[ -n "$TESTED_DOCLING_VERSION" ]]; then
      echo "锁定已测试的 Docling $TESTED_DOCLING_VERSION"
      RUNTIME_PACKAGES+=("docling==$TESTED_DOCLING_VERSION")
    else
      echo "当前环境没有 Docling，将安装项目允许的最新 2.x 版本。"
      PACKAGE_TARGET="${WHEEL_PATH}[primary]"
    fi
  fi
  "$PACKAGING_PYTHON" -m pip install --disable-pip-version-check \
    "pyinstaller>=6.11,<7" \
    "$PACKAGE_TARGET" \
    "${RUNTIME_PACKAGES[@]}"
fi

mkdir -p "$SERVER_ROOT"
PYINSTALLER_ARGS=(
  --noconfirm \
  --clean \
  --onedir \
  --name pdfmd-desktop-server \
  --distpath "$SERVER_ROOT/dist" \
  --workpath "$SERVER_ROOT/work" \
  --specpath "$SERVER_ROOT" \
  --collect-data pdfmd \
  --add-data "$PROJECT_DIR/frontend/dist:pdfmd/web" \
  --collect-submodules uvicorn \
  --osx-bundle-identifier com.pdfmarkdownstudio.server \
)

if [[ "$EDITION" == "full" ]]; then
  PYINSTALLER_ARGS+=(
    --collect-all docling
    --collect-all docling_core
    --collect-all docling_ibm_models
    --collect-all docling_parse
    --collect-all rapidocr
    --collect-all transformers
    --collect-all torchvision
    --collect-all pypdfium2
    --collect-all safetensors
    --collect-all tokenizers
    --collect-all rtree
    --collect-submodules scipy._external.array_api_compat
    --collect-submodules scipy._lib.array_api_compat
    --copy-metadata docling
    --copy-metadata docling-slim
    --copy-metadata docling-core
    --copy-metadata docling-ibm-models
    --copy-metadata docling-parse
    --copy-metadata rapidocr
    --copy-metadata transformers
    --copy-metadata torch
    --copy-metadata torchvision
    --hidden-import docling.models.plugins.defaults
    --hidden-import rapidocr.main
    --hidden-import rapidocr.inference_engine.pytorch
    --hidden-import transformers.models.rt_detr_v2
    --hidden-import transformers.models.idefics3
    --hidden-import torchvision._C
  )

  SOURCE_RAPIDOCR_MODELS="$("$SOURCE_PYTHON" -c \
    'from pathlib import Path; import rapidocr; print(Path(rapidocr.__file__).parent / "models")' \
    2>/dev/null || true)"
  for MODEL_NAME in \
    ch_PP-OCRv4_det_mobile.pth \
    ch_ptocr_mobile_v2.0_cls_mobile.pth \
    ch_PP-OCRv4_rec_mobile.pth \
    ppocr_keys_v1.txt; do
    if [[ -f "$SOURCE_RAPIDOCR_MODELS/$MODEL_NAME" ]]; then
      PYINSTALLER_ARGS+=(
        "--add-data=$SOURCE_RAPIDOCR_MODELS/${MODEL_NAME}:rapidocr/models"
      )
    else
      echo "提示：未找到可选的截图代码 OCR 资源 $MODEL_NAME。"
    fi
  done
fi

"$PYINSTALLER" \
  "${PYINSTALLER_ARGS[@]}" \
  "$PROJECT_DIR/scripts/desktop_server_entry.py"

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_CONTENTS/MacOS" "$APP_RESOURCES"
cp -R "$SERVER_ROOT/dist/pdfmd-desktop-server" "$APP_RESOURCES/server"

if [[ "$EDITION" == "full" && "$BUNDLE_MODELS" == "1" ]]; then
  echo "打包 Docling 离线模型…"
  SOURCE_HF_HOME="${PDFMD_MODEL_CACHE_SOURCE:-$("$SOURCE_PYTHON" -c \
    'from huggingface_hub.constants import HF_HOME; print(HF_HOME)')}"
  MODEL_CACHE_DEST="$APP_RESOURCES/model-cache/huggingface"
  REQUIRED_MODEL_REPOS=(
    models--docling-project--docling-layout-heron
    models--docling-project--docling-models
    models--docling-project--CodeFormulaV2
  )
  EXPECTED_LAYOUT_COMMIT=8f39ad3c0b4c58e9c2d2c84a38465abf757272d8
  EXPECTED_TABLE_COMMIT=fc0f2d45e2218ea24bce5045f58a389aed16dc23
  EXPECTED_CODE_COMMIT=ecedbe111d15c2dc60bfd4a823cbe80127b58af4
  mkdir -p "$MODEL_CACHE_DEST/hub"
  for MODEL_REPO in "${REQUIRED_MODEL_REPOS[@]}"; do
    SOURCE_MODEL_REPO="$SOURCE_HF_HOME/hub/$MODEL_REPO"
    if [[ ! -d "$SOURCE_MODEL_REPO" ]]; then
      echo "缺少离线模型缓存：$SOURCE_MODEL_REPO"
      echo "请先在源码环境中完成一次 Docling 代码增强转换，或设置 PDFMD_MODEL_CACHE_SOURCE。"
      exit 1
    fi
    /usr/bin/ditto "$SOURCE_MODEL_REPO" "$MODEL_CACHE_DEST/hub/$MODEL_REPO"
  done
  if [[ -f "$SOURCE_HF_HOME/hub/version.txt" ]]; then
    cp "$SOURCE_HF_HOME/hub/version.txt" "$MODEL_CACHE_DEST/hub/version.txt"
  fi
  if [[ -n "$(find -L "$MODEL_CACHE_DEST/hub" -type l -print -quit)" ]]; then
    echo "离线模型缓存包含失效的符号链接，构建已停止。"
    exit 1
  fi
  verify_model_ref() {
    local repo_name="$1"
    local ref_name="$2"
    local expected_commit="$3"
    local ref_file="$MODEL_CACHE_DEST/hub/$repo_name/refs/$ref_name"
    if [[ ! -f "$ref_file" || "$(<"$ref_file")" != "$expected_commit" ]]; then
      echo "离线模型版本不匹配：$repo_name/$ref_name"
      echo "期望 $expected_commit；请用已测试的 Docling 2.114 模型缓存重新构建。"
      exit 1
    fi
  }
  verify_model_ref \
    models--docling-project--docling-layout-heron main "$EXPECTED_LAYOUT_COMMIT"
  verify_model_ref \
    models--docling-project--docling-models v2.3.0 "$EXPECTED_TABLE_COMMIT"
  verify_model_ref \
    models--docling-project--CodeFormulaV2 main "$EXPECTED_CODE_COMMIT"
  HF_HOME="$MODEL_CACHE_DEST" \
    HF_HUB_CACHE="$MODEL_CACHE_DEST/hub" \
    HUGGINGFACE_HUB_CACHE="$MODEL_CACHE_DEST/hub" \
    HF_HUB_OFFLINE=1 \
    "$PACKAGING_PYTHON" -c \
      'from huggingface_hub import snapshot_download; snapshot_download("docling-project/docling-layout-heron", revision="8f39ad3c0b4c58e9c2d2c84a38465abf757272d8", local_files_only=True); snapshot_download("docling-project/docling-models", revision="fc0f2d45e2218ea24bce5045f58a389aed16dc23", local_files_only=True); snapshot_download("docling-project/CodeFormulaV2", revision="ecedbe111d15c2dc60bfd4a823cbe80127b58af4", local_files_only=True)'
fi

echo "编译原生 macOS 窗口…"
/usr/bin/clang \
  -O \
  -fobjc-arc \
  -framework AppKit \
  -framework UniformTypeIdentifiers \
  -framework WebKit \
  "$PROJECT_DIR/desktop/macos/PDFMarkdownStudioApp.m" \
  -o "$APP_EXECUTABLE"

sed "s/@VERSION@/$VERSION/g" \
  "$PROJECT_DIR/desktop/macos/Info.plist" \
  > "$APP_CONTENTS/Info.plist"

echo "生成应用图标…"
ICON_SOURCE="$BUILD_ROOT/AppIcon-1024.png"
ICONSET="$BUILD_ROOT/AppIcon.iconset"
/usr/bin/clang \
  -O \
  -fobjc-arc \
  -framework AppKit \
  "$PROJECT_DIR/desktop/macos/generate_icon.m" \
  -o "$BUILD_ROOT/generate-icon"
"$BUILD_ROOT/generate-icon" "$ICON_SOURCE"
mkdir -p "$ICONSET"
/usr/bin/sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
/usr/bin/sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
/usr/bin/sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
/usr/bin/sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
/usr/bin/sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
/usr/bin/sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
/usr/bin/sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
/usr/bin/sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
/usr/bin/sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
cp "$ICON_SOURCE" "$ICONSET/icon_512x512@2x.png"
"$SOURCE_PYTHON" "$PROJECT_DIR/scripts/build_icns.py" \
  "$ICONSET" \
  "$APP_RESOURCES/AppIcon.icns"

echo "签名并归档应用…"
/usr/bin/codesign --force --deep --sign - "$APP_BUNDLE"
/usr/bin/codesign --verify --deep --strict "$APP_BUNDLE"
rm -f "$ZIP_PATH"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ZIP_PATH"

echo
echo "构建完成："
echo "  $APP_BUNDLE"
echo "  $ZIP_PATH"
echo "  版本：$EDITION"
