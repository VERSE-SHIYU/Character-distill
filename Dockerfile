# ============================================================
# Stage 1: 前端构建
# ============================================================
FROM node:20-alpine AS frontend
WORKDIR /build
COPY web/frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/frontend/ ./
RUN npm run build

# ============================================================
# Stage 2: 后端运行
# ============================================================
FROM python:3.12-slim

# 系统依赖（sentence-transformers 编译需要）
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖（先拷贝 requirements 利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目代码
COPY . .

# 拷贝前端构建产物覆盖到 dist 目录
COPY --from=frontend /build/dist /app/web/frontend/dist

# 创建持久化目录
RUN mkdir -p /app/data /app/data/voice_cache /app/data/chroma_db

# 非 root 用户运行（安全）
RUN useradd -m -s /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/docs')" || exit 1

CMD ["python", "-m", "web.server"]
