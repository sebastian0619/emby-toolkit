# web_app.py
import os
import re
import json
import sqlite3
from datetime import date, datetime
from actor_sync_handler import UnifiedSyncHandler
from db_handler import ActorDBManager
import emby_handler
import moviepilot_handler
import utils
import extensions
from extensions import (
    login_required, 
    task_lock_required, 
    processor_ready_required
)
from utils import LogDBManager
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, stream_with_context, send_from_directory,Response, abort, session
from werkzeug.utils import safe_join, secure_filename
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
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from actor_utils import enrich_all_actor_aliases_task
import db_handler
from db_handler import get_db_connection as get_central_db_connection
from flask import session
from croniter import croniter
import logging
# --- 导入蓝图 ---
from routes.watchlist import watchlist_bp
from routes.collections import collections_bp
from routes.actor_subscriptions import actor_subscriptions_bp
from routes.logs import logs_bp
# --- 核心模块导入 ---
import constants # 你的常量定义\
import logging
from logger_setup import frontend_log_queue, add_file_handler # 日志记录器和前端日志队列
import utils       # 例如，用于 /api/search_media
import config_manager
import task_manager
# --- 核心模块导入结束 ---
logger = logging.getLogger(__name__)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
app = Flask(__name__, static_folder='static')
app.secret_key = os.urandom(24)
# ✨✨✨ 导入网页解析器 ✨✨✨
try:
    from web_parser import parse_cast_from_url, ParserError
    WEB_PARSER_AVAILABLE = True
except ImportError:
    logger.error("web_parser.py 未找到或无法导入，从URL提取功能将不可用。")
    WEB_PARSER_AVAILABLE = False
#过滤底层日志
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# --- 全局变量 ---
EMBY_SERVER_ID: Optional[str] = None # ★★★ 新增：用于存储 Emby Server ID
media_processor_instance: Optional[MediaProcessor] = None
actor_subscription_processor_instance: Optional[ActorSubscriptionProcessor] = None
media_processor_instance: Optional[MediaProcessor] = None
watchlist_processor_instance: Optional[WatchlistProcessor] = None

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
        if not os.path.exists(config_manager.PERSISTENT_DATA_PATH):
            os.makedirs(config_manager.PERSISTENT_DATA_PATH, exist_ok=True)

        with get_central_db_connection(config_manager.DB_PATH) as conn:
            cursor = conn.cursor()

            # --- 1. ★★★ 性能优化：启用 WAL 模式  ★★★ ---
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                result = cursor.fetchone()
                if result and result[0].lower() == 'wal':
                    logger.trace("  -> 数据库已成功启用 WAL (Write-Ahead Logging) 模式。")
                else:
                    logger.warning(f"  -> 尝试启用 WAL 模式失败，当前模式: {result[0] if result else '未知'}。")
            except Exception as e_wal:
                logger.error(f"  -> 启用 WAL 模式时出错: {e_wal}")

            # --- 2. 创建基础表 (日志、缓存、用户) ---
            logger.trace("  -> 正在创建基础表...")
            cursor.execute("CREATE TABLE IF NOT EXISTS processed_log (item_id TEXT PRIMARY KEY, item_name TEXT, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, score REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS failed_log (item_id TEXT PRIMARY KEY, item_name TEXT, reason TEXT, failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, error_message TEXT, item_type TEXT, score REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS translation_cache (original_text TEXT PRIMARY KEY, translated_text TEXT, engine_used TEXT, last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            # --- 3. 创建核心功能表 ---
            # 电影合集检查
            logger.trace("  -> 正在创建 'collections_info' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collections_info (
                    emby_collection_id TEXT PRIMARY KEY,
                    name TEXT,
                    tmdb_collection_id TEXT,
                    status TEXT,
                    has_missing BOOLEAN, 
                    missing_movies_json TEXT,
                    last_checked_at TIMESTAMP,
                    poster_path TEXT,
                    in_library_count INTEGER DEFAULT 0 
                )
            """)

            # ✨ 为老用户平滑升级数据库结构的逻辑
            try:
                cursor.execute("PRAGMA table_info(collections_info)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'in_library_count' not in columns:
                    logger.info("    -> 检测到旧版 'collections_info' 表，正在添加 'in_library_count' 字段...")
                    cursor.execute("ALTER TABLE collections_info ADD COLUMN in_library_count INTEGER DEFAULT 0;")
                    logger.info("    -> 'in_library_count' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'collections_info' 表添加新字段时出错: {e_alter}")

            # 剧集追踪 (追剧列表) 
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
                    paused_until DATE DEFAULT NULL 
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

            # 演员订阅功能表
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
                    config_min_rating REAL DEFAULT 6.0,          -- 最低评分筛选，0表示不筛选

                    -- 状态与维护 --
                    status TEXT DEFAULT 'active',                -- 订阅状态 ('active', 'paused')
                    last_checked_at TIMESTAMP,                   -- 上次计划任务检查的时间
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 添加订阅的时间
                )
            """)
            # ★★★ 新增：为老用户平滑升级数据库结构 ★★★
            try:
                cursor.execute("PRAGMA table_info(actor_subscriptions)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'config_min_rating' not in columns:
                    logger.info("    -> 检测到旧版 'actor_subscriptions' 表，正在添加 'config_min_rating' 字段...")
                    cursor.execute("ALTER TABLE actor_subscriptions ADD COLUMN config_min_rating REAL DEFAULT 6.0;")
                    logger.info("    -> 'config_min_rating' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'actor_subscriptions' 表添加新字段时出错: {e_alter}")
            # ★★★ 升级逻辑结束 ★★★
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
# --- 初始化认证系统 ---
def init_auth():
    """
    【V2 - 使用全局配置版】初始化认证系统。
    """
    # ✨✨✨ 核心修复：不再自己调用 load_config，而是依赖已加载的 config_manager.APP_CONFIG ✨✨✨
    # load_config() 应该在主程序入口处被调用一次
    
    auth_enabled = config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_AUTH_ENABLED, False)
    env_username = os.environ.get("AUTH_USERNAME")
    
    if env_username:
        username = env_username.strip()
        logger.debug(f"检测到 AUTH_USERNAME 环境变量，将使用用户名: '{username}'")
    else:
        username = config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_AUTH_USERNAME, constants.DEFAULT_USERNAME).strip()
        logger.debug(f"未检测到 AUTH_USERNAME 环境变量，将使用配置文件中的用户名: '{username}'")

    if not auth_enabled:
        logger.info("用户认证功能未启用。")
        return

    # ... 函数的其余部分保持不变 ...
    conn = None
    try:
        conn = get_central_db_connection(config_manager.DB_PATH)
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
            logger.critical("请立即使用此密码登录，并在设置页面修改为你自己的密码。")
            logger.critical("=" * 60)
        else:
            logger.trace(f"[AUTH DIAGNOSTIC] User '{username}' found in DB. No action needed.")

    except Exception as e:
        logger.error(f"初始化认证系统时发生错误: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
        logger.info("="*21 + " [基础配置加载完毕] " + "="*21)
# --- 保存配置并重新加载的函数 ---
def save_config_and_reload(new_config: Dict[str, Any]):
    """
    调用配置管理器保存配置，并在此处执行所有必要的重新初始化操作。
    """
    try:
        # 步骤 1: 调用 config_manager 来保存文件和更新内存中的 config_manager.APP_CONFIG
        config_manager.save_config(new_config)
        
        # 步骤 2: 执行所有依赖于新配置的重新初始化逻辑
        # 这是从旧的 save_config 函数中移动过来的，现在的位置更合理
        initialize_processors()
        init_auth()
        setup_scheduled_tasks()
        logger.info("所有组件已根据新配置重新初始化完毕。")
        
    except Exception as e:
        logger.error(f"保存配置文件或重新初始化时失败: {e}", exc_info=True)
        # 向上抛出异常，让 API 端点可以捕获它并返回错误信息
        raise
# --- 始化所有需要的处理器实例 ---
def initialize_processors():
    """初始化所有处理器，并将实例赋值给 extensions 模块中的全局变量。"""
    # 这个函数不再需要 global 声明，因为它只修改其他模块的变量
    global media_processor_instance, watchlist_processor_instance, actor_subscription_processor_instance, EMBY_SERVER_ID
    if not config_manager.APP_CONFIG:
        logger.error("无法初始化处理器：全局配置 APP_CONFIG 为空。")
        return

    current_config = config_manager.APP_CONFIG.copy()
    current_config['db_path'] = config_manager.DB_PATH

    # --- 1. 创建实例并存储在局部变量中 ---
    
    # 初始化 server_id_local
    server_id_local = None
    emby_url = current_config.get("emby_server_url")
    emby_key = current_config.get("emby_api_key")
    if emby_url and emby_key:
        server_info = emby_handler.get_emby_server_info(emby_url, emby_key)
        if server_info and server_info.get("Id"):
            server_id_local = server_info.get("Id")
            logger.trace(f"成功获取到 Emby Server ID: {server_id_local}")
        else:
            logger.warning("未能获取到 Emby Server ID，跳转链接可能不完整。")

    # 初始化 media_processor_instance_local
    try:
        media_processor_instance_local = MediaProcessor(config=current_config)
        logger.info("核心处理器 实例已创建/更新。")
    except Exception as e:
        logger.error(f"创建 MediaProcessor 实例失败: {e}", exc_info=True)
        media_processor_instance_local = None

    # 初始化 watchlist_processor_instance_local
    try:
        watchlist_processor_instance_local = WatchlistProcessor(config=current_config)
        logger.trace("WatchlistProcessor 实例已成功初始化。")
    except Exception as e:
        logger.error(f"创建 WatchlistProcessor 实例失败: {e}", exc_info=True)
        watchlist_processor_instance_local = None

    # 初始化 actor_subscription_processor_instance_local
    try:
        actor_subscription_processor_instance_local = ActorSubscriptionProcessor(config=current_config)
        logger.trace("ActorSubscriptionProcessor 实例已成功初始化。")
    except Exception as e:
        logger.error(f"创建 ActorSubscriptionProcessor 实例失败: {e}", exc_info=True)
        actor_subscription_processor_instance_local = None


    # 首先，赋值给 web_app.py 自己的全局变量
    media_processor_instance = media_processor_instance_local
    watchlist_processor_instance = watchlist_processor_instance_local
    actor_subscription_processor_instance = actor_subscription_processor_instance_local
    EMBY_SERVER_ID = server_id_local
    
    # 然后，将同样的值赋给 extensions 模块的全局变量，供蓝图使用
    extensions.media_processor_instance = media_processor_instance
    extensions.watchlist_processor_instance = watchlist_processor_instance
    extensions.actor_subscription_processor_instance = actor_subscription_processor_instance
    extensions.EMBY_SERVER_ID = EMBY_SERVER_ID
    
    # --- 3. 将 extensions 中的变量注入到任务管理器 ---
    task_manager.initialize_task_manager(
        media_proc=extensions.media_processor_instance,
        watchlist_proc=extensions.watchlist_processor_instance,
        actor_sub_proc=extensions.actor_subscription_processor_instance,
        status_callback=update_status_from_thread
    )
# --- 后台任务回调 ---
def update_status_from_thread(progress: int, message: str):
    """
    这个回调函数由处理器调用，用于更新任务状态。
    它通过 task_manager 模块来修改状态字典。
    """
    # 确保我们访问的是 task_manager 模块中的状态字典
    if task_manager.background_task_status:
        task_manager.background_task_status["progress"] = progress
        task_manager.background_task_status["message"] = message
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
    config = config_manager.APP_CONFIG
    
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
                task_manager.submit_task_to_queue(task_process_full_library, "定时全量扫描", process_episodes=config_manager.APP_CONFIG.get('process_episodes', True))
            
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
            scheduler.add_job(func=lambda: task_manager.submit_task_to_queue(task_sync_person_map, "定时同步演员映射表"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_SYNC_PERSON_MAP, name="定时同步演员映射表", replace_existing=True)
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
            scheduler.add_job(func=lambda: task_manager.submit_task_to_queue(task_refresh_collections, "定时刷新电影合集"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_REFRESH_COLLECTIONS, name="定时刷新电影合集", replace_existing=True)
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
                task_manager.submit_task_to_queue(
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
            task_manager.submit_task_to_queue(
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
            scheduler.add_job(func=lambda: task_manager.submit_task_to_queue(task_enrich_aliases, "定时演员元数据增强"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_ENRICH_ALIASES, name="定时演员元数据增强", replace_existing=True)
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
            scheduler.add_job(func=lambda: task_manager.submit_task_to_queue(task_actor_translation_cleanup, "定时演员名查漏补缺"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_ACTOR_CLEANUP, name="定时演员名查漏补缺", replace_existing=True)
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
            scheduler.add_job(func=lambda: task_manager.submit_task_to_queue(task_auto_subscribe, "定时智能订阅"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_AUTO_SUBSCRIBE, name="定时智能订阅", replace_existing=True)
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
                func=lambda: task_manager.submit_task_to_queue(task_process_actor_subscriptions, "定时演员订阅扫描"),
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
            db_path=config_manager.DB_PATH,
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
        db_path = config_manager.DB_PATH
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
    logger.trace(f"手动刷新任务(ID: {subscription_id})：开始准备Emby媒体库数据...")
    
    # 在调用核心扫描函数前，必须先获取Emby数据
    emby_tmdb_ids = set()
    try:
        # 从 processor 或全局配置中获取 Emby 连接信息
        config = processor.config # 假设 processor 对象中存有配置
        emby_url = config.get('emby_server_url')
        emby_api_key = config.get('emby_api_key')
        emby_user_id = config.get('emby_user_id')

        all_libraries = emby_handler.get_emby_libraries(emby_url, emby_api_key, emby_user_id)
        library_ids_to_scan = [lib['Id'] for lib in all_libraries if lib.get('CollectionType') in ['movies', 'tvshows']]
        emby_items = emby_handler.get_emby_library_items(base_url=emby_url, api_key=emby_api_key, user_id=emby_user_id, library_ids=library_ids_to_scan, media_type_filter="Movie,Series")
        
        emby_tmdb_ids = {item['ProviderIds'].get('Tmdb') for item in emby_items if item.get('ProviderIds', {}).get('Tmdb')}
        logger.debug(f"手动刷新任务：已从 Emby 获取 {len(emby_tmdb_ids)} 个媒体ID。")

    except Exception as e:
        logger.error(f"手动刷新任务：在获取Emby媒体库信息时失败: {e}", exc_info=True)
        # 获取失败时，可以传递一个空集合，让扫描逻辑继续（但可能不准确），或者直接返回
        # 这里选择继续，让用户至少能更新TMDb信息

    # 现在，带着准备好的 emby_tmdb_ids 调用函数
    processor.run_full_scan_for_actor(subscription_id, emby_tmdb_ids)
# --- 演员订阅 ---
def task_process_actor_subscriptions(processor: ActorSubscriptionProcessor):
    """【新】后台任务：执行所有启用的演员订阅扫描。"""
    processor.run_scheduled_task(update_status_callback=update_status_from_thread)
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
        # 这里的 update_status_from_thread 是你项目中用于更新UI的函数
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
# ★★★ 执行数据库导入的后台任务 ★★★
def task_import_database(processor, file_content: str, tables_to_import: list, import_mode: str):
    """
    【后台任务 V12 - 最终完整正确版】
    - 修正了所有摘要日志的收集和打印逻辑，确保在正确的循环层级执行，每个表只生成一条摘要。
    """
    task_name = f"数据库导入 ({import_mode}模式)"
    logger.info(f"后台任务开始：{task_name}，处理表: {tables_to_import}。")
    update_status_from_thread(0, "准备开始导入...")
    
    AUTOINCREMENT_KEYS_TO_IGNORE = ['map_id', 'id']
    TRANSLATION_SOURCE_PRIORITY = {'manual': 2, 'openai': 1, 'zhipuai': 1, 'gemini': 1}
    
    summary_lines = []

    TABLE_TRANSLATIONS = {
        'person_identity_map': '演员映射表',
        'ActorMetadata': '演员元数据',
        'translation_cache': '翻译缓存',
        'watchlist': '智能追剧列表',
        'actor_subscriptions': '演员订阅配置',
        'tracked_actor_media': '已追踪的演员作品',
        'collections_info': '电影合集信息',
        'processed_log': '已处理列表',
        'failed_log': '待复核列表',
        'users': '用户账户',
    }

    try:
        backup = json.loads(file_content)
        backup_data = backup.get("data", {})
        stop_event = processor.get_stop_event()

        for table_name in tables_to_import:
            if table_name not in backup_data:
                logger.warning(f"请求恢复的表 '{table_name}' 在备份文件中不存在，将跳过。")

        with get_central_db_connection(config_manager.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            logger.info("数据库事务已开始。")

            try:
                # 外层循环：遍历所有要处理的表
                for table_name in tables_to_import:
                    cn_name = TABLE_TRANSLATIONS.get(table_name, table_name)
                    if stop_event and stop_event.is_set():
                        logger.info("导入任务被用户中止。")
                        break
                    
                    table_data = backup_data.get(table_name, [])
                    if not table_data:
                        logger.debug(f"表 '{cn_name}' 在备份中没有数据，跳过。")
                        summary_lines.append(f"  - 表 '{cn_name}': 跳过 (备份中无数据)。")
                        continue

                    # --- 特殊处理 person_identity_map ---
                    if table_name == 'person_identity_map' and import_mode == 'merge':
                        cn_name = TABLE_TRANSLATIONS.get(table_name, table_name)
                        logger.info(f"模式[共享合并]: 正在为 '{cn_name}' 表执行合并策略...")
                        
                        cursor.execute("SELECT * FROM person_identity_map")
                        local_rows = cursor.fetchall()
                        id_to_local_row = {row['map_id']: dict(row) for row in local_rows}
                        
                        # --- 阶段 A: 在内存中计算最终合并方案 ---
                        inserts, simple_updates, complex_merges = [], [], []
                        
                        live_tmdb_map = {row['tmdb_person_id']: row['map_id'] for row in local_rows if row['tmdb_person_id']}
                        live_emby_map = {row['emby_person_id']: row['map_id'] for row in local_rows if row['emby_person_id']}
                        live_imdb_map = {row['imdb_id']: row['map_id'] for row in local_rows if row['imdb_id']}
                        live_douban_map = {row['douban_celebrity_id']: row['map_id'] for row in local_rows if row['douban_celebrity_id']}

                        for backup_row in table_data:
                            matched_map_ids = set()
                            for key, lookup_map in [('tmdb_person_id', live_tmdb_map), ('emby_person_id', live_emby_map), ('imdb_id', live_imdb_map), ('douban_celebrity_id', live_douban_map)]:
                                backup_id = backup_row.get(key)
                                if backup_id and backup_id in lookup_map:
                                    matched_map_ids.add(lookup_map[backup_id])
                            
                            if not matched_map_ids:
                                inserts.append(backup_row)
                            elif len(matched_map_ids) == 1:
                                survivor_id = matched_map_ids.pop()
                                consolidated_row = id_to_local_row[survivor_id].copy()
                                needs_update = False
                                for key in backup_row:
                                    if backup_row.get(key) and not consolidated_row.get(key):
                                        consolidated_row[key] = backup_row[key]
                                        needs_update = True
                                if needs_update:
                                    simple_updates.append(consolidated_row)
                            else:
                                survivor_id = min(matched_map_ids)
                                victim_ids = list(matched_map_ids - {survivor_id})
                                complex_merges.append({'survivor_id': survivor_id, 'victim_ids': victim_ids, 'backup_row': backup_row})
                                # 更新动态查找字典，将牺牲者的ID重定向到幸存者
                                for vid in victim_ids:
                                    victim_row = id_to_local_row[vid]
                                    for key, lookup_map in [('tmdb_person_id', live_tmdb_map), ('emby_person_id', live_emby_map), ('imdb_id', live_imdb_map), ('douban_celebrity_id', live_douban_map)]:
                                        if victim_row.get(key) and victim_row[key] in lookup_map:
                                            lookup_map[victim_row[key]] = survivor_id

                        # --- 阶段 B: 根据计算出的最终方案，执行数据库操作 ---
                        
                        # 1. 逐个处理最危险的复杂合并
                        processed_complex_merges = 0
                        deleted_from_complex = 0
                        for merge_case in complex_merges:
                            survivor_id = merge_case['survivor_id']
                            victim_ids = merge_case['victim_ids']
                            backup_row = merge_case['backup_row']
                            
                            # 重新获取最新的幸存者数据
                            cursor.execute("SELECT * FROM person_identity_map WHERE map_id = ?", (survivor_id,))
                            survivor_row = dict(cursor.fetchone())
                            
                            all_sources = [id_to_local_row[vid] for vid in victim_ids] + [backup_row]
                            for source_row in all_sources:
                                for key in source_row:
                                    if source_row.get(key) and not survivor_row.get(key):
                                        survivor_row[key] = source_row[key]
                            
                            # 腾位 -> 入住 -> 清理
                            sql_clear = "UPDATE person_identity_map SET tmdb_person_id=NULL, emby_person_id=NULL, imdb_id=NULL, douban_celebrity_id=NULL WHERE map_id = ?"
                            cursor.executemany(sql_clear, [(vid,) for vid in victim_ids])
                            
                            cols = [c for c in survivor_row.keys() if c not in AUTOINCREMENT_KEYS_TO_IGNORE]
                            set_str = ", ".join([f'"{c}" = ?' for c in cols])
                            sql_update = f"UPDATE person_identity_map SET {set_str} WHERE map_id = ?"
                            data = [survivor_row.get(c) for c in cols] + [survivor_id]
                            cursor.execute(sql_update, tuple(data))
                            
                            cursor.executemany("DELETE FROM person_identity_map WHERE map_id = ?", [(vid,) for vid in victim_ids])
                            processed_complex_merges += 1
                            deleted_from_complex += len(victim_ids)

                        # 2. 批量处理简单的增补更新
                        if simple_updates:
                            unique_updates = {row['map_id']: row for row in simple_updates}.values()
                            sql_update = "UPDATE person_identity_map SET primary_name = ?, tmdb_person_id = ?, imdb_id = ?, douban_celebrity_id = ? WHERE map_id = ?"
                            data = [(r.get('primary_name'), r.get('tmdb_person_id'), r.get('imdb_id'), r.get('douban_celebrity_id'), r['map_id']) for r in unique_updates]
                            cursor.executemany(sql_update, data)

                        # 3. 批量处理全新的插入
                        if inserts:
                            sql_insert = "INSERT INTO person_identity_map (primary_name, tmdb_person_id, imdb_id, douban_celebrity_id) VALUES (?, ?, ?, ?)"
                            data = [(r.get('primary_name'), r.get('tmdb_person_id'), r.get('imdb_id'), r.get('douban_celebrity_id')) for r in inserts]
                            cursor.executemany(sql_insert, data)
                        
                        summary_lines.append(f"  - 表 '{cn_name}': 新增 {len(inserts)} 条, 简单增补 {len(simple_updates)} 条, 复杂合并 {processed_complex_merges} 组 (清理冗余 {deleted_from_complex} 条)。")

                    # --- 特殊处理 translation_cache ---
                    elif table_name == 'translation_cache' and import_mode == 'merge':
                        cn_name = TABLE_TRANSLATIONS.get(table_name, table_name)
                        logger.info(f"模式[共享合并]: 正在为 '{cn_name}' 表执行基于优先级的合并策略...")
                        cursor.execute("SELECT original_text, translated_text, engine_used FROM translation_cache")
                        local_cache_data = {row['original_text']: {'text': row['translated_text'], 'engine': row['engine_used'], 'priority': TRANSLATION_SOURCE_PRIORITY.get(row['engine_used'], 0)} for row in cursor.fetchall()}
                        inserts, updates, kept = [], [], 0
                        for backup_row in table_data:
                            original_text = backup_row.get('original_text')
                            if not original_text: continue
                            backup_engine = backup_row.get('engine_used')
                            backup_priority = TRANSLATION_SOURCE_PRIORITY.get(backup_engine, 0)
                            if original_text not in local_cache_data:
                                inserts.append(backup_row)
                            else:
                                local_data = local_cache_data[original_text]
                                if backup_priority > local_data['priority']:
                                    updates.append(backup_row)
                                    logger.trace(f"  -> 冲突: '{original_text}'. 备份源({backup_engine}|P{backup_priority}) > 本地源({local_data['engine']}|P{local_data['priority']}). [决策: 更新]")
                                else:
                                    kept += 1
                                    logger.trace(f"  -> 冲突: '{original_text}'. 本地源({local_data['engine']}|P{local_data['priority']}) >= 备份源({backup_engine}|P{backup_priority}). [决策: 保留]")
                        if inserts:
                            cols = list(inserts[0].keys()); col_str = ", ".join(f'"{c}"' for c in cols); val_ph = ", ".join(["?"] * len(cols))
                            sql = f"INSERT INTO translation_cache ({col_str}) VALUES ({val_ph})"
                            data = [[row.get(c) for c in cols] for row in inserts]
                            cursor.executemany(sql, data)
                        if updates:
                            cols = list(updates[0].keys()); col_str = ", ".join(f'"{c}"' for c in cols); val_ph = ", ".join(["?"] * len(cols))
                            sql = f"INSERT OR REPLACE INTO translation_cache ({col_str}) VALUES ({val_ph})"
                            data = [[row.get(c) for c in cols] for row in updates]
                            cursor.executemany(sql, data)
                        summary_lines.append(f"  - 表 '{cn_name}': 新增 {len(inserts)} 条, 更新 {len(updates)} 条, 保留本地 {kept} 条。")
                    
                    # --- 通用合并/覆盖逻辑 ---
                    else:
                        mode_str = "本地恢复" if import_mode == 'overwrite' else "共享合并"
                        logger.info(f"模式[{mode_str}]: 正在处理表 '{cn_name}'...")
                        if import_mode == 'overwrite':
                            cursor.execute(f"DELETE FROM {table_name};")
                        logical_key = TABLE_PRIMARY_KEYS.get(table_name)
                        if import_mode == 'merge' and not logical_key:
                            logger.warning(f"表 '{cn_name}' 未定义主键，跳过合并。")
                            summary_lines.append(f"  - 表 '{table_name}': 跳过 (未定义合并键)。")
                            continue
                        all_cols = list(table_data[0].keys())
                        cols_for_op = [c for c in all_cols if c not in AUTOINCREMENT_KEYS_TO_IGNORE]
                        col_str = ", ".join(f'"{c}"' for c in cols_for_op)
                        val_ph = ", ".join(["?"] * len(cols_for_op))
                        sql = ""
                        if import_mode == 'merge':
                            conflict_key_str = ""; logical_key_set = set()
                            if isinstance(logical_key, str):
                                conflict_key_str = logical_key; logical_key_set = {logical_key}
                            elif isinstance(logical_key, tuple):
                                conflict_key_str = ", ".join(logical_key); logical_key_set = set(logical_key)
                            update_cols = [c for c in cols_for_op if c not in logical_key_set]
                            update_str = ", ".join([f'"{col}" = excluded."{col}"' for col in update_cols])
                            sql = (f"INSERT INTO {table_name} ({col_str}) VALUES ({val_ph}) "
                                   f"ON CONFLICT({conflict_key_str}) DO UPDATE SET {update_str}")
                        else:
                            sql = f"INSERT INTO {table_name} ({col_str}) VALUES ({val_ph})"
                        data = [[row.get(c) for c in cols_for_op] for row in table_data]
                        cursor.executemany(sql, data)
                        if import_mode == 'overwrite':
                            summary_lines.append(f"  - 表 '{cn_name}': 清空并插入 {len(data)} 条。")
                        else:
                            summary_lines.append(f"  - 表 '{cn_name}': 合并处理了 {len(data)} 条。")

                # --- 打印统一的摘要报告 ---
                logger.info("="*11 + " 数据库导入摘要 " + "="*11)
                if not summary_lines:
                    logger.info("  -> 本次操作没有对任何表进行改动。")
                else:
                    for line in summary_lines:
                        logger.info(line)
                logger.info("="*36)

                if not (stop_event and stop_event.is_set()):
                    conn.commit()
                    logger.info("数据库事务已成功提交！所有选择的表已恢复。")
                    update_status_from_thread(100, "导入成功完成！")
                else:
                    conn.rollback()
                    logger.warning("任务被中止，数据库操作已回滚。")
                    update_status_from_thread(-1, "任务已中止，所有更改已回滚。")

            except Exception as e:
                conn.rollback()
                logger.error(f"在事务处理期间发生严重错误，操作已回滚: {e}", exc_info=True)
                update_status_from_thread(-1, f"数据库错误，操作已回滚: {e}")
                raise

    except Exception as e:
        logger.error(f"数据库恢复任务执行失败: {e}", exc_info=True)
        update_status_from_thread(-1, f"任务失败: {e}")
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
# ✨ 辅助函数，并发刷新合集使用
def _process_single_collection_concurrently(collection_data: dict, db_path: str, tmdb_api_key: str) -> dict:
    """
    【V2 - 状态增强版】
    在单个线程中处理单个合集的所有逻辑。
    - 为合集中的每一部电影标记状态: in_library, missing, unreleased, subscribed
    - 保留已有的 subscribed 状态
    """
    collection_id = collection_data['Id']
    collection_name = collection_data.get('Name', '')
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # 每个线程创建自己的数据库连接
    with get_central_db_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # 1. 读取历史状态，主要是为了获取哪些电影之前是 'subscribed'
        cursor.execute("SELECT missing_movies_json FROM collections_info WHERE emby_collection_id = ?", (collection_id,))
        row = cursor.fetchone()
        previous_movies_map = {}
        if row and row[0]:
            try:
                previous_movies = json.loads(row[0])
                # 创建一个以 tmdb_id 为键的字典，方便快速查找
                previous_movies_map = {str(m['tmdb_id']): m for m in previous_movies}
            except (json.JSONDecodeError, TypeError):
                pass

    # 2. 准备数据
    all_movies_with_status = []
    emby_movie_tmdb_ids = set(collection_data.get("ExistingMovieTmdbIds", []))
    in_library_count = len(emby_movie_tmdb_ids)
    status, has_missing = "ok", False

    provider_ids = collection_data.get("ProviderIds", {})
    tmdb_id = provider_ids.get("TmdbCollection") or provider_ids.get("TmdbCollectionId") or provider_ids.get("Tmdb")

    if not tmdb_id:
        status = "unlinked"
    else:
        details = tmdb_handler.get_collection_details_tmdb(int(tmdb_id), tmdb_api_key)
        if not details or "parts" not in details:
            status = "tmdb_error"
        else:
            # 3. 遍历TMDb合集中的所有电影，并确定它们各自的状态
            for movie in details.get("parts", []):
                movie_tmdb_id = str(movie.get("id"))
                title = movie.get("title", "")
                # 过滤掉一些不规范的数据
                if not movie.get("release_date") or not re.search(r'[\u4e00-\u9fa5]', title):
                    continue

                movie_status = "unknown" # 默认状态
                
                # --- 状态判断优先级 ---
                # 1. 已入库？ (最高优先级)
                if movie_tmdb_id in emby_movie_tmdb_ids:
                    movie_status = "in_library"
                # 2. 未上映？
                elif movie.get("release_date", '') > today_str:
                    movie_status = "unreleased"
                # 3. 之前是否已订阅？ (如果未入库，则保留订阅状态)
                elif previous_movies_map.get(movie_tmdb_id, {}).get('status') == 'subscribed':
                    movie_status = "subscribed"
                # 4. 都不是，那就是缺失
                else:
                    movie_status = "missing"

                all_movies_with_status.append({
                    "tmdb_id": movie_tmdb_id, 
                    "title": title, 
                    "release_date": movie.get("release_date"), 
                    "poster_path": movie.get("poster_path"), 
                    "status": movie_status
                })
            
            # 4. 根据最终的电影状态列表，确定整个合集的状态
            if any(m['status'] == 'missing' for m in all_movies_with_status):
                has_missing = True
                status = "has_missing"
    
    image_tag = collection_data.get("ImageTags", {}).get("Primary")
    poster_path = f"/Items/{collection_id}/Images/Primary?tag={image_tag}" if image_tag else None

    # 5. 将所有结果打包返回
    return {
        "emby_collection_id": collection_id, "name": collection_name, "tmdb_collection_id": tmdb_id, 
        "status": status, "has_missing": has_missing, 
        # ★★★ 核心改变：现在存储的是包含所有状态的完整列表
        "missing_movies_json": json.dumps(all_movies_with_status), 
        "last_checked_at": time.time(), "poster_path": poster_path, "in_library_count": in_library_count
    }
# ★★★ 刷新合集的后台任务函数 ★★★
def task_refresh_collections(processor: MediaProcessor):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    update_status_from_thread(0, "正在获取 Emby 合集列表...")
    try:
        emby_collections = emby_handler.get_all_collections_with_items(
            base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id
        )
        if emby_collections is None: raise RuntimeError("从 Emby 获取合集列表失败")

        total = len(emby_collections)
        update_status_from_thread(5, f"共找到 {total} 个合集，准备开始并发处理...")

        # 清理数据库中已不存在的合集
        with get_central_db_connection(config_manager.DB_PATH) as conn:
            cursor = conn.cursor()
            emby_current_ids = {c['Id'] for c in emby_collections}
            cursor.execute("SELECT emby_collection_id FROM collections_info")
            db_known_ids = {row[0] for row in cursor.fetchall()}
            deleted_ids = db_known_ids - emby_current_ids
            if deleted_ids:
                cursor.executemany("DELETE FROM collections_info WHERE emby_collection_id = ?", [(id,) for id in deleted_ids])
            conn.commit()

        tmdb_api_key = config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_TMDB_API_KEY)
        if not tmdb_api_key: raise RuntimeError("未配置 TMDb API Key")

        processed_count = 0
        all_results = []
        
        # ✨ 核心修改：使用线程池进行并发处理
        with ThreadPoolExecutor(max_workers=5) as executor:
            # 提交所有任务
            futures = {executor.submit(_process_single_collection_concurrently, collection, config_manager.DB_PATH, tmdb_api_key): collection for collection in emby_collections}
            
            # 实时获取已完成的结果并更新进度条
            for future in as_completed(futures):
                if processor.is_stop_requested():
                    # 如果用户请求停止，我们可以尝试取消未开始的任务
                    for f in futures: f.cancel()
                    break
                
                collection_name = futures[future].get('Name', '未知合集')
                try:
                    result = future.result()
                    all_results.append(result)
                except Exception as e:
                    logger.error(f"处理合集 '{collection_name}' 时线程内发生错误: {e}", exc_info=True)
                
                processed_count += 1
                progress = 10 + int((processed_count / total) * 90)
                update_status_from_thread(progress, f"处理中: {collection_name[:20]}... ({processed_count}/{total})")

        if processor.is_stop_requested():
            logger.warning("任务被用户中断，部分数据可能未被处理。")
            # 即使被中断，我们依然保存已成功处理的结果
        
        # ✨ 所有并发任务完成后，在主线程中安全地、一次性地写入数据库
        if all_results:
            logger.info(f"并发处理完成，准备将 {len(all_results)} 条结果写入数据库...")
            with get_central_db_connection(config_manager.DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN TRANSACTION;")
                try:
                    cursor.executemany("""
                        INSERT OR REPLACE INTO collections_info 
                        (emby_collection_id, name, tmdb_collection_id, status, has_missing, missing_movies_json, last_checked_at, poster_path, in_library_count)
                        VALUES (:emby_collection_id, :name, :tmdb_collection_id, :status, :has_missing, :missing_movies_json, :last_checked_at, :poster_path, :in_library_count)
                    """, all_results)
                    conn.commit()
                    logger.info("数据库写入成功！")
                except Exception as e_db:
                    logger.error(f"数据库批量写入时发生错误: {e_db}", exc_info=True)
                    conn.rollback()
        
    except Exception as e:
        logger.error(f"刷新合集任务失败: {e}", exc_info=True)
        update_status_from_thread(-1, f"错误: {e}")
# ★★★ 带智能预判的自动订阅任务 ★★★
def task_auto_subscribe(processor: MediaProcessor):
    update_status_from_thread(0, "正在启动智能订阅任务...")
    
    if not config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_AUTOSUB_ENABLED):
        logger.info("智能订阅总开关未开启，任务跳过。")
        update_status_from_thread(100, "任务跳过：总开关未开启")
        return

    try:
        today = date.today()
        update_status_from_thread(10, f"智能订阅已启动...")
        successfully_subscribed_items = []

        with get_central_db_connection(config_manager.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # --- 1. 处理电影合集 ---
            update_status_from_thread(20, "正在检查缺失的电影...")
            
            sql_query_movies = "SELECT * FROM collections_info WHERE status = 'has_missing' AND missing_movies_json IS NOT NULL AND missing_movies_json != '[]'"
            logger.debug(f"【智能订阅-电影】执行查询: {sql_query_movies}")
            cursor.execute(sql_query_movies)
            collections_to_check = cursor.fetchall()

            logger.info(f"【智能订阅-电影】从数据库找到 {len(collections_to_check)} 个有缺失影片的电影合集需要检查。")

            for collection in collections_to_check:
                if processor.is_stop_requested(): break
                
                collection_name = collection['name']
                logger.info(f"【智能订阅-电影】>>> 正在检查合集: 《{collection_name}》")

                movies_to_keep = []
                all_missing_movies = json.loads(collection['missing_movies_json'])
                movies_changed = False
                for movie in all_missing_movies:
                    if processor.is_stop_requested(): break
                    
                    movie_title = movie.get('title', '未知电影')
                    movie_status = movie.get('status', 'unknown')

                    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                    # ★★★  核心逻辑修改：在这里处理 ignored 状态，打破死循环！ ★★★
                    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                    if movie_status == 'ignored':
                        logger.info(f"【智能订阅-电影】   -> 影片《{movie_title}》已被用户忽略，跳过。")
                        movies_to_keep.append(movie) # 保持 ignored 状态，下次不再处理
                        continue
                    
                    if movie_status == 'missing':
                        release_date_str = movie.get('release_date')
                        if release_date_str:
                            release_date_str = release_date_str.strip()
                        
                        if not release_date_str:
                            logger.warning(f"【智能订阅-电影】   -> 影片《{movie_title}》缺少上映日期，无法判断，跳过。")
                            movies_to_keep.append(movie)
                            continue
                        
                        try:
                            release_date = datetime.strptime(release_date_str, '%Y-%m-%d').date()
                        except (ValueError, TypeError):
                            logger.warning(f"【智能订阅-电影】   -> 影片《{movie_title}》的上映日期 '{release_date_str}' 格式无效，跳过。")
                            movies_to_keep.append(movie)
                            continue

                        if release_date <= today:
                            logger.info(f"【智能订阅-电影】   -> 影片《{movie_title}》(上映日期: {release_date}) 已上映，符合订阅条件，正在提交...")
                            
                            try:
                                success = moviepilot_handler.subscribe_movie_to_moviepilot(movie, config_manager.APP_CONFIG)
                                if success:
                                    logger.info(f"【智能订阅-电影】      -> 订阅成功！")
                                    successfully_subscribed_items.append(f"电影《{movie['title']}》")
                                    movies_changed = True # 订阅成功后，从缺失列表移除
                                else:
                                    logger.error(f"【智能订阅-电影】      -> MoviePilot报告订阅失败！将保留在缺失列表中。")
                                    movies_to_keep.append(movie)
                            except Exception as e:
                                logger.error(f"【智能订阅-电影】      -> 提交订阅到MoviePilot时发生内部错误: {e}", exc_info=True)
                                movies_to_keep.append(movie)
                        else:
                            logger.info(f"【智能订阅-电影】   -> 影片《{movie_title}》(上映日期: {release_date}) 尚未上映，跳过订阅。")
                            movies_to_keep.append(movie)
                    else:
                        status_translation = {
                            'unreleased': '未上映',
                            # 可以在这里添加更多翻译
                            'unknown': '未知状态'
                        }
                        # 使用 .get() 方法，如果找不到翻译，则显示原始状态，保证程序不会出错
                        display_status = status_translation.get(movie_status, movie_status)
                        # 此处会处理 'unreleased' 等其他所有状态
                        logger.info(f"【智能订阅-电影】   -> 影片《{movie_title}》因状态为 '{display_status}'，本次跳过订阅检查。")
                        movies_to_keep.append(movie)
                
                # 只有在订阅成功导致列表变化时才更新数据库
                if movies_changed:
                    # 重新生成缺失电影的JSON，只包含未被成功订阅的
                    new_missing_json = json.dumps(movies_to_keep)
                    # 如果更新后列表为空，可以顺便更新合集的状态
                    new_status = 'ok' if not movies_to_keep else 'has_missing'
                    cursor.execute("UPDATE collections_info SET missing_movies_json = ?, status = ? WHERE emby_collection_id = ?", (new_missing_json, new_status, collection['emby_collection_id']))

            # --- 2. 处理剧集 ---
            if not processor.is_stop_requested():
                update_status_from_thread(60, "正在检查缺失的剧集...")
                
                sql_query = "SELECT * FROM watchlist WHERE status IN ('Watching', 'Paused') AND missing_info_json IS NOT NULL AND missing_info_json != '[]'"
                logger.debug(f"【智能订阅-剧集】执行查询: {sql_query}")
                cursor.execute(sql_query)
                series_to_check = cursor.fetchall()
                
                logger.info(f"【智能订阅-剧集】从数据库找到 {len(series_to_check)} 部状态为'在追'或'暂停'且有缺失信息的剧集需要检查。")

                for series in series_to_check:
                    if processor.is_stop_requested(): break
                    
                    series_name = series['item_name']
                    logger.info(f"【智能订阅-剧集】>>> 正在检查: 《{series_name}》")
                    
                    try:
                        missing_info = json.loads(series['missing_info_json'])
                        missing_seasons = missing_info.get('missing_seasons', [])
                        
                        if not missing_seasons:
                            logger.info(f"【智能订阅-剧集】   -> 《{series_name}》没有记录在案的缺失季(missing_seasons为空)，跳过。")
                            continue

                        seasons_to_keep = []
                        seasons_changed = False
                        for season in missing_seasons:
                            if processor.is_stop_requested(): break
                            
                            season_num = season.get('season_number')
                            air_date_str = season.get('air_date')
                            if air_date_str:
                                air_date_str = air_date_str.strip()
                            
                            if not air_date_str:
                                logger.warning(f"【智能订阅-剧集】   -> 《{series_name}》第 {season_num} 季缺少播出日期(air_date)，无法判断，跳过。")
                                seasons_to_keep.append(season)
                                continue
                            
                            try:
                                season_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()
                            except (ValueError, TypeError):
                                logger.warning(f"【智能订阅-剧集】   -> 《{series_name}》第 {season_num} 季的播出日期 '{air_date_str}' 格式无效，跳过。")
                                seasons_to_keep.append(season)
                                continue

                            if season_date <= today:
                                logger.info(f"【智能订阅-剧集】   -> 《{series_name}》第 {season_num} 季 (播出日期: {season_date}) 已播出，符合订阅条件，正在提交...")
                                try:
                                    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                                    # ★★★  核心修复：剧集订阅也需要传递 config_manager.APP_CONFIG！ ★★★
                                    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                                    success = moviepilot_handler.subscribe_series_to_moviepilot(dict(series), season['season_number'], config_manager.APP_CONFIG)
                                    if success:
                                        logger.info(f"【智能订阅-剧集】      -> 订阅成功！")
                                        successfully_subscribed_items.append(f"《{series['item_name']}》第 {season['season_number']} 季")
                                        seasons_changed = True
                                    else:
                                        logger.error(f"【智能订阅-剧集】      -> MoviePilot报告订阅失败！将保留在缺失列表中。")
                                        seasons_to_keep.append(season)
                                except Exception as e:
                                    logger.error(f"【智能订阅-剧集】      -> 提交订阅到MoviePilot时发生内部错误: {e}", exc_info=True)
                                    seasons_to_keep.append(season)
                            else:
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
        # 这部分是你原有的核心修复逻辑，保持不变
        id_to_process = original_item_id
        type_to_process = original_item_type

        if original_item_type == "Episode":
            # ... (你原有的向上查找剧集ID的逻辑) ...
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
        
        success = task_manager.submit_task(
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
        success = task_manager.submit_task(
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
    
    status_data = task_manager.get_task_status()
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

    task_manager.clear_task_queue()
    task_manager.stop_task_worker()

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
    config = config_manager.APP_CONFIG
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
        with get_central_db_connection(config_manager.DB_PATH) as conn:
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
        with get_central_db_connection(config_manager.DB_PATH) as conn:
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
        current_config = config_manager.APP_CONFIG 
        
        if current_config:
            current_config['emby_server_id'] = EMBY_SERVER_ID
            logger.trace(f"API /api/config (GET): 成功加载并返回配置。")
            return jsonify(current_config)
        else:
            logger.error(f"API /api/config (GET): config_manager.APP_CONFIG 为空或未初始化。")
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
        save_config_and_reload(new_config_data)  
        
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

    try:
        # +++ 核心修改：调用 db_handler 的高级函数 +++
        items_to_review, total_matching_items = db_handler.get_review_items_paginated(
            db_path=config_manager.DB_PATH,
            page=page,
            per_page=per_page,
            query_filter=query_filter
        )
        
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

    except Exception as e:
        logger.error(f"API /api/review_items 获取数据失败: {e}", exc_info=True)
        return jsonify({"error": "获取待复核列表时发生服务器内部错误"}), 500
@app.route('/api/actions/mark_item_processed/<item_id>', methods=['POST'])
@task_lock_required
def api_mark_item_processed(item_id):
    if task_manager.is_task_running():
        return jsonify({"error": "后台有长时间任务正在运行，请稍后再试。"}), 409
    
    try:
        # +++ 核心修改：调用 db_handler 的高级函数 +++
        success = db_handler.mark_review_item_as_processed(
            db_path=config_manager.DB_PATH,
            item_id=item_id
        )
        
        if success:
            return jsonify({"message": f"项目 {item_id} 已成功标记为已处理。"}), 200
        else:
            return jsonify({"error": f"未在待复核列表中找到项目 {item_id}。"}), 404

    except Exception as e:
        logger.error(f"标记项目 {item_id} 为已处理时失败: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
# ✨✨✨ 清空待复核列表（并全部标记为已处理）的 API ✨✨✨
@app.route('/api/actions/clear_review_items', methods=['POST'])
@task_lock_required
def api_clear_review_items_revised():
    logger.info("API: 收到清空所有待复核项目并标记为已处理的请求。")
    
    try:
        # +++ 核心修改：调用 db_handler 的高级函数 +++
        processed_count = db_handler.clear_all_review_items(config_manager.DB_PATH)
        
        if processed_count > 0:
            message = f"操作成功！已将 {processed_count} 个项目从待复核列表移至已处理列表。"
        else:
            message = "操作完成，待复核列表本就是空的。"
            
        logger.info(message)
        return jsonify({"message": message}), 200

    except RuntimeError as e:
        # 捕获我们自己定义的、用于数据不一致的特定错误
        logger.error(f"清空待复核列表时发生数据一致性错误: {e}")
        return jsonify({"error": "服务器在处理数据时检测到不一致性，操作已自动取消以防止数据丢失。"}), 500
    except Exception as e:
        logger.error(f"清空并标记待复核列表时发生未知异常: {e}", exc_info=True)
        return jsonify({"error": "服务器在处理数据库时发生内部错误"}), 500
# --- 前端全量扫描接口 ---   
@app.route('/api/trigger_full_scan', methods=['POST'])
@processor_ready_required # <-- 检查处理器是否就绪
@task_lock_required      # <-- 检查任务锁
def api_handle_trigger_full_scan():
    logger.debug("API Endpoint: Received request to trigger full scan.")
    # 从 FormData 获取数据
    # 注意：前端发送的是 FormData，所以我们用 request.form
    force_reprocess = request.form.get('force_reprocess_all') == 'on'
    

    # ★★★ 你的完美逻辑在这里实现 ★★★
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
    process_episodes = config_manager.APP_CONFIG.get('process_episodes', True)
    
    # 提交纯粹的扫描任务
    success = task_manager.submit_task(
        task_process_full_library,
        action_message,
        process_episodes
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
        
        success = task_manager.submit_task(
            task_sync_person_map,
            "同步演员映射表"
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

    success = task_manager.submit_task(
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
        with get_central_db_connection(config_manager.DB_PATH) as conn:
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
# ★★★ 获取数据库中所有用户表的列表 ★★★
@app.route('/api/database/tables', methods=['GET'])
@login_required
def api_get_db_tables():
    """
    获取数据库中所有用户表的名称列表。
    排除 sqlite_ 开头的系统表。
    """
    try:
        with get_central_db_connection(config_manager.DB_PATH) as conn:
            cursor = conn.cursor()
            # 查询 sqlite_master 表来获取所有表名
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cursor.fetchall()]
            logger.info(f"API: 成功获取到数据库表列表: {tables}")
            return jsonify(tables)
    except Exception as e:
        logger.error(f"获取数据库表列表时出错: {e}", exc_info=True)
        return jsonify({"error": "无法获取数据库表列表"}), 500
# 数据库表结构。
TABLE_PRIMARY_KEYS = {
    "person_identity_map": "tmdb_person_id",
    "ActorMetadata": "tmdb_id",
    "translation_cache": "original_text",
    "collections_info": "emby_collection_id",
    "watchlist": "item_id",
    "actor_subscriptions": "tmdb_person_id",
    "tracked_actor_media": ("subscription_id", "tmdb_media_id"),
    "processed_log": "item_id",
    "failed_log": "item_id",
    "users": "username",
}
# ★★★ 通用数据库表导出  ★★★
@app.route('/api/database/export', methods=['POST'])
@login_required
def api_export_database():
    """
    【通用版】根据请求中指定的表名列表，导出一个包含这些表数据的JSON文件。
    """
    try:
        tables_to_export = request.json.get('tables')
        if not tables_to_export or not isinstance(tables_to_export, list):
            return jsonify({"error": "请求体中必须包含一个 'tables' 数组"}), 400

        logger.info(f"API: 收到数据库导出请求，目标表: {tables_to_export}")

        backup_data = {
            "metadata": {
                "export_date": datetime.utcnow().isoformat() + "Z",
                "app_version": constants.APP_VERSION,
                "source_emby_server_id": EMBY_SERVER_ID,
                "tables": tables_to_export
            },
            "data": {}
        }

        with get_central_db_connection(config_manager.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            for table_name in tables_to_export:
                # 安全性检查：确保表名是合法的
                if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
                     logger.warning(f"跳过无效的表名: {table_name}")
                     continue
                
                logger.debug(f"正在导出表: {table_name}...")
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()
                backup_data["data"][table_name] = [dict(row) for row in rows]
                logger.debug(f"表 {table_name} 导出完成，共 {len(rows)} 行。")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"database_backup_{timestamp}.json"
        
        json_output = json.dumps(backup_data, indent=2, ensure_ascii=False)

        response = Response(json_output, mimetype='application/json; charset=utf-8')
        response.headers.set("Content-Disposition", "attachment", filename=filename)
        return response

    except Exception as e:
        logger.error(f"导出数据库时发生错误: {e}", exc_info=True)
        return jsonify({"error": f"导出时发生服务器错误: {e}"}), 500
# ★★★ 通用数据库表导入 ★★★
@app.route('/api/database/import', methods=['POST'])
@login_required
@task_lock_required
def api_import_database():
    """
    【通用队列版】接收备份文件、要导入的表名列表以及导入模式，
    并提交一个后台任务来处理恢复。
    """
    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400
    
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.json'):
        return jsonify({"error": "未选择文件或文件类型必须是 .json"}), 400

    tables_to_import_str = request.form.get('tables')
    if not tables_to_import_str:
        return jsonify({"error": "必须通过 'tables' 字段指定要导入的表"}), 400
    tables_to_import = [table.strip() for table in tables_to_import_str.split(',')]

    # ★ 关键：从表单获取导入模式，默认为 'merge'，更安全 ★
    import_mode = request.form.get('mode', 'merge').lower()
    if import_mode not in ['overwrite', 'merge']:
        return jsonify({"error": "无效的导入模式。只支持 'overwrite' 或 'merge'"}), 400
    
    mode_translations = {
        'overwrite': '本地恢复模式',
        'merge': '共享合并模式',
    }
    # 使用 .get() 以防万一，如果找不到就用回英文原名
    import_mode_cn = mode_translations.get(import_mode, import_mode)

    try:
        file_content = file.stream.read().decode("utf-8-sig")
        # ▼▼▼ 新增的安全校验逻辑 ▼▼▼
        backup_json = json.loads(file_content)
        backup_metadata = backup_json.get("metadata", {})
        backup_server_id = backup_metadata.get("source_emby_server_id")

        # 只对最危险的“本地恢复”模式进行强制校验
        if import_mode == 'overwrite':
            # 检查1：备份文件必须有ID指纹
            if not backup_server_id:
                error_msg = "此备份文件缺少来源服务器ID，为安全起见，禁止使用“本地恢复”模式导入，这通常意味着它是一个旧版备份，请使用“共享合并”模式。"
                logger.warning(f"禁止导入: {error_msg}")
                return jsonify({"error": error_msg}), 403 # 403 Forbidden

            # 检查2：当前服务器必须能获取到ID
            current_server_id = EMBY_SERVER_ID
            if not current_server_id:
                error_msg = "无法获取当前Emby服务器的ID，可能连接已断开。为安全起见，暂时禁止使用“本地恢复”模式。"
                logger.warning(f"禁止导入: {error_msg}")
                return jsonify({"error": error_msg}), 503 # 503 Service Unavailable

            # 检查3：两个ID必须完全匹配
            if backup_server_id != current_server_id:
                error_msg = (f"服务器ID不匹配！此备份来自另一个Emby服务器，"
                           "直接使用“本地恢复”会造成数据严重混乱。操作已禁止。\n\n"
                           f"备份来源ID: ...{backup_server_id[-12:]}\n"
                           f"当前服务器ID: ...{current_server_id[-12:]}\n\n"
                           "如果你确实想合并数据，请改用“共享合并”模式。")
                logger.warning(f"禁止导入: {error_msg}")
                return jsonify({"error": error_msg}), 403 # 403 Forbidden
        # ▲▲▲ 安全校验逻辑结束 ▲▲▲
        logger.trace(f"已接收上传的备份文件 '{file.filename}'，将以 '{import_mode_cn}' 模式导入表: {tables_to_import}")

        success = task_manager.submit_task(
            task_import_database,  # ★ 调用新的后台任务函数
            f"以 {import_mode_cn} 模式恢复数据库表",
            # 传递任务所需的所有参数
            file_content=file_content,
            tables_to_import=tables_to_import,
            import_mode=import_mode
        )
        
        return jsonify({"message": f"文件上传成功，已提交后台任务以 '{import_mode_cn}' 模式恢复 {len(tables_to_import)} 个表。"}), 202

    except Exception as e:
        logger.error(f"处理数据库导入请求时发生错误: {e}", exc_info=True)
        return jsonify({"error": "处理上传文件时发生服务器错误"}), 500
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
        current_config = config_manager.APP_CONFIG
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
    success = task_manager.submit_task(
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
    success = task_manager.submit_task(
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
    success = task_manager.submit_task(
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
        success = task_manager.submit_task(
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
@task_lock_required
def api_trigger_task_now(task_identifier: str):
    """
    一个通用的API端点，用于立即触发指定的后台任务。
    它会响应前端发送的 /api/tasks/trigger/full-scan, /api/tasks/trigger/sync-person-map 等请求。
    """

    # 2. 从任务注册表中查找任务
    task_info = TASK_REGISTRY.get(task_identifier)
    if not task_info:
        return jsonify({
            "status": "error",
            "message": f"未知的任务标识符: {task_identifier}"
        }), 404 # Not Found

    task_function, task_name = task_info
    
    # 3. 提交任务到队列
    #    使用你现有的 submit_task_to_queue 函数
    #    对于需要额外参数的任务（如全量扫描），我们需要特殊处理
    kwargs = {}
    if task_identifier == 'full-scan':
        # 我们可以从请求体中获取参数，或者使用默认值
        # 这允许前端未来可以传递 '强制重处理' 等选项
        data = request.get_json(silent=True) or {}
        kwargs['process_episodes'] = data.get('process_episodes', True)
        # 假设 task_process_full_library 接受 process_episodes 参数
    
    success = task_manager.submit_task(
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
    
# +++ 在应用对象上注册所有蓝图 +++
app.register_blueprint(watchlist_bp)
app.register_blueprint(collections_bp)
app.register_blueprint(actor_subscriptions_bp)
app.register_blueprint(logs_bp)
if __name__ == '__main__':
    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}")
    
    # 1. ★★★ 首先，加载配置，让 config_manager.APP_CONFIG 获得真实的值 ★★★
    config_manager.load_config()
    
    # 2. ★★★ 然后，再执行依赖于配置的日志设置 ★★★
    # --- 日志文件处理器配置 ---
    config_manager.LOG_DIRECTORY = os.path.join(config_manager.PERSISTENT_DATA_PATH, 'logs')

    # 从现在已经有值的 config_manager.APP_CONFIG 中获取配置
    raw_size = config_manager.APP_CONFIG.get(
        constants.CONFIG_OPTION_LOG_ROTATION_SIZE_MB, 
        constants.DEFAULT_LOG_ROTATION_SIZE_MB
    )
    try:
        log_size = int(raw_size)
    except (ValueError, TypeError):
        log_size = constants.DEFAULT_LOG_ROTATION_SIZE_MB

    raw_backups = config_manager.APP_CONFIG.get(
        constants.CONFIG_OPTION_LOG_ROTATION_BACKUPS, 
        constants.DEFAULT_LOG_ROTATION_BACKUPS
    )
    try:
        log_backups = int(raw_backups)
    except (ValueError, TypeError):
        log_backups = constants.DEFAULT_LOG_ROTATION_BACKUPS

    # 将正确的配置注入日志系统
    add_file_handler(
        log_directory=config_manager.LOG_DIRECTORY,
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
    task_manager.start_task_worker_if_not_running()
    
    # 7. 设置定时任务 (它会依赖全局配置和实例)
    if not scheduler.running:
        scheduler.start()
    setup_scheduled_tasks()
    
    # 8. 运行 Flask 应用
    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=True, use_reloader=False)

# # --- 主程序入口结束 ---