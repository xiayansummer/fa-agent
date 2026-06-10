"""文档文本提取 skill —— 给 chat 的 summarize_document 工具用。

支持 pdf / docx / txt / md。下载 → 按扩展名解析 → 返回纯文本。
为了避免炸 LLM context，长文档截到 80K 字（≈ 30K tokens，minimax 256K 也够）。
"""
from __future__ import annotations
import io
import httpx
from harness.skill_registry import skill, skill_registry

_MAX_DOC_CHARS = 80_000


@skill(registry=skill_registry, name="文档.提取文本",
       version="1.0", timeout=90, retry=1)
async def doc_extract(url: str, filename: str = "") -> dict:
    """下载并提取文档文本。返回 {text, ext, chars, truncated}。
    扫描型 PDF（图片）会得到空文本，调用方自行处理。"""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"下载文档失败：HTTP {resp.status_code}")
        content = resp.content

    name = (filename or url).lower().split("?")[0]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""

    if ext == "pdf":
        text = _extract_pdf(content)
    elif ext == "docx":
        text = _extract_docx(content)
    elif ext in ("txt", "md", "csv"):
        text = content.decode("utf-8", errors="ignore")
    elif ext in ("doc", "ppt", "pptx"):
        raise RuntimeError(f"暂不支持 .{ext} 格式（请另存为 PDF 后上传）")
    else:
        # 兜底：尝试 UTF-8
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError(f"无法识别的文件类型：.{ext or '未知'}")

    text = (text or "").strip()
    truncated = False
    if len(text) > _MAX_DOC_CHARS:
        text = text[:_MAX_DOC_CHARS]
        truncated = True
    return {"text": text, "ext": ext, "chars": len(text), "truncated": truncated}


def _extract_pdf(content: bytes) -> str:
    # 函数内 import：依赖（pypdf）首次部署用热装时即可使用，不影响其它路径
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
        except Exception:
            continue
    return "\n\n".join(parts)


def _extract_docx(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text)
