# logger_setup.py
import logging
import sys
import constants # 确保导入 constants

# 配置基本的日志记录器
# 您可以根据需要调整日志级别、格式和输出目标（例如文件）

# 创建一个logger实例
logger = logging.getLogger("app_logger") # 给logger起个名字
logger.setLevel(logging.DEBUG if constants.DEBUG else logging.INFO) # 根据 DEBUG 常量设置级别

# 创建一个handler，用于将日志输出到控制台 (stdout)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.DEBUG if constants.DEBUG else logging.INFO)

# 创建一个formatter并将其添加到handler
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stream_handler.setFormatter(formatter)

# 将handler添加到logger
if not logger.handlers: # 防止重复添加handler (例如在Flask自动重载时)
    logger.addHandler(stream_handler)

# (可选) 如果您还想将日志输出到文件，可以添加FileHandler:
# import os
# APP_DATA_PATH = os.environ.get("APP_DATA_DIR", "/config") # 从环境变量或默认值获取
# LOG_DIR = os.path.join(APP_DATA_PATH, "logs")
# os.makedirs(LOG_DIR, exist_ok=True)
# log_file = os.path.join(LOG_DIR, "app.log")
# file_handler = logging.FileHandler(log_file, encoding='utf-8')
# file_handler.setLevel(logging.DEBUG if constants.DEBUG else logging.INFO)
# file_handler.setFormatter(formatter)
# if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
#    logger.addHandler(file_handler)

# 现在，其他模块可以直接 from logger_setup import logger 来使用这个配置好的logger
# 例如: logger.info("这是一条信息")
#        logger.error("这是一条错误")