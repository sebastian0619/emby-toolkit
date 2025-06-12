# core_processor.py (最终完整版 - 已修复)

import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
import threading
import time

# 确保所有依赖都已正确导入
import emby_handler
import tmdb_handler
import utils
from logger_setup import logger
import constants
from ai_translator import AITranslator

try:
    from douban import DoubanApi
    DOUBAN_API_AVAILABLE = True
except ImportError:
    DOUBAN_API_AVAILABLE = False
    class DoubanApi:
        def __init__(self, *args, **kwargs): pass
        def get_acting(self, *args, **kwargs): return {}
        def close(self): pass
        @staticmethod
        def _get_translation_from_db(*args, **kwargs): return None
        @staticmethod
        def _save_translation_to_db(*args, **kwargs): pass

def _read_local_json(file_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(file_path):
        logger.warning(f"本地元数据文件不存在: {file_path}")
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取本地JSON文件失败: {file_path}, 错误: {e}")
        return None

class MediaProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            raise ValueError("数据库路径 (db_path) 未在配置中提供。")

        self.douban_api = None
        if DOUBAN_API_AVAILABLE:
            self.douban_api = DoubanApi(db_path=self.db_path)

        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.tmdb_api_key = self.config.get("tmdb_api_key", "")
        self.local_data_path = self.config.get("local_data_path", "").strip()
        self.sync_images_enabled = self.config.get(constants.CONFIG_OPTION_SYNC_IMAGES, False)
        self.translator_engines = self.config.get(constants.CONFIG_OPTION_TRANSLATOR_ENGINES, constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        
        self.ai_translator = None
        if self.config.get("ai_translation_enabled", False):
            try:
                self.ai_translator = AITranslator(self.config)
            except Exception as e:
                logger.error(f"AI翻译器初始化失败: {e}")

        self._stop_event = threading.Event()
        self.processed_items_cache = self._load_processed_log_from_db()
        logger.info("MediaProcessor 初始化完成。")

    #---评分---
    def _evaluate_cast_processing_quality(self, final_cast: List[Dict[str, Any]], original_cast_count: int) -> float:
        """
        评估处理后的演员列表质量，并返回一个分数 (0.0 - 10.0)。
        这个版本是从旧代码迁移过来的，后续可以根据新的数据结构优化。
        """
        if not final_cast: return 0.0
        total_actors = len(final_cast)
        accumulated_score = 0.0
        
        logger.debug(f"  质量评估开始：原始演员数={original_cast_count}, 处理后演员数={total_actors}")

        for actor_data in final_cast:
            score = 0.0
            # 演员名评分 (满分 3)
            if actor_data.get("name") and utils.contains_chinese(actor_data.get("name")):
                score += 3.0
            elif actor_data.get("name"):
                score += 1.0
            
            # 角色名评分 (满分 3)
            role = actor_data.get("character", "")
            if role and utils.contains_chinese(role):
                if role not in ["演员", "配音"] and not role.endswith("(配音)"):
                    score += 3.0 # 有意义的中文角色名
                else:
                    score += 1.5 # 通用角色名
            elif role:
                score += 0.5 # 英文角色名
            
            # ID 评分 (满分 4)
            if actor_data.get("id"): score += 2.0 # TMDB ID
            if actor_data.get("imdb_id"): score += 1.5 # IMDb ID
            if actor_data.get("douban_id"): score += 0.5 # Douban ID
            
            # 确保单个演员分数在 0 到 10 之间
            final_actor_score = min(10.0, score)
            accumulated_score += final_actor_score
            logger.debug(f"    演员 '{actor_data.get('name', '未知')}' 单项评分: {final_actor_score:.1f}")

        avg_score = accumulated_score / total_actors if total_actors > 0 else 0.0
        
        # 如果演员数量大幅减少，进行惩罚
        if total_actors < original_cast_count * 0.8:
            logger.warning(f"  质量评估：演员数量从 {original_cast_count} 大幅减少到 {total_actors}，评分将乘以惩罚系数。")
            avg_score *= (total_actors / original_cast_count)
            
        final_score_rounded = round(avg_score, 1)
        logger.info(f"  媒体项演员处理质量评估完成，最终评分: {final_score_rounded:.1f}")
        return final_score_rounded
    # ✨ --- END: 评分功能 --- ✨

    def _get_db_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def signal_stop(self):
        self._stop_event.set()

    def clear_stop_signal(self):
        self._stop_event.clear()

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _load_processed_log_from_db(self) -> Dict[str, str]:
        log_dict = {}
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT item_id, item_name FROM processed_log")
            rows = cursor.fetchall()
            for row in rows:
                if row['item_id'] and row['item_name']:
                    log_dict[row['item_id']] = row['item_name']
            conn.close()
        except Exception as e:
            logger.error(f"从数据库读取已处理记录失败: {e}")
        return log_dict

    def save_to_processed_log(self, item_id: str, item_name: Optional[str] = None, score: Optional[float] = None):
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO processed_log (item_id, item_name, processed_at, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                (item_id, item_name or f"未知项目(ID:{item_id})", score)
            )
            conn.commit()
            conn.close()
            self.processed_items_cache[item_id] = item_name or f"未知项目(ID:{item_id})"
        except Exception as e:
            logger.error(f"保存已处理记录到数据库失败 (Item ID: {item_id}): {e}")

    def save_to_failed_log(self, item_id: str, item_name: Optional[str], error_msg: str, item_type: Optional[str] = None, score: Optional[float] = None):
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO failed_log (item_id, item_name, failed_at, error_message, item_type, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)",
                (item_id, item_name or f"未知项目(ID:{item_id})", error_msg, item_type or "未知类型", score)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存失败记录到数据库失败 (Item ID: {item_id}): {e}")

    def _remove_from_failed_log_if_exists(self, item_id: str):
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM failed_log WHERE item_id = ?", (item_id,))
            if cursor.rowcount > 0:
                logger.info(f"Item ID '{item_id}' 已从 failed_log 中移除。")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"从 failed_log 删除 Item ID '{item_id}' 时失败: {e}")

    def clear_processed_log(self):
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM processed_log")
            conn.commit()
            conn.close()
            self.processed_items_cache.clear()
            logger.info("数据库和内存中的已处理记录已清除。")
        except Exception as e:
            logger.error(f"清除数据库已处理记录失败: {e}")

    def _translate_actor_field(self, text: Optional[str], field_name: str, actor_name_for_log: str, db_cursor: sqlite3.Cursor) -> Optional[str]:
        """
        【修复缓存逻辑版】翻译演员的特定字段，智能选择AI或传统翻译引擎，并正确处理缓存。
        """
        # 1. 前置检查：如果文本为空、是纯空格，或已包含中文，则直接返回原文
        if not text or not text.strip() or utils.contains_chinese(text):
            return text
        
        text_stripped = text.strip()

        # 2. 前置检查：跳过短的大写字母缩写
        if len(text_stripped) <= 2 and text_stripped.isupper():
            return text

        # 3. 核心修复：优先从数据库读取缓存，并处理所有情况
        cached_entry = DoubanApi._get_translation_from_db(text_stripped, cursor=db_cursor)
        if cached_entry:
            # 情况 A: 缓存中有成功的翻译结果
            if cached_entry.get("translated_text"):
                cached_translation = cached_entry.get("translated_text")
                logger.debug(f"数据库翻译缓存命中 for '{text_stripped}' -> '{cached_translation}'")
                return cached_translation
            # 情况 B: 缓存中明确记录了这是一个失败的翻译
            else:
                logger.debug(f"数据库翻译缓存命中 (失败记录) for '{text_stripped}'，不再尝试在线翻译。")
                return text # 直接返回原文，避免重复请求

        # 4. 如果缓存中完全没有记录，才进行在线翻译
        logger.info(f"'{text_stripped}' 在翻译缓存中未找到，将进行在线翻译...")
        final_translation = None
        final_engine = "unknown"

        # 根据配置选择翻译方式
        if self.ai_translator and self.config.get("ai_translation_enabled", False):
            # --- 使用AI翻译 ---
            final_translation = self.ai_translator.translate(text_stripped)
            final_engine = self.ai_translator.provider
        else:
            # --- 使用传统翻译引擎 ---
            translation_result = utils.translate_text_with_translators(
                text_stripped,
                engine_order=self.translator_engines
            )
            if translation_result and translation_result.get("text"):
                final_translation = translation_result["text"]
                final_engine = translation_result["engine"]

        # 5. 处理在线翻译的结果，并更新缓存
        if final_translation and final_translation.strip() and final_translation.strip().lower() != text_stripped.lower():
            # 翻译成功，存入缓存并返回结果
            logger.info(f"在线翻译成功: '{text_stripped}' -> '{final_translation}' (使用引擎: {final_engine})")
            DoubanApi._save_translation_to_db(text_stripped, final_translation, final_engine, cursor=db_cursor)
            return final_translation
        else:
            # 翻译失败或返回原文，将失败状态存入缓存，并返回原文
            logger.warning(f"在线翻译未能翻译 '{text_stripped}' 或返回了原文 (使用引擎: {final_engine})。")
            DoubanApi._save_translation_to_db(text_stripped, None, f"failed_or_same_via_{final_engine}", cursor=db_cursor)
            return text

    def _select_best_role(self, current_role: str, candidate_role: str) -> str:
        current_role, candidate_role = str(current_role or '').strip(), str(candidate_role or '').strip()
        if candidate_role and candidate_role != "演员": return candidate_role
        return current_role or candidate_role

    def _fetch_douban_cast(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not (DOUBAN_API_AVAILABLE and self.douban_api): return []
        douban_data = self.douban_api.get_acting(
            name=media_info.get("Name"), imdbid=media_info.get("ProviderIds", {}).get("Imdb"),
            mtype="movie" if media_info.get("Type") == "Movie" else "tv", year=str(media_info.get("ProductionYear", "")),
            douban_id_override=media_info.get("ProviderIds", {}).get("Douban")
        )
        return douban_data.get("cast", []) if douban_data and not douban_data.get("error") else []

    def _format_douban_cast(self, douban_api_actors_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted, seen_ids, seen_names = [], set(), set()
        for item in douban_api_actors_raw:
            douban_id = str(item.get("id", "")).strip() or None
            name_zh = str(item.get("name", "")).strip()
            if not name_zh: continue
            if douban_id:
                if douban_id in seen_ids: continue
                seen_ids.add(douban_id)
            else:
                name_sig = f"{name_zh.lower()}|{str(item.get('original_name', '')).lower().strip()}"
                if name_sig in seen_names: continue
                seen_names.add(name_sig)
            formatted.append({
                "name": name_zh, "original_name": str(item.get("original_name", "")).strip(),
                "character": str(item.get("character", "")).strip(), "douban_id": douban_id,
            })
        return formatted
    
    def _find_person_in_map_by_douban_id(self, douban_id: str, cursor: sqlite3.Cursor) -> Optional[sqlite3.Row]:
        """
        根据豆瓣名人ID在 person_identity_map 表中查找对应的记录。
        """
        if not douban_id:
            return None
        try:
            cursor.execute(
                "SELECT * FROM person_identity_map WHERE douban_celebrity_id = ?",
                (douban_id,)
            )
            return cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"通过豆瓣ID '{douban_id}' 查询 person_identity_map 时出错: {e}")
            return None

    def _process_cast_list_from_local(self, local_cast_list: List[Dict[str, Any]], emby_item_info: Dict[str, Any], cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
        """
        【修复版】接收一个数据库游标，而不是自己创建连接。
        """
        # 不再自己创建 conn 和 cursor
        try:
            douban_candidates = self._format_douban_cast(self._fetch_douban_cast(emby_item_info))
            
            final_cast_map = {actor['id']: actor for actor in local_cast_list if actor.get('id')}
            for actor in local_cast_list:
                if not actor.get('id'):
                    name_key = f"name_{actor.get('name', '').lower()}"
                    if name_key not in final_cast_map:
                        final_cast_map[name_key] = actor

            for d_actor in douban_candidates:
                match_found = False
                for l_actor in final_cast_map.values():
                    if utils.are_names_match(d_actor.get("name"), d_actor.get("original_name"), l_actor.get("name"), l_actor.get("original_name")):
                        l_actor["name"] = d_actor.get("name")
                        l_actor["character"] = self._select_best_role(l_actor.get("character"), d_actor.get("character"))
                        if d_actor.get("douban_id"):
                            l_actor["douban_id"] = d_actor.get("douban_id")
                        match_found = True
                        break
                
                if not match_found:
                    douban_id = d_actor.get("douban_id")
                    if not douban_id:
                        continue

                    person_map_entry = self._find_person_in_map_by_douban_id(douban_id, cursor)

                    if person_map_entry and person_map_entry["tmdb_person_id"]:
                        tmdb_id_from_map = person_map_entry["tmdb_person_id"]

                        if tmdb_id_from_map in final_cast_map:
                            final_cast_map[tmdb_id_from_map]["name"] = d_actor.get("name")
                            final_cast_map[tmdb_id_from_map]["character"] = self._select_best_role(final_cast_map[tmdb_id_from_map].get("character"), d_actor.get("character"))
                            logger.info(f"通过数据库映射更新了演员 '{d_actor.get('name')}' 的信息 (TMDbID: {tmdb_id_from_map})。")
                        else:
                            logger.info(f"通过数据库映射补充了新演员: '{d_actor.get('name')}' (TMDbID: {tmdb_id_from_map})")
                            new_actor_entry = {
                                "id": tmdb_id_from_map,
                                "name": d_actor.get("name"),
                                "original_name": d_actor.get("original_name"),
                                "character": d_actor.get("character"),
                                "adult": False, "gender": 0, "known_for_department": "Acting",
                                "popularity": 0.0, "profile_path": None, "cast_id": None,
                                "credit_id": None, "order": -1,
                                "imdb_id": person_map_entry["imdb_id"],
                                "douban_id": douban_id,
                            }
                            actor_key = tmdb_id_from_map
                            final_cast_map[actor_key] = new_actor_entry

            # 统一对所有演员进行处理
            final_cast_list = list(final_cast_map.values())
            for actor in final_cast_list:
                # 清理角色名
                original_character = actor.get('character')
                cleaned_character = utils.clean_character_name_static(original_character)
                
                # 翻译角色名
                translated_character = self._translate_actor_field(cleaned_character, "角色名", actor.get('name'), cursor)
                actor['character'] = translated_character

                # 翻译演员名
                actor['name'] = self._translate_actor_field(actor.get('name'), "演员名", actor.get('name'), cursor)

            return final_cast_list
        
        except Exception as e:
            logger.error(f"在 _process_cast_list_from_local 中发生错误: {e}", exc_info=True)
            # 发生错误时返回一个空列表，让主流程能继续处理，但可能会导致评分低
            return []

    def _format_and_complete_cast_list(self, cast_list: List[Dict[str, Any]], is_animation: bool) -> List[Dict[str, Any]]:
        """
        【最终优化版】补全演员信息并根据是否为动画格式化角色名。
        """
        perfect_cast = []
        for idx, actor in enumerate(cast_list):
            # 步骤 1: 补全演员信息 (头像等)
            # 如果信息不全 (没有头像)，才执行查询
            if not actor.get("profile_path"):
                tmdb_person_id = actor.get("id")
                if tmdb_person_id:
                    logger.info(f"演员 '{actor.get('name')}' 缺少头像信息，正在从 TMDb (ID: {tmdb_person_id}) 补全...")
                    tmdb_person_data = tmdb_handler.get_person_details_for_cast(int(tmdb_person_id), self.tmdb_api_key)
                    
                    if tmdb_person_data:
                        # 合并信息：保留原始的 name 和 character，用 TMDB 的数据补全其他字段
                        tmdb_person_data["name"] = actor.get("name")
                        tmdb_person_data["character"] = actor.get("character")
                        actor = tmdb_person_data # 用补全后的数据替换原始 actor
                    else:
                        logger.warning(f"无法从TMDb获取演员 {tmdb_person_id} 的详细信息，将使用现有信息。")
            
            # 步骤 2: 格式化角色名
            current_role = actor.get("character", "").strip()
            if is_animation:
                if current_role and not current_role.endswith("(配音)"):
                    actor["character"] = f"{current_role} (配音)"
                elif not current_role:
                    actor["character"] = "配音"
            elif not current_role: # 如果不是动画且角色名为空
                actor["character"] = "演员"

            # 步骤 3: 添加到最终列表
            actor["order"] = idx
            perfect_cast.append(actor)
                
        return perfect_cast

    def _process_item_core_logic(self, item_details_from_emby: Dict[str, Any], force_reprocess_this_item: bool = False) -> bool:
        """
        【最终修复版 V5】统一数据库事务管理，修复缓存和评分逻辑，确保所有代码块完整。
        """
        item_id = item_details_from_emby.get("Id")
        item_name_for_log = item_details_from_emby.get("Name", f"未知项目(ID:{item_id})")
        tmdb_id = item_details_from_emby.get("ProviderIds", {}).get("Tmdb")
        item_type = item_details_from_emby.get("Type")

        logger.info(f"--- 开始核心处理: '{item_name_for_log}' (TMDbID: {tmdb_id}) ---")

        if not tmdb_id or not self.local_data_path:
            error_msg = "缺少TMDbID" if not tmdb_id else "未配置本地数据路径"
            logger.warning(f"跳过处理 '{item_name_for_log}'，原因: {error_msg}。")
            self.save_to_failed_log(item_id, item_name_for_log, f"预处理失败: {error_msg}", item_type)
            return False

        # --- 统一数据库事务管理 ---
        conn = self._get_db_connection()
        try:
            cursor = conn.cursor()

            # 路径和数据准备
            cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
            base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
            base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
            image_override_dir = os.path.join(base_override_dir, "images")
            os.makedirs(image_override_dir, exist_ok=True)

            base_json_filename = "all.json" if item_type == "Movie" else "series.json"
            base_json_data = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
            if not base_json_data:
                raise ValueError(f"无法读取基础JSON文件: {os.path.join(base_cache_dir, base_json_filename)}")

            # 演员处理流程
            original_cast_from_local = base_json_data.get("credits", {}).get("cast") or base_json_data.get("casts", {}).get("cast", [])
            initial_actor_count = len(original_cast_from_local)
            
            intermediate_cast = self._process_cast_list_from_local(original_cast_from_local, item_details_from_emby, cursor)
            
            genres = item_details_from_emby.get("Genres", [])
            is_animation = "Animation" in genres or "动画" in genres
            if is_animation:
                logger.info(f"检测到媒体 '{item_name_for_log}' 为动画片，将处理配音角色。")

            final_cast_perfect = self._format_and_complete_cast_list(intermediate_cast, is_animation)
            
            # 元数据文件生成
            if item_type == "Movie":
                base_json_data.setdefault("casts", {})["cast"] = final_cast_perfect
            else:
                base_json_data.setdefault("credits", {})["cast"] = final_cast_perfect
            
            override_json_path = os.path.join(base_override_dir, base_json_filename)
            with open(override_json_path, 'w', encoding='utf-8') as f:
                json.dump(base_json_data, f, ensure_ascii=False, indent=4)
            logger.info(f"成功生成覆盖元数据文件: {override_json_path}")

            if item_type == "Series" and self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False):
                logger.info(f"深度处理已启用，开始为 '{item_name_for_log}' 的所有子项目注入演员表...")
                for filename in os.listdir(base_cache_dir):
                    if filename.startswith("season-") and filename.endswith(".json"):
                        child_json_path = os.path.join(base_cache_dir, filename)
                        child_data = _read_local_json(child_json_path)
                        if child_data:
                            child_data.setdefault("credits", {})["cast"] = final_cast_perfect
                            override_child_path = os.path.join(base_override_dir, filename)
                            try:
                                with open(override_child_path, 'w', encoding='utf-8') as f:
                                    json.dump(child_data, f, ensure_ascii=False, indent=4)
                            except Exception as e:
                                logger.error(f"写入子项目JSON失败: {override_child_path}, {e}")

            # 图片同步
            if self.sync_images_enabled:
                logger.info(f"图片同步已启用，开始为 '{item_name_for_log}' 下载图片...")
                image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
                if item_type == "Movie": image_map["Thumb"] = "landscape.jpg"
                for image_type, filename in image_map.items():
                    emby_handler.download_emby_image(item_id, image_type, os.path.join(image_override_dir, filename), self.emby_url, self.emby_api_key)
                
                if item_type == "Series" and self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False):
                    children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id) or []
                    for child in children:
                        child_type, child_id = child.get("Type"), child.get("Id")
                        if child_type == "Season":
                            season_number = child.get("IndexNumber")
                            if season_number is not None:
                                emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}.jpg"), self.emby_url, self.emby_api_key)
                        elif child_type == "Episode":
                            season_number, episode_number = child.get("ParentIndexNumber"), child.get("IndexNumber")
                            if season_number is not None and episode_number is not None:
                                emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}-episode-{episode_number}.jpg"), self.emby_url, self.emby_api_key)

            # 统计、评分和刷新
            final_actor_count = len(final_cast_perfect)
            logger.info(f"【处理统计】'{item_name_for_log}':")
            logger.info(f"  - 本地缓存演员: {initial_actor_count} 位")
            newly_added_count = final_actor_count - initial_actor_count
            if newly_added_count > 0:
                logger.info(f"  - 通过豆瓣新增演员: {newly_added_count} 位")
            logger.info(f"  - 最终写入JSON: {final_actor_count} 位")

            processing_score = self._evaluate_cast_processing_quality(final_cast_perfect, initial_actor_count)
            min_score_for_review = float(self.config.get("min_score_for_review", constants.DEFAULT_MIN_SCORE_FOR_REVIEW))
            
            refresh_success = emby_handler.refresh_emby_item_metadata(
                item_emby_id=item_id,  # <--- 明确指定 item_emby_id = item_id
                emby_server_url=self.emby_url,
                emby_api_key=self.emby_api_key,
                replace_all_metadata_param=True,
                item_name_for_log=item_name_for_log
            )
            
            if not refresh_success:
                raise RuntimeError("触发Emby刷新失败")
            
            if processing_score < min_score_for_review:
                self.save_to_failed_log(item_id, item_name_for_log, f"处理评分过低 ({processing_score:.1f} / {min_score_for_review:.1f})", item_type, score=processing_score)
            else:
                self.save_to_processed_log(item_id, item_name_for_log, score=processing_score)
                self._remove_from_failed_log_if_exists(item_id)
            
            # 所有操作成功，提交数据库事务
            conn.commit()
            logger.info(f"--- 核心处理结束: '{item_name_for_log}' (数据库事务已提交) ---")
            return True

        except Exception as e:
            logger.error(f"处理 '{item_name_for_log}' 时发生严重错误: {e}", exc_info=True)
            if conn:
                conn.rollback()
            # 记录到失败日志，但不包含评分
            self.save_to_failed_log(item_id, item_name_for_log, f"核心处理异常: {str(e)}", item_type)
            return False
        finally:
            if conn:
                conn.close()

    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False, process_episodes: bool = True) -> bool:
        if self.is_stop_requested(): return False
        if not force_reprocess_this_item and emby_item_id in self.processed_items_cache: return True

        item_details = emby_handler.get_emby_item_details(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
        if not item_details:
            self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", "无法获取Emby项目详情")
            return False
        
        self.config[constants.CONFIG_OPTION_PROCESS_EPISODES] = process_episodes
        
        return self._process_item_core_logic(item_details, force_reprocess_this_item)

    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False, process_episodes: bool = True):
        self.clear_stop_signal()
        if force_reprocess_all: self.clear_processed_log()
        
        libs = self.config.get("libraries_to_process", [])
        if not libs: return

        movies = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Movie", self.emby_user_id, libs) or []
        series = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Series", self.emby_user_id, libs) or []
        all_items = movies + series
        
        total = len(all_items)
        if total == 0: return

        for i, item in enumerate(all_items):
            if self.is_stop_requested(): break
            item_name = item.get('Name', f"ID:{item.get('Id')}")
            if update_status_callback:
                update_status_callback(int(((i + 1) / total) * 100), f"处理中 ({i+1}/{total}): {item_name}")
            
            self.process_single_item(item.get('Id'), force_reprocess_all, process_episodes)
            
            time.sleep(float(self.config.get("delay_between_items_sec", 0.5)))
        
        if not self.is_stop_requested() and update_status_callback:
            update_status_callback(100, "全量处理完成")

    def translate_cast_list_for_editing(self, cast_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        【新】为手动编辑页面提供的一键翻译功能。
        它只翻译，不执行任何其他操作，并返回翻译后的列表。
        """
        logger.info(f"手动编辑-翻译：开始处理 {len(cast_list)} 位演员的姓名和角色。")
        translated_cast = [dict(actor) for actor in cast_list]
        
        conn = self._get_db_connection()
        try:
            cursor = conn.cursor()
            for i, actor in enumerate(translated_cast):
                actor_name_for_log = actor.get('name', '未知演员')
                
                # 翻译演员名
                name_to_translate = actor.get('name', '').strip()
                if name_to_translate:
                    translated_name = self._translate_actor_field(name_to_translate, "演员名", actor_name_for_log, cursor)
                    if translated_name and translated_name != name_to_translate:
                        translated_cast[i]['name'] = translated_name
                        actor_name_for_log = translated_name

                # 翻译角色名
                role_to_translate = actor.get('role', '').strip()
                if role_to_translate:
                    translated_role = self._translate_actor_field(role_to_translate, "角色名", actor_name_for_log, cursor)
                    if translated_role and translated_role != role_to_translate:
                        translated_cast[i]['role'] = translated_role
            
            conn.commit() # 提交翻译缓存的更改
        except Exception as e:
            logger.error(f"手动编辑-翻译时发生错误: {e}", exc_info=True)
            if conn: conn.rollback()
        finally:
            if conn: conn.close()

        logger.info("手动编辑-翻译完成。")
        return translated_cast

    def process_item_with_manual_cast(self, item_id: str, manual_cast_list: List[Dict[str, Any]], item_name: str) -> bool:
        """
        【新】使用手动编辑的演员列表来处理单个媒体项目。
        这个函数是为手动编辑 API 设计的，它会执行完整的处理流程。
        """
        logger.info(f"手动处理流程启动：ItemID: {item_id} ('{item_name}')，收到 {len(manual_cast_list)} 位演员。")

        # 1. 获取原始 Emby 详情，用于对比名字变更
        try:
            item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details:
                logger.error(f"手动处理失败：无法获取项目 {item_id} 的详情。")
                return False
            current_emby_cast_raw = item_details.get("People", [])
        except Exception as e:
            logger.error(f"手动处理失败：获取项目 {item_id} 详情时异常: {e}")
            return False

        # 2. 前置更新：更新被手动修改了名字的 Person 条目
        logger.info("手动处理：开始前置更新演员名字...")
        original_names_map = {p.get("Id"): p.get("Name") for p in current_emby_cast_raw if p.get("Id")}
        for actor in manual_cast_list:
            actor_id = actor.get("embyPersonId")
            new_name = actor.get("name")
            original_name = original_names_map.get(actor_id)
            if actor_id and new_name and original_name and new_name != original_name:
                logger.info(f"  - 名字变更: '{original_name}' -> '{new_name}' (ID: {actor_id})")
                emby_handler.update_person_details(
                    person_id=actor_id,
                    new_data={"Name": new_name},
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id
                )
        logger.info("手动处理：演员名字前置更新完成。")

        # 3. 最终更新：将手动编辑的列表更新到媒体
        logger.info(f"手动处理：准备将 {len(manual_cast_list)} 位演员更新到 Emby...")
        # 转换成 emby_handler 期望的格式
        cast_for_emby_handler = [
            {"name": actor.get("name"), "character": actor.get("role"), "emby_person_id": actor.get("embyPersonId")}
            for actor in manual_cast_list
        ]
        update_success = emby_handler.update_emby_item_cast(
            item_id=item_id,
            new_cast_list_for_handler=cast_for_emby_handler,
            emby_server_url=self.emby_url,
            emby_api_key=self.emby_api_key,
            user_id=self.emby_user_id
        )

        if not update_success:
            logger.error(f"手动处理失败：更新 Emby 项目 {item_id} 演员信息时失败。")
            return False

        # 4. 收尾工作：刷新EMBY并更新日志
        logger.info("手动处理：演员更新成功，准备刷新 Emby 元数据...")
        emby_handler.refresh_emby_item_metadata(
            item_id, self.emby_url, self.emby_api_key, 
            replace_all_metadata_param=True, 
            item_name_for_log=item_name
        )
        
        # 5. 更新处理日志，标记为已处理并给高分
        self.save_to_processed_log(item_id, item_name, score=10.0)
        self._remove_from_failed_log_if_exists(item_id)
        
        logger.info(f"手动处理 Item ID: {item_id} ('{item_name}') 流程完成。")
        return True

    def get_cast_for_editing(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        【新】获取单个媒体项的详细信息和格式化后的演员列表，专为编辑页面设计。
        """
        logger.info(f"为编辑页面获取数据：ItemID {item_id}")
        # 1. 从 Emby 获取详情
        try:
            emby_details = emby_handler.get_emby_item_details(
                item_id, self.emby_url, self.emby_api_key, self.emby_user_id
            )
            if not emby_details:
                logger.error(f"获取编辑数据失败：在Emby中未找到项目 {item_id}")
                return None
        except Exception as e:
            logger.error(f"获取编辑数据失败：获取Emby详情时异常 for ItemID {item_id}: {e}", exc_info=True)
            return None

        # 2. 从 failed_log 获取额外信息
        failed_log_info = {}
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT error_message, score FROM failed_log WHERE item_id = ?", (item_id,))
                row = cursor.fetchone()
                if row: failed_log_info = dict(row)
        except Exception as e:
            logger.error(f"获取编辑数据：查询failed_log失败: {e}")

        # 3. 格式化演员列表，并为每个演员获取头像
        cast_for_frontend = []
        if emby_details.get("People"):
            for person in emby_details.get("People", []):
                person_id = person.get("Id")
                if not (person_id and person.get("Name")): continue
                
                provider_ids = person.get("ProviderIds", {})
                actor_image_tag = emby_handler.get_person_image_tag(person_id, self.emby_url, self.emby_api_key, self.emby_user_id)
                
                cast_for_frontend.append({
                    "embyPersonId": str(person_id),
                    "name": person["Name"],
                    "role": person.get("Role", ""),
                    "imdbId": provider_ids.get("Imdb"),
                    "doubanId": provider_ids.get("Douban"),
                    "tmdbId": provider_ids.get("Tmdb"),
                    "image_tag": actor_image_tag
                })

        # 4. 组合最终响应数据
        response_data = {
            "item_id": item_id,
            "item_name": emby_details.get("Name"),
            "item_type": emby_details.get("Type"),
            "image_tag": emby_details.get('ImageTags', {}).get('Primary'),
            "original_score": failed_log_info.get("score"),
            "review_reason": failed_log_info.get("error_message"),
            "current_emby_cast": cast_for_frontend,
            "search_links": {
                "google_search_wiki": utils.generate_search_url('wikipedia', emby_details.get("Name"), emby_details.get("ProductionYear"))
            }
        }
        return response_data
    
    def close(self):
        if self.douban_api: self.douban_api.close()
        logger.debug("MediaProcessor closed.")


class SyncHandler:
    def __init__(self, db_path: str, emby_url: str, emby_api_key: str, emby_user_id: Optional[str], stop_event: threading.Event):
        """
        【修复】初始化时接收一个 threading.Event 对象用于停止。
        """
        self.db_path = db_path
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        self.stop_event = stop_event # 保存停止事件
        logger.info(f"SyncHandler initialized with stop event.")

    def _get_db_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def sync_emby_person_map_to_db(self, full_sync: bool = False, update_status_callback: Optional[callable] = None):
        mode_text = "全量同步 (Full Sync)" if full_sync else "增量同步 (Incremental Sync)"
        logger.info(f"开始同步 Emby Person 映射表，模式: {mode_text}")
        if update_status_callback: update_status_callback(0, f"正在从Emby获取所有人物信息... (模式: {mode_text})")

        # ✨ 检查点 #1: 在开始重量级操作前检查一次 ✨
        if self.stop_event.is_set():
            logger.info("同步任务在开始前被用户中止。")
            if update_status_callback: update_status_callback(-1, "任务已取消")
            return

        persons_from_emby = emby_handler.get_all_persons_from_emby(self.emby_url, self.emby_api_key, self.emby_user_id)
        if persons_from_emby is None:
            if update_status_callback: update_status_callback(-1, "从Emby获取人物信息失败")
            return
        
        total_emby_persons = len(persons_from_emby)
        logger.info(f"从Emby API获取到 {total_emby_persons} 个 Person 基础条目。")

        conn = self._get_db_conn()
        cursor = conn.cursor()
        
        persons_to_process_details = []
        if full_sync:
            persons_to_process_details = persons_from_emby
        else:
            try:
                cursor.execute("SELECT emby_person_id FROM person_identity_map")
                existing_ids = {row['emby_person_id'] for row in cursor.fetchall()}
                persons_to_process_details = [p for p in persons_from_emby if p.get("Id") not in existing_ids]
                logger.info(f"发现 {len(persons_to_process_details)} 位新演员需要处理。")
            except sqlite3.Error as e:
                logger.error(f"无法从本地数据库读取现有演员ID: {e}")
                if update_status_callback: update_status_callback(-1, "读取本地数据库失败")
                conn.close()
                return

        total_to_process = len(persons_to_process_details)
        if total_to_process == 0 and not full_sync:
            logger.info("没有发现新演员，增量同步完成。")
            if update_status_callback: update_status_callback(100, "增量同步完成，无新演员。")
            conn.close()
            return
            
        stats = {"total": total_to_process, "processed": 0, "updated": 0, "errors": 0}

        try:
            for idx, person_base in enumerate(persons_to_process_details):
                # ✨ 检查点 #2: 在每次循环开始时检查，这是最关键的“刹车” ✨
                if self.stop_event.is_set():
                    logger.info(f"同步任务在处理第 {idx+1} 个演员时被用户中止。")
                    # 提交已经处理完的部分
                    conn.commit()
                    logger.info("已将中止前处理完成的数据提交到数据库。")
                    if update_status_callback: update_status_callback(-1, "任务已取消")
                    return # 直接退出函数

                stats["processed"] += 1
                if update_status_callback:
                    progress = int(((idx + 1) / total_to_process) * 100) if total_to_process > 0 else 100
                    update_status_callback(progress, f"正在处理第 {idx+1}/{total_to_process} 个新演员...")

                emby_pid = person_base.get("Id")
                if not emby_pid: continue

                person_details = emby_handler.get_emby_item_details(emby_pid, self.emby_url, self.emby_api_key, self.emby_user_id)
                if not person_details:
                    stats["errors"] += 1
                    continue

                provider_ids = person_details.get("ProviderIds", {})
                update_data = {
                    "emby_pid": emby_pid,
                    "emby_name": person_details.get("Name"),
                    "tmdb_id": provider_ids.get("Tmdb"),
                    "imdb_id": provider_ids.get("Imdb"),
                    "douban_id": provider_ids.get("Douban"),
                }
                
                sql = """
                    INSERT INTO person_identity_map (emby_person_id, emby_person_name, imdb_id, tmdb_person_id, douban_celebrity_id, last_synced_at, last_updated_at)
                    VALUES (:emby_pid, :emby_name, :imdb_id, :tmdb_id, :douban_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(emby_person_id) DO UPDATE SET
                        emby_person_name = COALESCE(excluded.emby_person_name, emby_person_name),
                        imdb_id = COALESCE(excluded.imdb_id, imdb_id),
                        tmdb_person_id = COALESCE(excluded.tmdb_person_id, tmdb_person_id),
                        douban_celebrity_id = COALESCE(excluded.douban_celebrity_id, douban_celebrity_id),
                        last_synced_at = CURRENT_TIMESTAMP;
                """
                try:
                    cursor.execute(sql, update_data)
                    if cursor.rowcount > 0:
                        stats["updated"] += 1
                except sqlite3.Error as e:
                    logger.error(f"同步 Person (ID: {emby_pid}) 到数据库时失败: {e}")
                    stats["errors"] += 1
                
                if idx > 0 and idx % 200 == 0:
                    conn.commit()
            
            # 如果循环正常结束（没有被中止），提交最后的更改
            if not self.stop_event.is_set():
                conn.commit()
                logger.info("所有需要处理的 Person 记录已完成，数据库已提交。")

        except Exception as e:
            logger.error(f"同步映射表主循环发生错误: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

        # 任务结束后的状态报告
        if not self.stop_event.is_set():
            logger.info(f"--- Emby Person 映射表同步完成 ({mode_text}) ---")
            logger.info(f"总计处理: {stats['processed']}, 更新/新增: {stats['updated']}, 错误: {stats['errors']}")
            if update_status_callback:
                if stats["errors"] > 0:
                    update_status_callback(-1, f"同步完成但有{stats['errors']}个错误。")
                else:
                    update_status_callback(100, f"同步完成。共处理 {stats['processed']} 人，更新/新增 {stats['updated']} 条记录。")