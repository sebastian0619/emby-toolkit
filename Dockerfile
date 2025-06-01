# 使用一个官方的Python基础镜像 (slim版本比较小)
FROM python:3.11-slim

# 设置工作目录，后续的命令都会在这个目录下执行
WORKDIR /app

# (可选) 设置环境变量，例如Python不缓冲标准输出，方便看日志
ENV PYTHONUNBUFFERED 1

# (可选) 安装一些系统级的依赖，如果Python库需要编译或依赖它们
# 例如，如果 translators 库的某些引擎需要 nodejs:
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends nodejs npm && \
#     rm -rf /var/lib/apt/lists/*
# (请根据 translators 库的实际需求来决定是否需要这部分)

# 复制依赖描述文件到工作目录
COPY requirements.txt .

# 安装Python依赖
# --no-cache-dir 减少镜像大小
# --default-timeout=100 增加超时时间，防止网络慢导致安装失败
# --retries 5 增加重试次数
RUN pip install --no-cache-dir --default-timeout=100 --retries 5 -r requirements.txt

# 复制项目中的所有文件到工作目录
# 注意：确保 .dockerignore 文件配置正确，以排除不必要的文件
COPY . .

# 暴露Flask应用监听的端口 (与 web_app.py 中 app.run 的 port 一致)
EXPOSE 5257

# 容器启动时执行的命令
# 使用 gunicorn 作为生产环境的WSGI服务器会更健壮，但初期测试用Flask自带的也可以
# 如果使用Flask自带服务器：
CMD ["python", "web_app.py"]
# 如果将来使用gunicorn (需要在requirements.txt中添加gunicorn):
# CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "web_app:app"]
# gunicorn的 workers 和 threads 数量可以根据您的NAS性能调整，初步用1个worker，几个线程即可