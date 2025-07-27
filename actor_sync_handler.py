# actor_sync_handler.py (最终版)

import sqlite3
import time
import json
from typing import Optional, List, Dict, Any
import threading
# 导入必要的模块
import emby_handler
import logging
from db_handler import get_db_connection as get_central_db_connection
from db_handler import ActorDBManager
logger = logging.getLogger(__name__)

class UnifiedSyncHandler:
    def __init__(self, db_path: str, emby_url: str, emby_api_key: str, emby_user_id: Optional[str], tmdb_api_key: str):
        self.db_path = db_path
        self.actor_db_manager = ActorDBManager(self.db_path)
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        self.tmdb_api_key = tmdb_api_key # ★★★ 存储TMDb Key，用于记录冲突时获取头像 ★★★
        
        
        logger.trace(f"UnifiedSyncHandler 初始化完成。")
    def sync_emby_person_map_to_db(self, update_status_callback: Optional[callable] = None, stop_event: Optional[threading.Event] = None):
        """
        【流式处理版】分批次地获取、处理和汇报进度。
        """
        logger.info("开始统一的演员映射表同步任务 (流式处理)...")
        if update_status_callback: update_status_callback(0, "正在计算演员总数...")

        # 1. 先获取总数，用于计算进度百分比
        total_from_emby = emby_handler.get_item_count(self.emby_url, self.emby_api_key, self.emby_user_id, "Person")
        if total_from_emby is None:
            logger.error("无法获取Emby中的演员总数，中止同步。")
            if update_status_callback: update_status_callback(-1, "获取演员总数失败")
            return
        if total_from_emby == 0:
            logger.info("Emby 中没有找到任何演员条目。")
            if update_status_callback: update_status_callback(100, "Emby中无人物信息")
            return

        stats = {"total": total_from_emby, "processed": 0, "success": 0, "skipped": 0, "errors": 0}
        logger.info(f"Emby中共有约 {total_from_emby} 个演员条目，开始同步...")
        if update_status_callback: update_status_callback(0, f"开始同步 {total_from_emby} 位演员...")

        # ✨ 使用带有合并逻辑的 upsert_person，但关闭在线丰富功能
        with get_central_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            
            for person_batch in emby_handler.get_all_persons_from_emby(self.emby_url, self.emby_api_key, self.emby_user_id, stop_event):
                
                for person_emby in person_batch:
                    if stop_event and stop_event.is_set():
                        # ... (中止逻辑不变) ...
                        return

                    stats["processed"] += 1
                    
                    emby_pid = str(person_emby.get("Id", "")).strip()
                    person_name = str(person_emby.get("Name", "")).strip()

                    # ✨ 核心优化：在源头就跳过没有名字的演员 ✨
                    if not emby_pid or not person_name:
                        stats["skipped"] += 1
                        logger.debug(f"跳过Emby演员 (ID: {emby_pid or 'N/A'})，因为其ID或名字为空。")
                        continue
                    
                    provider_ids = person_emby.get("ProviderIds", {})
                    provider_ids_lower = {k.lower(): v for k, v in provider_ids.items()}
                    
                    person_data_for_db = {
                        "emby_id": emby_pid,
                        "name": person_name,
                        "tmdb_id": provider_ids_lower.get("tmdb"),
                        "imdb_id": provider_ids_lower.get("imdb"),
                        "douban_id": provider_ids_lower.get("douban"),
                    }
                    
                    try:
                        # ✨✨✨ 核心修改：关闭 enrich_details ✨✨✨
                        map_id = self.actor_db_manager.upsert_person(
                            cursor, 
                            person_data_for_db,
                            # 我们不需要传递 tmdb_api_key，因为 enrich_details 是 False
                            enrich_details=False 
                        )
                        # upsert_person 返回-1表示传入数据无效，但我们已经在前面检查过了
                        if map_id > 0: 
                            stats['success'] += 1
                        elif map_id == -1:
                            # 当 upsert_person 返回 -1 时，意味着发生了冲突或可预见的错误
                            # 我们将其计入 'errors' 或 'skipped' 计数器
                            stats['errors'] += 1
                    except Exception as e_upsert:
                        # 新的 upsert_person 内部会处理 IntegrityError，所以这里只捕获通用异常
                        logger.error(f"同步时写入数据库失败 for EmbyPID {emby_pid}: {e_upsert}")
                        stats['errors'] += 1

                # 3. 在处理完每一批后，立刻汇报进度！
                if update_status_callback and total_from_emby > 0:
                    progress = int((stats["processed"] / total_from_emby) * 100)
                    message = f"正在同步演员... ({stats['processed']}/{total_from_emby})"
                    update_status_callback(progress, message)
                
                conn.commit() # 每处理完一批就提交一次事务

        # ... (最终的统计日志) ...
        logger.info("--- 演员映射表同步完成 ---")
        logger.info(f"从 Emby API 共获取: {stats['total']} 条")
        logger.info(f"已处理: {stats['processed']} 条")
        logger.info(f"成功写入/更新: {stats['success']} 条")
        logger.info(f"跳过/错误/冲突: {stats['skipped'] + stats['errors']} 条")
        logger.info("-------------------------")

        if update_status_callback:
            update_status_callback(100, f"同步完成！共处理 {stats['total']} 条记录。")

