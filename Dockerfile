# =================================================================
# 阶段 1: 构建前端静态资源
# =================================================================
FROM node:20-alpine AS frontend-build

WORKDIR /app

# 仅复制 package 文件以利用 Docker 缓存层
COPY emby-actor-ui/package.json emby-actor-ui/package-lock.json* ./emby-actor-ui/
WORKDIR /app/emby-actor-ui
RUN npm install

# 复制前端所有源码并构建
COPY emby-actor-ui/ ./
RUN npm run build

# =================================================================
# 阶段 2: 构建最终的生产镜像
# =================================================================
FROM python:3.11-slim

# 设置环境变量，避免 Python 写入 .pyc 文件并以非缓冲模式运行
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# --- 安装系统依赖 ---
# 1. 安装 Node.js (为 'translators' 库)
# 2. 安装构建工具和 lxml 的 C 库依赖 (为 'lxml' Python 包)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # Node.js 依赖
    curl gnupg \
    # lxml 构建依赖
    gcc libxml2-dev libxslt1-dev \
    && \
    # 安装 Node.js
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    # 验证 Node.js 安装
    node -v && npm -v

# --- 安装 Python 依赖 ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    # 清理不再需要的构建工具和 apt 缓存，保持镜像苗条
    apt-get purge -y --auto-remove gcc libxml2-dev libxslt1-dev curl gnupg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- 拷贝应用代码 ---
# 使用 .dockerignore 来排除不需要的文件
COPY . .

# --- 拷贝前端构建产物 ---
COPY --from=frontend-build /app/emby-actor-ui/dist/. /app/static/

# --- 配置与运行 ---
# 声明 /config 卷，用于挂载外部的配置文件和数据库
VOLUME /config

# 暴露端口
EXPOSE 5257

# [推荐] 添加健康检查，让 Docker 知道应用是否正常运行
# 假设你有一个 /api/status 的健康检查端点
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:5257/api/status || exit 1

# 使用 Gunicorn 作为生产环境的 WSGI 服务器启动应用
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5257", "--access-logfile", "-", "--error-logfile", "-", "web_app:app"]