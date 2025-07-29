# web_app.py
import os
import sqlite3
from datetime import datetime
from actor_sync_handler import UnifiedSyncHandler
from db_handler import ActorDBManager
import emby_handler
import moviepilot_handler
import utils
from tasks import *
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
from datetime import datetime
import requests
import tmdb_handler
import task_manager
from douban import DoubanApi
from tasks import get_task_registry 
from typing import Optional, Dict, Any, List, Tuple, Union # 确保 List 被导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
import atexit # 用于应用退出处理
from core_processor import MediaProcessor
from actor_subscription_processor import ActorSubscriptionProcessor
from werkzeug.security import generate_password_hash, check_password_hash
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
from routes.database_admin import db_admin_bp
from routes.system import system_bp
from routes.media import media_api_bp, media_proxy_bp
from routes.auth import auth_bp, init_auth as init_auth_from_blueprint
from routes.actions import actions_bp
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

#过滤底层日志
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("docker").setLevel(logging.WARNING)

# --- 全局变量 ---

scheduler = BackgroundScheduler(timezone=str(pytz.timezone(constants.TIMEZONE)))
JOB_ID_FULL_SCAN = "scheduled_full_scan"
JOB_ID_SYNC_PERSON_MAP = "scheduled_sync_person_map"
JOB_ID_PROCESS_WATCHLIST = "scheduled_process_watchlist"
JOB_ID_REVIVAL_CHECK = "scheduled_revival_check"

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
                    paused_until DATE DEFAULT NULL,
                    force_ended BOOLEAN DEFAULT 0 NOT NULL 
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
                # 【新增】为 force_ended 字段添加升级逻辑
                if 'force_ended' not in columns:
                    logger.info("    -> 检测到旧版 'watchlist' 表，正在添加 'force_ended' 字段...")
                    cursor.execute("ALTER TABLE watchlist ADD COLUMN force_ended BOOLEAN DEFAULT 0 NOT NULL;")
                    logger.info("    -> 'force_ended' 字段添加成功。")
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
        init_auth_from_blueprint()
        setup_scheduled_tasks()
        logger.info("所有组件已根据新配置重新初始化完毕。")
        
    except Exception as e:
        logger.error(f"保存配置文件或重新初始化时失败: {e}", exc_info=True)
        # 向上抛出异常，让 API 端点可以捕获它并返回错误信息
        raise
# --- 始化所有需要的处理器实例 ---
def initialize_processors():
    """初始化所有处理器，并将实例赋值给 extensions 模块中的全局变量。"""
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


    # --- ✨✨✨ 简化为“单一赋值” ✨✨✨ ---
    # 直接赋值给 extensions 模块的全局变量
    extensions.media_processor_instance = media_processor_instance_local
    extensions.watchlist_processor_instance = watchlist_processor_instance_local
    extensions.actor_subscription_processor_instance = actor_subscription_processor_instance_local
    extensions.EMBY_SERVER_ID = server_id_local
    
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
                task_manager.submit_task(task_process_full_library, "定时全量扫描", process_episodes=config_manager.APP_CONFIG.get('process_episodes', True))
            
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
            scheduler.add_job(func=lambda: task_manager.submit_task(task_sync_person_map, "定时同步演员映射表"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_SYNC_PERSON_MAP, name="定时同步演员映射表", replace_existing=True)
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
            scheduler.add_job(func=lambda: task_manager.submit_task(task_refresh_collections, "定时刷新电影合集"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_REFRESH_COLLECTIONS, name="定时刷新电影合集", replace_existing=True)
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
                task_manager.submit_task(
                    lambda p: p.run_regular_processing_task(task_manager.update_status_from_thread),
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
            task_manager.submit_task(
                lambda p: p.run_revival_check_task(task_manager.update_status_from_thread),
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
            scheduler.add_job(func=lambda: task_manager.submit_task(task_enrich_aliases, "定时演员元数据增强"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_ENRICH_ALIASES, name="定时演员元数据增强", replace_existing=True)
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
            scheduler.add_job(func=lambda: task_manager.submit_task(task_actor_translation_cleanup, "定时演员名查漏补缺"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_ACTOR_CLEANUP, name="定时演员名查漏补缺", replace_existing=True)
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
            scheduler.add_job(func=lambda: task_manager.submit_task(task_auto_subscribe, "定时智能订阅"), trigger=CronTrigger.from_crontab(cron, timezone=str(pytz.timezone(constants.TIMEZONE))), id=JOB_ID_AUTO_SUBSCRIBE, name="定时智能订阅", replace_existing=True)
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
                func=lambda: task_manager.submit_task(task_process_actor_subscriptions, "定时演员订阅扫描"),
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

# --- webhook通知任务 ---
@app.route('/webhook/emby', methods=['POST'])
@extensions.processor_ready_required
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
                extensions.media_processor_instance.emby_url,
                extensions.media_processor_instance.emby_api_key,
                extensions.media_processor_instance.emby_user_id
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
            emby_server_url=extensions.media_processor_instance.emby_url,
            emby_api_key=extensions.media_processor_instance.emby_api_key,
            user_id=extensions.media_processor_instance.emby_user_id
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
        
        from tasks import image_update_task # 确保从 tasks 导入
        task_manager.submit_task(
            image_update_task,
            f"精准图片同步: {original_item_name}",
            item_id=original_item_id,
            update_description=update_description
        )
        
        return jsonify({"status": "precise_image_task_queued", "item_id": original_item_id}), 202
    
    return jsonify({"status": "event_unhandled"}), 500

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
app.register_blueprint(db_admin_bp)
app.register_blueprint(system_bp)
app.register_blueprint(media_api_bp) 
app.register_blueprint(media_proxy_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(actions_bp)
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
    init_auth_from_blueprint()

    # 5. 创建唯一的 MediaProcessor 实例
    initialize_processors()
    
    # 6. 启动后台任务工人
    task_manager.start_task_worker_if_not_running()
    
    # 7. 设置定时任务 (它会依赖全局配置和实例)
    if not scheduler.running:
        scheduler.start()
    setup_scheduled_tasks()
    
    # 8. 运行 Flask 应用
    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=False)

# # --- 主程序入口结束 ---