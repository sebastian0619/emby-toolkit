# core_processor.py
import time
import re
import os
import sqlite3 # 用于数据库操作
from typing import Dict, List, Optional, Any
import threading
import local_data_handler
import tmdb_handler
from douban import DoubanApi, clean_character_name_static
# 假设 emby_handler.py, utils.py, logger_setup.py, constants.py 都在同一级别或Python路径中
import emby_handler
import utils # 导入我们上面修改的 utils.py
from logger_setup import logger
import constants

# DoubanApi 的导入和可用性检查
try:
    from douban import DoubanApi # douban.py 现在也使用数据库
    DOUBAN_API_AVAILABLE = True
    logger.info("DoubanApi 模块已成功导入到 core_processor。")
except ImportError:
    logger.error("错误: douban.py 文件未找到或 DoubanApi 类无法导入 (core_processor)。")
    DOUBAN_API_AVAILABLE = False
    # 创建一个假的 DoubanApi 类，以便在 DoubanApi 不可用时程序仍能运行（但功能受限）
    class DoubanApi:
        def __init__(self, *args, **kwargs): logger.warning("使用的是假的 DoubanApi 实例 (core_processor)。")
        def get_acting(self, *args, **kwargs): return {"error": "DoubanApi not available", "cast": []}
        def close(self): pass
        # 添加静态方法以避免 AttributeError，如果 _translate_actor_field 尝试调用它们
        @staticmethod
        def _get_translation_from_db(*args, **kwargs): return None
        @staticmethod
        def _save_translation_to_db(*args, **kwargs): pass


class MediaProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            logger.error("MediaProcessor 初始化失败：未在配置中找到 'db_path'。")
            raise ValueError("数据库路径 (db_path) 未在配置中提供给 MediaProcessor。")

        self.douban_api = None
        # 使用 hasattr 检查常量是否存在，避免 AttributeError
        douban_api_available_flag = getattr(constants, 'DOUBAN_API_AVAILABLE', False)
        if douban_api_available_flag:
            try:
                self.douban_api = DoubanApi(db_path=self.db_path)
                logger.info("DoubanApi 实例已在 MediaProcessor 中创建 (使用数据库缓存)。")
            except Exception as e:
                logger.error(f"MediaProcessor 初始化 DoubanApi 失败: {e}", exc_info=True)
                self.douban_api = DoubanApi(db_path=self.db_path) # 假实例
        else:
            logger.warning("DoubanApi 常量指示不可用或未定义，将使用假的 DoubanApi 实例。")
            self.douban_api = DoubanApi(db_path=self.db_path) # 假实例

        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.tmdb_api_key = self.config.get("tmdb_api_key", "") # <--- 确保加载
        self.translator_engines = self.config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        self.data_source_mode = config.get("data_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)
        self.local_data_path = config.get("local_data_path", constants.DEFAULT_LOCAL_DATA_PATH).strip()
        self.libraries_to_process = config.get("libraries_to_process", [])

        self._stop_event = threading.Event() # 确保 threading 已导入
        self.processed_items_cache = self._load_processed_log_from_db()

        logger.info(f"MediaProcessor 初始化完成。Emby URL: {self.emby_url}, UserID: {self.emby_user_id}")
        logger.info(f"  TMDb API Key: {'已配置' if self.tmdb_api_key else '未配置'}")
        logger.info(f"  数据源处理模式: {self.data_source_mode}")
        logger.info(f"  本地数据源路径: '{self.local_data_path if self.local_data_path else '未配置'}'")
        logger.info(f"  将处理的媒体库ID: {self.libraries_to_process if self.libraries_to_process else '未指定特定库'}")
        logger.info(f"  已从数据库加载 {len(self.processed_items_cache)} 个已处理媒体记录到内存缓存。")
        logger.debug(f"  INIT - self.local_data_path: '{self.local_data_path}'")
        logger.debug(f"  INIT - self.data_source_mode: '{self.data_source_mode}'")
        logger.debug(f"  INIT - self.tmdb_api_key (len): {len(self.tmdb_api_key) if self.tmdb_api_key else 0}")
        logger.debug(f"  INIT - DOUBAN_API_AVAILABLE (from top level): {DOUBAN_API_AVAILABLE}") # 打印顶层导入状态
        logger.debug(f"  INIT - self.douban_api is None: {self.douban_api is None}")
        if self.douban_api:
            logger.debug(f"  INIT - self.douban_api type: {type(self.douban_api)}")


    def _get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接的辅助方法"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row # 方便按列名访问
        return conn

    def signal_stop(self):
        logger.info("MediaProcessor 收到停止信号。")
        self._stop_event.set()

    def clear_stop_signal(self):
        self._stop_event.clear()
        logger.debug("MediaProcessor 停止信号已清除。")

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _load_processed_log_from_db(self) -> set:
        log_set = set()
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT item_id FROM processed_log")
            rows = cursor.fetchall()
            for row in rows:
                log_set.add(row['item_id'])
            conn.close()
        except Exception as e:
            logger.error(f"从数据库读取已处理记录失败: {e}", exc_info=True)
        return log_set

    def save_to_processed_log(self, item_id: str, item_name: Optional[str] = None):
        """将成功处理的媒体项ID和名称保存到SQLite数据库和内存缓存中。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO processed_log (item_id, item_name, processed_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (item_id, item_name if item_name else f"未知项目(ID:{item_id})") # 提供默认名
            )
            conn.commit()
            conn.close()
            if item_id not in self.processed_items_cache:
                self.processed_items_cache.add(item_id)
                logger.info(f"Item ID '{item_id}' ('{item_name}') 已添加到已处理记录 (数据库和内存)。")
            else:
                logger.debug(f"Item ID '{item_id}' ('{item_name}') 已更新/确认在已处理记录 (数据库)。")
        except Exception as e:
            logger.error(f"保存已处理记录到数据库失败 (Item ID: {item_id}): {e}", exc_info=True)

    def clear_processed_log(self):
        """清除数据库中的已处理记录和内存缓存。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM processed_log")
            conn.commit()
            conn.close()
            self.processed_items_cache.clear()
            logger.info("数据库和内存中的已处理记录已清除。")
        except Exception as e:
            logger.error(f"清除数据库已处理记录失败: {e}", exc_info=True)

    def save_to_failed_log(self, item_id: str, item_name: Optional[str], error_msg: str, item_type: Optional[str] = None):
        """将处理失败的媒体项信息保存到SQLite数据库。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO failed_log (item_id, item_name, failed_at, error_message, item_type) VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)",
                (item_id, item_name if item_name else f"未知项目(ID:{item_id})", error_msg, item_type if item_type else "未知类型")
            )
            conn.commit()
            conn.close()
            logger.info(f"Item ID '{item_id}' ('{item_name}') 已作为失败项记录到数据库。原因: {error_msg}")
        except Exception as e:
            logger.error(f"保存失败记录到数据库失败 (Item ID: {item_id}): {e}", exc_info=True)

    def _translate_actor_field(self, text: Optional[str], field_name: str, actor_name_for_log: str) -> Optional[str]:
        if self.is_stop_requested():
            logger.debug(f"翻译字段 '{field_name}' 前检测到停止信号，跳过。")
            return text

        if not text or not text.strip() or utils.contains_chinese(text):
            # logger.debug(f"字段 '{field_name}' 为空、全空白或已包含中文 ('{text}')，跳过翻译。")
            return text

        text_stripped = text.strip()
        # 跳过单字母或双大写字母的逻辑
        if len(text_stripped) == 1 and 'A' <= text_stripped.upper() <= 'Z':
            logger.debug(f"字段 '{field_name}' ({text_stripped}) 为单字母，跳过翻译 (演员: {actor_name_for_log})。")
            return text
        if len(text_stripped) == 2 and text_stripped.isupper() and text_stripped.isalpha():
            logger.debug(f"字段 '{field_name}' ({text_stripped}) 为双大写字母，跳过翻译 (演员: {actor_name_for_log})。")
            return text

        # 从数据库读取翻译缓存
        # DoubanApi 类现在不直接处理缓存读写，而是由 MediaProcessor 通过其 db_path 操作
        # 或者，如果 DoubanApi 内部的 _get_translation_from_db 是静态方法且能访问 db_path，也可以
        # 为保持 DoubanApi 的封装性，翻译缓存的读写最好由 DoubanApi 自身处理，MediaProcessor 只调用
        # 因此，我们依赖 DoubanApi 实例的方法（如果它有）或其类方法（如果设计如此）
        cached_entry = None
        if self.douban_api and hasattr(DoubanApi, '_get_translation_from_db'): # 检查方法是否存在
             cached_entry = DoubanApi._get_translation_from_db(text_stripped) # 调用 DoubanApi 的类方法

        if cached_entry:
            cached_translation = cached_entry.get("translated_text")
            engine_used = cached_entry.get("engine_used")
            if cached_translation and cached_translation.strip():
                logger.info(f"数据库翻译缓存命中 for '{text_stripped}' -> '{cached_translation}' (引擎: {engine_used}, 演员: {actor_name_for_log}, 字段: {field_name})")
                return cached_translation
            else: # 缓存中存的是 None 或空字符串，表示之前翻译失败或无结果
                logger.info(f"数据库翻译缓存命中 (空值/之前失败) for '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name})，将使用原文。")
                return text # 或者可以考虑是否重新尝试翻译，取决于策略

        logger.info(f"准备在线翻译字段 '{field_name}': '{text_stripped}' (演员: {actor_name_for_log})")
        translation_result = utils.translate_text_with_translators(
            text_stripped,
            engine_order=self.translator_engines
        ) # utils.translate_text_with_translators 现在返回 {"text": ..., "engine": ...}

        translated_text = None
        used_engine = "unknown" # 默认未知引擎
        if translation_result and isinstance(translation_result, dict):
            translated_text = translation_result.get("text")
            used_engine = translation_result.get("engine", "unknown")

        if translated_text and translated_text.strip():
            final_translation = translated_text.strip()
            logger.info(f"在线翻译成功 ({used_engine}): '{text_stripped}' -> '{final_translation}' (演员: {actor_name_for_log}, 字段: {field_name})")
            # 保存翻译到数据库
            if self.douban_api and hasattr(DoubanApi, '_save_translation_to_db'):
                DoubanApi._save_translation_to_db(text_stripped, final_translation, used_engine)
            # 更新调试日志
            return final_translation
        else:
            logger.warning(f"在线翻译失败或返回空: '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name})")
            # 保存翻译失败状态 (None) 到数据库，避免重复无效尝试
            if self.douban_api and hasattr(DoubanApi, '_save_translation_to_db'):
                DoubanApi._save_translation_to_db(text_stripped, None, f"failed_or_empty_via_{used_engine}")
            return text # 返回原文


    def _process_cast_list(self, current_emby_cast_people: List[Dict[str, Any]], media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        media_name_for_log = media_info.get("Name", "未知媒体")
        logger.info(f"_process_cast_list: 开始处理媒体 '{media_name_for_log}' (Emby原始演员: {len(current_emby_cast_people)}位)")

        # --- 步骤 0: 准备 Emby 原始演员数据和标识符 ---
        processed_cast: List[Dict[str, Any]] = [] 
        emby_original_douban_ids = set()
        emby_original_tmdb_ids = set()
        emby_original_imdb_ids = set()
        emby_original_names_lower = set()

        for person_emby_raw in current_emby_cast_people:
            if self.is_stop_requested(): break
            
            emby_name = person_emby_raw.get("Name")
            emby_role = person_emby_raw.get("Role")
            emby_person_id = str(person_emby_raw.get("Id")).strip() if person_emby_raw.get("Id") else None
            
            provider_ids_from_emby = person_emby_raw.get("ProviderIds", {})
            tmdb_id_from_emby_person = str(provider_ids_from_emby.get("Tmdb")).strip() if provider_ids_from_emby.get("Tmdb") else None
            douban_id_from_emby_person = str(provider_ids_from_emby.get("Douban")).strip() if provider_ids_from_emby.get("Douban") else None
            imdb_id_from_emby_person = str(provider_ids_from_emby.get("Imdb")).strip() if provider_ids_from_emby.get("Imdb") else None
            
            original_name_from_emby = person_emby_raw.get("OriginalName", emby_name) 

            emby_actor_internal_format = {
                "Name": emby_name, "OriginalName": original_name_from_emby, "Role": emby_role,
                "EmbyPersonId": emby_person_id, "TmdbPersonId": tmdb_id_from_emby_person,
                "DoubanCelebrityId": douban_id_from_emby_person, "ImdbId": imdb_id_from_emby_person,
                "ProviderIds": provider_ids_from_emby.copy(), "_source": "emby_original"
            }
            processed_cast.append(emby_actor_internal_format)

            if douban_id_from_emby_person: emby_original_douban_ids.add(douban_id_from_emby_person)
            if tmdb_id_from_emby_person: emby_original_tmdb_ids.add(tmdb_id_from_emby_person)
            if imdb_id_from_emby_person: emby_original_imdb_ids.add(imdb_id_from_emby_person)
            
            if emby_name: emby_original_names_lower.add(str(emby_name).lower().strip())
            if original_name_from_emby and str(original_name_from_emby).lower().strip() != str(emby_name).lower().strip():
                emby_original_names_lower.add(str(original_name_from_emby).lower().strip())
        
        logger.debug(f"  步骤0: 处理完Emby原始演员，当前 processed_cast 长度: {len(processed_cast)}")
        logger.debug(f"    Emby原始豆瓣ID数: {len(emby_original_douban_ids)}, TMDbID数: {len(emby_original_tmdb_ids)}, IMDbID数: {len(emby_original_imdb_ids)}, 名字数: {len(emby_original_names_lower)}")
        if self.is_stop_requested(): return processed_cast # 注意：这里返回的是只包含Emby原始演员的列表
        
        provider_ids_media = media_info.get("ProviderIds", {})
        imdb_id_media = provider_ids_media.get("Imdb")
        douban_id_media = provider_ids_media.get("Douban")
        media_type_for_api = "movie" if media_info.get("Type") == "Movie" else ("tv" if media_info.get("Type") == "Series" else None)
        year_for_api = str(media_info.get("ProductionYear", ""))
        logger.debug(f"  媒体元数据 - IMDb: {imdb_id_media}, 豆瓣媒体ID: {douban_id_media}, 类型: {media_type_for_api}, 年份: {year_for_api}")

        # --- 步骤 1: 在线豆瓣API处理 ---
        douban_api_actors_raw: List[Dict[str, Any]] = []
        if DOUBAN_API_AVAILABLE and self.douban_api and self.data_source_mode in [constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY]:
            logger.info(f"步骤1: 媒体 '{media_name_for_log}' - 尝试在线豆瓣API。")
            douban_data = self.douban_api.get_acting(
                name=media_name_for_log, imdbid=imdb_id_media,
                mtype=media_type_for_api, year=year_for_api,
                douban_id_override=douban_id_media
            )
            logger.info(f"DEBUG_DOUBAN_API: 豆瓣 get_acting 返回的原始数据 douban_data 是: {douban_data}")
            if douban_data and not douban_data.get("error") and douban_data.get("cast"):
                douban_api_actors_raw = douban_data["cast"]
                logger.info(f"步骤1: 从豆瓣API获取到 {len(douban_api_actors_raw)} 位演员条目。")
            elif douban_data and douban_data.get("error"):
                logger.warning(f"步骤1: 豆瓣API get_acting 返回错误: {douban_data.get('message')}")
            else:
                logger.warning(f"步骤1: 豆瓣API get_acting 未返回有效演员数据或cast列表为空。")
        if self.is_stop_requested(): return processed_cast

        # --- 步骤 2: 格式化豆瓣演员，并初步去重豆瓣自身的重复项 ---
        logger.info(f"步骤2: 开始格式化和初步去重来自豆瓣的 {len(douban_api_actors_raw)} 位演员...")
        formatted_douban_candidates: List[Dict[str, Any]] = []
        seen_douban_ids_in_raw_douban_list = set()
        seen_name_sigs_in_raw_douban_list = set()

        for douban_actor_item in douban_api_actors_raw:
            if self.is_stop_requested(): break
            d_id_raw = douban_actor_item.get("id"); d_id_str = str(d_id_raw).strip() if d_id_raw is not None else ""
            d_name_chinese = str(douban_actor_item.get("name", "")).strip()
            # 确保从豆瓣的 "original_name" 或 "latin_name" 获取外文名
            d_name_foreign = str(douban_actor_item.get("original_name", douban_actor_item.get("latin_name", ""))).strip()
            d_character_raw = str(douban_actor_item.get("character", "")).strip(); d_character_cleaned = clean_character_name_static(d_character_raw)
            
            if not d_name_chinese:
                logger.debug(f"  步骤2: 跳过豆瓣演员，因缺少中文名(name): {douban_actor_item}")
                continue
            
            can_add_to_formatted = False
            if d_id_str and d_id_str not in seen_douban_ids_in_raw_douban_list:
                seen_douban_ids_in_raw_douban_list.add(d_id_str); can_add_to_formatted = True
            elif not d_id_str and d_name_chinese: # 无豆瓣ID，用名字签名去重
                name_sig = f"{d_name_chinese.lower()}|{d_name_foreign.lower()}"
                if name_sig not in seen_name_sigs_in_raw_douban_list:
                    seen_name_sigs_in_raw_douban_list.add(name_sig); can_add_to_formatted = True
            
            if can_add_to_formatted:
                formatted_douban_candidates.append({
                    "Name": d_name_chinese, "OriginalName": d_name_foreign, "Role": d_character_cleaned,
                    "DoubanCelebrityId": d_id_str if d_id_str else None,
                    "ProviderIds": {"Douban": d_id_str} if d_id_str else {}, # 初始化ProviderIds
                    "_source_comment": "from_douban_api_formatted"
                })
            else:
                logger.debug(f"  步骤2: 豆瓣演员 '{d_name_chinese}' (豆瓣ID: {d_id_str}) 因在豆瓣原始列表中重复，未加入格式化列表。")
                
        logger.info(f"步骤2: 格式化并初步去重后，得到 {len(formatted_douban_candidates)} 位豆瓣候选演员。")
        if formatted_douban_candidates: logger.debug(f"  格式化后的豆瓣候选 (前3条): {formatted_douban_candidates[:3]}")
        if self.is_stop_requested(): return processed_cast
        
        # --- 步骤 2.5 (筛选): 找出Emby中不存在的豆瓣候选演员 ---
        logger.info(f"步骤2.5: 从 {len(formatted_douban_candidates)} 位豆瓣候选筛选Emby中没有的...")
        new_candidates_for_processing: List[Dict[str, Any]] = []
        for douban_candidate in formatted_douban_candidates:
            if self.is_stop_requested(): break
            dc_douban_id = douban_candidate.get("DoubanCelebrityId")
            dc_name_chinese_lower = str(douban_candidate.get("Name", "")).lower().strip()
            dc_name_foreign_lower = str(douban_candidate.get("OriginalName", "")).lower().strip()
            
            is_already_in_emby = False
            if dc_douban_id and dc_douban_id in emby_original_douban_ids: is_already_in_emby = True
            elif dc_name_chinese_lower and dc_name_chinese_lower in emby_original_names_lower: is_already_in_emby = True
            elif dc_name_foreign_lower and dc_name_foreign_lower in emby_original_names_lower: is_already_in_emby = True
            
            if not is_already_in_emby: new_candidates_for_processing.append(douban_candidate)
            else: logger.debug(f"  步骤2.5: 豆瓣候选 '{douban_candidate.get('Name')}' 已通过ID或名字在Emby中找到，排除。")
        
        logger.info(f"步骤2.5筛选后，有 {len(new_candidates_for_processing)} 位新候选准备进入步骤3。")
        if new_candidates_for_processing: logger.debug(f"  准备进入步骤3的新候选 (前3条): {new_candidates_for_processing[:3]}")
        if self.is_stop_requested(): return processed_cast

        # --- 步骤 3: 处理筛选出的新候选演员 (TMDb匹配，获取IMDb ID，映射表交互) ---
        logger.info(f"步骤3: 开始处理 {len(new_candidates_for_processing)} 位新候选演员 (TMDb搜索、IMDb获取、映射表交互)...")
        if self.tmdb_api_key and new_candidates_for_processing:
            conn_map_step3 = self._get_db_connection()
            try:
                cursor_map_step3 = conn_map_step3.cursor()
                for candidate in new_candidates_for_processing:
                    if self.is_stop_requested(): break
                    
                    search_name_chinese = candidate.get("Name")
                    search_name_foreign = candidate.get("OriginalName")
                    douban_id_from_candidate = candidate.get("DoubanCelebrityId")

                    tmdb_id: Optional[str] = None
                    imdb_id: Optional[str] = None
                    
                    search_query_for_tmdb = search_name_chinese # 优先中文名
                    if not search_query_for_tmdb and search_name_foreign: # 中文名空，用外文
                        search_query_for_tmdb = search_name_foreign
                    
                    selected_tmdb_person_obj: Optional[Dict[str, Any]] = None

                    if search_query_for_tmdb:
                        logger.info(f"  TMDb搜索: 查询='{search_query_for_tmdb}' (源中文:'{search_name_chinese}', 源外文:'{search_name_foreign}')")
                        tmdb_search_results = tmdb_handler.search_person_tmdb(search_query_for_tmdb, self.tmdb_api_key)
                        if tmdb_search_results and tmdb_search_results.get("results"):
                            for tmdb_item in tmdb_search_results["results"][:5]: # 检查前5个
                                if tmdb_item.get("id") and tmdb_item.get("known_for_department") == "Acting":
                                    name_api = str(tmdb_item.get("name","")).strip()
                                    orig_name_api = str(tmdb_item.get("original_name","")).strip()
                                    if search_name_chinese and name_api.lower() == search_name_chinese.lower():
                                        selected_tmdb_person_obj = tmdb_item; break
                                    if search_name_foreign and orig_name_api and orig_name_api.lower() == search_name_foreign.lower():
                                        selected_tmdb_person_obj = tmdb_item; break
                                    if search_name_foreign and name_api and name_api.lower() == search_name_foreign.lower(): # TMDb name 可能是外文
                                        selected_tmdb_person_obj = tmdb_item; break
                            if not selected_tmdb_person_obj and tmdb_search_results["results"]: # 后备
                                if tmdb_search_results["results"][0].get("id") and tmdb_search_results["results"][0].get("known_for_department") == "Acting":
                                    selected_tmdb_person_obj = tmdb_search_results["results"][0]
                    
                    if selected_tmdb_person_obj and selected_tmdb_person_obj.get("id"):
                        tmdb_id = str(selected_tmdb_person_obj.get("id"))
                        candidate["TmdbPersonId"] = tmdb_id
                        candidate["ProviderIds"]["Tmdb"] = tmdb_id
                        logger.info(f"    TMDb最终选择匹配: '{selected_tmdb_person_obj.get('name')}' (ID: {tmdb_id})")
                        
                        tmdb_details = tmdb_handler.get_person_details_tmdb(int(tmdb_id), self.tmdb_api_key, append_to_response="external_ids")
                        if tmdb_details and tmdb_details.get("external_ids", {}).get("imdb_id"):
                            imdb_id = tmdb_details["external_ids"]["imdb_id"]
                            candidate["ImdbId"] = imdb_id
                            candidate["ProviderIds"]["Imdb"] = imdb_id
                            logger.info(f"      为TMDb ID {tmdb_id} 获取到 IMDb ID: {imdb_id}")

                        emby_pid_from_map: Optional[str] = None; name_from_map: Optional[str] = None
                        
                        if imdb_id: # 优先用IMDb ID查映射表
                            cursor_map_step3.execute("SELECT emby_person_id, emby_person_name, tmdb_person_id, douban_celebrity_id FROM person_identity_map WHERE imdb_id = ?", (imdb_id,))
                            entry = cursor_map_step3.fetchone()
                            if entry and entry["emby_person_id"]:
                                emby_pid_from_map = entry["emby_person_id"]; name_from_map = entry["emby_person_name"]
                                logger.info(f"      通过IMDb ID '{imdb_id}' 在映射表找到 EmbyPID: {emby_pid_from_map}")
                                if entry["tmdb_person_id"] != tmdb_id:
                                    cursor_map_step3.execute("UPDATE person_identity_map SET tmdb_person_id=?, tmdb_name=?, last_updated_at=CURRENT_TIMESTAMP WHERE imdb_id=?", (tmdb_id, candidate.get("Name"), imdb_id))
                                if douban_id_from_candidate and entry["douban_celebrity_id"] != douban_id_from_candidate:
                                    cursor_map_step3.execute("UPDATE person_identity_map SET douban_celebrity_id=?, douban_name=?, last_updated_at=CURRENT_TIMESTAMP WHERE imdb_id=?", (douban_id_from_candidate, candidate.get("Name"), imdb_id))
                                conn_map_step3.commit()
                        
                        if not emby_pid_from_map and tmdb_id: # IMDb未匹配到，再用TMDb ID查
                            cursor_map_step3.execute("SELECT emby_person_id, emby_person_name, imdb_id, douban_celebrity_id FROM person_identity_map WHERE tmdb_person_id = ?", (tmdb_id,))
                            entry = cursor_map_step3.fetchone()
                            if entry and entry["emby_person_id"]:
                                emby_pid_from_map = entry["emby_person_id"]; name_from_map = entry["emby_person_name"]
                                logger.info(f"      通过TMDb ID '{tmdb_id}' 在映射表找到 EmbyPID: {emby_pid_from_map}")
                                if imdb_id and entry["imdb_id"] != imdb_id:
                                    cursor_map_step3.execute("UPDATE person_identity_map SET imdb_id=?, last_updated_at=CURRENT_TIMESTAMP WHERE tmdb_person_id=?", (imdb_id, tmdb_id))
                                if douban_id_from_candidate and entry["douban_celebrity_id"] != douban_id_from_candidate:
                                     cursor_map_step3.execute("UPDATE person_identity_map SET douban_celebrity_id=?, douban_name=?, last_updated_at=CURRENT_TIMESTAMP WHERE tmdb_person_id=?", (douban_id_from_candidate, candidate.get("Name"), tmdb_id))
                                conn_map_step3.commit()
                        
                        if emby_pid_from_map:
                            candidate["EmbyPersonId"] = emby_pid_from_map
                            if name_from_map and name_from_map.strip(): candidate["Name"] = name_from_map
                            
                            # 合并到 processed_cast
                            actor_merged_or_added = False
                            for i, existing_actor in enumerate(processed_cast):
                                if existing_actor.get("EmbyPersonId") == emby_pid_from_map:
                                    processed_cast[i].get("ProviderIds", {}).update(candidate.get("ProviderIds", {}))
                                    if candidate.get("Role") and processed_cast[i].get("Role") != candidate.get("Role"):
                                        processed_cast[i]["Role"] = candidate.get("Role")
                                    logger.info(f"  步骤3: 候选 '{candidate.get('Name')}' (EmbyPID:{emby_pid_from_map}) 信息已合并到已存在的Emby演员。")
                                    actor_merged_or_added = True
                                    break
                            if not actor_merged_or_added:
                                processed_cast.append(candidate)
                                logger.info(f"  步骤3: 通过映射表，将演员 '{candidate['Name']}' (EmbyPID:{emby_pid_from_map}) 添加到最终演员列表。")
                        else:
                            logger.info(f"  步骤3: 演员 '{candidate.get('Name')}' (TMDbID:{tmdb_id}, IMDbID:{imdb_id}) 在映射表中未找到EmbyPID，跳过。")
                    else: 
                        logger.info(f"  步骤3: 未能从TMDb为候选 '{candidate.get('Name')}' 找到有效TMDb ID，跳过。")
            
            except sqlite3.Error as e_sqlite_step3:
                logger.error(f"步骤3数据库操作发生错误: {e_sqlite_step3}", exc_info=True)
                if conn_map_step3: conn_map_step3.rollback()
            except Exception as e_generic_step3:
                logger.error(f"步骤3发生未知错误: {e_generic_step3}", exc_info=True)
                if conn_map_step3: conn_map_step3.rollback()
            finally:
                if 'conn_map_step3' in locals() and conn_map_step3:
                    try: conn_map_step3.close(); logger.debug("步骤3的数据库连接已在finally块中关闭。")
                    except Exception as e_close: logger.error(f"关闭步骤3数据库连接时出错: {e_close}")
        else: 
            logger.info(f"步骤3: 跳过TMDb与映射表处理 (TMDb Key: {'Y' if self.tmdb_api_key else 'N'}, 新候选数: {len(new_candidates_for_processing)})")
        
        if self.is_stop_requested(): return processed_cast

        # --- 步骤 4: 最终翻译步骤 ---
        logger.info(f"步骤4: 对最终演员列表 ({len(processed_cast)}位) 进行翻译...")
        for actor_data in processed_cast:
            # ... (翻译逻辑不变) ...
            if self.is_stop_requested(): break
            actor_name_for_log = actor_data.get("Name", "未知演员")
            current_name = actor_data.get("Name")
            if current_name and not utils.contains_chinese(current_name):
                translated_name = self._translate_actor_field(current_name, "演员名(最终)", actor_name_for_log)
                if translated_name and current_name != translated_name: actor_data["Name"] = translated_name
            current_role = actor_data.get("Role")
            if current_role and not utils.contains_chinese(current_role):
                cleaned_role = clean_character_name_static(current_role)
                translated_role = self._translate_actor_field(cleaned_role, "角色名(最终)", actor_name_for_log)
                final_role_to_set = translated_role if translated_role and cleaned_role != translated_role else cleaned_role
                if actor_data.get("Role") != final_role_to_set: actor_data["Role"] = final_role_to_set
        logger.info("步骤4: 最终翻译步骤完成。")
        if self.is_stop_requested(): return processed_cast

        # --- 步骤 5: 最终去重 ---
        logger.info(f"步骤5: 开始最终去重，当前 processed_cast 长度: {len(processed_cast)}")
        final_unique_cast: List[Dict[str, Any]] = []
        seen_emby_pids_final = set()
        seen_tmdb_pids_final = set()
        seen_imdb_ids_final = set() # 新增，用于基于IMDb ID去重
        seen_douban_ids_final = set()
        seen_name_role_sigs_final = set()

        for actor in processed_cast:
            if self.is_stop_requested(): break
            
            emby_pid = actor.get("EmbyPersonId")
            tmdb_pid = actor.get("TmdbPersonId")
            imdb_pid = actor.get("ImdbId") # 获取IMDb ID
            douban_id = actor.get("DoubanCelebrityId")
            name = str(actor.get("Name", "")).strip().lower()
            role = str(actor.get("Role", "")).strip().lower()

            is_added = False
            # 优先基于EmbyPersonId去重
            if emby_pid and emby_pid not in seen_emby_pids_final:
                final_unique_cast.append(actor); seen_emby_pids_final.add(emby_pid); is_added = True
            # 其次基于IMDb ID去重
            elif not is_added and imdb_pid and imdb_pid not in seen_imdb_ids_final:
                final_unique_cast.append(actor); seen_imdb_ids_final.add(imdb_pid); is_added = True
            # 再次基于TMDbPersonId去重
            elif not is_added and tmdb_pid and tmdb_pid not in seen_tmdb_pids_final:
                final_unique_cast.append(actor); seen_tmdb_pids_final.add(tmdb_pid); is_added = True
            # 再次基于DoubanCelebrityId去重
            elif not is_added and douban_id and douban_id not in seen_douban_ids_final:
                final_unique_cast.append(actor); seen_douban_ids_final.add(douban_id); is_added = True
            # 最后基于名字+角色去重
            elif not is_added and name: 
                name_role_sig = f"{name}|{role}"
                if name_role_sig not in seen_name_role_sigs_final:
                    final_unique_cast.append(actor); seen_name_role_sigs_final.add(name_role_sig); is_added = True
            
            if not is_added:
                logger.debug(f"  步骤5最终去重: 跳过演员 '{actor.get('Name')}' (EmbyPID:{emby_pid}, TMDbPID:{tmdb_pid}, IMDbID:{imdb_pid}, DoubanID:{douban_id})，因ID或名字角色组合重复。")

        logger.info(f"步骤5: 演员列表最终处理完成 (去重后)，包含 {len(final_unique_cast)} 位演员。")
        return final_unique_cast
    
    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False) -> bool:
        """
        处理单个 Emby 媒体项目（电影或剧集）的演员信息。
        """
        if self.is_stop_requested():
            logger.info(f"任务已请求停止，跳过处理 Item ID: {emby_item_id}")
            return False # 表示未成功处理

        # 检查是否已处理过 (除非强制重处理)
        if not force_reprocess_this_item and emby_item_id in self.processed_items_cache:
            logger.info(f"Item ID '{emby_item_id}' 已处理过且未强制重处理，跳过。")
            return True # 视为成功，因为之前已处理

        # 检查基本配置
        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error(f"Emby配置不完整，无法处理 Item ID: {emby_item_id}")
            self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", "Emby配置不完整", "未知类型")
            return False

        logger.info(f"开始处理单个Emby Item ID: {emby_item_id}")
        item_details: Optional[Dict[str, Any]] = None
        item_name_for_log = f"未知项目(ID:{emby_item_id})" # 默认名
        item_type_for_log = "未知类型" # 默认类型

        # 1. 获取 Emby 项目详情
        try:
            item_details = emby_handler.get_emby_item_details(
                emby_item_id,
                self.emby_url,
                self.emby_api_key,
                self.emby_user_id
            )
            if item_details:
                item_name_for_log = item_details.get("Name", item_name_for_log)
                item_type_for_log = item_details.get("Type", item_type_for_log)
            else: # get_emby_item_details 返回了 None
                logger.error(f"无法获取Emby项目 {emby_item_id} 的详情（emby_handler返回None），处理中止。")
                self.save_to_failed_log(emby_item_id, item_name_for_log, "无法获取Emby项目详情(API返回None)", item_type_for_log)
                return False
        except Exception as e_get_details:
            logger.error(f"获取Emby项目 {emby_item_id} 详情时发生异常: {e_get_details}", exc_info=True)
            self.save_to_failed_log(emby_item_id, item_name_for_log, f"获取Emby详情异常: {e_get_details}", item_type_for_log)
            return False

        if self.is_stop_requested():
            logger.info(f"停止信号：获取Emby详情后中止 (Item ID: {emby_item_id})")
            return False

        # 2. 调用 _process_cast_list 处理演员列表
        # current_emby_cast_raw 是从 Emby 获取的原始 People 列表
        current_emby_cast_raw = item_details.get("People", [])
        logger.info(f"媒体 '{item_name_for_log}' 原始Emby People数量: {len(current_emby_cast_raw)} (将从中提取演员)")

        if self.is_stop_requested():
            logger.info(f"停止信号：调用 _process_cast_list 前中止 (Item ID: {emby_item_id})")
            return False

        # processed_cast_internal_format 是经过所有处理（包括新增、翻译、去重）后的最终演员列表
        # 它的每个元素是我们内部定义的统一演员字典结构
        final_cast_for_item = self._process_cast_list(current_emby_cast_raw, item_details)

        if self.is_stop_requested():
            logger.info(f"停止信号：_process_cast_list 执行完毕，更新Emby前中止 (Item ID: {emby_item_id})")
            return False

        # 3. 构建发送给 emby_handler.update_emby_item_cast 的数据
        cast_for_emby_update: List[Dict[str, Any]] = []
        for actor_data in final_cast_for_item:
            actor_name = actor_data.get("Name")
            if not actor_name or not str(actor_name).strip():
                logger.warning(f"process_single_item: 跳过无效的演员条目（缺少或空白Name）：{actor_data}")
                continue

            entry_for_emby_handler = {
                "name": str(actor_name).strip(),
                "character": str(actor_data.get("Role", "")).strip(), # 我们内部用 "Role"
                "provider_ids": actor_data.get("ProviderIds", {}).copy()
            }
            # 如果是已存在的 Emby 演员，传递其 Emby Person ID
            emby_person_id = actor_data.get("EmbyPersonId")
            if emby_person_id and str(emby_person_id).strip():
                entry_for_emby_handler["emby_person_id"] = str(emby_person_id).strip()
            
            cast_for_emby_update.append(entry_for_emby_handler)
        
        logger.debug(f"process_single_item: 准备发送给 emby_handler.update_emby_item_cast 的 cast_for_emby_update (共 {len(cast_for_emby_update)} 条，前5条): {cast_for_emby_update[:5]}")

        # 4. 更新 Emby 项目的演员信息
        update_success = False
        try:
            update_success = emby_handler.update_emby_item_cast(
                item_id=emby_item_id,
                new_cast_list_for_handler=cast_for_emby_update,
                emby_server_url=self.emby_url,
                emby_api_key=self.emby_api_key,
                user_id=self.emby_user_id
            )
        except Exception as e_update_cast:
            logger.error(f"更新Emby项目 {emby_item_id} ('{item_name_for_log}') 演员信息时发生严重异常: {e_update_cast}", exc_info=True)
            self.save_to_failed_log(emby_item_id, item_name_for_log, f"更新Emby演员时发生严重异常: {e_update_cast}", item_type_for_log)
            return False # 标记为处理失败

        # 5. 处理结果，记录日志，触发刷新
        if update_success:
            logger.info(f"Emby项目 {emby_item_id} ('{item_name_for_log}') 演员信息更新成功。")
            self.save_to_processed_log(emby_item_id, item_name_for_log) # 保存名称
            
            if self.config.get("refresh_emby_after_update", True):
                if self.is_stop_requested():
                    logger.info(f"停止信号：刷新Emby元数据前中止 (Item ID: {emby_item_id})")
                    return True # 更新已成功，只是刷新被中止
                
                logger.info(f"准备为项目 {emby_item_id} ('{item_name_for_log}') 触发Emby元数据刷新...")
                try:
                    emby_handler.refresh_emby_item_metadata(
                        item_emby_id=emby_item_id,
                        emby_server_url=self.emby_url,
                        emby_api_key=self.emby_api_key,
                        recursive=(item_type_for_log == "Series"), # 根据媒体类型判断是否递归
                        # 其他刷新参数使用 emby_handler 中的默认值
                    )
                except Exception as e_refresh:
                     logger.error(f"刷新Emby元数据失败 for '{item_name_for_log}' (ID: {emby_item_id}): {e_refresh}", exc_info=True)
                     # 即使刷新失败，演员更新本身是成功的，所以仍然返回 True
            return True
        else:
            logger.error(f"Emby项目 {emby_item_id} ('{item_name_for_log}') 演员信息更新失败 (emby_handler返回False)。")
            self.save_to_failed_log(emby_item_id, item_name_for_log, "更新Emby演员信息失败(API返回失败)", item_type_for_log)
            return False

    def _format_tmdb_person_for_emby_handler(self, tmdb_person_data: Dict[str, Any],
                                             role_from_source: Optional[str] = None
                                             ) -> Dict[str, Any]:
        """
        将从 TMDb API 获取的演员数据格式化。
        主要名字直接使用 TMDb 返回的 name。
        """
        tmdb_id_str = str(tmdb_person_data.get("id")) if tmdb_person_data.get("id") else None
        final_role = utils.clean_character_name_static(role_from_source) if role_from_source and role_from_source.strip() else "演员"

        name_from_tmdb = tmdb_person_data.get("name") # 直接使用 TMDb 的名字
        # original_name 可以是 TMDb 的 name (如果它是外文)，或者也用 name_from_tmdb
        # 如果 tmdb_person_data 中有 'original_name' 字段且不同于 'name'，也可以考虑使用
        original_name_to_use = tmdb_person_data.get("original_name", name_from_tmdb)


        actor_entry = {
            "Name": name_from_tmdb, # <--- 使用 TMDb 的名字
            "OriginalName": original_name_to_use,
            "Role": final_role,
            "Type": "Actor", # Emby 需要这个
            "ProviderIds": {},
            "EmbyPersonId": None, # 新增演员，初始没有 Emby Person ID
            "TmdbPersonId": tmdb_id_str, # 存储 TMDb Person ID
            "DoubanCelebrityId": None, # 初始设为 None，如果源数据有，后面会填充
            "ProfileImagePathTMDb": tmdb_person_data.get("profile_path"),
            "_source": "tmdb_added_or_enhanced" # 标记来源
        }
        if tmdb_id_str:
            actor_entry["ProviderIds"]["Tmdb"] = tmdb_id_str

        external_ids = tmdb_person_data.get("external_ids", {}) # 来自 get_person_details_tmdb
        tmdb_imdb_id = external_ids.get("imdb_id")
        if tmdb_imdb_id:
            actor_entry["ProviderIds"]["Imdb"] = tmdb_imdb_id
        
        # 如果 tmdb_person_data 中有 translations，可以考虑提取中文名作为 Name (如果 Name 当前是外文)
        # 例如:
        # translations = tmdb_person_data.get("translations", {}).get("translations", [])
        # for trans in translations:
        #     if trans.get("iso_639_1") == "zh" and trans.get("data", {}).get("name"):
        #         actor_entry["Name"] = trans["data"]["name"]
        #         logger.debug(f"  使用TMDb的中文翻译 '{actor_entry['Name']}' 替换/作为演员名。")
        #         break # 通常取第一个中文翻译即可

        return actor_entry

    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False):
        self.clear_stop_signal() # 清除任何可能残留的停止信号
        logger.debug(f"process_full_library: 方法开始执行。")
        logger.debug(f"  Initial self.libraries_to_process (来自实例属性): {self.libraries_to_process}")
        logger.debug(f"  force_reprocess_all: {force_reprocess_all}")

        if force_reprocess_all:
            logger.info("用户请求强制重处理所有媒体项，将清除数据库中的已处理记录。")
            self.clear_processed_log() # 这个方法应该清除数据库和内存缓存

        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error("Emby配置不完整，无法处理整个媒体库。")
            if update_status_callback:
                update_status_callback(-1, "Emby配置不完整")
            return

        # --- 关键检查点：从实例属性获取要处理的库列表 ---
        current_libs_to_process = self.libraries_to_process
        logger.info(f"process_full_library: 即将用于获取项目的媒体库ID列表为: {current_libs_to_process}")

        if not current_libs_to_process: # 检查列表是否为空
            logger.warning("process_full_library: 配置中要处理的媒体库ID列表 (current_libs_to_process) 为空。将不会从Emby获取任何项目。")
            if update_status_callback:
                update_status_callback(100, "未在配置中指定要处理的媒体库，或列表为空。")
            # 注意：emby_handler.get_emby_library_items 如果接收到空的 library_ids 列表，它自己也会返回空列表。
            # 所以这里的行为是正确的，后续 movies 和 series_list 会是空。
            # 如果你希望在这种情况下完全不执行后续步骤，可以在这里直接 return。
            # return
        # --- 关键检查点结束 ---

        logger.info(f"开始全量处理选定的Emby媒体库 (ID(s): {current_libs_to_process if current_libs_to_process else '无特定库'})...")
        if update_status_callback:
            update_status_callback(0, "正在获取电影列表...")

        movies: Optional[List[Dict[str, Any]]] = None
        series_list: Optional[List[Dict[str, Any]]] = None

        try:
            movies = emby_handler.get_emby_library_items(
                self.emby_url, self.emby_api_key, "Movie", self.emby_user_id,
                library_ids=current_libs_to_process # 使用局部变量传递
            )
        except Exception as e_movie_get:
            logger.error(f"获取电影列表时发生严重错误: {e_movie_get}", exc_info=True)
            movies = [] # 出错则视为空列表

        if self.is_stop_requested():
            logger.info("获取电影列表后检测到停止信号，处理中止。")
            if update_status_callback: update_status_callback(background_task_status.get("progress", 5), "任务已中断") # type: ignore
            return

        if update_status_callback:
            update_status_callback(5, "正在获取剧集列表...") # 假设电影占5%进度

        try:
            series_list = emby_handler.get_emby_library_items(
                self.emby_url, self.emby_api_key, "Series", self.emby_user_id,
                library_ids=current_libs_to_process # 使用局部变量传递
            )
        except Exception as e_series_get:
            logger.error(f"获取剧集列表时发生严重错误: {e_series_get}", exc_info=True)
            series_list = [] # 出错则视为空列表


        if self.is_stop_requested():
            logger.info("获取剧集列表后检测到停止信号，处理中止。")
            if update_status_callback: update_status_callback(background_task_status.get("progress", 10), "任务已中断") # type: ignore
            return

        # 合并电影和剧集列表，并确保它们是列表类型
        all_items = (movies if isinstance(movies, list) else []) + \
                    (series_list if isinstance(series_list, list) else [])
        total_items = len(all_items)

        if total_items == 0:
            logger.info("从选定的媒体库中未获取到任何电影或剧集项目，或者获取失败，无需进一步处理。")
            if update_status_callback:
                update_status_callback(100, "未在选定库中找到项目或获取失败。")
            return

        logger.info(f"总共从选定的库中获取到 {len(movies) if movies else 0} 部电影和 {len(series_list) if series_list else 0} 部剧集，共 {total_items} 个项目进行处理。")

        for i, item in enumerate(all_items):
            if self.is_stop_requested():
                logger.info("全量媒体库处理在项目迭代中被用户中断。")
                if update_status_callback:
                    current_progress_on_stop = int(((i) / total_items) * 100) if total_items > 0 else 0
                    update_status_callback(current_progress_on_stop, "任务已中断")
                break # 跳出循环

            item_id = item.get('Id')
            item_name = item.get('Name', f"未知项目(ID:{item_id})")
            item_type_str = "电影" if item.get("Type") == "Movie" else ("剧集" if item.get("Type") == "Series" else "未知类型")

            # 更新进度 (0-99% 用于处理过程，100% 用于完成)
            # 这里的进度是基于总项目数的，而不是获取列表的进度
            progress_percent = int(((i + 1) / total_items) * 90) + 10 # 假设获取列表占了前10%
            if progress_percent > 99: progress_percent = 99


            message = f"正在处理 {item_type_str} ({i+1}/{total_items}): {item_name}"
            logger.info(message)
            if update_status_callback:
                update_status_callback(progress_percent, message)

            if not item_id:
                logger.warning(f"条目缺少ID，跳过: {item_name}")
                continue

            # 调用 process_single_item 处理单个项目
            # force_reprocess_this_item 参数现在由 force_reprocess_all 控制
            process_success = self.process_single_item(item_id, force_reprocess_this_item=force_reprocess_all)

            if not process_success and self.is_stop_requested():
                # 如果处理单个项目失败是因为收到了停止信号
                logger.info(f"处理 Item ID {item_id} ('{item_name}') 时被中断，停止全量扫描。")
                if update_status_callback:
                    update_status_callback(progress_percent, f"处理 '{item_name}' 时中断")
                break # 跳出循环

            # 项目间延迟
            delay = float(self.config.get("delay_between_items_sec", 0.5))
            if delay > 0 and i < total_items - 1: # 最后一个项目之后不延迟
                if self.is_stop_requested():
                    logger.info("项目间延迟等待前检测到停止信号，中断全量扫描。")
                    if update_status_callback: update_status_callback(progress_percent, "任务已中断")
                    break
                time.sleep(delay)

        # 循环结束后的最终状态报告
        if self.is_stop_requested():
            logger.info("全量处理任务已结束（因用户请求停止）。")
            # 状态已在循环内或 _execute_task_with_lock 的 finally 中更新
        else:
            logger.info("全量处理Emby媒体库结束。")
            if update_status_callback:
                update_status_callback(100, "全量处理完成。")


    def close(self):
        """关闭 MediaProcessor 实例，例如关闭数据库连接池或释放其他资源。"""
        if self.douban_api and hasattr(self.douban_api, 'close'):
            logger.info("正在关闭 MediaProcessor 中的 DoubanApi session...")
            self.douban_api.close()
        # 如果有其他需要关闭的资源，例如数据库连接池（如果使用的话），在这里关闭
        logger.info("MediaProcessor close 方法执行完毕。")

class SyncHandler: # 或者直接作为 MediaProcessor 的方法
    def __init__(self, db_path: str, emby_url: str, emby_api_key: str, emby_user_id: Optional[str]):
        self.db_path = db_path
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id

    def _get_db_conn(self): # 与 MediaProcessor 中的类似
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def sync_emby_person_map_to_db(self, update_status_callback: Optional[callable] = None):
        logger.info("开始同步 Emby Person 映射表到本地数据库 (将包含IMDb ID)...")
        if update_status_callback: update_status_callback(0, "正在从Emby获取所有人物信息...")

        persons_from_emby = emby_handler.get_all_persons_from_emby(
            self.emby_url, self.emby_api_key, self.emby_user_id
        )
        
        if persons_from_emby is None:
            logger.error("同步映射表失败：无法从 Emby 获取 Person 列表。")
            if update_status_callback: update_status_callback(-1, "从Emby获取人物信息失败")
            return
        if not persons_from_emby:
            logger.info("Emby 中没有找到任何 Person 条目。")
            if update_status_callback: update_status_callback(100, "Emby中无人物信息")
            return

        conn = self._get_db_conn() # 打开数据库连接
        
        total_emby_persons_processed = 0
        persons_with_tmdb_id_found = 0
        persons_with_imdb_id_found = 0
        newly_mapped_count = 0
        updated_in_map_count = 0
        skipped_due_to_no_key_id = 0 # Renamed for clarity
        db_errors_count = 0

        total_persons_from_emby_api = len(persons_from_emby)
        logger.info(f"从Emby API获取到 {total_persons_from_emby_api} 个 Person 条目，开始处理并同步...")

        try: # 将整个循环和数据库操作包裹在 try...finally 中以确保连接关闭
            cursor = conn.cursor() # 在 try 内部获取 cursor

            for idx, person_emby in enumerate(persons_from_emby):
                total_emby_persons_processed += 1
                if update_status_callback and idx > 0 and idx % 100 == 0:
                    progress = int(((idx + 1) / total_persons_from_emby_api) * 100)
                    update_status_callback(progress, f"正在处理第 {idx+1}/{total_persons_from_emby_api} 个人物...")

                emby_pid = person_emby.get("Id")
                emby_name = person_emby.get("Name")
                provider_ids = person_emby.get("ProviderIds", {})
                tmdb_pid = str(provider_ids.get("Tmdb")).strip() if provider_ids.get("Tmdb") else None
                douban_pid = str(provider_ids.get("Douban")).strip() if provider_ids.get("Douban") else None
                imdb_pid = str(provider_ids.get("Imdb")).strip() if provider_ids.get("Imdb") else None

                if not emby_pid: 
                    logger.debug(f"跳过Emby Person (Name: '{emby_name}')，缺少 Emby Person ID。")
                    continue
                
                if tmdb_pid: persons_with_tmdb_id_found +=1
                if imdb_pid: persons_with_imdb_id_found +=1

                primary_external_id_type = None
                primary_external_id_value = None

                if tmdb_pid:
                    primary_external_id_type = "tmdb_person_id"
                    primary_external_id_value = tmdb_pid
                elif imdb_pid:
                    primary_external_id_type = "imdb_id"
                    primary_external_id_value = imdb_pid
                else:
                    logger.debug(f"跳过Emby Person '{emby_name}' (EmbyID: {emby_pid})，缺少TMDb ID和IMDb ID。")
                    skipped_due_to_no_key_id += 1
                    continue
                
                try:
                    sql_select = f"SELECT emby_person_id, emby_person_name, tmdb_person_id, imdb_id, douban_celebrity_id FROM person_identity_map WHERE {primary_external_id_type} = ?"
                    cursor.execute(sql_select, (primary_external_id_value,))
                    existing_map_entry = cursor.fetchone()

                    if existing_map_entry:
                        needs_update = False
                        update_sql_set_parts = []
                        update_values_dict = {}

                        if existing_map_entry["emby_person_id"] != emby_pid:
                            needs_update = True; update_sql_set_parts.append("emby_person_id = :val_emby_pid"); update_values_dict["val_emby_pid"] = emby_pid
                        if existing_map_entry["emby_person_name"] != emby_name:
                            needs_update = True; update_sql_set_parts.append("emby_person_name = :val_emby_name"); update_values_dict["val_emby_name"] = emby_name
                        
                        if tmdb_pid and existing_map_entry["tmdb_person_id"] != tmdb_pid:
                            needs_update = True; update_sql_set_parts.append("tmdb_person_id = :val_tmdb_pid"); update_values_dict["val_tmdb_pid"] = tmdb_pid
                            if not existing_map_entry.get("tmdb_name") or existing_map_entry.get("tmdb_name") != emby_name:
                                 update_sql_set_parts.append("tmdb_name = :val_tmdb_name"); update_values_dict["val_tmdb_name"] = emby_name
                        
                        if imdb_pid and existing_map_entry["imdb_id"] != imdb_pid:
                            needs_update = True; update_sql_set_parts.append("imdb_id = :val_imdb_pid"); update_values_dict["val_imdb_pid"] = imdb_pid
                        
                        if douban_pid and existing_map_entry["douban_celebrity_id"] != douban_pid:
                            needs_update = True; update_sql_set_parts.append("douban_celebrity_id = :val_douban_pid"); update_values_dict["val_douban_pid"] = douban_pid
                            if not existing_map_entry.get("douban_name") or existing_map_entry.get("douban_name") != emby_name:
                                 update_sql_set_parts.append("douban_name = :val_douban_name"); update_values_dict["val_douban_name"] = emby_name
                        
                        if needs_update:
                            update_sql_set_parts.append("last_synced_at = CURRENT_TIMESTAMP")
                            update_sql_set_parts.append("last_updated_at = CURRENT_TIMESTAMP")
                            sql_update = f"UPDATE person_identity_map SET {', '.join(update_sql_set_parts)} WHERE {primary_external_id_type} = :primary_id_val_where"
                            update_values_dict["primary_id_val_where"] = primary_external_id_value
                            
                            cursor.execute(sql_update, update_values_dict)
                            if cursor.rowcount > 0: updated_in_map_count += 1
                            logger.debug(f"更新映射表 for {primary_external_id_type} {primary_external_id_value} -> EmbyID {emby_pid}, Name: '{emby_name}'")
                    else:
                        # 准备插入新记录
                        cols_to_insert = ["emby_person_id", "emby_person_name"]
                        vals_for_execute = [emby_pid, emby_name]
                        placeholders_for_sql = ["?", "?"]

                        cols_to_insert.extend(["last_synced_at", "last_updated_at"])
                        placeholders_for_sql.extend(["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]) # 直接是SQL关键字

                        if tmdb_pid:
                            cols_to_insert.extend(["tmdb_person_id", "tmdb_name"])
                            vals_for_execute.extend([tmdb_pid, emby_name])
                            placeholders_for_sql.extend(["?", "?"])
                        if imdb_pid:
                            cols_to_insert.append("imdb_id")
                            vals_for_execute.append(imdb_pid)
                            placeholders_for_sql.append("?")
                        if douban_pid:
                            cols_to_insert.extend(["douban_celebrity_id", "douban_name"])
                            vals_for_execute.extend([douban_pid, emby_name])
                            placeholders_for_sql.extend(["?", "?"])
                        
                        sql_insert = f"INSERT INTO person_identity_map ({', '.join(cols_to_insert)}) VALUES ({', '.join(placeholders_for_sql)})"
                        
                        logger.debug(f"  准备执行 INSERT SQL: {sql_insert}")
                        logger.debug(f"  绑定的参数 (vals_for_execute): {tuple(vals_for_execute)}")
                        
                        cursor.execute(sql_insert, tuple(vals_for_execute))
                        if cursor.rowcount > 0: newly_mapped_count += 1
                        logger.info(f"新增映射到表: EmbyID {emby_pid}, Name: '{emby_name}', TMDbID: {tmdb_pid}, IMDbID: {imdb_pid}, DoubanID: {douban_pid}")
                
                except sqlite3.IntegrityError as e_integrity:
                    logger.warning(f"同步Emby Person '{emby_name}' 时发生完整性错误 (可能唯一键冲突): {e_integrity}. EmbyID: {emby_pid}, TMDbID: {tmdb_pid}, IMDbID: {imdb_pid}")
                    conn.rollback() # 回滚当前失败的事务部分
                    db_errors_count += 1
                except sqlite3.Error as e_sql_op:
                    logger.error(f"同步Emby Person '{emby_name}' (EmbyID: {emby_pid}) 到数据库时出错: {e_sql_op}")
                    db_errors_count += 1
                    conn.rollback()
            
            if idx > 0 and idx % 1000 == 0: # 每1000条提交一次，防止事务过大
                conn.commit()
                logger.info(f"已处理 {idx+1} 条记录，进行一次数据库提交。")

            conn.commit() # 循环结束后，提交所有剩余的更改

        except Exception as e_outer: # 捕获循环外部或连接本身的错误
            logger.error(f"同步映射表主循环发生错误: {e_outer}", exc_info=True)
            if conn: conn.rollback() # 如果连接存在，回滚
            db_errors_count +=1 # 算作一个大的DB错误
        finally:
            if conn: conn.close() # 确保连接在任何情况下都被关闭

        logger.info("--- Emby Person 映射表同步统计 ---")
        logger.info(f"从 Emby API 共获取 Person 条目数: {total_persons_from_emby_api}")
        logger.info(f"实际处理的 Emby Person 条目数: {total_emby_persons_processed}")
        logger.info(f"其中包含有效 TMDb ID 的 Person 数: {persons_with_tmdb_id_found}")
        logger.info(f"其中包含有效 IMDb ID 的 Person 数: {persons_with_imdb_id_found}")
        logger.info(f"因缺少关键ID而跳过的 Person 数: {skipped_due_to_no_key_id}")
        logger.info(f"本次同步新增到映射表的条目数: {newly_mapped_count}")
        logger.info(f"本次同步更新映射表中已有条目的数量: {updated_in_map_count}")
        logger.info(f"数据库操作错误数: {db_errors_count}")
        logger.info("------------------------------------")

        if update_status_callback:
            if db_errors_count > 0:
                update_status_callback(-1, f"映射表同步部分完成但有{db_errors_count}个错误。新增{newly_mapped_count}, 更新{updated_in_map_count}。")
            else:
                update_status_callback(100, f"映射表同步完成。新增{newly_mapped_count}, 更新{updated_in_map_count}。")