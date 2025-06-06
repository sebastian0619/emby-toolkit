# --- 阶段 1: 构建前端 ---
FROM node:20-alpine AS frontend-build
# ... (你的前端构建步骤不变) ...
WORKDIR /app/emby-actor-ui 
RUN npm install
COPY emby-actor-ui/ ./ 
RUN npm run build

# --- 阶段 2: 构建最终的生产镜像 ---
FROM python:3.11-slim

WORKDIR /app

# --- 添加 Node.js 安装 ---
# 使用 NodeSource 的官方脚本来安装 Node.js (推荐，版本较新且稳定)
# 或者你可以使用 apt-get install nodejs (版本可能较旧)
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    # 清理 apt 缓存以减小镜像体积
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # 验证安装
    node -v && npm -v
# --- Node.js 安装结束 ---

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码
COPY web_app.py .
COPY core_processor.py .
COPY douban.py .
COPY tmdb_handler.py .
COPY emby_handler.py .
COPY utils.py .
COPY logger_setup.py .
COPY constants.py .
COPY local_data_handler.py . 

# (可选) 如果有 templates 目录
# COPY templates/ ./templates/

# 从前端构建阶段拷贝编译好的静态文件
COPY emby-actor-ui/package.json emby-actor-ui/package-lock.json ./emby-actor-ui/ 

EXPOSE 5000 

CMD ["python", "web_app.py"]