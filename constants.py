# constants.py
APP_VERSION = "1.2.8-beta"
# Debugging flag
DEBUG = True  # 全局调试开关，True会输出更多日志，False则输出较少

# --- 配置文件的常量 ---
CONFIG_FILE = "config.ini"  # 程序配置文件的名字
TRANSLATION_CACHE_FILE = "douban_translation_cache.json"#翻译缓存

# --- 日志文件的常量 ---
PROCESSED_MEDIA_LOG_FILE = "processed_media_log.txt"  # 记录已处理媒体项的日志文件名
FAILED_ITEMS_LOG_PREFIX = "manual_review_required_"   # 失败项日志文件名的前缀 (暂时未使用，但保留)
FIXED_FAILURE_LOG_FILENAME = FAILED_ITEMS_LOG_PREFIX + "latest_batch_failures.txt" # 固定失败日志文件名

# --- 在线翻译引擎 ---
AVAILABLE_TRANSLATOR_ENGINES = ["baidu", "youdao", "sogou", "alibaba", "google", "bing", "baidu", "tencent", "deepl"]
DEFAULT_TRANSLATOR_ENGINES_ORDER = ["google", "bing", "baidu", "alibaba"] # <--- 确认这个常量存在且名字正确

# --- 在线翻译配置 ---
CONFIG_SECTION_TRANSLATION = "Translation"
CONFIG_OPTION_TRANSLATOR_ENGINES = "preferred_engines_order"

# --- 国产影视数据源配置 ---
CONFIG_SECTION_DOMESTIC_SOURCE = "DomesticSource"
CONFIG_OPTION_DOMESTIC_SOURCE_MODE = "douban_source_mode_for_domestic"
#CONFIG_OPTION_DOMESTIC_USE_ONLINE_API = "use_online_douban_api_for_domestic" # 可选值为 "local_douban" 或 "online_douban"
# 定义三个模式的常量值
DOMESTIC_SOURCE_MODE_LOCAL_ONLY = "local_only"
DOMESTIC_SOURCE_MODE_ONLINE_ONLY = "online_only"
DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE = "local_then_online" # 本地优先，在线备选
# 默认的国产影视数据源策略
DEFAULT_DOMESTIC_SOURCE_MODE = DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE

# [Paths] 配置段 (用于路径相关的设置)
CONFIG_SECTION_PATHS = "Paths"  # config.ini 中路径配置段的名称
CONFIG_OPTION_MAIN_CACHE_PATH = "main_cache_path"  # 主缓存目录的选项名
CONFIG_OPTION_OVERRIDE_CACHE_PATH = "override_cache_path"  # 覆盖缓存目录的选项名

# [Emby] 配置段 (用于Emby相关的设置)
CONFIG_SECTION_EMBY = "Emby"  # config.ini 中Emby配置段的名称
CONFIG_OPTION_EMBY_SERVER_URL = "server_url"  # Emby服务器地址的选项名
CONFIG_OPTION_EMBY_API_KEY = "api_key"  # Emby API密钥的选项名
CONFIG_OPTION_EMBY_USER_ID = "user_id"  # <--- 新增这一行，定义UserID的配置键名
CONFIG_OPTION_ENABLE_EMBY_ITEM_REFRESH = "enable_emby_item_refresh_after_processing"
DEFAULT_ENABLE_EMBY_ITEM_REFRESH = False # 默认关闭通知刷新

# --- TMDB API 配置段和选项名 ---
CONFIG_SECTION_TMDB = "TMDB"
CONFIG_OPTION_TMDB_API_KEY = "api_key"
FALLBACK_TMDB_API_KEY = ""
# --- TMDB API 配置结束 ---

# [API_Douban] 配置段 (用于豆瓣API相关的设置，之前可能是 [API])
CONFIG_SECTION_API_DOUBAN = "API_Douban"  # config.ini 中豆瓣API配置段的名称
CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN = "default_cooldown_seconds" # 默认冷却时间的选项名
CONFIG_OPTION_DOUBAN_MAX_COOLDOWN = "max_cooldown_seconds"      # 最大冷却时间的选项名
CONFIG_OPTION_DOUBAN_INCREMENT_COOLDOWN = "cooldown_increment_seconds" # 冷却时间增量的选项名
# --- 新增配置项常量结束 ---
DOUBAN_LOCAL_MOVIES_SUBDIR = "douban-movies"
DOUBAN_LOCAL_TV_SUBDIR = "douban-tv"
# UI Theming (PyQt6不直接用ttkthemes，但保留此常量以防未来有其他用途)
USE_TTK_THEMES = False

# --- 默认值常量 (当config.ini中没有对应配置时使用) ---
# 路径相关的默认值
FALLBACK_DEFAULT_MAIN_CACHE_PATH = "~"  # 如果config.ini里没有主缓存目录，默认指向用户的主目录 (~)
                                        # 之前是 FALLBACK_DEFAULT_JSON_PATH，现在改名更清晰

# 豆瓣API冷却相关的默认值
DEFAULT_API_COOLDOWN_SECONDS_FALLBACK = 1   # 默认冷却时间 (秒)
MAX_API_COOLDOWN_SECONDS_FALLBACK = 60      # 最大冷却时间 (秒)
COOLDOWN_INCREMENT_SECONDS_FALLBACK = 10    # 冷却时间递增值 (秒)
                                            # (你之前的例子是3，但原始常量是10，这里保持10，可以按需调整)

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