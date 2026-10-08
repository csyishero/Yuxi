# 办公增强沙盒镜像
#
# 基于 AIO Sandbox 基础镜像追加办公文档处理依赖，供沙盒内的 Agent 处理
# docx/xlsx/pptx/pdf/图片/压缩包等各类文件。
#
# 重要：不要覆盖基础镜像的 ENTRYPOINT (/opt/gem/run.sh)，也不要改 EXPOSE 8080。
# run.sh 负责创建 gem 用户并启动 supervisord/nginx/MCP 等服务，覆盖后沙盒不可用。
FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest

# 基础镜像的 apt 源指向 archive.ubuntu.com，国内构建慢且易超时，换成镜像站。
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
ARG DEBIAN_FRONTEND=noninteractive

RUN sed -i \
      -e "s@http://archive.ubuntu.com/ubuntu/@${APT_MIRROR}/@g" \
      -e "s@http://security.ubuntu.com/ubuntu/@${APT_MIRROR}/@g" \
      /etc/apt/sources.list

# LibreOffice 选用 -nogui 变体：模块与过滤器跟完整包一致，只是换成 core-nogui
# 而不引入图形界面依赖（实测体积差异不到 10MB）。impress-nogui 会自动带上
# draw-nogui，所以 .odg/.vsd 也能转；只有 Base（.mdb）才需要完整包。
# 基础镜像已内置 85 个 CJK 字体，中文渲染不缺字体，因此不再重复安装字体包。
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui \
      libreoffice-calc-nogui \
      libreoffice-impress-nogui \
      libreoffice-java-common \
      libreoffice-l10n-zh-cn \
      default-jre-headless \
      poppler-utils \
      ghostscript \
      qpdf \
      pdftk-java \
      tesseract-ocr \
      tesseract-ocr-eng \
      tesseract-ocr-chi-sim \
      tesseract-ocr-chi-tra \
      pandoc \
      antiword \
      catdoc \
      unrtf \
      p7zip-full \
      unrar-free \
      zstd \
      ripgrep \
      jq \
      sqlite3 \
      graphviz \
      libmagic1 \
      libimage-exiftool-perl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 基础镜像已内置 openpyxl / pandas / numpy / Pillow / PyMuPDF / python-pptx /
# xlsxwriter / xlrd / docx2txt / weasyprint，这里只补缺失的部分。
RUN pip3 install --no-cache-dir -i ${PIP_MIRROR} \
      python-docx \
      pypdf \
      pdfplumber \
      pdf2image \
      pdf2docx \
      pytesseract \
      odfpy \
      reportlab \
      img2pdf \
      mammoth \
      python-magic \
      chardet \
      jinja2 \
      tabulate \
      xlsx2csv \
      'markitdown[all]'

# 构建期冒烟测试：缺依赖或中文渲染异常时直接构建失败，不要等到线上才发现。
RUN set -eux; \
    soffice --version; \
    pandoc --version | head -1; \
    tesseract --version 2>&1 | head -1; \
    qpdf --version | head -1; \
    python3 -c "import docx, fitz, markitdown, openpyxl, pdf2docx, pdfplumber, pypdf, pptx, reportlab; print('python deps ok')"

RUN bash -eux <<'SMOKE'
workdir="$(mktemp -d)"
python3 - "$workdir" <<'PY'
import sys

from docx import Document
from openpyxl import Workbook

workdir = sys.argv[1]
doc = Document()
doc.add_heading("办公沙盒自检", 0)
doc.add_paragraph("中文渲染检查：你好，世界。")
doc.save(f"{workdir}/doc.docx")

wb = Workbook()
ws = wb.active
ws["A1"], ws["B1"], ws["A2"] = 1, 2, "=SUM(A1:B1)"
wb.save(f"{workdir}/sheet.xlsx")
PY
soffice --headless -env:UserInstallation=file:///tmp/lo-smoke \
    --convert-to pdf --outdir "$workdir" "$workdir/doc.docx" >/dev/null
soffice --headless -env:UserInstallation=file:///tmp/lo-smoke \
    --convert-to pdf --outdir "$workdir" "$workdir/sheet.xlsx" >/dev/null
test -s "$workdir/doc.pdf"
test -s "$workdir/sheet.pdf"
pdftotext "$workdir/doc.pdf" - | grep -q "办公沙盒自检"
rm -rf "$workdir" /tmp/lo-smoke
SMOKE
