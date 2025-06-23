# actor_manager.py

import sqlite3
from typing import List, Dict, Any, Optional
import itertools
from logger_setup import logger
from actor_utils import ActorDBManager # 它会使用我们底层的数据库工具
import tmdb_handler # 需要用它来获取最新的头像信息

class ActorManager:
    def __init__(self, db_path: str, tmdb_api_key: str):
        self.db_manager = ActorDBManager(db_path)
        self.tmdb_api_key = tmdb_api_key
        logger.info("ActorManager 初始化完成。")

    def get_pending_conflicts(self) -> List[Dict[str, Any]]:
        """
        获取所有待处理的演员冲突事件，并为前端补充头像URL。
        """
        with self.db_manager._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM actor_conflicts WHERE status = 'pending' ORDER BY detected_at DESC")
            conflicts = [dict(row) for row in cursor.fetchall()]
            
            # 为前端补充完整的头像URL
            for conflict in conflicts:
                if conflict.get('new_actor_image_path'):
                    conflict['new_actor_image_url'] = f"https://image.tmdb.org/t/p/w185{conflict['new_actor_image_path']}"
                if conflict.get('existing_actor_image_path'):
                    conflict['existing_actor_image_url'] = f"https://image.tmdb.org/t/p/w185{conflict['existing_actor_image_path']}"
            
            return conflicts

    def resolve_conflict(self, conflict_id: int, resolution: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据用户的裁决，解决一个冲突。
        """
        action = resolution.get("action")
        if not action:
            return {"success": False, "message": "未提供裁决动作 (action)。"}

        logger.info(f"开始解决冲突 (ID: {conflict_id}), 裁决动作: {action}")

        with self.db_manager._get_db_connection() as conn:
            cursor = conn.cursor()
            
            # 先获取冲突案件的详细信息
            cursor.execute("SELECT * FROM actor_conflicts WHERE conflict_id = ?", (conflict_id,))
            conflict = cursor.fetchone()
            if not conflict:
                return {"success": False, "message": f"找不到冲突ID: {conflict_id}"}

            try:
                if action == "merge_new_to_existing":
                    # 合并：将“原告”设为“被告”的别名
                    master_id = conflict['existing_tmdb_id']
                    alias_id = conflict['new_tmdb_id']
                    cursor.execute(
                        "INSERT OR REPLACE INTO actor_aliases (alias_tmdb_id, master_tmdb_id, merge_reason) VALUES (?, ?, ?)",
                        (alias_id, master_id, f"manual_merge_conflict_{conflict_id}")
                    )
                    # (可选) 删除被合并的记录
                    cursor.execute("DELETE FROM person_identity_map WHERE tmdb_person_id = ?", (alias_id,))
                    logger.info(f"合并裁决：已将 {alias_id} 设为 {master_id} 的别名。")

                elif action == "unbind_existing":
                    # 解绑：将“被告”的冲突字段设为NULL
                    column = conflict['conflict_type'].replace('_OCCUPIED', '').lower()
                    cursor.execute(
                        f"UPDATE person_identity_map SET {column} = NULL WHERE tmdb_person_id = ?",
                        (conflict['existing_tmdb_id'],)
                    )
                    logger.info(f"解绑裁决：已将 {conflict['existing_tmdb_id']} 的 {column} 字段清空。")

                elif action == "ignore":
                    # 忽略：只更新状态，不做任何数据操作
                    logger.info(f"忽略裁决：将忽略冲突ID {conflict_id}。")
                
                else:
                    raise ValueError(f"未知的裁决动作: {action}")

                # 最后，更新冲突事件的状态
                cursor.execute(
                    "UPDATE actor_conflicts SET status = 'resolved', resolution_type = ?, resolved_at = CURRENT_TIMESTAMP WHERE conflict_id = ?",
                    (action, conflict_id)
                )
                conn.commit()
                return {"success": True, "message": f"冲突 {conflict_id} 已成功解决。"}

            except Exception as e:
                conn.rollback()
                logger.error(f"解决冲突 {conflict_id} 时发生错误: {e}", exc_info=True)
                return {"success": False, "message": f"服务器内部错误: {e}"}
    # ★★★ “立案侦探” ★★★
    def find_and_record_duplicates(self):
        """
        扫描 person_identity_map 表，找出所有同名演员，并为他们创建“潜在重复”的冲突案件。
        """
        logger.info("开始扫描演员表，寻找潜在的重复记录...")
        
        with self.db_manager._get_db_connection() as conn:
            cursor = conn.cursor()
            
            # 1. 找出所有出现次数大于1的名字
            cursor.execute("""
                SELECT primary_name
                FROM person_identity_map
                WHERE primary_name IS NOT NULL AND primary_name != ''
                GROUP BY primary_name
                HAVING COUNT(map_id) > 1
            """)
            duplicate_names = [row['primary_name'] for row in cursor.fetchall()]
            
            if not duplicate_names:
                logger.info("未发现任何同名演员，数据库非常干净！")
                return {"message": "未发现任何同名演员。"}

            logger.info(f"发现了 {len(duplicate_names)} 组同名演员，开始逐一立案...")
            
            new_conflicts_created = 0
            # 2. 为每一组同名演员立案
            for name in duplicate_names:
                cursor.execute("SELECT * FROM person_identity_map WHERE primary_name = ?", (name,))
                actors = [dict(row) for row in cursor.fetchall()]
                
                # 使用 itertools.combinations 生成所有不重复的演员对
                for actor_a, actor_b in itertools.combinations(actors, 2):
                    try:
                        self._record_potential_duplicate(cursor, actor_a, actor_b)
                        new_conflicts_created += 1
                    except sqlite3.IntegrityError:
                        # 这说明这对演员的冲突已经立案了，正常跳过
                        logger.debug(f"冲突已存在，跳过立案: {actor_a['primary_name']} ({actor_a['tmdb_person_id']}) vs ({actor_b['tmdb_person_id']})")
                    except Exception as e:
                        logger.error(f"为 '{name}' 立案时发生未知错误: {e}")
            
            conn.commit()
        
        message = f"扫描完成！为 {len(duplicate_names)} 组同名演员，新创建了 {new_conflicts_created} 起待裁决案件。"
        logger.info(message)
        return {"message": message}
    # ★★★ 专门的“书记员” ★★★
    def _record_potential_duplicate(self, cursor: sqlite3.Cursor, actor_a: Dict, actor_b: Dict):
        """专门记录“潜在重复”案件。"""
        
        # 获取头像信息
        image_path_a = self._get_person_image_path(actor_a.get('tmdb_person_id'))
        image_path_b = self._get_person_image_path(actor_b.get('tmdb_person_id'))

        conflict_record = {
            "conflict_type": "POTENTIAL_DUPLICATE",
            "new_tmdb_id": actor_a.get('tmdb_person_id'),
            "new_actor_name": actor_a.get('primary_name'),
            "new_actor_image_path": image_path_a,
            "conflicting_value": f"同名: {actor_a.get('primary_name')}",
            "existing_tmdb_id": actor_b.get('tmdb_person_id'),
            "existing_actor_name": actor_b.get('primary_name'),
            "existing_actor_image_path": image_path_b,
        }
        
        cols = list(conflict_record.keys())
        vals = list(conflict_record.values())
        sql = f"INSERT INTO actor_conflicts ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})"
        cursor.execute(sql, tuple(vals))
        logger.info(f"  -> 成功立案 [潜在重复]: '{conflict_record['new_actor_name']}'")
    def _get_person_image_path(self, tmdb_id: Optional[str]) -> Optional[str]:
        """辅助函数：根据TMDb ID获取头像路径。"""
        if not tmdb_id or not self.tmdb_api_key:
            return None
        details = tmdb_handler.get_person_details_tmdb(tmdb_id, self.tmdb_api_key)
        return details.get("profile_path") if details else None