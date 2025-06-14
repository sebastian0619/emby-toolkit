# logger_setup.py

import logging
import sys
from collections import deque
import constants
import os
from logging.handlers import RotatingFileHandler # ✨ 1. 导入 RotatingFileHandler

# --- 2. 从 web_app 导入持久化数据路径 ---
# 我们需要知道日志文件应该存放在哪里。
# 为了避免循环导入，我们只在需要时导入这个变量。
try:
    from web_app import PERSISTENT_DATA_PATH
except (ImportError, ModuleNotFoundError):
    # 如果导入失败（例如，在独立测试此模块时），提供一个后备路径
    PERSISTENT_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_data_fallback")
    if not os.path.exists(PERSISTENT_DATA_PATH):
        os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
    print(f"[LOGGER_SETUP_WARNING] Could not import PERSISTENT_DATA_PATH from web_app.py. Using fallback path: {PERSISTENT_DATA_PATH}")

# --- 3. 定义日志文件相关的常量 ---
LOG_FILE_NAME = "app.log"
LOG_FILE_PATH = os.path.join(PERSISTENT_DATA_PATH, LOG_FILE_NAME)
LOG_FILE_MAX_SIZE_MB = 3  # 日志文件最大大小 (MB)
LOG_FILE_BACKUP_COUNT = 5 # 保留的旧日志文件数量

# --- 前端实时日志的全局队列 (保持不变) ---
frontend_log_queue = deque(maxlen=200)

# --- 自定义的日志处理器 (保持不变) ---
class FrontendQueueHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def emit(self, record):
        try:
            log_entry = self.format(record)
            frontend_log_queue.append(log_entry)
        except Exception:
            self.handleError(record)

# --- 原有的 logger 初始化代码 (保持不变) ---
logger = logging.getLogger("app_logger")
logger.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)

# --- 配置并添加 Handler (核心修改区域) ---
if not logger.handlers:
    # 1. 控制台 Handler (保持不变)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'
    )
    stream_handler.setFormatter(console_formatter)
    logger.addHandler(stream_handler)

    # 2. 前端队列 Handler (保持不变)
    try:
        frontend_handler = FrontendQueueHandler()
        frontend_handler.setLevel(logging.INFO)
        frontend_formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
        frontend_handler.setFormatter(frontend_formatter)
        logger.addHandler(frontend_handler)
    except Exception as e:
        logger.error(f"Failed to add FrontendQueueHandler: {e}", exc_info=True)

    # ✨ 3. 新增的文件轮转 Handler ✨
    try:
        # 确保日志文件所在的目录存在
        log_dir = os.path.dirname(LOG_FILE_PATH)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # 创建一个轮转文件处理器
        file_handler = RotatingFileHandler(
            LOG_FILE_PATH,
            maxBytes=LOG_FILE_MAX_SIZE_MB * 1024 * 1024, # 将 MB 转换为字节
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding='utf-8'
        )
        # 文件中记录 DEBUG 及以上的所有日志，方便排查最详细的问题
        file_handler.setLevel(logging.DEBUG) 
        # 文件日志使用和控制台一样的详细格式
        file_handler.setFormatter(console_formatter)
        logger.addHandler(file_handler)
        
    except Exception as e:
        # 如果文件日志配置失败，至少在控制台能看到错误
        logger.error(f"Failed to configure file logger: {e}", exc_info=True)

    # 这条初始日志现在会被所有三个 handler 捕获
    logger.info("Logger setup complete. All handlers (Console, Frontend, File) are active.")