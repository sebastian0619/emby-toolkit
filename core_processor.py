# core_processor.py (最终完整版 - 已修复)

import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
import threading
import time
import copy
import random
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
        # ✨✨✨ 增加 timeout 参数，单位是秒 ✨✨✨
        # 5-10秒是一个比较合理的等待时间
        conn = sqlite3.connect(self.db_path, timeout=10.0)
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
        # 1. 检查 Douban API 是否可用
        if not (DOUBAN_API_AVAILABLE and self.douban_api):
            logger.warning("在线豆瓣功能不可用 (DoubanApi 未导入或初始化失败)，无法获取豆瓣演员。")
            return []

        # 2. 直接执行在线请求逻辑
        logger.debug("神医专用版：开始在线请求豆瓣 API 获取演员...")
        douban_data = self.douban_api.get_acting(
            name=media_info.get("Name"),
            imdbid=media_info.get("ProviderIds", {}).get("Imdb"),
            mtype="movie" if media_info.get("Type") == "Movie" else "tv",
            year=str(media_info.get("ProductionYear", "")),
            douban_id_override=media_info.get("ProviderIds", {}).get("Douban")
        )
        
        if douban_data and not douban_data.get("error"):
            return douban_data.get("cast", [])
        
        # 如果请求失败或返回错误，打印警告并返回空列表
        if douban_data and douban_data.get("error"):
            logger.warning(f"请求豆瓣 API 失败: {douban_data.get('message')}")
            
        return []

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
        【最终确认版】处理本地演员列表，与豆瓣数据进行匹配、丰富，并根据数据库映射有条件地补全新增演员。
        """
        try:
            douban_candidates = self._format_douban_cast(self._fetch_douban_cast(emby_item_info))
            final_cast_map = {actor['id']: actor for actor in local_cast_list if actor.get('id')}
            for actor in local_cast_list:
                if not actor.get('id'):
                    name_key = f"name_{actor.get('name', '').lower()}"
                    if name_key not in final_cast_map:
                        final_cast_map[name_key] = actor

            for d_actor in douban_candidates:
                if self.is_stop_requested(): raise InterruptedError("任务在匹配豆瓣演员时被中止")
                
                match_found = False
                
                # --- 匹配阶段 ---
                # 优先级 1: 名字
                logger.debug(f"  -> 尝试为豆瓣演员 '{d_actor.get('name')}' 进行 [名字] 匹配...")
                for l_actor in final_cast_map.values():
                    if utils.are_names_match(d_actor.get("name"), d_actor.get("original_name"), l_actor.get("name"), l_actor.get("original_name")):
                        logger.info(f"  匹配成功 (按名字): 豆瓣演员 '{d_actor.get('name')}' -> 本地演员 '{l_actor.get('name')}'")
                        l_actor["name"] = d_actor.get("name"); l_actor["character"] = self._select_best_role(l_actor.get("character"), d_actor.get("character"))
                        if d_actor.get("douban_id") and not l_actor.get("douban_id"): l_actor["douban_id"] = d_actor.get("douban_id")
                        if d_actor.get("imdb_id") and not l_actor.get("imdb_id"): l_actor["imdb_id"] = d_actor.get("imdb_id")
                        match_found = True; break
                
                # 优先级 2: 豆瓣ID
                if not match_found:
                    d_douban_id = d_actor.get("douban_id")
                    logger.debug(f"  -> 名字匹配失败。尝试为 '{d_actor.get('name')}' (豆瓣ID: {d_douban_id}) 进行 [豆瓣ID] 匹配...")
                    if d_douban_id:
                        for l_actor in final_cast_map.values():
                            if l_actor.get("douban_id") == d_douban_id:
                                logger.info(f"  匹配成功 (按豆瓣ID '{d_douban_id}'): 豆瓣演员 '{d_actor.get('name')}' -> 本地演员 '{l_actor.get('name')}'")
                                l_actor["name"] = d_actor.get("name"); l_actor["character"] = self._select_best_role(l_actor.get("character"), d_actor.get("character"))
                                if d_actor.get("imdb_id") and not l_actor.get("imdb_id"): l_actor["imdb_id"] = d_actor.get("imdb_id")
                                match_found = True; break
                
                # 优先级 3: IMDb ID
                if not match_found:
                    d_imdb_id = d_actor.get("imdb_id")
                    logger.debug(f"  -> 豆瓣ID匹配失败。尝试为 '{d_actor.get('name')}' (IMDb ID: {d_imdb_id}) 进行 [IMDb ID] 匹配...")
                    if d_imdb_id:
                        for l_actor in final_cast_map.values():
                            if l_actor.get("imdb_id") == d_imdb_id:
                                logger.info(f"  匹配成功 (按IMDb ID '{d_imdb_id}'): 豆瓣演员 '{d_actor.get('name')}' -> 本地演员 '{l_actor.get('name')}'")
                                l_actor["name"] = d_actor.get("name"); l_actor["character"] = self._select_best_role(l_actor.get("character"), d_actor.get("character"))
                                if d_actor.get("douban_id") and not l_actor.get("douban_id"): l_actor["douban_id"] = d_actor.get("douban_id")
                                match_found = True; break

                # --- 新增阶段 (严格按规则) ---
                if not match_found:
                    logger.info(f"  -> 所有匹配均失败，放弃为 '{d_actor.get('name')}' 作为新演员补充。")
                    d_douban_id = d_actor.get("douban_id")
                    if d_douban_id:
                        person_map_entry = self._find_person_in_map_by_douban_id(d_douban_id, cursor)
                        if person_map_entry and person_map_entry["tmdb_person_id"]:
                            tmdb_id_from_map = person_map_entry["tmdb_person_id"]
                            if tmdb_id_from_map not in final_cast_map:
                                # 按需获取 IMDb ID 的逻辑可以保留，因为它只在新增时触发
                                imdb_id_for_new_actor = person_map_entry["imdb_id"] or d_actor.get("imdb_id")
                                if not imdb_id_for_new_actor and self.douban_api:
                                    details = self.douban_api.celebrity_details(d_douban_id)
                                    if details and not details.get("error"):
                                        imdb_attr = details.get("attrs", {}).get("imdb")
                                        if isinstance(imdb_attr, list) and imdb_attr: imdb_id_for_new_actor = imdb_attr[0]
                                        elif isinstance(imdb_attr, str): imdb_id_for_new_actor = imdb_attr
                                
                                logger.info(f"通过数据库映射补充了新演员: '{d_actor.get('name')}' (TMDbID: {tmdb_id_from_map})")
                                new_actor_entry = {
                                    "id": tmdb_id_from_map, "name": d_actor.get("name"), "original_name": d_actor.get("original_name"),
                                    "character": d_actor.get("character"), "adult": False, "gender": 0, "known_for_department": "Acting",
                                    "popularity": 0.0, "profile_path": None, "cast_id": None, "credit_id": None, "order": -1,
                                    "imdb_id": imdb_id_for_new_actor, "douban_id": d_douban_id, "_is_newly_added": True
                                }
                                final_cast_map[tmdb_id_from_map] = new_actor_entry
                        else:
                            logger.debug(f"  -> 丢弃演员: '{d_actor.get('name')}' (豆瓣ID: {d_douban_id}) 在 person_identity_map 中无有效TMDb映射。")
                    else:
                        logger.debug(f"  -> 丢弃演员: '{d_actor.get('name')}' 因无豆瓣ID且无匹配。")

            # 统一对所有演员进行处理
            final_cast_list = list(final_cast_map.values())
            for actor in final_cast_list:
                # ✨ 在循环开始时检查 ✨
                if self.is_stop_requested(): raise InterruptedError("任务在翻译演员列表时被中止")
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
        【已移除头像补全】只负责格式化角色名和排序。
        """
        perfect_cast = []
        logger.info("格式化演员列表：开始处理角色名和排序。") # 增加一条日志说明
        
        for idx, actor in enumerate(cast_list):
            # 步骤 1: 补全演员信息 (头像等)  <--- 这整个部分都被删除了

            # 步骤 2: 格式化角色名 (这部分保留)
            current_role = actor.get("character", "").strip()
            if is_animation:
                if current_role and not current_role.endswith("(配音)"):
                    actor["character"] = f"{current_role} (配音)"
                elif not current_role:
                    actor["character"] = "配音"
            elif not current_role: # 如果不是动画且角色名为空
                actor["character"] = "演员"

            # 步骤 3: 添加到最终列表 (这部分保留)
            actor["order"] = idx
            perfect_cast.append(actor)
                
        return perfect_cast

    def _process_item_core_logic(self, item_details_from_emby: Dict[str, Any], force_reprocess_this_item: bool = False) -> bool:
        """
        【最终修复版 V6】引入深拷贝，确保 cache 数据不被修改，并包含所有健壮性修复。
        """
        item_id = item_details_from_emby.get("Id")
        item_name_for_log = item_details_from_emby.get("Name", f"未知项目(ID:{item_id})")
        tmdb_id = item_details_from_emby.get("ProviderIds", {}).get("Tmdb")
        item_type = item_details_from_emby.get("Type")

        logger.info(f"--- 开始核心处理: '{item_name_for_log}' (TMDbID: {tmdb_id}) ---")

        if self.is_stop_requested():
            logger.info(f"任务在处理 '{item_name_for_log}' 前被中止。")
            return False

        if not tmdb_id or not self.local_data_path:
            error_msg = "缺少TMDbID" if not tmdb_id else "未配置本地数据路径"
            logger.warning(f"跳过处理 '{item_name_for_log}'，原因: {error_msg}。")
            self.save_to_failed_log(item_id, item_name_for_log, f"预处理失败: {error_msg}", item_type)
            return False

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
            
            # 1. 读取原始 cache 数据
            base_json_data_original = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
            if not base_json_data_original:
                raise ValueError(f"无法读取基础JSON文件: {os.path.join(base_cache_dir, base_json_filename)}")
            
            # 2. 立刻创建深拷贝副本，后续所有操作都针对副本
            base_json_data_for_override = copy.deepcopy(base_json_data_original)

            if self.is_stop_requested(): raise InterruptedError("任务被中止")

            # 演员处理流程
            original_cast_from_local = base_json_data_original.get("credits", {}).get("cast") or base_json_data_original.get("casts", {}).get("cast", [])
            initial_actor_count = len(original_cast_from_local)
            
            intermediate_cast = self._process_cast_list_from_local(original_cast_from_local, item_details_from_emby, cursor)
            
            genres = item_details_from_emby.get("Genres", [])
            is_animation = "Animation" in genres or "动画" in genres
            if is_animation:
                logger.info(f"检测到媒体 '{item_name_for_log}' 为动画片，将处理配音角色。")

            if self.is_stop_requested(): raise InterruptedError("任务被中止")

            final_cast_perfect = self._format_and_complete_cast_list(intermediate_cast, is_animation)
            
            # 3. 将处理结果写入到副本中
            if item_type == "Movie":
                base_json_data_for_override.setdefault("casts", {})["cast"] = final_cast_perfect
            else:
                base_json_data_for_override.setdefault("credits", {})["cast"] = final_cast_perfect
            
            # 4. 将副本写入 override 文件 (使用临时文件保证安全)
            override_json_path = os.path.join(base_override_dir, base_json_filename)
            temp_json_path = f"{override_json_path}.{random.randint(1000, 9999)}.tmp"
            try:
                with open(temp_json_path, 'w', encoding='utf-8') as f:
                    json.dump(base_json_data_for_override, f, ensure_ascii=False, indent=4)
                os.replace(temp_json_path, override_json_path)
                logger.info(f"✅ 成功生成覆盖元数据文件: {override_json_path}")
            except Exception as e_write:
                logger.error(f"写入或重命名元数据文件时发生错误: {e_write}", exc_info=True)
                if os.path.exists(temp_json_path): os.remove(temp_json_path)
                raise e_write

            # 5. 处理分集时，也对子文件使用深拷贝
            if item_type == "Series" and self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False):
                logger.info(f"深度处理已启用，开始为 '{item_name_for_log}' 的所有子项目注入演员表...")
                for filename in os.listdir(base_cache_dir):
                    if filename.startswith("season-") and filename.endswith(".json"):
                        child_json_original = _read_local_json(os.path.join(base_cache_dir, filename))
                        if child_json_original:
                            child_json_for_override = copy.deepcopy(child_json_original)
                            child_json_for_override.setdefault("credits", {})["cast"] = final_cast_perfect
                            override_child_path = os.path.join(base_override_dir, filename)
                            try:
                                with open(override_child_path, 'w', encoding='utf-8') as f:
                                    json.dump(child_json_for_override, f, ensure_ascii=False, indent=4)
                            except Exception as e:
                                logger.error(f"写入子项目JSON失败: {override_child_path}, {e}")

            if self.sync_images_enabled:
                if self.is_stop_requested(): raise InterruptedError("任务被中止")
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

            if self.is_stop_requested(): raise InterruptedError("任务被中止")

            processing_score = self._evaluate_cast_processing_quality(final_cast_perfect, initial_actor_count)
            min_score_for_review = float(self.config.get("min_score_for_review", constants.DEFAULT_MIN_SCORE_FOR_REVIEW))
            
            refresh_success = emby_handler.refresh_emby_item_metadata(
                item_emby_id=item_id,
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
            
            conn.commit()
            logger.info(f"--- 核心处理结束: '{item_name_for_log}' (数据库事务已提交) ---")
            return True

        except InterruptedError:
            logger.info(f"处理 '{item_name_for_log}' 的过程中被用户中止。")
            if conn:
                conn.rollback()
            return False
        except Exception as e:
            logger.error(f"处理 '{item_name_for_log}' 时发生严重错误: {e}", exc_info=True)
            if conn:
                conn.rollback()
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
        【神医模式重构版 V2】使用手动编辑的演员列表，生成 override 文件并触发刷新。引入深拷贝确保 cache 安全。
        """
        logger.info(f"手动处理流程启动 (神医模式)：ItemID: {item_id} ('{item_name}')")

        # 1. 获取媒体的 TMDb ID 和类型，用于定位文件
        try:
            item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details:
                logger.error(f"手动处理失败：无法获取项目 {item_id} 的详情。")
                return False
            
            tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
            item_type = item_details.get("Type")

            if not tmdb_id:
                logger.error(f"手动处理失败：项目 {item_id} 缺少 TMDb ID，无法定位 override 文件。")
                return False
        except Exception as e:
            logger.error(f"手动处理失败：获取项目 {item_id} 详情时异常: {e}")
            return False

        # 2. 构造 override 文件的完整路径
        cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
        base_json_filename = "all.json" if item_type == "Movie" else "series.json"
        base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
        os.makedirs(base_override_dir, exist_ok=True)
        override_json_path = os.path.join(base_override_dir, base_json_filename)

        # 3. 读取原始的 cache 文件作为基础
        base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
        base_json_data_original = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
        if not base_json_data_original:
            logger.warning(f"手动处理：未找到原始 cache 文件，将创建一个新的元数据结构。")
            base_json_data_original = {}

        # ✨ 4. 立刻创建深拷贝副本，后续所有操作都针对副本 ✨
        base_json_data_for_override = copy.deepcopy(base_json_data_original)

        # 5. 将手动编辑的演员列表，转换为符合 all.json 格式的列表
        final_cast_for_json = []
        for idx, actor in enumerate(manual_cast_list):
            final_cast_for_json.append({
                "id": actor.get("tmdbId"),
                "name": actor.get("name"),
                "original_name": actor.get("original_name"),
                "character": actor.get("role"),
                "order": idx,
                "adult": False,
                "gender": 0,
                "known_for_department": "Acting",
                "popularity": 0.0,
                "profile_path": None,
                "cast_id": None,
                "credit_id": None,
                "douban_id": actor.get("doubanId"),
                "imdb_id": actor.get("imdbId"),
            })

        # 6. 将新的演员列表更新到副本中
        if item_type == "Movie":
            base_json_data_for_override.setdefault("casts", {})["cast"] = final_cast_for_json
        else: # Series
            base_json_data_for_override.setdefault("credits", {})["cast"] = final_cast_for_json

        # 7. 将更新后的完整元数据写入 override 文件 (使用临时文件保证安全)
        temp_json_path = f"{override_json_path}.{random.randint(1000, 9999)}.tmp"
        try:
            with open(temp_json_path, 'w', encoding='utf-8') as f:
                json.dump(base_json_data_for_override, f, ensure_ascii=False, indent=4)
            os.replace(temp_json_path, override_json_path)
            logger.info(f"手动处理：成功生成覆盖元数据文件: {override_json_path}")
        except Exception as e_write:
            logger.error(f"手动处理：写入 override 文件时发生错误: {e_write}", exc_info=True)
            if os.path.exists(temp_json_path): os.remove(temp_json_path)
            return False

         # 8. 根据配置，处理分集 (注入演员表)
        if item_type == "Series" and self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False):
            logger.info(f"手动处理：深度处理已启用，将为所有子项目注入手动编辑后的演员表...")
            for filename in os.listdir(base_cache_dir):
                if filename.startswith("season-") and filename.endswith(".json"):
                    child_json_original = _read_local_json(os.path.join(base_cache_dir, filename))
                    if child_json_original:
                        child_json_for_override = copy.deepcopy(child_json_original)
                        child_json_for_override.setdefault("credits", {})["cast"] = final_cast_for_json
                        override_child_path = os.path.join(base_override_dir, filename)
                        try:
                            with open(override_child_path, 'w', encoding='utf-8') as f:
                                json.dump(child_json_for_override, f, ensure_ascii=False, indent=4)
                        except Exception as e:
                            logger.error(f"手动处理：写入子项目JSON失败: {override_child_path}, {e}")

        # 9. 根据配置，同步图片
        if self.sync_images_enabled:
            logger.info(f"手动处理：图片同步已启用，开始下载图片...")
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

        # 10. 触发 Emby 刷新
        logger.info(f"手动处理：准备刷新 Emby 项目 {item_name}...")
        emby_handler.refresh_emby_item_metadata(
            item_id, self.emby_url, self.emby_api_key, 
            replace_all_metadata_param=True, 
            item_name_for_log=item_name
        )
        
        # 11. 更新处理日志
        self.save_to_processed_log(item_id, item_name, score=10.0) # 手动处理给满分
        self._remove_from_failed_log_if_exists(item_id)
        
        logger.info(f"手动处理 Item ID: {item_id} ('{item_name}') 流程完成。")
        return True

    def get_cast_for_editing(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        【恢复循环请求版】获取单个媒体项的详细信息和格式化后的演员列表，确保能获取到头像。
        """
        logger.info(f"为编辑页面获取数据：ItemID {item_id}")
        # 1. 从 Emby 获取媒体详情
        try:
            # ✨ 注意：这里调用 get_emby_item_details 时，不需要它返回 People 的详情了
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
        people_list = emby_details.get("People", [])
        logger.info(f"开始为 {len(people_list)} 位演员获取头像信息...")
        for person in people_list:
            person_id = person.get("Id")
            if not (person_id and person.get("Name")): continue
            
            provider_ids = person.get("ProviderIds", {})
            
            # 调用专门的函数获取 image_tag，这个函数只请求最少的数据
            actor_image_tag = emby_handler.get_person_image_tag(
                person_id, self.emby_url, self.emby_api_key, self.emby_user_id
            )
            
            cast_for_frontend.append({
                "embyPersonId": str(person_id),
                "name": person["Name"],
                "role": person.get("Role", ""),
                "imdbId": provider_ids.get("Imdb"),
                "doubanId": provider_ids.get("Douban"),
                "tmdbId": provider_ids.get("Tmdb"),
                "image_tag": actor_image_tag # 使用获取到的 tag
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
    # --- 前端豆瓣刷新方法 ---
    def get_preview_of_processed_cast(self, item_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        【新】为前端“从豆瓣刷新”功能设计。
        它会完整地执行一次处理流程（但不写入文件或数据库），并返回最终的演员列表。
        """
        logger.info(f"预览处理流程启动：ItemID {item_id}")

        # 1. 获取 Emby 详情
        try:
            item_details = emby_handler.get_emby_item_details(
                item_id, self.emby_url, self.emby_api_key, self.emby_user_id
            )
            if not item_details:
                logger.error(f"预览失败：在Emby中未找到项目 {item_id}")
                return None
        except Exception as e:
            logger.error(f"预览失败：获取Emby详情时异常 for ItemID {item_id}: {e}", exc_info=True)
            return None

        # 2. 获取本地缓存的演员列表 (从 all.json)
        tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
        if not tmdb_id:
            logger.error(f"预览失败：项目 {item_id} 缺少 TMDb ID，无法找到本地缓存。")
            return None
            
        item_type = item_details.get("Type")
        cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
        base_json_filename = "all.json" if item_type == "Movie" else "series.json"
        base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
        base_json_data = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
        
        original_cast_from_local = []
        if base_json_data:
            original_cast_from_local = base_json_data.get("credits", {}).get("cast") or base_json_data.get("casts", {}).get("cast", [])
        else:
             logger.warning(f"预览：未找到本地缓存文件 for {item_id}，将使用空的演员列表作为基准。")

        # 3. 调用核心处理方法，并返回结果
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                processed_cast = self._process_cast_list_from_local(
                    local_cast_list=original_cast_from_local,
                    emby_item_info=item_details,
                    cursor=cursor
                )
            return processed_cast
        except Exception as e:
            logger.error(f"预览失败：在处理演员列表时发生错误 for ID {item_id}: {e}", exc_info=True)
            return None
    
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
        """
        【最终重构版】同步 Emby Person 映射表，并智能合并冗余数据。
        """
        mode_text = "全量同步" if full_sync else "增量同步"
        logger.info(f"开始同步 Emby Person 映射表，模式: {mode_text}")
        if update_status_callback: update_status_callback(0, f"正在从Emby获取所有人物信息... ({mode_text})")

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
        stats = {"processed": 0, "added": 0, "updated": 0, "merged": 0, "errors": 0}

        try:
            cursor = conn.cursor()
            
            for idx, person_base in enumerate(persons_from_emby):
                if self.stop_event.is_set():
                    logger.info(f"同步任务在处理第 {idx+1} 个演员时被用户中止。")
                    break

                stats["processed"] += 1
                if update_status_callback:
                    progress = int(((idx + 1) / total_emby_persons) * 100) if total_emby_persons > 0 else 100
                    update_status_callback(progress, f"正在处理第 {idx+1}/{total_emby_persons} 个新演员...")

                emby_pid = person_base.get("Id")
                if not emby_pid: continue

                # 获取 Person 完整详情
                person_details = emby_handler.get_emby_item_details(emby_pid, self.emby_url, self.emby_api_key, self.emby_user_id)
                if not person_details:
                    stats["errors"] += 1
                    continue

                # ✨✨✨ 1. 打印最原始的 ProviderIds ✨✨✨
                if self.stop_event.is_set():
                    logger.info(f"同步任务在获取演员 {emby_pid} 详情前被用户中止。")
                    break
                provider_ids_raw = person_details.get("ProviderIds", {})
                # 1. 提取当前 Emby Person 的所有 ID
                current_ids = {
                    "emby_person_id": emby_pid,
                    "emby_person_name": person_details.get("Name"),
                    # ✨✨✨ 关键修复：同时检查大写和小写键名 ✨✨✨
                    "tmdb_person_id": provider_ids_raw.get("Tmdb") or provider_ids_raw.get("tmdb"),
                    "imdb_id": provider_ids_raw.get("Imdb") or provider_ids_raw.get("imdb"),
                    "douban_celebrity_id": provider_ids_raw.get("Douban") or provider_ids_raw.get("douban"),
                }
                # 清理空值
                current_ids = {k: v for k, v in current_ids.items() if v}

                # 2. 查找所有相关的数据库记录
                search_conditions = []
                search_values = []
                for key, value in current_ids.items():
                    if key.endswith("_id"): # 只用 ID 字段进行查找
                        search_conditions.append(f"{key} = ?")
                        search_values.append(value)
                
                if not search_values: continue # 如果这个 Emby Person 没有任何 ID，跳过

                sql_find = f"SELECT * FROM person_identity_map WHERE {' OR '.join(search_conditions)}"
                cursor.execute(sql_find, tuple(search_values))
                related_db_rows = cursor.fetchall()

                # 3. 决策与执行
                if not related_db_rows:
                    # --- 情况 A: 新增 ---
                    logger.info(f"  新增记录: {current_ids.get('emby_person_name')} (EmbyID: {emby_pid})")
                    cols = list(current_ids.keys())
                    vals = list(current_ids.values())
                    placeholders = ', '.join(['?'] * len(cols))
                    sql_insert = f"INSERT INTO person_identity_map ({', '.join(cols)}, last_synced_at, last_updated_at) VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    cursor.execute(sql_insert, tuple(vals))
                    stats["added"] += 1
                else:
                    # --- 情况 B/C: 更新或合并 ---
                    # a. 将当前 Emby Person 的信息和所有找到的 DB 记录信息合并
                    merged_record = current_ids.copy()
                    all_map_ids_to_process = []
                    for row in related_db_rows:
                        all_map_ids_to_process.append(row["map_id"])
                        for col in row.keys():
                            # 合并规则：如果 merged_record 里没有这个字段，或者这个字段是空的，就用数据库里的值填充
                            if col not in merged_record or not merged_record.get(col):
                                if row[col] is not None:
                                    merged_record[col] = row[col]
                    
                    # b. 确定主记录 ID 和要删除的冗余记录 ID
                    all_map_ids_to_process.sort() # 排序，选择最小的 ID 作为主记录
                    primary_map_id = all_map_ids_to_process[0]
                    redundant_map_ids = all_map_ids_to_process[1:]

                    # c. 更新主记录
                    update_cols = list(merged_record.keys())
                    if "map_id" in update_cols: update_cols.remove("map_id")
                    
                    set_clauses = [f"{col} = ?" for col in update_cols]
                    update_vals = [merged_record.get(col) for col in update_cols]
                    
                    sql_update = f"UPDATE person_identity_map SET {', '.join(set_clauses)}, last_updated_at = CURRENT_TIMESTAMP WHERE map_id = ?"
                    update_vals.append(primary_map_id)
                    cursor.execute(sql_update, tuple(update_vals))
                    stats["updated"] += 1

                    # d. 如果有冗余记录，删除它们
                    if redundant_map_ids:
                        logger.warning(f"  发现并合并冗余记录 for {current_ids.get('emby_person_name')}. 主记录ID: {primary_map_id}, 将删除冗余ID: {redundant_map_ids}")
                        placeholders = ', '.join(['?'] * len(redundant_map_ids))
                        sql_delete = f"DELETE FROM person_identity_map WHERE map_id IN ({placeholders})"
                        cursor.execute(sql_delete, tuple(redundant_map_ids))
                        stats["merged"] += len(redundant_map_ids)

                # 定期提交事务
                if idx > 0 and idx % 200 == 0:
                    conn.commit()

            # 循环结束后提交所有剩余更改
            conn.commit()

        except Exception as e:
            logger.error(f"同步映射表主循环发生错误: {e}", exc_info=True)
            stats["errors"] += 1
            conn.rollback()
        finally:
            conn.close()

        # 任务结束后的状态报告
        if not self.stop_event.is_set():
            logger.info(f"--- Emby Person 映射表同步完成 ({mode_text}) ---")
            logger.info(f"总计处理: {stats['processed']}, 新增: {stats['added']}, 更新: {stats['updated']}, 合并冗余: {stats['merged']}, 错误: {stats['errors']}")
            if update_status_callback:
                if stats["errors"] > 0:
                    update_status_callback(-1, f"同步完成但有{stats['errors']}个错误。")
                else:
                    update_status_callback(100, f"同步完成。新增 {stats['added']}, 更新 {stats['updated']}, 合并 {stats['merged']} 条冗余记录。")