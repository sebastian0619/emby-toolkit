# logger_setup.py (最终修复版)

import logging
import sys
from collections import deque
import constants
import os
# from logging.handlers import RotatingFileHandler # 我们将换成更安全的版本
from concurrent_log_handler import ConcurrentRotatingFileHandler # 强烈推荐

# --- 定义常量 ---
LOG_FILE_NAME = "app.log"
LOG_FILE_MAX_SIZE_MB = 5  # 稍微调大一点
LOG_FILE_BACKUP_COUNT = 5

# --- 前端队列和 Handler (保持不变) ---
frontend_log_queue = deque()

class FrontendQueueHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def emit(self, record):
        try:
            log_entry = self.format(record)
            frontend_log_queue.append(log_entry)
        except Exception:
            self.handleError(record)

# --- ★★★ 核心修改 1: 获取根记录器，而不是 'app_logger' ★★★ ---
# 获取根记录器，它是所有 logger 的祖先
logger = logging.getLogger() 
logger.setLevel(logging.DEBUG) # 在根上设置最低级别

# --- ★★★ 核心修改 2: 检查并清空处理器，确保配置的唯一性 ★★★ ---
# 这一步至关重要，可以防止因任何原因重复执行此文件而导致处理器重复添加
if logger.hasHandlers():
    logger.handlers.clear()

# --- 初始化基础 Handler ---

# 1. 控制台 Handler
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)
console_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
    # 注意：此时 logger 可能还没有文件 handler，所以这个错误主要会显示在控制台
    logging.error(f"Failed to add FrontendQueueHandler: {e}", exc_info=True)


# --- 专门用于添加文件日志的函数 (保持不变，但内部实现更健壮) ---
def add_file_handler(log_directory: str):
    """
    向根 logger 添加一个线程安全的轮转文件处理器。
    """
    try:
        if not os.path.exists(log_directory):
            os.makedirs(log_directory, exist_ok=True)
            # 使用 logging 自己的 logger，因为它此时肯定可用
            logging.info(f"日志目录已创建: {log_directory}")

        log_file_path = os.path.join(log_directory, LOG_FILE_NAME)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # ★★★ 使用线程安全的 Handler，解决 WinError 32 问题 ★★★
        file_handler = ConcurrentRotatingFileHandler(
            log_file_path,
            "a", # mode
            LOG_FILE_MAX_SIZE_MB * 1024 * 1024,
            LOG_FILE_BACKUP_COUNT,
            encoding='utf-8',
            use_gzip=True
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        
        # 检查是否已经存在文件处理器，避免重复添加
        if not any(isinstance(h, ConcurrentRotatingFileHandler) for h in logger.handlers):
            logger.addHandler(file_handler)
            logging.info(f"文件日志功能已成功配置。日志将写入到: {log_file_path}")
        else:
            logging.warning("文件日志处理器已存在，本次不再重复添加。")

    except Exception as e:
        logging.error(f"配置日志文件处理器时发生错误: {e}", exc_info=True)

# 启动时打印一条消息，表示基础 logger 已就绪
logging.info("基础 Logger (控制台/前端) 已初始化。")