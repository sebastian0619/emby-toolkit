# --- 阶段 1: 构建前端 ---
FROM node:20-alpine AS frontend-build # 使用 alpine 版本减小此阶段镜像体积

WORKDIR /app

# 假设你的前端项目在 ./emby-actor-ui 子目录下
# 先复制 package.json 和 package-lock.json (或 yarn.lock)
COPY emby-actor-ui/package.json emby-actor-ui/package-lock.json* ./emby-actor-ui/
# 如果使用 yarn，则是 yarn.lock

WORKDIR /app/emby-actor-ui
RUN npm install # 或者 yarn install

# 复制前端所有剩余代码
COPY emby-actor-ui/ ./

# 执行构建命令
RUN npm run build # 构建产物在 /app/emby-actor-ui/dist/

# --- 阶段 2: 构建最终的生产镜像 ---
FROM python:3.11-slim

WORKDIR /app

# 安装 Python 依赖 (先复制 requirements.txt 以利用缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码
# (确保你复制了所有必要的后端文件和目录)
COPY web_app.py .
COPY core_processor.py .
COPY douban.py .
COPY tmdb_handler.py .
COPY emby_handler.py .
COPY utils.py .
COPY logger_setup.py .
COPY constants.py .
# 如果有 templates 目录并且 Flask 会用到，则复制
# COPY templates/ ./templates/
# 如果有 /config 目录的需求，可以在运行时通过 volume 挂载，或者在这里创建
# RUN mkdir -p /config 

# 从前端构建阶段拷贝编译好的静态文件到 Flask 的静态文件目录
COPY --from=frontend-build /app/emby-actor-ui/dist /app/static

# 暴露 Flask 应用的端口 (例如 5000)
EXPOSE 5257

# 启动程序 (使用 Gunicorn 或 Waitress 替换 Flask 开发服务器用于生产)
# CMD ["gunicorn", "--bind", "0.0.0.0:5000", "web_app:app"]
CMD ["python", "web_app.py"]