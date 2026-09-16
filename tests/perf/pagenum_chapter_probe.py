# -*- coding: utf-8 -*-
"""探针：**C 的提取路径 + 前端章节切分规则**，在「带页码、无章节词」的 PDF 上落到哪条规则。

**它回答的是哪个命题。** `core/text_manager._extract_pdf` 从 `pymupdf4llm.to_markdown`
改成 `"".join(page.get_text() ...)`（纯文本）之后，正文里不再有 markdown 的 `#` 标题。而
`web/frontend/src/components/BookReader.jsx` 的 `detectChapters` 有**四条优先级递减**的规则，
第一条就是 `^#{1,3}\\s+` —— 失去规则 1 之后，一份「没有中文章节词、也没有 `Chapter N`」的
PDF 会落到规则 4（`^\\s*(\\d{1,4})\\s*$`，本意是认「独占一行的章节编号」），而**页码恰好
长成那个样子**。本探针拿真 PDF 实测这件事成不成立、射程多大。

**跑的是真代码，不是抄一份规则。** 规则的唯一源头是 `BookReader.jsx` 本身：node 侧按
`function detectChapters(content) {` 与 `function splitByParagraphs(` 切片、`eval` 出
`detectChapters`，再单独切出 `const rules = [...]` 数每条规则各命中几处。切片锚点失效即抛，
不静默退回（退回 = 拿一份长得像规则的东西当证据）。改前端规则的人不用同步本文件。

**node 是硬前置。** 前端是 JS，仓里没有 JS 运行时就没法验这条。缺 node 即失败，不跳过。

**产物只留统计量。** PDF 与提取文本落临时目录、跑完即弃；入 `docs/evidence/` 的是页数、
命中数、章数与标题（全部由占位散文生成，不含任何真实语料）。

**跑法**：`python tests/perf/pagenum_chapter_probe.py`
"""
from __future__ import annotations

import importlib.metadata
import json
import pathlib
import subprocess
import sys
import tempfile

import pymupdf

PERF_DIR = pathlib.Path(__file__).resolve().parent
ROOT = PERF_DIR.parents[1]
sys.path.insert(0, str(PERF_DIR))

from evidence_writer import code_sha, write_evidence  # noqa: E402

EVIDENCE_ID = "pagenum-chapter-rules"
READER = ROOT / "web" / "frontend" / "src" / "components" / "BookReader.jsx"

# 占位散文：**不是**任何真实语料（版权 / 去标识，见 docs/evidence/README.md）。
PARA = ("The morning light came through the window and fell across the desk. "
        "Nothing in the room had moved since the previous evening.\n") * 4
PAGES = 5

# 切片锚点必须与真源一致；失效即抛，不退回「大概能跑」。
_LOADER = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const a = src.indexOf('function detectChapters(content) {');
const b = src.indexOf('function splitByParagraphs(');
if (a < 0 || b <= a) throw new Error('detectChapters 切片锚点失效');
const slice = src.slice(a, b);
if (!slice.includes('function autoChunkByLength')) throw new Error('切片没带上 autoChunkByLength');
const detectChapters = new Function(slice + '\nreturn detectChapters;')();
const ra = slice.indexOf('const rules = [');
const rb = slice.indexOf('\n  ]', ra);
if (ra < 0 || rb < 0) throw new Error('rules 切片锚点失效');
const rules = new Function('return ' + slice.slice(ra + 'const rules = '.length, rb + 4) + ';')();
if (rules.length !== 4) throw new Error('规则条数变了：' + rules.length);

const out = {};
for (const f of process.argv.slice(3)) {
  const text = fs.readFileSync(f, 'utf8');
  const rule_hits = rules.map(r => (text.match(new RegExp(r.re.source, r.re.flags)) || []).length);
  const ch = detectChapters(text);
  out[require('path').basename(f)] = {
    rule_hits,
    chapters: ch.length,
    titles: ch.map(c => String(c.title)).slice(0, 12),
  };
}
process.stdout.write(JSON.stringify(out));
'''


def _build_pdf(path: pathlib.Path, *, where: str, pad: int, headings: bool) -> None:
    doc = pymupdf.open()
    for i in range(1, PAGES + 1):
        page = doc.new_page()
        label = str(i).rjust(pad, "0") if pad else str(i)
        y = 100
        if headings:                      # 字号 22 > 正文 11 —— pymupdf4llm 会把它认成标题
            page.insert_text((72, y), f"Harbour Notes {i}", fontsize=22, lineheight=1.2)
            y += 60
        for k in range(3):
            page.insert_text((72, y + k * 200), PARA, fontsize=11, lineheight=1.4)
        page.insert_text((290, 800 if where == "footer" else 40), label, fontsize=10)
    doc.save(path)
    doc.close()


def _extract(path: pathlib.Path) -> str:
    """与 `core/text_manager._extract_pdf` 改动后**逐字同形**的那一句。"""
    doc = pymupdf.open(path)
    try:
        return "".join(page.get_text() for page in doc)
    finally:
        doc.close()


def main() -> int:
    cases = [
        ("footer_1digit", dict(where="footer", pad=0, headings=False)),
        ("footer_2digit", dict(where="footer", pad=2, headings=False)),
        ("header_1digit", dict(where="header", pad=0, headings=False)),
        ("with_font_heading", dict(where="footer", pad=0, headings=True)),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        texts: dict[str, pathlib.Path] = {}
        meta: dict[str, dict] = {}
        for name, kw in cases:
            pdf = tmpdir / f"{name}.pdf"
            _build_pdf(pdf, **kw)
            text = _extract(pdf)
            tf = tmpdir / f"{name}.txt"
            tf.write_text(text, encoding="utf-8")
            texts[name] = tf
            meta[name] = {
                "pages": PAGES,
                "chars": len(text),
                "digit_only_lines": sum(
                    1 for ln in text.splitlines()
                    if ln.strip().isdigit() and 1 <= len(ln.strip()) <= 4),
            }
        # 对照：有 markdown 标题时必须走规则 1 —— 证明这套跑法认得出另一种情形（不是恒真）。
        ctrl = tmpdir / "control_markdown.txt"
        ctrl.write_text("# Chapter Alpha\n\n" + PARA + "\n\n# Chapter Beta\n\n" + PARA + "\n",
                        encoding="utf-8")

        loader = tmpdir / "load_rules.js"
        loader.write_text(_LOADER, encoding="utf-8")
        files = [str(texts[n]) for n, _ in cases] + [str(ctrl)]
        r = subprocess.run(["node", str(loader), str(READER), *files],
                           capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
        if r.returncode != 0:
            print("node 侧失败（node 是硬前置，不跳过）：\n", r.stdout, r.stderr)
            return 2
        got = json.loads(r.stdout)

        out_cases = []
        for name, _ in cases:
            row = got[f"{name}.txt"]
            out_cases.append({"name": name, **meta[name], **row})
            print(f"[{name}] 页 {PAGES} / 字符 {meta[name]['chars']} / "
                  f"独占一行数字 {meta[name]['digit_only_lines']} / "
                  f"规则命中 {row['rule_hits']} / 章 {row['chapters']} / 标题 {row['titles']}")
        control = got["control_markdown.txt"]
        print(f"[control] 规则命中 {control['rule_hits']} / 章 {control['chapters']} / "
              f"标题 {control['titles']}")

    payload = {
        "probe": "pagenum_chapter_probe",
        "pymupdf": importlib.metadata.version("pymupdf"),
        "detect_source": "web/frontend/src/components/BookReader.jsx",
        "cases": out_cases,
        "control": {"name": "control_markdown", **control},
    }
    write_evidence(
        EVIDENCE_ID, payload,
        claim="带页码、无章节词的 PDF 在纯文本提取路径下被按页码切章：独占一行的页码 5 处 → "
              "章 5 个，标题即页码；对照含 markdown 标题的文本走标题规则",
        script="tests/perf/pagenum_chapter_probe.py",
        env=f"PyMuPDF {importlib.metadata.version('pymupdf')}（与生产镜像同版）；"
            "node 跑 web/frontend/src/components/BookReader.jsx 的 detectChapters（切片源码 eval，"
            "非重写规则）；PDF 由占位散文生成，页脚/页眉页码，正文 11pt",
        code_sha=code_sha(),
        subset=frozenset({"probe", "pymupdf", "detect_source", "cases", "control"}),
    )
    print(f"\n产物已写：docs/evidence/{EVIDENCE_ID}.json")
    print("人工层仍需表态：evidence_annotations.write(...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
