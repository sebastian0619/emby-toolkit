# config_manager.py
import os
import configparser
import logging
from typing import Dict, Any, Tuple

# --- 核心模块导入 ---
import constants # 你的常量定义

logger = logging.getLogger(__name__)

# --- 路径和配置定义 ---
# 这部分逻辑与配置紧密相关，所以移到这里
APP_DATA_DIR_ENV = os.environ.get("APP_DATA_DIR")

if APP_DATA_DIR_ENV:
    # 如果在 Docker 中，并且设置了 APP_DATA_DIR 环境变量 (例如设置为 "/config")
    PERSISTENT_DATA_PATH = APP_DATA_DIR_ENV
    logger.info(f"检测到 APP_DATA_DIR 环境变量，将使用持久化数据路径: {PERSISTENT_DATA_PATH}")
else:
    # 本地开发环境
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    PERSISTENT_DATA_PATH = os.path.join(PROJECT_ROOT, "local_data")
    logger.debug(f"未检测到 APP_DATA_DIR 环境变量，将使用本地开发数据路径: {PERSISTENT_DATA_PATH}")

# 确保这个持久化数据目录存在
try:
    if not os.path.exists(PERSISTENT_DATA_PATH):
        os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
        logger.info(f"持久化数据目录已创建/确认: {PERSISTENT_DATA_PATH}")
except OSError as e:
    logger.error(f"创建持久化数据目录 '{PERSISTENT_DATA_PATH}' 失败: {e}。程序可能无法正常读写配置文件和数据库。")
    raise RuntimeError(f"无法创建必要的数据目录: {PERSISTENT_DATA_PATH}") from e

# 从 constants 模块安全地获取文件名，如果不存在则使用默认值
CONFIG_FILE_NAME = getattr(constants, 'CONFIG_FILE_NAME', "config.ini")
DB_NAME = getattr(constants, 'DB_NAME', "emby_actor_processor.sqlite")

# 最终的配置文件和数据库路径
CONFIG_FILE_PATH = os.path.join(PERSISTENT_DATA_PATH, CONFIG_FILE_NAME)
DB_PATH = os.path.join(PERSISTENT_DATA_PATH, DB_NAME)
LOG_DIRECTORY = os.path.join(PERSISTENT_DATA_PATH, 'logs')


# ✨✨✨ “配置清单” - 这是配置模块的核心 ✨✨✨
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

    # [Logging]
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

# --- 全局配置字典 ---
# 其他模块可以通过 from config_manager import APP_CONFIG 来访问
APP_CONFIG: Dict[str, Any] = {}

# --- 加载配置 ---
def load_config() -> Tuple[Dict[str, Any], bool]:
    """【清单驱动版】从 config.ini 加载配置到全局的 APP_CONFIG 变量。"""
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
    logger.info("全局配置 APP_CONFIG 已加载/更新。")
    return app_cfg, is_first_run

# --- 保存配置 ---
def save_config(new_config: Dict[str, Any]):
    """【清单驱动版】将配置保存到 config.ini，并更新全局 APP_CONFIG。"""
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
        
        # 处理百分号，防止 configparser 报错
        value_to_write = value_to_write.replace('%', '%%')
        config_parser.set(section, key, value_to_write)

    try:
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
            config_parser.write(configfile)
        
        # 更新内存中的全局配置
        APP_CONFIG = new_config.copy()
        logger.info(f"配置已成功写入到 {CONFIG_FILE_PATH}，内存中的配置已同步。")
        
    except Exception as e:
        logger.error(f"保存配置文件时失败: {e}", exc_info=True)
        # 抛出异常，让调用者知道保存失败
        raise