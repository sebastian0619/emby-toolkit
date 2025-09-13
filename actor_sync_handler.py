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
        【V7 - 逻辑修正最终版】
        1. Emby -> DB: 确保将 Emby 完整的 ProviderIds 写入数据库。
        2. 清理阶段: 精确计算并删除差集。
        3. DB -> Emby: 只有当数据库的 ID 比 Emby 更丰富时才执行更新。
        """
        logger.trace("开始双向演员映射表同步任务 (V7 - 逻辑修正版)...")
        
        # ======================================================================
        # 阶段一：Emby -> DB
        # ======================================================================
        if update_status_callback: update_status_callback(0, "阶段 1/3: 从 Emby 读取所有演员...")
        
        # ... (这部分读取逻辑完全不变) ...
        all_persons_from_emby = []
        try:
            person_generator = emby_handler.get_all_persons_from_emby(self.emby_url, self.emby_api_key, self.emby_user_id, stop_event)
            for person_batch in person_generator:
                if stop_event and stop_event.is_set():
                    logger.warning("任务在读取阶段被用户中止。")
                    if update_status_callback: update_status_callback(-1, "任务已中止")
                    return
                all_persons_from_emby.extend(person_batch)
            total_from_emby = len(all_persons_from_emby)
            logger.info(f"  -> Emby 数据读取完成，共获取到 {total_from_emby} 个演员条目。")
        except Exception as e_read:
            logger.error(f"从Emby读取演员数据时发生严重错误: {e_read}", exc_info=True)
            if update_status_callback: update_status_callback(-1, "从Emby读取数据失败")
            return

        # ... (安全检查逻辑不变) ...
        if total_from_emby == 0:
            logger.warning("从 Emby 获取到 0 个演员条目，正在执行安全检查...")
            try:
                pids_in_db = get_all_emby_person_ids_from_map()
                db_count = len(pids_in_db)
                SAFETY_THRESHOLD = 100 
                if db_count > SAFETY_THRESHOLD:
                    error_message = f"安全中止：从 Emby 获取到 0 个演员，但数据库中存在 {db_count} 条记录。"
                    logger.error(error_message)
                    if update_status_callback: update_status_callback(-1, "安全中止：无法从Emby获取演员")
                    return
                else:
                    logger.info(f"数据库中记录数 ({db_count}) 低于安全阈值，将继续执行。")
            except Exception as e_check:
                logger.error(f"执行安全检查时发生数据库错误: {e_check}", exc_info=True)
                if update_status_callback: update_status_callback(-1, "安全检查失败")
                return

        stats = { "total": total_from_emby, "processed": 0, "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0, "deleted": 0 }
        if update_status_callback: update_status_callback(30, "阶段 2/3: 同步数据到本地数据库...")
        
        try:
            # ★★★ 核心修复 1/3: 在写入前，先获取数据库中已有的所有ID ★★★
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
                    
                    # ★★★ 核心修复 2/3: 确保将 Emby 所有的 ProviderIds 都传递给数据库 ★★★
                    provider_ids = person_emby.get("ProviderIds", {})
                    person_data_for_db = { 
                        "emby_id": emby_pid, 
                        "name": person_name, 
                        "tmdb_id": provider_ids.get("Tmdb"), 
                        "imdb_id": provider_ids.get("Imdb"), 
                        "douban_id": provider_ids.get("Douban"), 
                    }
                    
                    try:
                        _, status = self.actor_db_manager.upsert_person(cursor, person_data_for_db, emby_config=emby_config_for_upsert)
                        if status == "INSERTED": stats['inserted'] += 1
                        elif status == "UPDATED": stats['updated'] += 1
                        elif status == "UNCHANGED": stats['unchanged'] += 1
                        elif status == "SKIPPED": stats['skipped'] += 1
                        else: stats['errors'] += 1
                    except Exception as e_upsert:
                        logger.error(f"同步时写入数据库失败 for EmbyPID {emby_pid}: {e_upsert}")
                        stats['errors'] += 1
                conn.commit()

                # ★★★ 核心修复 3/3: 使用正确的集合运算来计算需要删除的ID ★★★
                pids_to_delete = list(pids_in_db_before_sync - all_emby_pids_from_sync)
                
                if pids_to_delete:
                    logger.warning(f"  -> 发现 {len(pids_to_delete)} 条失效记录需要删除。")
                    deleted_count = delete_persons_by_emby_ids(pids_to_delete)
                    stats['deleted'] = deleted_count
                else:
                    logger.info("  -> 数据库与Emby数据一致，无需清理。")

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
            
            reverse_synced_count = 0
            for i, person_db_row in enumerate(all_persons_in_db):
                if stop_event and stop_event.is_set():
                    logger.warning("任务在反向同步阶段被中止。")
                    break
                
                if i % 50 == 0 and update_status_callback:
                    progress = 80 + int((i / len(all_persons_in_db)) * 20)
                    update_status_callback(progress, f"反向同步中 ({i}/{len(all_persons_in_db)})...")

                success = emby_handler.update_person_provider_ids(
                    person_id=person_db_row['emby_person_id'],
                    provider_ids_from_db=person_db_row,
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id
                )
                if success:
                    reverse_synced_count += 1

            logger.info(f"  -> 反向同步完成，共检查 {len(all_persons_in_db)} 条，成功更新/确认 {reverse_synced_count} 条。")

        except Exception as e_reverse:
            logger.error(f"反向同步阶段发生严重错误: {e_reverse}", exc_info=True)
            if update_status_callback: update_status_callback(-1, "反向同步失败")
            return

        # ... (最终的统计日志输出，保持不变) ...
        total_changed = stats['inserted'] + stats['updated']
        total_failed = stats['skipped'] + stats['errors']
        logger.info("--- 双向同步演员映射完成 ---")
        logger.info(f"📊 Emby->DB: 新增 {stats['inserted']}, 更新 {stats['updated']}, 清理 {stats['deleted']}")
        logger.info(f"🔄 DB->Emby: 成功更新/确认 {reverse_synced_count} 条")
        logger.info("--------------------------")

        if update_status_callback:
            final_message = f"双向同步完成！Emby->DB (新增{stats['inserted']}, 更新{stats['updated']}) | DB->Emby (更新{reverse_synced_count})。"
            update_status_callback(100, final_message)

