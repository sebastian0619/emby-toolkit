# logger_setup.py
import logging
import sys
from collections import deque
import constants
import os
from concurrent_log_handler import ConcurrentRotatingFileHandler

# --- 定义常量 ---
LOG_FILE_NAME = "app.log"
LOG_FILE_MAX_SIZE_MB = 10
LOG_FILE_BACKUP_COUNT = 49

# --- 前端队列和 Handler ---
frontend_log_queue = deque(maxlen=100)

class FrontendQueueHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def emit(self, record):
        try:
            log_entry = self.format(record)
            frontend_log_queue.append(log_entry)
        except Exception:
            self.handleError(record)

# ★★★ 新增部分 1: 定义 httpx 日志降级过滤器 ★★★
class DowngradeHttpx200Filter(logging.Filter):
    """
    一个将 httpx 库中成功的 "200 OK" 请求日志从 INFO 降级到 DEBUG 的过滤器。
    """
    def filter(self, record):
        # 检查是否是 httpx 的 INFO 级别日志，并且内容是我们想修改的
        if (record.name == 'httpx' and 
            record.levelno == logging.INFO and 
            'HTTP Request:' in record.getMessage() and 
            '"HTTP/1.1 200 OK"' in record.getMessage()):
            
            # 动态修改这条日志的级别
            record.levelname = 'DEBUG'
            record.levelno = logging.DEBUG
            
        # 必须返回 True，否则所有日志都会被这个 filter 拦截掉
        return True

# --- 获取根记录器 ---
logger = logging.getLogger() 
logger.setLevel(logging.DEBUG) # 根级别必须是 DEBUG，才能捕获到降级后的日志

# --- 检查并清空处理器 ---
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
    logging.error(f"Failed to add FrontendQueueHandler: {e}", exc_info=True)

# ★★★ 新增部分 2: 获取 httpx logger 并应用过滤器 ★★★
# 获取名为 'httpx' 的 logger
httpx_logger = logging.getLogger('httpx')
# 为它单独添加我们自定义的过滤器
httpx_logger.addFilter(DowngradeHttpx200Filter())
# 注意：不需要设置 httpx_logger 的 level 或 handler，
# 它会把日志传递给根 logger (propagate=True 默认行为)，由根 logger 的 handler 处理。

# --- 专门用于添加文件日志的函数 ---
def add_file_handler(log_directory: str):
    """
    向根 logger 添加一个线程安全的轮转文件处理器。
    """
    try:
        if not os.path.exists(log_directory):
            os.makedirs(log_directory, exist_ok=True)
            logging.info(f"日志目录已创建: {log_directory}")

        log_file_path = os.path.join(log_directory, LOG_FILE_NAME)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        file_handler = ConcurrentRotatingFileHandler(
            log_file_path,
            "a",
            LOG_FILE_MAX_SIZE_MB * 1024 * 1024,
            LOG_FILE_BACKUP_COUNT,
            encoding='utf-8',
            use_gzip=False
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        
        if not any(isinstance(h, ConcurrentRotatingFileHandler) for h in logger.handlers):
            logger.addHandler(file_handler)
            logging.info(f"文件日志功能已成功配置。日志将写入到: {log_file_path}")
        else:
            logging.warning("文件日志处理器已存在，本次不再重复添加。")

    except Exception as e:
        logging.error(f"配置日志文件处理器时发生错误: {e}", exc_info=True)

# 启动时打印一条消息，表示基础 logger 已就绪
logging.info("基础 Logger (控制台/前端/过滤器) 已初始化。")