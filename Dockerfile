# --- 阶段 1: 构建前端 ---
FROM node:20-alpine AS frontend-build
WORKDIR /app/emby-actor-ui

# 1. 仅复制 package.json 和 lock 文件
COPY emby-actor-ui/package*.json ./

# 2. 安装依赖。这一层只有在 package.json 变动时才会重新运行
RUN npm cache clean --force && \
    npm install --no-fund --verbose --legacy-peer-deps

# 3. 复制所有前端源码
COPY emby-actor-ui/ ./

# 4. 构建前端。这一层只有在前端源码变动时才会重新运行
RUN npm run build

# --- 阶段 2: 构建最终的生产镜像 ---
FROM python:3.11-slim

# 设置环境变量 (保持不变)
ENV LANG="C.UTF-8" \
    TZ="Asia/Shanghai" \
    HOME="/embytoolkit" \
    CONFIG_DIR="/config" \
    APP_DATA_DIR="/config" \
    TERM="xterm" \
    PUID=0 \
    PGID=0 \
    UMASK=000

WORKDIR /app

# 1. 安装系统依赖。这一层很少变动，会长期被缓存。
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
        nodejs \
        gettext-base \
        locales \
        procps \
        gosu \
        bash \
        wget \
        curl \
        dumb-init && \
    apt-get clean && \
    rm -rf \
        /tmp/* \
        /var/lib/apt/lists/* \
        /var/tmp/*

# 2. 仅复制 Python 依赖文件
COPY requirements.txt .

# 3. 安装 Python 依赖。这一层只有在 requirements.txt 变动时才会重新运行
RUN pip install --no-cache-dir -r requirements.txt

# 4. 复制所有应用文件。这是变动最频繁的部分，放在后面。
COPY web_app.py \
     core_processor.py \
     douban.py \
     tmdb_handler.py \
     emby_handler.py \
     utils.py \
     logger_setup.py \
     constants.py \
     web_parser.py \
     ai_translator.py \
     watchlist_processor.py \
     actor_sync_handler.py \
     actor_utils.py \
     actor_subscription_processor.py \
     moviepilot_handler.py \
     config_manager.py \
     task_manager.py \
     db_handler.py \
     extensions.py \
     tasks.py \
     github_handler.py \
     custom_collection_handler.py \
     scheduler_manager.py \
     reverse_proxy.py \
     maoyan_fetcher.py \
     ./

COPY fonts/ ./fonts/
COPY services/ ./services/
COPY routes/ ./routes/
COPY templates/ ./templates/
COPY docker/entrypoint.sh /entrypoint.sh

# 5. 从前端构建阶段拷贝编译好的静态文件
COPY --from=frontend-build /app/emby-actor-ui/dist/. /app/static/

# 6. 设置权限和用户。这些操作依赖于被复制的文件，所以放在后面。
RUN chmod +x /entrypoint.sh && \
    mkdir -p ${HOME} && \
    groupadd -r embytoolkit -g 918 && \
    useradd -r embytoolkit -g embytoolkit -d ${HOME} -s /bin/bash -u 918

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -f http://localhost:5257/api/health || exit 1    

# 声明数据卷、端口和入口点 (保持不变)
VOLUME [ "${CONFIG_DIR}" ]
EXPOSE 5257
ENTRYPOINT [ "/entrypoint.sh" ]