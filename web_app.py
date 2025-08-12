# web_app.py
from gevent import monkey
monkey.patch_all()
import os
import sqlite3
import shutil
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
from scheduler_manager import scheduler_manager
from reverse_proxy import proxy_app
import logging
# --- 导入蓝图 ---
from routes.watchlist import watchlist_bp
from routes.collections import collections_bp
from routes.custom_collections import custom_collections_bp
from routes.actor_subscriptions import actor_subscriptions_bp
from routes.logs import logs_bp
from routes.database_admin import db_admin_bp
from routes.system import system_bp
from routes.media import media_api_bp, media_proxy_bp
from routes.auth import auth_bp, init_auth as init_auth_from_blueprint
from routes.actions import actions_bp
from routes.cover_generator_config import cover_generator_config_bp
from routes.tasks import tasks_bp
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
logging.getLogger("PIL").setLevel(logging.WARNING)
logging.getLogger("geventwebsocket").setLevel(logging.WARNING)
# --- 全局变量 ---

JOB_ID_FULL_SCAN = "scheduled_full_scan"
JOB_ID_SYNC_PERSON_MAP = "scheduled_sync_person_map"
JOB_ID_PROCESS_WATCHLIST = "scheduled_process_watchlist"
JOB_ID_REVIVAL_CHECK = "scheduled_revival_check"

# --- 数据库辅助函数 ---
def task_process_single_item(processor: MediaProcessor, item_id: str, force_reprocess: bool):
    """任务：处理单个媒体项"""
    processor.process_single_item(item_id, force_reprocess)
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
                    item_type TEXT DEFAULT 'Movie' NOT NULL,
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
                if 'item_type' not in columns:
                        logger.info("    -> 检测到旧版 'collections_info' 表，正在添加 'item_type' 字段...")
                        cursor.execute("ALTER TABLE collections_info ADD COLUMN item_type TEXT DEFAULT 'Movie' NOT NULL;")
                        logger.info("    -> 'item_type' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'collections_info' 表添加新字段时出错: {e_alter}")

            # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
            # ★★★ 新增: 'custom_collections' 表 (自定义合集) ★★★
            # ★★★ 这是实现你新功能的核心地基。                ★★★
            # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
            logger.trace("  -> 正在创建/升级 'custom_collections' 表 (自建合集)...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS custom_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    type TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    emby_collection_id TEXT,
                    last_synced_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ★★★ 为老用户平滑升级 custom_collections 表的逻辑 ★★★
            try:
                cursor.execute("PRAGMA table_info(custom_collections)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                
                new_columns_to_add = {
                    "health_status": "TEXT",
                    "item_type": "TEXT",
                    "in_library_count": "INTEGER DEFAULT 0",
                    "missing_count": "INTEGER DEFAULT 0",
                    "generated_media_info_json": "TEXT",
                    "poster_path": "TEXT",
                    "sort_order": "INTEGER NOT NULL DEFAULT 0"
                }

                for col_name, col_type in new_columns_to_add.items():
                    if col_name not in existing_columns:
                        logger.info(f"    -> 检测到旧版 'custom_collections' 表，正在添加 '{col_name}' 字段...")
                        cursor.execute(f"ALTER TABLE custom_collections ADD COLUMN {col_name} {col_type};")
                        logger.info(f"    -> '{col_name}' 字段添加成功。")
            except Exception as e_alter_cc:
                logger.error(f"  -> 为 'custom_collections' 表添加新字段时出错: {e_alter_cc}")


            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cc_type ON custom_collections (type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cc_status ON custom_collections (status)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cc_name_unique ON custom_collections (name)")

            # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
            # ★★★ 新增: 'media_metadata' 表 (筛选引擎的数据源) ★★★
            # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
            logger.trace("  -> 正在创建/升级 'media_metadata' 表 (用于自定义筛选)...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS media_metadata (
                    tmdb_id TEXT,
                    item_type TEXT NOT NULL,
                    title TEXT,
                    original_title TEXT,
                    release_year INTEGER,
                    rating REAL,                       -- 评分 (例如 CommunityRating)
                    release_date TEXT,                 -- ★ 新增: 上映日期 (格式: YYYY-MM-DD)
                    date_added TEXT,                   -- ★ 新增: 入库日期 (格式: YYYY-MM-DD)
                    
                    -- 使用JSON存储列表数据...
                    genres_json TEXT,
                    actors_json TEXT,
                    directors_json TEXT,
                    studios_json TEXT,
                    countries_json TEXT,
                    
                    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, item_type) -- ★ 优化: 使用复合主键
                )
            """)

            # ★★★ 新增：为老用户平滑升级 media_metadata 表的逻辑 ★★★
            try:
                cursor.execute("PRAGMA table_info(media_metadata)")
                columns = {row[1] for row in cursor.fetchall()}
                if 'release_date' not in columns:
                    logger.info("    -> 检测到旧版 'media_metadata' 表，正在添加 'release_date' 字段...")
                    cursor.execute("ALTER TABLE media_metadata ADD COLUMN release_date TEXT;")
                if 'date_added' not in columns:
                    logger.info("    -> 检测到旧版 'media_metadata' 表，正在添加 'date_added' 字段...")
                    cursor.execute("ALTER TABLE media_metadata ADD COLUMN date_added TEXT;")
            except Exception as e_alter_mm:
                logger.error(f"  -> 为 'media_metadata' 表添加新字段时出错: {e_alter_mm}")

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
    【新版】调用配置管理器保存配置，并在此处执行所有必要的重新初始化操作。
    """
    try:
        # 步骤 1: 调用 config_manager 来保存文件和更新内存中的 config_manager.APP_CONFIG
        config_manager.save_config(new_config)
        
        # 步骤 2: 执行所有依赖于新配置的重新初始化逻辑
        initialize_processors()
        init_auth_from_blueprint()
        
        scheduler_manager.update_task_chain_job()
        
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
    
# --- 应用退出处理 ---
def application_exit_handler():
    # global media_processor_instance, scheduler, task_worker_thread # 不再需要 scheduler
    global media_processor_instance, task_worker_thread # 修正后的
    logger.info("应用程序正在退出 (atexit)，执行清理操作...")

    # 1. 立刻通知当前正在运行的任务停止
    if extensions.media_processor_instance: # 从 extensions 获取
        logger.info("正在发送停止信号给当前任务...")
        extensions.media_processor_instance.signal_stop()

    task_manager.clear_task_queue()
    task_manager.stop_task_worker()

    # 4. 关闭其他资源
    if extensions.media_processor_instance: # 从 extensions 获取
        extensions.media_processor_instance.close()
    
    scheduler_manager.shutdown()
    
    logger.info("atexit 清理操作执行完毕。")
atexit.register(application_exit_handler)

# --- webhook通知任务 ---
@app.route('/webhook/emby', methods=['POST'])
@extensions.processor_ready_required
def emby_webhook():
    data = request.json
    event_type = data.get("Event") if data else "未知事件"
    logger.info(f"收到Emby Webhook: {event_type}")
    
    trigger_events = ["item.add", "library.new"]  # 删除了 image.update
    if event_type not in trigger_events:
        logger.info(f"Webhook事件 '{event_type}' 不在触发列表 {trigger_events} 中，将被忽略。")
        return jsonify({"status": "event_ignored_not_in_trigger_list"}), 200

    item_from_webhook = data.get("Item", {}) if data else {}
    original_item_id = item_from_webhook.get("Id")
    original_item_name = item_from_webhook.get("Name", "未知项目")
    original_item_type = item_from_webhook.get("Type")
    
    trigger_types = ["Movie", "Series", "Episode"]
    if not (original_item_id and original_item_type in trigger_types):
        logger.debug(f"Webhook事件 '{event_type}' (项目: {original_item_name}, 类型: {original_item_type}) 被忽略（缺少ID或类型不匹配）。")
        return jsonify({"status": "event_ignored_no_id_or_wrong_type"}), 200

    if event_type in ["item.add", "library.new"]:
        id_to_process = original_item_id
        type_to_process = original_item_type
        if original_item_type == "Episode":
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
            force_reprocess=True 
        )
        
        return jsonify({"status": "metadata_task_queued", "item_id": id_to_process}), 202

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
app.register_blueprint(custom_collections_bp)
app.register_blueprint(actor_subscriptions_bp)
app.register_blueprint(logs_bp)
app.register_blueprint(db_admin_bp)
app.register_blueprint(system_bp)
app.register_blueprint(media_api_bp) 
app.register_blueprint(media_proxy_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(actions_bp)
app.register_blueprint(cover_generator_config_bp)
app.register_blueprint(tasks_bp)
def ensure_cover_generator_fonts():
    """
    启动时检查 cover_generator/fonts 目录下是否有指定字体文件，
    若缺少则从项目根目录的 fonts 目录拷贝过去。
    """
    cover_fonts_dir = os.path.join(config_manager.PERSISTENT_DATA_PATH, 'cover_generator', 'fonts')
    project_fonts_dir = os.path.join(os.getcwd(), 'fonts')  # 项目根目录fonts

    required_fonts = [
        "en_font.ttf",
        "en_font_multi_1.otf",
        "zh_font.ttf",
        "zh_font_multi_1.ttf",
    ]

    if not os.path.exists(cover_fonts_dir):
        os.makedirs(cover_fonts_dir, exist_ok=True)
        logger.info(f"已创建字体目录：{cover_fonts_dir}")

    for font_name in required_fonts:
        dest_path = os.path.join(cover_fonts_dir, font_name)
        if not os.path.isfile(dest_path):
            src_path = os.path.join(project_fonts_dir, font_name)
            if os.path.isfile(src_path):
                try:
                    shutil.copy2(src_path, dest_path)
                    logger.info(f"已拷贝缺失字体文件 {font_name} 到 {cover_fonts_dir}")
                except Exception as e:
                    logger.error(f"拷贝字体文件 {font_name} 失败: {e}", exc_info=True)
            else:
                logger.warning(f"项目根目录缺少字体文件 {font_name}，无法拷贝至 {cover_fonts_dir}")
if __name__ == '__main__':
    # ★★★ 猴子补丁已经移到文件顶部，这里不再需要 ★★★
    from gevent.pywsgi import WSGIServer
    from geventwebsocket.handler import WebSocketHandler

    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}")
    
    config_manager.load_config()
    
    config_manager.LOG_DIRECTORY = os.path.join(config_manager.PERSISTENT_DATA_PATH, 'logs')
    try:
        log_size = int(config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_LOG_ROTATION_SIZE_MB, constants.DEFAULT_LOG_ROTATION_SIZE_MB))
        log_backups = int(config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_LOG_ROTATION_BACKUPS, constants.DEFAULT_LOG_ROTATION_BACKUPS))
    except (ValueError, TypeError):
        log_size = constants.DEFAULT_LOG_ROTATION_SIZE_MB
        log_backups = constants.DEFAULT_LOG_ROTATION_BACKUPS
    add_file_handler(log_directory=config_manager.LOG_DIRECTORY, log_size_mb=log_size, log_backups=log_backups)
    
    init_db()
    # 新增字体文件检测和拷贝
    ensure_cover_generator_fonts()
    init_auth_from_blueprint()
    initialize_processors()
    task_manager.start_task_worker_if_not_running()
    scheduler_manager.start()
    
    def run_proxy_server():
        if config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_PROXY_ENABLED):
            try:
                proxy_port = int(config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_PROXY_PORT))
                logger.info(f"🚀 [GEVENT] 反向代理服务即将启动，监听端口: {proxy_port}")
                
                proxy_server = WSGIServer(
                    ('0.0.0.0', proxy_port), 
                    proxy_app, 
                    handler_class=WebSocketHandler
                )
                proxy_server.serve_forever()

            except Exception as e:
                logger.error(f"启动反向代理服务失败: {e}", exc_info=True)
        else:
            logger.info("反向代理功能未在配置中启用。")

    proxy_thread = threading.Thread(target=run_proxy_server, daemon=True)
    proxy_thread.start()

    main_app_port = int(constants.WEB_APP_PORT)
    logger.info(f"🚀 [GEVENT] 主应用服务器即将启动，监听端口: {main_app_port}")
    
    class NullLogger:
        def write(self, data):
            pass
        def flush(self):
            pass

    main_server = WSGIServer(
        ('0.0.0.0', main_app_port), 
        app, log=NullLogger()
    )
    main_server.serve_forever()

# # --- 主程序入口结束 ---