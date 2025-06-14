# logger_setup.py

import logging
import sys
from collections import deque
import constants
import os
from logging.handlers import RotatingFileHandler

# --- 定义常量，但不立即使用路径 ---
LOG_FILE_NAME = "app.log"
LOG_FILE_MAX_SIZE_MB = 3
LOG_FILE_BACKUP_COUNT = 5

# --- 前端队列和 Handler (保持不变) ---
frontend_log_queue = deque(maxlen=200)

class FrontendQueueHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def emit(self, record):
        try:
            log_entry = self.format(record)
            frontend_log_queue.append(log_entry)
        except Exception:
            self.handleError(record)

# --- 初始化基础 Logger (不包含文件 Handler) ---
logger = logging.getLogger("app_logger")
logger.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)

if not logger.handlers:
    # 1. 控制台 Handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'
    )
    stream_handler.setFormatter(console_formatter)
    logger.addHandler(stream_handler)

    # 2. 前端队列 Handler
    try:
        frontend_handler = FrontendQueueHandler()
        frontend_handler.setLevel(logging.INFO)
        frontend_formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
        frontend_handler.setFormatter(frontend_formatter)
        logger.addHandler(frontend_handler)
    except Exception as e:
        logger.error(f"Failed to add FrontendQueueHandler: {e}", exc_info=True)

# ✨✨✨ 核心修改：创建一个专门用于添加文件日志的函数 ✨✨✨
def add_file_handler(log_directory: str):
    """
    向主 logger 添加一个轮转文件处理器。
    这个函数应该在确定了持久化数据路径后被调用。
    """
    try:
        if not os.path.exists(log_directory):
            os.makedirs(log_directory, exist_ok=True)
            logger.info(f"日志目录已创建: {log_directory}")

        log_file_path = os.path.join(log_directory, LOG_FILE_NAME)
        
        # 使用和控制台一样的详细格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'
        )

        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=LOG_FILE_MAX_SIZE_MB * 1024 * 1024,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        
        # 检查是否已经存在相同类型的文件处理器，避免重复添加
        if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
            logger.addHandler(file_handler)
            logger.info(f"文件日志功能已成功配置。日志将写入到: {log_file_path}")
        else:
            logger.warning("文件日志处理器已存在，本次不再重复添加。")

    except Exception as e:
        logger.error(f"配置日志文件处理器时发生错误: {e}", exc_info=True)

# 启动时打印一条消息，表示基础 logger 已就绪
logger.info("基础 Logger (控制台/前端) 已初始化。")