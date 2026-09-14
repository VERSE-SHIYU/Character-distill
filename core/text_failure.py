# -*- coding: utf-8 -*-
"""文本解析失败的**用户文案表**（缺陷 39）—— 只放常量，无逻辑。

为什么单独一个模块：`core/text_manager.py` 的职责是「怎么解析文件」，不是「用户
看到什么话」。两者揉在一起时，一个 `except` 会同时接住「我们写的用户文案」和
「第三方库的异常原文」，于是 `f"DOCX 解析失败: {str(e)}"` 把原文带上了屏（含服务器
路径）。把文案搬到这里，解析层就只剩「发生了什么」（一个键），说哪句话是表的事。

**与 adapters/llm_adapter.py 的上屏出口是两套概念**：那边是 LLM 侧失败的口径链
（`_INCOMPLETE_USER_MESSAGES` / `user_facing_error`），这边是本地文件解析失败。
不合并 —— 共用一处会让「新增一种 LLM 未完成终态」和「新增一种文件格式」互相牵动。

用法（`text_manager.py` 里唯一合法形态，由 tests/test_text_failure_messages.py::L1 强制）::

    raise ValueError(TEXT_FAILURE_MESSAGES["docx_empty"])
    raise ValueError(TEXT_FAILURE_MESSAGES["pdf_page_limit"].format(n=page_count, limit=MAX_PDF_PAGES))

带占位符的三条用 `.format()` 补本仓自己的事实（页数、字数上限、扩展名）；占位符
只接受**本仓产生的值**，不得喂第三方异常的字符串 —— 那是本模块存在的全部理由。
"""
from __future__ import annotations

TEXT_FAILURE_MESSAGES: dict[str, str] = {
    # ── 格式 / 体积 / 内容（两条公开入口共用） ─────────────────────────
    "unsupported_ext": "Unsupported file extension: {ext}",
    "empty_after_parse": "Text content is empty after parsing",
    "too_long": "文本超过 {limit_text} 字上限，请分卷上传",
    "chat_clean_empty": "聊天记录清洗后无有效内容，请检查文件格式",
    "text_not_found": "Text not found",
    # ── 文本文件读取 ──────────────────────────────────────────────────
    "encoding_unknown": "文件编码无法识别，请另存为 UTF-8 后重新上传",
    # ── PDF ───────────────────────────────────────────────────────────
    "pdf_open_failed": "PDF 文件无法打开（可能已损坏或加密），请确认文件完整后重试",
    "pdf_page_limit": "PDF 共 {page_count} 页，超过 {max_pages} 页上限，请拆分后上传",
    "pdf_parse_failed": "PDF 解析失败，请确认文件完整后重试",
    "pdf_no_text": (
        "该 PDF 无法提取文字（疑似扫描件或图片 PDF）。"
        "请上传可复制文字的 PDF，或直接上传 .txt 文件"
    ),
    # ── DOCX ──────────────────────────────────────────────────────────
    "docx_empty": "DOCX 文件无有效文本内容",
    "docx_parse_failed": "DOCX 解析失败，请确认文件完整后重试",
}
