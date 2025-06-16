# --- 阶段 1: 构建前端 ---
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY emby-actor-ui/package.json emby-actor-ui/package-lock.json* ./emby-actor-ui/
WORKDIR /app/emby-actor-ui
RUN npm install --no-fund
COPY emby-actor-ui/ ./

# ✨✨✨ 在 install 之前增加清理缓存的步骤 ✨✨✨
RUN npm cache clean --force

# 使用 --verbose 参数获取更详细的日志，方便排错
RUN npm install --no-fund --verbose

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
    # ... (安装 nodejs 的部分) ...
    apt-get install -y nodejs && \
    # ✨✨✨ START: 决定性的修复 ✨✨✨
    # 1. 检查目标 PUID 是否已被占用，如果被占用，就删除那个用户
    #    我们用 `getent passwd ${PUID}` 来查找，如果找到了，第一列就是用户名
    if getent passwd ${PUID} > /dev/null; then \
        echo "User with PUID ${PUID} already exists, deleting it."; \
        EXISTING_USER=$(getent passwd ${PUID} | cut -d: -f1); \
        deluser $EXISTING_USER; \
    fi && \
    # 2. 检查目标 PGID 是否已被占用，如果被占用，就删除那个组
    if getent group ${PGID} > /dev/null; then \
        echo "Group with PGID ${PGID} already exists, deleting it."; \
        EXISTING_GROUP=$(getent group ${PGID} | cut -d: -f1); \
        delgroup $EXISTING_GROUP; \
    fi && \
    # 3. 现在可以安全地创建我们自己的用户和组了
    groupadd -g ${PGID} myuser && \
    useradd -u ${PUID} -g myuser -s /bin/sh -m myuser && \
    # ✨✨✨ END: 决定性的修复 ✨✨✨
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
COPY watchlist_processor.py .

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