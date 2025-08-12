# tasks.py

import time
import re
import os
import json
import sqlite3
import logging
import threading
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
import concurrent.futures

# 导入类型提示
from typing import Optional
from core_processor import MediaProcessor
from watchlist_processor import WatchlistProcessor
from actor_subscription_processor import ActorSubscriptionProcessor
from custom_collection_handler import ListImporter, FilterEngine

# 导入需要的底层模块和共享实例
import db_handler
import emby_handler
import tmdb_handler
import moviepilot_handler
import config_manager
import constants
import extensions
import task_manager
from actor_utils import enrich_all_actor_aliases_task
from actor_sync_handler import UnifiedSyncHandler
from extensions import TASK_REGISTRY
from custom_collection_handler import ListImporter, FilterEngine
from core_processor import _read_local_json
from services.cover_generator import CoverGeneratorService
from utils import get_country_translation_map, translate_country_list

logger = logging.getLogger(__name__)

# ★★★ 全量处理任务 ★★★
def task_run_full_scan(processor: MediaProcessor, force_reprocess: bool = False):
    """
    根据传入的 force_reprocess 参数，决定是执行标准扫描还是强制扫描。
    """
    # 1. 根据参数决定日志信息
    if force_reprocess:
        logger.warning("即将执行【强制】全量处理，将处理所有媒体项...")
    else:
        logger.info("即将执行【标准】全量处理，将跳过已处理项...")


    # 3. 调用核心处理函数，并将 force_reprocess 参数透传下去
    processor.process_full_library(
        update_status_callback=task_manager.update_status_from_thread,
        force_reprocess_all=force_reprocess,
        force_fetch_from_tmdb=force_reprocess
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
            update_status_callback=task_manager.update_status_from_thread
        )
        
        logger.info(f"'{task_name}' 成功完成。")

    except Exception as e:
        logger.error(f"'{task_name}' 执行过程中发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"错误：同步失败 ({str(e)[:50]}...)")
# ✨✨✨ 演员元数据增强函数 ✨✨✨
def task_enrich_aliases(processor: MediaProcessor):
    """
    【V3 - 后台任务】演员元数据增强任务的入口点。
    - 核心逻辑：内置了30天的固定冷却时间，无需任何外部配置。
    """
    task_name = "演员元数据补充"
    logger.info(f"后台任务 '{task_name}' 开始执行...")
    task_manager.update_status_from_thread(0, "准备开始演员元数据补充...")

    try:
        # 从传入的 processor 对象中获取配置字典
        config = processor.config
        
        # 获取必要的配置项
        db_path = config_manager.DB_PATH
        tmdb_api_key = config.get(constants.CONFIG_OPTION_TMDB_API_KEY)

        if not tmdb_api_key:
            logger.error(f"任务 '{task_name}' 中止：未在配置中找到 TMDb API Key。")
            task_manager.update_status_from_thread(-1, "错误：缺少TMDb API Key")
            return

        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        # --- 【【【 这 是 核 心 修 改 点 】】】 ---
        
        # 1. 运行时长 (run_duration_minutes)
        # 假设核心函数 enrich_all_actor_aliases_task 仍然需要这个参数。
        # 如果不需要，可以安全地从下面的函数调用中移除它。
        # 我们将其硬编码为 0，代表“不限制时长”，这是最常见的用法。
        duration_minutes = 0

        # 2. 冷却时间 (sync_interval_days)
        # 直接将冷却时间硬编码为 30 天。
        cooldown_days = 30
        
        logger.info(f"演员元数据补充任务将使用固定的 {cooldown_days} 天冷却期。")

        # 调用核心函数，并传递写死的值
        enrich_all_actor_aliases_task(
            db_path=db_path,
            tmdb_api_key=tmdb_api_key,
            run_duration_minutes=duration_minutes,
            sync_interval_days=cooldown_days, # <--- 使用我们硬编码的冷却时间
            stop_event=processor.get_stop_event()
        )
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
        
        logger.info(f"'{task_name}' 任务执行完毕。")
        task_manager.update_status_from_thread(100, "演员元数据补充任务完成。")

    except Exception as e:
        logger.error(f"'{task_name}' 执行过程中发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"错误：任务失败 ({str(e)[:50]}...)")
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
    processor.run_scheduled_task(update_status_callback=task_manager.update_status_from_thread)
# ★★★ 处理webhook、用于编排任务的函数 ★★★
def webhook_processing_task(processor: MediaProcessor, item_id: str, force_reprocess: bool):
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
    processed_successfully = processor.process_single_item(
        item_id, 
        force_reprocess_this_item=force_reprocess 
    )
    
    # --- ★★★ 步骤 D: 新增的实时合集匹配逻辑 ★★★ ---
    if not processed_successfully:
        logger.warning(f"项目 {item_id} 的元数据处理未成功完成，跳过自定义合集匹配。")
        return

    try:
        tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
        if not tmdb_id:
            logger.debug("项目缺少TMDb ID，无法进行自定义合集匹配。")
            return

        # 1. 从我们的缓存表中获取刚刚存入的元数据
        item_metadata = db_handler.get_media_metadata_by_tmdb_id(config_manager.DB_PATH, tmdb_id) # (需要添加这个db函数)
        if not item_metadata:
            logger.warning(f"无法从本地缓存中找到TMDb ID为 {tmdb_id} 的元数据，无法匹配合集。")
            return

        # 2. 初始化筛选引擎并查找匹配的合集
        engine = FilterEngine(db_path=config_manager.DB_PATH)
        matching_collections = engine.find_matching_collections(item_metadata)

        if matching_collections:
            logger.info(f"影片《{item_metadata.get('title')}》匹配到 {len(matching_collections)} 个自定义合集，正在处理...")
            # 3. 遍历所有匹配的合集，并向其中追加当前项目
            for collection in matching_collections:
                emby_handler.append_item_to_collection(
                    collection_id=collection['emby_collection_id'],
                    item_emby_id=item_id, # 这是新入库项目的Emby ID
                    base_url=processor.emby_url,
                    api_key=processor.emby_api_key,
                    user_id=processor.emby_user_id
                )
        else:
            # 如果没有匹配到，只记录日志，然后函数会自然地继续往下执行
            logger.info(f"影片《{item_metadata.get('title')}》没有匹配到任何自定义合集。")
    except Exception as e:
        logger.error(f"为新入库项目 {item_id} 匹配自定义合集时发生意外错误: {e}", exc_info=True)

    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    # --- 新增：步骤 E - 为所属的常规媒体库生成封面 ---
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    try:
        # 1. 读取配置
        cover_config_path = os.path.join(config_manager.PERSISTENT_DATA_PATH, "cover_generator.json")
        cover_config = {}
        if os.path.exists(cover_config_path):
            with open(cover_config_path, 'r', encoding='utf-8') as f:
                cover_config = json.load(f)

        # 2. 检查开关
        if cover_config.get("enabled") and cover_config.get("transfer_monitor"):
            # 确保在获取 item_details 之后再记录日志
            item_details = emby_handler.get_emby_item_details(item_id, processor.emby_url, processor.emby_api_key, processor.emby_user_id)
            if not item_details:
                logger.error(f"Webhook 任务：无法获取项目 {item_id} 的详情，无法继续封面生成。")
                return

            logger.info(f"  -> 检测到 '{item_details.get('Name')}' 入库，将为其所属媒体库生成新封面...")
            
            # 3. 定位媒体库
            library_info = emby_handler.get_library_root_for_item(
                item_id, processor.emby_url, processor.emby_api_key, processor.emby_user_id
            )
            
            if not library_info:
                logger.warning(f"无法为项目 {item_id} 定位到其所属的媒体库根，跳过封面生成。")
                return

            library_id = library_info.get("Id")
            library_name = library_info.get("Name", library_id)
            
            # 4. 检查是否需要处理
            if library_info.get('CollectionType') not in ['movies', 'tvshows', 'boxsets', 'mixed', 'music']:
                logger.debug(f"父级 '{library_name}' 不是一个常规媒体库，跳过封面生成。")
                return

            server_id = 'main_emby'
            library_unique_id = f"{server_id}-{library_id}"
            if library_unique_id in cover_config.get("exclude_libraries", []):
                logger.info(f"媒体库 '{library_name}' 在忽略列表中，跳过。")
                return
            
            # 【【【核心修复：在这里也使用实时计数API】】】
            # 5. 定义类型映射
            TYPE_MAP = {
                'movies': 'Movie', 'tvshows': 'Series', 'music': 'MusicAlbum',
                'boxsets': 'BoxSet', 'mixed': 'Movie,Series'
            }
            collection_type = library_info.get('CollectionType')
            item_type_to_query = TYPE_MAP.get(collection_type)
            
            item_count = 0
            if library_id and item_type_to_query:
                logger.debug(f"正在为媒体库 '{library_name}' (ID: {library_id}) 实时查询 '{item_type_to_query}' 的总数...")
                # 调用我们之前增强过的、最可靠的计数函数
                item_count = emby_handler.get_item_count(
                    base_url=processor.emby_url,
                    api_key=processor.emby_api_key,
                    user_id=processor.emby_user_id,
                    parent_id=library_id,
                    item_type=item_type_to_query
                ) or 0
            
            # 6. 实例化服务并生成封面
            logger.info(f"  -> 正在为媒体库 '{library_name}' 生成封面 (当前实时数量: {item_count}) ---")
            cover_service = CoverGeneratorService(config=cover_config)
            cover_service.generate_for_library(
                emby_server_id=server_id,
                library=library_info,
                item_count=item_count 
            )

        else:
            logger.debug("封面生成器或入库监控未启用，跳过封面生成。")

    except Exception as e:
        logger.error(f"在新入库后执行精准封面生成时发生错误: {e}", exc_info=True)

    logger.trace(f"Webhook 任务及所有后续流程完成: {item_id}")
# --- 追剧 ---    
def task_process_watchlist(processor: WatchlistProcessor, item_id: Optional[str] = None):
    """
    【V9 - 启动器】
    调用处理器实例来执行追剧任务，并处理UI状态更新。
    """
    # 定义一个可以传递给处理器的回调函数
    def progress_updater(progress, message):
        # 这里的 task_manager.update_status_from_thread 是你项目中用于更新UI的函数
        task_manager.update_status_from_thread(progress, message)

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
        task_manager.update_status_from_thread(progress, message)

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
    task_manager.update_status_from_thread(0, "准备开始导入...")
    
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
        'custom_collections': '自建合集',
        'media_metadata': '媒体元数据',
    }

    try:
        backup = json.loads(file_content)
        backup_data = backup.get("data", {})
        stop_event = processor.get_stop_event()

        for table_name in tables_to_import:
            if table_name not in backup_data:
                logger.warning(f"请求恢复的表 '{table_name}' 在备份文件中不存在，将跳过。")

        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
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
                        logical_key = db_handler.TABLE_PRIMARY_KEYS.get(table_name)
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
                    task_manager.update_status_from_thread(100, "导入成功完成！")
                else:
                    conn.rollback()
                    logger.warning("任务被中止，数据库操作已回滚。")
                    task_manager.update_status_from_thread(-1, "任务已中止，所有更改已回滚。")

            except Exception as e:
                conn.rollback()
                logger.error(f"在事务处理期间发生严重错误，操作已回滚: {e}", exc_info=True)
                task_manager.update_status_from_thread(-1, f"数据库错误，操作已回滚: {e}")
                raise

    except Exception as e:
        logger.error(f"数据库恢复任务执行失败: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")
# ★★★ 重新处理单个项目 ★★★
def task_reprocess_single_item(processor: MediaProcessor, item_id: str, item_name_for_ui: str):
    """
    【最终版 - 职责分离】后台任务。
    此版本负责在任务开始时设置“正在处理”的状态，并执行核心逻辑。
    """
    logger.debug(f"--- 后台任务开始执行 ({item_name_for_ui}) ---")
    
    try:
        # ✨ 关键修改：任务一开始，就用“正在处理”的状态覆盖掉旧状态
        task_manager.update_status_from_thread(0, f"正在处理: {item_name_for_ui}")

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
        task_manager.update_status_from_thread(-1, f"处理失败: {item_name_for_ui}")
# --- 翻译演员任务 ---
def task_actor_translation_cleanup(processor):
    """
    【最终修正版】执行演员名翻译的查漏补缺工作，并使用正确的全局状态更新函数。
    """
    try:
        # ✨✨✨ 修正：直接调用全局函数，而不是processor的方法 ✨✨✨
        task_manager.update_status_from_thread(5, "正在准备需要翻译的演员数据...")
        
        # 1. 调用数据准备函数
        translation_map, name_to_persons_map = emby_handler.prepare_actor_translation_data(
            emby_url=processor.emby_url,
            emby_api_key=processor.emby_api_key,
            user_id=processor.emby_user_id,
            ai_translator=processor.ai_translator,
            stop_event=processor.get_stop_event()
        )

        if not translation_map:
            task_manager.update_status_from_thread(100, "任务完成，没有需要翻译的演员。")
            return

        total_to_update = len(translation_map)
        task_manager.update_status_from_thread(50, f"数据准备完毕，开始更新 {total_to_update} 个演员名...")
        
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
            task_manager.update_status_from_thread(progress, f"({processed_count}/{total_to_update}) 正在更新: {original_name} -> {translated_name}")

        # 任务结束时，也直接调用全局函数
        final_message = f"任务完成！共更新了 {update_count} 个演员名。"
        if processor.is_stop_requested():
            final_message = "任务已中断。"
        task_manager.update_status_from_thread(100, final_message)

    except Exception as e:
        logger.error(f"执行演员翻译任务时出错: {e}", exc_info=True)
        # 在异常处理中也直接调用全局函数
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")
# ★★★ 重新处理所有待复核项 ★★★
def task_reprocess_all_review_items(processor: MediaProcessor):
    """
    【已升级】后台任务：遍历所有待复核项并逐一以“强制在线获取”模式重新处理。
    """
    logger.info("--- 开始执行“重新处理所有待复核项”任务 [强制在线获取模式] ---")
    try:
        # +++ 核心修改 1：同时查询 item_id 和 item_name +++
        with db_handler.get_db_connection(processor.db_path) as conn:
            cursor = conn.cursor()
            # 从 failed_log 中同时获取 ID 和 Name
            cursor.execute("SELECT item_id, item_name FROM failed_log")
            # 将结果保存为一个字典列表，方便后续使用
            all_items = [{'id': row['item_id'], 'name': row['item_name']} for row in cursor.fetchall()]
        
        total = len(all_items)
        if total == 0:
            logger.info("待复核列表中没有项目，任务结束。")
            task_manager.update_status_from_thread(100, "待复核列表为空。")
            return

        logger.info(f"共找到 {total} 个待复核项需要以“强制在线获取”模式重新处理。")

        # +++ 核心修改 2：在循环中解包 item_id 和 item_name +++
        for i, item in enumerate(all_items):
            if processor.is_stop_requested():
                logger.info("任务被中止。")
                break
            
            item_id = item['id']
            item_name = item['name'] or f"ItemID: {item_id}" # 如果名字为空，提供一个备用名

            task_manager.update_status_from_thread(int((i/total)*100), f"正在重新处理 {i+1}/{total}: {item_name}")
            
            # +++ 核心修改 3：传递所有必需的参数 +++
            task_reprocess_single_item(processor, item_id, item_name)
            
            # 每个项目之间稍作停顿
            time.sleep(2) 

    except Exception as e:
        logger.error(f"重新处理所有待复核项时发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, "任务失败")
# ★★★ 全量图片同步的任务函数 ★★★
def task_full_image_sync(processor: MediaProcessor):
    """
    后台任务：调用 processor 的方法来同步所有图片。
    """
    # 直接把回调函数传进去
    processor.sync_all_media_assets(update_status_callback=task_manager.update_status_from_thread)
# ✨ 辅助函数，并发刷新合集使用
def _process_single_collection_concurrently(collection_data: dict, db_path: str, tmdb_api_key: str) -> dict:
    """
    【V4 - 纯粹电影版】
    在单个线程中处理单个电影合集的所有逻辑。
    这个函数现在可以完全信任传入的 collection_data 就是一个常规电影合集。
    """
    collection_id = collection_data['Id']
    collection_name = collection_data.get('Name', '')
    today_str = datetime.now().strftime('%Y-%m-%d')
    item_type = 'Movie'
    
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
            with db_handler.get_db_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT missing_movies_json FROM collections_info WHERE emby_collection_id = ?", (collection_id,))
                row = cursor.fetchone()
                previous_movies_map = {}
                if row and row[0]:
                    try:
                        previous_movies_map = {str(m['tmdb_id']): m for m in json.loads(row[0])}
                    except (json.JSONDecodeError, TypeError): pass
            
            for movie in details.get("parts", []):
                movie_tmdb_id = str(movie.get("id"))
                if not movie.get("release_date"): continue

                movie_status = "unknown"
                if movie_tmdb_id in emby_movie_tmdb_ids:
                    movie_status = "in_library"
                elif movie.get("release_date", '') > today_str:
                    movie_status = "unreleased"
                elif previous_movies_map.get(movie_tmdb_id, {}).get('status') == 'subscribed':
                    movie_status = "subscribed"
                else:
                    movie_status = "missing"

                all_movies_with_status.append({
                    "tmdb_id": movie_tmdb_id, "title": movie.get("title", ""), 
                    "release_date": movie.get("release_date"), "poster_path": movie.get("poster_path"), 
                    "status": movie_status
                })
            
            if any(m['status'] == 'missing' for m in all_movies_with_status):
                has_missing = True
                status = "has_missing"

    image_tag = collection_data.get("ImageTags", {}).get("Primary")
    poster_path = f"/Items/{collection_id}/Images/Primary?tag={image_tag}" if image_tag else None

    return {
        "emby_collection_id": collection_id, "name": collection_name, 
        "tmdb_collection_id": tmdb_id, "item_type": item_type,
        "status": status, "has_missing": has_missing, 
        "missing_movies_json": json.dumps(all_movies_with_status), 
        "last_checked_at": time.time(), "poster_path": poster_path, 
        "in_library_count": in_library_count
    }
# ★★★ 刷新合集的后台任务函数 ★★★
def task_refresh_collections(processor: MediaProcessor):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    task_manager.update_status_from_thread(0, "正在获取 Emby 合集列表...")
    try:
        emby_collections = emby_handler.get_all_collections_with_items(
            base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id
        )
        if emby_collections is None: raise RuntimeError("从 Emby 获取合集列表失败")

        total = len(emby_collections)
        task_manager.update_status_from_thread(5, f"共找到 {total} 个合集，准备开始并发处理...")

        # 清理数据库中已不存在的合集
        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
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
                task_manager.update_status_from_thread(progress, f"处理中: {collection_name[:20]}... ({processed_count}/{total})")

        if processor.is_stop_requested():
            logger.warning("任务被用户中断，部分数据可能未被处理。")
            # 即使被中断，我们依然保存已成功处理的结果
        
        # ✨ 所有并发任务完成后，在主线程中安全地、一次性地写入数据库
        if all_results:
            logger.info(f"并发处理完成，准备将 {len(all_results)} 条结果写入数据库...")
            with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
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
        task_manager.update_status_from_thread(-1, f"错误: {e}")
# ★★★ 带智能预判的自动订阅任务 ★★★
def task_auto_subscribe(processor: MediaProcessor):
    """
    【V5 - 最终完整版】
    全面覆盖原生电影合集、自定义电影合集、自定义剧集合集，并统一使用 'subscribed' 状态。
    """
    task_manager.update_status_from_thread(0, "正在启动智能订阅任务...")
    
    if not config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_AUTOSUB_ENABLED):
        logger.info("智能订阅总开关未开启，任务跳过。")
        task_manager.update_status_from_thread(100, "任务跳过：总开关未开启")
        return

    try:
        today = date.today()
        task_manager.update_status_from_thread(10, "智能订阅已启动...")
        successfully_subscribed_items = []

        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # ★★★ 1. 处理原生电影合集 (collections_info) ★★★
            if not processor.is_stop_requested():
                task_manager.update_status_from_thread(20, "正在检查原生电影合集...")
                sql_query_native_movies = "SELECT * FROM collections_info WHERE status = 'has_missing' AND missing_movies_json IS NOT NULL AND missing_movies_json != '[]'"
                cursor.execute(sql_query_native_movies)
                native_collections_to_check = cursor.fetchall()
                logger.info(f"【智能订阅-原生电影】找到 {len(native_collections_to_check)} 个有缺失影片的原生合集。")

                for collection in native_collections_to_check:
                    if processor.is_stop_requested(): break
                    
                    movies_to_keep = []
                    all_movies = json.loads(collection['missing_movies_json'])
                    movies_changed = False
                    
                    for movie in all_movies:
                        if processor.is_stop_requested(): break
                        if movie.get('status') == 'missing':
                            release_date_str = movie.get('release_date')
                            if not release_date_str:
                                movies_to_keep.append(movie)
                                continue
                            try:
                                release_date = datetime.strptime(release_date_str.strip(), '%Y-%m-%d').date()
                            except (ValueError, TypeError):
                                movies_to_keep.append(movie)
                                continue
                            
                            if release_date <= today:
                                if moviepilot_handler.subscribe_movie_to_moviepilot(movie, config_manager.APP_CONFIG):
                                    successfully_subscribed_items.append(f"电影《{movie['title']}》")
                                    movies_changed = True
                                    movie['status'] = 'subscribed'
                                    movies_to_keep.append(movie)
                                else:
                                    movies_to_keep.append(movie)
                            else:
                                movies_to_keep.append(movie)
                        else:
                            movies_to_keep.append(movie)
                            
                    if movies_changed:
                        new_missing_json = json.dumps(movies_to_keep)
                        new_status = 'ok' if not any(m.get('status') == 'missing' for m in movies_to_keep) else 'has_missing'
                        cursor.execute("UPDATE collections_info SET missing_movies_json = ?, status = ? WHERE emby_collection_id = ?", (new_missing_json, new_status, collection['emby_collection_id']))

            # --- 2. 处理智能追剧 ---
            if not processor.is_stop_requested():
                task_manager.update_status_from_thread(60, "正在检查缺失的剧集...")
                
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

            # ★★★ 3. 处理自定义合集 (custom_collections, item_type='Movie') ★★★
            if not processor.is_stop_requested():
                task_manager.update_status_from_thread(70, "正在检查自定义榜单合集...")
                
                # 步骤 1: 使用统一的SQL查询，获取所有需要处理的榜单
                sql_query_custom_collections = """
                    SELECT * FROM custom_collections 
                    WHERE type = 'list' AND health_status = 'has_missing' 
                    AND generated_media_info_json IS NOT NULL AND generated_media_info_json != '[]'
                """
                cursor.execute(sql_query_custom_collections)
                custom_collections_to_check = cursor.fetchall()
                logger.info(f"【智能订阅-自定义榜单】找到 {len(custom_collections_to_check)} 个有缺失媒体的自定义榜单。")

                # 步骤 2: 统一循环处理
                for collection in custom_collections_to_check:
                    if processor.is_stop_requested(): break
                    
                    collection_id = collection['id']
                    collection_name = collection['name']
                    
                    try:
                        # 步骤 2a: ★★★ 移植“防御性解析”逻辑，获取权威类型 ★★★
                        definition = json.loads(collection['definition_json'])
                        item_type_from_db = definition.get('item_type', 'Movie')

                        authoritative_type = None
                        if isinstance(item_type_from_db, list) and item_type_from_db:
                            authoritative_type = item_type_from_db[0] # 规则：取列表中的第一个作为该榜单的主要类型
                        elif isinstance(item_type_from_db, str):
                            authoritative_type = item_type_from_db
                        
                        if authoritative_type not in ['Movie', 'Series']:
                            logger.warning(f"合集 '{collection_name}' 的 item_type ('{authoritative_type}') 无法识别，将默认按 'Movie' 处理。")
                            authoritative_type = 'Movie'

                        # 步骤 2b: 遍历媒体列表，执行订阅
                        media_to_keep = []
                        all_media = json.loads(collection['generated_media_info_json'])
                        media_changed = False
                        
                        for media_item in all_media:
                            if processor.is_stop_requested(): break
                            
                            if media_item.get('status') == 'missing':
                                release_date_str = media_item.get('release_date')
                                if not release_date_str:
                                    media_to_keep.append(media_item)
                                    continue
                                try:
                                    release_date = datetime.strptime(release_date_str.strip(), '%Y-%m-%d').date()
                                except (ValueError, TypeError):
                                    media_to_keep.append(media_item)
                                    continue
                                
                                if release_date <= today:
                                    # ★★★ 使用权威类型来决定调用哪个订阅函数 ★★★
                                    success = False
                                    media_title = media_item.get('title', '未知标题')
                                    if authoritative_type == 'Movie':
                                        success = moviepilot_handler.subscribe_movie_to_moviepilot(media_item, config_manager.APP_CONFIG)
                                    elif authoritative_type == 'Series':
                                        series_info = { "item_name": media_title, "tmdb_id": media_item.get('tmdb_id') }
                                        success = moviepilot_handler.subscribe_series_to_moviepilot(series_info, season_number=None, config=config_manager.APP_CONFIG)
                                    
                                    if success:
                                        successfully_subscribed_items.append(f"{authoritative_type}《{media_title}》")
                                        media_changed = True
                                        media_item['status'] = 'subscribed'
                                        media_to_keep.append(media_item)
                                    else:
                                        media_to_keep.append(media_item)
                                else:
                                    media_to_keep.append(media_item)
                            else:
                                media_to_keep.append(media_item)
                        
                        # 步骤 2c: 如果有订阅成功，更新数据库
                        if media_changed:
                            new_missing_json = json.dumps(media_to_keep, ensure_ascii=False)
                            new_missing_count = sum(1 for m in media_to_keep if m.get('status') == 'missing')
                            new_health_status = 'has_missing' if new_missing_count > 0 else 'ok'
                            cursor.execute(
                                "UPDATE custom_collections SET generated_media_info_json = ?, health_status = ?, missing_count = ? WHERE id = ?", 
                                (new_missing_json, new_health_status, new_missing_count, collection_id)
                            )
                    except Exception as e_coll:
                        logger.error(f"【智能订阅】处理自定义合集 '{collection_name}' 时发生错误: {e_coll}", exc_info=True)

            conn.commit()

        if successfully_subscribed_items:
            summary = "任务完成！已自动订阅: " + ", ".join(successfully_subscribed_items)
            logger.info(summary)
            task_manager.update_status_from_thread(100, summary)
        else:
            task_manager.update_status_from_thread(100, "任务完成：本次运行没有发现符合自动订阅条件的媒体。")

    except Exception as e:
        logger.error(f"智能订阅任务失败: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"错误: {e}")
# ✨✨✨ 一键添加所有剧集到追剧列表的任务 ✨✨✨
def task_add_all_series_to_watchlist(processor: MediaProcessor):
    """
    后台任务：获取 Emby 中所有剧集，并批量添加到追剧列表。
    """
    task_name = "一键扫描全库剧集"
    logger.info(f"--- 开始执行 '{task_name}' 任务 ---")
    
    try:
        # 1. 从 processor 获取必要的配置
        emby_url = processor.emby_url
        emby_api_key = processor.emby_api_key
        emby_user_id = processor.emby_user_id
        db_path = processor.db_path
        
        # +++ 核心修改：智能获取要扫描的媒体库ID +++
        library_ids_to_process = config_manager.APP_CONFIG.get('emby_libraries_to_process', [])
        
        # 如果用户没有在 config.ini 中指定，我们就自动获取所有媒体库
        if not library_ids_to_process:
            logger.info("未在配置中指定媒体库，将自动扫描所有媒体库...")
            all_libraries = emby_handler.get_emby_libraries(emby_url, emby_api_key, emby_user_id)
            if all_libraries:
                # 只选择电影和剧集类型的库
                library_ids_to_process = [
                    lib['Id'] for lib in all_libraries 
                    if lib.get('CollectionType') in ['tvshows', 'mixed']
                ]
                logger.info(f"将扫描以下剧集库: {[lib['Name'] for lib in all_libraries if lib.get('CollectionType') in ['tvshows', 'mixed']]}")
            else:
                logger.warning("未能从 Emby 获取到任何媒体库。")
        
        if not library_ids_to_process:
            task_manager.update_status_from_thread(100, "任务完成：没有找到可供扫描的剧集媒体库。")
            return

        # 2. 调用 emby_handler 获取所有剧集
        task_manager.update_status_from_thread(10, "正在从 Emby 获取所有剧集...")
        # 注意：这里我们不再使用 get_all_series_from_emby，而是直接使用更通用的 get_emby_library_items
        all_series = emby_handler.get_emby_library_items(
            base_url=emby_url,
            api_key=emby_api_key,
            user_id=emby_user_id,
            library_ids=library_ids_to_process,
            media_type_filter="Series"
        )

        if all_series is None:
            raise RuntimeError("从 Emby 获取剧集列表失败，请检查网络和配置。")

        total = len(all_series)
        if total == 0:
            task_manager.update_status_from_thread(100, "任务完成：在指定的媒体库中未找到任何剧集。")
            return

        # ... (后续的筛选和数据库写入逻辑保持不变) ...
        task_manager.update_status_from_thread(30, f"共找到 {total} 部剧集，正在筛选...")
        series_to_insert = []
        for series in all_series:
            tmdb_id = series.get("ProviderIds", {}).get("Tmdb")
            item_name = series.get("Name")
            item_id = series.get("Id")
            if tmdb_id and item_name and item_id:
                series_to_insert.append({
                    "item_id": item_id, "tmdb_id": tmdb_id,
                    "item_name": item_name, "item_type": "Series"
                })

        if not series_to_insert:
            task_manager.update_status_from_thread(100, "任务完成：找到的剧集均缺少TMDb ID，无法添加。")
            return

        added_count = 0
        total_to_add = len(series_to_insert)
        task_manager.update_status_from_thread(60, f"筛选出 {total_to_add} 部有效剧集，准备写入数据库...")
        
        with db_handler.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            try:
                for series in series_to_insert:
                    cursor.execute("""
                        INSERT OR IGNORE INTO watchlist (item_id, tmdb_id, item_name, item_type, status)
                        VALUES (?, ?, ?, ?, 'Watching')
                    """, (series["item_id"], series["tmdb_id"], series["item_name"], series["item_type"]))
                    added_count += cursor.rowcount
                conn.commit()
            except Exception as e_db:
                conn.rollback()
                raise RuntimeError(f"数据库批量写入时发生错误: {e_db}")

        # 1. 先报告第一阶段任务的完成情况
        scan_complete_message = f"扫描完成！共发现 {total} 部剧集，新增 {added_count} 部。"
        logger.info(scan_complete_message)
        
        # 2. 如果确实有新增剧集，或者即使用户没新增也想刷新一下，就触发后续任务
        if added_count > 0:
            logger.info("--- 任务链：即将自动触发【检查所有在追剧集】任务 ---")
            
            # 更新UI状态，告诉用户即将进入下一阶段
            task_manager.update_status_from_thread(99, "扫描完成，正在启动追剧检查...")
            time.sleep(2) # 短暂暂停，让用户能看到状态变化

            # 提交新的任务
            # 注意：这里我们不能直接调用 task_process_watchlist，因为它需要一个新的后台线程
            # 我们需要通过 task_manager 来提交
            # 并且，我们不能在这里等待它完成，因为我们自己就在一个任务线程里
            
            # 最简单的实现是让前端在收到特定消息后触发
            # 但更健壮的后端实现如下：
            
            # 我们直接调用下一个任务的核心逻辑。
            # 注意：这会在同一个线程中执行，UI进度条会从99%直接跳到下一个任务的进度
            # 这是一个简单有效的实现。
            try:
                # 我们需要 WatchlistProcessor，但当前函数只有 MediaProcessor
                # 所以我们从 extensions 获取
                watchlist_proc = extensions.watchlist_processor_instance
                if watchlist_proc:
                    # 直接调用 watchlist_processor 的核心方法
                    watchlist_proc.run_regular_processing_task(
                        progress_callback=task_manager.update_status_from_thread,
                        item_id=None # None 表示处理所有
                    )
                    final_message = "自动化流程完成：扫描与追剧检查均已结束。"
                    task_manager.update_status_from_thread(100, final_message)
                else:
                    raise RuntimeError("WatchlistProcessor 未初始化，无法执行链式任务。")

            except Exception as e_chain:
                 logger.error(f"执行链式任务【检查所有在追剧集】时失败: {e_chain}", exc_info=True)
                 task_manager.update_status_from_thread(-1, f"链式任务失败: {e_chain}")

        else:
            # 如果没有新增剧集，就正常结束
            final_message = f"任务完成！共扫描到 {total} 部剧集，没有发现可新增的剧集。"
            logger.info(final_message)
            task_manager.update_status_from_thread(100, final_message)

    except Exception as e:
        logger.error(f"执行 '{task_name}' 任务时发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")
# --- 任务链 ---
def task_run_chain(processor: MediaProcessor, task_sequence: list):
    """
    【V2 - 核心修复版】自动化任务链。
    按顺序执行指定的一系列任务，并为每个任务智能选择正确的处理器。
    """
    task_name = "自动化任务链"
    total_tasks = len(task_sequence)
    logger.info(f"--- '{task_name}' 已启动，共包含 {total_tasks} 个子任务 ---")
    task_manager.update_status_from_thread(0, f"任务链启动，共 {total_tasks} 个任务。")

    # 获取所有可用任务的定义
    registry = get_task_registry()
    
    # 遍历用户定义的任务序列
    for i, task_key in enumerate(task_sequence):
        # 注意：这里的 processor.is_stop_requested() 依赖于 task_manager 传递进来的默认处理器
        # 这是一个可以接受的设计，因为停止信号是全局的。
        if processor.is_stop_requested():
            logger.warning(f"'{task_name}' 被用户中止。")
            break

        task_info = registry.get(task_key)
        if not task_info:
            logger.error(f"任务链警告：在注册表中未找到任务 '{task_key}'，已跳过。")
            continue

        task_function, task_description = task_info
        
        progress = int((i / total_tasks) * 100)
        status_message = f"({i+1}/{total_tasks}) 正在执行: {task_description}"
        logger.info(f"--- {status_message} ---")
        task_manager.update_status_from_thread(progress, status_message)

        try:
            # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
            # --- 【【【 这 是 核 心 修 改 点 2/2：智能选择处理器】】】 ---
            processor_to_use = None
            # 根据任务的唯一标识符 (key) 来判断使用哪个处理器
            if task_key in ['process-watchlist', 'refresh-single-watchlist-item']:
                processor_to_use = extensions.watchlist_processor_instance
                logger.debug(f"任务 '{task_description}' 将使用 WatchlistProcessor。")
            elif task_key in ['actor-tracking', 'scan-actor-media']:
                processor_to_use = extensions.actor_subscription_processor_instance
                logger.debug(f"任务 '{task_description}' 将使用 ActorSubscriptionProcessor。")
            else:
                # 默认情况下，使用通用的 MediaProcessor
                processor_to_use = extensions.media_processor_instance
                logger.debug(f"任务 '{task_description}' 将使用默认的 MediaProcessor。")

            if not processor_to_use:
                logger.error(f"任务链中的子任务 '{task_description}' 无法执行：对应的处理器未初始化，已跳过。")
                continue

            # ★★★ 使用我们刚刚智能选择的 `processor_to_use` 来执行任务 ★★★
            task_function(processor_to_use)
            # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
            
            # 子任务成功执行后，短暂等待，让状态更新能被前端捕获
            time.sleep(1) 

        except Exception as e:
            error_message = f"任务链中的子任务 '{task_description}' 执行失败: {e}"
            logger.error(error_message, exc_info=True)
            # 更新UI状态以反映错误，但任务链会继续
            task_manager.update_status_from_thread(progress, f"子任务'{task_description}'失败，继续...")
            time.sleep(3) # 让用户能看到错误信息
            continue # 继续下一个任务

    final_message = f"'{task_name}' 执行完毕。"
    if processor.is_stop_requested():
        final_message = f"'{task_name}' 已中止。"
    
    logger.info(f"--- {final_message} ---")
    task_manager.update_status_from_thread(100, "任务链已全部执行完毕。")
# --- 任务注册表 ---
def get_task_registry(context: str = 'all'):
    """
    【V2】返回一个包含所有可执行任务的字典，并可根据上下文筛选。
    :param context: 'all' (默认) 返回所有任务, 'chain' 只返回适合任务链的任务。
    """
    # 完整的任务注册表
    full_registry = {
        'task-chain': (task_run_chain, "自动化任务链", False), # 第三个元素表示是否适合任务链
        
        # --- 适合任务链的常规任务 ---
        'full-scan': (task_run_full_scan, "全量处理媒体", True),
        'populate-metadata': (task_populate_metadata_cache, "同步媒体数据", True),
        'sync-person-map': (task_sync_person_map, "同步演员映射", True),
        'sync-images-map': (task_full_image_sync, "覆盖缓存备份", True),
        'process-watchlist': (task_process_watchlist, "智能追剧更新", True),
        'enrich-aliases': (task_enrich_aliases, "演员数据补充", True),
        'actor-cleanup': (task_actor_translation_cleanup, "演员姓名翻译", True),
        'refresh-collections': (task_refresh_collections, "原生合集刷新", True),
        'custom-collections': (task_process_all_custom_collections, "自建合集刷新", True),
        'auto-subscribe': (task_auto_subscribe, "智能订阅缺失", True),
        'actor-tracking': (task_process_actor_subscriptions, "演员订阅扫描", True),
        'generate-all-covers': (task_generate_all_covers, "生成所有封面", True),

        # --- 不适合任务链的、需要特定参数的任务 ---
        'process_all_custom_collections': (task_process_all_custom_collections, "生成所有自建合集", False),
        'process-single-custom-collection': (task_process_custom_collection, "生成单个自建合集", False),
        # 更多需要参数的任务可以加在这里，例如：
        # 'reprocess-single-item': (task_reprocess_single_item, "重新处理单个项目", False),
    }

    if context == 'chain':
        return {key: (info[0], info[1]) for key, info in full_registry.items() if info[2]}
    
    # 默认返回不包含第三个布尔值的简化版，以兼容旧的调用
    return {key: (info[0], info[1]) for key, info in full_registry.items()}

# ★★★ 一键生成所有合集的后台任务，核心优化在于只获取一次Emby媒体库 ★★★
def task_process_all_custom_collections(processor: MediaProcessor):
    """
    【V3 - 终极完整版】处理所有已启用的自定义合集。
    不仅在Emby中创建/更新，还为每个合集执行完整的健康状态分析并写入数据库。
    """
    task_name = "生成所有自建合集"
    logger.info(f"--- 开始执行 '{task_name}' 任务 ---")

    try:
        # --- 步骤 1: 获取所有启用的自定义合集定义 ---
        task_manager.update_status_from_thread(0, "正在获取所有启用的合集定义...")
        active_collections = db_handler.get_all_active_custom_collections(config_manager.DB_PATH)
        if not active_collections:
            logger.info("没有找到任何已启用的自定义合集，任务结束。")
            task_manager.update_status_from_thread(100, "没有已启用的合集。")
            return
        
        total = len(active_collections)
        logger.info(f"共找到 {total} 个已启用的自定义合集需要处理。")

        # --- 步骤 2: 【核心优化】一次性获取所有需要的数据 ---
        task_manager.update_status_from_thread(2, "正在从Emby获取全库媒体数据...")
        libs_to_process_ids = processor.config.get("libraries_to_process", [])
        if not libs_to_process_ids: raise ValueError("未在配置中指定要处理的媒体库。")
        
        movies = emby_handler.get_emby_library_items(base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id, media_type_filter="Movie", library_ids=libs_to_process_ids) or []
        series = emby_handler.get_emby_library_items(base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id, media_type_filter="Series", library_ids=libs_to_process_ids) or []
        all_emby_items = movies + series
        logger.info(f"已从Emby获取 {len(all_emby_items)} 个媒体项目。")

        task_manager.update_status_from_thread(5, "正在从Emby获取现有合集列表...")
        all_emby_collections = emby_handler.get_all_collections_from_emby_generic(
            base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id
        ) or []
        
        prefetched_collection_map = {coll.get('Name', '').lower(): coll for coll in all_emby_collections}
        logger.info(f"已预加载 {len(prefetched_collection_map)} 个现有合集的信息。")

        # --- 步骤 3: 遍历所有合集，在内存中进行处理 ---
        for i, collection in enumerate(active_collections):
            if processor.is_stop_requested():
                logger.warning("任务被用户中止。")
                break

            collection_id = collection['id']
            collection_name = collection['name']
            collection_type = collection['type']
            definition = json.loads(collection['definition_json'])
            item_type_for_collection = definition.get('item_type', 'Movie')
            
            progress = 10 + int((i / total) * 90)
            task_manager.update_status_from_thread(progress, f"({i+1}/{total}) 正在处理: {collection_name}")

            try:
                # 3a. 生成目标TMDb ID列表
                definition = json.loads(collection['definition_json'])
                item_types_for_collection = definition.get('item_type', ['Movie'])
                tmdb_items = []
                if collection_type == 'list':
                    importer = ListImporter(processor.tmdb_api_key)
                    tmdb_items = importer.process(definition)
                elif collection_type == 'filter':
                    engine = FilterEngine(db_path=config_manager.DB_PATH)
                    tmdb_items = engine.execute_filter(definition)
                
                tmdb_ids = [item['id'] for item in tmdb_items]

                if not tmdb_ids:
                    logger.warning(f"合集 '{collection_name}' 未能生成任何媒体ID，跳过。")
                    db_handler.update_custom_collection_after_sync(config_manager.DB_PATH, collection_id, {"emby_collection_id": None})
                    continue

                # 3b. 在Emby中创建/更新合集
                result_tuple = emby_handler.create_or_update_collection_with_tmdb_ids(
                    collection_name=collection_name, 
                    tmdb_ids=tmdb_ids, 
                    base_url=processor.emby_url,
                    api_key=processor.emby_api_key, 
                    user_id=processor.emby_user_id,
                    prefetched_emby_items=all_emby_items, 
                    prefetched_collection_map=prefetched_collection_map,
                    item_types=item_types_for_collection # ★ 传递类型列表
                )
                
                if not result_tuple:
                    raise RuntimeError("在Emby中创建或更新合集失败。")
                
                emby_collection_id, tmdb_ids_in_library = result_tuple

                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                # ★★★ 核心改造：在这里执行完整的健康状态分析和数据准备 ★★★
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                update_data = {
                    "emby_collection_id": emby_collection_id,
                    # ★ item_type 现在从 definition 中获取，并存为JSON字符串
                    "item_type": json.dumps(definition.get('item_type', ['Movie'])),
                    "last_synced_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }

                if not emby_collection_id:
                    logger.warning(f"合集 '{collection_name}' 未能在Emby中创建，跳过分析。")
                elif collection_type == 'list':

                    # ▼▼▼ 核心修正点 ▼▼▼
                    # 在分析前，加载当前已存在的媒体状态信息，以便保留 'subscribed' 状态
                    previous_media_map = {}
                    try:
                        # collection 对象是从数据库循环中得到的，它包含了旧的JSON数据
                        previous_media_list = json.loads(collection.get('generated_media_info_json') or '[]')
                        previous_media_map = {str(m.get('tmdb_id')): m for m in previous_media_list}
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"解析合集 {collection_name} 的旧媒体JSON失败，将无法保留'subscribed'状态。")

                    # 对榜单类型进行详细分析
                    existing_tmdb_ids = set(map(str, tmdb_ids_in_library))
                    emby_collection_details = emby_handler.get_emby_item_details(emby_collection_id, processor.emby_url, processor.emby_api_key, processor.emby_user_id)
                    image_tag = emby_collection_details.get("ImageTags", {}).get("Primary")
                    
                    all_media_details = []
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        future_to_item = {}
                        for item in tmdb_items:
                            if item['type'] == 'Series':
                                future = executor.submit(tmdb_handler.get_tv_details_tmdb, item['id'], processor.tmdb_api_key)
                            else: # 默认为 Movie
                                future = executor.submit(tmdb_handler.get_movie_details, item['id'], processor.tmdb_api_key)
                            future_to_item[future] = item

                        for future in concurrent.futures.as_completed(future_to_item):
                            item_info = future_to_item[future]
                            try:
                                detail = future.result()
                                if detail:
                                    all_media_details.append(detail)
                            except Exception as exc:
                                logger.error(f"获取 TMDb 详情 (ID: {item_info['id']}, Type: {item_info['type']}) 时线程内发生错误: {exc}")
                    
                    all_media_with_status, has_missing, missing_count = [], False, 0
                    today_str = datetime.now().strftime('%Y-%m-%d')
                    for media in all_media_details:
                        if not media: continue
                        media_tmdb_id = str(media.get("id"))
                        release_date = media.get("release_date") or media.get("first_air_date", '')
                        
                        # ▼▼▼ 核心修正点：修正状态判断的优先级 ▼▼▼
                        if media_tmdb_id in existing_tmdb_ids:
                            status = "in_library"
                        elif previous_media_map.get(media_tmdb_id, {}).get('status') == 'subscribed':
                            status = "subscribed" # 优先保留已订阅状态
                        elif release_date and release_date > today_str:
                            status = "unreleased"
                        else:
                            status, has_missing, missing_count = "missing", True, missing_count + 1
                        
                        all_media_with_status.append({
                            "tmdb_id": media_tmdb_id, "title": media.get("title") or media.get("name"),
                            "release_date": release_date, "poster_path": media.get("poster_path"), "status": status
                        })

                    update_data.update({
                        "health_status": "has_missing" if has_missing else "ok",
                        "in_library_count": len(existing_tmdb_ids), "missing_count": missing_count,
                        "generated_media_info_json": json.dumps(all_media_with_status, ensure_ascii=False),
                        "poster_path": f"/Items/{emby_collection_id}/Images/Primary?tag={image_tag}" if image_tag else None
                    })
                else: # 对于 'filter' 类型，写入默认的健康状态
                    update_data.update({
                        "health_status": "ok", "in_library_count": len(tmdb_ids_in_library),
                        "missing_count": 0, "generated_media_info_json": '[]', "poster_path": None
                    })
                
                # 3c. ★★★ 核心改造：调用新的、功能更全的数据库更新函数 ★★★
                db_handler.update_custom_collection_after_sync(config_manager.DB_PATH, collection_id, update_data)
                logger.info(f"合集 '{collection_name}' 处理完成，并已更新数据库状态。")

            except Exception as e_coll:
                logger.error(f"处理合集 '{collection_name}' (ID: {collection_id}) 时发生错误: {e_coll}", exc_info=True)
                continue

        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        # --- 新增：封面生成逻辑 ---
        # 在所有合集都处理完毕后，统一开始生成封面
        # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
        try:
            cover_config_path = os.path.join(config_manager.PERSISTENT_DATA_PATH, "cover_generator.json")
            cover_config = {}
            if os.path.exists(cover_config_path):
                with open(cover_config_path, 'r', encoding='utf-8') as f:
                    cover_config = json.load(f)

            if cover_config.get("enabled"):
                logger.info("检测到封面生成器已启用，将为所有已处理的合集生成封面...")
                task_manager.update_status_from_thread(95, "合集同步完成，开始生成封面...")
                
                cover_service = CoverGeneratorService(config=cover_config)
                
                # ★★★ 核心逻辑：再次查询数据库，获取所有刚刚被更新了 Emby ID 的合集 ★★★
                # 这样做比在循环中传递变量更健壮
                updated_collections = db_handler.get_all_active_custom_collections(config_manager.DB_PATH)

                for collection in updated_collections:
                    collection_name = collection.get('name')
                    emby_collection_id = collection.get('emby_collection_id')
                    
                    if emby_collection_id:
                        logger.info(f"  -> 正在为合集 '{collection_name}' 生成封面")
                        # 同样，您需要有方法确定服务器ID
                        server_id = 'main_emby' 
                        
                        library_info = emby_handler.get_emby_item_details(
                            emby_collection_id, 
                            processor.emby_url, 
                            processor.emby_api_key, 
                            processor.emby_user_id
                        )
                        
                        if library_info:
                            # 从数据库记录中获取已入库和缺失的数量
                            item_count_to_pass = collection.get('in_library_count', 0)
                            cover_service.generate_for_library(
                                emby_server_id=server_id,
                                library=library_info,
                                item_count=item_count_to_pass
                            )
                        else:
                            logger.warning(f"无法获取 Emby 合集 {emby_collection_id} 的详情，跳过封面生成。")
                    else:
                        logger.debug(f"合集 '{collection_name}' 没有关联的 Emby ID，跳过封面生成。")

        except Exception as e:
            logger.error(f"在任务末尾执行批量封面生成时失败: {e}", exc_info=True)
        # --- 封面生成逻辑结束 ---
        
        final_message = "所有启用的自定义合集均已处理完毕！"
        if processor.is_stop_requested(): final_message = "任务已中止。"
        
        task_manager.update_status_from_thread(100, final_message)
        logger.info(f"--- '{task_name}' 任务成功完成 ---")

    except Exception as e:
        logger.error(f"执行 '{task_name}' 任务时发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")

# --- 处理单个自定义合集的核心任务 ---
def task_process_custom_collection(processor: MediaProcessor, custom_collection_id: int):
    """
    【V8 - 状态持久化修复版】处理单个自定义合集。
    - 修正了状态判断逻辑，确保在重新生成时能正确保留 'subscribed' 状态。
    """
    task_name = f"处理自定义合集 (ID: {custom_collection_id})"
    logger.info(f"--- 开始执行 '{task_name}' 任务 ---")
    
    try:
        # --- 步骤 1: 获取定义并生成TMDb ID列表 ---
        task_manager.update_status_from_thread(0, "正在读取合集定义...")
        collection = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, custom_collection_id)
        if not collection: raise ValueError(f"未找到ID为 {custom_collection_id} 的自定义合集。")
        
        collection_name = collection['name']
        collection_type = collection['type']
        definition = json.loads(collection['definition_json'])
        
        item_types_for_collection = definition.get('item_type', ['Movie'])
        
        tmdb_items = []
        if collection_type == 'list':
            importer = ListImporter(processor.tmdb_api_key)
            # importer.process 现在返回 [{'id': '123', 'type': 'Movie'}, ...]
            tmdb_items = importer.process(definition)
        elif collection_type == 'filter':
            engine = FilterEngine(db_path=config_manager.DB_PATH)
            # engine.execute_filter 现在也返回相同的结构
            tmdb_items = engine.execute_filter(definition)
        
        tmdb_ids = [item['id'] for item in tmdb_items]
        
        if not tmdb_ids:
            logger.warning(f"合集 '{collection_name}' 未能生成任何媒体ID，任务结束。")
            return

        # --- 步骤 2: 在Emby中创建/更新合集 ---
        task_manager.update_status_from_thread(70, f"已生成 {len(tmdb_items)} 个ID，正在Emby中创建/更新合集...")
        libs_to_process_ids = processor.config.get("libraries_to_process", [])

        result_tuple = emby_handler.create_or_update_collection_with_tmdb_ids(
            collection_name=collection_name, 
            tmdb_ids=tmdb_ids, 
            base_url=processor.emby_url,
            api_key=processor.emby_api_key, 
            user_id=processor.emby_user_id,
            library_ids=libs_to_process_ids, 
            item_types=item_types_for_collection # ★ 传递类型列表
        )

        if not result_tuple:
            raise RuntimeError("在Emby中创建或更新合集失败。")
        
        emby_collection_id, tmdb_ids_in_library = result_tuple

        if not emby_collection_id:
            logger.warning(f"合集 '{collection_name}' 未能在Emby中创建（可能无匹配项），跳过缺失分析。")
            db_handler.update_custom_collection_after_sync(config_manager.DB_PATH, custom_collection_id, {"emby_collection_id": emby_collection_id})
            task_manager.update_status_from_thread(100, "任务完成，未在Emby中创建合集。")
            return

        # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
        # ★★★ 核心改造：分析合集状态并准备写入 custom_collections 表 ★★★
        # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
        
        update_data = {
            "emby_collection_id": emby_collection_id,
            # ★ item_type 现在从 definition 中获取，因为它可能是个列表
            "item_type": json.dumps(definition.get('item_type', ['Movie'])),
            "last_synced_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        # 只有 'list' 类型的合集才需要进行详细的缺失分析
        if collection_type == 'list':
            task_manager.update_status_from_thread(90, "榜单合集已生成/更新，正在并行获取详情...")
            
            # ▼▼▼ 核心修正点 ▼▼▼
            # 在分析前，加载当前已存在的媒体状态信息
            previous_media_map = {}
            try:
                # collection 对象是从数据库里读出来的，它包含了旧的JSON数据
                previous_media_list = json.loads(collection.get('generated_media_info_json') or '[]')
                previous_media_map = {str(m.get('tmdb_id')): m for m in previous_media_list}
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"解析合集 {collection_name} 的旧媒体JSON失败，将无法保留'subscribed'状态。")

            existing_tmdb_ids = set(map(str, tmdb_ids_in_library))
            
            emby_collection_details = emby_handler.get_emby_item_details(emby_collection_id, processor.emby_url, processor.emby_api_key, processor.emby_user_id)
            image_tag = emby_collection_details.get("ImageTags", {}).get("Primary")
            
            all_media_details = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_item = {}
                for item in tmdb_items:
                    if item['type'] == 'Series':
                        future = executor.submit(tmdb_handler.get_tv_details_tmdb, item['id'], processor.tmdb_api_key)
                    else: # 默认为 Movie
                        future = executor.submit(tmdb_handler.get_movie_details, item['id'], processor.tmdb_api_key)
                    future_to_item[future] = item

                for future in concurrent.futures.as_completed(future_to_item):
                    item_info = future_to_item[future]
                    try:
                        detail = future.result()
                        if detail:
                            all_media_details.append(detail)
                    except Exception as exc:
                        logger.error(f"获取 TMDb 详情 (ID: {item_info['id']}, Type: {item_info['type']}) 时线程内发生错误: {exc}")
            
            all_media_with_status, has_missing, missing_count = [], False, 0
            today_str = datetime.now().strftime('%Y-%m-%d')
            for media in all_media_details:
                if not media: continue
                media_tmdb_id = str(media.get("id"))
                media_status = "unknown"
                release_date = media.get("release_date") or media.get("first_air_date", '')
            
                # ▼▼▼ 核心修正点：修正状态判断的优先级，并移除重复逻辑 ▼▼▼
                # 1. 检查是否在库
                if media_tmdb_id in existing_tmdb_ids:
                    media_status = "in_library"
                # 2. 如果不在库，检查之前是否为“已订阅”
                elif previous_media_map.get(media_tmdb_id, {}).get('status') == 'subscribed':
                    media_status = "subscribed"  # 保留已订阅状态！
                # 3. 如果也不是已订阅，检查是否“未上映”
                elif release_date and release_date > today_str:
                    media_status = "unreleased"
                # 4. 都不是，则为“缺失”
                else:
                    media_status, has_missing, missing_count = "missing", True, missing_count + 1
                
                all_media_with_status.append({
                    "tmdb_id": media_tmdb_id, 
                    "title": media.get("title") or media.get("name"),
                    "release_date": release_date, 
                    "poster_path": media.get("poster_path"),
                    "status": media_status
                })

            update_data.update({
                "health_status": "has_missing" if has_missing else "ok",
                "in_library_count": len(existing_tmdb_ids),
                "missing_count": missing_count,
                "generated_media_info_json": json.dumps(all_media_with_status, ensure_ascii=False),
                "poster_path": f"/Items/{emby_collection_id}/Images/Primary?tag={image_tag}" if image_tag else None
            })
            logger.info(f"已为RSS合集 '{collection_name}' 分析健康状态。")
        else: # 对于 'filter' 类型
            task_manager.update_status_from_thread(95, "筛选合集已生成，跳过缺失分析。")
            update_data.update({
                "health_status": "ok",
                "in_library_count": len(tmdb_ids_in_library),
                "missing_count": 0,
                "generated_media_info_json": '[]',
                "poster_path": None
            })

        # --- 步骤 3: 统一更新数据库 ---
        db_handler.update_custom_collection_after_sync(config_manager.DB_PATH, custom_collection_id, update_data)
        logger.info(f"已更新自定义合集 '{collection_name}' (ID: {custom_collection_id}) 的同步状态和健康信息。")


        # --- 封面生成逻辑 ---
        try:
            cover_config_path = os.path.join(config_manager.PERSISTENT_DATA_PATH, "cover_generator.json")
            cover_config = {}
            if os.path.exists(cover_config_path):
                with open(cover_config_path, 'r', encoding='utf-8') as f:
                    cover_config = json.load(f)

            # 检查插件是否在配置中启用
            if cover_config.get("enabled"):
                logger.info(f"检测到封面生成器已启用，将为合集 '{collection_name}' 生成封面...")
                
                # 实例化服务
                cover_service = CoverGeneratorService(config=cover_config)
                
                # emby_collection_id 变量在上面已经获取到了
                if emby_collection_id:
                    # 您需要有一种方式来确定当前操作的服务器ID
                    # 这里我们先用一个占位符，您需要根据项目结构替换它
                    server_id = 'main_emby' 
                    
                    # 获取合集的详细信息，作为 library_info 传递
                    library_info = emby_handler.get_emby_item_details(
                        emby_collection_id, 
                        processor.emby_url, 
                        processor.emby_api_key, 
                        processor.emby_user_id
                    )
                    
                    if library_info:
                        # update_data 字典里包含了我们刚刚计算好的数量信息
                        in_library_count = update_data.get('in_library_count', 0)
                        
                        # 同样，我们只关心已入库的数量
                        item_count_to_pass = in_library_count

                        cover_service.generate_for_library(
                            emby_server_id=server_id,
                            library=library_info,
                            item_count=item_count_to_pass # <--- 传递修正后的值
                        )
                    else:
                        logger.warning(f"无法获取 Emby 合集 {emby_collection_id} 的详情，跳过封面生成。")

        except Exception as e:
            logger.error(f"为合集 '{collection_name}' 生成封面时发生错误: {e}", exc_info=True)
        # --- 封面生成逻辑结束 ---

        task_manager.update_status_from_thread(100, "自定义合集同步并分析完成！")

    except Exception as e:
        logger.error(f"执行 '{task_name}' 任务时发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★★★ 新增：轻量级的元数据缓存填充任务 ★★★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
def task_populate_metadata_cache(processor: 'MediaProcessor'):
    """
    通过批量预加载和并发处理，大幅提升元数据缓存的速度。
    """
    task_name = "同步媒体数据"
    logger.info(f"--- 开始执行 '{task_name}' 任务 (完整性能最终版) ---")
    
    task_manager = getattr(processor, 'task_manager', None)
    if not task_manager:
        # ... (FakeTaskManager fallback logic) ...
        class FakeTaskManager:
            def update_status_from_thread(self, *args, **kwargs): pass
        task_manager = FakeTaskManager()

    try:
        task_manager.update_status_from_thread(0, "阶段1/4: 批量获取Emby媒体索引...")
        
        # ======================================================================
        # 步骤 1: 一次性获取所有 Emby 媒体项的完整详情
        # ======================================================================
        libs_to_process_ids = processor.config.get("libraries_to_process", [])
        if not libs_to_process_ids:
            raise ValueError("未在配置中指定要处理的媒体库。")

        all_emby_items = emby_handler.get_emby_library_items(
            base_url=processor.emby_url, api_key=processor.emby_api_key, user_id=processor.emby_user_id,
            media_type_filter="Movie,Series", library_ids=libs_to_process_ids,
            fields="ProviderIds,Type,DateCreated,Name,ProductionYear,OriginalTitle,PremiereDate,CommunityRating,Genres,Studios,ProductionLocations,People"
        ) or []
        
        total = len(all_emby_items)
        if total == 0:
            task_manager.update_status_from_thread(100, "未找到任何媒体项。")
            return

        # ======================================================================
        # 步骤 2: 一次性增强所有演员
        # ======================================================================
        task_manager.update_status_from_thread(25, f"阶段2/4: 批量同步所有演员信息...")
        
        all_people_to_enrich = [person for item in all_emby_items for person in item.get("People", [])]
        logger.info(f"共找到 {len(all_people_to_enrich)} 个演员条目需要进行同步处理...")
        enriched_people_list = processor._enrich_cast_from_db_and_api(all_people_to_enrich)
        enriched_people_map = {str(p.get("Id")): p for p in enriched_people_list}
        logger.info("所有演员信息同步完成，等待在线获取导演元数据...")

        # ======================================================================
        # 步骤 3: 并发获取所有 TMDB 补充数据
        # ======================================================================
        task_manager.update_status_from_thread(50, f"阶段3/4: 并发获取TMDB补充数据...")
        
        tmdb_details_map = {}
        
        def fetch_tmdb_details(item):
            tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
            item_type = item.get("Type")
            if not tmdb_id: return None, None
            
            details = None
            if item_type == 'Movie':
                details = tmdb_handler.get_movie_details(tmdb_id, processor.tmdb_api_key)
            elif item_type == 'Series':
                details = tmdb_handler.get_tv_details_tmdb(tmdb_id, processor.tmdb_api_key)
            
            return tmdb_id, details

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_tmdb_id = {executor.submit(fetch_tmdb_details, item): item.get("ProviderIds", {}).get("Tmdb") for item in all_emby_items}
            for future in concurrent.futures.as_completed(future_to_tmdb_id):
                tmdb_id, details = future.result()
                if tmdb_id and details:
                    tmdb_details_map[tmdb_id] = details
        
        logger.info(f"成功并发获取了 {len(tmdb_details_map)} 个媒体项的TMDB详情。")

        # ======================================================================
        # 步骤 4: 在内存中组装数据并准备批量写入
        # ======================================================================
        task_manager.update_status_from_thread(75, f"阶段4/4: 组装数据并写入数据库...")
        
        metadata_batch = []
        for item in all_emby_items:
            tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
            if not tmdb_id: continue

            full_details_emby = item
            tmdb_details = tmdb_details_map.get(tmdb_id)

            # --- 组装演员 ---
            actors = []
            for person in full_details_emby.get("People", []):
                person_id = str(person.get("Id"))
                enriched_person = enriched_people_map.get(person_id)
                if enriched_person and enriched_person.get("ProviderIds", {}).get("Tmdb"):
                    actors.append({
                        'id': enriched_person["ProviderIds"]["Tmdb"],
                        'name': enriched_person.get('Name')
                    })
            
            # --- 组装导演和国家 ---
            directors, countries = [], []
            if tmdb_details:
                item_type = full_details_emby.get("Type")
                if item_type == 'Movie':
                    credits_data = tmdb_details.get("credits", {}) or tmdb_details.get("casts", {})
                    if credits_data:
                        directors = [{'id': p.get('id'), 'name': p.get('name')} for p in credits_data.get('crew', []) if p.get('job') == 'Director']
                    countries = translate_country_list([c['name'] for c in tmdb_details.get('production_countries', [])])
                elif item_type == 'Series':
                    credits_data = tmdb_details.get("credits", {})
                    if credits_data:
                        directors = [{'id': p.get('id'), 'name': p.get('name')} for p in credits_data.get('crew', []) if p.get('job') == 'Director']
                    if not directors: directors = [{'id': c.get('id'), 'name': c.get('name')} for c in tmdb_details.get('created_by', [])]
                    countries = translate_country_list(tmdb_details.get('origin_country', []))

            # ★★★★★★★★★★★★★★★ 变量定义修复 START ★★★★★★★★★★★★★★★
            # 提取工作室 (从 Emby 获取)
            studios = [s['Name'] for s in full_details_emby.get('Studios', []) if s.get('Name')]
            
            # 提取上映日期 (从 Emby 获取)
            release_date_str = (full_details_emby.get('PremiereDate') or '0000-01-01T00:00:00.000Z').split('T')[0]
            # ★★★★★★★★★★★★★★★ 变量定义修复 END ★★★★★★★★★★★★★★★

            metadata_to_save = {
                "tmdb_id": tmdb_id,
                "item_type": full_details_emby.get("Type"),
                "title": full_details_emby.get('Name'),
                "original_title": full_details_emby.get('OriginalTitle'),
                "release_year": full_details_emby.get('ProductionYear'),
                "rating": full_details_emby.get('CommunityRating'),
                "release_date": release_date_str,
                "date_added": (full_details_emby.get("DateCreated") or '').split('T')[0] or None,
                "genres_json": json.dumps(full_details_emby.get('Genres', []), ensure_ascii=False),
                "actors_json": json.dumps(actors, ensure_ascii=False),
                "directors_json": json.dumps(directors, ensure_ascii=False),
                "studios_json": json.dumps(studios, ensure_ascii=False),
                "countries_json": json.dumps(countries, ensure_ascii=False),
            }
            metadata_batch.append(metadata_to_save)

        # 步骤 4: 批量写入数据库
        if metadata_batch:
            task_manager.update_status_from_thread(95, f"提取完成，正在将 {len(metadata_batch)} 条数据写入数据库...")
            db_handler.bulk_replace_media_metadata(config_manager.DB_PATH, metadata_batch)

        task_manager.update_status_from_thread(100, f"元数据同步完成！共处理 {len(metadata_batch)} 条。")
        logger.info(f"--- '{task_name}' 任务成功完成 ---")

    except Exception as e:
        logger.error(f"执行 '{task_name}' 任务时发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")

        

# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★★★ 新增：立即生成所有媒体库封面的后台任务 ★★★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
def task_generate_all_covers(processor: MediaProcessor):
    """
    后台任务：为所有（未被忽略的）媒体库生成封面。
    """
    task_name = "一键生成所有媒体库封面"
    logger.info(f"--- 开始执行 '{task_name}' 任务 ---")
    
    try:
        # 1. 读取配置
        cover_config_path = os.path.join(config_manager.PERSISTENT_DATA_PATH, "cover_generator.json")
        if not os.path.exists(cover_config_path):
            task_manager.update_status_from_thread(-1, "错误：找不到封面生成器配置文件。")
            return

        with open(cover_config_path, 'r', encoding='utf-8') as f:
            cover_config = json.load(f)

        if not cover_config.get("enabled"):
            task_manager.update_status_from_thread(100, "任务跳过：封面生成器未启用。")
            return

        # 2. 获取媒体库列表
        task_manager.update_status_from_thread(5, "正在获取所有媒体库列表...")
        all_libraries = emby_handler.get_emby_libraries(
            base_url=processor.emby_url,
            api_key=processor.emby_api_key,
            user_id=processor.emby_user_id
        )
        if not all_libraries:
            task_manager.update_status_from_thread(-1, "错误：未能从Emby获取到任何媒体库。")
            return

        # 3. 筛选媒体库
        # ★★★ 核心修复：直接使用原始ID进行比较 ★★★
        exclude_ids = set(cover_config.get("exclude_libraries", []))
        
        libraries_to_process = [
            lib for lib in all_libraries 
            if lib.get('Id') not in exclude_ids  # <-- 修正了这里的判断逻辑
            and lib.get('CollectionType') in ['movies', 'tvshows', 'boxsets', 'mixed', 'music']
        ]
        
        total = len(libraries_to_process)
        if total == 0:
            task_manager.update_status_from_thread(100, "任务完成：没有需要处理的媒体库。")
            return
            
        logger.info(f"将为 {total} 个媒体库生成封面: {[lib['Name'] for lib in libraries_to_process]}")
        
        # 4. 实例化服务并循环处理
        cover_service = CoverGeneratorService(config=cover_config)
        
        TYPE_MAP = {
            'movies': 'Movie', 'tvshows': 'Series', 'music': 'MusicAlbum',
            'boxsets': 'BoxSet', 'mixed': 'Movie,Series'
        }

        for i, library in enumerate(libraries_to_process):
            if processor.is_stop_requested(): break
            
            progress = 10 + int((i / total) * 90)
            task_manager.update_status_from_thread(progress, f"({i+1}/{total}) 正在处理: {library.get('Name')}")
            
            try:
                library_id = library.get('Id')
                collection_type = library.get('CollectionType')
                item_type_to_query = TYPE_MAP.get(collection_type)

                item_count = 0
                if library_id and item_type_to_query:
                    item_count = emby_handler.get_item_count(
                        base_url=processor.emby_url,
                        api_key=processor.emby_api_key,
                        user_id=processor.emby_user_id,
                        parent_id=library_id,
                        item_type=item_type_to_query
                    ) or 0

                cover_service.generate_for_library(
                    emby_server_id='main_emby', # 这里的 server_id 只是一个占位符，不影响忽略逻辑
                    library=library,
                    item_count=item_count
                )
            except Exception as e_gen:
                logger.error(f"为媒体库 '{library.get('Name')}' 生成封面时发生错误: {e_gen}", exc_info=True)
                continue
        
        final_message = "所有媒体库封面已处理完毕！"
        if processor.is_stop_requested(): final_message = "任务已中止。"
        task_manager.update_status_from_thread(100, final_message)

    except Exception as e:
        logger.error(f"执行 '{task_name}' 任务时发生严重错误: {e}", exc_info=True)
        task_manager.update_status_from_thread(-1, f"任务失败: {e}")