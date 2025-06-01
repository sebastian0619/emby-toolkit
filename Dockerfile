# 使用官方Python基础镜像 (slim版本比较小)
FROM python:3.11-slim

# 设置工作目录，后续的命令都会在这个目录下执行
WORKDIR /app

# 设置环境变量，例如Python不缓冲标准输出，方便看日志
ENV PYTHONUNBUFFERED 1

# 安装系统级的依赖，包括 Node.js 和 npm (用于 translators 库)
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# 复制依赖描述文件到工作目录
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir --default-timeout=100 --retries 5 -r requirements.txt

# 复制所有项目文件到工作目录
COPY . . 

# 暴露Flask应用监听的端口
EXPOSE 5257

# 容器启动时执行的命令
CMD ["python", "web_app.py"]