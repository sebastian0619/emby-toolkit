# web_app.py
import os
import re
import json
import inspect
import sqlite3
import shutil
from actor_sync_handler import UnifiedSyncHandler
import emby_handler
import utils
from utils import LogDBManager
import configparser
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, stream_with_context, send_from_directory,Response
from werkzeug.utils import safe_join
from queue import Queue
from functools import wraps
from utils import get_override_path_for_item
from watchlist_processor import WatchlistProcessor
import threading
import time
from datetime import datetime
import requests
from douban import DoubanApi
from typing import Optional, Dict, Any, List, Tuple, Union # 确保 List 被导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
import atexit # 用于应用退出处理
from core_processor import MediaProcessor
import csv
from io import StringIO
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from actor_utils import ActorDBManager, enrich_all_actor_aliases_task
from actor_utils import get_db_connection as get_central_db_connection
from flask import session
from croniter import croniter
import logging
# --- 核心模块导入 ---
import constants # 你的常量定义\
import logging
from logger_setup import frontend_log_queue, add_file_handler # 日志记录器和前端日志队列
import utils       # 例如，用于 /api/search_media
# --- 核心模块导入结束 ---
logger = logging.getLogger(__name__)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
static_folder='static'
app = Flask(__name__)
# CORS(app) # 最简单的全局启用 CORS，允许所有源
# app.secret_key = os.urandom(24) # 用于 flash 消息等
# ✨✨✨ 新增：导入我们创建的网页解析器 ✨✨✨
try:
    from web_parser import parse_cast_from_url, ParserError
    WEB_PARSER_AVAILABLE = True
except ImportError:
    logger.error("web_parser.py 未找到或无法导入，从URL提取功能将不可用。")
    WEB_PARSER_AVAILABLE = False
# vue_dev_server_origin = "http://localhost:5173"
# CORS(app, resources={r"/api/*": {"origins": vue_dev_server_origin}})
# --- 路径和配置定义 ---
APP_DATA_DIR_ENV = os.environ.get("APP_DATA_DIR")
app = Flask(__name__, static_folder='static')
app.secret_key = os.urandom(24)
# ✨✨✨ “配置清单” ✨✨✨
CONFIG_DEFINITION = {
    # [Emby]
    constants.CONFIG_OPTION_EMBY_SERVER_URL: (constants.CONFIG_SECTION_EMBY, 'string', ""),
    constants.CONFIG_OPTION_EMBY_API_KEY: (constants.CONFIG_SECTION_EMBY, 'string', ""),
    constants.CONFIG_OPTION_EMBY_USER_ID: (constants.CONFIG_SECTION_EMBY, 'string', ""),
    constants.CONFIG_OPTION_REFRESH_AFTER_UPDATE: (constants.CONFIG_SECTION_EMBY, 'boolean', True),
    constants.CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS: (constants.CONFIG_SECTION_EMBY, 'list', []),

    # [TMDB]
    constants.CONFIG_OPTION_TMDB_API_KEY: (constants.CONFIG_SECTION_TMDB, 'string', ""),

    # [DoubanAPI]
    constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN: (constants.CONFIG_SECTION_API_DOUBAN, 'float', 1.0),
    constants.CONFIG_OPTION_DOUBAN_COOKIE: (constants.CONFIG_SECTION_API_DOUBAN, 'string', ""),

    # [Translation]
    constants.CONFIG_OPTION_TRANSLATOR_ENGINES: (constants.CONFIG_SECTION_TRANSLATION, 'list', constants.DEFAULT_TRANSLATOR_ENGINES_ORDER),
    
    # [LocalDataSource]
    constants.CONFIG_OPTION_LOCAL_DATA_PATH: (constants.CONFIG_SECTION_LOCAL_DATA, 'string', ""),

    # [General]
    "delay_between_items_sec": ("General", 'float', 0.5),
    constants.CONFIG_OPTION_MIN_SCORE_FOR_REVIEW: ("General", 'float', constants.DEFAULT_MIN_SCORE_FOR_REVIEW),
    constants.CONFIG_OPTION_PROCESS_EPISODES: ("General", 'boolean', True),
    constants.CONFIG_OPTION_SYNC_IMAGES: ("General", 'boolean', False),
    constants.CONFIG_OPTION_MAX_ACTORS_TO_PROCESS: ("General", 'int', constants.DEFAULT_MAX_ACTORS_TO_PROCESS),

    # [Network]
    "user_agent": ("Network", 'string', 'Mozilla/5.0 ...'), # 省略默认值
    "accept_language": ("Network", 'string', 'zh-CN,zh;q=0.9,en;q=0.8'),

    # [AITranslation]
    constants.CONFIG_OPTION_AI_TRANSLATION_ENABLED: (constants.CONFIG_SECTION_AI_TRANSLATION, 'boolean', False),
    constants.CONFIG_OPTION_AI_PROVIDER: (constants.CONFIG_SECTION_AI_TRANSLATION, 'string', "openai"),
    constants.CONFIG_OPTION_AI_API_KEY: (constants.CONFIG_SECTION_AI_TRANSLATION, 'string', ""),
    constants.CONFIG_OPTION_AI_MODEL_NAME: (constants.CONFIG_SECTION_AI_TRANSLATION, 'string', "deepseek-ai/DeepSeek-V2.5"),
    constants.CONFIG_OPTION_AI_BASE_URL: (constants.CONFIG_SECTION_AI_TRANSLATION, 'string', "https://api.siliconflow.cn/v1"),
    constants.CONFIG_OPTION_AI_TRANSLATION_MODE: (
        constants.CONFIG_SECTION_AI_TRANSLATION, # 属于 AITranslation 部分
        'string',                                # 它的值是一个字符串
        'fast'                                   # 默认值为 'fast' (翻译模式)
    ),

    # [Scheduler]
    constants.CONFIG_OPTION_SCHEDULE_ENABLED: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_CRON: (constants.CONFIG_SECTION_SCHEDULER, 'string', "0 3 * * *"),
    constants.CONFIG_OPTION_SCHEDULE_FORCE_REPROCESS: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_SYNC_MAP_ENABLED: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_SYNC_MAP_CRON: (constants.CONFIG_SECTION_SCHEDULER, 'string', "0 1 * * *"),
    constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_ENABLED: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_CRON: (constants.CONFIG_SECTION_SCHEDULER, 'string', constants.DEFAULT_SCHEDULE_WATCHLIST_CRON),
    constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_ENABLED: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_CRON: (constants.CONFIG_SECTION_SCHEDULER, 'string', "30 2 * * *"),
    constants.CONFIG_OPTION_SCHEDULE_ENRICH_DURATION_MINUTES: (constants.CONFIG_SECTION_SCHEDULER, 'int', 420), # 默认420分钟 = 7小时
    constants.CONFIG_OPTION_SCHEDULE_ENRICH_SYNC_INTERVAL_DAYS: (constants.CONFIG_SECTION_SCHEDULER, 'int', constants.DEFAULT_ENRICH_ALIASES_SYNC_INTERVAL_DAYS),
    constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_ENABLED: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', True),
    constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_CRON: (constants.CONFIG_SECTION_SCHEDULER, 'string', constants.DEFAULT_SCHEDULE_ACTOR_CLEANUP_CRON),

    # [Authentication]
    constants.CONFIG_OPTION_AUTH_ENABLED: (constants.CONFIG_SECTION_AUTH, 'boolean', False),
    constants.CONFIG_OPTION_AUTH_USERNAME: (constants.CONFIG_SECTION_AUTH, 'string', constants.DEFAULT_USERNAME),
    constants.CONFIG_OPTION_ACTOR_ROLE_ADD_PREFIX: (constants.CONFIG_SECTION_ACTOR, 'boolean', False),
}
if APP_DATA_DIR_ENV:
    # 如果在 Docker 中，并且设置了 APP_DATA_DIR 环境变量 (例如设置为 "/config")
    PERSISTENT_DATA_PATH = APP_DATA_DIR_ENV
    logger.info(f"检测到 APP_DATA_DIR 环境变量，将使用持久化数据路径: {PERSISTENT_DATA_PATH}")
else:
    # 本地开发环境：在 web_app.py 文件所在的目录的上一级，创建一个名为 'local_data' 的文件夹
    # 或者，如果你希望 local_data 与 web_app.py 同级，可以调整 BASE_DIR_FOR_DATA
    # BASE_DIR_FOR_DATA = os.path.dirname(os.path.abspath(__file__)) # web_app.py 所在目录
    # PERSISTENT_DATA_PATH = os.path.join(BASE_DIR_FOR_DATA, "local_data")
    
    # 更常见的本地开发做法：数据目录在项目根目录（假设 web_app.py 在项目根目录或子目录）
    # 如果 web_app.py 在项目根目录:
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    # 如果 web_app.py 在类似 src/ 的子目录，你可能需要 os.path.dirname(PROJECT_ROOT)
    PERSISTENT_DATA_PATH = os.path.join(PROJECT_ROOT, "local_data")
    logger.debug(f"未检测到 APP_DATA_DIR 环境变量，将使用本地开发数据路径: {PERSISTENT_DATA_PATH}")

# 确保这个持久化数据目录存在 (无论是在本地还是在容器内)
try:
    if not os.path.exists(PERSISTENT_DATA_PATH):
        os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
        logger.info(f"持久化数据目录已创建/确认: {PERSISTENT_DATA_PATH}")
except OSError as e:
    logger.error(f"创建持久化数据目录 '{PERSISTENT_DATA_PATH}' 失败: {e}。程序可能无法正常读写配置文件和数据库。")
    # 在这种情况下，程序可能无法继续，可以考虑退出或抛出异常
    # raise RuntimeError(f"无法创建必要的数据目录: {PERSISTENT_DATA_PATH}") from e

CONFIG_FILE_NAME = getattr(constants, 'CONFIG_FILE_NAME', "config.ini")
CONFIG_FILE_PATH = os.path.join(PERSISTENT_DATA_PATH, CONFIG_FILE_NAME)

DB_NAME = getattr(constants, 'DB_NAME', "emby_actor_processor.sqlite")
DB_PATH = os.path.join(PERSISTENT_DATA_PATH, DB_NAME)
add_file_handler(PERSISTENT_DATA_PATH)
logger.info(f"配置文件路径 (CONFIG_FILE_PATH) 设置为: {CONFIG_FILE_PATH}")
logger.info(f"数据库文件路径 (DB_PATH) 设置为: {DB_PATH}")
logging.basicConfig(
    level=logging.INFO,
    # ✨ 关键在这里：设置你想要的格式 ✨
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)

# --- 全局变量 ---
media_processor_instance: Optional[MediaProcessor] = None
background_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务"
}
task_lock = threading.Lock() # 用于确保后台任务串行执行
APP_CONFIG: Dict[str, Any] = {} # ✨✨✨ 新增：全局配置字典 ✨✨✨
media_processor_instance: Optional[MediaProcessor] = None
watchlist_processor_instance: Optional[WatchlistProcessor] = None

# ✨✨✨ 任务队列 ✨✨✨
task_queue = Queue()
task_worker_thread: Optional[threading.Thread] = None
task_worker_lock = threading.Lock()

scheduler = BackgroundScheduler(timezone=str(pytz.timezone(constants.TIMEZONE)))
JOB_ID_FULL_SCAN = "scheduled_full_scan"
JOB_ID_SYNC_PERSON_MAP = "scheduled_sync_person_map"
JOB_ID_PROCESS_WATCHLIST = "scheduled_process_watchlist"
# --- 全局变量结束 ---

# --- 数据库辅助函数 ---
def task_process_single_item(processor: MediaProcessor, item_id: str, force_reprocess: bool, process_episodes: bool):
    """任务：处理单个媒体项"""
    processor.process_single_item(item_id, force_reprocess, process_episodes)
# --- 初始化数据库 ---
def init_db():
    """
    【重建版】初始化数据库，创建面向未来的统一表结构。
    此版本已移除旧的、分离的演员表，并引入了统一的身份管理体系。
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        # --- 1. 准备工作：创建目录并获取连接 ---
        if not os.path.exists(PERSISTENT_DATA_PATH):
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
            logger.info(f"持久化数据目录已创建: {PERSISTENT_DATA_PATH}")

        conn = get_central_db_connection(DB_PATH)
        cursor = conn.cursor()

        # --- 2. 性能优化：启用 WAL 模式 ---
        # 提高并发读写性能，是现代 SQLite 应用的标配。
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            result = cursor.fetchone()
            if result and result[0].lower() == 'wal':
                logger.debug("数据库已成功启用 WAL (Write-Ahead Logging) 模式。")
            else:
                logger.warning(f"尝试启用 WAL 模式失败，当前模式: {result[0] if result else '未知'}。")
        except Exception as e_wal:
            logger.error(f"启用 WAL 模式时出错: {e_wal}")

        # --- 3. 创建基础表 (日志、缓存、用户) ---
        logger.debug("正在确认/创建基础表...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_log (
                item_id TEXT PRIMARY KEY, item_name TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, score REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_log (
                item_id TEXT PRIMARY KEY, item_name TEXT,reason TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error_message TEXT, item_type TEXT, score REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_cache (
                original_text TEXT PRIMARY KEY, translated_text TEXT,
                engine_used TEXT, last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.debug("基础表结构已确认。")

        # --- 4. 创建核心功能表 (追剧列表) ---
        logger.debug("正在确认/创建 'watchlist' 表...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                item_id TEXT PRIMARY KEY,
                tmdb_id TEXT NOT NULL,
                item_name TEXT,
                item_type TEXT DEFAULT 'Series',
                status TEXT DEFAULT 'Watching', -- 'Watching', 'Paused', 'Completed'
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked_at TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist (status)")
        logger.debug("表 'watchlist' 结构已确认。")


        # 核心表：person_identity_map (单一事实来源)
        # 职责：存储每个演员的唯一身份和跨平台ID映射。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_identity_map (
                -- 中立的内部主键，我们的地盘我们做主！
                map_id INTEGER PRIMARY KEY AUTOINCREMENT,
                
                -- 权威的、用户友好的名字
                primary_name TEXT NOT NULL,
                -- (可选) 使用JSON存储其他平台的名字，如 {"tmdb": "Yan Ni", "douban": "闫妮"}

                -- 所有外部ID，都应该是 UNIQUE 且允许为 NULL
                emby_person_id TEXT UNIQUE,
                tmdb_person_id INTEGER UNIQUE,
                imdb_id TEXT UNIQUE,
                douban_celebrity_id TEXT UNIQUE,

                -- 时间戳
                last_synced_at TIMESTAMP,
                last_updated_at TIMESTAMP
            )
        """)
        # 为所有外部ID创建索引，加速查找和冲突检测
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_emby_id ON person_identity_map (emby_person_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_tmdb_id ON person_identity_map (tmdb_person_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_imdb_id ON person_identity_map (imdb_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_douban_id ON person_identity_map (douban_celebrity_id)")
        logger.debug("  -> [核心] 'person_identity_map' 表已创建。")

        # --- 6. 提交事务 ---
        conn.commit()
        logger.info(f"数据库重建完成！所有表结构已在 '{DB_PATH}' 中创建。")

    except sqlite3.Error as e_sqlite:
        logger.error(f"数据库初始化时发生 SQLite 错误: {e_sqlite}", exc_info=True)
        if conn:
            try: conn.rollback()
            except Exception as e_rb: logger.error(f"SQLite 错误后回滚失败: {e_rb}")
    except Exception as e_global:
        logger.error(f"数据库初始化时发生未知错误: {e_global}", exc_info=True)
        if conn:
            try: conn.rollback()
            except Exception as e_rb: logger.error(f"未知错误后回滚失败: {e_rb}")
    finally:
        if conn:
            conn.close()
            logger.debug("数据库连接已在 init_db 的 finally 块中安全关闭。")
# ✨✨✨ 装饰器：检查登陆状态 ✨✨✨
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # ★★★ 核心修复：正确地解包 load_config 返回的元组 ★★★
        if not APP_CONFIG.get(constants.CONFIG_OPTION_AUTH_ENABLED, False) or 'user_id' in session:
            return f(*args, **kwargs)
        
        return jsonify({"error": "未授权，请先登录"}), 401
    return decorated_function
# ✨✨✨ 装饰器：检查后台任务锁是否被占用 ✨✨✨
def task_lock_required(f):
    """装饰器：检查后台任务锁是否被占用。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if task_lock.locked():
            return jsonify({"error": "后台有任务正在运行，请稍后再试。"}), 409
        return f(*args, **kwargs)
    return decorated_function
# ✨✨✨ 装饰器：检查核心处理器是否已初始化 ✨✨✨
def processor_ready_required(f):
    """装饰器：检查核心处理器是否已初始化。"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not media_processor_instance:
            return jsonify({"error": "核心处理器未就绪。"}), 503
        return f(*args, **kwargs)
    return decorated_function
# --- 初始化认证系统 ---
def init_auth():
    """
    【V2 - 使用全局配置版】初始化认证系统。
    """
    # ✨✨✨ 核心修复：不再自己调用 load_config，而是依赖已加载的 APP_CONFIG ✨✨✨
    # load_config() 应该在主程序入口处被调用一次
    
    auth_enabled = APP_CONFIG.get(constants.CONFIG_OPTION_AUTH_ENABLED, False)
    env_username = os.environ.get("AUTH_USERNAME")
    
    if env_username:
        username = env_username.strip()
        logger.info(f"检测到 AUTH_USERNAME 环境变量，将使用用户名: '{username}'")
    else:
        username = APP_CONFIG.get(constants.CONFIG_OPTION_AUTH_USERNAME, constants.DEFAULT_USERNAME).strip()
        logger.debug(f"未检测到 AUTH_USERNAME 环境变量，将使用配置文件中的用户名: '{username}'")

    if not auth_enabled:
        logger.info("用户认证功能未启用。")
        return

    # ... 函数的其余部分保持不变 ...
    conn = None
    try:
        conn = get_central_db_connection(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if user is None:
            logger.warning(f"[AUTH DIAGNOSTIC] User '{username}' not found in DB. Proceeding to create password.")
            # ... (生成密码的逻辑不变) ...
            random_password = secrets.token_urlsafe(12)
            password_hash = generate_password_hash(random_password)
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash)
            )
            conn.commit()
            logger.critical("=" * 60)
            logger.critical(" " * 20 + "!!! 重要提示 !!!")
            logger.critical(f"首次运行，已为用户 '{username}' 自动生成初始密码。")
            logger.critical(f"用户名: {username}")
            logger.critical(f"初始密码: {random_password}")
            logger.critical("请立即使用此密码登录，并在设置页面修改为您自己的密码。")
            logger.critical("=" * 60)
        else:
            logger.debug(f"[AUTH DIAGNOSTIC] User '{username}' found in DB. No action needed.")

    except Exception as e:
        logger.error(f"初始化认证系统时发生错误: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
        logger.info("="*21 + " [基础配置加载完毕] " + "="*21)
# --- 加载配置 ---
def load_config() -> Tuple[Dict[str, Any], bool]:
    """【清单驱动版】从 config.ini 加载配置。"""
    global APP_CONFIG
    config_parser = configparser.ConfigParser()
    is_first_run = not os.path.exists(CONFIG_FILE_PATH)

    if not is_first_run:
        try:
            config_parser.read(CONFIG_FILE_PATH, encoding='utf-8')
        except Exception as e:
            logger.error(f"解析配置文件时出错: {e}", exc_info=True)

    app_cfg = {}
    
    # 遍历配置清单，自动加载所有配置项
    for key, (section, type, default) in CONFIG_DEFINITION.items():
        if not config_parser.has_section(section):
            config_parser.add_section(section)
            
        if type == 'boolean':
            # 特殊处理首次运行时的认证开关
            if key == constants.CONFIG_OPTION_AUTH_ENABLED and is_first_run:
                app_cfg[key] = True
            else:
                app_cfg[key] = config_parser.getboolean(section, key, fallback=default)
        elif type == 'int':
            app_cfg[key] = config_parser.getint(section, key, fallback=default)
        elif type == 'float':
            app_cfg[key] = config_parser.getfloat(section, key, fallback=default)
        elif type == 'list':
            value_str = config_parser.get(section, key, fallback=",".join(map(str, default)))
            app_cfg[key] = [item.strip() for item in value_str.split(',') if item.strip()]
        else: # string
            app_cfg[key] = config_parser.get(section, key, fallback=default)

    APP_CONFIG = app_cfg.copy()
    logger.debug("全局配置 APP_CONFIG 已更新。")
    return app_cfg, is_first_run
# --- 保存配置 ---
def save_config(new_config: Dict[str, Any]):
    """【清单驱动版】将配置保存到 config.ini。"""
    global APP_CONFIG
    config_parser = configparser.ConfigParser()
    
    # 遍历配置清单，自动设置所有配置项
    for key, (section, type, _) in CONFIG_DEFINITION.items():
        if not config_parser.has_section(section):
            config_parser.add_section(section)
        
        value = new_config.get(key)
        
        # 将值转换为适合写入ini文件的字符串格式
        if isinstance(value, bool):
            value_to_write = str(value).lower()
        elif isinstance(value, list):
            value_to_write = ",".join(map(str, value))
        else:
            value_to_write = str(value)
        value_to_write = value_to_write.replace('%', '%%')
        config_parser.set(section, key, value_to_write)

    try:
        # ... (写入文件和重新初始化的逻辑保持不变) ...
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
            config_parser.write(configfile)
        
        APP_CONFIG = new_config.copy()
        logger.info(f"配置已成功写入到 {CONFIG_FILE_PATH}。")
        
        # 重新初始化相关服务
        initialize_processors()
        init_auth()
        setup_scheduled_tasks()
        logger.info("所有组件已根据新配置重新初始化完毕。")
        
    except Exception as e:
        logger.error(f"保存配置文件或重新初始化时失败: {e}", exc_info=True)
# --- 始化所有需要的处理器实例 ---
def initialize_processors():
    """
    【修复版】初始化所有需要的处理器实例，包括 MediaProcessor 和 WatchlistProcessor。
    """
    # ★★★ 1. 声明所有需要修改的全局变量 ★★★
    global media_processor_instance, watchlist_processor_instance
    
    if not APP_CONFIG:
        logger.error("无法初始化处理器：全局配置 APP_CONFIG 为空。")
        return

    current_config = APP_CONFIG.copy()
    current_config['db_path'] = DB_PATH

    # --- 初始化 MediaProcessor  ---
    if media_processor_instance:
        media_processor_instance.close()
    try:
        media_processor_instance = MediaProcessor(config=current_config)
        logger.info("MediaProcessor 实例已创建/更新。")
    except Exception as e:
        logger.error(f"创建 MediaProcessor 实例失败: {e}", exc_info=True)
        media_processor_instance = None

    # --- ★★★ 2. 新增：初始化 WatchlistProcessor ★★★ ---
    if watchlist_processor_instance:
        try:
            watchlist_processor_instance.close()
        except Exception as e:
            logger.warning(f"关闭旧的 watchlist_processor_instance 时出错: {e}")

    # 追剧功能通常依赖于核心配置，我们在这里创建它，让它随时待命
    # 假设 WatchlistProcessor 也需要 Emby URL 和 API Key
    if current_config.get("emby_server_url") and current_config.get("emby_api_key"):
        try:
            # 假设 WatchlistProcessor 的构造函数和 MediaProcessor 类似，接收一个 config 字典
            watchlist_processor_instance = WatchlistProcessor(config=current_config)
            logger.debug("WatchlistProcessor 实例已成功初始化，随时待命。")
        except Exception as e:
            logger.error(f"创建 WatchlistProcessor 实例失败: {e}", exc_info=True)
            watchlist_processor_instance = None # 初始化失败，明确设为 None
    else:
        logger.warning("WatchlistProcessor 未初始化，因为缺少必要的 Emby 配置。")
        watchlist_processor_instance = None
# --- 后台任务回调 ---
def update_status_from_thread(progress: int, message: str):
    global background_task_status
    if progress >= 0:
        background_task_status["progress"] = progress
    background_task_status["message"] = message
# --- 后台任务封装 ---
def _execute_task_with_lock(task_function, task_name: str, processor: Union[MediaProcessor, WatchlistProcessor], *args, **kwargs):
    """
    【V2 - 工人专用版】通用后台任务执行器。
    第一个参数必须是 MediaProcessor 实例。
    """
    global background_task_status
    # 锁的检查可以移到提交任务的地方，或者保留作为双重保险
    # if task_lock.locked(): ...

    with task_lock:
        # 1. 检查传入的处理器是否有效
        if not processor:
            logger.error(f"任务 '{task_name}' 无法启动：对应的处理器未初始化。")
            # 可以在这里更新状态，但因为没有启动，所以只打日志可能更清晰
            return

        # 2. 清理当前任务处理器的停止信号
        processor.clear_stop_signal()

        # 3. 设置任务状态，准备执行
        background_task_status["is_running"] = True
        background_task_status["current_action"] = task_name
        background_task_status["progress"] = 0
        background_task_status["message"] = f"{task_name} 初始化..."
        logger.info(f"后台任务 '{task_name}' 开始执行。")

        task_completed_normally = False
        try:
            if processor.is_stop_requested():
                raise InterruptedError("任务被取消")

            # 执行核心任务
            task_function(processor, *args, **kwargs)
            
            # ★★★ 核心修复：如果任务能顺利执行到这里，说明它正常完成了 ★★★
            # 我们只在没有被用户中止的情况下，才标记为正常完成
            if not processor.is_stop_requested():
                task_completed_normally = True
        finally:
            final_message_for_status = "未知结束状态"
            current_progress = background_task_status["progress"] # 获取当前进度

            if processor and processor.is_stop_requested():
                final_message_for_status = "任务已成功中断。"
            elif task_completed_normally:
                final_message_for_status = "处理完成。"
                current_progress = 100 # 正常完成则进度100%
            # else: 异常退出时，消息已在except中通过update_status_from_thread设置

            update_status_from_thread(current_progress, final_message_for_status)
            logger.info(f"后台任务 '{task_name}' 结束，最终状态: {final_message_for_status}")

            if processor:
                processor.close()
                logger.debug(f"任务 '{task_name}' 结束 (finally块)，准备调用 media_processor_instance.close() ...")
                try:
                    media_processor_instance.close()
                    logger.debug(f"media_processor_instance.close() 调用完毕 (任务 '{task_name}' finally块)。")
                except Exception as e_close_proc:
                    logger.error(f"调用 media_processor_instance.close() 时发生错误: {e_close_proc}", exc_info=True)

            time.sleep(1)
        background_task_status["is_running"] = False
        background_task_status["current_action"] = "无"
        background_task_status["progress"] = 0
        background_task_status["message"] = "等待任务"
        if processor:
            processor.clear_stop_signal()
        logger.debug(f"后台任务 '{task_name}' 状态已重置。")
# --- 通用队列 ---
def task_worker_function():
    """
    通用工人线程，从队列中获取并处理各种后台任务。
    """
    logger.info("通用任务线程已启动，等待任务...")
    while True:
        try:
            # 从队列中获取任务元组
            task_info = task_queue.get()

            if task_info is None: # 停止信号
                logger.info("工人线程收到停止信号，即将退出。")
                break

            # 解包任务信息
            task_function, task_name, args, kwargs = task_info
            # ★★★ 核心修复：在任务执行前，检查全局实例是否可用 ★★★
            if "追剧" in task_name or "watchlist" in task_function.__name__:
                processor_to_use = watchlist_processor_instance
                logger.debug(f"任务 '{task_name}' 将使用 WatchlistProcessor。")
            else:
                processor_to_use = media_processor_instance
                logger.debug(f"任务 '{task_name}' 将使用 MediaProcessor。")

            if not processor_to_use:
                logger.error(f"任务 '{task_name}' 无法执行：对应的处理器未初始化。")
                task_queue.task_done()
                continue

            _execute_task_with_lock(task_function, task_name, processor_to_use, *args, **kwargs)
            
            task_queue.task_done()
        except Exception as e:
            logger.error(f"通用工人线程发生未知错误: {e}", exc_info=True)
            time.sleep(5)
# --- 安全地启动通用工人线程 ---
def start_task_worker_if_not_running():
    """
    安全地启动通用工人线程。
    """
    global task_worker_thread
    with task_worker_lock:
        if task_worker_thread is None or not task_worker_thread.is_alive():
            logger.debug("通用任务线程未运行，正在启动...")
            task_worker_thread = threading.Thread(target=task_worker_function, daemon=True)
            task_worker_thread.start()
        else:
            logger.debug("通用任务线程已在运行。")
# --- 为通用队列添加任务 ---
def submit_task_to_queue(task_function, task_name: str, *args, **kwargs):
    """
    【修复版】将一个任务提交到通用队列中，并在这里清空日志。
    """
    # ★★★ 核心修改：在提交任务到队列之前，就清空旧日志 ★★★
    # 这个操作应该在 task_lock 的保护下进行，以确保原子性
    with task_lock:
        # 检查是否可以启动新任务
        if background_task_status["is_running"]:
            # 这里可以抛出异常或返回一个状态，让调用方知道任务提交失败
            # 为了简单起见，我们先打印日志并直接返回
            logger.warning(f"任务 '{task_name}' 提交失败：已有任务正在运行。")
            # 或者 raise RuntimeError("已有任务在运行")
            return

        # 如果可以启动，我们就在这里清空日志
        frontend_log_queue.clear()
        logger.info(f"任务 '{task_name}' 已提交到队列，并已清空前端日志。")
        
        task_info = (task_function, task_name, args, kwargs)
        task_queue.put(task_info)
        start_task_worker_if_not_running()
# --- 将 CRON 表达式转换为人类可读的、干净的执行计划字符串 ---
def _get_next_run_time_str(cron_expression: str) -> str:
    """
    【V3 - 口齿伶俐版】将 CRON 表达式转换为人类可读的、干净的执行计划字符串。
    """
    try:
        parts = cron_expression.split()
        if len(parts) != 5:
            raise ValueError("CRON 表达式必须有5个部分")

        minute, hour, day_of_month, month, day_of_week = parts

        # --- 周期描述 ---
        # 优先判断分钟级的周期任务
        if minute.startswith('*/') and all(p == '*' for p in [hour, day_of_month, month, day_of_week]):
            return f"每隔 {minute[2:]} 分钟执行"
        
        # 判断小时级的周期任务
        if hour.startswith('*/') and all(p == '*' for p in [day_of_month, month, day_of_week]):
            # 如果分钟是0，就说是整点
            if minute == '0':
                return f"每隔 {hour[2:]} 小时的整点执行"
            else:
                return f"每隔 {hour[2:]} 小时的第 {minute} 分钟执行"

        # --- 时间点描述 ---
        time_str = f"{hour.zfill(2)}:{minute.zfill(2)}"
        
        # 判断星期
        if day_of_week != '*':
            day_map = {
                '0': '周日', '1': '周一', '2': '周二', '3': '周三', 
                '4': '周四', '5': '周五', '6': '周六', '7': '周日',
                'sun': '周日', 'mon': '周一', 'tue': '周二', 'wed': '周三',
                'thu': '周四', 'fri': '周五', 'sat': '周六'
            }
            days = [day_map.get(d.lower(), d) for d in day_of_week.split(',')]
            return f"每周的 {','.join(days)} {time_str} 执行"
        
        # 判断日期
        if day_of_month != '*':
            if day_of_month.startswith('*/'):
                 return f"每隔 {day_of_month[2:]} 天的 {time_str} 执行"
            else:
                 return f"每月的 {day_of_month} 号 {time_str} 执行"

        # 如果上面都没匹配上，说明是每天
        return f"每天 {time_str} 执行"

    except Exception as e:
        logger.warning(f"无法智能解析CRON表达式 '{cron_expression}': {e}，回退到简单模式。")
        # 保留旧的回退方案
        try:
            # ... (croniter 的回退逻辑保持不变) ...
            tz = pytz.timezone(constants.TIMEZONE)
            now = datetime.now(tz)
            iterator = croniter(cron_expression, now)
            next_run = iterator.get_next(datetime)
            return f"下一次将在 {next_run.strftime('%Y-%m-%d %H:%M')} 执行"
        except:
            return f"按计划 '{cron_expression}' 执行"
# --- 定时任务配置 ---
def setup_scheduled_tasks():
    config = APP_CONFIG
    # --- 处理全量扫描的定时任务 ---
    schedule_scan_enabled = config.get("schedule_enabled", False)
    scan_cron_expression = config.get("schedule_cron", "0 3 * * *")
    force_reprocess_scheduled_scan = config.get("schedule_force_reprocess", False)

    if scheduler.get_job(JOB_ID_FULL_SCAN):
        scheduler.remove_job(JOB_ID_FULL_SCAN)
        # logger.info("已移除旧的定时全量扫描任务。") # 可以选择性保留或移除此日志

    if schedule_scan_enabled:
        try:
            def submit_scheduled_scan_to_queue():
                # ... (内部逻辑保持不变)
                logger.info(f"定时任务触发：准备提交全量扫描到任务队列 (强制={force_reprocess_scheduled_scan})。")
                if force_reprocess_scheduled_scan:
                    logger.info("定时任务：检测到“强制重处理”选项，将在任务开始前清空已处理日志。")
                    if media_processor_instance:
                        media_processor_instance.clear_processed_log()
                    else:
                        logger.error("定时任务：无法清空日志，因为处理器未初始化。")
                current_config, _ = load_config()
                process_episodes = current_config.get('process_episodes', True)
                submit_task_to_queue(
                    task_process_full_library,
                    "定时全量扫描",
                    process_episodes=process_episodes
                )

            scheduler.add_job(
                func=submit_scheduled_scan_to_queue,
                trigger=CronTrigger.from_crontab(scan_cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_FULL_SCAN,
                name="定时全量媒体库扫描",
                replace_existing=True,
            )
            # ✨ 日志优化 ✨
            next_run_str = _get_next_run_time_str(scan_cron_expression)
            force_str = " (强制重处理)" if force_reprocess_scheduled_scan else ""
            logger.info(f"已设置定时任务：全量扫描，将{next_run_str}{force_str}")

        except Exception as e:
            logger.error(f"设置定时全量扫描任务失败: {e}", exc_info=True)
    else:
        logger.info("定时全量扫描任务未启用。")

    # --- 对同步映射表的定时任务也做类似修改 ---
    schedule_sync_map_enabled = config.get("schedule_sync_map_enabled", False)
    sync_map_cron_expression = config.get("schedule_sync_map_cron", "0 1 * * *")

    if scheduler.get_job(JOB_ID_SYNC_PERSON_MAP):
        scheduler.remove_job(JOB_ID_SYNC_PERSON_MAP)

    if schedule_sync_map_enabled:
        try:
            def scheduled_sync_map_task():
                # ... (内部逻辑保持不变)
                logger.info("定时任务触发：演员映射表同步。")
                submit_task_to_queue(
                    task_sync_person_map,
                    "定时同步演员映射表"
                )

            scheduler.add_job(
                func=scheduled_sync_map_task,
                trigger=CronTrigger.from_crontab(sync_map_cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_SYNC_PERSON_MAP, name="定时同步Emby演员映射表", replace_existing=True
            )
            # ✨ 日志优化 ✨
            next_run_str = _get_next_run_time_str(sync_map_cron_expression)
            logger.info(f"已设置定时任务：同步演员映射表，将{next_run_str}")

        except Exception as e:
            logger.error(f"设置定时同步演员映射表任务失败: {e}", exc_info=True)
    else:
        logger.info("定时同步演员映射表任务未启用。")

    # --- 对智能追剧任务也做类似修改 ---
    if scheduler.get_job(JOB_ID_PROCESS_WATCHLIST):
        scheduler.remove_job(JOB_ID_PROCESS_WATCHLIST)

    if config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_ENABLED, False):
            cron_expression = config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_CRON)
            if cron_expression:
                try:
                    def scheduled_watchlist_task():
                        # ... (内部逻辑保持不变)
                        logger.debug("定时任务触发：智能追剧更新。")
                        submit_task_to_queue(task_process_watchlist, "定时智能追剧更新")

                    scheduler.add_job(
                        func=scheduled_watchlist_task,
                        trigger=CronTrigger.from_crontab(cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                        id=JOB_ID_PROCESS_WATCHLIST,
                        name="定时智能追剧更新",
                        replace_existing=True,
                    )
                    # ✨ 日志优化 ✨
                    next_run_str = _get_next_run_time_str(cron_expression)
                    logger.info(f"已设置定时任务：智能追剧更新，将{next_run_str}")

                except Exception as e:
                    logger.error(f"设置定时智能追剧更新任务失败: {e}", exc_info=True)
    else:
        logger.info("定时智能追剧更新任务未启用。")
    # ✨✨✨ 处理外部ID补充任务 ✨✨✨
    job_id_enrich = 'scheduled_enrich_aliases' # 给它一个唯一的ID
    if scheduler.get_job(job_id_enrich):
        scheduler.remove_job(job_id_enrich)

    if config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_ENABLED, False):
        cron_expression = config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_CRON)
        if cron_expression:
            try:
                def scheduled_enrich_task_submitter():
                    logger.debug("定时任务触发：准备提交外部ID补充任务到队列。")
                    submit_task_to_queue(
                        task_enrich_aliases, # <--- 调用我们刚刚创建的任务函数
                        "定时外部ID补充"
                    )

                scheduler.add_job(
                    func=scheduled_enrich_task_submitter, # 调度器调用这个提交者
                    trigger=CronTrigger.from_crontab(cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                    id=job_id_enrich,
                    name="定时补充演员外部ID",
                    replace_existing=True,
                )
                next_run_str = _get_next_run_time_str(cron_expression)
                logger.info(f"已设置定时任务：外部ID补充，将{next_run_str}")
            except Exception as e:
                logger.error(f"设置定时外部ID补充任务失败: {e}", exc_info=True)
    else:
        logger.info("定时外部ID补充任务未启用。")

    # --- ✨✨✨ 演员名翻译查漏补缺任务 ✨✨✨ ---
    JOB_ID_ACTOR_CLEANUP = 'scheduled_actor_translation_cleanup'

    if scheduler.get_job(JOB_ID_ACTOR_CLEANUP):
        scheduler.remove_job(JOB_ID_ACTOR_CLEANUP)

    # 使用常量从配置中读取
    schedule_enabled = config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_ENABLED, True)
    cron_expression = config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_CRON, constants.DEFAULT_SCHEDULE_ACTOR_CLEANUP_CRON)

    if schedule_enabled:
        try:
            def submit_scheduled_actor_cleanup():
                logger.info("定时任务触发：准备提交演员名查漏补缺任务到队列。")
                submit_task_to_queue(
                    task_actor_translation_cleanup, # 任务包装函数
                    "定时演员名查漏补缺"
                )

            scheduler.add_job(
                func=submit_scheduled_actor_cleanup,
                trigger=CronTrigger.from_crontab(cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_ACTOR_CLEANUP,
                name="定时演员名翻译查漏补缺",
                replace_existing=True,
            )
            
            next_run_str = _get_next_run_time_str(cron_expression)
            logger.info(f"已设置定时任务：演员名查漏补缺，将{next_run_str}")

        except Exception as e:
            logger.error(f"设置定时演员名查漏补缺任务失败: {e}", exc_info=True)
    else:
        logger.info("定时演员名查漏补缺任务未启用。")

    # --- 启动调度器逻辑保持不变 ---
    scan_enabled = config.get("schedule_enabled", False)
    sync_enabled = config.get("schedule_sync_map_enabled", False)
    watchlist_enabled = config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_ENABLED, False)
    actor_cleanup_enabled = config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_ENABLED, True)

    # 在 if 条件中加入 actor_cleanup_enabled
    if not scheduler.running and (
        config.get(constants.CONFIG_OPTION_SCHEDULE_ENABLED, False) or
        config.get(constants.CONFIG_OPTION_SCHEDULE_SYNC_MAP_ENABLED, False) or
        config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_ENABLED, False) or
        config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_ENABLED, False) or
        actor_cleanup_enabled # ✨ 添加我们的新任务标志
    ):
        try:
            scheduler.start()
            logger.info("APScheduler 已根据任务需求启动。")
        except Exception as e_scheduler_start:
            logger.error(f"APScheduler 启动失败: {e_scheduler_start}", exc_info=True)
# --- 定时任务结束 ---
def enrich_and_match_douban_cast_to_emby(
    douban_actors_api_data: List[Dict[str, Any]],
    current_emby_cast_list: List[Dict[str, Any]],
    tmdb_api_key: Optional[str],
    db_cursor: sqlite3.Cursor # 注意：这里接收的是游标，不是连接
) -> List[Dict[str, Any]]:
    logger.info(f"enrich_and_match_douban_cast_to_emby: 开始处理 {len(douban_actors_api_data)} 位豆瓣演员，与 {len(current_emby_cast_list)} 位Emby演员进行匹配增强。")
    results = []
    processed_emby_pids_in_this_run = set()

    for d_actor_data in douban_actors_api_data:
        # ... (这里是详细的匹配、TMDb增强、状态标记逻辑) ...
        # ... (确保这个函数内部调用的 match_douban_actor_to_emby_person 也已定义或导入) ...
        # ... (并且 tmdb_handler 也是可用的) ...

        # 示例：
        d_name = d_actor_data.get("name")
        logger.debug(f"  正在处理豆瓣演员: {d_name}")
        # (此处应有完整的匹配和信息组合逻辑)
        # (如果匹配成功，将结果添加到 results 列表)
        # results.append({ "embyPersonId": ..., "name": ..., ... "matchStatus": ... })

    logger.info(f"enrich_and_match_douban_cast_to_emby: 处理完成，返回 {len(results)} 个匹配/增强的演员信息。")
    return results
# --- 一键重构演员数据 ---
def run_full_rebuild_task(self, update_status_callback: Optional[callable] = None, stop_event: Optional[threading.Event] = None):
    """
    【总指挥 - 最终版】执行完整的一键重构演员数据库任务。
    编排所有步骤，并向前端汇报进度。
    'self' 在这里就是 media_processor_instance。
    """
    def _update_status(progress, message):
        """内部辅助函数，用于安全地调用回调并检查中止信号。"""
        if update_status_callback:
            # 确保进度在0-100之间
            safe_progress = max(0, min(100, int(progress)))
            update_status_callback(safe_progress, message)
        if stop_event and stop_event.is_set():
            raise InterruptedError("任务被用户中止")

    try:
        _update_status(0, "任务已启动，正在准备执行阶段 1...")
        # ======================================================================
        # 阶段一：通过API解除所有演员关联 (占总进度的 0% -> 60%)
        # ======================================================================
        _update_status(0, "阶段 1/3: 正在解除所有媒体的演员关联...")
        
        clear_success = emby_handler.clear_all_persons_via_api(
            base_url=self.emby_url,
            api_key=self.emby_api_key,
            user_id=self.emby_user_id,
            # 将此阶段的内部进度(0-100)映射到总进度的0-60
            update_status_callback=lambda p, m: _update_status(int(p * 0.6), f"阶段 1/3: {m}"),
            stop_event=stop_event
        )
        if not clear_success:
            raise RuntimeError("解除演员关联失败，任务中止。")

        # ======================================================================
        # 阶段二：清理本地映射表的EmbyID (占总进度的 60% -> 65%)
        # ======================================================================
        _update_status(60, "阶段 2/3: 正在清空本地映射表中的EmbyID...")
        with get_central_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE person_identity_map SET emby_person_id = NULL;")
            conn.commit()
        logger.info("本地映射表中的EmbyID已全部清空。")
        _update_status(65, "本地映射表清理完成。")

        # ======================================================================
        # 阶段三：触发并监控Emby刷新 (占总进度的 65% -> 85%)
        # ======================================================================
        _update_status(90, "阶段 3/3: 正在触发所有媒体库的深度刷新...")
        
        refresh_success = emby_handler.start_library_scan( # 使用你最可靠的那个触发函数
            base_url=self.emby_url,
            api_key=self.emby_api_key,
            user_id=self.emby_user_id 
        )
        
        if not refresh_success:
            logger.warning("部分或全部媒体库的深度刷新请求可能发送失败，请稍后手动检查Emby。")
        
        # ★★★ 最终的用户提示 ★★★
        final_message = (
            "第一阶段完成！已触发Emby后台刷新。\n"
            "请在Emby中确认媒体库刷新完成后，再执行【同步演员映射表】以完成重新链接EmbyID。"
        )
        _update_status(100, final_message)
        logger.info(final_message)

    except InterruptedError:
        logger.info("重构任务被用户中止。")
        raise
    except Exception as e:
        logger.error(f"执行重构任务时发生严重错误: {e}", exc_info=True)
        if update_status_callback:
            update_status_callback(-1, f"任务失败: {e}")
        raise
# --- 执行全量媒体库扫描 ---
def task_process_full_library(processor: MediaProcessor, process_episodes: bool):
    processor.process_full_library(
        update_status_callback=update_status_from_thread,
        process_episodes=process_episodes
    )
# --- 同步演员映射表 ---
def task_sync_person_map(processor):
    """
    【最终兼容版】任务：同步演员映射表。
    接收 processor 和 is_full_sync 以匹配通用任务执行器，
    但内部逻辑已统一，不再使用 is_full_sync。
    """
    task_name = "演员映射表同步"
    # 我们不再需要根据 is_full_sync 来改变任务名了，因为逻辑已经统一
    
    logger.info(f"开始执行 '{task_name}'...")
    
    try:
        # ★★★ 从传入的 processor 对象中获取 config 字典 ★★★
        config = processor.config
        
        sync_handler = UnifiedSyncHandler(
            db_path=DB_PATH,
            emby_url=config.get("emby_server_url"),
            emby_api_key=config.get("emby_api_key"),
            emby_user_id=config.get("emby_user_id"),
            tmdb_api_key=config.get("tmdb_api_key", "")
        )
        
        # 调用同步方法，不再需要传递 is_full_sync
        sync_handler.sync_emby_person_map_to_db(
            update_status_callback=update_status_from_thread
        )
        
        logger.info(f"'{task_name}' 成功完成。")

    except Exception as e:
        logger.error(f"'{task_name}' 执行过程中发生严重错误: {e}", exc_info=True)
        update_status_from_thread(-1, f"错误：同步失败 ({str(e)[:50]}...)")
# ✨✨✨ 补充外部ID函数 ✨✨✨
def task_enrich_aliases(processor: MediaProcessor):
    """
    【后台任务】外部ID补充任务的入口点。
    它会调用 actor_utils 中的核心逻辑，并传递运行时长。
    """
    task_name = "演员外部ID补充"
    logger.info(f"后台任务 '{task_name}' 开始执行...")
    update_status_from_thread(0, "准备开始补充演员别名...")

    try:
        # 从传入的 processor 对象中获取配置字典
        config = processor.config
        
        # 获取必要的配置项
        db_path = DB_PATH
        tmdb_api_key = config.get(constants.CONFIG_OPTION_TMDB_API_KEY)

        if not tmdb_api_key:
            logger.error(f"任务 '{task_name}' 中止：未在配置中找到 TMDb API Key。")
            update_status_from_thread(-1, "错误：缺少TMDb API Key")
            return

        # --- 使用 .get() 方法从字典中安全地获取所有配置 ---
        
        # 1. 获取运行时长
        duration_minutes = config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_DURATION_MINUTES, 0)
        
        # 2. 获取同步冷却天数 (这是关键的修复！)
        sync_interval_days = config.get(
            constants.CONFIG_OPTION_SCHEDULE_ENRICH_SYNC_INTERVAL_DAYS, 
            constants.DEFAULT_ENRICH_ALIASES_SYNC_INTERVAL_DAYS  # 使用默认值以防万一
        )
        
        # 调用核心函数，并传递所有正确获取的参数
        enrich_all_actor_aliases_task(
            db_path=db_path,
            tmdb_api_key=tmdb_api_key,
            run_duration_minutes=duration_minutes,
            sync_interval_days=sync_interval_days, # <--- 现在这里是完全正确的
            stop_event=processor.get_stop_event()
        )
        
        logger.info(f"'{task_name}' 任务执行完毕。")
        update_status_from_thread(100, "外部ID补充任务完成。")

    except Exception as e:
        logger.error(f"'{task_name}' 执行过程中发生严重错误: {e}", exc_info=True)
        update_status_from_thread(-1, f"错误：任务失败 ({str(e)[:50]}...)")
# --- 使用手动编辑的结果处理媒体项 ---
def task_manual_update(processor: MediaProcessor, item_id: str, manual_cast_list: list, item_name: str):
    """任务：使用手动编辑的结果处理媒体项"""
    processor.process_item_with_manual_cast(
        item_id=item_id,
        manual_cast_list=manual_cast_list,
        item_name=item_name
    )
# ★★★ 1. 定义一个webhoo专用追剧、用于编排任务的函数 ★★★
def webhook_processing_task(processor: MediaProcessor, item_id: str, force_reprocess: bool, process_episodes: bool):
    """
    【修复版】这个函数编排了处理新入库项目的完整流程。
    它的第一个参数现在是 MediaProcessor 实例，以匹配任务执行器的调用方式。
    """
    logger.info(f"Webhook 任务启动，处理项目: {item_id}")

    # 步骤 A: 获取完整的项目详情
    # ★★★ 修复：不再使用全局的 media_processor_instance，而是使用传入的 processor ★★★
    item_details = emby_handler.get_emby_item_details(
        item_id, 
        processor.emby_url, 
        processor.emby_api_key, 
        processor.emby_user_id
    )
    if not item_details:
        logger.error(f"Webhook 任务：无法获取项目 {item_id} 的详情，任务中止。")
        return

    # 步骤 B: 调用追剧判断
    # ★★★ 修复：使用传入的 processor ★★★
    processor.check_and_add_to_watchlist(item_details)

    # 步骤 C: 执行通用的元数据处理流程
    # ★★★ 修复：使用传入的 processor ★★★
    processor.process_single_item(
        item_id, 
        force_reprocess_this_item=force_reprocess, 
        process_episodes=process_episodes
    )
    
    logger.debug(f"Webhook 任务完成: {item_id}")
# --- 追剧 ---    
def task_process_watchlist(processor: WatchlistProcessor):
    """
    任务：处理追剧列表。
    """
    # 不传递 item_id，执行全量更新
    processor.process_watching_list()
# ★★★ 只更新追剧列表中的一个特定项目 ★★★
def task_process_single_watchlist_item(processor: WatchlistProcessor, item_id: str):
    """任务：只更新追剧列表中的一个特定项目"""
    # 传递 item_id，执行单项更新
    processor.process_watching_list(item_id=item_id)
# ★★★ 导入映射表 ★★★
def task_import_person_map(processor, file_content: str, **kwargs):
    """
    【V2 - 功能完整版】从一个CSV文件字符串内容中，导入演员映射表。
    """
    task_name = "导入演员映射表"
    logger.info(f"后台任务 '{task_name}' 开始执行...")
    update_status_from_thread(0, "准备开始导入...")

    try:
        # ✨ 1. 从 processor 获取必要的配置和工具 ✨
        config = processor.config
        tmdb_api_key = config.get("tmdb_api_key")
        stop_event = processor.get_stop_event()

        # --- 数据准备 (这部分逻辑不变) ---
        lines = file_content.splitlines()
        total_lines = len(lines) - 1 if len(lines) > 0 else 0
        if total_lines <= 0:
            update_status_from_thread(100, "导入完成：文件为空或只有表头。")
            return
            
        stream_for_reader = StringIO(file_content, newline=None)
        csv_reader = csv.DictReader(stream_for_reader)
        
        stats = {"total": total_lines, "processed": 0, "skipped": 0, "errors": 0}
        
        # ✨✨✨ 核心修改在这里 ✨✨✨
        # 1. 创建 ActorDBManager 的实例
        db_manager = ActorDBManager(DB_PATH) 

        # 2. 使用 with 和中央函数获取连接
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            
            for i, row in enumerate(csv_reader):
                if stop_event and stop_event.is_set():
                    logger.info("导入任务被用户中止。")
                    break

                # 3. 构建 person_data 字典 (不变)
                person_data = {
                    "name": row.get('primary_name'),
                    "tmdb_id": row.get('tmdb_person_id') or None,
                    "imdb_id": row.get('imdb_id') or None,
                    "douban_id": row.get('douban_celebrity_id') or None,
                }

                if not person_data["name"] and not any([person_data["emby_id"], person_data["tmdb_id"], person_data["imdb_id"], person_data["douban_id"]]):
                    stats["skipped"] += 1
                    continue

                try:
                    # 4. 通过正确的 db_manager 实例调用方法
                    db_manager.upsert_person(
                        cursor, 
                        person_data
                        # 注意：upsert_person 的定义里没有 tmdb_api_key 参数，所以这里移除了
                    )
                    stats["processed"] += 1
                except Exception as e_row:
                    logger.error(f"处理导入文件第 {i+2} 行时发生错误: {e_row}")
                    stats["errors"] += 1
                
                if i > 0 and i % 100 == 0 and total_lines > 0:
                    progress = int(((i + 1) / total_lines) * 100)
                    update_status_from_thread(progress, f"正在导入... ({i+1}/{total_lines})")
            
            # 5. 循环结束后提交事务
            conn.commit()
        # ✨✨✨ 修改结束 ✨✨✨

        message = f"导入完成。总行数: {stats['total']}, 成功处理: {stats['processed']}, 跳过: {stats['skipped']}, 错误: {stats['errors']}"
        logger.info(f"导入任务完成: {message}")
        update_status_from_thread(100, "导入完成！")

    except Exception as e:
        logger.error(f"后台导入任务失败: {e}", exc_info=True)
        update_status_from_thread(-1, f"导入失败: {e}")
# ★★★ 重新处理单个项目 ★★★
def task_reprocess_single_item(processor: MediaProcessor, item_id: str):
    """
    【已升级 - 强制在线获取版】
    后台任务：通过强制在线获取TMDb最新数据的方式，重新处理单个项目。
    """
    item_name_for_log = f"ItemID: {item_id}"
    logger.info(f"--- 开始执行“重新处理单个项目”任务 ({item_name_for_log}) [强制在线获取模式] ---")
    
    try:
        # 1. 获取项目名用于日志（可选，但体验更好）
        item_details = emby_handler.get_emby_item_details(item_id, processor.emby_url, processor.emby_api_key, processor.emby_user_id)
        if item_details:
            item_name_for_log = item_details.get("Name", item_name_for_log)
        
        update_status_from_thread(10, f"正在处理: {item_name_for_log}")

        # 2. 【核心修改】直接调用 processor 的核心方法，并传递 force_fetch_from_tmdb=True
        #    不再需要删除缓存、触发Emby刷新、等待等脆弱的步骤！
        logger.info(f"为 '{item_name_for_log}' 调用核心处理器，并设置强制在线获取标志...")
        
        processor.process_single_item(
            item_id, 
            force_reprocess_this_item=True, # 确保它会被处理，即使在processed_log里
            force_fetch_from_tmdb=True      # ★★★ 这就是我们新的“魔法棒” ★★★
        )
        
        # 3. 任务结束（process_single_item内部会处理最终状态）
        # 这里的日志可以简化，因为核心日志在processor里
        logger.info(f"--- “重新处理单个项目”任务完成 ({item_name_for_log}) ---")

    except Exception as e:
        logger.error(f"重新处理 '{item_name_for_log}' 时发生严重错误: {e}", exc_info=True)
        update_status_from_thread(-1, f"重新处理失败: {e}")
# --- 翻译演员任务 ---
def task_actor_translation_cleanup(processor):
    """
    【最终修正版】执行演员名翻译的查漏补缺工作，并使用正确的全局状态更新函数。
    """
    try:
        # ✨✨✨ 修正：直接调用全局函数，而不是processor的方法 ✨✨✨
        update_status_from_thread(5, "正在准备需要翻译的演员数据...")
        
        # 1. 调用数据准备函数
        translation_map, name_to_persons_map = emby_handler.prepare_actor_translation_data(
            emby_url=processor.emby_url,
            emby_api_key=processor.emby_api_key,
            user_id=processor.emby_user_id,
            ai_translator=processor.ai_translator,
            stop_event=processor.get_stop_event()
        )

        if not translation_map:
            update_status_from_thread(100, "任务完成，没有需要翻译的演员。")
            return

        total_to_update = len(translation_map)
        update_status_from_thread(50, f"数据准备完毕，开始更新 {total_to_update} 个演员名...")
        
        update_count = 0
        processed_count = 0

        # 2. 主循环
        for original_name, translated_name in translation_map.items():
            processed_count += 1
            if processor.is_stop_requested():
                logger.info("演员翻译任务被用户中断。")
                break
            
            if not translated_name or original_name == translated_name:
                continue

            persons_to_update = name_to_persons_map.get(original_name, [])
            for person in persons_to_update:
                # 3. 更新单个条目
                success = emby_handler.update_person_details(
                    person_id=person.get("Id"),
                    new_data={"Name": translated_name},
                    emby_server_url=processor.emby_url,
                    emby_api_key=processor.emby_api_key,
                    user_id=processor.emby_user_id
                )
                if success:
                    update_count += 1
                    time.sleep(0.2)

            # 4. 更新进度
            progress = int(50 + (processed_count / total_to_update) * 50)
            update_status_from_thread(progress, f"({processed_count}/{total_to_update}) 正在更新: {original_name} -> {translated_name}")

        # 任务结束时，也直接调用全局函数
        final_message = f"任务完成！共更新了 {update_count} 个演员名。"
        if processor.is_stop_requested():
            final_message = "任务已中断。"
        update_status_from_thread(100, final_message)

    except Exception as e:
        logger.error(f"执行演员翻译任务时出错: {e}", exc_info=True)
        # 在异常处理中也直接调用全局函数
        update_status_from_thread(-1, f"任务失败: {e}")
# ★★★ 重新处理所有待复核项 ★★★
def task_reprocess_all_review_items(processor: MediaProcessor):
    """
    【已升级】后台任务：遍历所有待复核项并逐一以“强制在线获取”模式重新处理。
    """
    logger.info("--- 开始执行“重新处理所有待复核项”任务 [强制在线获取模式] ---")
    try:
        with processor._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT item_id FROM failed_log")
            all_item_ids = [row['item_id'] for row in cursor.fetchall()]
        
        total = len(all_item_ids)
        if total == 0:
            logger.info("待复核列表中没有项目，任务结束。")
            update_status_from_thread(100, "待复核列表为空。")
            return

        logger.info(f"共找到 {total} 个待复核项需要以“强制在线获取”模式重新处理。")

        for i, item_id in enumerate(all_item_ids):
            if processor.is_stop_requested():
                logger.info("任务被中止。")
                break
            
            update_status_from_thread(int((i/total)*100), f"正在重新处理 {i+1}/{total}: {item_id}")
            
            # 【核心修改】直接调用升级后的单个重新处理任务函数
            task_reprocess_single_item(processor, item_id)
            
            # 每个项目之间稍作停顿
            time.sleep(2) 

    except Exception as e:
        logger.error(f"重新处理所有待复核项时发生严重错误: {e}", exc_info=True)
        update_status_from_thread(-1, "任务失败")
# ★★★ 全量图片同步的任务函数 ★★★
def task_full_image_sync(processor: MediaProcessor):
    """
    后台任务：调用 processor 的方法来同步所有图片。
    """
    # 直接把回调函数传进去
    processor.sync_all_images(update_status_callback=update_status_from_thread)
# --- 立即执行任务注册表 ---
TASK_REGISTRY = {
    'full-scan': (task_process_full_library, "立即执行全量扫描"),
    'sync-person-map': (task_sync_person_map, "立即执行同步演员映射表"),
    'process-watchlist': (task_process_watchlist, "立即执行智能追剧更新"),
    'enrich-aliases': (task_enrich_aliases, "立即执行外部ID补充"),
    'actor-cleanup': (task_actor_translation_cleanup, "立即执行演员名查漏补缺")
}
# --- 路由区 ---
# --- webhook通知任务 ---
@app.route('/webhook/emby', methods=['POST'])
@processor_ready_required
def emby_webhook():
    data = request.json
    event_type = data.get("Event") if data else "未知事件"
    logger.info(f"收到Emby Webhook: {event_type}")
    
    trigger_events = ["item.add", "library.new"] 

    if event_type not in trigger_events:
        logger.info(f"Webhook事件 '{event_type}' 不在触发列表 {trigger_events} 中，将被忽略。")
        return jsonify({"status": "event_ignored_not_in_trigger_list"}), 200

    item_from_webhook = data.get("Item", {}) if data else {}
    original_item_id = item_from_webhook.get("Id")
    original_item_name = item_from_webhook.get("Name", "未知项目")
    original_item_type = item_from_webhook.get("Type")
    
    # 我们关心的类型是电影、剧集和分集
    trigger_types = ["Movie", "Series", "Episode"]

    if not (original_item_id and original_item_type in trigger_types):
        logger.debug(f"Webhook事件 '{event_type}' (项目: {original_item_name}, 类型: {original_item_type}) 被忽略（缺少ID或类型不匹配）。")
        return jsonify({"status": "event_ignored_no_id_or_wrong_type"}), 200


    # ★★★ 核心修复逻辑 START ★★★
    
    id_to_process = original_item_id
    type_to_process = original_item_type

    # 1. 如果是分集，向上查找剧集ID
    if original_item_type == "Episode":
        logger.info(f"Webhook 收到分集 '{original_item_name}' (ID: {original_item_id})，正在向上查找其所属剧集...")
        series_id = emby_handler.get_series_id_from_child_id(
            original_item_id,
            media_processor_instance.emby_url,
            media_processor_instance.emby_api_key,
            media_processor_instance.emby_user_id
        )
        if series_id:
            id_to_process = series_id
            type_to_process = "Series" # 明确类型为剧集
            logger.info(f"成功找到所属剧集 ID: {id_to_process}。将处理此剧集。")
        else:
            logger.error(f"无法为分集 '{original_item_name}' 找到所属剧集ID，将跳过处理。")
            return jsonify({"status": "event_ignored_series_not_found"}), 200

    # 2. 无论最初是什么类型，都用最终确定的ID重新获取一次完整的项目详情
    logger.info(f"准备重新获取项目 {id_to_process} 的最新、最完整的元数据...")
    full_item_details = emby_handler.get_emby_item_details(
        item_id=id_to_process,
        emby_server_url=media_processor_instance.emby_url,
        emby_api_key=media_processor_instance.emby_api_key,
        user_id=media_processor_instance.emby_user_id
    )

    if not full_item_details:
        logger.error(f"无法获取项目 {id_to_process} 的完整详情，处理中止。")
        return jsonify({"status": "event_ignored_details_fetch_failed"}), 200

    # 3. 从新获取的详情中提取最终的名称和TMDb ID
    final_item_name = full_item_details.get("Name", f"未知项目(ID:{id_to_process})")
    provider_ids = full_item_details.get("ProviderIds", {})
    tmdb_id = provider_ids.get("Tmdb")

    # 4. 在提交到队列前，做最后一次检查
    if not tmdb_id:
        logger.warning(f"项目 '{final_item_name}' (ID: {id_to_process}) 缺少 TMDb ID，无法进行处理。这可能是因为 Emby 尚未完成对该项目的元数据刮削。将跳过本次 Webhook 请求。")
        return jsonify({"status": "event_ignored_no_tmdb_id"}), 200
        
    # ★★★ 核心修复逻辑 END ★★★

    logger.info(f"Webhook事件触发，最终处理项目 '{final_item_name}' (ID: {id_to_process}, TMDbID: {tmdb_id}) 已提交到任务队列。")
    
    # ★★★ 核心修改点在这里 ★★★
    # 使用最终确定的信息，提交我们新的“编排任务”到队列
    submit_task_to_queue(
        webhook_processing_task,  # <--- 目标函数是这个，而不是 process_single_item
        f"Webhook处理: {final_item_name}",
        # --- 传递给 webhook_processing_task 的参数 ---
        id_to_process,
        force_reprocess=True, 
        process_episodes=True
    )
    
    return jsonify({"status": "task_queued", "item_id": id_to_process}), 202
@app.route('/trigger_sync_person_map', methods=['POST'])
def trigger_sync_person_map(): # WebUI 用的

    task_name = "同步Emby演员映射表 (WebUI)"
    logger.info(f"收到手动触发 '{task_name}' 的请求。")

    submit_task_to_queue(
        task_sync_person_map,
        task_name
    )

    flash(f"'{task_name}' 任务已在后台启动。", "info")
    return redirect(url_for('settings_page'))

@app.route('/trigger_stop_task', methods=['POST'])
def trigger_stop_task():
    global background_task_status
    if media_processor_instance and hasattr(media_processor_instance, 'signal_stop'):
        media_processor_instance.signal_stop()
        flash("已发送停止后台任务的请求。任务将在当前步骤完成后停止。", "info")
        background_task_status["message"] = "正在尝试停止任务..."
    else:
        flash("错误：服务未就绪或不支持停止操作。", "error")
    return redirect(url_for('settings_page'))

@app.route('/status')
def get_status():
    return jsonify(background_task_status)

#--- 日志 ---
@app.route('/api/status', methods=['GET'])
def api_get_task_status():
    global background_task_status, frontend_log_queue
    
    status_data = background_task_status.copy() # 复制一份，避免线程问题
    
    # 将日志队列的内容添加到返回数据中
    status_data['logs'] = list(frontend_log_queue)
    
    return jsonify(status_data)

@app.route('/api/emby_libraries')
def api_get_emby_libraries():
    # 确保 media_processor_instance 已初始化并且 Emby 配置有效
    if not media_processor_instance or \
       not media_processor_instance.emby_url or \
       not media_processor_instance.emby_api_key:
        logger.warning("/api/emby_libraries: Emby配置不完整或服务未就绪。")
        return jsonify({"error": "Emby配置不完整或服务未就绪"}), 500

    libraries = emby_handler.get_emby_libraries(
        media_processor_instance.emby_url,
        media_processor_instance.emby_api_key,
        media_processor_instance.emby_user_id # 传递 user_id
    )

    if libraries is not None: # get_emby_libraries 成功返回列表 (可能为空列表)
        return jsonify(libraries)
    else: # get_emby_libraries 返回了 None，表示获取失败
        logger.error("/api/emby_libraries: 无法获取Emby媒体库列表 (emby_handler返回None)。")
        return jsonify({"error": "无法获取Emby媒体库列表，请检查Emby连接和日志"}), 500

# --- 应用退出处理 ---
def application_exit_handler():
    global media_processor_instance, scheduler, task_worker_thread
    logger.info("应用程序正在退出 (atexit)，执行清理操作...")

    # 1. 立刻通知当前正在运行的任务停止
    if media_processor_instance:
        logger.info("正在发送停止信号给当前任务...")
        media_processor_instance.signal_stop()

    # 2. 清空任务队列，丢弃所有排队中的任务
    if not task_queue.empty():
        logger.info(f"队列中还有 {task_queue.qsize()} 个任务，正在清空...")
        while not task_queue.empty():
            try:
                task_queue.get_nowait()
            except Queue.Empty:
                break
        logger.info("任务队列已清空。")

    # 3. 停止工人线程
    if task_worker_thread and task_worker_thread.is_alive():
        logger.info("正在发送停止信号给任务工人线程...")
        task_queue.put(None) # 发送“毒丸”
        task_worker_thread.join(timeout=5) # 等待线程退出
        if task_worker_thread.is_alive():
            logger.warning("任务工人线程在5秒内未能正常退出。")
        else:
            logger.info("任务工人线程已成功停止。")

    # 4. 关闭其他资源
    if media_processor_instance:
        media_processor_instance.close()
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    
    logger.info("atexit 清理操作执行完毕。")

atexit.register(application_exit_handler)
# --- 应用退出处理结束 ---

# --- API 端点 搜索媒体库 ---
@app.route('/api/search_emby_library', methods=['GET'])
@processor_ready_required
def api_search_emby_library():
    query = request.args.get('query', '')
    if not query.strip():
        return jsonify({"error": "搜索词不能为空"}), 400

    try:
        # ✨✨✨ 调用改造后的函数，并传入 search_term ✨✨✨
        search_results = emby_handler.get_emby_library_items(
            base_url=media_processor_instance.emby_url,
            api_key=media_processor_instance.emby_api_key,
            user_id=media_processor_instance.emby_user_id,
            media_type_filter="Movie,Series", # 搜索时指定类型
            search_term=query # <--- 传递搜索词
        )
        
        if search_results is None:
            return jsonify({"error": "搜索时发生服务器错误"}), 500

        # 将搜索结果转换为前端表格期望的格式 (这部分逻辑不变)
        formatted_results = []
        for item in search_results:
            formatted_results.append({
                "item_id": item.get("Id"),
                "item_name": item.get("Name"),
                "item_type": item.get("Type"),
                "failed_at": None,
                "error_message": f"来自 Emby 库的搜索结果 (年份: {item.get('ProductionYear', 'N/A')})",
                "score": None,
                # ★★★ 核心修复：把 ProviderIds 也传递给前端 ★★★
                "provider_ids": item.get("ProviderIds") 
            })
        
        return jsonify({
            "items": formatted_results,
            "total_items": len(formatted_results)
        })

    except Exception as e:
        logger.error(f"API /api/search_emby_library Error: {e}", exc_info=True)
        return jsonify({"error": "搜索时发生未知服务器错误"}), 500
# --- 认证 API 端点 ---
@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """检查当前认证状态"""
    config, _ = load_config()
    auth_enabled = config.get(constants.CONFIG_OPTION_AUTH_ENABLED, False)
    
    response = {
        "auth_enabled": auth_enabled,
        "logged_in": 'user_id' in session,
        "username": session.get('username')
    }
    return jsonify(response)

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    username_from_req = data.get('username')
    password_from_req = data.get('password')

    if not username_from_req or not password_from_req:
        return jsonify({"error": "缺少用户名或密码"}), 400

    user = None # 先在 with 外部定义 user 变量
    
    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username_from_req,))
            user = cursor.fetchone()
        # with 代码块结束时，conn 会被自动、安全地关闭
    except Exception as e:
        logger.error(f"登录时数据库查询失败: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨

    if user and check_password_hash(user['password_hash'], password_from_req):
        session['user_id'] = user['id']
        session['username'] = user['username']
        logger.info(f"用户 '{user['username']}' 登录成功。")
        return jsonify({"message": "登录成功", "username": user['username']})
    
    logger.warning(f"用户 '{username_from_req}' 登录失败：用户名或密码错误。")
    return jsonify({"error": "用户名或密码错误"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    username = session.get('username', '未知用户')
    session.clear()
    logger.info(f"用户 '{username}' 已注销。")
    return jsonify({"message": "注销成功"})

# --- 认证 API 端点结束 ---
@app.route('/api/auth/change_password', methods=['POST'])
@login_required
def change_password():
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({"error": "缺少当前密码或新密码"}), 400
    
    if len(new_password) < 6:
        return jsonify({"error": "新密码长度不能少于6位"}), 400

    user_id = session.get('user_id')
    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 1. 查询用户
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()

            # 2. 验证当前密码
            if not user or not check_password_hash(user['password_hash'], current_password):
                # 注意：这里不需要手动 close() 了，with 语句会在函数返回时处理
                logger.warning(f"用户 '{session.get('username')}' 修改密码失败：当前密码不正确。")
                return jsonify({"error": "当前密码不正确"}), 403

            # 3. 更新密码
            new_password_hash = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
            
            # 4. 提交事务
            conn.commit()
            
            # with 代码块正常结束，连接会自动关闭
            
    except Exception as e:
        # 如果 try 块中的任何地方（包括数据库操作）发生错误
        # with 语句会确保连接被关闭
        logger.error(f"修改密码时发生数据库错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨

    logger.info(f"用户 '{user['username']}' 成功修改密码。")
    return jsonify({"message": "密码修改成功"})
# --- API 端点：获取当前配置 ---
@app.route('/api/config', methods=['GET'])
def api_get_config():
    try:
        # ★★★ 确保这里正确解包了元组 ★★★
        current_config, _ = load_config() 
        
        if current_config:
            logger.debug(f"API /api/config (GET): 成功加载并返回配置。")
            return jsonify(current_config)
        else:
            logger.error(f"API /api/config (GET): load_config() 返回为空或None。")
            return jsonify({"error": "无法加载配置数据"}), 500
    except Exception as e:
        logger.error(f"API /api/config (GET) 获取配置时发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取配置信息时发生服务器内部错误"}), 500
        logger.error(f"API /api/config (GET) 获取配置时发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取配置信息时发生服务器内部错误"}), 500

# --- API 端点：保存配置 ---
@app.route('/api/config', methods=['POST'])
def api_save_config():
    try:
        new_config_data = request.json
        if not new_config_data:
            return jsonify({"error": "请求体中未包含配置数据"}), 400
        
        # ★★★ 核心修改：在这里进行严格校验并“打回去” ★★★
        user_id_to_save = new_config_data.get("emby_user_id", "").strip()

        # 规则1：检查是否为空
        if not user_id_to_save:
            error_message = "Emby User ID 不能为空！这是获取媒体库列表的必需项。"
            logger.warning(f"API /api/config (POST): 拒绝保存，原因: {error_message}")
            return jsonify({"error": error_message}), 400

        # 规则2：检查格式是否正确
        if not re.match(r'^[a-f0-9]{32}$', user_id_to_save, re.I):
            error_message = "Emby User ID 格式不正确！它应该是一串32位的字母和数字。"
            logger.warning(f"API /api/config (POST): 拒绝保存，原因: {error_message} (输入值: '{user_id_to_save}')")
            return jsonify({"error": error_message}), 400
        # ★★★ 校验结束 ★★★

        logger.info(f"API /api/config (POST): 收到新的配置数据，准备保存...")
        
        # 校验通过后，才调用保存函数
        save_config(new_config_data) 
        
        logger.debug("API /api/config (POST): 配置已成功传递给 save_config 函数。")
        return jsonify({"message": "配置已成功保存并已触发重新加载。"})
        
    except Exception as e:
        logger.error(f"API /api/config (POST) 保存配置时发生错误: {e}", exc_info=True)
        return jsonify({"error": f"保存配置时发生服务器内部错误: {str(e)}"}), 500

# --- API 端点：获取待复核列表 ---
@app.route('/api/review_items', methods=['GET'])
def api_get_review_items():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    query_filter = request.args.get('query', '', type=str).strip()

    if page < 1: page = 1
    if per_page < 1: per_page = 10
    if per_page > 100: per_page = 100

    offset = (page - 1) * per_page
    
    items_to_review = []
    total_matching_items = 0
    
    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row # 确保可以按列名访问，这很重要
            cursor = conn.cursor()
            
            where_clause = ""
            sql_params = []

            if query_filter:
                where_clause = "WHERE item_name LIKE ?"
                sql_params.append(f"%{query_filter}%")

            # 1. 查询总数
            count_sql = f"SELECT COUNT(*) FROM failed_log {where_clause}"
            cursor.execute(count_sql, tuple(sql_params))
            count_row = cursor.fetchone()
            if count_row:
                total_matching_items = count_row[0]

            logger.debug(f"DATABASE CHECK: Found a total of {total_matching_items} items.")

            # 2. 查询当前页的数据
            items_sql = f"""
                SELECT item_id, item_name, failed_at, reason, item_type, score 
                FROM failed_log 
                {where_clause}
                ORDER BY failed_at DESC 
                LIMIT ? OFFSET ?
            """
            params_for_page_query = sql_params + [per_page, offset]
            cursor.execute(items_sql, tuple(params_for_page_query))
            fetched_rows = cursor.fetchall()
            
            for row in fetched_rows:
                items_to_review.append(dict(row))
        
        # with 代码块结束，数据库连接已安全关闭

    except Exception as e:
        logger.error(f"API /api/review_items 获取数据失败: {e}", exc_info=True)
        return jsonify({"error": "获取待复核列表时发生服务器内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨
            
    # 这部分代码不需要数据库连接，所以放在 try...except 块外面是完全正确的
    total_pages = (total_matching_items + per_page - 1) // per_page if total_matching_items > 0 else 0
    
    logger.debug(f"API /api/review_items: 返回 {len(items_to_review)} 条待复核项目 (总计: {total_matching_items}, 第 {page}/{total_pages} 页)")
    return jsonify({
        "items": items_to_review,
        "total_items": total_matching_items,
        "total_pages": total_pages,
        "current_page": page,
        "per_page": per_page,
        "query": query_filter
    })
@app.route('/api/actions/mark_item_processed/<item_id>', methods=['POST'])
def api_mark_item_processed(item_id):
    if task_lock.locked():
        return jsonify({"error": "后台有长时间任务正在运行，请稍后再试。"}), 409
    
    deleted_count = 0 # 在 try 块外部定义，以便 finally 之后能访问

    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row # 确保可以按列名访问
            cursor = conn.cursor()
            
            # 1. 从 failed_log 获取信息
            cursor.execute("SELECT item_name, item_type, score FROM failed_log WHERE item_id = ?", (item_id,))
            failed_item_info = cursor.fetchone()
            
            # 2. 从 failed_log 删除
            cursor.execute("DELETE FROM failed_log WHERE item_id = ?", (item_id,))
            deleted_count = cursor.rowcount
            
            # 3. 如果删除成功，则添加到 processed_log
            if deleted_count > 0 and failed_item_info:
                score_to_save = failed_item_info["score"] if failed_item_info["score"] is not None else 10.0
                item_name = failed_item_info["item_name"]
                
                cursor.execute(
                    "REPLACE INTO processed_log (item_id, item_name, processed_at, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                    (item_id, item_name, score_to_save)
                )
                logger.info(f"项目 {item_id} ('{item_name}') 已标记为已处理，并移至已处理日志 (评分: {score_to_save})。")

            # 4. 所有操作成功，提交事务
            conn.commit()
            # with 代码块结束，连接自动关闭

    except Exception as e:
        # 如果 with 块内任何地方出错，未提交的更改会自动回滚
        logger.error(f"标记项目 {item_id} 为已处理时失败: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨
    
    # 根据事务执行的结果返回响应
    if deleted_count > 0:
        return jsonify({"message": f"项目 {item_id} 已成功标记为已处理。"}), 200
    else:
        return jsonify({"error": f"未在待复核列表中找到项目 {item_id}。"}), 404
# --- 前端全量扫描接口 ---   
@app.route('/api/trigger_full_scan', methods=['POST'])
@processor_ready_required # <-- 检查处理器是否就绪
@task_lock_required      # <-- 检查任务锁
def api_handle_trigger_full_scan():
    logger.debug("API Endpoint: Received request to trigger full scan.")
    # 从 FormData 获取数据
    # 注意：前端发送的是 FormData，所以我们用 request.form
    force_reprocess = request.form.get('force_reprocess_all') == 'on'
    

    # ★★★ 您的完美逻辑在这里实现 ★★★
    if force_reprocess:
        logger.info("API: 检测到“强制重处理”选项，将在任务开始前清空已处理日志。")
        try:
            media_processor_instance.clear_processed_log()
            logger.info("API: 已处理日志已成功清除。")
        except Exception as e:
            logger.error(f"API: 清空已处理日志时发生错误: {e}")
            return jsonify({"error": f"清空日志失败: {e}"}), 500

    # 准备任务参数
    action_message = "全量媒体库扫描"
    if force_reprocess:
        action_message += " (已清空日志)"

    # 从全局配置获取处理深度
    process_episodes = APP_CONFIG.get('process_episodes', True)
    
    # 提交纯粹的扫描任务
    submit_task_to_queue(
        task_process_full_library, # 调用简化后的任务函数
        action_message,
        process_episodes # 不再需要传递 force_reprocess
    )
    
    return jsonify({"message": f"{action_message} 任务已提交启动。"}), 202
# --- 同步演员映射表 ---
@app.route('/api/trigger_sync_person_map', methods=['POST'])
@login_required
def api_handle_trigger_sync_map():
    logger.debug("API: 收到触发演员映射表同步的请求。")
    try:
        # ★★★ 核心修复：不再需要 full_sync，因为同步逻辑已经统一 ★★★
        task_name_for_api = "同步演员映射表"
        
        submit_task_to_queue(
            task_sync_person_map,
            task_name_for_api
        )

        return jsonify({"message": f"'{task_name_for_api}' 任务已提交启动。"}), 202
    except Exception as e:
        logger.error(f"API /api/trigger_sync_person_map error: {e}", exc_info=True)
        return jsonify({"error": "启动同步映射表时发生服务器内部错误"}), 500

@app.route('/api/trigger_stop_task', methods=['POST'])
def api_handle_trigger_stop_task():
    logger.debug("API Endpoint: Received request to stop current task.")
    if media_processor_instance:
        media_processor_instance.signal_stop()
        logger.info("已发送停止信号给当前正在运行的任务。")
        return jsonify({"message": "已发送停止任务请求。"}), 200
    else:
        logger.warning("API: MediaProcessor 未初始化，无法发送停止信号。")
        return jsonify({"error": "核心处理器未就绪"}), 503
    
# ✨✨✨ 保存手动编辑结果的 API ✨✨✨
@app.route('/api/update_media_cast_sa/<item_id>', methods=['POST'])
@login_required
@processor_ready_required
def api_update_edited_cast_sa(item_id):
    data = request.json
    if not data or "cast" not in data or not isinstance(data["cast"], list):
        return jsonify({"error": "请求体中缺少有效的 'cast' 列表"}), 400
    
    edited_cast = data["cast"]
    item_name = data.get("item_name", f"未知项目(ID:{item_id})")

    submit_task_to_queue(
        task_manual_update, # 传递包装函数
        f"手动更新: {item_name}",
        # --- 后面是传递给 task_manual_update 的参数 ---
        item_id,
        edited_cast,
        item_name
    )
    
    return jsonify({"message": "手动更新任务已在后台启动。"}), 202
@app.route('/api/update_media_cast_api/<item_id>', methods=['POST'])
@login_required
@processor_ready_required
def api_update_edited_cast_api(item_id):
    try:
        data = request.json
        if not data or "cast" not in data or not isinstance(data["cast"], list):
            return jsonify({"error": "请求体中缺少有效的 'cast' 列表"}), 400

        edited_cast_from_frontend = data["cast"]
        logger.info(f"API: 收到为 ItemID {item_id} 更新演员的请求，共 {len(edited_cast_from_frontend)} 位演员。")

        # ✨✨✨ 1. 使用 with 语句，在所有操作开始前获取数据库连接 ✨✨✨
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()

            # ✨✨✨ 2. 手动开启一个事务 ✨✨✨
            # 虽然这里的操作不多，但使用事务可以保证所有日志记录的原子性
            cursor.execute("BEGIN TRANSACTION;")
            logger.debug(f"API 手动更新 (ItemID: {item_id}) 的数据库事务已开启。")

            try:
                # 准备传递给核心处理器的数据
                cast_for_processor = []
                for actor_frontend in edited_cast_from_frontend:
                    entry = {
                        "emby_person_id": str(actor_frontend.get("embyPersonId", "")).strip(),
                        "name": str(actor_frontend.get("name", "")).strip(),
                        "character": str(actor_frontend.get("role", "")).strip(),
                        "provider_ids": {}
                    }
                    cast_for_processor.append(entry)

                # 调用核心处理函数
                # 注意：我们需要确保 process_item_with_manual_cast 不会自己 commit/close 连接
                # 理想情况下，它也应该接收 cursor 参数。但如果它内部自己管理连接，我们暂时也能接受。
                process_success = media_processor_instance.process_item_with_manual_cast(
                    item_id=item_id,
                    manual_cast_list=cast_for_processor
                )

                if not process_success:
                    logger.error(f"API: 核心处理器未能成功处理 ItemID {item_id} 的手动更新。")
                    # 核心处理失败，回滚日志事务并返回错误
                    conn.rollback()
                    return jsonify({"error": "核心处理器执行手动更新失败，请检查后端日志。"}), 500

                # ✨✨✨ 3. 在事务中，使用新的 LogDBManager 更新日志 ✨✨✨
                logger.info(f"API: ItemID {item_id} 的手动更新流程已成功执行，正在更新处理日志...")
                item_name_for_log = data.get("item_name", f"未知项目(ID:{item_id})")
                
                # 使用 self.log_db_manager 调用，并传入 cursor
                media_processor_instance.log_db_manager.remove_from_failed_log(cursor, item_id)
                media_processor_instance.log_db_manager.save_to_processed_log(cursor, item_id, item_name_for_log, score=10.0)

                # ✨✨✨ 4. 所有操作成功后，提交事务 ✨✨✨
                conn.commit()
                
                return jsonify({"message": "演员信息已成功更新，并执行了完整的处理流程。"}), 200

            except Exception as inner_e:
                # 如果在事务中发生任何错误，回滚
                logger.error(f"API /api/update_media_cast 事务处理中发生错误 for {item_id}: {inner_e}", exc_info=True)
                conn.rollback()
                # 重新抛出，让外层捕获并返回 500
                raise

    except Exception as outer_e:
        logger.error(f"API /api/update_media_cast 顶层错误 for {item_id}: {outer_e}", exc_info=True)
        return jsonify({"error": "保存演员信息时发生服务器内部错误"}), 500
# ★★★ 导出演员映射表 ★★★
@app.route('/api/actors/export', methods=['GET'])
@login_required
def api_export_person_map():
    """
    【统一版】导出演员身份映射表 (person_identity_map)。
    """
    table_name = 'person_identity_map'
    headers = [
    'primary_name', 
    'tmdb_person_id', 'imdb_id', 'douban_celebrity_id'
    ]
    logger.info(f"API: 收到导出演员映射表 '{table_name}' 的请求。")

    def generate_csv():
        string_io = StringIO()
        try:
            # ✨✨✨ 核心修改在这里 ✨✨✨
            with get_central_db_connection(DB_PATH) as conn:
            # ✨✨✨ 修改结束 ✨✨✨
                conn.row_factory = sqlite3.Row # 确保可以按列名访问
                cursor = conn.cursor()
                writer = csv.DictWriter(string_io, fieldnames=headers, extrasaction='ignore')
                
                writer.writeheader()
                yield string_io.getvalue()
                string_io.seek(0); string_io.truncate(0)

                # 使用 f-string 格式化列名，更安全
                query_columns = ', '.join(f'"{h}"' for h in headers)
                cursor.execute(f"SELECT {query_columns} FROM {table_name}")
                
                for row in cursor:
                    writer.writerow(dict(row))
                    yield string_io.getvalue()
                    string_io.seek(0); string_io.truncate(0)
        except Exception as e:
            logger.error(f"导出映射表时发生错误: {e}", exc_info=True)
            yield f"Error: {e}"

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"person_identity_map_backup_{timestamp}.csv"
    
    response = Response(stream_with_context(generate_csv()), mimetype='text/csv; charset=utf-8')
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    return response
# ★★★ 导入演员映射表 ★★★
@app.route('/api/actors/import', methods=['POST'])
@login_required
@task_lock_required
def api_import_person_map():
    """
    【队列版】接收上传的CSV文件，读取内容，并提交一个后台任务来处理它。
    """
    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400
    
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.csv'):
        return jsonify({"error": "未选择文件或文件类型不正确"}), 400

    try:
        # 1. 直接将文件内容读入内存字符串
        file_content = file.stream.read().decode("utf-8-sig")
        logger.info(f"已接收上传文件 '{file.filename}'，内容长度: {len(file_content)}")

        # 2. 提交一个后台任务，把文件内容和需要的配置传过去
        submit_task_to_queue(
            task_import_person_map,
            "导入演员映射表",
            # ★★★ 把任务需要的所有东西，都作为关键字参数传递 ★★★
            file_content=file_content,
            tmdb_api_key=app.config.get("tmdb_api_key", "")
        )
        
        return jsonify({"message": "文件上传成功，已提交到后台队列进行导入。"}), 202

    except Exception as e:
        logger.error(f"处理导入文件请求时发生错误: {e}", exc_info=True)
        return jsonify({"error": f"处理上传文件时发生服务器错误"}), 500
# ✨✨✨ 编辑页面的API接口 ✨✨✨
@app.route('/api/media_for_editing_sa/<item_id>', methods=['GET'])
@login_required
@processor_ready_required
def api_get_media_for_editing_sa(item_id):
    # 直接调用 core_processor 的新方法
    data_for_editing = media_processor_instance.get_cast_for_editing(item_id)
    
    if data_for_editing:
        return jsonify(data_for_editing)
    else:
        return jsonify({"error": f"无法获取项目 {item_id} 的编辑数据，请检查日志。"}), 404
# ✨✨✨   生成外部搜索链接 ✨✨✨
@app.route('/api/parse_cast_from_url', methods=['POST'])
def api_parse_cast_from_url():
    # 检查 web_parser 是否可用
    try:
        from web_parser import parse_cast_from_url, ParserError
    except ImportError:
        return jsonify({"error": "网页解析功能在服务器端不可用。"}), 501

    data = request.json
    url_to_parse = data.get('url')
    if not url_to_parse:
        return jsonify({"error": "请求中未提供 'url' 参数"}), 400

    try:
        current_config, _ = load_config()
        headers = {'User-Agent': current_config.get('user_agent', '')}
        parsed_cast = parse_cast_from_url(url_to_parse, custom_headers=headers)
        
        frontend_cast = [{"name": item['actor'], "role": item['character']} for item in parsed_cast]
        return jsonify(frontend_cast)

    except ParserError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"解析 URL '{url_to_parse}' 时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": "解析时发生未知的服务器错误"}), 500
# ✨✨✨ 一键翻译 ✨✨✨
@app.route('/api/actions/translate_cast_sa', methods=['POST']) # 注意路径不同
@login_required
@processor_ready_required
def api_translate_cast_sa():
    data = request.json
    current_cast = data.get('cast')
    if not isinstance(current_cast, list):
        return jsonify({"error": "请求体必须包含 'cast' 列表。"}), 400

    # 【★★★ 从请求中获取所有需要的上下文信息 ★★★】
    title = data.get('title')
    year = data.get('year')

    try:
        # 【★★★ 调用新的、需要完整上下文的函数 ★★★】
        translated_list = media_processor_instance.translate_cast_list_for_editing(
            cast_list=current_cast,
            title=title,
            year=year,
        )
        return jsonify(translated_list)
    except Exception as e:
        logger.error(f"一键翻译演员列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在翻译时发生内部错误。"}), 500
# ✨✨✨ 预览处理后的演员表 ✨✨✨
@app.route('/api/preview_processed_cast/<item_id>', methods=['POST'])
@processor_ready_required
def api_preview_processed_cast(item_id):
    """
    一个轻量级的API，用于预览单个媒体项经过核心处理器处理后的演员列表。
    它只返回处理结果，不执行任何数据库更新或Emby更新。
    """
    logger.info(f"API: 收到为 ItemID {item_id} 预览处理后演员的请求。")

    # 步骤 1: 获取当前媒体的 Emby 详情
    try:
        item_details = emby_handler.get_emby_item_details(
            item_id,
            media_processor_instance.emby_url,
            media_processor_instance.emby_api_key,
            media_processor_instance.emby_user_id
        )
        if not item_details:
            return jsonify({"error": "无法获取当前媒体的Emby详情"}), 404
    except Exception as e:
        logger.error(f"API /preview_processed_cast: 获取Emby详情失败 for ID {item_id}: {e}", exc_info=True)
        return jsonify({"error": f"获取Emby详情时发生错误: {e}"}), 500

    # 步骤 2: 调用核心处理方法
    try:
        current_emby_cast_raw = item_details.get("People", [])
        
        # 直接调用 MediaProcessor 的核心方法
        processed_cast_result = media_processor_instance._process_cast_list(
            current_emby_cast_people=current_emby_cast_raw,
            media_info=item_details
        )
        
        # 步骤 3: 将处理结果转换为前端友好的格式
        # processed_cast_result 的格式是内部格式，我们需要转换为前端期望的格式
        # (embyPersonId, name, role, imdbId, doubanId, tmdbId)
        
        cast_for_frontend = []
        for actor_data in processed_cast_result:
            cast_for_frontend.append({
                "embyPersonId": actor_data.get("EmbyPersonId"),
                "name": actor_data.get("Name"),
                "role": actor_data.get("Role"),
                "imdbId": actor_data.get("ImdbId"),
                "doubanId": actor_data.get("DoubanCelebrityId"),
                "tmdbId": actor_data.get("TmdbPersonId"),
                "matchStatus": "已刷新" # 可以根据 actor_data['_source_comment'] 提供更详细的状态
            })

        logger.info(f"API: 成功为 ItemID {item_id} 预览了处理后的演员列表，返回 {len(cast_for_frontend)} 位演员。")
        return jsonify(cast_for_frontend)

    except Exception as e:
        logger.error(f"API /preview_processed_cast: 调用 _process_cast_list 时发生错误 for ID {item_id}: {e}", exc_info=True)
        return jsonify({"error": "在服务器端处理演员列表时发生内部错误"}), 500    
# ★★★ START: Emby 图片代理路由 ★★★
@app.route('/image_proxy/<path:image_path>')
@processor_ready_required
def proxy_emby_image(image_path):
    """
    一个安全的、动态的 Emby 图片代理。
    """
    try:
        # 从已加载的配置中获取 Emby URL 和 API Key
        emby_url = media_processor_instance.emby_url.rstrip('/')
        emby_api_key = media_processor_instance.emby_api_key

        query_string = request.query_string.decode('utf-8')
        target_url = f"{emby_url}/{image_path}"
        if query_string:
            target_url += f"?{query_string}"
        
        headers = {
            'X-Emby-Token': emby_api_key,
            'User-Agent': request.headers.get('User-Agent', 'EmbyActorProcessorProxy/1.0')
        }
        
        logger.debug(f"代理图片请求: {target_url}")

        emby_response = requests.get(target_url, headers=headers, stream=True, timeout=20)
        emby_response.raise_for_status()

        return Response(
            stream_with_context(emby_response.iter_content(chunk_size=8192)),
            content_type=emby_response.headers.get('Content-Type'),
            status=emby_response.status_code
        )
    except Exception as e:
        logger.error(f"代理 Emby 图片时发生严重错误: {e}", exc_info=True)
        # 返回一个占位符图片
        return Response(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82',
            mimetype='image/png'
        )
# ✨✨✨ 清空待复核列表（并全部标记为已处理）的 API ✨✨✨
@app.route('/api/actions/clear_review_items', methods=['POST'])
@task_lock_required
def api_clear_review_items():
    logger.info("API: 收到清空所有待复核项目并标记为已处理的请求。")
    # ✨✨✨ 添加防御性检查 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as pre_check_conn:
            count = pre_check_conn.execute("SELECT COUNT(*) FROM failed_log").fetchone()[0]
            logger.info(f"防御性检查：在事务开始前，'failed_log' 表中有 {count} 条记录。")
    except Exception as e_check:
        logger.error(f"防御性检查失败: {e_check}")
    # ✨✨✨ 检查结束 ✨✨✨
    deleted_count = 0 # 在 try 块外部定义

    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 1. 将所有 failed_log 中的项目信息复制到 processed_log
            copy_sql = """
                REPLACE INTO processed_log (item_id, item_name, processed_at, score)
                SELECT
                    item_id,
                    item_name,
                    CURRENT_TIMESTAMP,
                    COALESCE(score, 10.0)
                FROM
                    failed_log;
            """
            cursor.execute(copy_sql)
            
            # 2. 清空 failed_log 表
            cursor.execute("DELETE FROM failed_log")
            deleted_count = cursor.rowcount
            
            # 3. 提交事务
            conn.commit()
            # with 代码块结束，连接自动关闭

    except Exception as e:
        # 如果 with 块内任何地方出错，未提交的更改会自动回滚
        logger.error(f"清空并标记待复核列表时失败: {e}", exc_info=True)
        return jsonify({"error": "服务器在处理数据库时发生内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨
    
    # 根据事务执行的结果返回响应
    if deleted_count > 0:
        message = f"操作成功！已将 {deleted_count} 个项目从待复核列表移至已处理列表。"
        logger.info(message)
        return jsonify({"message": message}), 200
    else:
        message = "操作完成，待复核列表本就是空的。"
        logger.info(message)
        return jsonify({"message": message}), 200

# # ★★★ 获取追剧列表的API ★★★
@app.route('/api/watchlist', methods=['GET']) 
@login_required
def api_get_watchlist():
    # 模式检查

    logger.info("API: 收到获取追剧列表的请求。")
    
    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row # 确保可以按列名访问
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM watchlist ORDER BY added_at DESC")
            items = [dict(row) for row in cursor.fetchall()]
        # with 代码块结束，连接自动关闭
        
        return jsonify(items)
        
    except Exception as e:
        logger.error(f"获取追剧列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取追剧列表时发生服务器内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨
# ★★★ 新增：手动添加到追剧列表的API ★★★
@app.route('/api/watchlist/add', methods=['POST'])
@login_required
def api_add_to_watchlist():
    data = request.json
    item_id = data.get('item_id')
    tmdb_id = data.get('tmdb_id')
    item_name = data.get('item_name')
    item_type = data.get('item_type')

    if not all([item_id, tmdb_id, item_name, item_type]):
        return jsonify({"error": "缺少必要的项目信息"}), 400
    
    if item_type != 'Series':
        return jsonify({"error": "只能将'剧集'类型添加到追剧列表"}), 400

    logger.info(f"API: 收到手动添加 '{item_name}' 到追剧列表的请求。")
    
    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO watchlist (item_id, tmdb_id, item_name, item_type, status, last_checked_at)
                VALUES (?, ?, ?, ?, 'Watching', NULL)
            """, (item_id, tmdb_id, item_name, item_type))
            
            conn.commit()
        # with 代码块结束，连接自动关闭
        
        return jsonify({"message": f"《{item_name}》已成功添加到追剧列表！"}), 200
        
    except Exception as e:
        # 如果 with 块内任何地方出错，未提交的更改会自动回滚
        logger.error(f"手动添加项目到追剧列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在添加时发生内部错误"}), 500
    # ✨✨✨ 修改结束 ✨✨✨

#★★★ 手动触发追剧列表更新的API ★★★
@app.route('/api/watchlist/trigger_full_update', methods=['POST']) 
@login_required
def api_trigger_watchlist_update(): # <-- 函数名可以不变，因为它和路径无关了
    # 模式检查

    # ... (这个函数的内部逻辑完全不变) ...
    
    logger.info("API: 收到手动触发追剧列表更新的请求。")
    submit_task_to_queue(
        task_process_watchlist,
        "手动追剧更新"
    )
    return jsonify({"message": "追剧列表更新任务已在后台启动！"}), 202
# ★★★ 新增：手动更新追剧状态的API ★★★
@app.route('/api/watchlist/update_status', methods=['POST'])
@login_required
@task_lock_required
def api_update_watchlist_status():
    # 1. 检查任务锁，防止并发写入
    data = request.json
    item_id = data.get('item_id')
    new_status = data.get('new_status')

    if not item_id or new_status not in ['Watching', 'Ended', 'Paused']:
        return jsonify({"error": "请求参数无效"}), 400

    logger.info(f"API: 收到请求，将项目 {item_id} 的追剧状态更新为 '{new_status}'。")
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE watchlist SET status = ? WHERE item_id = ?",
                (new_status, item_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"尝试更新追剧状态，但未在列表中找到项目 {item_id}。")
                return jsonify({"error": "未在追剧列表中找到该项目"}), 404
        
        return jsonify({"message": "状态更新成功"}), 200
        
    except Exception as e:
        logger.error(f"更新追剧状态时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在更新状态时发生内部错误"}), 500


# ★★★ 新增：手动从追剧列表移除的API ★★★
@app.route('/api/watchlist/remove/<item_id>', methods=['POST'])
@login_required
@task_lock_required
def api_remove_from_watchlist(item_id):
    logger.info(f"API: 收到请求，将项目 {item_id} 从追剧列表移除。")
    
    # ✨✨✨ 核心修改在这里 ✨✨✨
    try:
        # 1. 将 with 语句放在 try 块内部
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            
            logger.debug(f"准备执行 DELETE FROM watchlist WHERE item_id = {item_id}")
            cursor.execute("DELETE FROM watchlist WHERE item_id = ?", (item_id,))
            
            # 2. 检查操作是否成功
            if cursor.rowcount > 0:
                logger.info(f"成功执行 DELETE 语句，影响行数: {cursor.rowcount}。准备提交...")
                conn.commit()
                logger.info("数据库事务已提交。")
                return jsonify({"message": "已从追剧列表移除"}), 200
            else:
                logger.warning(f"尝试删除项目 {item_id}，但在数据库中未找到匹配项。")
                return jsonify({"error": "未在追剧列表中找到该项目"}), 404
            
    # 3. 保留你精心设计的、精细化的异常处理块
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower():
            logger.error(f"从追剧列表移除项目时发生数据库锁定错误: {e}", exc_info=True)
            return jsonify({"error": "数据库当前正忙，请稍后再试。"}), 503
        else:
            logger.error(f"从追剧列表移除项目时发生数据库操作错误: {e}", exc_info=True)
            return jsonify({"error": "移除项目时发生数据库操作错误"}), 500
            
    except Exception as e:
        logger.error(f"从追剧列表移除项目时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": "移除项目时发生未知的服务器内部错误"}), 500
    # 4. 不再需要 finally 块，因为 with 语句已经处理了连接关闭
    # ✨✨✨ 修改结束 ✨✨✨
# ★★★ 新增：手动触发单项追剧更新的API ★★★
@app.route('/api/watchlist/trigger_update/<item_id>/', methods=['POST'])
@login_required
@task_lock_required
def api_trigger_single_watchlist_update(item_id):
    logger.info(f"API: 收到对单个项目 {item_id} 的追剧更新请求。")
    if not watchlist_processor_instance:
        return jsonify({"error": "追剧处理模块未就绪"}), 503

    # 我们需要一个新的、只处理单个项目的任务函数
    # 我们在下面定义它
    submit_task_to_queue(
        task_process_single_watchlist_item,
        f"手动单项追剧更新: {item_id}",
        item_id # 把 item_id 作为参数传给任务
    )
    
    return jsonify({"message": f"项目 {item_id} 的更新任务已在后台启动！"}), 202
# ★★★ 重新处理单个项目 ★★★
@app.route('/api/actions/reprocess_item/<item_id>', methods=['POST'])
@login_required
@task_lock_required # <-- 检查任务锁
def api_reprocess_item(item_id):
    logger.info(f"API: 收到重新处理项目 '{item_id}' 的请求。")
    submit_task_to_queue(
        task_reprocess_single_item,
        f"重新处理: {item_id}",
        item_id
    )
    return jsonify({"message": f"重新处理项目 '{item_id}' 的任务已提交。"}), 202

# ★★★ 重新处理所有待复核项 ★★★
@app.route('/api/actions/reprocess_all_review_items', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def api_reprocess_all_review_items():
    """
    提交一个任务，用于重新处理所有待复核列表中的项目。
    """
    logger.info("API: 收到重新处理所有待复核项的请求。")
    # 提交一个宏任务，让后台线程来做这件事
    submit_task_to_queue(
        task_reprocess_all_review_items, # <--- 我们需要创建这个新的任务函数
        "重新处理所有待复核项"
    )
    
    return jsonify({"message": "重新处理所有待复核项的任务已提交。"}), 202
# ★★★ 触发全量图片同步的 API 接口 ★★★
@app.route('/api/actions/trigger_full_image_sync', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def api_trigger_full_image_sync():
    """
    提交一个任务，用于全量同步所有已处理项目的海报。
    """
    submit_task_to_queue(
        task_full_image_sync,
        "全量同步媒体库海报"
    )
    
    return jsonify({"message": "全量海报同步任务已成功提交。"}), 202
# --- 一键重构演员数据端点 ---
@app.route('/api/tasks/rebuild-actors', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def trigger_rebuild_actors_task():
    """
    API端点，用于触发“一键重构演员数据库”的后台任务。
    """
    try:
        # 假设你的处理器实例是全局可访问的，或者通过某种方式获取
        # 我们需要把这个函数本身，以及它的名字，提交到队列
        submit_task_to_queue(
            run_full_rebuild_task, # <--- 传递函数本身
            "重构演员数据库" # <--- 任务名
            # 注意：这里不需要传递 processor 实例，因为 task_worker_function 会自动选择
        )
        return jsonify({"status": "success", "message": "重构演员数据库任务已成功提交到后台队列。"}), 202
    except RuntimeError as e:
        # submit_task_to_queue 在有任务运行时会抛出 RuntimeError
        return jsonify({"status": "error", "message": str(e)}), 409 # 409 Conflict
    except Exception as e:
        logger.error(f"提交重构任务时发生错误: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "提交任务失败，请查看后端日志。"}), 500
# ✨✨✨ 一键删除TMDb缓存 ✨✨✨
@app.route('/api/actions/clear_tmdb_caches', methods=['POST'])
@login_required
@processor_ready_required
def api_clear_tmdb_caches():
    """
    API端点，用于触发清除TMDb相关缓存的功能。
    """
    try:
        result = media_processor_instance.clear_tmdb_caches()
        if result.get("success"):
            return jsonify(result), 200
        else:
            # 如果部分失败，返回一个服务器错误码，让前端知道事情不妙
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"调用清除TMDb缓存功能时发生意外错误: {e}", exc_info=True)
        return jsonify({"success": False, "message": "服务器在执行清除操作时发生未知错误。"}), 500
# ✨✨✨ “立即执行”API接口 ✨✨✨
@app.route('/api/tasks/trigger/<task_identifier>', methods=['POST'])
def api_trigger_task_now(task_identifier: str):
    """
    一个通用的API端点，用于立即触发指定的后台任务。
    它会响应前端发送的 /api/tasks/trigger/full-scan, /api/tasks/trigger/sync-person-map 等请求。
    """
    # 1. 检查是否有任务正在运行 (这是双重保险，防止前端禁用逻辑失效)
    with task_lock:
        if background_task_status["is_running"]:
            return jsonify({
                "status": "error",
                "message": "已有其他任务正在运行，请稍后再试。"
            }), 409 # 409 Conflict

    # 2. 从任务注册表中查找任务
    task_info = TASK_REGISTRY.get(task_identifier)
    if not task_info:
        return jsonify({
            "status": "error",
            "message": f"未知的任务标识符: {task_identifier}"
        }), 404 # Not Found

    task_function, task_name = task_info
    
    # 3. 提交任务到队列
    #    使用您现有的 submit_task_to_queue 函数
    #    对于需要额外参数的任务（如全量扫描），我们需要特殊处理
    kwargs = {}
    if task_identifier == 'full-scan':
        # 我们可以从请求体中获取参数，或者使用默认值
        # 这允许前端未来可以传递 '强制重处理' 等选项
        data = request.get_json() or {}
        kwargs['process_episodes'] = data.get('process_episodes', True)
        # 假设 task_process_full_library 接受 process_episodes 参数
    
    submit_task_to_queue(
        task_function,
        task_name,
        **kwargs # 使用字典解包来传递命名参数
    )

    return jsonify({
        "status": "success",
        "message": "任务已成功提交到后台队列。",
        "task_name": task_name
    }), 202 # 202 Accepted 表示请求已被接受，将在后台处理
# ★★★ END: 1. ★★★
#--- 兜底路由，必须放最后 ---
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    static_folder_path = app.static_folder 

    if path != "" and os.path.exists(os.path.join(static_folder_path, path)):
        return send_from_directory(static_folder_path, path)
    else:
        return send_from_directory(static_folder_path, 'index.html')
    
if __name__ == '__main__':
    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}")
    
    # 1. 加载配置到全局变量
    load_config()
    
    # 2. 初始化数据库
    init_db()
    
    # 3. 初始化认证系统 (它会依赖全局配置)
    init_auth()

    # 4. ★★★ 创建唯一的 MediaProcessor 实例 ★★★
    initialize_processors()
    
    # 5. 启动后台任务工人
    start_task_worker_if_not_running()
    
    # 6. 设置定时任务 (它会依赖全局配置和实例)
    if not scheduler.running:
        scheduler.start()
    setup_scheduled_tasks()
    
    # 7. 运行 Flask 应用
    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=True, use_reloader=False)

# # --- 主程序入口结束 ---