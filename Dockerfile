# 第 1 阶段：构建前端
FROM node:20 AS frontend-build
WORKDIR /app
COPY emby-actor-ui/ ./emby-actor-ui/
WORKDIR /app/emby-actor-ui
RUN npm install && npm run build

# 第 2 阶段：构建 Python 后端 + 嵌入前端
FROM python:3.11-slim

# 安装 Python 依赖
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码
COPY *.py ./
COPY local_data/ ./local_data/
COPY templates/ ./templates/

# 拷贝前端静态文件到 Flask 模板或静态目录（根据你的 web_app.py 实现）
COPY --from=frontend-build /app/emby-actor-ui/dist/ ./static/

# 启动程序
CMD ["python", "web_app.py"]
