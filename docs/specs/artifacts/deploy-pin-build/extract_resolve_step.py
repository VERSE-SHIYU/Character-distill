"""把 deploy.yml 里 resolve 步骤的 run 脚本抽出来，替换 ${{ }} 表达式后落成可直接 bash 执行的 .sh。

为什么这样测：脚本正文一律来自 deploy.yml 本身，不复制、不改写。改了工作流，
演练对象就跟着变，不会出现「测的是一份复制品、跑的是另一份」。
产出脚本的 sha256 可作为「被测对象 == 待审改动」的指纹。

用法:
  python extract_resolve_step.py --sha <commit sha> --out <step.sh 路径>
  python extract_resolve_step.py --sha X --out step.sh --synthetic-gh <bin 目录> --synthetic-status in_progress

--synthetic-gh 会往给定目录里放一个假 gh（固定返回指定 status 的 run list JSON），
用来演「build 未完成」这条路 —— 真实的 in_progress 是一瞬间的状态，无法复现。
"""
import argparse
import hashlib
import os
from pathlib import Path

import yaml

# <root>/docs/specs/artifacts/deploy-pin-build/extract_resolve_step.py
REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

EXPRESSIONS = [
    ("${{ github.sha }}", None),  # None = 用 --sha 传入
    ("${{ github.repository }}", "VERSE-SHIYU/Character-distill"),
    ("${{ github.event.inputs.image_sha }}", ""),
    ("${{ github.event.inputs.build_run_id }}", ""),
]


def extract_run_script():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = doc["jobs"]["resolve-digest"]["steps"][0]
    assert step["id"] == "resolve", step.get("id")
    return step["run"]


def substitute(script, sha):
    for expr, value in EXPRESSIONS:
        script = script.replace(expr, sha if value is None else value)
    return script


def write_synthetic_gh(bindir, status):
    bindir = Path(bindir)
    bindir.mkdir(parents=True, exist_ok=True)
    payload = '[{"databaseId":99999,"status":"%s","conclusion":""}]' % status
    shim = bindir / "gh"
    with open(shim, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\ncat <<'JSON2'\n%s\nJSON2\n" % payload)
    os.chmod(shim, 0o755)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--synthetic-gh")
    ap.add_argument("--synthetic-status", default="in_progress")
    args = ap.parse_args()

    script = substitute(extract_run_script(), args.sha)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(script)

    if args.synthetic_gh:
        write_synthetic_gh(args.synthetic_gh, args.synthetic_status)

    print("step.sh sha256 = %s" % hashlib.sha256(script.encode()).hexdigest())


if __name__ == "__main__":
    main()
