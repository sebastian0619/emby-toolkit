# logger_setup.py
import logging
import sys
from collections import deque
import constants

# --- 新增：用于前端实时日志的全局队列 ---
# maxlen=200 表示最多只保留最新的200条日志给前端，防止内存无限增长
# 这个队列将被 web_app.py 导入并使用
frontend_log_queue = deque(maxlen=200)

# --- 新增：自定义的日志处理器 ---
class FrontendQueueHandler(logging.Handler):
    """
    一个自定义的日志处理器，它将格式化后的日志记录发送到全局的 deque 中。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def emit(self, record):
        """在日志被记录时调用此方法。"""
        # self.format(record) 会根据设置的 formatter 将日志记录转换为字符串
        try:
            log_entry = self.format(record)
            frontend_log_queue.append(log_entry)
        except Exception:
            # 在日志处理的emit方法中，最好不要让异常抛出，以免影响主程序
            self.handleError(record)


# --- 原有的 logger 初始化代码 ---
logger = logging.getLogger("app_logger")
logger.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)

# --- 配置并添加 Handler ---
# 使用 `if not logger.hasHandlers()` 或 `if not logger.handlers` 来防止在 Flask 自动重载时重复添加
if not logger.handlers:
    # 1. 控制台 Handler (保持不变)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG if constants.DEBUG_MODE else logging.INFO)
    # 给控制台一个详细的格式
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'
    )
    stream_handler.setFormatter(console_formatter)
    logger.addHandler(stream_handler)

    # 2. 新增的前端队列 Handler
    try:
        frontend_handler = FrontendQueueHandler()
        # 我们只给前端看 INFO 及以上级别的日志，避免过多的 DEBUG 信息刷屏
        frontend_handler.setLevel(logging.INFO)

        # 给它一个简洁的格式，带上时间戳，这样前端就不用自己加了
        frontend_formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
        frontend_handler.setFormatter(frontend_formatter)

        logger.addHandler(frontend_handler)
        # 这条初始日志现在也会被捕获到前端队列中
        logger.info("Logger setup complete. FrontendQueueHandler is active.")
    except Exception as e:
        # 如果添加失败，至少在控制台能看到错误
        logger.error(f"Failed to add FrontendQueueHandler: {e}", exc_info=True)

# (可选的文件 Handler 代码可以放在这里)