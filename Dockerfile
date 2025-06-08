# --- 阶段 1: 构建前端 ---
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY emby-actor-ui/package.json emby-actor-ui/package-lock.json* ./emby-actor-ui/
WORKDIR /app/emby-actor-ui
RUN npm install
COPY emby-actor-ui/ ./
RUN npm run build

# --- 阶段 2: 构建最终的生产镜像 ---
FROM python:3.11-slim
WORKDIR /app

# 安装 Node.js (因为 translators -> exejs 需要)
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    node -v && npm -v

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码 (由于 .dockerignore，local_data/ 不会被复制)
COPY web_app.py .
COPY core_processor.py .
COPY douban.py .
COPY tmdb_handler.py .
COPY emby_handler.py .
COPY utils.py .
COPY logger_setup.py .
COPY constants.py .
COPY local_data_handler.py .
COPY web_parser.py .  
COPY ai_translator.py . 

COPY templates/ ./templates/ 

# 从前端构建阶段拷贝编译好的静态文件
COPY --from=frontend-build /app/emby-actor-ui/dist/. /app/static/

# 声明 /config 目录为一个卷，期望在运行时被挂载
# Dockerfile 本身不应该包含实际的配置文件或数据库文件到这个目录
VOLUME /config

EXPOSE 5257 

CMD ["python", "web_app.py"]