# --- 阶段 1: 构建前端 ---
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY emby-actor-ui/package.json emby-actor-ui/package-lock.json* ./emby-actor-ui/
WORKDIR /app/emby-actor-ui
# 使用 --no-fund 避免不必要的提示
RUN npm install --no-fund
COPY emby-actor-ui/ ./
RUN npm run build

# --- 阶段 2: 构建最终的生产镜像 ---
FROM python:3.11-slim

# ✨ 1. 接收从 Unraid (或 docker-compose) 传来的环境变量，并设置默认值 ✨
ARG PUID=1000
ARG PGID=100

WORKDIR /app

# 安装必要的系统依赖和 Node.js
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    # ✨ 2. 在这里创建用户和组 ✨
    groupadd -g ${PGID} myuser && \
    useradd -u ${PUID} -g myuser -s /bin/sh -m myuser && \
    # 清理 apt 缓存
    apt-get clean && rm -rf /var/lib/apt/lists/*

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
COPY web_parser.py .  
COPY ai_translator.py . 

COPY templates/ ./templates/ 

# 从前端构建阶段拷贝编译好的静态文件
COPY --from=frontend-build /app/emby-actor-ui/dist/. /app/static/

# ✨ 3. 声明 /config 和 /app 目录，并确保新用户有权访问它们 ✨
# /config 是你挂载的持久化数据目录
VOLUME /config
# 确保新创建的用户对应用目录有所有权
RUN chown -R myuser:myuser /app

# ✨ 4. 切换到这个新创建的非 root 用户 ✨
USER myuser

EXPOSE 5257 

# ✨ 5. 用这个非 root 用户的身份来启动应用 ✨
CMD ["python", "web_app.py"]