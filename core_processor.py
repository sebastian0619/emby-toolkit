# core_processor.py

import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
import threading
import time
import requests
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
        self.manual_edit_cache = {}
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

    # ✨ 从 SyncHandler 迁移并改造，用于在本地缓存中查找豆瓣JSON文件
    def _find_local_douban_json(self, imdb_id: Optional[str], douban_id: Optional[str], douban_cache_dir: str) -> Optional[str]:
        """根据 IMDb ID 或 豆瓣 ID 在本地缓存目录中查找对应的豆瓣JSON文件。"""
        if not os.path.exists(douban_cache_dir):
            return None
        
        # 优先使用 IMDb ID 匹配，更准确
        if imdb_id:
            for dirname in os.listdir(douban_cache_dir):
                if dirname.startswith('0_'): continue
                if imdb_id in dirname:
                    dir_path = os.path.join(douban_cache_dir, dirname)
                    for filename in os.listdir(dir_path):
                        if filename.endswith('.json'):
                            return os.path.join(dir_path, filename)
                            
        # 其次使用豆瓣 ID 匹配
        if douban_id:
            for dirname in os.listdir(douban_cache_dir):
                if dirname.startswith(f"{douban_id}_"):
                    dir_path = os.path.join(douban_cache_dir, dirname)
                    for filename in os.listdir(dir_path):
                        if filename.endswith('.json'):
                            return os.path.join(dir_path, filename)
        return None

    # ✨ 封装了“优先本地缓存，失败则在线获取”的逻辑
    def _get_douban_cast_with_local_cache(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        获取豆瓣演员列表。优先从本地 douban-movies/tv 缓存中查找，如果找不到，再通过在线API获取。
        """
        # 1. 准备查找所需的信息
        provider_ids = media_info.get("ProviderIds", {})
        imdb_id = provider_ids.get("Imdb")
        douban_id = provider_ids.get("Douban")
        item_type = media_info.get("Type")
        
        douban_cache_dir_name = "douban-movies" if item_type == "Movie" else "douban-tv"
        douban_cache_path = os.path.join(self.local_data_path, "cache", douban_cache_dir_name)

        # 2. 尝试从本地缓存查找
        local_json_path = self._find_local_douban_json(imdb_id, douban_id, douban_cache_path)

        if local_json_path:
            logger.info(f"发现本地豆瓣缓存文件，将直接使用: {local_json_path}")
            douban_data = _read_local_json(local_json_path)
            # 注意：豆瓣刮削器缓存的键是 'actors'
            if douban_data and 'actors' in douban_data:
                # 为了与API返回的格式兼容，这里做个转换
                # API返回: {'cast': [...]}  本地缓存: {'actors': [...]}
                # _format_douban_cast 需要的是一个列表，所以直接返回列表即可
                return douban_data.get('actors', [])
            else:
                logger.warning(f"本地豆瓣缓存文件 '{local_json_path}' 无效或不含 'actors' 键，将回退到在线API。")
        
        # 3. 如果本地未找到，回退到在线API
        logger.info("未找到本地豆瓣缓存，将通过在线API获取演员信息。")
        return self._fetch_douban_cast(media_info)

    def _format_douban_cast(self, douban_api_actors_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted, seen_ids, seen_names = [], set(), set()
        for item in douban_api_actors_raw:
            # 兼容两种可能的来源：API返回的 'name' 和 本地缓存的 'name'/'latin_name'
            name_zh = str(item.get("name", "")).strip()
            if not name_zh: continue
            
            douban_id = str(item.get("id", "")).strip() or None
            
            # 兼容本地缓存的 'latin_name'
            original_name = str(item.get("original_name", "") or item.get("latin_name", "")).strip()

            if douban_id:
                if douban_id in seen_ids: continue
                seen_ids.add(douban_id)
            else:
                name_sig = f"{name_zh.lower()}|{original_name.lower()}"
                if name_sig in seen_names: continue
                seen_names.add(name_sig)
            
            formatted.append({
                "name": name_zh, "original_name": original_name,
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
            
    # --- 核心处理流程 ---
    def _process_cast_list_from_local(self, local_cast_list: List[Dict[str, Any]], emby_item_info: Dict[str, Any], cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
        """
        【V9 - 集成本地缓存优先】使用本地缓存或在线API获取豆瓣演员，再进行匹配和处理。
        """
        try:
            # ✨ 核心修改：调用新的封装函数，它会优先使用本地缓存
            douban_candidates_raw = self._get_douban_cast_with_local_cache(emby_item_info)
            douban_candidates = self._format_douban_cast(douban_candidates_raw)
            
            final_cast_map = {actor['id']: actor for actor in local_cast_list if actor.get('id')}
            for actor in local_cast_list:
                if not actor.get('id'):
                    name_key = f"name_{actor.get('name', '').lower()}"
                    if name_key not in final_cast_map:
                        final_cast_map[name_key] = actor

            unmatched_douban_candidates = []

            # --- 步骤 1: 用豆瓣演员名和原始演员表匹配 ---
            logger.info("--- 匹配阶段 1: 按名字匹配 ---")
            matched_douban_indices = set()
            for i, d_actor in enumerate(douban_candidates):
                for l_actor in final_cast_map.values():
                    if utils.are_names_match(d_actor.get("name"), d_actor.get("original_name"), l_actor.get("name"), l_actor.get("original_name")):
                        logger.info(f"  匹配成功 (名字): 豆瓣演员 '{d_actor.get('name')}' -> 本地演员 '{l_actor.get('name')}'")
                        l_actor["name"] = d_actor.get("name")
                        l_actor["character"] = self._select_best_role(l_actor.get("character"), d_actor.get("character"))
                        if d_actor.get("douban_id"): l_actor["douban_id"] = d_actor.get("douban_id")
                        matched_douban_indices.add(i)
                        break
            
            unmatched_douban_candidates = [d for i, d in enumerate(douban_candidates) if i not in matched_douban_indices]
            
            # --- 步骤 2: 溢出的演员用豆瓣ID遍历演员映射表匹配 ---
            logger.info(f"--- 匹配阶段 2: 用豆瓣ID查 person_identity_map ({len(unmatched_douban_candidates)} 位演员) ---")
            still_unmatched = []
            for d_actor in unmatched_douban_candidates:
                if self.is_stop_requested():
                    logger.info("任务在处理豆瓣演员时被中止 (循环开始)。")
                    raise InterruptedError("任务中止")
                d_douban_id = d_actor.get("douban_id")
                match_found = False
                if d_douban_id:
                    entry = self._find_person_in_map_by_douban_id(d_douban_id, cursor)
                    
                    if entry and entry["tmdb_person_id"]:
                        tmdb_id_from_map = entry["tmdb_person_id"]
                        
                        if tmdb_id_from_map not in final_cast_map:
                            logger.info(f"  新增成功 (数据库映射): 豆瓣演员 '{d_actor.get('name')}' -> 新增 TMDbID: {tmdb_id_from_map}")
                            
                            new_actor_entry = {
                                "id": tmdb_id_from_map,
                                "name": d_actor.get("name"),
                                "original_name": d_actor.get("original_name"),
                                "character": d_actor.get("character"),
                                "adult": False, "gender": 0, "known_for_department": "Acting",
                                "popularity": 0.0, "profile_path": None, "cast_id": None,
                                "credit_id": None, "order": -1,
                                "imdb_id": entry["imdb_id"],
                                "douban_id": d_douban_id,
                                "_is_newly_added": True
                            }
                            final_cast_map[tmdb_id_from_map] = new_actor_entry
                        
                        match_found = True

                if not match_found:
                    still_unmatched.append(d_actor)
            
            unmatched_douban_candidates = still_unmatched

            # --- 步骤 3 & 4: 查询IMDbID -> TMDb反查 -> 新增 ---
            logger.info(f"--- 匹配阶段 3 & 4: 用IMDb ID进行最终匹配和新增 ({len(unmatched_douban_candidates)} 位演员) ---")
            still_unmatched_final = []
            for d_actor in unmatched_douban_candidates:
                if self.is_stop_requested(): raise InterruptedError("任务中止")
                d_douban_id = d_actor.get("douban_id")
                match_found = False
                if d_douban_id and self.douban_api and self.tmdb_api_key:
                    if self.is_stop_requested():
                        logger.info("任务在处理豆瓣演员时被中止 (豆瓣API调用前)。")
                        raise InterruptedError("任务中止")
                    details = self.douban_api.celebrity_details(d_douban_id)
                    time.sleep(0.3)
                    
                    d_imdb_id = None
                    if details and not details.get("error"):
                        try:
                            info_list = details.get("extra", {}).get("info", [])
                            if isinstance(info_list, list):
                                for item in info_list:
                                    if isinstance(item, list) and len(item) == 2 and item[0] == 'IMDb编号':
                                        d_imdb_id = item[1]
                                        break
                        except Exception as e_parse:
                            logger.warning(f"    -> 解析 IMDb ID 时发生意外错误: {e_parse}")
                    
                    if d_imdb_id:
                        logger.debug(f"    -> 为 '{d_actor.get('name')}' 获取到 IMDb ID: {d_imdb_id}，开始反查...")
                        if self.is_stop_requested():
                            logger.info("任务在处理豆瓣演员时被中止 (TMDb API调用前)。")
                            raise InterruptedError("任务中止")
                        person_from_tmdb = tmdb_handler.find_person_by_external_id(d_imdb_id, self.tmdb_api_key, "imdb_id")
                        if person_from_tmdb and person_from_tmdb.get("id"):
                            tmdb_id_from_find = str(person_from_tmdb.get("id"))
                            logger.info(f"  新增成功 (TMDb反查): 豆瓣演员 '{d_actor.get('name')}' -> 新增 TMDbID: {tmdb_id_from_find}")
                            new_actor_entry = {
                                "id": tmdb_id_from_find, "name": d_actor.get("name"), "original_name": d_actor.get("original_name"),
                                "character": d_actor.get("character"), "adult": False, "gender": 0, "known_for_department": "Acting",
                                "popularity": 0.0, "profile_path": None, "cast_id": None, "credit_id": None, "order": -1,
                                "imdb_id": d_imdb_id, "douban_id": d_douban_id, "_is_newly_added": True
                            }
                            final_cast_map[tmdb_id_from_find] = new_actor_entry
                            match_found = True
                
                if not match_found:
                    still_unmatched_final.append(d_actor)

            if still_unmatched_final:
                discarded_names = [d.get('name') for d in still_unmatched_final]
                logger.info(f"--- 最终丢弃 {len(still_unmatched_final)} 位无匹配的豆瓣演员: {', '.join(discarded_names[:5])}{'...' if len(discarded_names) > 5 else ''} ---")

            # 统一对所有演员进行处理
            final_cast_list = list(final_cast_map.values())
            for actor in final_cast_list:
                if self.is_stop_requested():
                    logger.info("任务在翻译演员列表时被中止。")
                    raise InterruptedError("任务在翻译演员列表时被中止")
                if self.is_stop_requested(): raise InterruptedError("任务在翻译演员列表时被中止")
                
                original_character = actor.get('character')
                cleaned_character = utils.clean_character_name_static(original_character)
                
                translated_character = self._translate_actor_field(cleaned_character, "角色名", actor.get('name'), cursor)
                actor['character'] = translated_character

                actor['name'] = self._translate_actor_field(actor.get('name'), "演员名", actor.get('name'), cursor)

            return final_cast_list
        
        except InterruptedError:
            # 捕获到中止信号，直接重新抛出，让上层处理
            logger.info("在演员列表处理中检测到任务中止信号，将中断当前项目。")
            raise
        except Exception as e:
            # 只捕获其他真正的未知错误
            logger.error(f"在 _process_cast_list_from_local 中发生未知错误: {e}", exc_info=True)
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
        【最终毕业版 V11】以TMDb ID为核心，在处理流程中实时、安全地反哺映射表。
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
        
        try:
            # --- 阶段1: 演员处理、翻译、数据库反哺 (在一个事务中完成) ---
            final_cast_perfect = []
            initial_actor_count = 0
            
            # 路径和数据准备
            cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
            base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
            base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
            image_override_dir = os.path.join(base_override_dir, "images")
            os.makedirs(image_override_dir, exist_ok=True)
            base_json_filename = "all.json" if item_type == "Movie" else "series.json"
            base_json_data_original = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
            if not base_json_data_original:
                raise ValueError(f"无法读取基础JSON文件: {os.path.join(base_cache_dir, base_json_filename)}")
            
            # ✨ 使用 with 语句管理数据库连接，确保所有相关操作在同一个事务中 ✨
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                
                if self.is_stop_requested(): raise InterruptedError("任务被中止")

                original_cast_from_local = base_json_data_original.get("credits", {}).get("cast") or base_json_data_original.get("casts", {}).get("cast", [])
                initial_actor_count = len(original_cast_from_local)
                
                # a. 生成理想的演员列表 (包含匹配和新增)
                intermediate_cast = self._process_cast_list_from_local(original_cast_from_local, item_details_from_emby, cursor)
                
                genres = item_details_from_emby.get("Genres", [])
                is_animation = "Animation" in genres or "动画" in genres
                if is_animation:
                    logger.info(f"检测到媒体 '{item_name_for_log}' 为动画片，将处理配音角色。")

                if self.is_stop_requested(): raise InterruptedError("任务被中止")

                # b. 格式化角色名等
                final_cast_perfect = self._format_and_complete_cast_list(intermediate_cast, is_animation)
                
                # c. ✨✨✨ 在这里进行反哺 ✨✨✨
                logger.info("--- 开始实时更新 person_identity_map 映射表 ---")
                for actor_data in final_cast_perfect:
                    self._update_person_map_entry(cursor, actor_data)
                logger.info("--- person_identity_map 映射表更新完成 ---")

                # with 块结束时，conn 会被自动 commit，所有翻译缓存和映射表更新都会被保存
            
            # --- 阶段2: 文件写入 (不涉及数据库) ---
            base_json_data_for_override = copy.deepcopy(base_json_data_original)
            if item_type == "Movie":
                base_json_data_for_override.setdefault("casts", {})["cast"] = final_cast_perfect
            else:
                base_json_data_for_override.setdefault("credits", {})["cast"] = final_cast_perfect
            
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
                    children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, series_name_for_log=item_name_for_log) or []
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

            # --- 阶段3: 统计、评分和最终日志记录 ---
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
            
            logger.info(f"--- 核心处理结束: '{item_name_for_log}' ---")
            return True

        except InterruptedError:
            logger.info(f"处理 '{item_name_for_log}' 的过程中被用户中止。")
            return False
        except Exception as e:
            logger.error(f"处理 '{item_name_for_log}' 时发生严重错误: {e}", exc_info=True)
            self.save_to_failed_log(item_id, item_name_for_log, f"核心处理异常: {str(e)}", item_type)
            return False
        
    def _update_person_map_entry(self, cursor: sqlite3.Cursor, actor_data: Dict[str, Any]):
        """
        【TMDb为核心版】使用 UPSERT 逻辑，高效地更新或插入 person_identity_map 记录。
        """
        tmdb_id = actor_data.get("id")
        # 只有存在 TMDb ID 时，才进行操作
        if not tmdb_id:
            return

        # 准备要写入的数据
        data_to_write = {
            "tmdb_person_id": tmdb_id,
            "imdb_id": actor_data.get("imdb_id"),
            "douban_celebrity_id": actor_data.get("douban_id"),
            "emby_person_name": actor_data.get("name"), # 使用处理后的中文名
            "tmdb_name": actor_data.get("original_name") or actor_data.get("name"),
            "douban_name": actor_data.get("name"),
        }
        # 清理掉值为 None 的键
        clean_data = {k: v for k, v in data_to_write.items() if v is not None}
        
        cols = list(clean_data.keys())
        vals = list(clean_data.values())
        
        # 构建 UPSERT 语句，当 tmdb_person_id 冲突时，执行更新
        update_clauses = [f"{col} = COALESCE(excluded.{col}, person_identity_map.{col})" for col in cols if col != "tmdb_person_id"]
        
        sql = f"""
            INSERT INTO person_identity_map ({', '.join(cols)}, last_updated_at)
            VALUES ({', '.join(['?'] * len(cols))}, CURRENT_TIMESTAMP)
            ON CONFLICT(tmdb_person_id) DO UPDATE SET
                {', '.join(update_clauses)},
                last_updated_at = CURRENT_TIMESTAMP;
        """
        
        try:
            cursor.execute(sql, tuple(vals))
            logger.debug(f"  -> 成功 UPSERT 映射表 for TMDb ID: {tmdb_id}")
        except sqlite3.Error as e:
            logger.error(f"  -> 更新映射表失败 for TMDb ID {tmdb_id}: {e}")

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
    # --- 一键翻译 ---
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
    # --- 手动处理 ---
    def process_item_with_manual_cast(self, item_id: str, manual_cast_list: List[Dict[str, Any]], item_name: str) -> bool:
        """
        【V4 - 后端缓存最终版】使用前端提交的轻量级修改，与内存中的完整数据合并。
        """
        logger.info(f"手动处理流程启动 (后端缓存模式)：ItemID: {item_id} ('{item_name}')")
        
        try:
            # ★★★ 1. 从内存缓存中获取这个会话的完整原始演员列表 ★★★
            original_full_cast = self.manual_edit_cache.get(item_id)
            if not original_full_cast:
                raise ValueError(f"在内存缓存中找不到 ItemID {item_id} 的原始演员数据。请重新进入编辑页面。")

            # 2. 获取基础信息
            item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details: raise ValueError(f"无法获取项目 {item_id} 的详情。")
            tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
            item_type = item_details.get("Type")
            if not tmdb_id: raise ValueError(f"项目 {item_id} 缺少 TMDb ID。")

            # 3. 构建一个以 TMDb ID 为键的原始数据映射表，方便查找
            reliable_cast_map = {str(actor['id']): actor for actor in original_full_cast if actor.get('id')}

            # 4. 遍历前端传来的轻量级列表，安全地合并修改
            final_cast_for_json = []
            for actor_from_frontend in manual_cast_list:
                frontend_tmdb_id = actor_from_frontend.get("tmdbId")
                if not frontend_tmdb_id: continue

                # 从我们的“真理之源”中找到对应的完整原始数据
                original_actor_data = reliable_cast_map.get(str(frontend_tmdb_id))
                if not original_actor_data:
                    logger.warning(f"在原始缓存中找不到 TMDb ID {frontend_tmdb_id}，跳过此演员。")
                    continue
                
                # 创建一个副本，并只更新 name 和 role
                updated_actor_data = copy.deepcopy(original_actor_data)
                updated_actor_data['name'] = actor_from_frontend.get('name')
                updated_actor_data['character'] = actor_from_frontend.get('role')
                
                final_cast_for_json.append(updated_actor_data)
            
            # 为最终列表设置正确的顺序
            for idx, actor in enumerate(final_cast_for_json):
                actor['order'] = idx

            # ★★★ 5. 后续的文件写入和刷新流程，现在使用的是100%完整的演员数据 ★★★
            # ... (这部分逻辑与你之前的版本完全相同，我将它补全) ...
            
            cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
            base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
            base_json_filename = "all.json" if item_type == "Movie" else "series.json"
            base_json_data_original = _read_local_json(os.path.join(base_cache_dir, base_json_filename)) or {}
            base_json_data_for_override = copy.deepcopy(base_json_data_original)

            if item_type == "Movie":
                base_json_data_for_override.setdefault("casts", {})["cast"] = final_cast_for_json
            else:
                base_json_data_for_override.setdefault("credits", {})["cast"] = final_cast_for_json
            
            base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
            image_override_dir = os.path.join(base_override_dir, "images")
            os.makedirs(image_override_dir, exist_ok=True)
            override_json_path = os.path.join(base_override_dir, base_json_filename)
            
            temp_json_path = f"{override_json_path}.{random.randint(1000, 9999)}.tmp"
            with open(temp_json_path, 'w', encoding='utf-8') as f:
                json.dump(base_json_data_for_override, f, ensure_ascii=False, indent=4)
            os.replace(temp_json_path, override_json_path)
            logger.info(f"手动处理：成功生成覆盖元数据文件: {override_json_path}")

            #---深度处理剧集
            process_episodes_config = self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, True)
            if item_type == "Series" and process_episodes_config:
                logger.info(f"手动处理：深度处理已启用，将为所有子项目注入手动编辑后的演员表...")
                # base_cache_dir 变量在函数前面已经定义好了，可以直接使用
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

            #---同步图片
            if self.sync_images_enabled:
                if self.is_stop_requested(): raise InterruptedError("任务中止")
                logger.info(f"手动处理：图片同步已启用，开始下载图片...")
                # image_override_dir 变量在函数前面已经定义好了，可以直接使用
                image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
                if item_type == "Movie": image_map["Thumb"] = "landscape.jpg"
                for image_type, filename in image_map.items():
                    emby_handler.download_emby_image(item_id, image_type, os.path.join(image_override_dir, filename), self.emby_url, self.emby_api_key)
                
                if item_type == "Series" and process_episodes_config:
                    # item_name 变量是从函数参数中传进来的，可以直接使用
                    children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, series_name_for_log=item_name) or []
                    for child in children:
                        if self.is_stop_requested(): break
                        child_type, child_id = child.get("Type"), child.get("Id")
                        if child_type == "Season":
                            season_number = child.get("IndexNumber")
                            if season_number is not None:
                                emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}.jpg"), self.emby_url, self.emby_api_key)
                        elif child_type == "Episode":
                            season_number, episode_number = child.get("ParentIndexNumber"), child.get("IndexNumber")
                            if season_number is not None and episode_number is not None:
                                emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}-episode-{episode_number}.jpg"), self.emby_url, self.emby_api_key)

            logger.info(f"手动处理：准备刷新 Emby 项目 {item_name}...")
            refresh_success = emby_handler.refresh_emby_item_metadata(
                item_emby_id=item_id,
                emby_server_url=self.emby_url,
                emby_api_key=self.emby_api_key,
                replace_all_metadata_param=True,
                item_name_for_log=item_name
            )
            if not refresh_success:
                # 即使刷新失败，文件也已经生成了，所以我们不让整个任务失败
                # 但可以记录一个警告
                logger.warning(f"手动处理：文件已生成，但触发 Emby 刷新失败。你可能需要稍后在 Emby 中手动刷新。")

            # 更新处理日志
            self.save_to_processed_log(item_id, item_name, score=10.0)
            self._remove_from_failed_log_if_exists(item_id)
            
            logger.info(f"✅ 手动处理 '{item_name}' 流程完成。")
            return True
        
        except Exception as e:
            logger.error(f"手动处理 '{item_name}' 时发生严重错误: {e}", exc_info=True)
            self.save_to_failed_log(item_id, item_name, f"手动处理异常: {str(e)}")
            return False
        finally:
            # ★★★ 清理本次编辑会话的缓存 ★★★
            if item_id in self.manual_edit_cache:
                del self.manual_edit_cache[item_id]
                logger.debug(f"已清理 ItemID {item_id} 的内存缓存。")
    # --- 从本地 cache 文件获取演员列表用于编辑 ---
    def get_cast_for_editing(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        【V4 - 后端缓存最终版】为手动编辑准备数据。
        1. 从本地 cache 加载完整的演员列表。
        2. 将完整列表缓存在内存中。
        3. 只向前端发送轻量级数据 (ID, name, role, profile_path)。
        """
        logger.info(f"为编辑页面准备数据 (后端缓存模式)：ItemID {item_id}")
        
        try:
            # 1. 获取基础信息
            emby_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not emby_details: raise ValueError(f"在Emby中未找到项目 {item_id}")

            tmdb_id = emby_details.get("ProviderIds", {}).get("Tmdb")
            item_type = emby_details.get("Type")
            if not tmdb_id: raise ValueError(f"项目 {item_id} 缺少 TMDb ID")

            # 2. 从本地 cache 文件读取最可靠的演员列表
            cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
            base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
            base_json_filename = "all.json" if item_type == "Movie" else "series.json"
            tmdb_data = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
            if not tmdb_data: raise ValueError("未找到本地 TMDb 缓存文件")
            
            full_cast_from_cache = tmdb_data.get("credits", {}).get("cast", []) or tmdb_data.get("casts", {}).get("cast", [])

            # ★★★ 3. 将完整的演员列表存入内存缓存 ★★★
            # 使用 item_id 作为键，确保每个编辑会话都是独立的
            self.manual_edit_cache[item_id] = full_cast_from_cache
            logger.debug(f"已为 ItemID {item_id} 缓存了 {len(full_cast_from_cache)} 条完整演员数据。")

            # 4. 构建并发送“轻量级”数据给前端
            cast_for_frontend = []
            for actor_data in full_cast_from_cache:
                actor_tmdb_id = actor_data.get('id')
                if not actor_tmdb_id: continue
                
                # 直接拼接 TMDb 头像链接，使用最小尺寸 w185
                profile_path = actor_data.get('profile_path')
                image_url = f"https://image.tmdb.org/t/p/w185{profile_path}" if profile_path else None

                cast_for_frontend.append({
                    "tmdbId": actor_tmdb_id,
                    "name": actor_data.get('name'),
                    "role": actor_data.get('character'),
                    "imageUrl": image_url, # 发送拼接好的完整 URL
                })
            
            # ... (从 failed_log 获取信息和组合 response_data 的逻辑保持不变) ...
            failed_log_info = {}
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT error_message, score FROM failed_log WHERE item_id = ?", (item_id,))
                row = cursor.fetchone()
                if row: failed_log_info = dict(row)

            response_data = {
                "item_id": item_id,
                "item_name": emby_details.get("Name"),
                "item_type": item_type,
                "image_tag": emby_details.get('ImageTags', {}).get('Primary'),
                "original_score": failed_log_info.get("score"),
                "review_reason": failed_log_info.get("error_message"),
                "current_emby_cast": cast_for_frontend,
                "search_links": {
                    "google_search_wiki": utils.generate_search_url('wikipedia', emby_details.get("Name"), emby_details.get("ProductionYear"))
                }
            }
            return response_data

        except Exception as e:
            logger.error(f"获取编辑数据失败 for ItemID {item_id}: {e}", exc_info=True)
            return None

    
    def close(self):
        if self.douban_api: self.douban_api.close()
        logger.debug("MediaProcessor closed.")



class SyncHandler:
    def __init__(self, db_path: str, emby_url: str, emby_api_key: str, emby_user_id: Optional[str], stop_event: threading.Event, tmdb_api_key: str, local_data_path: str):
        self.db_path = db_path
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        self.stop_event = stop_event
        self.tmdb_api_key = tmdb_api_key
        self.local_data_path = local_data_path
        logger.info(f"SyncHandler initialized for local cache sync.")

    def _get_db_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _find_douban_json(self, tmdb_data: Dict[str, Any], douban_cache_dir: str) -> Optional[str]:
        if not os.path.exists(douban_cache_dir): return None
        imdb_id = tmdb_data.get('imdb_id')
        if imdb_id:
            for douban_dir_name in os.listdir(douban_cache_dir):
                if douban_dir_name.startswith('0_'): continue
                if imdb_id in douban_dir_name:
                    douban_dir_path = os.path.join(douban_cache_dir, douban_dir_name)
                    for filename in os.listdir(douban_dir_path):
                        if filename.endswith('.json'): return os.path.join(douban_dir_path, filename)
        douban_id_from_tmdb = tmdb_data.get("ProviderIds", {}).get("Douban")
        if douban_id_from_tmdb:
            for douban_dir_name in os.listdir(douban_cache_dir):
                if douban_dir_name.startswith(f"{douban_id_from_tmdb}_"):
                    douban_dir_path = os.path.join(douban_cache_dir, douban_dir_name)
                    for filename in os.listdir(douban_dir_path):
                        if filename.endswith('.json'): return os.path.join(douban_dir_path, filename)
        return None

    def _upsert_person_map(self, cursor: sqlite3.Cursor, person_data: Dict[str, Any]) -> str:
        """
        【V4 用户友好日志版】使用 UPSERT 逻辑，并能优雅处理和清晰报告 UNIQUE 字段的冲突。
        """
        tmdb_id = person_data.get("tmdb_person_id")
        if not tmdb_id: return 'skipped'

        cursor.execute("SELECT tmdb_person_id FROM person_identity_map WHERE tmdb_person_id = ?", (tmdb_id,))
        action_type = 'updated' if cursor.fetchone() else 'added'

        def attempt_upsert(data_to_write: Dict[str, Any], is_degraded: bool = False) -> bool:
            clean_data = {k: v for k, v in data_to_write.items() if v is not None and str(v).strip() != ''}
            if len(clean_data) <= 1 and "tmdb_person_id" in clean_data: return True

            cols = list(clean_data.keys())
            vals = list(clean_data.values())
            update_clauses = [f"{col} = COALESCE(excluded.{col}, person_identity_map.{col})" for col in cols if col != "tmdb_person_id"]
            
            if not update_clauses: return True

            sql = f"INSERT INTO person_identity_map ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))}) ON CONFLICT(tmdb_person_id) DO UPDATE SET {', '.join(update_clauses)}, last_updated_at = CURRENT_TIMESTAMP;"
            
            try:
                cursor.execute(sql, tuple(vals))
                if is_degraded:
                    logger.info(f"  -> [降级写入成功] for TMDb ID {tmdb_id} (已更新非唯一信息)")
                else:
                    # 为了避免刷屏，常规成功的日志保持 DEBUG 级别
                    logger.debug(f"  -> [UPSERT成功] for TMDb ID {tmdb_id}")
                return True
            except sqlite3.IntegrityError as e:
                # ★★★ 核心修改：在这里进行详细的冲突分析和日志记录 ★★★
                conflicting_douban_id = data_to_write.get("douban_celebrity_id")
                if conflicting_douban_id and "douban_celebrity_id" in str(e):
                    # 确定是豆瓣 ID 冲突，反查数据库找出占用者
                    cursor.execute("SELECT tmdb_person_id, tmdb_name, douban_name FROM person_identity_map WHERE douban_celebrity_id = ?", (conflicting_douban_id,))
                    occupant = cursor.fetchone()
                    if occupant:
                        logger.warning(
                            f"  -> [数据冲突] 演员 '{data_to_write.get('tmdb_name', tmdb_id)}' (TMDb ID: {tmdb_id}) "
                            f"尝试关联的豆瓣ID '{conflicting_douban_id}' 已被 "
                            f"'{occupant['douban_name'] or occupant['tmdb_name']}' (TMDb ID: {occupant['tmdb_person_id']}) 占用。"
                        )
                    else:
                        # 理论上不应该发生，但以防万一
                        logger.warning(f"  -> [数据冲突] 演员 (TMDb ID: {tmdb_id}) 尝试关联的豆瓣ID '{conflicting_douban_id}' 已被占用，但无法找到占用者。")
                else:
                    # 其他类型的 UNIQUE 冲突
                    logger.warning(f"  -> [数据冲突] UPSERT for TMDb ID {tmdb_id} 遇到 UNIQUE 约束失败: {e}.")
                
                logger.info("    -> [应对策略] 将放弃本次关联，只尝试更新该演员的名字等非冲突信息。")
                return False
            except sqlite3.Error as e:
                logger.error(f"  -> UPSERT 映射表失败 for TMDb ID {tmdb_id}: {e}")
                return False

        # 第一次尝试：is_degraded 默认为 False
        if attempt_upsert(person_data):
            return action_type
        else:
            # 第二次尝试：降级写入
            degraded_data = person_data.copy()
            degraded_data.pop("douban_celebrity_id", None)
            degraded_data.pop("imdb_id", None)
            
            # ★★★ 2. 在调用时，明确传递 is_degraded=True ★★★
            if attempt_upsert(degraded_data, is_degraded=True):
                return 'updated'
            else:
                logger.error(f"  -> 降级写入 for TMDb ID {tmdb_id} 仍然失败。")
                return 'error'

    def sync_emby_person_map_to_db(self, full_sync: bool = False, update_status_callback: Optional[callable] = None):
        mode_text = "深度补充模式 (耗时)" if full_sync else "快速本地模式"
        logger.info(f"--- 开始演员映射表同步任务 ({mode_text}) ---")
        if update_status_callback: update_status_callback(0, f"准备中... ({mode_text})")

        stats = {"tmdb_items_scanned": 0, "douban_items_found": 0, "actors_processed": 0, "map_added": 0, "map_updated": 0, "online_lookups": 0, "online_success": 0, "imdb_added": 0}

        with self._get_db_conn() as conn:
            cursor = conn.cursor()
            douban_api = DoubanApi(db_path=self.db_path)

            # --- 阶段一：快速本地同步 (所有模式下都会执行) ---
            logger.info("--- [阶段 1] 开始执行快速本地文件扫描 ---")
            tmdb_movies_path = os.path.join(self.local_data_path, "cache", "tmdb-movies2")
            tmdb_tv_path = os.path.join(self.local_data_path, "cache", "tmdb-tv")
            douban_movies_path = os.path.join(self.local_data_path, "cache", "douban-movies")
            douban_tv_path = os.path.join(self.local_data_path, "cache", "douban-tv")

            if os.path.exists(tmdb_movies_path) and os.path.exists(tmdb_tv_path):
                tmdb_dirs = [os.path.join(tmdb_movies_path, d) for d in os.listdir(tmdb_movies_path) if os.path.isdir(os.path.join(tmdb_movies_path, d))]
                tmdb_dirs += [os.path.join(tmdb_tv_path, d) for d in os.listdir(tmdb_tv_path) if os.path.isdir(os.path.join(tmdb_tv_path, d))]
                total_items = len(tmdb_dirs)

                for i, tmdb_item_dir in enumerate(tmdb_dirs):
                    if self.stop_event.is_set(): break
                    stats["tmdb_items_scanned"] += 1
                    progress = int(((i + 1) / total_items) * 100) if total_items > 0 else 100
                    if update_status_callback: update_status_callback(progress, f"快速扫描 ({i+1}/{total_items}): {os.path.basename(tmdb_item_dir)}")
                    
                    # ... (此处是完整的本地文件处理和名字匹配逻辑，与你之前的代码完全相同) ...
                    json_filename = "all.json" if "tmdb-movies2" in tmdb_item_dir else "series.json"
                    tmdb_json_path = os.path.join(tmdb_item_dir, json_filename)
                    tmdb_data = _read_local_json(tmdb_json_path)
                    if not tmdb_data: continue
                    douban_cache_dir = douban_movies_path if "tmdb-movies2" in tmdb_item_dir else douban_tv_path
                    douban_json_path = self._find_douban_json(tmdb_data, douban_cache_dir)
                    douban_data = _read_local_json(douban_json_path) if douban_json_path else None
                    douban_cast = douban_data.get('actors', []) if douban_data else []
                    if douban_json_path: stats["douban_items_found"] += 1
                    tmdb_cast = tmdb_data.get('casts', {}).get('cast', tmdb_data.get('credits', {}).get('cast', []))
                    for tmdb_actor in tmdb_cast:
                        if self.stop_event.is_set(): break
                        stats["actors_processed"] += 1
                        tmdb_person_id = tmdb_actor.get('id')
                        if not tmdb_person_id: continue
                        person_data = {"tmdb_person_id": str(tmdb_person_id), "tmdb_name": tmdb_actor.get('name') or tmdb_actor.get('original_name')}
                        matched_douban_actor = next((da for da in douban_cast if utils.are_names_match(tmdb_actor.get('name'), tmdb_actor.get('original_name'), da.get('name'), da.get('latin_name'))), None)
                        if matched_douban_actor:
                            douban_id = matched_douban_actor.get('id')
                            if douban_id:
                                person_data["douban_celebrity_id"] = str(douban_id)
                                person_data["douban_name"] = matched_douban_actor.get('name')
                        action = self._upsert_person_map(cursor, person_data)
                        if action == 'added': stats['map_added'] += 1
                        elif action == 'updated': stats['map_updated'] += 1

            # ★★★ 阶段二：深度在线补充 (仅在 full_sync 模式下执行) ★★★
            if full_sync and not self.stop_event.is_set():
                logger.info("--- [阶段 2] 开始执行深度在线补充 (耗时) ---")
                
                # 1. 补充豆瓣独有演员
                # (这个逻辑比较复杂，我们先简化，直接补充 IMDb ID)

                # 2. 补充 IMDb ID
                cursor.execute("SELECT tmdb_person_id, tmdb_name FROM person_identity_map WHERE imdb_id IS NULL OR imdb_id = ''")
                records_to_enrich = cursor.fetchall()
                total_to_enrich = len(records_to_enrich)
                
                if total_to_enrich > 0:
                    logger.info(f"发现 {total_to_enrich} 条记录需要在线补充 IMDb ID。")
                    for i, record in enumerate(records_to_enrich):
                        if self.stop_event.is_set(): break
                        progress = int(((i + 1) / total_to_enrich) * 100) if total_to_enrich > 0 else 100
                        if update_status_callback: update_status_callback(progress, f"深度补充 ({i+1}/{total_to_enrich}): {record['tmdb_name']}")
                        
                        details = tmdb_handler.get_person_details_from_tmdb(record["tmdb_person_id"], self.tmdb_api_key)
                        time.sleep(0.2)
                        if details and details.get("imdb_id"):
                            stats["imdb_added"] += 1
                            cursor.execute("UPDATE person_identity_map SET imdb_id = ? WHERE tmdb_person_id = ?", (details.get("imdb_id"), record["tmdb_person_id"]))
            
            conn.commit()
            douban_api.close()

        # ... (最终的详细统计日志报告) ...
        final_message = "演员映射表同步完成。"
        if self.stop_event.is_set(): final_message = "同步任务已中断。"
        
        # ★★★ 增加醒目的边框 ★★★
        logger.info("=" * 60)
        logger.info(f"--- {final_message} 统计报告 ({mode_text}) ---")
        logger.info(f"  - 扫描的 TMDb 项目数: {stats['tmdb_items_scanned']}")
        logger.info(f"  - 成功关联的豆瓣项目数: {stats['douban_items_found']}")
        logger.info(f"  - 处理的总演员人次: {stats['actors_processed']}")
        logger.info(f"  - 新增的映射记录: {stats['map_added']}")
        logger.info(f"  - 更新的映射记录: {stats['map_updated']}")
        if full_sync:
            logger.info(f"  - 在线补充的 IMDb ID 数量: {stats['imdb_added']}")
        #logger.info(f"  - 尝试在线查找的豆瓣独有演员: {stats['online_lookups']}")
        #logger.info(f"  - 在线查找成功并建立关联: {stats['online_success']}")
        logger.info("=" * 60)
        
        if update_status_callback:
            summary = f"完成！新增 {stats['map_added']}, 更新 {stats['map_updated']}。"
            if full_sync:
                summary += f" IMDb补充 {stats['imdb_added']}。"
            update_status_callback(100, summary)