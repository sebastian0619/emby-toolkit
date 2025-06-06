# --- 阶段 1: 构建前端 ---
# 使用一个包含 Node.js 的基础镜像来构建 Vue 前端
FROM node:18-alpine AS build-stage

# 设置工作目录
WORKDIR /app

# 复制 package.json 和 lock 文件
COPY package*.json ./

# 安装前端依赖
# 如果你的依赖很少变动，这一步会被缓存，加快后续构建速度
RUN npm install

# 复制所有前端代码到容器中
COPY . .

# 执行构建命令，生成静态文件
# 这会在 /app/dist 目录下生成 index.html, CSS, JS 等文件
RUN npm run build


# --- 阶段 2: 构建最终的生产镜像 ---
# 使用一个包含 Python 的基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 从构建阶段 (build-stage) 复制编译好的前端静态文件到后端的工作目录
# 我们将它放在一个名为 'static' 的子目录中，这是 Flask 的标准实践
COPY --from=build-stage /app/dist /app/static

# 安装后端依赖
# 先复制 requirements.txt 以利用 Docker 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有后端代码
# 注意：这里我们只复制后端需要的文件，而不是整个项目
# 假设你的后端代码都在一个或几个特定的目录/文件中
COPY web_app.py .
COPY core_processor.py .
COPY douban.py .
COPY tmdb_handler.py .
COPY emby_handler.py .
COPY utils.py .
COPY logger_setup.py .
COPY constants.py .
# 如果有其他后端文件或目录，也一并复制

# 暴露 Flask 应用的端口
EXPOSE 5257

# 定义容器启动时运行的命令
# 使用 gunicorn 或 waitress 等生产级 WSGI 服务器来运行 Flask 应用
# 这里以 gunicorn 为例，你需要将它添加到 requirements.txt
# CMD ["gunicorn", "--bind", "0.0.0.0:5000", "web_app:app"]

# 或者，如果为了简单，仍然使用 Flask 的开发服务器（不推荐用于生产）
CMD ["python", "web_app.py"]