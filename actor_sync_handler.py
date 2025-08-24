# actor_sync_handler.py (最终版)

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
    def __init__(self, emby_url: str, emby_api_key: str, emby_user_id: Optional[str], tmdb_api_key: str):
        self.actor_db_manager = ActorDBManager()
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        self.tmdb_api_key = tmdb_api_key # ★★★ 存储TMDb Key，用于记录冲突时获取头像 ★★★
        
        
        logger.trace(f"UnifiedSyncHandler 初始化完成。")
    def sync_emby_person_map_to_db(self,
                              update_status_callback: Optional[callable] = None,
                              stop_event: Optional[threading.Event] = None):

        logger.trace("开始统一的演员映射表同步任务 (流式处理)...")
        if update_status_callback:
            update_status_callback(0, "正在清理无 emby_person_id 的脏数据...")

        with get_central_db_connection() as conn:
            cursor = conn.cursor()
            # **这里清理无 emby_person_id 的记录**
            cursor.execute("DELETE FROM person_identity_map WHERE emby_person_id IS NULL OR emby_person_id = ''")
            conn.commit()
        if update_status_callback:
            update_status_callback(0, "正在计算演员总数...")

        total_from_emby = emby_handler.get_item_count(
            self.emby_url, self.emby_api_key, self.emby_user_id, "Person"
        )
        if total_from_emby is None:
            logger.error("无法获取Emby中的演员总数，中止同步。")
            if update_status_callback:
                update_status_callback(-1, "获取演员总数失败")
            return
        if total_from_emby == 0:
            logger.info("Emby 中没有找到任何演员条目。")
            if update_status_callback:
                update_status_callback(100, "Emby中无人物信息")
            return

        stats = {
            "total": total_from_emby,
            "processed": 0,
            "success": 0,
            "skipped": 0,
            "errors": 0,
            "invalid_data": 0,
        }

        logger.info(f"  -> Emby中共有约 {total_from_emby} 个演员条目，开始同步...")
        if update_status_callback:
            update_status_callback(0, f"开始同步 {total_from_emby} 位演员...")

        with get_central_db_connection() as conn:
            cursor = conn.cursor()

            for person_batch in emby_handler.get_all_persons_from_emby(
                    self.emby_url, self.emby_api_key, self.emby_user_id, stop_event
            ):

                for person_emby in person_batch:
                    if stop_event and stop_event.is_set():
                        logger.info("同步被外部请求中止。")
                        if update_status_callback:
                            update_status_callback(-2, "同步已取消")
                        return

                    emby_pid = str(person_emby.get("Id", "")).strip()
                    person_name = str(person_emby.get("Name", "")).strip()

                    if not emby_pid or not person_name:
                        stats["skipped"] += 1
                        stats["invalid_data"] += 1
                        logger.debug(f"跳过Emby演员 (ID: {emby_pid or 'N/A'})，因为其ID或名字为空。")
                        stats["processed"] += 1
                        continue

                    provider_ids_lower = {
                        k.lower(): v for k, v in (person_emby.get("ProviderIds") or {}).items()
                    }

                    person_data_for_db = {
                        "emby_id": emby_pid,
                        "name": person_name,
                        "tmdb_id": provider_ids_lower.get("tmdb"),
                        "imdb_id": provider_ids_lower.get("imdb"),
                        "douban_id": provider_ids_lower.get("douban"),
                    }

                    try:
                        map_id = self.actor_db_manager.upsert_person(
                            cursor,
                            person_data_for_db,
                            enrich_details=False  # 兼容旧参数，代码里可以无视
                        )

                        if map_id > 0:
                            stats['success'] += 1
                        else:
                            stats['errors'] += 1  # 只要不是正常id都算错误

                    except Exception as e_upsert:
                        logger.error(f"同步时写入数据库失败 for EmbyPID {emby_pid}: {e_upsert}")
                        stats['errors'] += 1

                    stats["processed"] += 1

                if update_status_callback and total_from_emby > 0:
                    progress = int((stats["processed"] / total_from_emby) * 100)
                    message = f"正在同步演员... ({stats['processed']}/{total_from_emby})"
                    update_status_callback(progress, message)

                conn.commit()

        logger.info("--- 同步演员映射完成 ---")
        logger.info(f"✅ 从 Emby API 共获取: {stats['total']} 条")
        logger.info(f"✅ 已处理: {stats['processed']} 条")
        logger.info(f"✅ 成功写入/更新: {stats['success']} 条")
        logger.info(f"✅ 跳过(无效数据): {stats['invalid_data']} 条")
        logger.info(f"✅ 错误: {stats['errors']} 条")
        logger.info("----------------------")
        if update_status_callback:
            update_status_callback(100, f"同步完成！共处理 {stats['total']} 条记录。")

