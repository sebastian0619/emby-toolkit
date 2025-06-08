# constants.py

# ... (你已有的其他常量，比如 EMBY_SERVER_URL, API_KEY 等) ...

# --- Web Application Settings ---
APP_VERSION = "1.0.6"  # 或者你的实际版本号
DEBUG_MODE = False      # 开发时设为 True，部署到生产环境时应设为 False
WEB_APP_PORT = 5257    # Web UI 监听的端口

# --- 数据库设置 ---
DB_NAME = "emby_actor_processor.sqlite" # 数据库文件名 (也可以在这里定义，然后在 web_app.py 中引用)

# --- 配置文件名 ---
CONFIG_FILE_NAME = "config.ini"
DOUBAN_API_AVAILABLE = True

# --- 翻译引擎 ---
# 你可以定义所有可用的翻译引擎列表，供设置页面选择
AVAILABLE_TRANSLATOR_ENGINES = ['bing', 'google', 'baidu', 'alibaba', 'youdao', 'tencent']
# 默认的翻译引擎顺序
DEFAULT_TRANSLATOR_ENGINES_ORDER = ['bing', 'google', 'baidu']

#本地数据源配置
CONFIG_SECTION_LOCAL_DATA = "LocalDataSource" # 新的节名
CONFIG_OPTION_LOCAL_DATA_PATH = "local_data_path" # 本地数据源根路径
DEFAULT_LOCAL_DATA_PATH = "" # 默认空，表示未配置

CONFIG_FILE_NAME = "config.ini"
DB_NAME = "emby_actor_processor.sqlite"

# --- 豆瓣处理模式 ---
DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE = "local_then_online"
DOMESTIC_SOURCE_MODE_ONLINE_ONLY = "online_only"
DOMESTIC_SOURCE_MODE_LOCAL_ONLY = "local_only"
DOMESTIC_SOURCE_MODE_DISABLED = "disabled_douban" # 明确禁用
DEFAULT_DOMESTIC_SOURCE_MODE = DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE
# 用于设置页面的选项
DOMESTIC_SOURCE_OPTIONS = [
    {"value": DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, "text": "豆瓣本地优先，在线备选 (推荐)"},
    {"value": DOMESTIC_SOURCE_MODE_ONLINE_ONLY, "text": "仅在线豆瓣API"},
    {"value": DOMESTIC_SOURCE_MODE_LOCAL_ONLY, "text": "仅豆瓣本地数据 (神医刮削)"},
    {"value": DOMESTIC_SOURCE_MODE_DISABLED, "text": "禁用豆瓣数据源"}
]


# --- API 冷却时间默认值 (如果 core_processor 或其他地方需要) ---
DEFAULT_API_COOLDOWN_SECONDS_FALLBACK = 1.0
MAX_API_COOLDOWN_SECONDS_FALLBACK = 5.0
COOLDOWN_INCREMENT_SECONDS_FALLBACK = 0.5

# --- TMDB API Key (如果 core_processor 或其他地方需要) ---
FALLBACK_TMDB_API_KEY = "" # 最好让用户在配置中填写

# --- 配置文件的节和选项名 (保持与你的 load_config/save_config 一致) ---
CONFIG_SECTION_EMBY = "Emby"
CONFIG_OPTION_EMBY_SERVER_URL = "emby_server_url"
CONFIG_OPTION_EMBY_API_KEY = "emby_api_key"
CONFIG_OPTION_EMBY_USER_ID = "emby_user_id"
CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS = "libraries_to_process"
# ... 其他配置常量 ...
CONFIG_SECTION_TMDB = "TMDB"
CONFIG_OPTION_TMDB_API_KEY = "tmdb_api_key"
CONFIG_SECTION_API_DOUBAN = "DoubanAPI"
CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN = "api_douban_default_cooldown_seconds"
CONFIG_SECTION_TRANSLATION = "Translation"
CONFIG_OPTION_TRANSLATOR_ENGINES = "translator_engines_order"
CONFIG_SECTION_DOMESTIC_SOURCE = "DomesticSource"
CONFIG_OPTION_DOMESTIC_SOURCE_MODE = "domestic_source_mode"
DEFAULT_MIN_SCORE_FOR_REVIEW = 6.0

# --- 演员状态文本映射 (用于 /api/search_media) ---
ACTOR_STATUS_TEXT_MAP = {
    "ok": "已处理",
    "name_untranslated": "演员名未翻译",
    "character_untranslated": "角色名未翻译",
    "name_char_untranslated": "演员名和角色名均未翻译",
    "pending_translation": "待翻译",
    "parent_failed": "媒体项处理失败",
    "unknown": "未知状态"
}


# ... (其他你可能已经定义的常量) ...

# API Source Map (如果未来增加其他API来源)
SOURCE_API_MAP = {
    "Douban": {
        "name": "豆瓣",
        "search_types": {
            "movie": {"title": "电影", "season": False},
            "tv": {"title": "电视剧", "season": True},
        },
    },
}
#时区
TIMEZONE = "Asia/Shanghai"

# 中文语言代码列表 (用于判断文本是否为中文)
CHINESE_LANG_CODES = ["zh", "zh-cn", "zh-hans", "cmn", "yue", "cn", "zh-sg", "zh-tw", "zh-hk"]