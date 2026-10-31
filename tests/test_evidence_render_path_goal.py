"""目标检查：渲染新鲜度变异用例**单独跑**也必须成立（不依赖先被别的用例导入）。

缺陷形态：`render_evidence` 在导入时把输出路径算死，若首次导入发生在变异用例把
`evidence_writer.EVIDENCE_DIR` 搬到 tmp 之后，路径永久指向 tmp，单跑报 FileNotFoundError。
本用例用子进程从零起一个 pytest —— 进程内是否巧合先导入过都不再生效。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODEID = (
    "tests/test_evidence_integrity_mutations.py::test_mutation_reds_its_own_assertion"
    "[measured_at_drifts_from_render]"
)


def test_single_mutation_run_passes_without_prior_import():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", NODEID],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
