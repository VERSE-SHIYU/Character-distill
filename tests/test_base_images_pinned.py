"""基础镜像必须锁 digest（2026-10-12：openresty:alpine 浮动 tag 上游更新 → nginx 层跨境重下 24 分钟）。"""
from pathlib import Path
import re

_REPO = Path(__file__).resolve().parent.parent
_FROM_RE = re.compile(r"^FROM\s+(\S+)", re.M)


def test_every_from_is_digest_pinned():
    froms = []
    for f in ("Dockerfile", "nginx/Dockerfile"):
        froms += [(f, ref) for ref in _FROM_RE.findall((_REPO / f).read_text(encoding="utf-8"))]
    assert len(froms) == 3, froms  # 负控：node / python / openresty 三处都解析到
    bad = [x for x in froms if not re.search(r"@sha256:[0-9a-f]{64}$", x[1])]
    assert bad == [], f"未锁 digest：{bad}"


def test_dependabot_watches_both_dockerfile_dirs():
    import yaml
    cfg = yaml.safe_load((_REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    docker = [u for u in cfg["updates"] if u["package-ecosystem"] == "docker"]
    assert docker and set(docker[0]["directories"]) == {"/", "/nginx"}, docker
