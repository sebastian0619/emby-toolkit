# web_app.py
import os
import sqlite3
import emby_handler
import configparser
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import threading
import time
from typing import Optional, Dict, Any, List # 确保 List 被导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
import atexit # 用于应用退出处理
from core_processor import MediaProcessor, SyncHandler

# --- 核心模块导入 ---
import constants # 你的常量定义
from core_processor import MediaProcessor # 核心处理逻辑
from logger_setup import logger # 日志记录器
# emby_handler 和 utils 会在需要的地方被 core_processor 或此文件中的函数调用
# 如果直接在此文件中使用它们的功能，也需要在这里导入
import utils       # 例如，用于 /api/search_media
# from douban import DoubanApi # 通常不需要在 web_app.py 直接导入 DoubanApi，由 MediaProcessor 管理
# --- 核心模块导入结束 ---


app = Flask(__name__)
app.secret_key = os.urandom(24) # 用于 flash 消息等

# --- 路径和配置定义 ---
APP_DATA_DIR_ENV = os.environ.get("APP_DATA_DIR")
JOB_ID_SYNC_PERSON_MAP = "scheduled_sync_person_map"

if APP_DATA_DIR_ENV: # 如果设置了环境变量 (例如在 Dockerfile 中)
    PERSISTENT_DATA_PATH = APP_DATA_DIR_ENV
    logger.info(f"使用环境变量 APP_DATA_DIR 指定的持久化路径: {PERSISTENT_DATA_PATH}")
else:
    # 本地开发环境：在项目根目录下创建一个名为 'local_data' 的文件夹
    # BASE_DIR 通常是 web_app.py 所在的目录
    BASE_DIR_FOR_DATA = os.path.dirname(os.path.abspath(__file__)) # web_app.py 所在目录
    PERSISTENT_DATA_PATH = os.path.join(BASE_DIR_FOR_DATA, "local_data")
    logger.info(f"未检测到 APP_DATA_DIR 环境变量，将使用本地开发数据路径: {PERSISTENT_DATA_PATH}")
# --- 路径和配置定义结束 ---

try:
    if not os.path.exists(PERSISTENT_DATA_PATH):
        os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
        logger.info(f"持久化数据目录已创建/确认: {PERSISTENT_DATA_PATH}")
except OSError as e:
    logger.error(f"创建持久化数据目录 '{PERSISTENT_DATA_PATH}' 失败: {e}。程序可能无法正常读写配置文件和数据库。")
    # 在这种情况下，程序可能无法继续，可以考虑退出或抛出异常
    # raise RuntimeError(f"无法创建必要的数据目录: {PERSISTENT_DATA_PATH}") from e

# --- 后续所有 CONFIG_FILE_PATH 和 DB_PATH 都基于这个 PERSISTENT_DATA_PATH ---
CONFIG_FILE_NAME = getattr(constants, 'CONFIG_FILE_NAME', "config.ini") # 从常量获取或默认
CONFIG_FILE_PATH = os.path.join(PERSISTENT_DATA_PATH, constants.CONFIG_FILE_NAME)

DB_NAME = getattr(constants, 'DB_NAME', "emby_actor_processor.sqlite") # 从常量获取或默认
DB_PATH = os.path.join(PERSISTENT_DATA_PATH, constants.DB_NAME)


# --- 全局变量 ---
media_processor_instance: Optional[MediaProcessor] = None
background_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务"
}
task_lock = threading.Lock() # 用于确保后台任务串行执行

scheduler = BackgroundScheduler(timezone=str(pytz.timezone(constants.TIMEZONE)))
JOB_ID_FULL_SCAN = "scheduled_full_scan"
# --- 全局变量结束 ---

# --- 数据库辅助函数 ---
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # 方便按列名访问数据
    return conn

def init_db():
    """初始化数据库表结构"""
    conn = None # 初始化 conn 以确保 finally 中可用
    try:
        if not os.path.exists(PERSISTENT_DATA_PATH):
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
            logger.info(f"持久化数据目录已创建: {PERSISTENT_DATA_PATH}")

        conn = get_db_connection() # 获取连接
        cursor = conn.cursor()

        # --- 创建 processed_log 表 ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_log (
                item_id TEXT PRIMARY KEY,
                item_name TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("Table 'processed_log' schema confirmed/created.")

        # --- 创建 failed_log 表 ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS failed_log (
                item_id TEXT PRIMARY KEY,
                item_name TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error_message TEXT,
                item_type TEXT
            )
        ''')
        logger.info("Table 'failed_log' schema confirmed/created.")

        # --- 创建 translation_cache 表 ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS translation_cache (
                original_text TEXT PRIMARY KEY,
                translated_text TEXT,
                engine_used TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_original_text ON translation_cache (original_text)")
        logger.info("Table 'translation_cache' and index schema confirmed/created.")

        # --- 创建 person_identity_map 表 (包含 imdb_id) ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS person_identity_map (
                tmdb_person_id TEXT,
                emby_person_id TEXT NOT NULL,
                emby_person_name TEXT,
                tmdb_name TEXT,
                imdb_id TEXT,
                douban_celebrity_id TEXT,
                douban_name TEXT,
                last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (emby_person_id),
                UNIQUE (tmdb_person_id),      
                UNIQUE (imdb_id),            
                UNIQUE (douban_celebrity_id) 
            )
        ''')
        # 为 person_identity_map 表创建其他需要的索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_tmdb_id_non_unique ON person_identity_map (tmdb_person_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_imdb_id_non_unique ON person_identity_map (imdb_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pim_douban_id_non_unique ON person_identity_map (douban_celebrity_id)")
        logger.info("Table 'person_identity_map' (with imdb_id) and indexes schema confirmed/created.")

        # --- 所有表和索引创建完毕后，进行一次总的提交 ---
        conn.commit()
        logger.info(f"数据库表结构已在 '{DB_PATH}' 初始化/检查完毕并已提交。")

    except Exception as e:
        logger.error(f"数据库初始化失败: {e}", exc_info=True)
        if conn: # 如果连接存在且发生错误，尝试回滚
            try:
                conn.rollback()
                logger.info("数据库初始化错误，事务已回滚。")
            except Exception as e_rollback:
                logger.error(f"数据库回滚失败: {e_rollback}", exc_info=True)
    finally:
        if conn: # 确保 conn 已定义且不为 None
            conn.close()
            logger.debug("数据库连接已在 init_db 的 finally 块中关闭。")
# --- 数据库辅助函数结束 ---

# --- 配置加载与保存 ---
def load_config() -> Dict[str, Any]:
    """
    从 config.ini 文件加载配置。
    如果文件不存在或缺少某些项，则使用默认值。
    """
    # 使用 getattr 从 constants 获取默认值，如果常量未定义则使用硬编码的后备值
    # 确保所有在 constants.py 中为配置项定义的 DEFAULT_XXX 常量都存在
    defaults = {
        constants.CONFIG_OPTION_EMBY_SERVER_URL: getattr(constants, 'DEFAULT_EMBY_SERVER_URL', ""),
        constants.CONFIG_OPTION_EMBY_API_KEY: getattr(constants, 'DEFAULT_EMBY_API_KEY', ""),
        constants.CONFIG_OPTION_EMBY_USER_ID: getattr(constants, 'DEFAULT_EMBY_USER_ID', ""),
        "refresh_emby_after_update": str(getattr(constants, 'DEFAULT_REFRESH_EMBY_AFTER_UPDATE', True)).lower(),
        constants.CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS: "",

        constants.CONFIG_OPTION_TMDB_API_KEY: getattr(constants, 'FALLBACK_TMDB_API_KEY', ""),

        constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN: str(getattr(constants, 'DEFAULT_API_COOLDOWN_SECONDS_FALLBACK', 1.0)),

        constants.CONFIG_OPTION_TRANSLATOR_ENGINES: ",".join(getattr(constants, 'DEFAULT_TRANSLATOR_ENGINES_ORDER', ['bing', 'google'])),

        constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE: getattr(constants, 'DEFAULT_DOMESTIC_SOURCE_MODE', "local_then_online"),
        
        constants.CONFIG_OPTION_LOCAL_DATA_PATH: getattr(constants, 'DEFAULT_LOCAL_DATA_PATH', ""),

        "delay_between_items_sec": str(getattr(constants, 'DEFAULT_DELAY_BETWEEN_ITEMS_SEC', 0.5)),

        "schedule_enabled": str(getattr(constants, 'DEFAULT_SCHEDULE_ENABLED', False)).lower(),
        "schedule_cron": getattr(constants, 'DEFAULT_SCHEDULE_CRON', "0 3 * * *"),
        "schedule_force_reprocess": str(getattr(constants, 'DEFAULT_SCHEDULE_FORCE_REPROCESS', False)).lower(),
        "schedule_sync_map_enabled": str(getattr(constants, 'DEFAULT_SCHEDULE_SYNC_MAP_ENABLED', False)).lower(),
        "schedule_sync_map_cron": getattr(constants, 'DEFAULT_SCHEDULE_SYNC_MAP_CRON', "0 1 * * *")
    }

    config_parser = configparser.ConfigParser(defaults=defaults)
    # config_parser.optionxform = str # 如果需要保留选项名大小写

    expected_sections = [
        constants.CONFIG_SECTION_EMBY, constants.CONFIG_SECTION_TMDB,
        constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_SECTION_TRANSLATION,
        constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_SECTION_LOCAL_DATA,
        "General", "Scheduler"
    ]

    if os.path.exists(CONFIG_FILE_PATH):
        try:
            config_parser.read(CONFIG_FILE_PATH, encoding='utf-8')
            logger.info(f"配置已从 '{CONFIG_FILE_PATH}' 加载。")
        except configparser.Error as e:
            logger.error(f"读取配置文件 '{CONFIG_FILE_PATH}' 失败: {e}。将使用默认值。")
            config_parser = configparser.ConfigParser(defaults=defaults)
    else:
        logger.warning(f"配置文件 '{CONFIG_FILE_PATH}' 未找到，将使用默认值。请通过设置页面保存一次以创建文件。")

    for section_name in expected_sections:
        if not config_parser.has_section(section_name):
            config_parser.add_section(section_name)
            logger.debug(f"load_config: 添加了缺失的配置节 [{section_name}]")

    app_cfg: Dict[str, Any] = {}

    # --- Emby Section ---
    app_cfg["emby_server_url"] = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL)
    app_cfg["emby_api_key"] = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY)
    app_cfg["emby_user_id"] = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_USER_ID)
    app_cfg["refresh_emby_after_update"] = config_parser.getboolean(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update")
    libraries_str = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS)
    app_cfg["libraries_to_process"] = [lib_id.strip() for lib_id in libraries_str.split(',') if lib_id.strip()]

    # --- TMDB Section ---
    app_cfg["tmdb_api_key"] = config_parser.get(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY)

    # --- Douban API Section ---
    app_cfg["api_douban_default_cooldown_seconds"] = config_parser.getfloat(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN)

    # --- Translation Section ---
    engines_str = config_parser.get(constants.CONFIG_SECTION_TRANSLATION, constants.CONFIG_OPTION_TRANSLATOR_ENGINES)
    app_cfg["translator_engines_order"] = [eng.strip() for eng in engines_str.split(',') if eng.strip()]
    if not app_cfg["translator_engines_order"]:
        app_cfg["translator_engines_order"] = getattr(constants, 'DEFAULT_TRANSLATOR_ENGINES_ORDER', ['bing', 'google'])

    # --- Domestic Source Section (数据源模式) ---
    app_cfg["data_source_mode"] = config_parser.get(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE)

    # --- Local Data Source Section ---
    app_cfg["local_data_path"] = config_parser.get(constants.CONFIG_SECTION_LOCAL_DATA, constants.CONFIG_OPTION_LOCAL_DATA_PATH).strip()

    # --- General Section ---
    app_cfg["delay_between_items_sec"] = config_parser.getfloat("General", "delay_between_items_sec")

    # --- Scheduler Section ---
    app_cfg["schedule_enabled"] = config_parser.getboolean("Scheduler", "schedule_enabled") # 用于全量扫描
    app_cfg["schedule_cron"] = config_parser.get("Scheduler", "schedule_cron")
    app_cfg["schedule_force_reprocess"] = config_parser.getboolean("Scheduler", "schedule_force_reprocess")
    app_cfg["schedule_sync_map_enabled"] = config_parser.getboolean("Scheduler", "schedule_sync_map_enabled")
    app_cfg["schedule_sync_map_cron"] = config_parser.get("Scheduler", "schedule_sync_map_cron")

    logger.debug(f"load_config: 返回的 app_cfg['libraries_to_process'] = {app_cfg.get('libraries_to_process')}")
    logger.debug(f"load_config: 返回的 app_cfg['data_source_mode'] = {app_cfg.get('data_source_mode')}")
    logger.debug(f"load_config: 返回的 app_cfg['schedule_enabled'] (for scan) = {app_cfg.get('schedule_enabled')}")
    return app_cfg

def save_config(new_config: Dict[str, Any]):
    config = configparser.ConfigParser()
    # 如果配置文件已存在，先读取它，这样可以保留文件中不由UI管理的注释或部分
    if os.path.exists(CONFIG_FILE_PATH):
        config.read(CONFIG_FILE_PATH, encoding='utf-8')

    # --- 修改开始 ---
    # 1. 定义所有此函数会管理的配置节（分组名）
    #    确保这些名称与 constants.py 中的定义以及 load_config 函数的期望一致
    all_sections_to_manage = [
        constants.CONFIG_SECTION_EMBY,
        constants.CONFIG_SECTION_TMDB,
        constants.CONFIG_SECTION_API_DOUBAN,    # 用于豆瓣API特定设置，如冷却时间
        constants.CONFIG_SECTION_TRANSLATION,
        constants.CONFIG_SECTION_DOMESTIC_SOURCE, # 用于 domestic_source_mode (之前出错的地方)
        constants.CONFIG_SECTION_LOCAL_DATA,    # 用于 local_data_path
        "General",                              # 通用设置
        "Scheduler"                             # 定时任务设置
    ]

    # 2. 确保所有这些节都存在于 config 对象中，如果不存在则添加
    for section_name in all_sections_to_manage:
        if not config.has_section(section_name):
            config.add_section(section_name)
    # --- 修改结束 ---

    # 3. 现在可以安全地设置每个配置项了，因为我们知道它们所属的节已经存在

    # --- Emby Section ---
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL, str(new_config.get("emby_server_url", "")))
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY, str(new_config.get("emby_api_key", "")))
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_USER_ID, str(new_config.get("emby_user_id", "")))
    config.set(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", str(new_config.get("refresh_emby_after_update", True)).lower())
    
    # --- 修正：正确保存媒体库列表到 Emby 节下 ---
    libraries_list = new_config.get("libraries_to_process", [])
    if not isinstance(libraries_list, list): # 做个类型检查和转换
        if isinstance(libraries_list, str) and libraries_list:
            libraries_list = [lib_id.strip() for lib_id in libraries_list.split(',') if lib_id.strip()]
        else:
            libraries_list = []
    # 使用 constants.CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS 作为键名
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS, ",".join(map(str, libraries_list)))
    # --- 修正结束 ---

    # --- TMDB Section ---
    config.set(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY, str(new_config.get("tmdb_api_key", constants.FALLBACK_TMDB_API_KEY)))

    # --- Douban API Section (对应常量 constants.CONFIG_SECTION_API_DOUBAN) ---
    config.set(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, str(new_config.get("api_douban_default_cooldown_seconds", constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK)))

    # --- Translation Section ---
    engines_list = new_config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
    if not isinstance(engines_list, list) or not engines_list: # 确保是列表且非空
        engines_list = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
    config.set(constants.CONFIG_SECTION_TRANSLATION, constants.CONFIG_OPTION_TRANSLATOR_ENGINES, ",".join(engines_list))

    # --- Domestic Source Section (对应常量 constants.CONFIG_SECTION_DOMESTIC_SOURCE) ---
    config.set(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, str(new_config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)))

    # --- Local Data Section (对应常量 constants.CONFIG_SECTION_LOCAL_DATA) ---
    config.set(constants.CONFIG_SECTION_LOCAL_DATA, constants.CONFIG_OPTION_LOCAL_DATA_PATH, str(new_config.get("local_data_path", "")))

    # --- General Section ---
    config.set("General", "delay_between_items_sec", str(new_config.get("delay_between_items_sec", "0.5")))

    # --- Scheduler Section ---
    config.set("Scheduler", "schedule_enabled", str(new_config.get("schedule_enabled", False)).lower())
    config.set("Scheduler", "schedule_cron", str(new_config.get("schedule_cron", "0 3 * * *")))
    config.set("Scheduler", "schedule_force_reprocess", str(new_config.get("schedule_force_reprocess", False)).lower())

    # --- 打印即将写入的配置 (用于调试，这部分可以保留) ---
    logger.debug("save_config: 即将写入文件的 ConfigParser 内容:")
    for section_name_debug in config.sections():
        logger.debug(f"  [{section_name_debug}]")
        for key_debug, value_debug in config.items(section_name_debug):
            logger.debug(f"    {key_debug} = {value_debug}")
    # --- 调试日志结束 ---

    try:
        if not os.path.exists(PERSISTENT_DATA_PATH):
            logger.info(f"save_config: 持久化目录 {PERSISTENT_DATA_PATH} 不存在，尝试创建...")
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
        logger.info(f"save_config: 准备将配置写入到文件: {CONFIG_FILE_PATH}")
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
            config.write(configfile)
        logger.info(f"配置已成功写入到 {CONFIG_FILE_PATH}。")
    except Exception as e:
        logger.error(f"保存配置文件 {CONFIG_FILE_PATH} 失败: {e}", exc_info=True)
        # flash(f"保存配置文件失败: {e}", "error") # 在非请求上下文中 flash 会报错
    finally:
        initialize_media_processor() # 重新加载配置并初始化处理器
        setup_scheduled_tasks()    # 根据新配置更新定时任务
# --- 配置加载与保存结束 --- (确保这个注释和你的文件结构匹配)

# --- MediaProcessor 初始化 ---
def initialize_media_processor():
    global media_processor_instance
    current_config = load_config() # load_config 现在会包含 libraries_to_process
    current_config['db_path'] = DB_PATH

    # ... (关闭旧实例的逻辑不变) ...
    if media_processor_instance and hasattr(media_processor_instance, 'close'):
        logger.info("准备关闭旧的 MediaProcessor 实例...")
        try:
            media_processor_instance.close()
            logger.info("旧的 MediaProcessor 实例已关闭。")
        except Exception as e_close_old:
            logger.error(f"关闭旧 MediaProcessor 实例时出错: {e_close_old}", exc_info=True)

    logger.info("准备创建新的 MediaProcessor 实例...")
    try:
        media_processor_instance = MediaProcessor(config=current_config) # current_config 已包含所需信息
        logger.info("新的 MediaProcessor 实例已创建/更新。")
    except Exception as e_init_mp:
        logger.error(f"创建 MediaProcessor 实例失败: {e_init_mp}", exc_info=True)
        media_processor_instance = None
        print(f"CRITICAL ERROR: MediaProcessor 核心处理器初始化失败: {e_init_mp}. 应用可能无法正常工作。")
# --- MediaProcessor 初始化结束 ---

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
                    logger.info(f"media_processor_instance.close() 调用完毕 (任务 '{task_name}' finally块)。")
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

# --- Flask 路由 ---
@app.route('/')
def index():
    return redirect(url_for('settings_page'))

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        # ... (你的 POST 逻辑，这部分应该没问题) ...
        new_conf = {
            "emby_server_url": request.form.get("emby_server_url", "").strip(),
            "emby_api_key": request.form.get("emby_api_key", "").strip(),
            "emby_user_id": request.form.get("emby_user_id", "").strip(),
            "local_data_path": request.form.get("local_data_path", "").strip(),
            "domestic_source_mode": request.form.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE), # 确保这里获取正确
            "tmdb_api_key": request.form.get("tmdb_api_key", "").strip(),
            "translator_engines_order": [eng.strip() for eng in request.form.get("translator_engines_order", "").split(',') if eng.strip()],
            "delay_between_items_sec": float(request.form.get("delay_between_items_sec", 0.5)),
            "refresh_emby_after_update": "refresh_emby_after_update" in request.form,
            "api_douban_default_cooldown_seconds": float(request.form.get("api_douban_default_cooldown_seconds", constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK)),
            "schedule_enabled": "schedule_enabled" in request.form,
            "schedule_cron": request.form.get("schedule_cron", "0 3 * * *").strip(),
            "schedule_force_reprocess": "schedule_force_reprocess" in request.form,
        }
        new_conf["schedule_sync_map_enabled"] = "schedule_sync_map_enabled" in request.form
        new_conf["schedule_sync_map_cron"] = request.form.get("schedule_sync_map_cron", "0 1 * * *").strip()
        selected_libs_from_form = request.form.getlist("libraries_to_process")
        new_conf["libraries_to_process"] = selected_libs_from_form
        if not new_conf.get("translator_engines_order"):
            new_conf["translator_engines_order"] = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
        
        logger.debug(f"settings_page POST - 从表单获取的 new_conf: {new_conf}")
        save_config(new_conf) # 假设 save_config 存在
        flash("配置已保存！媒体库选择和定时任务已根据新配置更新。", "success")
        return redirect(url_for('settings_page'))

    # --- GET 请求逻辑 ---
    current_config = load_config() # load_config() 返回包含所有配置的字典
    available_engines = constants.AVAILABLE_TRANSLATOR_ENGINES
    current_engines_list = current_config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
    current_engine_str = ",".join(current_engines_list)

    selected_libraries = current_config.get("libraries_to_process", [])

    # --- 核心调试和修改开始 ---
    logger.debug(f"SETTINGS_PAGE_GET (Before extraction): current_config IS: {current_config}")
    logger.debug(f"SETTINGS_PAGE_GET (Before extraction): current_config['data_source_mode'] IS [{current_config.get('data_source_mode')}]")
    logger.debug(f"SETTINGS_PAGE_GET (Before extraction): constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE IS [{constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE}]")

    # 显式提取 domestic_source_mode 的值
    # 我们期望 constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE 的值是 "data_source_mode"
    # 或者如果 load_config 中用的是 "domestic_source_mode" 作为键，那这里也应该是 "domestic_source_mode"
    # 根据之前的日志，load_config 返回的字典中，键是 "data_source_mode"
    key_for_dsm_in_config_dict = "data_source_mode" # 这是 load_config 实际使用的键
    
    current_domestic_source_mode_value = current_config.get(key_for_dsm_in_config_dict)
    
    logger.debug(f"SETTINGS_PAGE_GET (After extraction): Extracted current_domestic_source_mode_value (from key '{key_for_dsm_in_config_dict}') IS [{current_domestic_source_mode_value}]")
    # --- 核心调试和修改结束 ---

    template_context = {
        'config': current_config, # 仍然传递完整的 config，其他地方可能需要
        'available_engines': available_engines,
        'current_engine_str': current_engine_str,
        'domestic_source_options_in_template': constants.DOMESTIC_SOURCE_OPTIONS,
        # 'constant_config_option_domestic_source_mode': constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, # 我们暂时不直接用这个来get
        'task_status': background_task_status, # 假设 background_task_status 已定义
        'app_version': constants.APP_VERSION,
        'selected_libraries': selected_libraries,
        'type': type, # 为了让模板中的 type() 能工作
        'current_dsm_value_for_template': current_domestic_source_mode_value # 将显式提取的值传递给模板
    }

    # 再次确认传递给模板前的值
    logger.debug(f"SETTINGS_PAGE_GET (Before render_template): template_context['config']['{key_for_dsm_in_config_dict}'] IS [{template_context.get('config', {}).get(key_for_dsm_in_config_dict)}]")
    logger.debug(f"SETTINGS_PAGE_GET (Before render_template): template_context['current_dsm_value_for_template'] IS [{template_context.get('current_dsm_value_for_template')}]")

    return render_template('settings.html', **template_context)
@app.route('/webhook/emby', methods=['POST'])
def emby_webhook():
    data = request.json
    logger.info(f"收到Emby Webhook: {data.get('Event') if data else '未知数据'}")
    item_id = data.get("Item", {}).get("Id") if data else None
    event_type = data.get("Event") if data else None
    trigger_events = ["item.add", "library.new"] # 你想触发处理的事件

    if item_id and event_type in trigger_events:
        logger.info(f"Webhook事件 '{event_type}'，准备处理 Item ID: {item_id}")
        def webhook_task_internal(item_id_to_process):
            if media_processor_instance:
                media_processor_instance.process_single_item(item_id_to_process)
            else:
                logger.error(f"Webhook处理 Item ID {item_id_to_process} 失败：MediaProcessor 未初始化。")
        # 使用通用任务执行器
        thread = threading.Thread(target=_execute_task_with_lock, args=(webhook_task_internal, f"Webhook处理 Item ID: {item_id}", item_id))
        thread.start()
        return jsonify({"status": "processing_triggered", "item_id": item_id}), 202
    return jsonify({"status": "event_not_handled"}), 200


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
def trigger_sync_person_map():
    global background_task_status
    if not media_processor_instance:
        flash("错误：服务未就绪，无法开始同步映射表。", "error")
        logger.warning("trigger_sync_person_map: MediaProcessor未初始化。")
        return redirect(url_for('settings_page'))

    if task_lock.locked():
        flash("已有其他后台任务正在运行，请稍后再试。", "warning")
        logger.warning("trigger_sync_person_map: 检测到已有任务运行。")
        return redirect(url_for('settings_page'))

    task_name = "同步Emby人物映射表"
    logger.info(f"收到手动触发 '{task_name}' 的请求。")

    def sync_map_task_internal(): # 这是在后台线程中执行的
        if media_processor_instance and \
           media_processor_instance.emby_url and \
           media_processor_instance.emby_api_key: # 确保 Emby 配置有效

            logger.info(f"'{task_name}': 准备创建 SyncHandler 实例...")
            try:
                # --- 创建 SyncHandler 实例 ---
                sync_handler_instance = SyncHandler(
                    db_path=DB_PATH, # 使用 web_app.py 中定义的全局 DB_PATH
                    emby_url=media_processor_instance.emby_url,
                    emby_api_key=media_processor_instance.emby_api_key,
                    emby_user_id=media_processor_instance.emby_user_id
                )
                logger.info(f"'{task_name}': SyncHandler 实例已创建。")

                # --- 调用同步方法 ---
                sync_handler_instance.sync_emby_person_map_to_db(
                    update_status_callback=update_status_from_thread # 传递状态更新回调
                )
            except NameError as ne: # 如果 SyncHandler 没有被正确导入
                 logger.error(f"'{task_name}' 无法执行：SyncHandler 类未定义或未导入。错误: {ne}", exc_info=True)
                 update_status_from_thread(-1, "错误：同步功能组件未找到")
            except Exception as e_sync:
                logger.error(f"'{task_name}' 执行过程中发生严重错误: {e_sync}", exc_info=True)
                update_status_from_thread(-1, f"错误：同步失败 ({str(e_sync)[:50]}...)")
        else:
            logger.error(f"'{task_name}' 无法执行：MediaProcessor 未初始化或 Emby 配置不完整。")
            update_status_from_thread(-1, "错误：核心处理器或Emby配置未就绪")

    thread = threading.Thread(target=_execute_task_with_lock, args=(sync_map_task_internal, task_name))
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

# --- API 端点 for 搜索 (骨架，需要你填充 Emby 搜索和更完善的状态判断) ---
@app.route('/api/search_media')
def api_search_media():
    query = request.args.get('query', '', type=str).strip()
    scope = request.args.get('scope', 'all', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    if page < 1: page = 1
    if per_page < 1: per_page = 10
    if per_page > 100: per_page = 100

    offset = (page - 1) * per_page
    results: List[Dict[str, Any]] = [] # 明确类型
    total_items = 0
    item_ids_on_page: List[str] = [] # 明确类型

    conn = get_db_connection()
    cursor = conn.cursor()

    # TODO: scope 'all' 需要真实的 Emby 搜索集成
    if scope == "all":
        logger.warning("'/api/search_media' scope 'all' 的 Emby 搜索部分尚未完全实现。将临时使用已处理数据。")
        # 临时使用已处理数据代替，你需要替换为真实的 Emby 搜索逻辑
        # emby_search_results = emby_handler.search_emby_library(query, page, per_page, media_processor_instance.emby_url, ...)
        # total_items = emby_search_results.get('total_count', 0)
        # item_ids_on_page = [item['Id'] for item in emby_search_results.get('items', [])]
        # --- 临时代码 ---
        count_sql = "SELECT COUNT(*) FROM processed_log WHERE item_name LIKE ?" if query else "SELECT COUNT(*) FROM processed_log"
        items_sql = "SELECT item_id FROM processed_log WHERE item_name LIKE ? ORDER BY processed_at DESC LIMIT ? OFFSET ?" if query else "SELECT item_id FROM processed_log ORDER BY processed_at DESC LIMIT ? OFFSET ?"
        params = ('%' + query + '%',) if query else ()
        total_items = cursor.execute(count_sql, params).fetchone()[0]
        item_ids_on_page = [row['item_id'] for row in cursor.execute(items_sql, params + (per_page, offset) if query else (per_page, offset)).fetchall()]
        # --- 临时代码结束 ---
    elif scope == "processed":
        count_sql = "SELECT COUNT(*) FROM processed_log WHERE item_name LIKE ?" if query else "SELECT COUNT(*) FROM processed_log"
        items_sql = "SELECT item_id FROM processed_log WHERE item_name LIKE ? ORDER BY processed_at DESC LIMIT ? OFFSET ?" if query else "SELECT item_id FROM processed_log ORDER BY processed_at DESC LIMIT ? OFFSET ?"
        params = ('%' + query + '%',) if query else ()
        total_items = cursor.execute(count_sql, params).fetchone()[0]
        item_ids_on_page = [row['item_id'] for row in cursor.execute(items_sql, params + (per_page, offset) if query else (per_page, offset)).fetchall()]
    elif scope == "failed":
        count_sql = "SELECT COUNT(*) FROM failed_log WHERE item_name LIKE ?" if query else "SELECT COUNT(*) FROM failed_log"
        items_sql = "SELECT item_id FROM failed_log WHERE item_name LIKE ? ORDER BY failed_at DESC LIMIT ? OFFSET ?" if query else "SELECT item_id FROM failed_log ORDER BY failed_at DESC LIMIT ? OFFSET ?"
        params = ('%' + query + '%',) if query else ()
        total_items = cursor.execute(count_sql, params).fetchone()[0]
        item_ids_on_page = [row['item_id'] for row in cursor.execute(items_sql, params + (per_page, offset) if query else (per_page, offset)).fetchall()]
    else:
        conn.close()
        return jsonify({"error": "无效的搜索范围"}), 400

    for item_id_str in item_ids_on_page:
        if not media_processor_instance: break
        emby_details = None
        try:
            emby_details = emby_handler.get_emby_item_details(item_id_str, media_processor_instance.emby_url, media_processor_instance.emby_api_key, media_processor_instance.emby_user_id)
        except Exception as e: logger.error(f"API search: 获取Emby详情失败 for ID {item_id_str}: {e}")

        item_result = {
            "emby_id": item_id_str,
            "name": emby_details.get("Name", "未知") if emby_details else "获取Emby信息失败",
            "year": emby_details.get("ProductionYear") if emby_details else None,
            "type": emby_details.get("Type") if emby_details else None,
            "overall_status": "pending", "failure_reason": None, "actors": []
        }
        processed_entry = cursor.execute("SELECT * FROM processed_log WHERE item_id = ?", (item_id_str,)).fetchone()
        failed_entry = cursor.execute("SELECT * FROM failed_log WHERE item_id = ?", (item_id_str,)).fetchone()
        if failed_entry: item_result["overall_status"], item_result["failure_reason"] = "failed", failed_entry["error_message"]
        elif processed_entry: item_result["overall_status"] = "processed"

        if emby_details and emby_details.get("People"):
            for person in emby_details.get("People", []):
                if person.get("Type") == "Actor":
                    actor_name, actor_role = person.get("Name"), person.get("Role")
                    actor_status_code = "ok"
                    if item_result["overall_status"] == "failed": actor_status_code = "parent_failed"
                    elif item_result["overall_status"] == "pending":
                        actor_status_code = "pending_translation"
                        # TODO: 更精确的状态判断，例如检查翻译缓存或 utils.contains_chinese
                        # if actor_name and not utils.contains_chinese(actor_name): actor_status_code = "name_untranslated"
                        # if actor_role and not utils.contains_chinese(actor_role): actor_status_code = "character_untranslated"
                    item_result["actors"].append({
                        "name": actor_name, "character": actor_role,
                        "status_code": actor_status_code,
                        "status_text": constants.ACTOR_STATUS_TEXT_MAP.get(actor_status_code, "未知")
                    })
        results.append(item_result)
    conn.close()
    total_pages = (total_items + per_page - 1) // per_page if total_items > 0 else 0
    return jsonify({
        "items": results, "total_items": total_items, "total_pages": total_pages,
        "current_page": page, "per_page": per_page, "query": query, "scope": scope
    })

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
        else: logger.info("MediaProcessor 实例已关闭 (atexit)。")

    if scheduler and scheduler.running:
        logger.info("正在关闭 APScheduler (atexit)...")
        try: scheduler.shutdown(wait=False) # wait=False 避免阻塞退出
        except Exception as e: logger.error(f"关闭 APScheduler 时发生错误 (atexit): {e}", exc_info=True)
        else: logger.info("APScheduler 已关闭 (atexit)。")
    logger.info("atexit 清理操作执行完毕。")

atexit.register(application_exit_handler)
logger.info("已注册应用程序退出处理程序 (atexit)。")
# --- 应用退出处理结束 ---

# --- 主程序入口 ---
if __name__ == '__main__':
    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}, 调试模式: {constants.DEBUG_MODE}")
    init_db()
    initialize_media_processor()
    if not scheduler.running:
        try:
            scheduler.start()
            logger.info("APScheduler 已启动。")
        except Exception as e_scheduler_start:
            logger.error(f"APScheduler 启动失败: {e_scheduler_start}", exc_info=True)
    setup_scheduled_tasks()


    # --- !!! 测试代码开始 !!! ---
if __name__ == '__main__':
    RUN_MANUAL_TESTS = False  # <--- 在这里控制是否运行测试代码

    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}, 调试模式: {constants.DEBUG_MODE}")
    init_db()
    initialize_media_processor() # 确保 media_processor_instance 被创建并配置好
    # ... (scheduler setup) ...

    # --- !!! 测试 _process_cast_list 方法 !!! ---
    # if media_processor_instance and media_processor_instance.emby_url:
    #     TEST_MOVIE_ID_FOR_PROCESS_CAST = "432258" # 继续用 “骗骗”喜欢你 的 ID，或者你换一个
        
    #     logger.info(f"--- 开始手动测试 _process_cast_list for movie ID: {TEST_MOVIE_ID_FOR_PROCESS_CAST} ---")

    #     # 1. 获取这部电影的原始 Emby 详情 (包含原始 People 列表)
    #     raw_movie_details = None
    #     try:
    #         raw_movie_details = emby_handler.get_emby_item_details(
    #             TEST_MOVIE_ID_FOR_PROCESS_CAST,
    #             media_processor_instance.emby_url,
    #             media_processor_instance.emby_api_key,
    #             media_processor_instance.emby_user_id
    #         )
    #     except Exception as e_get_raw:
    #         logger.error(f"测试：获取原始电影详情失败: {e_get_raw}", exc_info=True)
        
    #     if raw_movie_details and raw_movie_details.get("People"):
    #         original_emby_people = raw_movie_details.get("People", [])
    #         logger.info(f"测试：获取到电影 '{raw_movie_details.get('Name')}' 的原始 People 列表，数量: {len(original_emby_people)}")
    #         logger.debug(f"测试：原始 People (前3条): {original_emby_people[:3]}")

    #         # 2. (可选) 构造或加载你的 source_actors_candidates 数据
    #         #    为了精确测试，你可以手动构造这个列表，模拟从本地文件或豆瓣API获取的数据
    #         #    确保它包含我们想测试的各种情况（能匹配映射表、不能匹配等）
    #         #    这里我们先假设 _process_cast_list 内部会自己处理 source_actors_candidates 的获取
    #         #    如果你想更精确控制，可以在这里手动创建 source_actors_candidates 并想办法传递给
    #         #    一个修改版的 _process_cast_list (但这会使测试更复杂)
    #         #    目前，我们先依赖 _process_cast_list 内部的数据源获取逻辑。
    #         #    确保你的 config.ini 中 data_source_mode 和 local_data_path 设置正确以便它能获取到数据。

    #         # 3. 调用 _process_cast_list 方法
    #         logger.info(f"测试：准备调用 media_processor_instance._process_cast_list...")
    #         try:
    #             # _process_cast_list 期望的第一个参数是原始的 People 列表
    #             final_cast_list = media_processor_instance._process_cast_list(
    #                 original_emby_people, # 传递原始 People 列表
    #                 raw_movie_details      # 传递整个电影详情，方法内部会提取所需信息
    #             )
                
    #             logger.info(f"测试：_process_cast_list 执行完毕。最终演员列表长度: {len(final_cast_list)}")
    #             logger.info(f"测试：最终演员列表 (前5条):")
    #             for i, actor in enumerate(final_cast_list[:5]):
    #                 logger.info(f"  演员 {i+1}: Name='{actor.get('Name')}', Role='{actor.get('Role')}', EmbyPID='{actor.get('EmbyPersonId')}', TMDbPID='{actor.get('TmdbPersonId')}', DoubanID='{actor.get('DoubanCelebrityId')}', Providers='{actor.get('ProviderIds')}', Source='{actor.get('_source')}'")
                
    #             # 4. (可选) 如果你想继续测试写入 Emby，可以取消注释下面的代码
    #             #    但建议先只看 _process_cast_list 的输出是否正确
    #             # logger.info("测试：准备调用 update_emby_item_cast 将处理后的演员列表写回Emby...")
    #             # cast_for_emby_update = []
    #             # for actor_data in final_cast_list:
    #             #     # ... (与 process_single_item 中类似的构建 cast_for_emby_update 的逻辑) ...
    #             # success_write = emby_handler.update_emby_item_cast(...)
    #             # if success_write:
    #             #     logger.info("测试：演员列表已尝试更新到Emby。")
    #             #     emby_handler.refresh_emby_item_metadata(...)

    #         except Exception as e_proc_cast:
    #             logger.error(f"测试：调用 _process_cast_list 时发生错误: {e_proc_cast}", exc_info=True)
    #     else:
    #         logger.error(f"测试：未能获取电影 {TEST_MOVIE_ID_FOR_PROCESS_CAST} 的原始详情，无法继续测试 _process_cast_list。")

    #     logger.info(f"--- 手动测试 _process_cast_list 结束 ---")
    # else:
    #     logger.warning("手动测试 _process_cast_list 跳过：MediaProcessor 未初始化或 Emby 配置不完整。")
    # # --- 测试代码结束 --- #

    # app.run(...) # 你可以暂时注释掉 app.run，这样脚本执行完测试就结束了，方便看日志
    # 或者保留它，测试完后再通过浏览器访问应用

    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=constants.DEBUG_MODE, use_reloader=not constants.DEBUG_MODE)
    # 注意: debug=True 配合 use_reloader=True (Flask默认) 会导致 atexit 执行两次或行为异常。
    # 在生产中，use_reloader 应为 False。为了开发方便，可以暂时接受 atexit 的一些小问题。
    # 或者在 debug 模式下，考虑不依赖 atexit，而是通过其他方式（如信号处理）来触发清理。
    # 最简单的是，开发时接受它，部署时确保 use_reloader=False。
# --- 主程序入口结束 ---