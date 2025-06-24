# actor_manager.py

import sqlite3
from typing import List, Dict, Any, Optional
import itertools
from logger_setup import logger
from actor_utils import ActorDBManager # 它会使用我们底层的数据库工具
import tmdb_handler # 需要用它来获取最新的头像信息
import emby_handler

class ActorManager:
    def __init__(self, db_path: str, tmdb_api_key: str, emby_url: str, emby_api_key: str, emby_user_id: str):
        self.db_manager = ActorDBManager(db_path)
        self.tmdb_api_key = tmdb_api_key
        # ★★★ 把Emby配置存起来 ★★★
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        logger.info("ActorManager 初始化完成 (已配备Emby工具)。")

    def get_pending_conflicts(self, page: int = 1, page_size: int = 20, search_query: str = "") -> Dict[str, Any]:
        """【分页搜索版】获取待处理的演员冲突事件。"""
        with self.db_manager._get_db_connection() as conn:
            cursor = conn.cursor()
            
            where_clauses = ["status = 'pending'"]
            params = []
            
            if search_query:
                where_clauses.append("(new_actor_name LIKE ? OR existing_actor_name LIKE ?)")
                params.extend([f"%{search_query}%", f"%{search_query}%"])

            where_sql = " WHERE " + " AND ".join(where_clauses)
            
            # 1. 获取总数
            cursor.execute(f"SELECT COUNT(*) FROM actor_conflicts{where_sql}", tuple(params))
            total_items = cursor.fetchone()[0]
            
            # 2. 查询分页数据
            params.extend([page_size, (page - 1) * page_size])
            cursor.execute(
                f"SELECT * FROM actor_conflicts{where_sql} ORDER BY detected_at DESC LIMIT ? OFFSET ?",
                tuple(params)
            )
            conflicts = [dict(row) for row in cursor.fetchall()]
            
            # 为前端补充完整的头像URL
            for conflict in conflicts:
                if conflict.get('new_actor_image_path'):
                    conflict['new_actor_image_url'] = f"https://image.tmdb.org/t/p/w185{conflict['new_actor_image_path']}"
                if conflict.get('existing_actor_image_path'):
                    conflict['existing_actor_image_url'] = f"https://image.tmdb.org/t/p/w185{conflict['existing_actor_image_path']}"
            
            return {
                "items": conflicts,
                "total_items": total_items,
                "total_pages": (total_items + page_size - 1) // page_size,
                "current_page": page,
                "per_page": page_size,
            }

    def resolve_conflict(self, conflict_id: int, resolution: Dict[str, Any]) -> Dict[str, Any]:
        action = resolution.get("action")
        updated_names = resolution.get("updated_names", {})
        
        # 用户权限验证（使用类变量中存储的current_user）
        if not self.current_user:
            return {"success": False, "message": "未识别用户身份，请先登录"}
        
        logger.info(f"用户 {self.current_user} 开始解决冲突 (ID: {conflict_id})")

        with self.db_manager._get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 1. 验证用户权限（假设有user_permissions表）
                cursor.execute("""
                    SELECT permission_level FROM user_permissions 
                    WHERE user_id = ? AND resource_type = 'actor_conflict'
                    """, (self.current_user,))
                permission = cursor.fetchone()
                
                if not permission or permission['permission_level'] < 2:  # 假设2是编辑权限
                    return {"success": False, "message": "用户没有解决冲突的权限"}

                # 2. 获取冲突详情
                cursor.execute("""
                    SELECT * FROM actor_conflicts 
                    WHERE conflict_id = ? AND status = 'pending'
                    """, (conflict_id,))
                conflict = cursor.fetchone()
                
                if not conflict: 
                    return {"success": False, "message": f"找不到待解决的冲突ID: {conflict_id}"}

                # 3. 名字更新逻辑（加入用户验证）
                if updated_names:
                    # 更新新演员名字
                    if new_name := updated_names.get('new_actor_name'):
                        self._update_actor_name(
                            cursor,
                            tmdb_id=conflict['new_tmdb_id'],
                            new_name=new_name,
                            old_name=conflict['new_primary_name'],
                            user_id=self.current_user
                        )

                    # 更新现有演员名字
                    if existing_name := updated_names.get('existing_actor_name'):
                        self._update_actor_name(
                            cursor,
                            tmdb_id=conflict['existing_tmdb_id'],
                            new_name=existing_name,
                            old_name=conflict['existing_primary_name'],
                            user_id=self.current_user
                        )

                # 3. 根据 action 执行核心裁决逻辑
                if action == "merge_new_to_existing":
                    master_id = conflict['existing_tmdb_id']
                    alias_id = conflict['new_tmdb_id']
                    if master_id and alias_id:
                        cursor.execute(
                            "INSERT OR REPLACE INTO actor_aliases (alias_tmdb_id, master_tmdb_id, merge_reason) VALUES (?, ?, ?)",
                            (alias_id, master_id, f"manual_merge_conflict_{conflict_id}")
                        )
                        cursor.execute("DELETE FROM person_identity_map WHERE tmdb_person_id = ?", (alias_id,))
                        logger.info(f"合并裁决：已将 {alias_id} 设为 {master_id} 的别名。")

                elif action == "unbind_existing":
                    column = str(conflict['conflict_type']).replace('_OCCUPIED', '').lower()
                    # 增加一个安全检查，防止SQL注入（虽然这里是内部逻辑，但好习惯很重要）
                    if column in ['douban_celebrity_id', 'imdb_id', 'emby_person_id']:
                        cursor.execute(
                            f"UPDATE person_identity_map SET {column} = NULL WHERE tmdb_person_id = ?",
                            (conflict['existing_tmdb_id'],)
                        )
                        logger.info(f"解绑裁决：已将 {conflict['existing_tmdb_id']} 的 {column} 字段清空。")
                    else:
                        raise ValueError(f"不安全的解绑字段: {column}")

                elif action == "ignore":
                    logger.info(f"忽略裁决：将忽略冲突ID {conflict_id}。")
                
                else:
                    raise ValueError(f"未知的裁决动作: {action}")

                # 4. 最后，更新冲突事件的状态
                cursor.execute(
                    "UPDATE actor_conflicts SET status = 'resolved', resolution_type = ?, resolved_at = CURRENT_TIMESTAMP WHERE conflict_id = ?",
                    (action, conflict_id)
                )
                
                # conn.commit() # with 语句会自动处理 commit
                return {"success": True, "message": f"冲突 {conflict_id} 已成功解决。"}

            except Exception as e:
                # conn.rollback() # with 语句在发生异常时会自动处理 rollback
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
        logger.info(f"  -> 记录在案 [潜在重复]: '{conflict_record['new_actor_name']}'")
    def _get_person_image_path(self, tmdb_id: Optional[str]) -> Optional[str]:
        """辅助函数：根据TMDb ID获取头像路径。"""
        if not tmdb_id or not self.tmdb_api_key:
            return None
        details = tmdb_handler.get_person_details_tmdb(tmdb_id, self.tmdb_api_key)
        return details.get("profile_path") if details else None