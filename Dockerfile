# 第 1 阶段：构建前端
FROM node:20 AS frontend-build
WORKDIR /app
COPY emby-actor-ui/ ./emby-actor-ui/
WORKDIR /app/emby-actor-ui
RUN npm install && npm run build

# 第 2 阶段：构建 Python 后端 + 嵌入前端
FROM python:3.11-slim

# 安装 Node.js 和 Python 所需依赖
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    node -v && npm -v

# 安装 Python 依赖
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码
COPY *.py ./
RUN mkdir -p /config
COPY templates/ ./templates/

# 拷贝前端静态文件
COPY --from=frontend-build /app/emby-actor-ui/dist/ ./static/

# 启动程序
CMD ["python", "web_app.py"]

