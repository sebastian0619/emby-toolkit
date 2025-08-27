# web_app.py
from gevent import monkey
monkey.patch_all()
import os
import shutil
import threading
import psycopg2 #临时用，过段时间删除
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

# --- 临时函数，过段时间删除
def _migrate_actor_metadata_table(cursor):
    """
    一个一次性的数据迁移函数，用于将数据从旧的 "ActorMetadata" 表
    安全地迁移到新的 "actor_metadata" 表，然后删除旧表。
    此函数是幂等的，可以安全地多次运行。
    """
    try:
        # 1. 检查旧表 "ActorMetadata" 是否存在
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'actormetadata'
            );
        """)
        # ★★★ 修复 1/2: 使用字典键 'exists' 访问 ★★★
        old_table_exists = cursor.fetchone()['exists']

        if not old_table_exists:
            logger.debug("旧表 'actormetadata' 不存在，无需迁移。")
            return

        logger.warning("检测到旧的 'actormetadata' 表，将开始自动数据迁移并清理...")

        # 2. 确保新表 "actor_metadata" 存在
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'actor_metadata'
            );
        """)
        # ★★★ 修复 2/2: 同样使用字典键 'exists' 访问 ★★★
        if not cursor.fetchone()['exists']:
            logger.error("迁移失败：新表 'actor_metadata' 不存在！请确保 init_db() 已正确运行。")
            return

        # 3. 将数据从旧表插入到新表
        logger.info("  -> 正在将数据从 'actormetadata' 复制到 'actor_metadata'...")
        cursor.execute("""
            INSERT INTO actor_metadata (
                tmdb_id, profile_path, gender, adult, 
                popularity, original_name, last_updated_at
            )
            SELECT 
                tmdb_id, profile_path, gender, adult, 
                popularity, original_name, last_updated_at
            FROM "actormetadata"
            ON CONFLICT (tmdb_id) DO NOTHING;
        """)
        
        moved_rows = cursor.rowcount
        logger.info(f"  -> 成功迁移 {moved_rows} 条演员元数据。")

        # 4. 直接删除旧表
        logger.warning("  -> 正在删除旧表 'actormetadata'...")
        cursor.execute('DROP TABLE "actormetadata" CASCADE;')

        logger.info("✅ 演员元数据表自动迁移和清理已成功完成！")

    except psycopg2.Error as e:
        logger.error(f"自动迁移 'ActorMetadata' 表时发生数据库错误: {e}", exc_info=True)
        cursor.connection.rollback()
    except Exception as e:
        logger.error(f"自动迁移 'ActorMetadata' 表时发生未知错误: {e}", exc_info=True)
        cursor.connection.rollback()

# --- 初始化数据库 ---
def init_db():
    """
    【PostgreSQL版】初始化数据库，创建所有表的最终结构。
    """
    logger.info("正在初始化 PostgreSQL 数据库，创建/验证所有表的结构...")
    
    # get_central_db_connection 应该就是 db_handler.get_db_connection
    # 确保它现在调用的是无参数版本
    try:
        with db_handler.get_db_connection() as conn:
            with conn.cursor() as cursor:
                logger.info("  -> 数据库连接成功，开始建表...")

                # --- 1. 创建基础表 (日志、缓存、用户) ---
                logger.trace("  -> 正在创建基础表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS processed_log (
                        item_id TEXT PRIMARY KEY, 
                        item_name TEXT, 
                        processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(), 
                        score REAL,
                        assets_synced_at TIMESTAMP WITH TIME ZONE,
                        last_emby_modified_at TIMESTAMP WITH TIME ZONE
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS failed_log (
                        item_id TEXT PRIMARY KEY, 
                        item_name TEXT, 
                        reason TEXT, 
                        failed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(), 
                        error_message TEXT, 
                        item_type TEXT, 
                        score REAL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY, 
                        username TEXT UNIQUE NOT NULL, 
                        password_hash TEXT NOT NULL, 
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS translation_cache (
                        original_text TEXT PRIMARY KEY, 
                        translated_text TEXT, 
                        engine_used TEXT, 
                        last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)

                # --- 2. 创建核心功能表 ---
                logger.trace("  -> 正在创建 'collections_info' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collections_info (
                        emby_collection_id TEXT PRIMARY KEY,
                        name TEXT,
                        tmdb_collection_id TEXT,
                        status TEXT,
                        has_missing BOOLEAN, 
                        missing_movies_json JSONB,
                        last_checked_at TIMESTAMP WITH TIME ZONE,
                        poster_path TEXT,
                        item_type TEXT DEFAULT 'Movie' NOT NULL,
                        in_library_count INTEGER DEFAULT 0
                    )
                """)

                logger.trace("  -> 正在创建 'custom_collections' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS custom_collections (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        type TEXT NOT NULL,
                        definition_json JSONB NOT NULL,
                        status TEXT DEFAULT 'active',
                        emby_collection_id TEXT,
                        last_synced_at TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        health_status TEXT,
                        item_type TEXT,
                        in_library_count INTEGER DEFAULT 0,
                        missing_count INTEGER DEFAULT 0,
                        generated_media_info_json JSONB,
                        poster_path TEXT,
                        sort_order INTEGER NOT NULL DEFAULT 0
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_cc_type ON custom_collections (type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_cc_status ON custom_collections (status)")

                logger.trace("  -> 正在创建 'media_metadata' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS media_metadata (
                        tmdb_id TEXT,
                        item_type TEXT NOT NULL,
                        title TEXT,
                        original_title TEXT,
                        release_year INTEGER,
                        rating REAL,
                        genres_json JSONB,
                        actors_json JSONB,
                        directors_json JSONB,
                        studios_json JSONB,
                        countries_json JSONB,
                        last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        release_date DATE,
                        date_added TIMESTAMP WITH TIME ZONE,
                        tags_json JSONB,
                        last_synced_at TIMESTAMP WITH TIME ZONE,
                        PRIMARY KEY (tmdb_id, item_type)
                    )
                """)

                logger.trace("  -> 正在创建 'watchlist' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS watchlist (
                        item_id TEXT PRIMARY KEY,
                        tmdb_id TEXT NOT NULL,
                        item_name TEXT,
                        item_type TEXT DEFAULT 'Series',
                        status TEXT DEFAULT 'Watching',
                        added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        last_checked_at TIMESTAMP WITH TIME ZONE,
                        tmdb_status TEXT,
                        next_episode_to_air_json JSONB,
                        missing_info_json JSONB,
                        paused_until DATE DEFAULT NULL,
                        force_ended BOOLEAN DEFAULT FALSE NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist (status)")

                logger.trace("  -> 正在创建 'person_identity_map' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS person_identity_map (
                        map_id SERIAL PRIMARY KEY, 
                        primary_name TEXT NOT NULL, 
                        emby_person_id TEXT NOT NULL UNIQUE,
                        tmdb_person_id INTEGER UNIQUE, 
                        imdb_id TEXT UNIQUE, 
                        douban_celebrity_id TEXT UNIQUE,
                        last_synced_at TIMESTAMP WITH TIME ZONE, 
                        last_updated_at TIMESTAMP WITH TIME ZONE
                    )
                """)

                logger.trace("  -> 正在创建 'actor_metadata' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS actor_metadata (
                        tmdb_id INTEGER PRIMARY KEY, 
                        profile_path TEXT, 
                        gender INTEGER, 
                        adult BOOLEAN,
                        popularity REAL, 
                        original_name TEXT, 
                        last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        FOREIGN KEY(tmdb_id) REFERENCES person_identity_map(tmdb_person_id) ON DELETE CASCADE
                    )
                """)

                logger.trace("  -> 正在创建 'actor_subscriptions' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS actor_subscriptions (
                        id SERIAL PRIMARY KEY,
                        tmdb_person_id INTEGER NOT NULL UNIQUE,
                        actor_name TEXT NOT NULL,
                        profile_path TEXT,
                        config_start_year INTEGER DEFAULT 1900,
                        config_media_types TEXT DEFAULT 'Movie,TV',
                        config_genres_include_json JSONB,
                        config_genres_exclude_json JSONB,
                        status TEXT DEFAULT 'active',
                        last_checked_at TIMESTAMP WITH TIME ZONE,
                        added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        config_min_rating REAL DEFAULT 6.0
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_as_status ON actor_subscriptions (status)")

                logger.trace("  -> 正在创建 'tracked_actor_media' 表...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tracked_actor_media (
                        id SERIAL PRIMARY KEY,
                        subscription_id INTEGER NOT NULL,
                        tmdb_media_id INTEGER NOT NULL,
                        media_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        release_date DATE,
                        poster_path TEXT,
                        status TEXT NOT NULL,
                        emby_item_id TEXT,
                        last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        FOREIGN KEY(subscription_id) REFERENCES actor_subscriptions(id) ON DELETE CASCADE,
                        UNIQUE(subscription_id, tmdb_media_id)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tam_subscription_id ON tracked_actor_media (subscription_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tam_status ON tracked_actor_media (status)")

                # ★★★ 临时用，果断时间删除 ★★★
                logger.info("  -> 检查并执行数据迁移...")
                _migrate_actor_metadata_table(cursor)

            conn.commit()
            logger.info("✅ PostgreSQL 数据库初始化完成，所有表结构已创建/验证。")

    except psycopg2.Error as e_pg:
        logger.error(f"数据库初始化时发生 PostgreSQL 错误: {e_pg}", exc_info=True)
        raise
    except Exception as e_global:
        logger.error(f"数据库初始化时发生未知错误: {e_global}", exc_info=True)
        raise

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

# --- 生成Nginx配置 ---
def ensure_nginx_config():
    """
    【Jinja2 最终版】使用 Jinja2 模板引擎，强制生成 Nginx 配置文件。
    """
    logger.trace("正在强制同步 Nginx 配置文件 (使用 Jinja2)...")
    
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

# --- 反代监控 ---
@app.route('/api/health')
def health_check():
    """一个简单的健康检查端点，用于 Docker healthcheck。"""
    return jsonify({"status": "ok"}), 200

# --- webhook通知任务 ---
@app.route('/webhook/emby', methods=['POST'])
@extensions.processor_ready_required
def emby_webhook():
    data = request.json
    event_type = data.get("Event") if data else "未知事件"
    logger.info(f"收到Emby Webhook: {event_type}")

    # --- 后台处理函数：处理媒体刮削 (item.add) ---
    def _process_in_background(item_id_to_process, item_name_for_log):
        logger.info(f"后台线程启动，开始处理: '{item_name_for_log}' (ID: {item_id_to_process})")
        try:
            webhook_processing_task(
                processor=extensions.media_processor_instance,
                item_id=item_id_to_process,
                force_reprocess=True
            )
            logger.info(f"后台现线程成功完成: '{item_name_for_log}'")
        except Exception as e:
            logger.error(f"后台线程处理 '{item_name_for_log}' 时发生严重错误: {e}", exc_info=True)

    # --- 后台处理函数：定点同步资源文件 (image.update) ---
    def _sync_assets_in_background(item_id, item_name, update_description, sync_timestamp):
        logger.info(f"实时覆盖缓存备份线程启动: '{item_name}' (ID: {item_id})")
        try:
            processor = extensions.media_processor_instance
            processor.sync_single_item_assets(item_id, update_description, sync_timestamp)
            logger.info(f"实时覆盖缓存备份成功完成: '{item_name}'")
        except Exception as e:
            logger.error(f"实时覆盖缓存备份 '{item_name}' 时发生严重错误: {e}", exc_info=True)

    # --- 后台处理函数：定点同步元数据缓存 (metadata.update) ---
    def _sync_metadata_cache_in_background(item_id, item_name):
        logger.info(f"实时媒体元数据同步线程启动: '{item_name}' (ID: {item_id})")
        try:
            processor = extensions.media_processor_instance
            processor.sync_single_item_to_metadata_cache(item_id)
            logger.info(f"实时媒体元数据同步成功完成: '{item_name}'")
        except Exception as e:
            logger.error(f"实时媒体元数据同步 '{item_name}' 时发生严重错误: {e}", exc_info=True)

    # --- Webhook 事件分发逻辑 ---
    # ▼▼▼ 核心修改 1/3: 将 image.update 添加到触发列表 ▼▼▼
    trigger_events = ["item.add", "library.new", "library.deleted", "metadata.update", "image.update"]
    if event_type not in trigger_events:
        logger.info(f"Webhook事件 '{event_type}' 不在触发列表 {trigger_events} 中，将被忽略。")
        return jsonify({"status": "event_ignored_not_in_trigger_list"}), 200

    item_from_webhook = data.get("Item", {}) if data else {}
    original_item_id = item_from_webhook.get("Id")
    original_item_name = item_from_webhook.get("Name", "未知项目")
    original_item_type = item_from_webhook.get("Type")
    
    trigger_types = ["Movie", "Series", "Episode"]
    if not (original_item_id and original_item_type in trigger_types):
        logger.debug(f"Webhook事件 '{event_type}' (项目: {original_item_name}, 类型: {original_item_type}) 被忽略。")
        return jsonify({"status": "event_ignored_no_id_or_wrong_type"}), 200

    # --- 处理删除事件 (逻辑不变) ---
    if event_type == "library.deleted":
        # ... (此处代码省略，与您现有的逻辑相同) ...
        try:
            with get_central_db_connection() as conn:
                log_manager = LogDBManager()
                log_manager.remove_from_processed_log(conn.cursor(), original_item_id)
                conn.commit()
            return jsonify({"status": "processed_log_entry_removed", "item_id": original_item_id}), 200
        except Exception as e:
            return jsonify({"status": "error_processing_remove_event", "error": str(e)}), 500
    
    # --- 处理新增/入库事件 (逻辑不变) ---
    if event_type in ["item.add", "library.new"]:
        # ... (此处代码省略，与您现有的逻辑相同) ...
        id_to_process = original_item_id
        if original_item_type == "Episode":
            series_id = emby_handler.get_series_id_from_child_id(
                original_item_id, extensions.media_processor_instance.emby_url,
                extensions.media_processor_instance.emby_api_key, extensions.media_processor_instance.emby_user_id
            )
            if not series_id: return jsonify({"status": "event_ignored_series_not_found"}), 200
            id_to_process = series_id
        full_item_details = emby_handler.get_emby_item_details(
            item_id=id_to_process, emby_server_url=extensions.media_processor_instance.emby_url,
            emby_api_key=extensions.media_processor_instance.emby_api_key, user_id=extensions.media_processor_instance.emby_user_id
        )
        if not full_item_details: return jsonify({"status": "event_ignored_details_fetch_failed"}), 200
        final_item_name = full_item_details.get("Name", f"未知(ID:{id_to_process})")
        if not full_item_details.get("ProviderIds", {}).get("Tmdb"): return jsonify({"status": "event_ignored_no_tmdb_id"}), 200
        thread = threading.Thread(target=_process_in_background, args=(id_to_process, final_item_name))
        thread.daemon = True
        thread.start()
        return jsonify({"status": "processing_started_in_background", "item_id": id_to_process}), 202

    # --- ▼▼▼ 核心修改 2/3: 新增 image.update 事件的独立处理逻辑 ▼▼▼ ---
    if event_type == "image.update":
        if not config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_LOCAL_DATA_PATH):
            logger.debug("Webhook 'image.update' 收到，但未配置本地数据源，将忽略。")
            return jsonify({"status": "event_ignored_no_local_data_path"}), 200

        update_description = data.get("UpdateInfo", {}).get("Description", "")
        webhook_received_at_iso = datetime.now(timezone.utc).isoformat()
        logger.info(f"Webhook 'image.update' 触发资源文件同步 for '{original_item_name}' (原因: {update_description})")
        
        thread = threading.Thread(
            target=_sync_assets_in_background,
            args=(original_item_id, original_item_name, update_description, webhook_received_at_iso)
        )
        thread.daemon = True
        thread.start()
        return jsonify({"status": "asset_sync_started", "item_id": original_item_id}), 202

    # --- ▼▼▼ 核心修改 3/3: 简化 metadata.update 的处理逻辑 ▼▼▼ ---
    if event_type == "metadata.update":
        if not config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_LOCAL_DATA_PATH):
            logger.debug("Webhook 'metadata.update' 收到，但未配置本地数据源，将忽略。")
            return jsonify({"status": "event_ignored_no_local_data_path"}), 200
        
        logger.info(f"Webhook 'metadata.update' 触发元数据缓存同步 for '{original_item_name}'")
        
        thread = threading.Thread(
            target=_sync_metadata_cache_in_background,
            args=(original_item_id, original_item_name)
        )
        thread.daemon = True
        thread.start()
        return jsonify({"status": "metadata_cache_sync_started", "item_id": original_item_id}), 202

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
    from gevent.pywsgi import WSGIServer
    from geventwebsocket.handler import WebSocketHandler
    import gevent # <--- 1. 导入 gevent

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

    gevent.spawn(run_proxy_server)

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