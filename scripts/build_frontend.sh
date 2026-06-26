#!/usr/bin/env bash
# ============================================================
# 本地前端构建脚本（在你的电脑上跑，不在 2G 服务器上跑）
# 用途：构建好 web/frontend/dist，供服务器直接使用，避免服务器 OOM
#
# 用法：
#   bash scripts/build_frontend.sh
# 然后把 web/frontend/dist 提交到 git，或用 rsync/scp 传到服务器。
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> 进入前端目录"
cd web/frontend

echo "==> 安装依赖 (npm ci)"
npm ci --no-audit --no-fund

echo "==> 构建生产产物 (npm run build)"
npm run build

if [ -f dist/index.html ]; then
    echo "✅ 构建成功：web/frontend/dist/index.html 已生成"
    echo "   产物大小：$(du -sh dist | cut -f1)"
    echo ""
    echo "下一步（二选一）："
    echo "  A. git add web/frontend/dist && git commit && git push，服务器 git pull 后 build 镜像"
    echo "  B. rsync -avz web/frontend/dist/ user@服务器:/path/Character-distill/web/frontend/dist/"
else
    echo "❌ 构建失败：dist/index.html 未生成，检查上面的报错"
    exit 1
fi
