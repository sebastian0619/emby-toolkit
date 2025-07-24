# web_app.py
import os
import re
import json
import inspect
import sqlite3
import shutil
from datetime import date, timedelta, datetime
from actor_sync_handler import UnifiedSyncHandler
import emby_handler
import moviepilot_handler
import utils
from utils import LogDBManager
import configparser
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, stream_with_context, send_from_directory,Response, abort
from werkzeug.utils import safe_join, secure_filename
from queue import Queue
from functools import wraps
from utils import get_override_path_for_item
from watchlist_processor import WatchlistProcessor
import threading
import time
from datetime import datetime
import requests
import tmdb_handler
from douban import DoubanApi
from typing import Optional, Dict, Any, List, Tuple, Union # 确保 List 被导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
import atexit # 用于应用退出处理
from core_processor import MediaProcessor
from actor_subscription_processor import ActorSubscriptionProcessor
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

    # [MoviePilot]
    constants.CONFIG_OPTION_MOVIEPILOT_URL: (constants.CONFIG_SECTION_MOVIEPILOT, 'string', ""),
    constants.CONFIG_OPTION_MOVIEPILOT_USERNAME: (constants.CONFIG_SECTION_MOVIEPILOT, 'string', ""),
    constants.CONFIG_OPTION_MOVIEPILOT_PASSWORD: (constants.CONFIG_SECTION_MOVIEPILOT, 'string', ""),
    constants.CONFIG_OPTION_AUTOSUB_ENABLED: (constants.CONFIG_SECTION_MOVIEPILOT, 'boolean', False),

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
    constants.CONFIG_OPTION_SCHEDULE_AUTOSUB_ENABLED: (constants.CONFIG_SECTION_SCHEDULER, 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_AUTOSUB_CRON: (constants.CONFIG_SECTION_SCHEDULER, 'string', constants.DEFAULT_SCHEDULE_AUTOSUB_CRON),
    constants.CONFIG_OPTION_SCHEDULE_REFRESH_COLLECTIONS_ENABLED: ('Scheduler', 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_REFRESH_COLLECTIONS_CRON: ('Scheduler', 'string', constants.DEFAULT_SCHEDULE_REFRESH_COLLECTIONS_CRON),
    constants.CONFIG_OPTION_SCHEDULE_ACTOR_TRACKING_ENABLED: ('Scheduler', 'boolean', False),
    constants.CONFIG_OPTION_SCHEDULE_ACTOR_TRACKING_CRON: ('Scheduler', 'string', "0 5 * * *"), # 默认每天早上5点
    # [Authentication]
    constants.CONFIG_OPTION_AUTH_ENABLED: (constants.CONFIG_SECTION_AUTH, 'boolean', False),
    constants.CONFIG_OPTION_AUTH_USERNAME: (constants.CONFIG_SECTION_AUTH, 'string', constants.DEFAULT_USERNAME),
    constants.CONFIG_OPTION_ACTOR_ROLE_ADD_PREFIX: (constants.CONFIG_SECTION_ACTOR, 'boolean', False),

    # ★★★日志轮转配置 ★★★
    constants.CONFIG_OPTION_LOG_ROTATION_SIZE_MB: (
        constants.CONFIG_SECTION_LOGGING, 
        'int', 
        constants.DEFAULT_LOG_ROTATION_SIZE_MB
    ),
    constants.CONFIG_OPTION_LOG_ROTATION_BACKUPS: (
        constants.CONFIG_SECTION_LOGGING, 
        'int', 
        constants.DEFAULT_LOG_ROTATION_BACKUPS
    ),

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
    LOG_DIRECTORY = os.path.join(PERSISTENT_DATA_PATH, 'logs')

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

#过滤底层日志
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logger.info(f"配置文件路径 (CONFIG_FILE_PATH) 设置为: {CONFIG_FILE_PATH}")
logger.info(f"数据库文件路径 (DB_PATH) 设置为: {DB_PATH}")

# --- 全局变量 ---
EMBY_SERVER_ID: Optional[str] = None # ★★★ 新增：用于存储 Emby Server ID
media_processor_instance: Optional[MediaProcessor] = None
actor_subscription_processor_instance: Optional[ActorSubscriptionProcessor] = None
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
JOB_ID_REVIVAL_CHECK = "scheduled_revival_check"
# --- 全局变量结束 ---

# --- 数据库辅助函数 ---
def task_process_single_item(processor: MediaProcessor, item_id: str, force_reprocess: bool, process_episodes: bool):
    """任务：处理单个媒体项"""
    processor.process_single_item(item_id, force_reprocess, process_episodes)
# --- 初始化数据库 ---
def init_db():
    """
    【最终版】初始化数据库，创建所有表的最终结构，并包含性能优化。
    """
    logger.info("正在初始化数据库，创建/验证所有表的最终结构...")
    conn: Optional[sqlite3.Connection] = None
    try:
        # 确保数据目录存在
        if not os.path.exists(PERSISTENT_DATA_PATH):
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)

        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()

            # --- 1. ★★★ 性能优化：启用 WAL 模式 (必须保留) ★★★ ---
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                result = cursor.fetchone()
                if result and result[0].lower() == 'wal':
                    logger.trace("  -> 数据库已成功启用 WAL (Write-Ahead Logging) 模式。")
                else:
                    logger.warning(f"  -> 尝试启用 WAL 模式失败，当前模式: {result[0] if result else '未知'}。")
            except Exception as e_wal:
                logger.error(f"  -> 启用 WAL 模式时出错: {e_wal}")

            # --- 2. 创建基础表 (日志、用户) ---
            logger.trace("  -> 正在创建基础表...")
            cursor.execute("CREATE TABLE IF NOT EXISTS processed_log (item_id TEXT PRIMARY KEY, item_name TEXT, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, score REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS failed_log (item_id TEXT PRIMARY KEY, item_name TEXT, reason TEXT, failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, error_message TEXT, item_type TEXT, score REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

            # --- 3. 创建核心功能表 ---
            # 电影合集检查
            logger.trace("  -> 正在创建 'collections_info' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collections_info (
                    emby_collection_id TEXT PRIMARY KEY,
                    name TEXT,
                    tmdb_collection_id TEXT,
                    status TEXT,
                    has_missing BOOLEAN, -- ★★★ 把这个字段加回来！ ★★★
                    missing_movies_json TEXT,
                    last_checked_at TIMESTAMP,
                    poster_path TEXT
                )
            """)

            # 剧集追踪 (追剧列表) - ★★★ 已更新 ★★★
            logger.trace("  -> 正在创建/更新 'watchlist' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    item_id TEXT PRIMARY KEY,
                    tmdb_id TEXT NOT NULL,
                    item_name TEXT,
                    item_type TEXT DEFAULT 'Series',
                    status TEXT DEFAULT 'Watching',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_checked_at TIMESTAMP,
                    tmdb_status TEXT,
                    next_episode_to_air_json TEXT,
                    missing_info_json TEXT,
                    paused_until DATE DEFAULT NULL  -- ★★★ 新增字段：用于记录暂停至何时 ★★★
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist (status)")

            # ★★★ 新增：为现有数据库平滑升级的逻辑 ★★★
            # 这种方式可以确保老用户更新程序后，数据库结构也能自动更新而不会报错。
            try:
                cursor.execute("PRAGMA table_info(watchlist)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'paused_until' not in columns:
                    logger.info("    -> 检测到旧版 'watchlist' 表，正在添加 'paused_until' 字段...")
                    cursor.execute("ALTER TABLE watchlist ADD COLUMN paused_until DATE DEFAULT NULL;")
                    logger.info("    -> 'paused_until' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'watchlist' 表添加新字段时出错: {e_alter}")

            # 演员身份映射
            logger.trace("  -> 正在创建 'person_identity_map' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS person_identity_map (
                    map_id INTEGER PRIMARY KEY AUTOINCREMENT, primary_name TEXT NOT NULL, emby_person_id TEXT UNIQUE,
                    tmdb_person_id INTEGER UNIQUE, imdb_id TEXT UNIQUE, douban_celebrity_id TEXT UNIQUE,
                    last_synced_at TIMESTAMP, last_updated_at TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_emby_id ON person_identity_map (emby_person_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_tmdb_id ON person_identity_map (tmdb_person_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_imdb_id ON person_identity_map (imdb_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_douban_id ON person_identity_map (douban_celebrity_id)")

            # 演员元数据缓存
            logger.trace("  -> 正在创建 'ActorMetadata' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ActorMetadata (
                    tmdb_id INTEGER PRIMARY KEY, profile_path TEXT, gender INTEGER, adult BOOLEAN,
                    popularity REAL, original_name TEXT, last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(tmdb_id) REFERENCES person_identity_map(tmdb_person_id) ON DELETE CASCADE
                )
            """)

            # --- 4. ★★★ 演员订阅功能表 ★★★ ---
            logger.trace("  -> 正在创建 'actor_subscriptions' 表 (演员订阅)...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS actor_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_person_id INTEGER NOT NULL UNIQUE,      -- 演员在TMDb的唯一ID，这是关联的核心
                    actor_name TEXT NOT NULL,                    -- 演员名字 (用于UI显示)
                    profile_path TEXT,                           -- 演员头像路径 (用于UI显示)

                    -- 订阅配置 --
                    config_start_year INTEGER DEFAULT 1900,      -- 起始年份筛选
                    config_media_types TEXT DEFAULT 'Movie,TV',  -- 订阅的媒体类型 (逗号分隔, e.g., "Movie,TV")
                    config_genres_include_json TEXT,             -- 包含的类型ID (JSON数组, e.g., "[28, 12]")
                    config_genres_exclude_json TEXT,             -- 排除的类型ID (JSON数组, e.g., "[99]")

                    -- 状态与维护 --
                    status TEXT DEFAULT 'active',                -- 订阅状态 ('active', 'paused')
                    last_checked_at TIMESTAMP,                   -- 上次计划任务检查的时间
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 添加订阅的时间
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_as_tmdb_person_id ON actor_subscriptions (tmdb_person_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_as_status ON actor_subscriptions (status)")

            logger.trace("  -> 正在创建 'tracked_actor_media' 表 (追踪的演员媒体)...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracked_actor_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id INTEGER NOT NULL,            -- 外键，关联到 actor_subscriptions.id
                    tmdb_media_id INTEGER NOT NULL,              -- 影视项目在TMDb的ID (电影或剧集)
                    media_type TEXT NOT NULL,                    -- 'Movie' 或 'Series'

                    -- 用于UI显示和筛选的基本信息 --
                    title TEXT NOT NULL,
                    release_date TEXT,
                    poster_path TEXT,

                    -- 核心状态字段 --
                    status TEXT NOT NULL,                        -- 'IN_LIBRARY', 'PENDING_RELEASE', 'SUBSCRIBED', 'MISSING'
                    emby_item_id TEXT,                           -- 如果已入库，其在Emby中的ID
                    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY(subscription_id) REFERENCES actor_subscriptions(id) ON DELETE CASCADE,
                    UNIQUE(subscription_id, tmdb_media_id) -- 确保每个订阅下，一个媒体项只被追踪一次
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tam_subscription_id ON tracked_actor_media (subscription_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tam_status ON tracked_actor_media (status)")

            conn.commit()
            logger.info("数据库初始化完成，所有表结构已更新至最新版本。")

    except sqlite3.Error as e_sqlite:
        logger.error(f"数据库初始化时发生 SQLite 错误: {e_sqlite}", exc_info=True)
        if conn:
            try: conn.rollback()
            except Exception as e_rb: logger.error(f"SQLite 错误后回滚失败: {e_rb}")
        raise # 重新抛出异常，让程序停止
    except Exception as e_global:
        logger.error(f"数据库初始化时发生未知错误: {e_global}", exc_info=True)
        if conn:
            try: conn.rollback()
            except Exception as e_rb: logger.error(f"未知错误后回滚失败: {e_rb}")
        raise # 重新抛出异常，让程序停止
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
        logger.debug(f"检测到 AUTH_USERNAME 环境变量，将使用用户名: '{username}'")
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
            logger.trace(f"[AUTH DIAGNOSTIC] User '{username}' found in DB. No action needed.")

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
    logger.trace("全局配置 APP_CONFIG 已更新。")
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
    global media_processor_instance, watchlist_processor_instance, EMBY_SERVER_ID, actor_subscription_processor_instance
    if not APP_CONFIG:
        logger.error("无法初始化处理器：全局配置 APP_CONFIG 为空。")
        return

    current_config = APP_CONFIG.copy()
    current_config['db_path'] = DB_PATH

    # --- ★★★ 在初始化时自动发现 Server ID ★★★ ---
    emby_url = current_config.get(constants.CONFIG_OPTION_EMBY_SERVER_URL)
    emby_key = current_config.get(constants.CONFIG_OPTION_EMBY_API_KEY)
    if emby_url and emby_key:
        server_info = emby_handler.get_emby_server_info(emby_url, emby_key)
        if server_info and server_info.get("Id"):
            EMBY_SERVER_ID = server_info.get("Id")
            logger.trace(f"成功获取到 Emby Server ID: {EMBY_SERVER_ID}")
        else:
            EMBY_SERVER_ID = None
            logger.warning("未能获取到 Emby Server ID，跳转链接可能不完整。")
    else:
        EMBY_SERVER_ID = None
    # --- 初始化 MediaProcessor  ---
    if media_processor_instance:
        media_processor_instance.close()
    try:
        media_processor_instance = MediaProcessor(config=current_config)
        logger.info("核心处理器 实例已创建/更新。")
    except Exception as e:
        logger.error(f"创建 MediaProcessor 实例失败: {e}", exc_info=True)
        media_processor_instance = None

    # --- ★★★ 初始化 WatchlistProcessor ★★★ ---
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
            logger.trace("WatchlistProcessor 实例已成功初始化，随时待命。")
        except Exception as e:
            logger.error(f"创建 WatchlistProcessor 实例失败: {e}", exc_info=True)
            watchlist_processor_instance = None # 初始化失败，明确设为 None
    else:
        logger.warning("WatchlistProcessor 未初始化，因为缺少必要的 Emby 配置。")
        watchlist_processor_instance = None

    # --- ★★★ 初始化 ActorSubscriptionProcessor ★★★ ---
    if actor_subscription_processor_instance:
        try:
            # 如果以后这个类有 close 方法，可以在这里调用
            pass
        except Exception as e:
            logger.warning(f"关闭旧的 actor_subscription_processor_instance 时出错: {e}")

    try:
        # 假设它的构造函数也接收一个 config 字典
        actor_subscription_processor_instance = ActorSubscriptionProcessor(config=current_config)
        logger.trace("ActorSubscriptionProcessor 实例已成功初始化，随时待命。")
    except Exception as e:
        logger.error(f"创建 ActorSubscriptionProcessor 实例失败: {e}", exc_info=True)
        actor_subscription_processor_instance = None # 初始化失败，明确设为 None
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
                try:
                    media_processor_instance.close()
                except Exception as e_close_proc:
                    logger.error(f"调用 media_processor_instance.close() 时发生错误: {e_close_proc}", exc_info=True)

            time.sleep(1)
        background_task_status["is_running"] = False
        background_task_status["current_action"] = "无"
        background_task_status["progress"] = 0
        background_task_status["message"] = "等待任务"
        if processor:
            processor.clear_stop_signal()
        logger.trace(f"后台任务 '{task_name}' 状态已重置。")
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
            elif "演员" in task_name or "actor" in task_function.__name__: 
                processor_to_use = actor_subscription_processor_instance
                logger.debug(f"任务 '{task_name}' 将使用 ActorSubscriptionProcessor。")
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
            logger.trace("通用任务线程未运行，正在启动...")
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
    """
    【最终版】根据全局配置，设置或移除所有定时任务。
    """
    config = APP_CONFIG
    
    # --- 任务 1: 全量扫描 ---
    JOB_ID_FULL_SCAN = "scheduled_full_scan"
    if scheduler.get_job(JOB_ID_FULL_SCAN): scheduler.remove_job(JOB_ID_FULL_SCAN)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_CRON)
            force = config.get(constants.CONFIG_OPTION_SCHEDULE_FORCE_REPROCESS, False)
            
            def scheduled_scan_task():
                logger.info(f"定时任务触发：全量扫描 (强制={force})。")
                if force: media_processor_instance.clear_processed_log()
                submit_task_to_queue(task_process_full_library, "定时全量扫描", process_episodes=APP_CONFIG.get('process_episodes', True))
            
            scheduler.add_job(func=scheduled_scan_task, trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_FULL_SCAN, name="定时全量扫描", replace_existing=True)
            logger.info(f"已设置定时任务：全量扫描，将{_get_next_run_time_str(cron)}{' (强制重处理)' if force else ''}")
        except Exception as e:
            logger.error(f"设置定时全量扫描任务失败: {e}", exc_info=True)
    else:
        logger.info("定时全量扫描任务未启用。")

    # --- 任务 2: 同步演员映射表 ---
    JOB_ID_SYNC_PERSON_MAP = "scheduled_sync_person_map"
    if scheduler.get_job(JOB_ID_SYNC_PERSON_MAP): scheduler.remove_job(JOB_ID_SYNC_PERSON_MAP)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_SYNC_MAP_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_SYNC_MAP_CRON)
            scheduler.add_job(func=lambda: submit_task_to_queue(task_sync_person_map, "定时同步演员映射表"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_SYNC_PERSON_MAP, name="定时同步演员映射表", replace_existing=True)
            logger.info(f"已设置定时任务：同步演员映射表，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时同步演员映射表任务失败: {e}", exc_info=True)
    else:
        logger.info("定时同步演员映射表任务未启用。")

    # --- 任务 3: 刷新电影合集 ---
    JOB_ID_REFRESH_COLLECTIONS = 'scheduled_refresh_collections'
    if scheduler.get_job(JOB_ID_REFRESH_COLLECTIONS): scheduler.remove_job(JOB_ID_REFRESH_COLLECTIONS)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_REFRESH_COLLECTIONS_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_REFRESH_COLLECTIONS_CRON)
            scheduler.add_job(func=lambda: submit_task_to_queue(task_refresh_collections, "定时刷新电影合集"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_REFRESH_COLLECTIONS, name="定时刷新电影合集", replace_existing=True)
            logger.info(f"已设置定时任务：刷新电影合集，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时刷新电影合集任务失败: {e}", exc_info=True)
    else:
        logger.info("定时刷新电影合集任务未启用。")

    # --- 任务 4: 智能追剧刷新 ---
    JOB_ID_PROCESS_WATCHLIST = "scheduled_process_watchlist"
    if scheduler.get_job(JOB_ID_PROCESS_WATCHLIST): scheduler.remove_job(JOB_ID_PROCESS_WATCHLIST)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_CRON)
            # ★★★ 核心修改：让定时任务调用新的、职责更明确的函数 ★★★
            def scheduled_watchlist_task():
                submit_task_to_queue(
                    lambda p: p.run_regular_processing_task(update_status_from_thread),
                    "定时智能追剧更新"
                )
            scheduler.add_job(func=scheduled_watchlist_task, trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_PROCESS_WATCHLIST, name="定时常规追剧更新", replace_existing=True)
            logger.info(f"已设置定时任务：智能追剧更新，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时智能追剧更新任务失败: {e}", exc_info=True)
    else:
        logger.info("定时智能追剧更新任务未启用。")

    # ★★★ 已完结剧集复活检查 (硬编码，每周一次) ★★★
    global JOB_ID_REVIVAL_CHECK
    if scheduler.get_job(JOB_ID_REVIVAL_CHECK): scheduler.remove_job(JOB_ID_REVIVAL_CHECK)
    try:
        # 硬编码为每周日的凌晨5点执行，这个时间点API调用压力小
        revival_cron = "0 5 * * 0" 
        def scheduled_revival_check_task():
            submit_task_to_queue(
                lambda p: p.run_revival_check_task(update_status_from_thread),
                "每周已完结剧集复活检查"
            )
        scheduler.add_job(func=scheduled_revival_check_task, trigger=CronTrigger.from_crontab(revival_cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_REVIVAL_CHECK, name="每周已完结剧集复活检查", replace_existing=True)
        logger.info(f"已设置内置任务：已完结剧集复活检查，将{_get_next_run_time_str(revival_cron)}")
    except Exception as e:
        logger.error(f"设置内置的已完结剧集复活检查任务失败: {e}", exc_info=True)

    # --- 任务 5: 演员元数据 ---
    JOB_ID_ENRICH_ALIASES = 'scheduled_enrich_aliases'
    if scheduler.get_job(JOB_ID_ENRICH_ALIASES): scheduler.remove_job(JOB_ID_ENRICH_ALIASES)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_CRON)
            scheduler.add_job(func=lambda: submit_task_to_queue(task_enrich_aliases, "定时演员元数据增强"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_ENRICH_ALIASES, name="定时演员元数据增强", replace_existing=True)
            logger.info(f"已设置定时任务：演员元数据补充，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时演员元数据补充任务失败: {e}", exc_info=True)
    else:
        logger.info("定时演员元数据补充任务未启用。")

    # --- 任务 6: 演员名翻译 ---
    JOB_ID_ACTOR_CLEANUP = 'scheduled_actor_translation_cleanup'
    if scheduler.get_job(JOB_ID_ACTOR_CLEANUP): scheduler.remove_job(JOB_ID_ACTOR_CLEANUP)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_ENABLED, True):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_CRON)
            scheduler.add_job(func=lambda: submit_task_to_queue(task_actor_translation_cleanup, "定时演员名查漏补缺"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_ACTOR_CLEANUP, name="定时演员名查漏补缺", replace_existing=True)
            logger.info(f"已设置定时任务：演员名翻译，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时演员名翻译任务失败: {e}", exc_info=True)
    else:
        logger.info("定时演员名翻译任务未启用。")

    # --- 任务 7: 智能订阅 ---
    JOB_ID_AUTO_SUBSCRIBE = 'scheduled_auto_subscribe'
    if scheduler.get_job(JOB_ID_AUTO_SUBSCRIBE): scheduler.remove_job(JOB_ID_AUTO_SUBSCRIBE)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_AUTOSUB_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_AUTOSUB_CRON)
            scheduler.add_job(func=lambda: submit_task_to_queue(task_auto_subscribe, "定时智能订阅"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_AUTO_SUBSCRIBE, name="定时智能订阅", replace_existing=True)
            logger.info(f"已设置定时任务：智能订阅，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时智能订阅任务失败: {e}", exc_info=True)
    else:
        logger.info("定时智能订阅任务未启用。")

    # --- ★★★ 任务 8: 演员订阅扫描 ★★★ ---
    JOB_ID_ACTOR_TRACKING = 'scheduled_actor_tracking'
    if scheduler.get_job(JOB_ID_ACTOR_TRACKING): scheduler.remove_job(JOB_ID_ACTOR_TRACKING)
    if config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_TRACKING_ENABLED, False):
        try:
            cron = config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_TRACKING_CRON)
            scheduler.add_job(
                func=lambda: submit_task_to_queue(task_process_actor_subscriptions, "定时演员订阅扫描"),
                trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_ACTOR_TRACKING,
                name="定时演员订阅扫描",
                replace_existing=True
            )
            logger.info(f"已设置定时任务：演员订阅扫描，将{_get_next_run_time_str(cron)}")
        except Exception as e:
            logger.error(f"设置定时演员订阅扫描任务失败: {e}", exc_info=True)
    else:
        logger.info("定时演员订阅扫描任务未启用。")

    # --- 启动调度器逻辑 (包含所有任务开关) ---
    all_schedules_enabled = [
        config.get(constants.CONFIG_OPTION_SCHEDULE_ENABLED, False),
        config.get(constants.CONFIG_OPTION_SCHEDULE_SYNC_MAP_ENABLED, False),
        config.get(constants.CONFIG_OPTION_SCHEDULE_REFRESH_COLLECTIONS_ENABLED, False),
        config.get(constants.CONFIG_OPTION_SCHEDULE_WATCHLIST_ENABLED, False),
        config.get(constants.CONFIG_OPTION_SCHEDULE_ENRICH_ALIASES_ENABLED, False),
        config.get(constants.CONFIG_OPTION_SCHEDULE_ACTOR_CLEANUP_ENABLED, True),
        config.get(constants.CONFIG_OPTION_SCHEDULE_AUTOSUB_ENABLED, False)
    ]
    if not scheduler.running and any(all_schedules_enabled):
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
# ✨✨✨ 演员元数据增强函数 ✨✨✨
def task_enrich_aliases(processor: MediaProcessor):
    """
    【后台任务】演员元数据增强任务的入口点。
    它会调用 actor_utils 中的核心逻辑，并传递运行时长。
    """
    task_name = "演员元数据增强"
    logger.info(f"后台任务 '{task_name}' 开始执行...")
    update_status_from_thread(0, "准备开始演员元数据增强...")

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
        update_status_from_thread(100, "演员元数据增强任务完成。")

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
# --- 扫描单个演员订阅的所有作品 ---
def task_scan_actor_media(processor: ActorSubscriptionProcessor, subscription_id: int):
    """【新】后台任务：扫描单个演员订阅的所有作品。"""
    processor.run_full_scan_for_actor(subscription_id)
# --- 演员订阅 ---
def task_process_actor_subscriptions(processor: ActorSubscriptionProcessor):
    """【新】后台任务：执行所有启用的演员订阅扫描。"""
    processor.run_scheduled_task()
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
def task_process_watchlist(processor: WatchlistProcessor, item_id: Optional[str] = None):
    """
    【V9 - 启动器】
    调用处理器实例来执行追剧任务，并处理UI状态更新。
    """
    # 定义一个可以传递给处理器的回调函数
    def progress_updater(progress, message):
        # 这里的 update_status_from_thread 是您项目中用于更新UI的函数
        update_status_from_thread(progress, message)

    try:
        # 直接调用 processor 实例的方法，并将回调函数传入
        processor.run_regular_processing_task(progress_callback=progress_updater, item_id=item_id)

    except Exception as e:
        task_name = "追剧列表更新"
        if item_id:
            task_name = f"单项追剧更新 (ID: {item_id})"
        logger.error(f"执行 '{task_name}' 时发生顶层错误: {e}", exc_info=True)
        progress_updater(-1, f"启动任务时发生错误: {e}")
# ★★★ 只更新追剧列表中的一个特定项目 ★★★
def task_refresh_single_watchlist_item(processor: WatchlistProcessor, item_id: str):
    """
    【V11 - 新增】后台任务：只刷新追剧列表中的一个特定项目。
    这是一个职责更明确的函数，专门用于手动触发。
    """
    # 定义一个可以传递给处理器的回调函数
    def progress_updater(progress, message):
        update_status_from_thread(progress, message)

    try:
        # 直接调用处理器的主方法，并将 item_id 传入
        # 这会执行完整的元数据刷新、状态检查和数据库更新流程
        processor.run_regular_processing_task(progress_callback=progress_updater, item_id=item_id)

    except Exception as e:
        task_name = f"单项追剧刷新 (ID: {item_id})"
        logger.error(f"执行 '{task_name}' 时发生顶层错误: {e}", exc_info=True)
        progress_updater(-1, f"启动任务时发生错误: {e}")
# ★★★ 导入映射表 + 元数据 ★★★
def task_import_person_map(processor, file_content: str, **kwargs):
    """
    【V3 - 元数据增强版】从CSV文件字符串中，导入演员映射表和元数据。
    """
    task_name = "导入完整演员数据"
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

                # ★★★ 1. 拆分数据：为两个表分别准备数据字典 ★★★
                
                # 1.1 准备 person_identity_map 的数据
                person_map_data = {
                    "name": row.get('primary_name'),
                    "tmdb_id": row.get('tmdb_person_id') or None,
                    "imdb_id": row.get('imdb_id') or None,
                    "douban_id": row.get('douban_celebrity_id') or None,
                }

                # 1.2 准备 ActorMetadata 的数据
                actor_metadata = {
                    "tmdb_id": row.get('tmdb_person_id'),
                    "profile_path": row.get('profile_path') or None,
                    "gender": row.get('gender') or None,
                    "adult": row.get('adult') or None,
                    "popularity": row.get('popularity') or None,
                    "original_name": row.get('original_name') or None,
                }

                # 如果连最基本的ID都没有，就跳过
                if not person_map_data["name"] and not person_map_data["tmdb_id"]:
                    stats["skipped"] += 1
                    continue

                try:
                    # ★★★ 2. 执行数据库操作：分两步走 ★★★
                    
                    # 2.1 先插入或更新身份映射表
                    db_manager.upsert_person(cursor, person_map_data)
                    
                    # 2.2 如果有元数据，再插入或更新元数据表
                    #     我们只在有 tmdb_id 的情况下才操作元数据表
                    if actor_metadata["tmdb_id"]:
                        # 为了健壮性，将 None 转换为空字符串或0
                        # 注意：SQLite对布尔值的处理，通常是 1 和 0
                        adult_val = row.get('adult')
                        is_adult = 1 if adult_val and str(adult_val).lower() in ['true', '1', 'yes'] else 0

                        sql_upsert_metadata = """
                            INSERT OR REPLACE INTO ActorMetadata 
                            (tmdb_id, profile_path, gender, adult, popularity, original_name, last_updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """
                        cursor.execute(sql_upsert_metadata, (
                            actor_metadata["tmdb_id"],
                            actor_metadata["profile_path"],
                            actor_metadata["gender"],
                            is_adult,
                            actor_metadata["popularity"],
                            actor_metadata["original_name"]
                        ))

                    stats["processed"] += 1
                except Exception as e_row:
                    logger.error(f"处理导入文件第 {i+2} 行时发生错误: {e_row}", exc_info=True)
                    stats["errors"] += 1
                
                if i > 0 and i % 100 == 0 and total_lines > 0:
                    progress = int(((i + 1) / total_lines) * 100)
                    update_status_from_thread(progress, f"正在导入... ({i+1}/{total_lines})")
            
            conn.commit()

        message = f"导入完成。总行数: {stats['total']}, 成功处理: {stats['processed']}, 跳过: {stats['skipped']}, 错误: {stats['errors']}"
        logger.info(f"导入任务完成: {message}")
        update_status_from_thread(100, "导入完成！")

    except Exception as e:
        logger.error(f"后台导入任务失败: {e}", exc_info=True)
        update_status_from_thread(-1, f"导入失败: {e}")
# ★★★ 重新处理单个项目 ★★★
def task_reprocess_single_item(processor: MediaProcessor, item_id: str, item_name_for_ui: str):
    """
    【最终版 - 职责分离】后台任务。
    此版本负责在任务开始时设置“正在处理”的状态，并执行核心逻辑。
    """
    logger.debug(f"--- 后台任务开始执行 ({item_name_for_ui}) ---")
    
    try:
        # ✨ 关键修改：任务一开始，就用“正在处理”的状态覆盖掉旧状态
        update_status_from_thread(0, f"正在处理: {item_name_for_ui}")

        # 现在才开始真正的工作
        processor.process_single_item(
            item_id, 
            force_reprocess_this_item=True,
            force_fetch_from_tmdb=True
        )
        # 任务成功完成后的状态更新会自动由任务队列处理，我们无需关心
        logger.debug(f"--- 后台任务完成 ({item_name_for_ui}) ---")

    except Exception as e:
        logger.error(f"后台任务处理 '{item_name_for_ui}' 时发生严重错误: {e}", exc_info=True)
        update_status_from_thread(-1, f"处理失败: {item_name_for_ui}")
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
# --- 精准图片同步后台任务 ---
def image_update_task(processor: MediaProcessor, item_id: str, update_description: str):
    """
    【升级版】这是一个轻量级的后台任务，专门用于处理图片更新事件。
    它现在可以接收一个描述，以实现精准同步。
    """
    logger.debug(f"图片更新任务启动，处理项目: {item_id}，描述: '{update_description}'")

    item_details = emby_handler.get_emby_item_details(
        item_id, 
        processor.emby_url, 
        processor.emby_api_key, 
        processor.emby_user_id
    )
    if not item_details:
        logger.error(f"图片更新任务：无法获取项目 {item_id} 的详情，任务中止。")
        return

    item_name_for_log = item_details.get("Name", f"未知项目(ID:{item_id})")

    # ★★★ 修改点 4: 将 description 传递给核心同步方法 ★★★
    sync_success = processor.sync_item_images(item_details, update_description=update_description)
    
    if not sync_success:
        logger.error(f"为 '{item_name_for_log}' 同步图片时失败。")
        return

    logger.debug(f"图片更新任务完成: {item_id}")
# ★★★ 刷新合集的后台任务函数 ★★★
def task_refresh_collections(processor: MediaProcessor):
    update_status_from_thread(0, "正在获取 Emby 合集列表...")
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            
            emby_collections = emby_handler.get_all_collections_with_items(
                base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id
            )
            if emby_collections is None: raise RuntimeError("从 Emby 获取合集列表失败")

            total = len(emby_collections)
            update_status_from_thread(5, f"共找到 {total} 个合集，开始同步...")

            emby_current_ids = {c['Id'] for c in emby_collections}
            cursor.execute("SELECT emby_collection_id FROM collections_info")
            db_known_ids = {row[0] for row in cursor.fetchall()}
            
            deleted_ids = db_known_ids - emby_current_ids
            if deleted_ids:
                cursor.executemany("DELETE FROM collections_info WHERE emby_collection_id = ?", [(id,) for id in deleted_ids])

            tmdb_api_key = APP_CONFIG.get(constants.CONFIG_OPTION_TMDB_API_KEY)
            if not tmdb_api_key: raise RuntimeError("未配置 TMDb API Key")

            today_str = datetime.now().strftime('%Y-%m-%d')

            for i, collection in enumerate(emby_collections):
                if processor.is_stop_requested(): break
                
                progress = 10 + int((i / total) * 90)
                update_status_from_thread(progress, f"正在同步: {collection.get('Name', '')[:20]}... ({i+1}/{total})")

                collection_id = collection['Id']
                provider_ids = collection.get("ProviderIds", {})
                tmdb_id = provider_ids.get("TmdbCollection") or provider_ids.get("TmdbCollectionId") or provider_ids.get("Tmdb")
                
                status, has_missing = "ok", False
                all_missing_movies = []

                collection_id = collection['Id']
                provider_ids = collection.get("ProviderIds", {})
                tmdb_id = provider_ids.get("TmdbCollection") or provider_ids.get("TmdbCollectionId") or provider_ids.get("Tmdb")

                if not tmdb_id:
                    status = "unlinked"
                else:
                    details = tmdb_handler.get_collection_details_tmdb(int(tmdb_id), tmdb_api_key)
                    if not details or "parts" not in details:
                        status = "tmdb_error"
                    else:
                        emby_movie_ids = set(collection.get("ExistingMovieTmdbIds", []))
                        for movie in details.get("parts", []):
                            release_date = movie.get("release_date")
                            # 过滤掉没有发布日期的影片
                            if not release_date:
                                continue  # 跳过没有日期的影片
                            movie_tmdb_id = str(movie.get("id"))
                            if movie_tmdb_id not in emby_movie_ids:
                                release_date = movie.get("release_date")
                                movie_status = "missing"
                                if release_date and release_date > today_str:
                                    movie_status = "unreleased"
                                
                                all_missing_movies.append({
                                    "tmdb_id": movie_tmdb_id,
                                    "title": movie.get("title"),
                                    "release_date": release_date, 
                                    "poster_path": movie.get("poster_path"),
                                    "status": movie_status
                                })
                        
                        if all_missing_movies:
                            has_missing = True
                            status = "has_missing"
                
                image_tag = collection.get("ImageTags", {}).get("Primary")
                poster_path = f"/Items/{collection_id}/Images/Primary?tag={image_tag}" if image_tag else None
                
                cursor.execute("""
                    INSERT OR REPLACE INTO collections_info 
                    (emby_collection_id, name, tmdb_collection_id, status, has_missing, missing_movies_json, last_checked_at, poster_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    collection_id, collection.get('Name'), tmdb_id, status, 
                    has_missing, 
                    json.dumps(all_missing_movies), time.time(), poster_path
                ))
            
            conn.commit()
    except Exception as e:
        logger.error(f"刷新合集任务失败: {e}", exc_info=True)
        update_status_from_thread(-1, f"错误: {e}")
# ★★★ 带智能预判的自动订阅任务 ★★★
def task_auto_subscribe(processor: MediaProcessor):
    update_status_from_thread(0, "正在启动智能订阅任务...")
    
    if not APP_CONFIG.get(constants.CONFIG_OPTION_AUTOSUB_ENABLED):
        logger.info("智能订阅总开关未开启，任务跳过。")
        update_status_from_thread(100, "任务跳过：总开关未开启")
        return

    try:
        today = date.today()
        # 我们之前的修复是正确的，这里保持 season_date <= today 的逻辑
        
        update_status_from_thread(10, f"智能订阅已启动...")
        successfully_subscribed_items = []

        with get_central_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # --- 1. 处理电影合集 (这部分逻辑不变，但可以保持) ---
            update_status_from_thread(20, "正在检查缺失的电影...")
            cursor.execute("SELECT * FROM collections_info WHERE status = 'has_missing' AND missing_movies_json IS NOT NULL AND missing_movies_json != '[]'")
            collections_to_check = cursor.fetchall()
            
            for collection in collections_to_check:
                if processor.is_stop_requested(): break
                
                movies_to_keep = []
                all_missing_movies = json.loads(collection['missing_movies_json'])
                movies_changed = False
                for movie in all_missing_movies:
                    if processor.is_stop_requested(): break

                    if movie.get('status') == 'missing':
                        release_date_str = movie.get('release_date')
                        if not release_date_str:
                            movies_to_keep.append(movie)
                            continue
                        
                        try:
                            release_date = datetime.strptime(release_date_str, '%Y-%m-%d').date()
                            if release_date <= today:
                                success = moviepilot_handler.subscribe_movie_to_moviepilot(movie)
                                if success:
                                    successfully_subscribed_items.append(f"电影《{movie['title']}》")
                                    movies_changed = True
                                else:
                                    movies_to_keep.append(movie)
                            else:
                                movies_to_keep.append(movie)
                        except (ValueError, TypeError):
                            movies_to_keep.append(movie)
                    else:
                        movies_to_keep.append(movie)
                
                if movies_changed:
                    cursor.execute("UPDATE collections_info SET missing_movies_json = ? WHERE emby_collection_id = ?", (json.dumps(movies_to_keep), collection['emby_collection_id']))

            # --- 2. 处理剧集 (这是我们重点修改的部分) ---
            if not processor.is_stop_requested():
                update_status_from_thread(60, "正在检查缺失的剧集...")
                
                # ▼▼▼ 日志点 1: 打印将要执行的查询 ▼▼▼
                sql_query = "SELECT * FROM watchlist WHERE status IN ('Watching', 'Paused') AND missing_info_json IS NOT NULL AND missing_info_json != '[]'"
                logger.debug(f"【智能订阅-剧集】执行查询: {sql_query}")
                cursor.execute(sql_query)
                series_to_check = cursor.fetchall()
                
                # ▼▼▼ 日志点 2: 打印找到了多少需要检查的剧集 ▼▼▼
                logger.info(f"【智能订阅-剧集】从数据库找到 {len(series_to_check)} 部状态为'在追'或'暂停'且有缺失信息的剧集需要检查。")

                for series in series_to_check:
                    if processor.is_stop_requested(): break
                    
                    # ▼▼▼ 日志点 3: 开始处理单部剧集 ▼▼▼
                    series_name = series['item_name']
                    logger.info(f"【智能订阅-剧集】>>> 正在检查: 《{series_name}》")
                    
                    try:
                        missing_info = json.loads(series['missing_info_json'])
                        missing_seasons = missing_info.get('missing_seasons', [])
                        
                        # ▼▼▼ 日志点 4: 检查是否有缺失的季 ▼▼▼
                        if not missing_seasons:
                            logger.info(f"【智能订阅-剧集】   -> 《{series_name}》没有记录在案的缺失季(missing_seasons为空)，跳过。")
                            continue

                        seasons_to_keep = []
                        seasons_changed = False
                        for season in missing_seasons:
                            if processor.is_stop_requested(): break
                            
                            season_num = season.get('season_number')
                            air_date_str = season.get('air_date')
                            
                            # ▼▼▼ 日志点 5: 检查播出日期是否存在 ▼▼▼
                            if not air_date_str:
                                logger.warning(f"【智能订阅-剧集】   -> 《{series_name}》第 {season_num} 季缺少播出日期(air_date)，无法判断，跳过。")
                                seasons_to_keep.append(season)
                                continue
                            
                            season_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()

                            # ▼▼▼ 日志点 6: 核心判断，并打印决策过程！▼▼▼
                            if season_date <= today:
                                logger.info(f"【智能订阅-剧集】   -> 《{series_name}》第 {season_num} 季 (播出日期: {season_date}) 已播出，符合订阅条件，正在提交...")
                                success = moviepilot_handler.subscribe_series_to_moviepilot(dict(series), season['season_number'])
                                if success:
                                    logger.info(f"【智能订阅-剧集】      -> 订阅成功！")
                                    successfully_subscribed_items.append(f"《{series['item_name']}》第 {season['season_number']} 季")
                                    seasons_changed = True
                                else:
                                    logger.error(f"【智能订阅-剧集】      -> 订阅失败！将保留在缺失列表中。")
                                    seasons_to_keep.append(season)
                            else:
                                # 这是您最想要的日志！
                                logger.info(f"【智能订阅-剧集】   -> 《{series_name}》第 {season_num} 季 (播出日期: {season_date}) 尚未播出，跳过订阅。")
                                seasons_to_keep.append(season)
                        
                        if seasons_changed:
                            missing_info['missing_seasons'] = seasons_to_keep
                            cursor.execute("UPDATE watchlist SET missing_info_json = ? WHERE item_id = ?", (json.dumps(missing_info), series['item_id']))
                    except Exception as e_series:
                        logger.error(f"【智能订阅-剧集】处理剧集 '{series['item_name']}' 时出错: {e_series}")
            
            conn.commit()

        if successfully_subscribed_items:
            summary = "任务完成！已自动订阅: " + ", ".join(successfully_subscribed_items)
            logger.info(summary)
            update_status_from_thread(100, summary)
        else:
            update_status_from_thread(100, "任务完成：本次运行没有发现符合自动订阅条件的媒体。")

    except Exception as e:
        logger.error(f"智能订阅任务失败: {e}", exc_info=True)
        update_status_from_thread(-1, f"错误: {e}")
# --- 立即执行任务注册表 ---
TASK_REGISTRY = {
    'full-scan': (task_process_full_library, "立即执行全量扫描"),
    'sync-person-map': (task_sync_person_map, "立即执行同步演员映射表"),
    'process-watchlist': (task_process_watchlist, "立即执行智能追剧刷新"),
    'enrich-aliases': (task_enrich_aliases, "立即执行演员元数据补充"),
    'actor-cleanup': (task_actor_translation_cleanup, "立即执行演员名翻译"),
    'refresh-collections': (task_refresh_collections, "立即执行电影合集刷新"),
    'auto-subscribe': (task_auto_subscribe, "立即执行智能订阅"),
    'actor-tracking': (task_process_actor_subscriptions, "立即执行演员订阅")
}
# --- 路由区 ---
# --- webhook通知任务 ---
@app.route('/webhook/emby', methods=['POST'])
@processor_ready_required
def emby_webhook():
    data = request.json
    event_type = data.get("Event") if data else "未知事件"
    logger.info(f"收到Emby Webhook: {event_type}")
    
    trigger_events = ["item.add", "library.new", "image.update"] 

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


    # --- 分支 A: 处理元数据新增/更新事件 ---
    if event_type in ["item.add", "library.new"]:
        # 这部分是您原有的核心修复逻辑，保持不变
        id_to_process = original_item_id
        type_to_process = original_item_type

        if original_item_type == "Episode":
            # ... (您原有的向上查找剧集ID的逻辑) ...
            logger.info(f"Webhook 收到分集 '{original_item_name}' (ID: {original_item_id})，正在向上查找其所属剧集...")
            series_id = emby_handler.get_series_id_from_child_id(
                original_item_id,
                media_processor_instance.emby_url,
                media_processor_instance.emby_api_key,
                media_processor_instance.emby_user_id
            )
            if series_id:
                id_to_process = series_id
                type_to_process = "Series"
                logger.info(f"成功找到所属剧集 ID: {id_to_process}。将处理此剧集。")
            else:
                logger.error(f"无法为分集 '{original_item_name}' 找到所属剧集ID，将跳过处理。")
                return jsonify({"status": "event_ignored_series_not_found"}), 200

        full_item_details = emby_handler.get_emby_item_details(
            item_id=id_to_process,
            emby_server_url=media_processor_instance.emby_url,
            emby_api_key=media_processor_instance.emby_api_key,
            user_id=media_processor_instance.emby_user_id
        )

        if not full_item_details:
            logger.error(f"无法获取项目 {id_to_process} 的完整详情，处理中止。")
            return jsonify({"status": "event_ignored_details_fetch_failed"}), 200

        final_item_name = full_item_details.get("Name", f"未知项目(ID:{id_to_process})")
        provider_ids = full_item_details.get("ProviderIds", {})
        tmdb_id = provider_ids.get("Tmdb")

        if not tmdb_id:
            logger.warning(f"项目 '{final_item_name}' (ID: {id_to_process}) 缺少 TMDb ID，无法进行处理。将跳过本次 Webhook 请求。")
            return jsonify({"status": "event_ignored_no_tmdb_id"}), 200
            
        logger.info(f"Webhook事件触发，最终处理项目 '{final_item_name}' (ID: {id_to_process}, TMDbID: {tmdb_id}) 已提交到任务队列。")
        
        submit_task_to_queue(
            webhook_processing_task,
            f"Webhook处理: {final_item_name}",
            id_to_process,
            force_reprocess=True, 
            process_episodes=True
        )
        
        return jsonify({"status": "metadata_task_queued", "item_id": id_to_process}), 202

    # --- 分支 B: 处理图片更新事件 ---
    elif event_type == "image.update":
        # ★★★ 核心修改点 1: 获取 Description 字段 ★★★
        update_description = data.get("Description", "")
        logger.info(f"Webhook 图片更新事件触发，项目 '{original_item_name}' (ID: {original_item_id})。描述: '{update_description}'")
        
        # ★★★ 核心修改点 2: 将 description 传递给任务队列 ★★★
        submit_task_to_queue(
            image_update_task,
            f"精准图片同步: {original_item_name}",
            # --- 传递给 image_update_task 的参数 ---
            item_id=original_item_id,
            update_description=update_description # <--- 新增参数
        )
        
        return jsonify({"status": "precise_image_task_queued", "item_id": original_item_id}), 202
    
    return jsonify({"status": "event_unhandled"}), 500
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
    config = APP_CONFIG
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
        current_config = APP_CONFIG 
        
        if current_config:
            current_config['emby_server_id'] = EMBY_SERVER_ID
            logger.trace(f"API /api/config (GET): 成功加载并返回配置。")
            return jsonify(current_config)
        else:
            logger.error(f"API /api/config (GET): APP_CONFIG 为空或未初始化。")
            return jsonify({"error": "无法加载配置数据"}), 500
    except Exception as e:
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
        action_message += " (已清空已处理记录)"

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
    
    stopped_any = False
    # --- ★★★ 核心修复：尝试停止所有可能的处理器 ★★★ ---
    if media_processor_instance:
        media_processor_instance.signal_stop()
        stopped_any = True
        
    if watchlist_processor_instance:
        watchlist_processor_instance.signal_stop()
        stopped_any = True

    if actor_subscription_processor_instance:
        actor_subscription_processor_instance.signal_stop()
        stopped_any = True
    # --- 修复结束 ---

    if stopped_any:
        logger.info("已发送停止信号给当前正在运行的任务。")
        return jsonify({"message": "已发送停止任务请求。"}), 200
    else:
        logger.warning("API: 没有任何处理器实例被初始化，无法发送停止信号。")
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
# ★★★ 导出演员映射表 + 元数据 ★★★
@app.route('/api/actors/export', methods=['GET'])
@login_required
def api_export_person_map():
    """
    【V2 - 元数据增强版】导出演员身份映射表和TMDb元数据缓存。
    """
    # ★★★ 1. 扩展表头，加入 ActorMetadata 的字段 ★★★
    headers = [
        'primary_name', 
        'tmdb_person_id', 'imdb_id', 'douban_celebrity_id',
        'profile_path', 'gender', 'adult', 'popularity', 'original_name'
    ]
    logger.info(f"API: 收到导出完整演员数据 (map + metadata) 的请求。")

    def generate_csv():
        string_io = StringIO()
        try:
            with get_central_db_connection(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                writer = csv.DictWriter(string_io, fieldnames=headers, extrasaction='ignore')
                
                writer.writeheader()
                yield string_io.getvalue()
                string_io.seek(0); string_io.truncate(0)

                # ★★★ 2. 改造SQL查询，使用 LEFT JOIN 合并两张表 ★★★
                query = """
                    SELECT
                        p.primary_name,
                        p.tmdb_person_id,
                        p.imdb_id,
                        p.douban_celebrity_id,
                        m.profile_path,
                        m.gender,
                        m.adult,
                        m.popularity,
                        m.original_name
                    FROM
                        person_identity_map AS p
                    LEFT JOIN
                        ActorMetadata AS m ON p.tmdb_person_id = m.tmdb_id
                """
                cursor.execute(query)
                
                for row in cursor:
                    writer.writerow(dict(row))
                    yield string_io.getvalue()
                    string_io.seek(0); string_io.truncate(0)
        except Exception as e:
            logger.error(f"导出完整演员数据时发生错误: {e}", exc_info=True)
            yield f"Error: {e}"

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"full_actor_data_backup_{timestamp}.csv" # 文件名也更新一下
    
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
        current_config = APP_CONFIG
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
    【V2 - 完整修复版】确保 api_key 作为 URL 参数传递，适用于所有图片类型。
    """
    try:
        emby_url = media_processor_instance.emby_url.rstrip('/')
        emby_api_key = media_processor_instance.emby_api_key

        # 1. 构造基础 URL，包含路径和原始查询参数
        query_string = request.query_string.decode('utf-8')
        target_url = f"{emby_url}/{image_path}"
        if query_string:
            target_url += f"?{query_string}"
        
        # 2. ★★★ 核心修复：将 api_key 作为 URL 参数追加 ★★★
        # 判断是使用 '?' 还是 '&' 来追加 api_key
        separator = '&' if '?' in target_url else '?'
        target_url_with_key = f"{target_url}{separator}api_key={emby_api_key}"
        
        logger.trace(f"代理图片请求 (最终URL): {target_url_with_key}")

        # 3. 发送请求
        emby_response = requests.get(target_url_with_key, stream=True, timeout=20)
        emby_response.raise_for_status()

        # 4. 将 Emby 的响应流式传输回浏览器
        return Response(
            stream_with_context(emby_response.iter_content(chunk_size=8192)),
            content_type=emby_response.headers.get('Content-Type'),
            status=emby_response.status_code
        )
    except Exception as e:
        logger.error(f"代理 Emby 图片时发生严重错误: {e}", exc_info=True)
        # 返回一个1x1的透明像素点作为占位符，避免显示大的裂图图标
        return Response(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82',
            mimetype='image/png'
        )
# ✨✨✨ 清空待复核列表（并全部标记为已处理）的 API ✨✨✨
@app.route('/api/actions/clear_review_items', methods=['POST'])
@task_lock_required
def api_clear_review_items_revised():
    logger.info("API: 收到清空所有待复核项目并标记为已处理的请求。")
    
    # 首先，在事务外部获取初始计数，用于最终验证
    try:
        with get_central_db_connection(DB_PATH) as pre_check_conn:
            initial_count = pre_check_conn.execute("SELECT COUNT(*) FROM failed_log").fetchone()[0]
            logger.info(f"防御性检查：在事务开始前，'failed_log' 表中有 {initial_count} 条记录。")
            if initial_count == 0:
                message = "操作完成，待复核列表本就是空的。"
                logger.info(message)
                return jsonify({"message": message}), 200
    except Exception as e_check:
        logger.error(f"数据库预检查失败: {e_check}")
        return jsonify({"error": "服务器在预检查数据库时发生内部错误"}), 500

    copied_count = 0
    deleted_count = 0

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
            # ▲▲▲ 关键改动(1)：获取 REPLACE 操作影响的行数
            copied_count = cursor.rowcount 
            logger.info(f"事务内：尝试从 '待复核' 复制 {copied_count} 条记录到 '已处理'。")

            # 2. 清空 failed_log 表
            cursor.execute("DELETE FROM failed_log")
            deleted_count = cursor.rowcount # 获取 DELETE 操作影响的行数
            logger.info(f"事务内：尝试从 '待复核' 删除 {deleted_count} 条记录。")
            
            # ▲▲▲ 关键改动(2)：添加验证逻辑
            # 只有当复制的记录数和删除的记录数相等，且与初始数量一致时，才提交事务。
            # 这可以防止源数据被删除但目标数据未成功写入的情况。
            if copied_count == deleted_count and initial_count == deleted_count:
                conn.commit() # 验证通过，提交事务
                logger.info(f"数据一致性验证成功 ({copied_count} == {deleted_count})，事务已提交。")
            else:
                conn.rollback() # 验证失败，回滚事务
                logger.error(
                    f"数据不一致，事务已回滚！"
                    f"初始数量: {initial_count}, "
                    f"尝试复制: {copied_count}, "
                    f"尝试删除: {deleted_count}."
                )
                return jsonify({"error": "服务器在处理数据时检测到不一致性，操作已自动取消以防止数据丢失。"}), 500

    except Exception as e:
        # 如果 with 块内任何地方出错，未提交的更改会自动回滚
        logger.error(f"清空并标记待复核列表时发生未知异常: {e}", exc_info=True)
        return jsonify({"error": "服务器在处理数据库时发生内部错误"}), 500
    
    # 根据事务执行的结果返回响应
    # 如果代码执行到这里，意味着事务是成功提交的
    message = f"操作成功！已将 {deleted_count} 个项目从待复核列表移至已处理列表。"
    logger.info(message)
    return jsonify({"message": message}), 200
# # ★★★ 获取追剧列表的API ★★★
@app.route('/api/watchlist', methods=['GET']) 
@login_required
def api_get_watchlist():
    # 模式检查

    logger.debug("API: 收到获取追剧列表的请求。")
    
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
# ★★★ 手动触发单项追剧更新的API ★★★
@app.route('/api/watchlist/refresh/<item_id>', methods=['POST'])
@login_required
@task_lock_required
def api_trigger_single_watchlist_refresh(item_id):
    """
    【V11 - 新增】API端点，用于手动触发对单个追剧项目的即时刷新。
    """
    logger.trace(f"API: 收到对单个追剧项目 {item_id} 的刷新请求。")
    if not watchlist_processor_instance:
        return jsonify({"error": "追剧处理模块未就绪"}), 503

    # 从数据库获取项目名称，让任务名更友好
    item_name = "未知剧集"
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT item_name FROM watchlist WHERE item_id = ?", (item_id,))
            row = cursor.fetchone()
            if row:
                item_name = row[0]
    except Exception as e:
        logger.warning(f"获取项目 {item_id} 名称时出错: {e}")

    # 提交我们刚刚创建的、职责单一的新任务
    submit_task_to_queue(
        task_refresh_single_watchlist_item,
        f"手动刷新: {item_name}",
        item_id # 把 item_id 作为参数传给任务
    )
    
    return jsonify({"message": f"《{item_name}》的刷新任务已在后台启动！"}), 202
# ★★★ 重新处理单个项目 ★★★
@app.route('/api/actions/reprocess_item/<item_id>', methods=['POST'])
@login_required
@task_lock_required # <-- 检查任务锁
def api_reprocess_item(item_id):
    """
    【最终版 - 职责分离】启动单个项目重新处理的API接口。
    此版本只负责提交任务和设置一个简单的“已提交”状态。
    """
    global media_processor_instance

    logger.info(f"API: 收到重新处理项目 '{item_id}' 的请求。")
    
    # 仍然需要获取名称，以便传递给后台任务
    item_details = emby_handler.get_emby_item_details(
        item_id,
        media_processor_instance.emby_url,
        media_processor_instance.emby_api_key,
        media_processor_instance.emby_user_id
    )
    item_name_for_ui = item_details.get("Name", f"ItemID: {item_id}") if item_details else f"ItemID: {item_id}"

    logger.info(f"API: 将为 '{item_name_for_ui}' 提交重新处理任务。")

    # ✨ 关键修改：设置一个非常简单的初始状态，避免与后台任务冲突
    submit_task_to_queue(
        task_reprocess_single_item,
        f"任务已提交: {item_name_for_ui}",  # <--- 只说“已提交”
        item_id,
        item_name_for_ui  # 将友好名称作为参数传递给后台任务
    )
    
    return jsonify({"message": f"重新处理项目 '{item_name_for_ui}' 的任务已提交。"}), 202
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
        data = request.get_json(silent=True) or {}
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
# ▼▼▼ 日志查看器 API 路由 ▼▼▼
@app.route('/api/logs/list', methods=['GET'])
@login_required
def list_log_files():
    """列出日志目录下的所有日志文件 (app.log*)"""
    try:
        # PERSISTENT_DATA_PATH 变量在当前作用域中可以直接使用
        all_files = os.listdir(LOG_DIRECTORY)
        log_files = [f for f in all_files if f.startswith('app.log')]
        
        # 对日志文件进行智能排序，确保 app.log 在最前，然后是 .1.gz, .2.gz ...
        def sort_key(filename):
            if filename == 'app.log':
                return -1
            parts = filename.split('.')
            # 适用于 'app.log.1.gz' 这样的格式
            if len(parts) > 2 and parts[-1] == 'gz' and parts[-2].isdigit():
                return int(parts[-2])
            return float('inf') # 其他不规范的格式排在最后

        log_files.sort(key=sort_key)
        return jsonify(log_files)
    except Exception as e:
        logging.error(f"API: 无法列出日志文件: {e}", exc_info=True)
        return jsonify({"error": "无法读取日志文件列表"}), 500
@app.route('/api/logs/view', methods=['GET'])
@login_required
def view_log_file():
    """查看指定日志文件的内容，自动处理 .gz 文件"""
    # 安全性第一：防止目录遍历攻击
    filename = secure_filename(request.args.get('filename', ''))
    if not filename or not filename.startswith('app.log'):
        abort(403, "禁止访问非日志文件或无效的文件名。")

    full_path = os.path.join(LOG_DIRECTORY, filename)

    # 再次确认最终路径仍然在合法的日志目录下
    if not os.path.abspath(full_path).startswith(os.path.abspath(LOG_DIRECTORY)):
        abort(403, "检测到非法路径访问。")
        
    if not os.path.exists(full_path):
        abort(404, "文件未找到。")

    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        return Response(content, mimetype='text/plain')
        
    except Exception as e:
        logging.error(f"API: 读取日志文件 '{filename}' 时出错: {e}", exc_info=True)
        abort(500, f"读取文件 '{filename}' 时发生内部错误。")
@app.route('/api/logs/search', methods=['GET'])
@login_required
def search_all_logs():
    """
    在所有日志文件 (app.log*) 中搜索关键词。
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "搜索关键词不能为空"}), 400
    TIMESTAMP_REGEX = re.compile(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})")

    search_results = []
    
    try:
        # 1. 获取并排序所有日志文件，确保从新到旧搜索
        all_files = os.listdir(LOG_DIRECTORY)
        log_files = [f for f in all_files if f.startswith('app.log')]
        
        # --- 代码修改点 ---
        # 简化了排序键，不再处理 .gz 后缀
        def sort_key(filename):
            if filename == 'app.log':
                return -1  # app.log 永远排在最前面
            parts = filename.split('.')
            # 适用于 app.log.1, app.log.2 等格式
            if len(parts) == 3 and parts[0] == 'app' and parts[1] == 'log' and parts[2].isdigit():
                return int(parts[2])
            return float('inf') # 其他不符合格式的文件排在最后
        
        log_files.sort(key=sort_key)

        # 2. 遍历每个文件进行搜索
        for filename in log_files:
            full_path = os.path.join(LOG_DIRECTORY, filename)
            try:
                # --- 代码修改点 ---
                # 移除了 opener 的判断，直接使用 open 函数
                with open(full_path, 'rt', encoding='utf-8', errors='ignore') as f:
                    # 逐行读取，避免内存爆炸
                    for line_num, line in enumerate(f, 1):
                        # 不区分大小写搜索
                        if query.lower() in line.lower():
                            match = TIMESTAMP_REGEX.search(line)
                            line_date = match.group(1) if match else "" # 如果匹配失败则为空字符串
                            
                            # 2. 将提取到的日期添加到返回结果中
                            search_results.append({
                                "file": filename,
                                "line_num": line_num,
                                "content": line.strip(),
                                "date": line_date  # <--- 新增的日期字段
                            })
            except Exception as e:
                # 如果单个文件读取失败，记录错误并继续
                logging.warning(f"API: 搜索时无法读取文件 '{filename}': {e}")

        search_results.sort(key=lambda x: x['date'])
        return jsonify(search_results)

    except Exception as e:
        logging.error(f"API: 全局日志搜索时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "搜索过程中发生服务器内部错误"}), 500
@app.route('/api/logs/search_context', methods=['GET'])
@login_required
def search_logs_with_context():
    """
    【最终修正版】在所有日志文件中定位包含关键词的完整“处理块”，
    并根据块内的时间戳进行精确排序，同时保留日期信息。
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "搜索关键词不能为空"}), 400

    # 正则表达式保持不变
    START_MARKER = re.compile(r"成功获取Emby演员 '(.+?)' \(ID: .*?\) 的详情")
    END_MARKER = re.compile(r"(✨✨✨处理完成|最终状态: 处理完成)")
    TIMESTAMP_REGEX = re.compile(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})")

    found_blocks = []
    
    try:
        # 获取所有 app.log* 文件，无需预先排序
        all_files = os.listdir(LOG_DIRECTORY)
        log_files = [f for f in all_files if f.startswith('app.log')]

        for filename in log_files:
            full_path = os.path.join(LOG_DIRECTORY, filename)
            
            in_block = False
            current_block = []
            current_item_name = None

            try:
                with open(full_path, 'rt', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue

                        is_start_marker = START_MARKER.search(line)

                        if is_start_marker:
                            if in_block: pass
                            in_block = True
                            current_block = [line]
                            current_item_name = is_start_marker.group(1)
                        
                        elif in_block:
                            current_block.append(line)
                            
                            is_end_marker = END_MARKER.search(line)
                            
                            if is_end_marker and current_item_name and current_item_name in line:
                                block_content = "\n".join(current_block)
                                if query.lower() in block_content.lower():
                                    
                                    # ★★★ 核心修改 1: 提取时间戳，并将其存储在名为 'date' 的键中 ★★★
                                    block_date = "Unknown Date" # 默认值
                                    if current_block:
                                        match = TIMESTAMP_REGEX.search(current_block[0])
                                        if match:
                                            # match.group(1) 的结果是 "YYYY-MM-DD HH:MM:SS"
                                            block_date = match.group(1)

                                    found_blocks.append({
                                        "file": filename,
                                        "date": block_date, # <--- 使用 'date' 键，前端需要它
                                        "lines": current_block
                                    })
                                
                                in_block = False
                                current_block = []
                                current_item_name = None
            except Exception as e:
                logging.warning(f"API: 上下文搜索时无法读取文件 '{filename}': {e}")
        
        # ★★★ 核心修改 2: 根据我们刚刚添加的 'date' 键进行排序 ★★★
        found_blocks.sort(key=lambda x: x['date'])
        
        # ★★★ 核心修改 3: 移除那行画蛇添足的 "del" 代码 ★★★
        # (这里不再有删除键的代码)

        return jsonify(found_blocks)

    except Exception as e:
        logging.error(f"API: 上下文日志搜索时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "搜索过程中发生服务器内部错误"}), 500
# ★★★ 获取所有合集状态的 API 端点 ★★★
@app.route('/api/collections/status', methods=['GET'])
@login_required
@processor_ready_required
def api_get_collections_status():
    """
    获取所有 Emby 合集状态。
    - 默认：直接从数据库读取。
    - force_refresh=true：提交一个后台任务来执行全量同步。
    【V5 - 任务化版】
    """
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'

    # --- 模式一：强制刷新（提交后台任务） ---
    if force_refresh:
        # 使用我们现有的装饰器逻辑来检查任务锁
        if task_lock.locked():
            return jsonify({"error": "后台有任务正在运行，请稍后再试。"}), 409
        
        submit_task_to_queue(
            task_refresh_collections,
            "刷新合集列表"
        )
        return jsonify({"message": "刷新任务已在后台启动，请稍后查看结果。"}), 202

    # --- 模式二：快速读取（默认行为） ---
    try:
        with get_central_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM collections_info ORDER BY name")
            final_results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                row_dict['missing_movies'] = json.loads(row_dict.get('missing_movies_json', '[]'))
                del row_dict['missing_movies_json']
                final_results.append(row_dict)
            return jsonify(final_results)
    except Exception as e:
        logger.error(f"读取合集状态时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "读取合集时发生服务器内部错误"}), 500
# ★★★ 将电影提交到 MoviePilot 订阅  ★★★
@app.route('/api/subscribe/moviepilot', methods=['POST'])
@login_required
def api_subscribe_moviepilot():
    data = request.json
    tmdb_id = data.get('tmdb_id')
    title = data.get('title')

    if not tmdb_id or not title:
        return jsonify({"error": "请求中缺少 tmdb_id 或 title"}), 400

    # 1. 从配置中获取信息
    moviepilot_url = APP_CONFIG.get(constants.CONFIG_OPTION_MOVIEPILOT_URL, '').rstrip('/')
    mp_username = APP_CONFIG.get(constants.CONFIG_OPTION_MOVIEPILOT_USERNAME, '')
    mp_password = APP_CONFIG.get(constants.CONFIG_OPTION_MOVIEPILOT_PASSWORD, '')

    if not all([moviepilot_url, mp_username, mp_password]):
        return jsonify({"error": "服务器未完整配置 MoviePilot URL、用户名或密码。"}), 500

    # --- 流程第一步：登录并获取 Token ---
    try:
        login_url = f"{moviepilot_url}/api/v1/login/access-token"
        login_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        login_data = {"username": mp_username, "password": mp_password}

        logger.trace(f"正在向 MoviePilot ({login_url}) 请求 access token...")
        login_response = requests.post(login_url, headers=login_headers, data=login_data, timeout=10)
        login_response.raise_for_status()

        login_json = login_response.json()
        access_token = login_json.get("access_token")

        if not access_token:
            logger.error("MoviePilot 登录成功，但未在响应中找到 access_token。")
            return jsonify({"error": "MoviePilot 认证失败：未能获取到 Token。"}), 500
        
        logger.trace("成功获取 MoviePilot access token。")

    except requests.exceptions.HTTPError as e:
        error_msg = f"MoviePilot 登录认证失败: {e.response.status_code}。请检查用户名和密码。"
        logger.error(f"{error_msg} 响应: {e.response.text}")
        return jsonify({"error": error_msg}), 502
    except requests.exceptions.RequestException as e:
        error_msg = f"连接 MoviePilot 时发生网络错误: {e}"
        logger.error(error_msg)
        return jsonify({"error": error_msg}), 503

    # --- 流程第二步：使用 Token 提交订阅 (只有在第一步成功后才会执行) ---
    try:
        subscribe_url = f"{moviepilot_url}/api/v1/subscribe/"
        subscribe_headers = {"Authorization": f"Bearer {access_token}"}
        subscribe_payload = {"name": title, "tmdbid": int(tmdb_id), "type": "电影"}

        logger.info(f"正在向 MoviePilot 提交订阅请求 '{title}'")
        sub_response = requests.post(subscribe_url, headers=subscribe_headers, json=subscribe_payload, timeout=15)
        
        logger.trace(f"收到 MoviePilot 订阅接口的响应: Status={sub_response.status_code}, Body='{sub_response.text}'")

        if sub_response.status_code in [200, 201, 204]:
            logger.info("通过 MoviePilot 订阅成功。")
            return jsonify({"message": f"《{title}》已成功提交到 MoviePilot 订阅！"}), 200
        else:
            error_detail = "未知错误"
            try:
                error_json = sub_response.json()
                error_detail = error_json.get("message") or error_json.get("detail", sub_response.text)
            except Exception:
                error_detail = sub_response.text
            logger.error(f"通过 MoviePilot 订阅失败: {error_detail}")
            return jsonify({"error": f"MoviePilot 报告订阅失败: {error_detail}"}), 500

    except requests.exceptions.HTTPError as e:
        error_msg = f"向 MoviePilot 提交订阅时出错: {e.response.status_code} - {e.response.text}"
        logger.error(error_msg)
        return jsonify({"error": error_msg}), 502
    except requests.exceptions.RequestException as e:
        error_msg = f"连接 MoviePilot 时发生网络错误: {e}"
        logger.error(error_msg)
        return jsonify({"error": error_msg}), 503
# ★★★ 订阅剧集（季/集）的专用API ★★★
@app.route('/api/subscribe/moviepilot/series', methods=['POST'])
@login_required
def api_subscribe_series_moviepilot():
    data = request.json
    tmdb_id = data.get('tmdb_id')
    title = data.get('title')
    season_number = data.get('season_number') # 可能是季号，也可能是null

    if not tmdb_id or not title:
        return jsonify({"error": "请求中缺少 tmdb_id 或 title"}), 400

    # (这里的登录逻辑与电影订阅完全相同，可以直接复用)
    moviepilot_url = APP_CONFIG.get(constants.CONFIG_OPTION_MOVIEPILOT_URL, '').rstrip('/')
    mp_username = APP_CONFIG.get(constants.CONFIG_OPTION_MOVIEPILOT_USERNAME, '')
    mp_password = APP_CONFIG.get(constants.CONFIG_OPTION_MOVIEPILOT_PASSWORD, '')
    if not all([moviepilot_url, mp_username, mp_password]):
        return jsonify({"error": "服务器未完整配置 MoviePilot URL、用户名或密码。"}), 500

    try:
        login_url = f"{moviepilot_url}/api/v1/login/access-token"
        login_data = {"username": mp_username, "password": mp_password}
        login_response = requests.post(login_url, data=login_data, timeout=10)
        login_response.raise_for_status()
        access_token = login_response.json().get("access_token")
        if not access_token:
            return jsonify({"error": "MoviePilot 认证失败：未能获取到 Token。"}), 500
    except Exception as e:
        # (为了简洁，省略了详细的错误处理，实际应与电影订阅的错误处理保持一致)
        return jsonify({"error": f"MoviePilot 登录失败: {e}"}), 502

    # --- 核心订阅逻辑 ---
    try:
        subscribe_url = f"{moviepilot_url}/api/v1/subscribe/"
        subscribe_headers = {"Authorization": f"Bearer {access_token}"}
        
        # ★★★ 核心区别：根据是否有 season_number 构造不同的 payload ★★★
        subscribe_payload = {
            "name": title,
            "tmdbid": int(tmdb_id),
            "type": "电视剧"
        }
        if season_number is not None:
            subscribe_payload["season"] = int(season_number)
            log_message = f"正在向 MoviePilot 提交订阅请求 '{title}' 第 {season_number} 季"
        else:
            # 如果不指定季，通常是订阅整部剧
            log_message = f"正在向 MoviePilot 提交订阅请求 '{title}' (整部剧)"

        logger.info(log_message)
        sub_response = requests.post(subscribe_url, headers=subscribe_headers, json=subscribe_payload, timeout=15)
        
        logger.info(f"收到 MoviePilot 订阅接口的响应: Status={sub_response.status_code}, Body='{sub_response.text}'")

        if sub_response.status_code in [200, 201, 204]:
            logger.info("MoviePilot 报告订阅成功。")
            return jsonify({"message": "订阅请求已成功发送！"}), 200
        else:
            error_detail = sub_response.json().get("message", sub_response.text)
            logger.error(f"MoviePilot 报告订阅失败: {error_detail}")
            return jsonify({"error": f"MoviePilot 报告订阅失败: {error_detail}"}), 500

    except Exception as e:
        return jsonify({"error": f"向 MoviePilot 提交订阅时出错: {e}"}), 502
# --- 演员订阅 API ---
@app.route('/api/actor-subscriptions/search', methods=['GET'])
@login_required
@processor_ready_required
def api_search_actors():
    """
    API: 根据提供的名字搜索演员。
    """
    query = request.args.get('name', '').strip()
    if not query:
        return jsonify({"error": "必须提供搜索关键词 'name'"}), 400

    tmdb_api_key = APP_CONFIG.get(constants.CONFIG_OPTION_TMDB_API_KEY)
    if not tmdb_api_key:
        return jsonify({"error": "服务器未配置TMDb API Key"}), 503

    try:
        search_results = tmdb_handler.search_person_tmdb(query, tmdb_api_key)
        if search_results is None:
            return jsonify({"error": "从TMDb搜索演员时发生错误"}), 500
        
        # 为了前端方便，我们可以稍微处理一下结果，让信息更清晰
        formatted_results = []
        for person in search_results:
            # 只选择有头像和有知名作品的演员，过滤掉一些无关结果
            if person.get('profile_path') and person.get('known_for'):
                 formatted_results.append({
                     "id": person.get("id"),
                     "name": person.get("name"),
                     "profile_path": person.get("profile_path"),
                     "known_for_department": person.get("known_for_department"),
                     # 将 "known_for" 里的作品标题拼接起来，方便前端展示
                     "known_for": ", ".join([
                         item.get('title', item.get('name', '')) 
                         for item in person.get('known_for', [])
                     ])
                 })

        return jsonify(formatted_results)
    except Exception as e:
        logger.error(f"API /api/actor-subscriptions/search 发生错误: {e}", exc_info=True)
        return jsonify({"error": "搜索演员时发生未知的服务器错误"}), 500
# --- 获取所有演员订阅列表，或新增一个演员订阅 ---
@app.route('/api/actor-subscriptions', methods=['GET', 'POST'])
@login_required
def handle_actor_subscriptions():
    """
    API: 获取所有演员订阅列表，或新增一个演员订阅。
    """
    # --- 处理 GET 请求：获取列表 ---
    if request.method == 'GET':
        try:
            with get_central_db_connection(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT id, tmdb_person_id, actor_name, profile_path, status, last_checked_at FROM actor_subscriptions ORDER BY added_at DESC")
                subscriptions = [dict(row) for row in cursor.fetchall()]
            return jsonify(subscriptions)
        except Exception as e:
            logger.error(f"API 获取演员订阅列表失败: {e}", exc_info=True)
            return jsonify({"error": "获取订阅列表时发生服务器内部错误"}), 500

    # --- 处理 POST 请求：新增订阅 ---
    if request.method == 'POST':
        data = request.json
        tmdb_person_id = data.get('tmdb_person_id')
        actor_name = data.get('actor_name')
        profile_path = data.get('profile_path')
        config = data.get('config', {}) # <<< 1. 获取配置字典

        # 从配置字典中提取具体值，并提供默认值
        start_year = config.get('start_year', 1900)
        media_types = config.get('media_types', 'Movie,TV')
        genres_include = config.get('genres_include_json', '[]')
        genres_exclude = config.get('genres_exclude_json', '[]')

        if not all([tmdb_person_id, actor_name]):
            return jsonify({"error": "缺少必要的参数 (tmdb_person_id, actor_name)"}), 400

        try:
            with get_central_db_connection(DB_PATH) as conn:
                cursor = conn.cursor()
                # vvv 2. 修改 INSERT 语句以包含新字段 vvv
                cursor.execute(
                    """
                    INSERT INTO actor_subscriptions 
                    (tmdb_person_id, actor_name, profile_path, config_start_year, config_media_types, config_genres_include_json, config_genres_exclude_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tmdb_person_id, actor_name, profile_path, start_year, media_types, genres_include, genres_exclude)
                )
                conn.commit()
                new_sub_id = cursor.lastrowid
            

            logger.info(f"成功添加新的演员订阅: {actor_name} (TMDb ID: {tmdb_person_id})")
            return jsonify({"message": f"演员 {actor_name} 已成功订阅！", "id": new_sub_id}), 201

        except sqlite3.IntegrityError:
            return jsonify({"error": "该演员已经被订阅过了"}), 409
        except Exception as e:
            logger.error(f"API 添加演员订阅失败: {e}", exc_info=True)
            return jsonify({"error": "添加订阅时发生服务器内部错误"}), 500
# --- 手动触发对单个演员订阅的刷新任务 ---
@app.route('/api/actor-subscriptions/<int:sub_id>/refresh', methods=['POST'])
@login_required
@task_lock_required
def refresh_single_actor_subscription(sub_id):
    """【新】API: 手动触发对单个演员订阅的刷新任务。"""
    logger.info(f"API: 收到对订阅ID {sub_id} 的手动刷新请求。")
    
    # 为了让任务名更友好，可以先从数据库查一下演员名字
    actor_name = f"订阅ID {sub_id}"
    try:
        with get_central_db_connection(DB_PATH) as conn:
            cursor = conn.cursor()
            name = cursor.execute("SELECT actor_name FROM actor_subscriptions WHERE id = ?", (sub_id,)).fetchone()
            if name:
                actor_name = name[0]
    except Exception:
        pass # 查不到也无所谓

    submit_task_to_queue(
        task_scan_actor_media,
        f"手动刷新演员: {actor_name}",
        sub_id
    )
    return jsonify({"message": f"刷新演员 {actor_name} 作品的任务已提交！"}), 202
# --- 获取、更新或删除单个演员的订阅详情 ---
@app.route('/api/actor-subscriptions/<int:sub_id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def handle_single_actor_subscription(sub_id):
    """
    API: 获取、更新或删除单个演员的订阅详情。
    """
    # --- 处理 GET 请求：获取详情 ---
    if request.method == 'GET':
        try:
            with get_central_db_connection(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # 1. 获取订阅主信息
                cursor.execute("SELECT * FROM actor_subscriptions WHERE id = ?", (sub_id,))
                subscription = cursor.fetchone()
                if not subscription:
                    return jsonify({"error": "未找到指定的订阅"}), 404
                
                # 2. 获取该订阅追踪的所有媒体项目
                cursor.execute("SELECT * FROM tracked_actor_media WHERE subscription_id = ? ORDER BY release_date DESC", (sub_id,))
                tracked_media = [dict(row) for row in cursor.fetchall()]

            # 3. 组合成一个对象返回给前端
            response_data = dict(subscription)
            response_data['tracked_media'] = tracked_media
            
            return jsonify(response_data)

        except Exception as e:
            logger.error(f"API 获取订阅详情 {sub_id} 失败: {e}", exc_info=True)
            return jsonify({"error": "获取订阅详情时发生服务器内部错误"}), 500
    
    # --- ★★★ 新增：处理 PUT 请求：更新配置 ★★★ ---
    if request.method == 'PUT':
        try:
            config = request.json.get('config', {})
            if not config:
                return jsonify({"error": "请求体中缺少配置数据"}), 400

            # 从配置字典中提取具体值
            start_year = config.get('start_year', 1900)
            media_types = config.get('media_types', 'Movie,TV')
            genres_include = config.get('genres_include_json', '[]')
            genres_exclude = config.get('genres_exclude_json', '[]')

            with get_central_db_connection(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE actor_subscriptions SET
                    config_start_year = ?, config_media_types = ?, 
                    config_genres_include_json = ?, config_genres_exclude_json = ?
                    WHERE id = ?
                """, (start_year, media_types, genres_include, genres_exclude, sub_id))
                conn.commit()

            logger.info(f"成功更新订阅ID {sub_id} 的配置。")
            return jsonify({"message": "配置已成功保存！"}), 200
        except Exception as e:
            logger.error(f"API 更新订阅配置 {sub_id} 失败: {e}", exc_info=True)
            return jsonify({"error": "保存配置时发生服务器内部错误"}), 500

    # --- ★★★ 新增：处理 DELETE 请求：删除订阅 ★★★ ---
    if request.method == 'DELETE':
        try:
            with get_central_db_connection(DB_PATH) as conn:
                cursor = conn.cursor()
                # ON DELETE CASCADE 会自动删除 tracked_actor_media 中的关联数据
                cursor.execute("DELETE FROM actor_subscriptions WHERE id = ?", (sub_id,))
                conn.commit()
            
            logger.info(f"成功删除订阅ID {sub_id}。")
            return jsonify({"message": "订阅已成功删除。"}), 200
        except Exception as e:
            logger.error(f"API 删除订阅 {sub_id} 失败: {e}", exc_info=True)
            return jsonify({"error": "删除订阅时发生服务器内部错误"}), 500

    return jsonify({"error": "Method Not Allowed"}), 405
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
    
    # 1. ★★★ 首先，加载配置，让 APP_CONFIG 获得真实的值 ★★★
    load_config()
    
    # 2. ★★★ 然后，再执行依赖于配置的日志设置 ★★★
    # --- 日志文件处理器配置 ---
    LOG_DIRECTORY = os.path.join(PERSISTENT_DATA_PATH, 'logs')

    # 从现在已经有值的 APP_CONFIG 中获取配置
    raw_size = APP_CONFIG.get(
        constants.CONFIG_OPTION_LOG_ROTATION_SIZE_MB, 
        constants.DEFAULT_LOG_ROTATION_SIZE_MB
    )
    try:
        log_size = int(raw_size)
    except (ValueError, TypeError):
        log_size = constants.DEFAULT_LOG_ROTATION_SIZE_MB

    raw_backups = APP_CONFIG.get(
        constants.CONFIG_OPTION_LOG_ROTATION_BACKUPS, 
        constants.DEFAULT_LOG_ROTATION_BACKUPS
    )
    try:
        log_backups = int(raw_backups)
    except (ValueError, TypeError):
        log_backups = constants.DEFAULT_LOG_ROTATION_BACKUPS

    # 将正确的配置注入日志系统
    add_file_handler(
        log_directory=LOG_DIRECTORY,
        log_size_mb=log_size,
        log_backups=log_backups
    )
    
    # 3. 初始化数据库
    init_db()
    
    # 4. 初始化认证系统 (它会依赖全局配置)
    init_auth()

    # 5. 创建唯一的 MediaProcessor 实例
    initialize_processors()
    
    # 6. 启动后台任务工人
    start_task_worker_if_not_running()
    
    # 7. 设置定时任务 (它会依赖全局配置和实例)
    if not scheduler.running:
        scheduler.start()
    setup_scheduled_tasks()
    
    # 8. 运行 Flask 应用
    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=True, use_reloader=False)

# # --- 主程序入口结束 ---