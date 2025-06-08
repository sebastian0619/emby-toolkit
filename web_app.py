# web_app.py
import os
import sqlite3
import emby_handler
import utils
import configparser
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, stream_with_context, send_from_directory,Response
from werkzeug.utils import safe_join
from queue import Queue
import threading
import time
from douban import DoubanApi
from typing import Optional, Dict, Any, List # 确保 List 被导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
import atexit # 用于应用退出处理
from core_processor import MediaProcessor, SyncHandler
import csv
from io import StringIO
# --- 核心模块导入 ---
import constants # 你的常量定义
from core_processor import MediaProcessor # 核心处理逻辑
from logger_setup import logger, frontend_log_queue # 日志记录器和前端日志队列
# emby_handler 和 utils 会在需要的地方被 core_processor 或此文件中的函数调用
# 如果直接在此文件中使用它们的功能，也需要在这里导入
import utils       # 例如，用于 /api/search_media
# from douban import DoubanApi # 通常不需要在 web_app.py 直接导入 DoubanApi，由 MediaProcessor 管理
# --- 核心模块导入结束 ---
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
    logger.info(f"未检测到 APP_DATA_DIR 环境变量，将使用本地开发数据路径: {PERSISTENT_DATA_PATH}")

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

logger.info(f"配置文件路径 (CONFIG_FILE_PATH) 设置为: {CONFIG_FILE_PATH}")
logger.info(f"数据库文件路径 (DB_PATH) 设置为: {DB_PATH}")


# --- 全局变量 ---
media_processor_instance: Optional[MediaProcessor] = None
background_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务"
}
task_lock = threading.Lock() # 用于确保后台任务串行执行

# ✨✨✨ Webhook任务队列和工人线程 ✨✨✨
webhook_task_queue = Queue()
webhook_worker_thread: Optional[threading.Thread] = None
webhook_worker_lock = threading.Lock() # 用于安全地启动工人线程


scheduler = BackgroundScheduler(timezone=str(pytz.timezone(constants.TIMEZONE)))
JOB_ID_FULL_SCAN = "scheduled_full_scan"
JOB_ID_SYNC_PERSON_MAP = "scheduled_sync_person_map"
# --- 全局变量结束 ---

# --- 数据库辅助函数 ---
def get_db_connection() -> sqlite3.Connection:
    # 确保 DB_PATH 是有效的，并且目录存在
    # 这个函数本身不应该处理目录创建，那是 init_db 的责任
    if not os.path.exists(os.path.dirname(DB_PATH)): # 检查数据库文件所在的目录是否存在
        logger.warning(f"数据库目录 {os.path.dirname(DB_PATH)} 不存在，get_db_connection 可能失败。")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """初始化数据库表结构。只在表不存在时创建它们。"""
    conn: Optional[sqlite3.Connection] = None
    cursor: Optional[sqlite3.Cursor] = None
    try:
        if not os.path.exists(PERSISTENT_DATA_PATH):
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
            logger.info(f"持久化数据目录已创建: {PERSISTENT_DATA_PATH}")

        conn = get_db_connection()
        cursor = conn.cursor()

        # --- processed_log 表 ---
        # 只在表不存在时创建，如果已存在则不操作
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_log (
                item_id TEXT PRIMARY KEY,
                item_name TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                score REAL
            )
        """)
        # **重要：如果表已存在但没有 score 列，你需要手动或通过一次性脚本添加它**
        # 例如： self._add_column_if_not_exists(cursor, "processed_log", "score", "REAL")
        logger.info("Table 'processed_log' schema confirmed/created if not exists.")

        # --- failed_log 表 ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_log (
                item_id TEXT PRIMARY KEY, 
                item_name TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error_message TEXT,
                item_type TEXT,
                score REAL
            )
        """)
        # **同上，如果表已存在但没有 score 列，需要手动或脚本添加**
        # self._add_column_if_not_exists(cursor, "failed_log", "score", "REAL")
        logger.info("Table 'failed_log' schema confirmed/created if not exists.")

        # --- translation_cache 表 ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_cache (
                original_text TEXT PRIMARY KEY,
                translated_text TEXT,
                engine_used TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_original_text ON translation_cache (original_text)")
        logger.info("Table 'translation_cache' and index schema confirmed/created if not exists.")

        # --- person_identity_map 表 ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_identity_map (
                map_id INTEGER PRIMARY KEY AUTOINCREMENT,
                emby_person_id TEXT UNIQUE NOT NULL,
                emby_person_name TEXT,
                tmdb_person_id TEXT, 
                tmdb_name TEXT,
                imdb_id TEXT,
                douban_celebrity_id TEXT,
                douban_name TEXT,
                last_synced_at TIMESTAMP,
                last_updated_at TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_emby_person_id ON person_identity_map (emby_person_id)")
        # ... (其他 person_identity_map 的索引) ...
        logger.info("Table 'person_identity_map' and indexes schema confirmed/created if not exists.")

        conn.commit()
        logger.info(f"数据库表结构已在 '{DB_PATH}' 检查/创建完毕 (如果不存在)。")

    except sqlite3.Error as e_sqlite: # 更具体地捕获 SQLite 错误
        logger.error(f"数据库初始化时发生 SQLite 错误: {e_sqlite}", exc_info=True)
        if conn:
            try: conn.rollback()
            except Exception as e_rb: logger.error(f"SQLite 错误后回滚失败: {e_rb}")
    except OSError as e_os: # 捕获目录创建等OS错误
        logger.error(f"数据库初始化时发生文件/目录操作错误: {e_os}", exc_info=True)
    except Exception as e_global:
        logger.error(f"数据库初始化时发生未知错误: {e_global}", exc_info=True)
        if conn: # 如果连接存在但发生了其他未知错误，也尝试回滚
            try: conn.rollback()
            except Exception as e_rb: logger.error(f"未知错误后回滚失败: {e_rb}")
    finally:
        if cursor: # 先关闭 cursor
            try: cursor.close()
            except Exception as e_cur_close: logger.debug(f"关闭 cursor 时出错: {e_cur_close}")
        if conn:
            try: conn.close()
            except Exception as e_conn_close: logger.debug(f"关闭 conn 时出错: {e_conn_close}")
            else: logger.debug("数据库连接已在 init_db 的 finally 块中关闭。")# --- 数据库辅助函数结束 ---

# --- 配置加载与保存 ---
def load_config() -> Dict[str, Any]:
    """
    从 config.ini 文件加载配置。
    如果文件不存在或缺少任何节或选项，都能安全地使用默认值。
    """
    config_parser = configparser.ConfigParser()
    
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            config_parser.read(CONFIG_FILE_PATH, encoding='utf-8')
            logger.info(f"配置已从 '{CONFIG_FILE_PATH}' 加载。")
        except configparser.Error as e:
            logger.error(f"读取配置文件 '{CONFIG_FILE_PATH}' 失败: {e}。")
    else:
        logger.warning(f"配置文件 '{CONFIG_FILE_PATH}' 未找到。")

    # 定义所有期望的节，如果不存在就创建
    expected_sections = [
        constants.CONFIG_SECTION_EMBY, constants.CONFIG_SECTION_TMDB,
        constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_SECTION_TRANSLATION,
        constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_SECTION_LOCAL_DATA,
        "General", "Scheduler", "Network", "AITranslation" # 确保AI的节在这里
    ]
    for section_name in expected_sections:
        if not config_parser.has_section(section_name):
            config_parser.add_section(section_name)

    app_cfg: Dict[str, Any] = {}

    # --- 使用 fallback 参数来提供默认值，确保万无一失 ---
    
    # Emby Section
    app_cfg["emby_server_url"] = config_parser.get(constants.CONFIG_SECTION_EMBY, "emby_server_url", fallback="")
    app_cfg["emby_api_key"] = config_parser.get(constants.CONFIG_SECTION_EMBY, "emby_api_key", fallback="")
    app_cfg["emby_user_id"] = config_parser.get(constants.CONFIG_SECTION_EMBY, "emby_user_id", fallback="")
    app_cfg["refresh_emby_after_update"] = config_parser.getboolean(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", fallback=True)
    libraries_str = config_parser.get(constants.CONFIG_SECTION_EMBY, "libraries_to_process", fallback="")
    app_cfg["libraries_to_process"] = [lib_id.strip() for lib_id in libraries_str.split(',') if lib_id.strip()]

    # TMDB, Douban, Translation, etc.
    app_cfg["tmdb_api_key"] = config_parser.get(constants.CONFIG_SECTION_TMDB, "tmdb_api_key", fallback="")
    app_cfg["api_douban_default_cooldown_seconds"] = config_parser.getfloat(constants.CONFIG_SECTION_API_DOUBAN, "api_douban_default_cooldown_seconds", fallback=1.0)
    engines_str = config_parser.get(constants.CONFIG_SECTION_TRANSLATION, "translator_engines_order", fallback="bing,google")
    app_cfg["translator_engines_order"] = [eng.strip() for eng in engines_str.split(',') if eng.strip()]
    app_cfg["data_source_mode"] = config_parser.get(constants.CONFIG_SECTION_DOMESTIC_SOURCE, "data_source_mode", fallback="local_then_online")
    app_cfg["local_data_path"] = config_parser.get(constants.CONFIG_SECTION_LOCAL_DATA, "local_data_path", fallback="").strip()

    # General Section
    app_cfg["delay_between_items_sec"] = config_parser.getfloat("General", "delay_between_items_sec", fallback=0.5)
    app_cfg["min_score_for_review"] = config_parser.getfloat("General", "min_score_for_review", fallback=6.0)
    app_cfg["process_episodes"] = config_parser.getboolean("General", "process_episodes", fallback=True)

    # Network Section
    app_cfg["user_agent"] = config_parser.get("Network", "user_agent", fallback='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36')
    app_cfg["accept_language"] = config_parser.get("Network", "accept_language", fallback='zh-CN,zh;q=0.9,en;q=0.8')

    # ✨✨✨ 关键修复：把AI配置的 fallback 也加上 ✨✨✨
    app_cfg["ai_translation_enabled"] = config_parser.getboolean("AITranslation", "ai_translation_enabled", fallback=False)
    app_cfg["ai_provider"] = config_parser.get("AITranslation", "ai_provider", fallback="openai")
    app_cfg["ai_api_key"] = config_parser.get("AITranslation", "ai_api_key", fallback="")
    app_cfg["ai_model_name"] = config_parser.get("AITranslation", "ai_model_name", fallback="gpt-3.5-turbo")
    app_cfg["ai_base_url"] = config_parser.get("AITranslation", "ai_base_url", fallback="")
    app_cfg["ai_translation_prompt"] = config_parser.get("AITranslation", "ai_translation_prompt", fallback=constants.DEFAULT_AI_TRANSLATION_PROMPT)
    # ✨✨✨ 修复结束 ✨✨✨

    # Scheduler Section
    app_cfg["schedule_enabled"] = config_parser.getboolean("Scheduler", "schedule_enabled", fallback=False)
    app_cfg["schedule_cron"] = config_parser.get("Scheduler", "schedule_cron", fallback="0 3 * * *")
    app_cfg["schedule_force_reprocess"] = config_parser.getboolean("Scheduler", "schedule_force_reprocess", fallback=False)
    app_cfg["schedule_sync_map_enabled"] = config_parser.getboolean("Scheduler", "schedule_sync_map_enabled", fallback=False)
    app_cfg["schedule_sync_map_cron"] = config_parser.get("Scheduler", "schedule_sync_map_cron", fallback="0 1 * * *")

    return app_cfg

def save_config(new_config: Dict[str, Any]):
    config = configparser.ConfigParser()
    
    if os.path.exists(CONFIG_FILE_PATH):
        config.read(CONFIG_FILE_PATH, encoding='utf-8')

    # ✨✨✨ 关键修复：在设置任何值之前，确保所有节都存在 ✨✨✨
    all_sections_to_manage = [
        constants.CONFIG_SECTION_EMBY,
        constants.CONFIG_SECTION_TMDB,
        constants.CONFIG_SECTION_API_DOUBAN,
        constants.CONFIG_SECTION_TRANSLATION,
        constants.CONFIG_SECTION_DOMESTIC_SOURCE,
        constants.CONFIG_SECTION_LOCAL_DATA,
        "General",
        "Scheduler",
        "Network",
        "AITranslation" # 确保AI的节在这里
    ]

    for section_name in all_sections_to_manage:
        if not config.has_section(section_name):
            logger.info(f"保存配置：配置文件中缺少节 '[{section_name}]'，将自动创建。")
            config.add_section(section_name)
    # ✨✨✨ 修复结束 ✨✨✨

    # --- 现在可以安全地设置每个配置项了 ---
    
    # Emby Section
    config.set(constants.CONFIG_SECTION_EMBY, "emby_server_url", str(new_config.get("emby_server_url", "")))
    config.set(constants.CONFIG_SECTION_EMBY, "emby_api_key", str(new_config.get("emby_api_key", "")))
    config.set(constants.CONFIG_SECTION_EMBY, "emby_user_id", str(new_config.get("emby_user_id", "")))
    config.set(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", str(new_config.get("refresh_emby_after_update", True)).lower())
    libraries_list = new_config.get("libraries_to_process", [])
    if not isinstance(libraries_list, list):
        libraries_list = [lib_id.strip() for lib_id in str(libraries_list).split(',') if lib_id.strip()]
    config.set(constants.CONFIG_SECTION_EMBY, "libraries_to_process", ",".join(map(str, libraries_list)))

    # TMDB, Douban, Translation, etc.
    config.set(constants.CONFIG_SECTION_TMDB, "tmdb_api_key", str(new_config.get("tmdb_api_key", "")))
    config.set(constants.CONFIG_SECTION_API_DOUBAN, "api_douban_default_cooldown_seconds", str(new_config.get("api_douban_default_cooldown_seconds", 1.0)))
    engines_list = new_config.get("translator_engines_order", [])
    if not isinstance(engines_list, list) or not engines_list:
        engines_list = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
    config.set(constants.CONFIG_SECTION_TRANSLATION, "translator_engines_order", ",".join(engines_list))
    config.set(constants.CONFIG_SECTION_DOMESTIC_SOURCE, "data_source_mode", str(new_config.get("data_source_mode", "local_then_online")))
    config.set(constants.CONFIG_SECTION_LOCAL_DATA, "local_data_path", str(new_config.get("local_data_path", "")))

    # General Section
    config.set("General", "delay_between_items_sec", str(new_config.get("delay_between_items_sec", "0.5")))
    config.set("General", "min_score_for_review", str(new_config.get("min_score_for_review", "6.0")))
    config.set("General", "process_episodes", str(new_config.get("process_episodes", True)).lower())

    # Network Section
    config.set("Network", "user_agent", str(new_config.get("user_agent", "")))
    config.set("Network", "accept_language", str(new_config.get("accept_language", "")))

    # AITranslation Section
    config.set("AITranslation", "ai_translation_enabled", str(new_config.get("ai_translation_enabled", False)).lower())
    config.set("AITranslation", "ai_provider", str(new_config.get("ai_provider", "openai")))
    config.set("AITranslation", "ai_api_key", str(new_config.get("ai_api_key", "")))
    config.set("AITranslation", "ai_model_name", str(new_config.get("ai_model_name", "gpt-3.5-turbo")))
    config.set("AITranslation", "ai_base_url", str(new_config.get("ai_base_url", "")))
    config.set("AITranslation", "ai_translation_prompt", str(new_config.get("ai_translation_prompt", "")))

    # Scheduler Section
    config.set("Scheduler", "schedule_enabled", str(new_config.get("schedule_enabled", False)).lower())
    config.set("Scheduler", "schedule_cron", str(new_config.get("schedule_cron", "0 3 * * *")))
    config.set("Scheduler", "schedule_force_reprocess", str(new_config.get("schedule_force_reprocess", False)).lower())
    config.set("Scheduler", "schedule_sync_map_enabled", str(new_config.get("schedule_sync_map_enabled", False)).lower())
    config.set("Scheduler", "schedule_sync_map_cron", str(new_config.get("schedule_sync_map_cron", "0 1 * * *")))

    try:
        if not os.path.exists(PERSISTENT_DATA_PATH):
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
            config.write(configfile)
        logger.info(f"配置已成功写入到 {CONFIG_FILE_PATH}。")
    except Exception as e:
        logger.error(f"保存配置文件 {CONFIG_FILE_PATH} 失败: {e}", exc_info=True)
    finally:
        initialize_media_processor()
        setup_scheduled_tasks()
# --- 配置加载与保存结束 --- (确保这个注释和你的文件结构匹配)

# --- MediaProcessor 初始化 ---
def initialize_media_processor():
    global media_processor_instance
    current_config = load_config() # load_config 现在会包含 libraries_to_process
    current_config['db_path'] = DB_PATH

    # ... (关闭旧实例的逻辑不变) ...
    if media_processor_instance and hasattr(media_processor_instance, 'close'):
        logger.debug("准备关闭旧的 MediaProcessor 实例...")
        try:
            media_processor_instance.close()
            logger.debug("旧的 MediaProcessor 实例已关闭。")
        except Exception as e_close_old:
            logger.error(f"关闭旧 MediaProcessor 实例时出错: {e_close_old}", exc_info=True)

    logger.info("准备创建新的 MediaProcessor 实例...")
    try:
        media_processor_instance = MediaProcessor(config=current_config) # current_config 已包含所需信息
        logger.debug("新的 MediaProcessor 实例已创建/更新。")
    except Exception as e_init_mp:
        logger.error(f"创建 MediaProcessor 实例失败: {e_init_mp}", exc_info=True)
        media_processor_instance = None
        print(f"CRITICAL ERROR: MediaProcessor 核心处理器初始化失败: {e_init_mp}. 应用可能无法正常工作。")
# --- MediaProcessor 初始化结束 ---
# ✨✨✨ 工人线程的核心逻辑 ✨✨✨
def webhook_worker_function():
    """
    这个函数在后台线程中运行，持续从队列中获取并处理任务。
    """
    logger.info("Webhook工人线程已启动，等待任务...")
    while True:
        try:
            # 从队列中获取任务，如果队列为空，它会在这里阻塞等待
            item_id = webhook_task_queue.get()

            # "None" 是我们用来优雅地停止线程的“毒丸”信号
            if item_id is None:
                logger.info("Webhook工人线程收到停止信号，即将退出。")
                break

            # 使用通用的后台任务执行器来处理这个单一任务
            # 这样可以复用状态更新和锁的逻辑
            try:
                current_config = load_config()
                # 从配置中获取 process_episodes 的值，如果找不到，默认为 True (深度处理)
                process_episodes_from_config = current_config.get('process_episodes', True)
                logger.info(f"Webhook任务 (ID: {item_id})：根据配置，处理分集开关为: {process_episodes_from_config}")
            except Exception as e:
                logger.error(f"Webhook任务 (ID: {item_id})：读取配置失败: {e}。将默认使用深度处理模式。")
                process_episodes_from_config = True

            def single_webhook_task_internal(id_to_process, process_episodes_flag):
                if media_processor_instance:
                    # ✨ 把开关传递给核心函数 ✨
                    media_processor_instance.process_single_item(
                        id_to_process,
                        process_episodes=process_episodes_flag
                    )
                else:
                    logger.error(f"Webhook处理 Item ID {id_to_process} 失败：MediaProcessor 未初始化。")
            
            # ✨ 把开关作为参数传递给后台任务 ✨
            _execute_task_with_lock(
                single_webhook_task_internal, 
                f"Webhook处理 Item ID: {item_id}", 
                item_id,
                process_episodes_from_config # 这个会作为 *args 传给 single_webhook_task_internal
            )
            webhook_task_queue.task_done()
        except Exception as e:
            logger.error(f"Webhook工人线程发生未知错误: {e}", exc_info=True)
            time.sleep(5)

# ✨✨✨ 安全地启动工人线程的辅助函数 ✨✨✨
def start_webhook_worker_if_not_running():
    """
    检查工人线程是否在运行，如果不在，则启动它。
    使用锁来确保线程安全。
    """
    global webhook_worker_thread
    with webhook_worker_lock:
        if webhook_worker_thread is None or not webhook_worker_thread.is_alive():
            logger.info("Webhook工人线程未运行，正在启动...")
            webhook_worker_thread = threading.Thread(target=webhook_worker_function, daemon=True)
            webhook_worker_thread.start()
        else:
            logger.debug("Webhook工人线程已在运行。")
# --- 后台任务回调 ---
def update_status_from_thread(progress: int, message: str):
    global background_task_status
    if progress >= 0:
        background_task_status["progress"] = progress
    background_task_status["message"] = message
    # logger.debug(f"状态更新回调: Progress={progress}%, Message='{message}'") # 这条日志太频繁，可以注释掉
# --- 后台任务回调结束 ---

# --- 后台任务封装 ---
def _execute_task_with_lock(task_function, task_name: str, *args, **kwargs):
    """通用后台任务执行器，包含锁和状态管理"""
    global background_task_status
    if task_lock.locked():
        logger.warning(f"任务 '{task_name}' 触发：但已有其他后台任务运行，本次跳过。")
        if 'update_status_callback' in kwargs: # 如果有回调，通知它
             kwargs['update_status_callback'](-1, "已有任务运行，本次跳过")
        return

    with task_lock:
        frontend_log_queue.clear()
        if media_processor_instance:
            media_processor_instance.clear_stop_signal()
        else: # 如果 media_processor_instance 未初始化成功
            logger.error(f"任务 '{task_name}' 无法启动：MediaProcessor 未初始化。")
            background_task_status["message"] = "错误：核心处理器未就绪"
            background_task_status["is_running"] = False
            return


        background_task_status["is_running"] = True
        background_task_status["current_action"] = task_name
        background_task_status["progress"] = 0
        background_task_status["message"] = f"{task_name} 初始化..."
        logger.info(f"后台任务 '{task_name}' 开始执行。")

        task_completed_normally = False
        try:
            task_function(*args, **kwargs) # 执行实际的任务函数
            if not (media_processor_instance and media_processor_instance.is_stop_requested()):
                task_completed_normally = True
        except Exception as e:
            logger.error(f"后台任务 '{task_name}' 执行失败: {e}", exc_info=True)
            update_status_from_thread(-1, f"任务失败: {str(e)[:100]}...")
        finally:
            final_message_for_status = "未知结束状态"
            current_progress = background_task_status["progress"] # 获取当前进度

            if media_processor_instance and media_processor_instance.is_stop_requested():
                final_message_for_status = "任务已成功中断。"
            elif task_completed_normally:
                final_message_for_status = "处理完成。"
                current_progress = 100 # 正常完成则进度100%
            # else: 异常退出时，消息已在except中通过update_status_from_thread设置

            update_status_from_thread(current_progress, final_message_for_status)
            logger.info(f"后台任务 '{task_name}' 结束，最终状态: {final_message_for_status}")

            if media_processor_instance and hasattr(media_processor_instance, 'close'):
                logger.info(f"任务 '{task_name}' 结束 (finally块)，准备调用 media_processor_instance.close() ...")
                try:
                    media_processor_instance.close()
                    logger.debug(f"media_processor_instance.close() 调用完毕 (任务 '{task_name}' finally块)。")
                except Exception as e_close_proc:
                    logger.error(f"调用 media_processor_instance.close() 时发生错误: {e_close_proc}", exc_info=True)

            time.sleep(1) # 给前端一点时间抓取最终状态
            background_task_status["is_running"] = False
            background_task_status["current_action"] = "无"
            background_task_status["progress"] = 0
            background_task_status["message"] = "等待任务"
            if media_processor_instance:
                media_processor_instance.clear_stop_signal()
            logger.info(f"后台任务 '{task_name}' 状态已重置。")

# --- 定时任务 ---
def scheduled_task_job_internal(force_reprocess: bool):
    """定时任务的实际执行内容 (被 _execute_task_with_lock 调用)"""
    if media_processor_instance:
        media_processor_instance.process_full_library(
            update_status_callback=update_status_from_thread,
            force_reprocess_all=force_reprocess
        )
    else:
        logger.error("定时任务无法执行：MediaProcessor 未初始化。")

def scheduled_task_job_wrapper(force_reprocess: bool):
    """定时任务的包装器，用于被 APScheduler 调用"""
    task_name = "定时全量扫描"
    if force_reprocess: task_name += " (强制)"
    _execute_task_with_lock(scheduled_task_job_internal, task_name, force_reprocess)


def setup_scheduled_tasks():
    config = load_config()

    # --- 处理全量扫描的定时任务 ---
    schedule_scan_enabled = config.get("schedule_enabled", False) # <--- 从 config 获取
    scan_cron_expression = config.get("schedule_cron", "0 3 * * *")
    force_reprocess_scheduled_scan = config.get("schedule_force_reprocess", False)

    if scheduler.get_job(JOB_ID_FULL_SCAN):
        scheduler.remove_job(JOB_ID_FULL_SCAN)
        logger.info("已移除旧的定时全量扫描任务。")
    if schedule_scan_enabled: # <--- 使用这里定义的 schedule_scan_enabled
        try:
            scheduler.add_job(
                func=scheduled_task_job_wrapper,
                trigger=CronTrigger.from_crontab(scan_cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_FULL_SCAN, name="定时全量媒体库扫描", replace_existing=True,
                args=[force_reprocess_scheduled_scan]
            )
            logger.info(f"已设置定时全量扫描任务: CRON='{scan_cron_expression}', 强制={force_reprocess_scheduled_scan}")
        except Exception as e:
            logger.error(f"设置定时全量扫描任务失败: CRON='{scan_cron_expression}', 错误: {e}", exc_info=True)
    else:
        logger.info("定时全量扫描任务未启用。")

    # --- 处理同步人物映射表的定时任务 ---
    schedule_sync_map_enabled = config.get("schedule_sync_map_enabled", False)
    sync_map_cron_expression = config.get("schedule_sync_map_cron", "0 1 * * *")

    if scheduler.get_job(JOB_ID_SYNC_PERSON_MAP):
        scheduler.remove_job(JOB_ID_SYNC_PERSON_MAP)
        logger.info("已移除旧的定时同步人物映射表任务。")
    if schedule_sync_map_enabled:
        try:
            def scheduled_sync_map_wrapper(): # 这个包装器是正确的
                task_name = "定时同步Emby人物映射表"
                def sync_map_task_for_scheduler():
                    if media_processor_instance and media_processor_instance.emby_url and media_processor_instance.emby_api_key:
                        logger.info(f"'{task_name}' (定时): 准备创建 SyncHandler 实例...")
                        try:
                            # 假设 SyncHandler 在 core_processor.py 中定义，或者你已正确导入
                            from core_processor import SyncHandler # 或者 from sync_handler import SyncHandler
                            sync_handler_instance = SyncHandler(
                                db_path=DB_PATH, emby_url=media_processor_instance.emby_url,
                                emby_api_key=media_processor_instance.emby_api_key, emby_user_id=media_processor_instance.emby_user_id
                            )
                            logger.info(f"'{task_name}' (定时): SyncHandler 实例已创建。")
                            sync_handler_instance.sync_emby_person_map_to_db(update_status_callback=update_status_from_thread)
                        except NameError as ne: # SyncHandler 未定义
                            logger.error(f"'{task_name}' (定时) 无法执行：SyncHandler 类未定义或未导入。错误: {ne}", exc_info=True)
                            update_status_from_thread(-1, "错误：同步功能组件未找到 (定时)")
                        except Exception as e_sync_sched:
                            logger.error(f"'{task_name}' (定时) 执行过程中发生错误: {e_sync_sched}", exc_info=True)
                            update_status_from_thread(-1, f"错误：定时同步失败 ({str(e_sync_sched)[:50]}...)")
                    else:
                        logger.error(f"'{task_name}' (定时) 无法执行：MediaProcessor 未初始化或 Emby 配置不完整。")
                        update_status_from_thread(-1, "错误：核心处理器或Emby配置未就绪 (定时)")
                _execute_task_with_lock(sync_map_task_for_scheduler, task_name)

            scheduler.add_job(
                func=scheduled_sync_map_wrapper,
                trigger=CronTrigger.from_crontab(sync_map_cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_SYNC_PERSON_MAP, name="定时同步Emby人物映射表", replace_existing=True
            )
            logger.info(f"已设置定时同步人物映射表任务: CRON='{sync_map_cron_expression}'")
        except Exception as e:
            logger.error(f"设置定时同步人物映射表任务失败: CRON='{sync_map_cron_expression}', 错误: {e}", exc_info=True)
    else:
        logger.info("定时同步人物映射表任务未启用。")

    if scheduler.running:
        try: scheduler.print_jobs()
        except Exception as e_print_jobs: logger.warning(f"打印 APScheduler 任务列表时出错: {e_print_jobs}")
    if not scheduler.running and (schedule_scan_enabled or schedule_sync_map_enabled): # 修正这里的条件
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

def api_specific_sync_map_task(api_task_name: str): # <--- API 专属的，接收一个任务名参数
    logger.info(f"'{api_task_name}': API专属同步任务开始执行。")
    if media_processor_instance and \
       media_processor_instance.emby_url and \
       media_processor_instance.emby_api_key:
        try:
            sync_handler_instance = SyncHandler(
                db_path=DB_PATH,
                emby_url=media_processor_instance.emby_url,
                emby_api_key=media_processor_instance.emby_api_key,
                emby_user_id=media_processor_instance.emby_user_id
            )
            logger.info(f"'{api_task_name}': SyncHandler 实例已创建 (API)。")
            sync_handler_instance.sync_emby_person_map_to_db(
                update_status_callback=update_status_from_thread
            )
            logger.info(f"'{api_task_name}': 同步操作完成 (API)。")
        except NameError as ne:
             logger.error(f"'{api_task_name}' (API) 无法执行：SyncHandler 类未定义或未导入。错误: {ne}", exc_info=True)
             update_status_from_thread(-1, "错误：同步功能组件未找到")
        except Exception as e_sync:
            logger.error(f"'{api_task_name}' (API) 执行过程中发生严重错误: {e_sync}", exc_info=True)
            update_status_from_thread(-1, f"错误：同步失败 ({str(e_sync)[:50]}...)")
    else:
        logger.error(f"'{api_task_name}' (API) 无法执行：MediaProcessor 未初始化或 Emby 配置不完整。")
        update_status_from_thread(-1, "错误：核心处理器或Emby配置未就绪")

# --- webhook通知任务 ---
@app.route('/webhook/emby', methods=['POST'])
def emby_webhook():
    data = request.json
    event_type = data.get("Event") if data else "未知事件"
    logger.info(f"收到Emby Webhook: {event_type}")
    
    # 我们关心的事件列表
    trigger_events = ["item.add", "library.new"] 

    if event_type not in trigger_events:
        logger.info(f"Webhook事件 '{event_type}' 不在触发列表 {trigger_events} 中，将被忽略。")
        return jsonify({"status": "event_ignored_not_in_trigger_list"}), 200

    # ✨ 核心修改：不再关心事件类型，只要有Item ID就处理 ✨
    item = data.get("Item", {}) if data else {}
    item_id = item.get("Id")
    item_name = item.get("Name", "未知项目")
    item_type = item.get("Type")
    
    # 我们只处理有ID，并且类型是电影或剧集或单集的项目
    # 注意：即使是 library.new 事件，如果它由神医插件等聚合，也可能包含Item信息
    trigger_types = ["Movie", "Series", "Episode"]

    if item_id and item_type in trigger_types:
        logger.info(f"Webhook事件 '{event_type}' 触发，项目 '{item_name}' (ID: {item_id}, 类型: {item_type}) 已加入处理队列。")
        
        webhook_task_queue.put(item_id)
        start_webhook_worker_if_not_running()
        
        return jsonify({"status": "task_queued", "item_id": item_id}), 202
    
    logger.debug(f"Webhook事件 '{event_type}' (项目: {item_name}, 类型: {item_type}) 被忽略（缺少ItemID或类型不匹配）。")
    return jsonify({"status": "event_ignored_no_id_or_wrong_type"}), 200


@app.route('/trigger_full_scan', methods=['POST'])
def trigger_full_scan():
    force_reprocess = request.form.get('force_reprocess_all') == 'on'
    action_message = "全量媒体库扫描"
    if force_reprocess: action_message += " (强制重处理所有)"

    def full_scan_task_internal(force_flag): # 内部任务函数
        if media_processor_instance:
            media_processor_instance.process_full_library(
                update_status_callback=update_status_from_thread,
                force_reprocess_all=force_flag
            )
        else:
            logger.error("全量扫描无法执行：MediaProcessor 未初始化。")
            update_status_from_thread(-1, "错误：核心处理器未就绪")

    thread = threading.Thread(target=_execute_task_with_lock, args=(full_scan_task_internal, action_message, force_reprocess))
    thread.start()
    flash(f"{action_message}任务已在后台启动。", "info")
    return redirect(url_for('settings_page'))

@app.route('/trigger_sync_person_map', methods=['POST'])
def trigger_sync_person_map(): # WebUI 用的
    # ... (你的 if not media_processor_instance 和 if task_lock.locked() 检查逻辑不变) ...

    task_name = "同步Emby人物映射表 (WebUI)"
    logger.info(f"收到手动触发 '{task_name}' 的请求。")

    def sync_map_task_internal_for_webui(): # <--- 这是 WebUI 专属的内部任务函数
        logger.info(f"'{task_name}': WebUI专属同步任务开始执行。")
        if media_processor_instance and \
           media_processor_instance.emby_url and \
           media_processor_instance.emby_api_key:
            try:
                sync_handler_instance = SyncHandler(
                    db_path=DB_PATH,
                    emby_url=media_processor_instance.emby_url,
                    emby_api_key=media_processor_instance.emby_api_key,
                    emby_user_id=media_processor_instance.emby_user_id
                )
                logger.info(f"'{task_name}': SyncHandler 实例已创建 (WebUI)。")
                sync_handler_instance.sync_emby_person_map_to_db(
                    update_status_callback=update_status_from_thread
                )
                logger.info(f"'{task_name}': 同步操作完成 (WebUI)。")
            except NameError as ne:
                 logger.error(f"'{task_name}' (WebUI) 无法执行：SyncHandler 类未定义或未导入。错误: {ne}", exc_info=True)
                 update_status_from_thread(-1, "错误：同步功能组件未找到")
            except Exception as e_sync:
                logger.error(f"'{task_name}' (WebUI) 执行过程中发生严重错误: {e_sync}", exc_info=True)
                update_status_from_thread(-1, f"错误：同步失败 ({str(e_sync)[:50]}...)")
        else:
            logger.error(f"'{task_name}' (WebUI) 无法执行：MediaProcessor 未初始化或 Emby 配置不完整。")
            update_status_from_thread(-1, "错误：核心处理器或Emby配置未就绪")

    # WebUI 路由调用它自己的内部任务函数
    thread = threading.Thread(target=_execute_task_with_lock, args=(sync_map_task_internal_for_webui, task_name))
    thread.start()

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
# --- API 端点结束 ---


# --- 应用退出处理 ---
def application_exit_handler():
    global media_processor_instance, scheduler
    logger.info("应用程序正在退出 (atexit)，执行清理操作...")
    if media_processor_instance and hasattr(media_processor_instance, 'close'):
        logger.info("正在关闭 MediaProcessor 实例 (atexit)...")
        try: media_processor_instance.close()
        except Exception as e: logger.error(f"atexit 关闭 MediaProcessor 时出错: {e}", exc_info=True)
        else: logger.debug("MediaProcessor 实例已关闭 (atexit)。")

    if scheduler and scheduler.running:
        logger.info("正在关闭 APScheduler (atexit)...")
        try: scheduler.shutdown(wait=False) # wait=False 避免阻塞退出
        except Exception as e: logger.error(f"关闭 APScheduler 时发生错误 (atexit): {e}", exc_info=True)
        else: logger.debug("APScheduler 已关闭 (atexit)。")
    logger.info("atexit 清理操作执行完毕。")

    # ✨✨✨ 停止 Webhook 工人线程 ✨✨✨
    if webhook_worker_thread and webhook_worker_thread.is_alive():
        logger.info("正在发送停止信号给 Webhook 工人线程...")
        webhook_task_queue.put(None) # 发送“毒丸”
        webhook_worker_thread.join(timeout=5) # 等待线程退出，最多等5秒
        if webhook_worker_thread.is_alive():
            logger.warning("Webhook 工人线程在5秒内未能正常退出。")
        else:
            logger.info("Webhook 工人线程已成功停止。")

    logger.info("atexit 清理操作执行完毕。")

atexit.register(application_exit_handler)
logger.info("已注册应用程序退出处理程序 (atexit)。")
# --- 应用退出处理结束 ---

# --- API 端点 搜索媒体库 ---
@app.route('/api/search_emby_library', methods=['GET'])
def api_search_emby_library():
    query = request.args.get('query', '')
    if not query.strip():
        return jsonify({"error": "搜索词不能为空"}), 400

    if not media_processor_instance:
        return jsonify({"error": "核心处理器未就绪"}), 503

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
                "score": None
            })
        
        return jsonify({
            "items": formatted_results,
            "total_items": len(formatted_results)
        })

    except Exception as e:
        logger.error(f"API /api/search_emby_library Error: {e}", exc_info=True)
        return jsonify({"error": "搜索时发生未知服务器错误"}), 500

# --- API 端点：获取当前配置 ---
@app.route('/api/config', methods=['GET'])
def api_get_config():
    try:
        current_config = load_config() # 调用你实际的加载配置函数
        if current_config:
            logger.info(f"API /api/config (GET): 成功加载并返回配置。")
            return jsonify(current_config)
        else:
            logger.error(f"API /api/config (GET): load_config() 返回为空或None。")
            return jsonify({"error": "无法加载配置数据"}), 500
    except Exception as e:
        logger.error(f"API /api/config (GET) 获取配置时发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取配置信息时发生服务器内部错误"}), 500

# --- API 端点：保存配置 ---
@app.route('/api/config', methods=['POST'])
def api_save_config():
    try:
        new_config_data = request.json # 前端发送的是 JSON 数据
        if not new_config_data:
            logger.warning("API /api/config (POST): 未收到配置数据。")
            return jsonify({"error": "请求体中未包含配置数据"}), 400
        
        logger.info(f"API /api/config (POST): 收到新的配置数据，准备保存...")
        # logger.debug(f"收到的原始配置数据: {new_config_data}") # 可以取消注释用于调试

        # 调用你实际的 save_config 函数
        # save_config 函数内部应该处理数据转换、写入文件、
        # 以及调用 initialize_media_processor() 和 setup_scheduled_tasks()
        save_config(new_config_data) 
        
        logger.info("API /api/config (POST): 配置已成功传递给 save_config 函数。")
        return jsonify({"message": "配置已成功保存并已触发重新加载。"}) # 返回成功消息
        
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
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # --- 终极简化：移除所有 error_message 筛选 ---
        # 既然 failed_log 表中的所有记录都需要复核，我们就不需要任何关键词过滤了。
        
        where_clause = ""
        sql_params = []

        # 只在用户输入搜索词时才添加 WHERE 条件
        if query_filter:
            where_clause = "WHERE item_name LIKE ?"
            sql_params.append(f"%{query_filter}%")
        # --- 简化结束 ---

        # 1. 查询总数 (WHERE 条件要么为空，要么是按名称搜索)
        count_sql = f"SELECT COUNT(*) FROM failed_log {where_clause}"
        cursor.execute(count_sql, tuple(sql_params))
        count_row = cursor.fetchone()
        if count_row:
            total_matching_items = count_row[0]

        # --- 在这里加上确认日志 ---
        print(f"DATABASE CHECK: Found a total of {total_matching_items} items.")
        logger.info(f"DATABASE CHECK: Found a total of {total_matching_items} items.")
        # --- -------------------- ---

        # 2. 查询当前页的数据
        items_sql = f"""
            SELECT item_id, item_name, failed_at, error_message, item_type, score 
            FROM failed_log 
            {where_clause}
            ORDER BY failed_at DESC 
            LIMIT ? OFFSET ?
        """
        # 将分页参数添加到参数列表
        params_for_page_query = sql_params + [per_page, offset]
        cursor.execute(items_sql, tuple(params_for_page_query))
        fetched_rows = cursor.fetchall()
        
        for row in fetched_rows:
            items_to_review.append(dict(row))

    except Exception as e:
        logger.error(f"API /api/review_items 获取数据失败: {e}", exc_info=True)
        return jsonify({"error": "获取待复核列表时发生服务器内部错误"}), 500
    finally:
        if conn:
            conn.close()
            
    total_pages = (total_matching_items + per_page - 1) // per_page if total_matching_items > 0 else 0
    
    logger.info(f"API /api/review_items: 返回 {len(items_to_review)} 条待复核项目 (总计: {total_matching_items}, 第 {page}/{total_pages} 页)")
    return jsonify({
        "items": items_to_review,
        "total_items": total_matching_items,
        "total_pages": total_pages,
        "current_page": page,
        "per_page": per_page,
        "query": query_filter
    })
    
@app.route('/api/actions/reprocess_item/<item_id>', methods=['POST'])
def api_reprocess_item(item_id):
    if not media_processor_instance:
        return jsonify({"error": "核心处理器未就绪"}), 503
    
    # 最好在一个新线程中执行，避免阻塞API请求
    # 但 process_single_item 内部可能已经有自己的线程管理或快速返回
    # 如果 process_single_item 是耗时的，应该用 _execute_task_with_lock
    
    # 假设 process_single_item 是可以同步调用的（如果它内部处理了耗时操作）
    # 或者我们在这里只触发一个后台任务
    logger.info(f"API: 收到重新处理项目 {item_id} 的请求。")
    
    def reprocess_task_internal(id_to_process):
        success = media_processor_instance.process_single_item(id_to_process, force_reprocess_this_item=True)
        if success:
            # 尝试从 failed_log 移除 (如果 process_single_item 没处理)
            # 通常 process_single_item 会根据新评分决定是否移除或更新
            logger.info(f"项目 {id_to_process} 重新处理任务完成。")
        else:
            logger.warning(f"项目 {id_to_process} 重新处理任务可能未完全成功。")

    # 使用 _execute_task_with_lock 来管理这个单一任务
    # 注意：_execute_task_with_lock 通常用于长时间运行的任务，
    # 如果 process_single_item 很快，直接调用也可以，但要处理好状态。
    # 为了一致性，我们还是用它，但任务名可以更具体。
    thread = threading.Thread(target=_execute_task_with_lock, 
                              args=(reprocess_task_internal, f"重新处理项目: {item_id}", item_id))
    thread.start()
    
    return jsonify({"message": f"项目 {item_id} 已提交重新处理。"}), 202

@app.route('/api/actions/mark_item_processed/<item_id>', methods=['POST'])
def api_mark_item_processed(item_id):
    if task_lock.locked():
        return jsonify({"error": "后台有长时间任务正在运行，请稍后再试。"}), 409
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 从 failed_log 获取信息，以便存入 processed_log
        cursor.execute("SELECT item_name, item_type, score FROM failed_log WHERE item_id = ?", (item_id,))
        failed_item_info = cursor.fetchone()
        
        cursor.execute("DELETE FROM failed_log WHERE item_id = ?", (item_id,))
        deleted_count = cursor.rowcount
        
        if deleted_count > 0 and failed_item_info:
            # 标记为已处理时，可以给一个特殊的高分或标记
            # 或者，如果原始评分存在，就用那个评分
            score_to_save = failed_item_info["score"] if failed_item_info["score"] is not None else 10.0 # 假设手动处理给10分
            item_name = failed_item_info["item_name"]
            
            # 添加到 processed_log
            # 假设 MediaProcessor 实例在这里不可用，我们直接操作数据库
            # 如果 MediaProcessor 可用，调用其 save_to_processed_log 方法更好
            cursor.execute(
                "REPLACE INTO processed_log (item_id, item_name, processed_at, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                (item_id, item_name, score_to_save)
            )
            logger.info(f"项目 {item_id} ('{item_name}') 已标记为已处理，并移至已处理日志 (评分: {score_to_save})。")

        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            return jsonify({"message": f"项目 {item_id} 已成功标记为已处理。"}), 200
        else:
            return jsonify({"error": f"未在待复核列表中找到项目 {item_id}。"}), 404
    except Exception as e:
        logger.error(f"标记项目 {item_id} 为已处理时失败: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    
@app.route('/api/trigger_full_scan', methods=['POST'])
@app.route('/api/trigger_full_scan', methods=['POST'])
def api_handle_trigger_full_scan():
    logger.info("API Endpoint: Received request to trigger full scan.")
    try:
        # 只从前端获取“是否强制”这一个临时选项
        force_reprocess = request.form.get('force_reprocess_all') == 'on'
        
        # “处理深度”将由后台任务自己去读取配置，不再从这里传递
        
        logger.info(f"API /api/trigger_full_scan: 强制重处理={force_reprocess}")
        
        action_message = "全量媒体库扫描"
        if force_reprocess: action_message += " (强制)"
        # 任务名称里不再体现深度，因为它是由配置决定的

        def task_to_run():
            """这个嵌套函数会在后台执行"""
            if media_processor_instance:
                # ✨ 在任务执行时，才去读取配置 ✨
                config = load_config()
                process_episodes = config.get('process_episodes', True) # 默认为True
                logger.info(f"全量扫描任务：根据配置，处理分集开关为: {process_episodes}")

                media_processor_instance.process_full_library(
                    update_status_callback=update_status_from_thread,
                    force_reprocess_all=force_reprocess,
                    process_episodes=process_episodes
                )
            else:
                logger.error("API: 全量扫描无法执行：MediaProcessor 未初始化。")
                update_status_from_thread(-1, "错误：核心处理器未就绪")

        thread = threading.Thread(
            target=_execute_task_with_lock,
            args=(task_to_run, action_message)
        )
        thread.start()
        
        return jsonify({"message": f"{action_message} 任务已提交启动。"}), 202
    except Exception as e:
        logger.error(f"API /api/trigger_full_scan error: {e}", exc_info=True)
        return jsonify({"error": "启动全量扫描时发生服务器内部错误"}), 500

@app.route('/api/trigger_sync_person_map', methods=['POST'])
def api_handle_trigger_sync_map():
    logger.info("API Endpoint: Received request to trigger sync person map.")
    try:
        if task_lock.locked():
            logger.warning("API /api/trigger_sync_person_map: Task lock is active.")
            return jsonify({"error": "已有其他后台任务正在运行，请稍后再试。"}), 409

        task_name_for_api = "同步Emby人物映射表 (API)" # API 调用的任务名

        # API 路由调用它专属的后台任务函数
        # 假设 _execute_task_with_lock 的第二个参数是任务描述，第三个是传递给目标函数的第一个参数
        thread = threading.Thread(target=_execute_task_with_lock, args=(api_specific_sync_map_task, task_name_for_api, task_name_for_api))
        thread.start()

        return jsonify({"message": f"'{task_name_for_api}' 任务已提交启动。"}), 202
    except Exception as e:
        logger.error(f"API /api/trigger_sync_person_map error: {e}", exc_info=True)
        return jsonify({"error": "启动同步映射表时发生服务器内部错误"}), 500

@app.route('/api/trigger_stop_task', methods=['POST'])
def api_handle_trigger_stop_task():
    logger.info("API Endpoint: Received request to stop current task.")
    try:
        # (调用你实际停止任务的逻辑，例如设置一个全局的停止事件)
        # global_stop_event.set() # 假设你有这样一个事件
        if media_processor_instance and hasattr(media_processor_instance, 'signal_stop'):
            media_processor_instance.signal_stop()
            logger.info("API: 已发送停止信号给 MediaProcessor。")
            # 你可能还需要停止其他类型的任务，例如同步任务
            # 这取决于你的 _execute_task_with_lock 如何处理停止信号
            # 或者你有一个更通用的任务管理器来发送停止信号
        else:
            logger.warning("API: MediaProcessor 未初始化，无法发送停止信号。")

        return jsonify({"message": "已发送停止任务请求。"}), 200
    except Exception as e:
        logger.error(f"API /api/trigger_stop_task error: {e}", exc_info=True)
        return jsonify({"error": "发送停止任务请求时发生服务器内部错误"}), 500
    
# @app.route('/api/media_with_cast_for_editing/<item_id>', methods=['GET'])
# def api_get_media_for_editing(item_id):
#     logger.info(f"API: Received request to get media details for editing, ItemID: {item_id}")

#     if not media_processor_instance:
#         logger.error("API: MediaProcessor 未初始化，无法获取媒体详情。")
#         return jsonify({"error": "核心处理器未就绪"}), 503

#     # 1. 从 Emby 获取详情
#     emby_details = None
#     try: # <--- Python 的 try:
#         emby_details = emby_handler.get_emby_item_details(
#             item_id,
#             media_processor_instance.emby_url,
#             media_processor_instance.emby_api_key,
#             media_processor_instance.emby_user_id
#         )
#     except Exception as e: # <--- Python 的 except Exception as e:
#         logger.error(f"API: 调用 emby_handler.get_emby_item_details 失败 for ItemID {item_id}: {e}", exc_info=True)
#         # 即使这里失败，我们仍然尝试从 failed_log 获取信息，或者至少返回一个部分错误
    
#     # ... 后续从 failed_log 获取信息、组合数据等逻辑保持不变 ...
#     # (确保那部分代码的 try...except...finally 也是 Python 语法)

#     if not emby_details:
#         logger.warning(f"API: 无法从 Emby 获取项目 {item_id} 的详情。可能项目不存在或Emby API问题。")

#     # 2. 从 failed_log 获取信息 (这部分你之前的代码是正确的 Python try-except-finally)
#     conn = None
#     failed_log_info = {}
#     try:
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         cursor.execute("SELECT item_name, item_type, error_message, score FROM failed_log WHERE item_id = ?", (item_id,))
#         row = cursor.fetchone()
#         if row:
#             failed_log_info = dict(row)
#             logger.debug(f"API: 从 failed_log 找到项目 {item_id} 的信息: {failed_log_info}")
#         else:
#             logger.warning(f"API: 项目 {item_id} 在 failed_log 中未找到。")
#             if not emby_details:
#                  return jsonify({"error": f"项目 {item_id} 在 Emby 和待复核日志中均未找到"}), 404
#     except Exception as e:
#         logger.error(f"API: 查询 failed_log 获取项目 {item_id} 信息失败: {e}", exc_info=True)
#         if not emby_details:
#             return jsonify({"error": f"获取项目 {item_id} 信息时发生数据库错误，且无法从Emby获取详情"}), 500
#     finally:
#         if conn:
#             conn.close()

#     # 3. 提取并格式化 Emby 演员列表 (这部分你之前的代码是正确的 Python 逻辑)
#     current_cast_for_frontend = []
#     # ... (这部分代码不变) ...
#     if emby_details and emby_details.get("People"):
#         for person in emby_details.get("People", []):
#             if person.get("Id") and person.get("Name"): # 必须有 Emby Person ID 和名字
#                 provider_ids = person.get("ProviderIds", {})
#                 current_cast_for_frontend.append({
#                     "embyPersonId": str(person["Id"]),
#                     "name": person["Name"],
#                     "role": person.get("Role", ""),
#                     "imdbId": provider_ids.get("Imdb"),
#                     "doubanId": provider_ids.get("Douban"),
#                     "tmdbId": provider_ids.get("Tmdb")
#                 })
#     elif emby_details: 
#         logger.info(f"API: Emby 项目 {item_id} 详情中没有 People (演员) 信息。")


#     # 4. 组合最终的响应数据 (这部分你之前的代码是正确的 Python 逻辑)
#     # ... (这部分代码不变) ...
#     final_item_name = failed_log_info.get('item_name')
#     if not final_item_name and emby_details:
#         final_item_name = emby_details.get('Name')
    
#     final_item_type = failed_log_info.get('item_type')
#     if not final_item_type and emby_details:
#         final_item_type = emby_details.get('Type')

#     response_data = {
#         "item_id": item_id,
#         "item_name": final_item_name or f"未知名称 (ID: {item_id})",
#         "item_type": final_item_type or "未知类型",
#         "original_score": failed_log_info.get('score'),
#         "review_reason": failed_log_info.get('error_message'),
#         "current_emby_cast": current_cast_for_frontend
#     }
    
#     logger.info(f"API: 成功为项目 {item_id} 构建编辑详情，准备返回。演员数: {len(current_cast_for_frontend)}")
#     return jsonify(response_data)
#--- 从豆瓣刷新演员表 ---
@app.route('/api/preview_processed_cast/<item_id>', methods=['POST'])
def api_preview_processed_cast(item_id):
    """
    一个轻量级的API，用于预览单个媒体项经过核心处理器处理后的演员列表。
    它只返回处理结果，不执行任何数据库更新或Emby更新。
    """
    if not media_processor_instance:
        return jsonify({"error": "核心处理器未就绪"}), 503

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
    
@app.route('/api/update_media_cast/<item_id>', methods=['POST'])
def api_update_edited_cast(item_id):
    if not media_processor_instance:
        return jsonify({"error": "核心处理器未就绪"}), 503
    
    try:
        data = request.json
        if not data or "cast" not in data or not isinstance(data["cast"], list):
            return jsonify({"error": "请求体中缺少有效的 'cast' 列表"}), 400

        edited_cast_from_frontend = data["cast"]
        logger.info(f"API: 收到为 ItemID {item_id} 更新演员的请求，共 {len(edited_cast_from_frontend)} 位演员。")

        # ======================================================================
        # ✨✨✨ 逻辑变更：不再自己处理，而是交给 core_processor ✨✨✨
        # ======================================================================

        # 1. 更新翻译缓存 (这部分逻辑可以保留在 API 层)
        # (您现有的更新翻译缓存的代码可以放在这里，我暂时省略以保持清晰)
        # ... 您更新翻译缓存的代码 ...
        
        # 2. 准备传递给核心处理器的数据 (emby_handler 期望的格式)
        cast_for_processor = []
        for actor_frontend in edited_cast_from_frontend:
            entry = {
                "emby_person_id": str(actor_frontend.get("embyPersonId", "")).strip(),
                "name": str(actor_frontend.get("name", "")).strip(),
                "character": str(actor_frontend.get("role", "")).strip(),
                "provider_ids": {} # 您可以根据需要填充这个
            }
            cast_for_processor.append(entry)

        # 3. 调用新的核心处理函数
        process_success = media_processor_instance.process_item_with_manual_cast(
            item_id=item_id,
            manual_cast_list=cast_for_processor
        )

        # 4. 根据处理结果返回响应
        if process_success:
            logger.info(f"API: ItemID {item_id} 的手动更新流程已成功执行。")
            # 更新成功后，也应该更新处理日志
            item_name_for_log = data.get("item_name", f"未知项目(ID:{item_id})")
            media_processor_instance._remove_from_failed_log_if_exists(item_id)
            media_processor_instance.save_to_processed_log(item_id, item_name_for_log, score=10.0) # 手动处理给满分
            return jsonify({"message": "演员信息已成功更新，并执行了完整的处理流程。"}), 200
        else:
            logger.error(f"API: 核心处理器未能成功处理 ItemID {item_id} 的手动更新。")
            return jsonify({"error": "核心处理器执行手动更新失败，请检查后端日志。"}), 500

    except Exception as e:
        logger.error(f"API /api/update_media_cast CRITICAL Error for {item_id}: {e}", exc_info=True)
        return jsonify({"error": "保存演员信息时发生服务器内部错误"}), 500
    
@app.route('/api/export_person_map', methods=['GET'])
def api_export_person_map():
    """
    导出 person_identity_map 表为 CSV 文件。
    """
    logger.info("API: 收到导出人物映射表的请求。")
    
    def generate_csv():
        # 使用 StringIO 作为内存中的文件缓冲区
        string_io = StringIO()
        
        # 获取数据库连接
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # 定义CSV文件的表头
            # 我们只导出核心ID，名字可以由程序自动关联，减少文件大小和复杂性
            headers = [
                'emby_person_id', 
                'imdb_id', 
                'tmdb_person_id', 
                'douban_celebrity_id',
                'emby_person_name' # 包含名字方便用户查看
            ]
            
            # 创建CSV写入器
            writer = csv.DictWriter(string_io, fieldnames=headers)
            
            # 写入表头
            writer.writeheader()
            yield string_io.getvalue() # 流式返回表头
            string_io.seek(0)
            string_io.truncate(0)

            # 查询所有映射数据
            cursor.execute(f"SELECT {', '.join(headers)} FROM person_identity_map")
            
            # 逐行写入CSV
            for row in cursor:
                writer.writerow(dict(row))
                yield string_io.getvalue() # 流式返回每一行
                string_io.seek(0)
                string_io.truncate(0)

        except Exception as e:
            logger.error(f"导出映射表时发生错误: {e}", exc_info=True)
            # 如果出错，可以考虑返回一个错误信息的文本
            yield f"Error exporting data: {e}"
        finally:
            if conn:
                conn.close()

    # 创建一个文件名，包含日期
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"person_identity_map_backup_{timestamp}.csv"
    
    # 使用 Response 对象来设置HTTP头，告诉浏览器这是一个需要下载的文件
    response = Response(stream_with_context(generate_csv()), mimetype='text/csv')
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    
    return response


@app.route('/api/import_person_map', methods=['POST'])
def api_import_person_map():
    """
    从上传的 CSV 文件导入数据到 person_identity_map 表。
    """
    logger.info("API: 收到导入人物映射表的请求。")
    
    # 检查是否有文件被上传
    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    if file and file.filename.endswith('.csv'):
        try:
            # 读取文件内容
            # 为了处理不同编码，最好先解码
            stream = StringIO(file.stream.read().decode("utf-8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            # 准备统计数据
            stats = {"total": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
            
            conn = get_db_connection()
            cursor = conn.cursor()

            for row in csv_reader:
                stats["total"] += 1
                emby_pid = row.get('emby_person_id')
                
                if not emby_pid:
                    stats["skipped"] += 1
                    continue
                
                # 使用 REPLACE INTO (UPSERT) 逻辑来插入或更新
                # 这会根据 PRIMARY KEY 或 UNIQUE 约束来工作
                # 确保你的 emby_person_id 是 UNIQUE 的
                sql = """
                    REPLACE INTO person_identity_map (
                        emby_person_id, emby_person_name, imdb_id, 
                        tmdb_person_id, douban_celebrity_id, last_updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """
                params = (
                    emby_pid,
                    row.get('emby_person_name'),
                    row.get('imdb_id'),
                    row.get('tmdb_person_id'),
                    row.get('douban_celebrity_id')
                )
                
                try:
                    cursor.execute(sql, params)
                    # cursor.rowcount 在 REPLACE 语句中，如果是插入则为1，如果是替换则为2
                    if cursor.rowcount > 0:
                        # 简单起见，我们不区分插入和更新，都算成功
                        stats["updated"] += 1 # 或者你可以用更复杂的逻辑来区分
                except Exception as e_row:
                    stats["errors"] += 1
                    logger.error(f"导入行 {row} 时出错: {e_row}")

            conn.commit()
            conn.close()
            
            message = f"导入完成。总行数: {stats['total']}, 成功处理: {stats['updated']}, 跳过: {stats['skipped']}, 错误: {stats['errors']}."
            logger.info(f"API: {message}")
            return jsonify({"message": message}), 200

        except Exception as e:
            logger.error(f"导入文件时发生严重错误: {e}", exc_info=True)
            return jsonify({"error": f"处理文件时发生错误: {e}"}), 500
    else:
        return jsonify({"error": "无效的文件类型，请上传 .csv 文件"}), 400  
# ✨✨✨ 加载编辑页面的API接口 ✨✨✨
@app.route('/api/media_with_cast_for_editing/<item_id>', methods=['GET'])
def api_get_media_for_editing(item_id):
    logger.info(f"API: 收到为 ItemID {item_id} 获取编辑详情的请求。")

    if not media_processor_instance:
        return jsonify({"error": "核心处理器未就绪"}), 503

    # ... (前面的代码，如获取 Emby 详情、数据库信息、演员列表等，保持不变) ...
    # 1. 从 Emby 获取详情 (保持不变)
    try:
        emby_details = emby_handler.get_emby_item_details(
            item_id,
            media_processor_instance.emby_url,
            media_processor_instance.emby_api_key,
            media_processor_instance.emby_user_id
        )
        if not emby_details:
            return jsonify({"error": f"在Emby中未找到项目 {item_id}"}), 404
    except Exception as e:
        logger.error(f"API: 获取Emby详情失败 for ItemID {item_id}: {e}", exc_info=True)
        return jsonify({"error": "获取Emby详情时发生服务器错误"}), 500

    # 2. 从 failed_log 获取额外信息 (保持不变)
    conn = None
    failed_log_info = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT item_name, item_type, error_message, score FROM failed_log WHERE item_id = ?", (item_id,))
        row = cursor.fetchone()
        if row:
            failed_log_info = dict(row)
    finally:
        if conn:
            conn.close()

    # 3. 格式化演员列表 (保持不变)
    current_cast_for_frontend = []
    if emby_details.get("People"):
        for person in emby_details.get("People", []):
            if person.get("Id") and person.get("Name"):
                provider_ids = person.get("ProviderIds", {})
                current_cast_for_frontend.append({
                    "embyPersonId": str(person["Id"]),
                    "name": person["Name"],
                    "role": person.get("Role", ""),
                    "imdbId": provider_ids.get("Imdb"),
                    "doubanId": provider_ids.get("Douban"),
                    "tmdbId": provider_ids.get("Tmdb")
                })

    # ✨✨✨ [关键修改] 4. 增强生成搜索链接的日志 ✨✨✨
    google_search_for_wiki_url = None
    title = emby_details.get("Name")
    year = emby_details.get("ProductionYear")
    
    if title:
        logger.info(f"准备为标题 '{title}' (年份: {year}) 生成搜索链接。")
        try:
            # ✨✨✨ [关键修复] 使用 utils. 前缀来调用函数 ✨✨✨
            google_search_for_wiki_url = utils.generate_search_url('wikipedia', title, year)
            
            if google_search_for_wiki_url:
                logger.info(f"成功生成搜索链接: {google_search_for_wiki_url}")
            else:
                logger.warning(f"generate_search_url 函数为标题 '{title}' 返回了一个空值或 None。")
        except Exception as e:
            logger.error(f"为 '{title}' 生成维基百科搜索链接时发生异常: {e}", exc_info=True)
    else:
        logger.warning("无法生成搜索链接，因为 Emby 详情中缺少标题 (Name)。")

    # 5. 组合最终的响应数据 (保持不变)
    response_data = {
        "item_id": item_id,
        "item_name": emby_details.get("Name"),
        "item_type": emby_details.get("Type"),
        "original_score": failed_log_info.get("score"),
        "review_reason": failed_log_info.get("error_message"),
        "current_emby_cast": current_cast_for_frontend,
        "search_links": {
            "google_search_wiki": google_search_for_wiki_url
        }
    }
    
    logger.info(f"API: 成功为项目 {item_id} 构建编辑详情，准备返回。")
    return jsonify(response_data)

# ✨✨✨   生成外部搜索链接 ✨✨✨
@app.route('/api/parse_cast_from_url', methods=['POST'])
def api_parse_cast_from_url():
    if not WEB_PARSER_AVAILABLE:
        return jsonify({"error": "网页解析功能在服务器端不可用。"}), 501

    data = request.json
    url_to_parse = data.get('url')

    if not url_to_parse:
        return jsonify({"error": "请求中未提供 'url' 参数"}), 400

    logger.info(f"API: 收到从 URL 解析演员表的请求: {url_to_parse}")

    try:
        # 加载配置并准备请求头
        current_config = load_config()
        user_agent = current_config.get('user_agent')
        accept_language = current_config.get('accept_language')
        
        custom_headers = {}
        if user_agent:
            custom_headers['User-Agent'] = user_agent
        if accept_language:
            custom_headers['Accept-Language'] = accept_language
        
        # 将请求头传递给解析器
        parsed_cast = parse_cast_from_url(url_to_parse, custom_headers=custom_headers)
        
        if parsed_cast:
            frontend_cast = [{"name": item['actor'], "role": item['character']} for item in parsed_cast]
            return jsonify(frontend_cast)
        else:
            return jsonify({"message": "未找到有效的演员信息，请检查URL或网页内容。"}), 200

    except ParserError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"解析 URL '{url_to_parse}' 时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": "解析时发生未知的服务器错误"}), 500
# ✨✨✨ 更新演员表 ✨✨✨
@app.route('/api/actions/enrich_cast_list', methods=['POST'])
def api_enrich_cast_list():
    if not media_processor_instance:
        return jsonify({"error": "核心处理器未就绪"}), 503
        
    data = request.json
    current_cast = data.get('current_cast')
    new_cast_from_web = data.get('new_cast_from_web')

    if not isinstance(current_cast, list) or not isinstance(new_cast_from_web, list):
        return jsonify({"error": "请求体必须包含 'current_cast' 和 'new_cast_from_web' 两个列表。"}), 400

    try:
        # 调用新的、更安全的核心方法
        enriched_list = media_processor_instance.enrich_cast_list(current_cast, new_cast_from_web)
        
        return jsonify(enriched_list)
        
    except Exception as e:
        logger.error(f"丰富演员列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在丰富演员列表时发生内部错误。"}), 500
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
    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}, 调试模式: {constants.DEBUG_MODE}")
    init_db()
    initialize_media_processor()
    # ✨✨✨ 新增：应用启动时就启动工人线程 ✨✨✨
    start_webhook_worker_if_not_running()
    if not scheduler.running:
        try:
            scheduler.start()
            logger.info("APScheduler 已启动。")
        except Exception as e_scheduler_start:
            logger.error(f"APScheduler 启动失败: {e_scheduler_start}", exc_info=True)
    setup_scheduled_tasks()
    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=constants.DEBUG_MODE)

# if __name__ == '__main__':
#     RUN_MANUAL_TESTS = False  # <--- 在这里控制是否运行测试代码

#     logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}, 调试模式: {constants.DEBUG_MODE}")
#     init_db()
#     initialize_media_processor() # 确保 media_processor_instance 被创建并配置好
#     # ... (scheduler setup) ...

#     # --- !!! 测试 _process_cast_list 方法 !!! ---
#     if RUN_MANUAL_TESTS and media_processor_instance and media_processor_instance.emby_url:
#         TEST_MEDIA_ID_TO_PROCESS = "464188" # 测试电视剧《白蛇传》
        
#         logger.info(f"--- 开始手动测试 _process_cast_list for MEDIA ID: {TEST_MEDIA_ID_TO_PROCESS} ---")

#         raw_media_item_details = None # <--- 修改变量名
#         try:
#             raw_media_item_details = emby_handler.get_emby_item_details( # <--- 修改变量名
#                 TEST_MEDIA_ID_TO_PROCESS,
#                 media_processor_instance.emby_url,
#                 media_processor_instance.emby_api_key,
#                 media_processor_instance.emby_user_id
#             )
#         except Exception as e_get_raw:
#             logger.error(f"测试：获取原始 MEDIA 详情失败 (ID: {TEST_MEDIA_ID_TO_PROCESS}): {e_get_raw}", exc_info=True)
        
#         # 打印获取到的原始详情，用于调试
#         if raw_media_item_details:
#             logger.info(f"DEBUG_DETAILS: 原始获取到的 raw_media_item_details 内容 (部分键): {{'Name': '{raw_media_item_details.get('Name')}', 'Type': '{raw_media_item_details.get('Type')}', 'Id': '{raw_media_item_details.get('Id')}', 'HasPeopleField': {'People' in raw_media_item_details}, 'PeopleFieldType': {type(raw_media_item_details.get('People')).__name__ if 'People' in raw_media_item_details else 'N/A'} }}")
#             # 如果想看完整内容，取消下面这行的注释，但可能会很长
#             # logger.info(f"DEBUG_DETAILS_FULL: {raw_media_item_details}")
#         else:
#             logger.error("DEBUG_DETAILS: raw_media_item_details 为 None，获取失败。")

#         # --- 核心判断逻辑 ---
#         # 检查 raw_media_item_details 是否有效，并且 People 字段是否存在且是一个列表
#         if raw_media_item_details and isinstance(raw_media_item_details.get("People"), list):
#             original_emby_people = raw_media_item_details.get("People", []) # 如果People键不存在，默认为空列表
            
#             # 即使 People 列表存在但为空，也应该继续，让 _process_cast_list 尝试从豆瓣补充
#             logger.info(f"测试：获取到 MEDIA '{raw_media_item_details.get('Name')}' 的原始 People 列表，数量: {len(original_emby_people)}")
#             if original_emby_people: # 只在列表非空时打印前3条
#                 logger.debug(f"测试：原始 People (前3条): {original_emby_people[:3]}")

#             logger.info(f"测试：准备调用 media_processor_instance._process_cast_list...")
#             try:
#                 final_cast_list = media_processor_instance._process_cast_list(
#                     original_emby_people,
#                     raw_media_item_details # <--- 修改变量名
#                 )
#                 # ... (打印 final_cast_list) ...
#             except Exception as e_proc_cast:
#                 logger.error(f"测试：调用 _process_cast_list 时发生错误: {e_proc_cast}", exc_info=True)
        
#         else: # raw_media_item_details 无效，或者 People 字段不存在/不是列表
#             logger.error(f"测试：未能获取 MEDIA {TEST_MEDIA_ID_TO_PROCESS} 的原始详情，或详情中无有效People列表，无法继续测试 _process_cast_list。")

#         logger.info(f"--- 手动测试 _process_cast_list 结束 ---")
#     # # --- 测试代码结束 --- #

#     # app.run(...) # 你可以暂时注释掉 app.run，这样脚本执行完测试就结束了，方便看日志
#     # 或者保留它，测试完后再通过浏览器访问应用

#     app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=constants.DEBUG_MODE, use_reloader=not constants.DEBUG_MODE)
#     # 注意: debug=True 配合 use_reloader=True (Flask默认) 会导致 atexit 执行两次或行为异常。
#     # 在生产中，use_reloader 应为 False。为了开发方便，可以暂时接受 atexit 的一些小问题。
#     # 或者在 debug 模式下，考虑不依赖 atexit，而是通过其他方式（如信号处理）来触发清理。
#     # 最简单的是，开发时接受它，部署时确保 use_reloader=False。
# # --- 主程序入口结束 ---