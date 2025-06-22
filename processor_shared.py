# file: processor_shared.py

# --- 共享的导入 ---
import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
import threading
import time
import re

# --- 共享的自定义模块导入 ---
import emby_handler
import tmdb_handler
import utils
from logger_setup import logger
import constants
from ai_translator import AITranslator

# --- 共享的全局变量和检查 ---
try:
    from douban import DoubanApi
    DOUBAN_API_AVAILABLE = True
    logger.debug("DoubanApi 模块已成功导入 (共享模块)。")
except ImportError:
    logger.error("错误: douban.py 文件未找到或 DoubanApi 类无法导入 (共享模块)。")
    DOUBAN_API_AVAILABLE = False
    # 创建一个假的 DoubanApi 类
    class DoubanApi:
        def __init__(self, *args, **kwargs): logger.warning("使用的是假的 DoubanApi 实例。")
        def get_acting(self, *args, **kwargs): return {"error": "DoubanApi not available", "cast": []}
        def close(self): pass
        @staticmethod
        def _get_translation_from_db(*args, **kwargs): return None
        @staticmethod
        def _save_translation_to_db(*args, **kwargs): pass

# --- 共享的辅助函数 (如果它们只被处理器使用) ---