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
    def sync_emby_person_map_to_db(self, update_status_callback: Optional[callable] = None, stop_event: Optional[threading.Event] = None):
        """
        【流式处理版】分批次地获取、处理和汇报进度，并提供精确的统计。
        """
        logger.trace("开始统一的演员映射表同步任务 (流式处理)...")
        if update_status_callback: update_status_callback(0, "正在计算演员总数...")

        total_from_emby = emby_handler.get_item_count(self.emby_url, self.emby_api_key, self.emby_user_id, "Person")
        if total_from_emby is None:
            logger.error("无法获取Emby中的演员总数，中止同步。")
            if update_status_callback: update_status_callback(-1, "获取演员总数失败")
            return
        if total_from_emby == 0:
            logger.info("Emby 中没有找到任何演员条目。")
            if update_status_callback: update_status_callback(100, "Emby中无人物信息")
            return

        # 初始化更详细的统计计数器
        stats = {
            "total": total_from_emby, 
            "processed": 0, 
            "inserted": 0, 
            "updated": 0, 
            "unchanged": 0,
            "skipped": 0, 
            "errors": 0
        }
        logger.info(f"  -> Emby中共有约 {total_from_emby} 个演员条目，开始同步...")
        if update_status_callback: update_status_callback(0, f"开始同步 {total_from_emby} 位演员...")

        emby_config_for_upsert = {
            "url": self.emby_url,
            "api_key": self.emby_api_key,
            "user_id": self.emby_user_id
        }
        
        with get_central_db_connection() as conn:
            cursor = conn.cursor()
            
            for person_batch in emby_handler.get_all_persons_from_emby(self.emby_url, self.emby_api_key, self.emby_user_id, stop_event):
                
                for person_emby in person_batch:
                    if stop_event and stop_event.is_set():
                        logger.warning("任务被用户中止。")
                        conn.commit()
                        if update_status_callback: update_status_callback(-1, "任务已中止")
                        return

                    stats["processed"] += 1
                    
                    emby_pid = str(person_emby.get("Id", "")).strip()
                    person_name = str(person_emby.get("Name", "")).strip()

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
                        # 使用返回 (map_id, status) 的新版 upsert_person
                        map_id, status = self.actor_db_manager.upsert_person(
                            cursor, 
                            person_data_for_db,
                            emby_config=emby_config_for_upsert 
                        )
                        
                        # 根据返回的状态进行分类计数
                        if status == "INSERTED":
                            stats['inserted'] += 1
                        elif status == "UPDATED":
                            stats['updated'] += 1
                        elif status == "UNCHANGED":
                            stats['unchanged'] += 1
                        elif status == "SKIPPED":
                            stats['skipped'] += 1
                        else: # "ERROR"
                            stats['errors'] += 1

                    except Exception as e_upsert:
                        logger.error(f"同步时写入数据库失败 for EmbyPID {emby_pid}: {e_upsert}")
                        stats['errors'] += 1

                if update_status_callback and total_from_emby > 0:
                    progress = int((stats["processed"] / total_from_emby) * 100)
                    message = f"正在同步演员... ({stats['processed']}/{total_from_emby})"
                    update_status_callback(progress, message)
                
                conn.commit()

        # --- 修改最终的统计日志输出 ---
        total_changed = stats['inserted'] + stats['updated']
        total_failed = stats['skipped'] + stats['errors']

        logger.info("--- 同步演员映射完成 ---")
        logger.info(f"📊 Emby 总数: {stats['total']} 条")
        logger.info(f"⚙️ 已处理: {stats['processed']} 条")
        logger.info(f"✅ 成功写入/更新: {total_changed} 条 (新增: {stats['inserted']}, 更新: {stats['updated']})")
        logger.info(f"➖ 无需变动: {stats['unchanged']} 条")
        if total_failed > 0:
            logger.warning(f"⚠️ 跳过或错误: {total_failed} 条 (跳过: {stats['skipped']}, 错误: {stats['errors']})")
        logger.info("----------------------")

        if update_status_callback:
            final_message = f"同步完成！处理 {stats['processed']} 条，新增 {stats['inserted']}，更新 {stats['updated']}。"
            update_status_callback(100, final_message)

