# actor_sync_handler.py (最终版)

from typing import Optional
import threading
# 导入必要的模块
import emby_handler
import logging
from db_handler import get_db_connection as get_central_db_connection, get_all_emby_person_ids_from_map, delete_persons_by_emby_ids
from db_handler import ActorDBManager
logger = logging.getLogger(__name__)

class UnifiedSyncHandler:
    def __init__(self, emby_url: str, emby_api_key: str, emby_user_id: Optional[str], tmdb_api_key: str):
        self.actor_db_manager = ActorDBManager()
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        self.tmdb_api_key = tmdb_api_key
        
        logger.trace(f"UnifiedSyncHandler 初始化完成。")
        
    def sync_emby_person_map_to_db(self, update_status_callback: Optional[callable] = None, stop_event: Optional[threading.Event] = None):
        """
        【V8 - 精确统计最终版】
        - 反向同步阶段现在能精确统计实际更新、跳过和无需变动的数量。
        """
        logger.trace("开始双向演员映射表同步任务 (V8 - 精确统计版)...")
        
        # ... (阶段一和阶段二的代码完全不变) ...
        if update_status_callback: update_status_callback(0, "阶段 1/3: 从 Emby 读取所有演员...")
        all_persons_from_emby = []
        try:
            person_generator = emby_handler.get_all_persons_from_emby(self.emby_url, self.emby_api_key, self.emby_user_id, stop_event)
            for person_batch in person_generator:
                if stop_event and stop_event.is_set():
                    if update_status_callback: update_status_callback(-1, "任务已中止")
                    return
                all_persons_from_emby.extend(person_batch)
            total_from_emby = len(all_persons_from_emby)
            logger.info(f"  -> Emby 数据读取完成，共获取到 {total_from_emby} 个演员条目。")
        except Exception as e_read:
            if update_status_callback: update_status_callback(-1, "从Emby读取数据失败")
            return

        if total_from_emby == 0:
            try:
                pids_in_db = get_all_emby_person_ids_from_map()
                db_count = len(pids_in_db)
                if db_count > 100:
                    if update_status_callback: update_status_callback(-1, "安全中止：无法从Emby获取演员")
                    return
            except Exception as e_check:
                if update_status_callback: update_status_callback(-1, "安全检查失败")
                return

        stats = { "total": total_from_emby, "processed": 0, "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0, "deleted": 0 }
        if update_status_callback: update_status_callback(30, "阶段 2/3: 同步数据到本地数据库...")
        
        try:
            pids_in_db_before_sync = get_all_emby_person_ids_from_map()
            all_emby_pids_from_sync = {str(p.get("Id", "")).strip() for p in all_persons_from_emby if p.get("Id")}
            with get_central_db_connection() as conn:
                cursor = conn.cursor()
                emby_config_for_upsert = {"url": self.emby_url, "api_key": self.emby_api_key, "user_id": self.emby_user_id}
                for person_emby in all_persons_from_emby:
                    if stop_event and stop_event.is_set(): raise InterruptedError("任务在写入阶段被中止")
                    stats["processed"] += 1
                    emby_pid = str(person_emby.get("Id", "")).strip()
                    person_name = str(person_emby.get("Name", "")).strip()
                    if not emby_pid or not person_name:
                        stats["skipped"] += 1
                        continue
                    provider_ids = person_emby.get("ProviderIds", {})
                    person_data_for_db = { "emby_id": emby_pid, "name": person_name, "tmdb_id": provider_ids.get("Tmdb"), "imdb_id": provider_ids.get("Imdb"), "douban_id": provider_ids.get("Douban"), }
                    try:
                        _, status = self.actor_db_manager.upsert_person(cursor, person_data_for_db, emby_config=emby_config_for_upsert)
                        if status == "INSERTED": stats['inserted'] += 1
                        elif status == "UPDATED": stats['updated'] += 1
                        elif status == "UNCHANGED": stats['unchanged'] += 1
                        elif status == "SKIPPED": stats['skipped'] += 1
                        else: stats['errors'] += 1
                    except Exception as e_upsert:
                        stats['errors'] += 1
                conn.commit()
                pids_to_delete = list(pids_in_db_before_sync - all_emby_pids_from_sync)
                if pids_to_delete:
                    deleted_count = delete_persons_by_emby_ids(pids_to_delete)
                    stats['deleted'] = deleted_count
        except InterruptedError as e:
            if 'conn' in locals() and conn: conn.rollback()
            if update_status_callback: update_status_callback(-1, "任务已中止")
            return
        except Exception as e_write:
            if 'conn' in locals() and conn: conn.rollback()
            if update_status_callback: update_status_callback(-1, "数据库操作失败")
            return

        # ======================================================================
        # 阶段三：DB -> Emby (反向同步)
        # ======================================================================
        if update_status_callback: update_status_callback(80, "阶段 3/3: 将外部ID同步回 Emby...")
        
        try:
            with get_central_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT emby_person_id, tmdb_person_id, imdb_id, douban_celebrity_id FROM person_identity_map")
                all_persons_in_db = cursor.fetchall()

            logger.info(f"  -> 开始反向同步，将检查数据库中 {len(all_persons_in_db)} 条记录并更新到 Emby...")
            
            # ★★★ 核心修复：初始化反向同步的精确统计 ★★★
            reverse_stats = {"updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
            
            for i, person_db_row in enumerate(all_persons_in_db):
                if stop_event and stop_event.is_set():
                    logger.warning("任务在反向同步阶段被中止。")
                    break
                
                if i % 50 == 0 and update_status_callback:
                    progress = 80 + int((i / len(all_persons_in_db)) * 20)
                    update_status_callback(progress, f"反向同步中 ({i}/{len(all_persons_in_db)})...")

                # ★★★ 核心修复：接收并统计详细状态 ★★★
                status = emby_handler.update_person_provider_ids(
                    person_id=person_db_row['emby_person_id'],
                    provider_ids_from_db=person_db_row,
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id
                )
                if status == "UPDATED":
                    reverse_stats["updated"] += 1
                elif status == "UNCHANGED":
                    reverse_stats["unchanged"] += 1
                elif status == "SKIPPED":
                    reverse_stats["skipped"] += 1
                else: # ERROR
                    reverse_stats["errors"] += 1

            logger.info(f"  -> 反向同步完成，结果: 更新 {reverse_stats['updated']} 条, 无需变动 {reverse_stats['unchanged']} 条, 跳过 {reverse_stats['skipped']} 条。")

        except Exception as e_reverse:
            logger.error(f"反向同步阶段发生严重错误: {e_reverse}", exc_info=True)
            if update_status_callback: update_status_callback(-1, "反向同步失败")
            return

        # ★★★ 核心修复：更新最终的日志和UI消息 ★★★
        total_changed = stats['inserted'] + stats['updated']
        logger.info("--- 双向同步演员映射完成 ---")
        logger.info(f"📊 Emby->DB: 新增 {stats['inserted']}, 更新 {stats['updated']}, 清理 {stats['deleted']}")
        logger.info(f"🔄 DB->Emby: 成功更新 {reverse_stats['updated']} 条 (检查总数: {len(all_persons_in_db)})")
        logger.info("--------------------------")

        if update_status_callback:
            final_message = f"双向同步完成！Emby->DB (新增{stats['inserted']}, 更新{stats['updated']}) | DB->Emby (更新{reverse_stats['updated']})。"
            update_status_callback(100, final_message)

