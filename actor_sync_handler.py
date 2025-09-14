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
        【V9 - 单次处理高效版】
        - 将正向同步 (Emby->DB) 与反向同步 (DB->Emby) 合并到一个循环中。
        - 在 upsert 一个演员到数据库后，立即回头检查并按需更新 Emby，实现精准、高效的双向同步。
        - 彻底移除了低效的、遍历全库的“阶段三”反向同步。
        """
        logger.info("--- 开始执行演员数据同步任务 ---")
        
        # 阶段一：从 Emby 读取数据 (逻辑不变)
        if update_status_callback: update_status_callback(0, "阶段 1/2: 从 Emby 读取所有演员...")
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

        # 安全检查 (逻辑不变)
        if total_from_emby == 0:
            try:
                pids_in_db = get_all_emby_person_ids_from_map()
                if len(pids_in_db) > 100:
                    if update_status_callback: update_status_callback(-1, "安全中止：无法从Emby获取演员")
                    return
            except Exception:
                if update_status_callback: update_status_callback(-1, "安全检查失败")
                return

        # ▼▼▼ 阶段二：单次循环处理，完成所有同步与清理 ▼▼▼
        stats = { "total": total_from_emby, "processed": 0, "db_inserted": 0, "db_updated": 0, 
                  "reverse_updated": 0, "unchanged": 0, "skipped": 0, "errors": 0, "deleted": 0 }
        
        if update_status_callback: update_status_callback(30, "阶段 2/2: 正在双向同步数据...")
        
        try:
            pids_in_db_before_sync = get_all_emby_person_ids_from_map()
            all_emby_pids_from_sync = {str(p.get("Id", "")).strip() for p in all_persons_from_emby if p.get("Id")}

            with get_central_db_connection() as conn:
                cursor = conn.cursor()
                emby_config_for_upsert = {"url": self.emby_url, "api_key": self.emby_api_key, "user_id": self.emby_user_id}

                for i, person_emby in enumerate(all_persons_from_emby):
                    if stop_event and stop_event.is_set(): raise InterruptedError("任务在写入阶段被中止")
                    
                    stats["processed"] += 1
                    if i % 50 == 0 and update_status_callback:
                        progress = 30 + int((i / total_from_emby) * 70)
                        update_status_callback(progress, f"双向同步中 ({i}/{total_from_emby})...")

                    emby_pid = str(person_emby.get("Id", "")).strip()
                    person_name = str(person_emby.get("Name", "")).strip()
                    if not emby_pid or not person_name:
                        stats["skipped"] += 1
                        continue
                    
                    # 1. 正向同步 (Emby -> DB)
                    provider_ids = person_emby.get("ProviderIds", {})
                    person_data_for_db = { "emby_id": emby_pid, "name": person_name, "tmdb_id": provider_ids.get("Tmdb"), "imdb_id": provider_ids.get("Imdb"), "douban_id": provider_ids.get("Douban"), }
                    try:
                        _, status = self.actor_db_manager.upsert_person(cursor, person_data_for_db, emby_config=emby_config_for_upsert)
                        if status == "INSERTED": stats['db_inserted'] += 1
                        elif status == "UPDATED": stats['db_updated'] += 1
                        elif status == "UNCHANGED": stats['unchanged'] += 1
                        elif status == "SKIPPED": stats['skipped'] += 1
                    except Exception as e_upsert:
                        stats['errors'] += 1
                        logger.error(f"处理演员 {person_name} (ID: {emby_pid}) 的 upsert 时失败: {e_upsert}")
                        continue # 如果upsert失败，跳过后续的反向同步

                    # 2. ★★★ 精准反向同步 (DB -> Emby) ★★★
                    # 在 upsert 之后，数据库中的记录就是最完整的“真理”
                    final_db_record = self.actor_db_manager.find_person_by_any_id(cursor, emby_id=emby_pid)
                    if not final_db_record: continue

                    # 从“真理”构建目标 ProviderIds
                    target_provider_ids = {
                        "Tmdb": str(v) for k, v in final_db_record.items() if k == 'tmdb_person_id' and v}
                    if final_db_record.get('imdb_id'): target_provider_ids['Imdb'] = final_db_record['imdb_id']
                    if final_db_record.get('douban_celebrity_id'): target_provider_ids['Douban'] = final_db_record['douban_celebrity_id']
                    
                    # 获取 Emby 当前的 ProviderIds
                    current_emby_ids = {k: str(v) for k, v in provider_ids.items() if v}

                    # 如果不一致，则用数据库的“真理”覆盖 Emby
                    if target_provider_ids != current_emby_ids:
                        logger.info(f"  -> [反向同步] 检测到演员 '{person_name}' (ID: {emby_pid}) 的外部ID需要更新。")
                        logger.debug(f"     Emby 当前: {current_emby_ids}")
                        logger.debug(f"     DB 更新为: {target_provider_ids}")
                        success = emby_handler.update_person_details(
                            person_id=emby_pid,
                            new_data={"ProviderIds": target_provider_ids},
                            emby_server_url=self.emby_url,
                            emby_api_key=self.emby_api_key,
                            user_id=self.emby_user_id
                        )
                        if success:
                            stats['reverse_updated'] += 1
                
                conn.commit()

                # 3. 清理操作 (逻辑不变)
                pids_to_delete = list(pids_in_db_before_sync - all_emby_pids_from_sync)
                if pids_to_delete:
                    deleted_count = delete_persons_by_emby_ids(pids_to_delete)
                    stats['deleted'] = deleted_count

        except InterruptedError:
            if 'conn' in locals() and conn: conn.rollback()
            if update_status_callback: update_status_callback(-1, "任务已中止")
            return
        except Exception as e_write:
            if 'conn' in locals() and conn: conn.rollback()
            if update_status_callback: update_status_callback(-1, "数据库操作失败")
            return

        # 最终统计
        logger.info("--- 同步演员数据完成 ---")
        logger.info(f"📊 Emby->DB: 新增 {stats['db_inserted']}, 更新 {stats['db_updated']}, 清理 {stats['deleted']}")
        logger.info(f"🔄 DB->Emby: 成功更新 {stats['reverse_updated']} 条 (在 {total_from_emby} 次检查中)")
        logger.info("--------------------------")

        if update_status_callback:
            final_message = f"同步完成！Emby->DB (新增{stats['db_inserted']}, 更新{stats['db_updated']}) | DB->Emby (更新{stats['reverse_updated']})。"
            update_status_callback(100, final_message)

