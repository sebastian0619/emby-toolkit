# actor_sync_handler.py (最终版)

import sqlite3
import time
from typing import Optional, List, Dict, Any
import threading
# 导入必要的模块
import emby_handler
from logger_setup import logger
from actor_utils import ActorDBManager # ★★★ 导入我们专业的数据库管理员 ★★★

class UnifiedSyncHandler:
    def __init__(self, db_path: str, emby_url: str, emby_api_key: str, emby_user_id: Optional[str], tmdb_api_key: str):
        self.db_path = db_path
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        self.tmdb_api_key = tmdb_api_key # ★★★ 存储TMDb Key，用于记录冲突时获取头像 ★★★
        
        # ★★★ 核心：创建并持有一个 ActorDBManager 实例 ★★★
        self.actor_db_manager = ActorDBManager(self.db_path)
        
        logger.info(f"UnifiedSyncHandler 初始化完成。")

    # ★★★ _get_db_conn 和 _upsert_from_emby_sync 已被彻底删除 ★★★

    def sync_emby_person_map_to_db(self, update_status_callback: Optional[callable] = None, stop_event: Optional[threading.Event] = None):
        """
        【流式处理版】分批次地获取、处理和汇报进度。
        """
        logger.info("开始演员映射表同步任务 (流式处理)...")
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

        stats = {
            "total": total_from_emby,
            "actual_fetched": 0,
            "processed": 0,
            "success": 0,
            "skipped": 0,
            "errors": 0
        }

        logger.info(f"Emby中共有约 {total_from_emby} 个演员条目，开始同步...")
        if update_status_callback: update_status_callback(0, f"开始同步 {total_from_emby} 位演员...")

        with self.actor_db_manager._get_db_connection() as conn:
            cursor = conn.cursor()

            for person_batch in emby_handler.get_all_persons_from_emby(self.emby_url, self.emby_api_key, self.emby_user_id, stop_event):
                stats["actual_fetched"] += len(person_batch)

                for person_emby in person_batch:
                    if stop_event and stop_event.is_set():
                        logger.warning("同步任务被用户中止。")
                        conn.commit()
                        if update_status_callback:
                            progress = int((stats['processed'] / total_from_emby) * 100)
                            update_status_callback(progress, f"任务中止，已处理 {stats['processed']} / {total_from_emby}")
                        return

                    stats["processed"] += 1

                    emby_pid = str(person_emby.get("Id", "")).strip()
                    if not emby_pid:
                        stats["skipped"] += 1
                        continue

                    provider_ids = person_emby.get("ProviderIds", {})
                    provider_ids_lower = {k.lower(): v for k, v in provider_ids.items()}

                    person_data_for_db = {
                        "emby_id": emby_pid,
                        "name": str(person_emby.get("Name", "")).strip(),
                        "tmdb_id": provider_ids_lower.get("tmdb"),
                        "imdb_id": provider_ids_lower.get("imdb"),
                        "douban_id": provider_ids_lower.get("douban"),
                    }

                    try:
                        map_id = self.actor_db_manager.upsert_person(cursor, person_data_for_db, self.tmdb_api_key)
                        if map_id > 0:
                            stats['success'] += 1
                        else:
                            stats['skipped'] += 1  # 可能因为无变化等被跳过
                    except sqlite3.IntegrityError as e:
                        self.actor_db_manager.record_conflict(cursor, person_data_for_db, str(e), self.tmdb_api_key)
                        stats['errors'] += 1
                    except Exception as e_upsert:
                        logger.error(f"同步时写入数据库失败 for EmbyPID {emby_pid}: {e_upsert}")
                        stats['errors'] += 1

                conn.commit()

                if update_status_callback and total_from_emby > 0:
                    progress = int((stats["processed"] / total_from_emby) * 100)
                    update_status_callback(progress, f"正在同步演员... ({stats['processed']}/{total_from_emby})")

        # 同步完成日志输出
        logger.info("--- 演员映射表同步完成 ---")
        logger.info(f"从 Emby API 预计获取: {stats['total']} 条")
        logger.info(f"实际获取并处理: {stats['actual_fetched']} 条")
        logger.info(f"成功写入/更新: {stats['success']} 条")
        logger.info(f"跳过: {stats['skipped']} 条，错误/冲突: {stats['errors']} 条")
        if stats['actual_fetched'] < stats['total']:
            logger.warning("实际处理数量少于 Emby 报告的总数，可能同步被中止或部分失败。")
        logger.info("-------------------------")

        if update_status_callback:
            update_status_callback(100, f"同步完成，成功: {stats['success']}，跳过: {stats['skipped']}，错误: {stats['errors']}")

