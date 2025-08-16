# web_app.py
from gevent import monkey
monkey.patch_all()
import os
import sqlite3
import shutil
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
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
from string import Template
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
            
            # ✨ 为老用户平滑升级 'processed_log' 表 (使用可扩展模式)
            try:
                cursor.execute("PRAGMA table_info(processed_log)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                
                # 定义需要检查和添加的字段。未来增加新字段，只需在此处添加键值对。
                new_columns_to_add_processed = {
                    "assets_synced_at": "TEXT",
                    "last_emby_modified_at": "TEXT"
                }

                for col_name, col_type in new_columns_to_add_processed.items():
                    if col_name not in existing_columns:
                        logger.info(f"    -> 检测到旧版 'processed_log' 表，正在添加 '{col_name}' 字段...")
                        cursor.execute(f"ALTER TABLE processed_log ADD COLUMN {col_name} {col_type};")
                        logger.info(f"    -> '{col_name}' 字段添加成功。")
            except Exception as e_alter_processed:
                logger.error(f"  -> 为 'processed_log' 表添加新字段时出错: {e_alter_processed}")
            
            # --- 3. 创建核心功能表 ---
            # 电影合集检查
            logger.trace("  -> 正在创建/升级 'collections_info' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collections_info (
                    emby_collection_id TEXT PRIMARY KEY,
                    name TEXT,
                    tmdb_collection_id TEXT,
                    status TEXT,
                    has_missing BOOLEAN, 
                    missing_movies_json TEXT,
                    last_checked_at TIMESTAMP,
                    poster_path TEXT
                )
            """)

            # ✨ 为老用户平滑升级 'collections_info' 表的统一逻辑
            try:
                cursor.execute("PRAGMA table_info(collections_info)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                
                new_columns_to_add = {
                    "item_type": "TEXT DEFAULT 'Movie' NOT NULL",
                    "in_library_count": "INTEGER DEFAULT 0"
                }

                for col_name, col_type in new_columns_to_add.items():
                    if col_name not in existing_columns:
                        logger.info(f"    -> 检测到旧版 'collections_info' 表，正在添加 '{col_name}' 字段...")
                        cursor.execute(f"ALTER TABLE collections_info ADD COLUMN {col_name} {col_type};")
                        logger.info(f"    -> '{col_name}' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'collections_info' 表添加新字段时出错: {e_alter}")

            # 自定义合集
            logger.trace("  -> 正在创建/升级 'custom_collections' 表...")
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
            
            # ✨ 为老用户平滑升级 'custom_collections' 表的统一逻辑
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

            # 媒体元数据表 (筛选引擎数据源)
            logger.trace("  -> 正在创建/升级 'media_metadata' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS media_metadata (
                    tmdb_id TEXT,
                    item_type TEXT NOT NULL,
                    title TEXT,
                    original_title TEXT,
                    release_year INTEGER,
                    rating REAL,
                    genres_json TEXT,
                    actors_json TEXT,
                    directors_json TEXT,
                    studios_json TEXT,
                    countries_json TEXT,
                    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, item_type)
                )
            """)

            # ✨ 为老用户平滑升级 'media_metadata' 表的统一逻辑
            try:
                cursor.execute("PRAGMA table_info(media_metadata)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                
                new_columns_to_add = {
                    "release_date": "TEXT",
                    "date_added": "TEXT"
                }

                for col_name, col_type in new_columns_to_add.items():
                    if col_name not in existing_columns:
                        logger.info(f"    -> 检测到旧版 'media_metadata' 表，正在添加 '{col_name}' 字段...")
                        cursor.execute(f"ALTER TABLE media_metadata ADD COLUMN {col_name} {col_type};")
                        logger.info(f"    -> '{col_name}' 字段添加成功。")
            except Exception as e_alter_mm:
                logger.error(f"  -> 为 'media_metadata' 表添加新字段时出错: {e_alter_mm}")

            # 剧集追踪 (追剧列表) 
            logger.trace("  -> 正在创建/升级 'watchlist' 表...")
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
                    missing_info_json TEXT
                )
            """)

            # ✨ 为老用户平滑升级 'watchlist' 表的统一逻辑
            try:
                cursor.execute("PRAGMA table_info(watchlist)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                
                new_columns_to_add = {
                    "paused_until": "DATE DEFAULT NULL",
                    "force_ended": "BOOLEAN DEFAULT 0 NOT NULL"
                }

                for col_name, col_type in new_columns_to_add.items():
                    if col_name not in existing_columns:
                        logger.info(f"    -> 检测到旧版 'watchlist' 表，正在添加 '{col_name}' 字段...")
                        cursor.execute(f"ALTER TABLE watchlist ADD COLUMN {col_name} {col_type};")
                        logger.info(f"    -> '{col_name}' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'watchlist' 表添加新字段时出错: {e_alter}")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist (status)")

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
            logger.trace("  -> 正在创建/升级 'actor_subscriptions' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS actor_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_person_id INTEGER NOT NULL UNIQUE,
                    actor_name TEXT NOT NULL,
                    profile_path TEXT,
                    config_start_year INTEGER DEFAULT 1900,
                    config_media_types TEXT DEFAULT 'Movie,TV',
                    config_genres_include_json TEXT,
                    config_genres_exclude_json TEXT,
                    status TEXT DEFAULT 'active',
                    last_checked_at TIMESTAMP,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # ✨ 为老用户平滑升级 'actor_subscriptions' 表的统一逻辑
            try:
                cursor.execute("PRAGMA table_info(actor_subscriptions)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                
                new_columns_to_add = {
                    "config_min_rating": "REAL DEFAULT 6.0"
                }

                for col_name, col_type in new_columns_to_add.items():
                    if col_name not in existing_columns:
                        logger.info(f"    -> 检测到旧版 'actor_subscriptions' 表，正在添加 '{col_name}' 字段...")
                        cursor.execute(f"ALTER TABLE actor_subscriptions ADD COLUMN {col_name} {col_type};")
                        logger.info(f"    -> '{col_name}' 字段添加成功。")
            except Exception as e_alter:
                logger.error(f"  -> 为 'actor_subscriptions' 表添加新字段时出错: {e_alter}")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_as_tmdb_person_id ON actor_subscriptions (tmdb_person_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_as_status ON actor_subscriptions (status)")

            # 追踪的演员媒体表
            logger.trace("  -> 正在创建 'tracked_actor_media' 表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracked_actor_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id INTEGER NOT NULL,
                    tmdb_media_id INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    release_date TEXT,
                    poster_path TEXT,
                    status TEXT NOT NULL,
                    emby_item_id TEXT,
                    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(subscription_id) REFERENCES actor_subscriptions(id) ON DELETE CASCADE,
                    UNIQUE(subscription_id, tmdb_media_id)
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

# --- 初始化所有需要的处理器实例 ---
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

# --- 虚拟库反代配置 ---
def ensure_nginx_config():
    """
    【Jinja2 最终版】使用 Jinja2 模板引擎，强制生成 Nginx 配置文件。
    """
    logger.info("正在强制同步 Nginx 配置文件 (使用 Jinja2)...")
    
    # 定义路径
    nginx_config_dir = os.path.join(config_manager.PERSISTENT_DATA_PATH, 'nginx', 'conf.d')
    final_config_path = os.path.join(nginx_config_dir, 'default.conf')
    # Jinja2 需要模板所在的目录
    template_dir = os.path.join(os.getcwd(), 'templates', 'nginx')
    template_filename = 'emby_proxy.conf.template'

    try:
        # 确保 Nginx 配置目录存在
        os.makedirs(nginx_config_dir, exist_ok=True)

        # 1. 设置 Jinja2 环境
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template(template_filename)

        # 2. 从 APP_CONFIG 获取值 (逻辑不变)
        emby_url = config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_EMBY_SERVER_URL, "")
        nginx_listen_port = config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_PROXY_PORT, 8097)

        # 3. 准备替换值 (逻辑不变)
        emby_upstream = emby_url.replace("http://", "").replace("https://", "").rstrip('/')
        proxy_upstream = "emby-toolkit:8098"

        if not emby_upstream:
            logger.error("config.ini 中未配置 Emby 服务器地址，无法生成 Nginx 配置！")
            return

        # 4. 填充模板
        context = {
            'EMBY_UPSTREAM': emby_upstream,
            'PROXY_UPSTREAM': proxy_upstream,
            'NGINX_LISTEN_PORT': nginx_listen_port
        }
        final_config_content = template.render(context)

        # 5. 写入最终的配置文件 (会直接覆盖旧文件)
        with open(final_config_path, 'w', encoding='utf-8') as f:
            f.write(final_config_content)
        
        logger.info("✅ Nginx 配置文件已成功同步！")

    except Exception as e:
        logger.error(f"处理 Nginx 配置文件时发生严重错误: {e}", exc_info=True)

# --- 检查字体文件 ---
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
        logger.trace(f"已创建字体目录：{cover_fonts_dir}")

    for font_name in required_fonts:
        dest_path = os.path.join(cover_fonts_dir, font_name)
        if not os.path.isfile(dest_path):
            src_path = os.path.join(project_fonts_dir, font_name)
            if os.path.isfile(src_path):
                try:
                    shutil.copy2(src_path, dest_path)
                    logger.trace(f"已拷贝缺失字体文件 {font_name} 到 {cover_fonts_dir}")
                except Exception as e:
                    logger.error(f"拷贝字体文件 {font_name} 失败: {e}", exc_info=True)
            else:
                logger.warning(f"项目根目录缺少字体文件 {font_name}，无法拷贝至 {cover_fonts_dir}")

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
    
    trigger_events = ["item.add", "library.new", "library.deleted"]  # 删除了 image.update
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

    # ✨ 3. 新增删除事件的处理逻辑
    if event_type == "library.deleted":
        logger.info(f"Webhook 收到删除事件，将从已处理日志中移除项目 '{original_item_name}' (ID: {original_item_id})。")
        try:
            with get_central_db_connection(config_manager.DB_PATH) as conn:
                cursor = conn.cursor()
                log_manager = LogDBManager(config_manager.DB_PATH)
                log_manager.remove_from_processed_log(cursor, original_item_id)
                conn.commit()
            logger.info(f"成功从已处理日志中删除记录: {original_item_name}")
            return jsonify({"status": "processed_log_entry_removed", "item_id": original_item_id}), 200
        except Exception as e:
            logger.error(f"处理删除事件时发生数据库错误: {e}", exc_info=True)
            return jsonify({"status": "error_processing_remove_event", "error": str(e)}), 500
    
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
    # --- 拷贝反代配置 ---
    ensure_nginx_config()
    # 新增字体文件检测和拷贝
    ensure_cover_generator_fonts()
    init_auth_from_blueprint()
    initialize_processors()
    task_manager.start_task_worker_if_not_running()
    scheduler_manager.start()
    
    def run_proxy_server():
        if config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_PROXY_ENABLED):
            try:
                # 定义一个固定的内部端口
                internal_proxy_port = 8098
                logger.trace(f"🚀 [GEVENT] 反向代理服务即将启动，监听内部端口: {internal_proxy_port}")
                
                proxy_server = WSGIServer(
                    ('0.0.0.0', internal_proxy_port), 
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