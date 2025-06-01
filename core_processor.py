# core_processor.py
import time
import re
import os
import sqlite3 # 用于数据库操作
from typing import Dict, List, Optional, Any
import threading
import local_data_handler

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
        self.db_path = config.get('db_path') # 从配置中获取 db_path

        if not self.db_path:
            logger.error("MediaProcessor 初始化失败：未在配置中找到 'db_path'。")
            raise ValueError("数据库路径 (db_path) 未在配置中提供给 MediaProcessor。")

        self.douban_api = None
        if DOUBAN_API_AVAILABLE:
            try:
                self.douban_api = DoubanApi(db_path=self.db_path) # 将 db_path 传递给 DoubanApi
                logger.info("DoubanApi 实例已在 MediaProcessor 中创建 (使用数据库缓存)。")
            except Exception as e:
                logger.error(f"MediaProcessor 初始化 DoubanApi 失败: {e}", exc_info=True)
                self.douban_api = DoubanApi(db_path=self.db_path) # 即使失败，也尝试用假的
        else:
            logger.warning("DoubanApi 在 MediaProcessor 中不可用，将使用假的实例。")
            self.douban_api = DoubanApi(db_path=self.db_path) # 使用假的实例

        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.translator_engines = self.config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        self.domestic_source_mode = self.config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)

        self.data_source_mode = config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)
        self.local_data_path = config.get("local_data_path", constants.DEFAULT_LOCAL_DATA_PATH).strip() # 获取并去除首尾空格

        self._stop_event = threading.Event()
        self.processed_items_cache = self._load_processed_log_from_db() # 从数据库加载
        self.libraries_to_process = config.get("libraries_to_process", [])

        logger.info(f"MediaProcessor __init__: self.libraries_to_process 被设置为: {self.libraries_to_process}")
        if not self.libraries_to_process:
            logger.warning("MediaProcessor __init__: 注意！self.libraries_to_process 为空，全量扫描将不处理任何特定库。")

        logger.info(f"MediaProcessor 初始化完成。Emby URL: {self.emby_url}, UserID: {self.emby_user_id}")
        logger.debug(f"  将使用数据库: {self.db_path}")
        logger.debug(f"  翻译引擎顺序: {self.translator_engines}")
        logger.debug(f"  国产片豆瓣策略: {self.domestic_source_mode}")
        logger.info(f"  已从数据库加载 {len(self.processed_items_cache)} 个已处理媒体记录到内存缓存。")


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


    def _process_cast_list(self, current_cast: List[Dict[str, Any]], media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        media_name_for_log = media_info.get("Name", "未知媒体")
        logger.info(f"开始处理媒体 '{media_name_for_log}' 的演员列表，原始Emby演员数量: {len(current_cast)} 位。")

        # 准备一个工作副本的演员列表，以及标记数组
        processed_cast: List[Dict[str, Any]] = [actor.copy() for actor in current_cast]
        # 标记哪些 Emby 演员的字段被本地数据或豆瓣成功提供了“最终”中文版本
        # True 表示该字段已最终确定，无需后续翻译
        actor_field_finalized: List[Dict[str, bool]] = [{"name": False, "character": False} for _ in processed_cast]

        # 从 media_info 中获取用于查找本地文件和调用豆瓣API的ID
        # ProviderIds 结构可能因 Emby 版本或刮削器而异，确保路径正确
        provider_ids = media_info.get("ProviderIds", {})
        imdb_id_from_emby = provider_ids.get("Imdb")
        douban_id_from_emby = provider_ids.get("Douban") # 神医刮削通常会写入豆瓣ID

        media_type_for_local = "movie" if media_info.get("Type") == "Movie" else ("tv" if media_info.get("Type") == "Series" else None)
        year_for_api = str(media_info.get("ProductionYear", ""))

        local_data_processed_actors = False # 标记是否成功从本地数据加载了演员

        # --------------------------------------------------------------------
        # 步骤 0: 尝试从本地数据源 (神医JSON) 加载演员信息
        # --------------------------------------------------------------------
        if self.local_data_path and media_type_for_local and \
           self.data_source_mode in [constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, constants.DOMESTIC_SOURCE_MODE_LOCAL_ONLY]:

            logger.info(f"步骤0: 媒体 '{media_name_for_log}' - 尝试从本地数据源加载演员信息 (IMDb: {imdb_id_from_emby}, Douban: {douban_id_from_emby})。")
            local_json_file_path = local_data_handler.find_local_json_path(
                local_data_root_path=self.local_data_path,
                media_type=media_type_for_local,
                imdb_id=imdb_id_from_emby
                # douban_id=douban_id_from_emby # find_local_json_path 现在主要用 imdb_id
            )

            if local_json_file_path:
                local_parsed_data = local_data_handler.parse_local_actor_data(local_json_file_path)
                if local_parsed_data and local_parsed_data.get("cast"):
                    local_cast_list = local_parsed_data["cast"]
                    logger.info(f"成功从本地文件 '{local_json_file_path}' 加载到 {len(local_cast_list)} 位演员。")

                    # TODO: 核心逻辑 - 将 local_cast_list 的数据应用到 processed_cast
                    # 这部分会比较复杂，需要匹配或替换 Emby 中的演员
                    # 简化处理：如果本地有数据，我们先假设它完全覆盖 Emby 的演员列表
                    # （这可能不理想，更好的做法是基于演员名或ID进行匹配和合并）
                    # 为了演示，我们先用本地数据替换（如果本地数据非空）
                    if local_cast_list: # 只有当本地真的有演员时才替换
                        logger.info(f"使用本地数据覆盖/填充演员列表 (原Emby演员数: {len(processed_cast)})。")
                        new_processed_cast = []
                        new_actor_field_finalized = [] # 新的标记数组

                        for local_actor in local_cast_list:
                            # 将本地演员数据转换为我们内部使用的格式
                            # 注意：local_actor 的 "id" 可能是豆瓣 celebrity ID
                            internal_actor_entry = {
                                "Name": local_actor.get("name"), # 本地数据通常是中文名
                                "Character": local_actor.get("character"), # 本地数据通常是中文角色名
                                "Id": None, # Emby Person ID，本地数据通常没有，除非神医也存了
                                "OriginalName": local_actor.get("latin_name"), # 本地数据可能有外文名
                                "_source": "local_data" # 标记来源
                            }
                            new_processed_cast.append(internal_actor_entry)
                            # 假设本地获取的都是最终中文，标记为已最终确定
                            new_actor_field_finalized.append({"name": True, "character": True})

                        processed_cast = new_processed_cast
                        actor_field_finalized = new_actor_field_finalized
                        local_data_processed_actors = True
                        logger.info(f"演员列表已更新为本地数据，共 {len(processed_cast)} 位演员。")
                    else:
                        logger.info(f"本地文件 '{local_json_file_path}' 解析成功但未包含演员信息。")
                else:
                    logger.info(f"本地文件 '{local_json_file_path}' 解析失败或未返回有效数据结构。")
            else:
                logger.info(f"未能在本地数据源中找到媒体 '{media_name_for_log}' 的元数据文件。")

        if self.is_stop_requested(): logger.info("本地数据处理后检测到停止信号。"); return processed_cast

        # --------------------------------------------------------------------
        # 步骤 1: (如果需要) 尝试从在线豆瓣API获取演员信息
        # --------------------------------------------------------------------
        should_call_douban_api = False
        if self.data_source_mode == constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY:
            should_call_douban_api = True
            logger.info(f"步骤1: 模式为仅在线API，准备调用豆瓣API。")
        elif self.data_source_mode == constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE and not local_data_processed_actors:
            should_call_douban_api = True
            logger.info(f"步骤1: 模式为本地优先但本地未处理成功，准备调用豆瓣API。")
        elif self.data_source_mode == constants.DOMESTIC_SOURCE_MODE_LOCAL_ONLY:
            logger.info(f"步骤1: 模式为仅本地数据，跳过在线豆瓣API。")
        elif local_data_processed_actors and self.data_source_mode == constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE:
            logger.info(f"步骤1: 已通过本地数据处理，跳过在线豆瓣API。")


        if should_call_douban_api and self.douban_api and hasattr(self.douban_api, 'get_acting') and \
           self.data_source_mode != constants.DOMESTIC_SOURCE_MODE_DISABLED: # 确保API未被禁用
            # (这里的 self.config.get("domestic_source_mode", ...) 判断可以移除或与 self.data_source_mode 统一)

            logger.info(f"步骤1: 媒体 '{media_name_for_log}' - 尝试从在线豆瓣API获取演员信息。")
            douban_actors_raw = None
            try:
                douban_api_cooldown = float(self.config.get("api_douban_default_cooldown_seconds", 1.0))
                if douban_api_cooldown > 0: time.sleep(douban_api_cooldown)

                # 调用豆瓣API时，优先使用从Emby获取的豆瓣ID（如果神医刮削了）或IMDbID
                douban_actors_raw = self.douban_api.get_acting(
                    name=media_name_for_log, # 作为后备
                    imdbid=imdb_id_from_emby,
                    mtype=media_type_for_local,
                    year=year_for_api,
                    douban_id_override=douban_id_from_emby # 优先用这个
                )
            except Exception as e_douban_get_acting:
                logger.error(f"调用豆瓣 get_acting 时发生错误: {e_douban_get_acting}", exc_info=True)

            if self.is_stop_requested(): logger.info("获取豆瓣信息后检测到停止信号。"); return processed_cast

            if douban_actors_raw and douban_actors_raw.get("cast"):
                online_douban_cast_list = douban_actors_raw["cast"]
                logger.info(f"从在线豆瓣API为 '{media_name_for_log}' 获取到 {len(online_douban_cast_list)} 位演员。")
                # TODO: 核心逻辑 - 将 online_douban_cast_list 的数据应用到 processed_cast
                # 这部分逻辑与你之前处理豆瓣API返回的逻辑类似。
                # 如果本地数据已经处理过 (local_data_processed_actors is True)，
                # 你可能需要决定是合并、替换还是跳过。
                # 为了简化，如果本地已处理，我们这里可以先跳过在线豆瓣数据，
                # 或者只用在线数据补充本地数据中没有的演员（这需要更复杂的匹配逻辑）。
                # 假设：如果本地数据已处理，我们不再用在线豆瓣数据覆盖。
                if not local_data_processed_actors: # 只有当本地数据未处理时，才使用在线豆瓣数据
                    logger.info("使用在线豆瓣数据填充/覆盖演员列表。")
                    new_processed_cast_online = []
                    new_actor_field_finalized_online = []
                    for online_actor in online_douban_cast_list:
                        internal_actor_entry = {
                            "Name": online_actor.get("name"),
                            "Character": online_actor.get("character"), # 可能需要 clean_character_name_static
                            "Id": None,
                            "OriginalName": online_actor.get("latin_name", online_actor.get("original_name")),
                            "_source": "douban_api"
                        }
                        new_processed_cast_online.append(internal_actor_entry)
                        new_actor_field_finalized_online.append({"name": True, "character": True}) # 假设豆瓣API返回的是最终中文
                    processed_cast = new_processed_cast_online
                    actor_field_finalized = new_actor_field_finalized_online
                    logger.info(f"演员列表已更新为在线豆瓣数据，共 {len(processed_cast)} 位演员。")
                else:
                    logger.info("本地数据已处理演员，在线豆瓣数据将被忽略或仅用于补充（补充逻辑未实现）。")

            elif douban_actors_raw and douban_actors_raw.get("error"):
                 logger.warning(f"在线豆瓣API返回错误: {douban_actors_raw.get('message', '未知豆瓣错误')}")
            else:
                 logger.info(f"在线豆瓣API未能为 '{media_name_for_log}' 提供演员信息或返回未知结构: {douban_actors_raw}")
        elif self.data_source_mode == constants.DOMESTIC_SOURCE_MODE_DISABLED:
            logger.info(f"步骤1: 在线API已禁用，跳过。")


        if self.is_stop_requested(): logger.info("在线豆瓣API处理后检测到停止信号。"); return processed_cast

        # --------------------------------------------------------------------
        # 步骤 2: 对那些未被本地数据或豆瓣API最终确定的字段进行补充翻译
        # --------------------------------------------------------------------
        logger.info(f"步骤2: 媒体 '{media_name_for_log}' - 对未最终确定的字段进行补充翻译...")
        translation_performed_count = 0
        for idx, actor_data in enumerate(processed_cast): # 遍历的是已经可能被本地或豆瓣数据更新过的列表
            if self.is_stop_requested(): logger.info("翻译循环在步骤2被中断..."); break

            actor_name_original_for_log = actor_data.get("Name", "未知演员") # 用于日志的原始名可能是中文或英文

            # 检查对应索引的 finalized 标记
            finalized_info = actor_field_finalized[idx] if idx < len(actor_field_finalized) else {"name": False, "character": False}

            if not finalized_info.get("name"):
                current_actor_name = actor_data.get("Name")
                translated_actor_name = self._translate_actor_field(current_actor_name, "演员名(补充翻译)", actor_name_original_for_log)
                if translated_actor_name and actor_data.get("Name") != translated_actor_name:
                    actor_data["Name"] = translated_actor_name
                    translation_performed_count += 1
                    finalized_info["name"] = utils.contains_chinese(translated_actor_name) # 如果翻译结果是中文，则标记为最终

            if not finalized_info.get("character"):
                current_actor_character = actor_data.get("Character")
                char_to_translate = utils.clean_character_name_static(current_actor_character) # 清理后再翻译

                translated_role_part = self._translate_actor_field(char_to_translate, "角色名(补充翻译)", actor_name_original_for_log)

                final_character_str = translated_role_part if translated_role_part and translated_role_part.strip() else char_to_translate
                cleaned_final_char = utils.clean_character_name_static(final_character_str) # 再次清理

                if actor_data.get("Character") != cleaned_final_char:
                    actor_data["Character"] = cleaned_final_char
                    translation_performed_count += 1
                    finalized_info["character"] = utils.contains_chinese(cleaned_final_char) # 如果翻译结果是中文，则标记为最终
        if translation_performed_count > 0:
            logger.info(f"步骤2结果：对 {translation_performed_count} 个字段进行了补充翻译。")
        else:
            logger.info("步骤2结果：无需进行补充翻译或所有需翻译字段均失败/已包含中文。")


        # --------------------------------------------------------------------
        # 步骤 3: (可选) 为豆瓣/本地数据中多出（但Emby原始列表没有）的演员查找TMDb ID并尝试添加
        # --------------------------------------------------------------------
        # 这个逻辑会比较复杂，涉及到与Emby原始演员列表的对比和TMDb API的调用
        # 目前可以先简化或跳过，专注于核心的替换和翻译
        logger.info(f"步骤3: 溢出演员处理逻辑已暂时简化/跳过。")


        # --------------------------------------------------------------------
        # 最终去重 (基于 Name 和 Character，或者如果能获取到 Emby Person ID 则基于ID)
        # --------------------------------------------------------------------
        final_unique_cast: List[Dict[str, Any]] = []
        seen_actor_signatures = set() # 用于去重
        for actor in processed_cast:
            # 去重签名可以基于演员名和角色名的小写形式
            # 如果演员有唯一的ID (比如来自本地数据的豆瓣celebrity ID，或未来从TMDb获取的ID)，用ID去重更好
            actor_name_lower = str(actor.get("Name", "")).lower()
            character_name_lower = str(actor.get("Character", "")).lower()
            signature = (actor_name_lower, character_name_lower)

            # 如果这个演员是来自Emby原始列表并且有Emby Person ID，优先用ID去重
            # (但我们目前的替换逻辑可能导致原始Emby ID丢失，需要更精细的合并策略)
            # emby_person_id = actor.get("Id") # 这是Emby Person ID
            # if emby_person_id:
            #     signature = ("emby_id", emby_person_id)

            if signature not in seen_actor_signatures:
                final_unique_cast.append(actor)
                seen_actor_signatures.add(signature)
            else:
                logger.debug(f"去重时跳过重复演员: {actor.get('Name')} - {actor.get('Character')}")

        processed_cast = final_unique_cast
        logger.info(f"演员列表最终处理完成 (去重后)，包含 {len(processed_cast)} 位演员。")
        return processed_cast

    # ... (你的 process_single_item, close 等其他方法保持不变) ...
    # process_single_item 会调用 _process_cast_list


    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False) -> bool:
        if self.is_stop_requested():
            logger.info(f"任务已请求停止，跳过处理 Item ID: {emby_item_id}")
            return False # 返回 False 表示未成功处理

        if not force_reprocess_this_item and emby_item_id in self.processed_items_cache:
            logger.info(f"Item ID '{emby_item_id}' 已处理过且未强制重处理，跳过。")
            return True # 返回 True 表示之前已成功处理

        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error(f"Emby配置不完整，无法处理 Item ID: {emby_item_id}")
            # 记录失败，即使没有 item_name
            self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", "Emby配置不完整", "未知类型")
            return False

        logger.info(f"开始处理单个Emby Item ID: {emby_item_id}")
        item_details = None
        item_name_for_log = f"未知项目(ID:{emby_item_id})" # 默认名
        item_type_for_log = "未知类型" # 默认类型
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
        except Exception as e_get_details:
            logger.error(f"获取Emby项目 {emby_item_id} 详情时发生异常: {e_get_details}", exc_info=True)
            self.save_to_failed_log(emby_item_id, item_name_for_log, f"获取Emby详情异常: {e_get_details}", item_type_for_log)
            return False

        if self.is_stop_requested():
            logger.info(f"停止信号：获取Emby详情后中止 (Item ID: {emby_item_id})")
            return False # 未完成处理
        if not item_details:
            logger.error(f"无法获取Emby项目 {emby_item_id} 的详情，处理中止。")
            self.save_to_failed_log(emby_item_id, item_name_for_log, "无法获取Emby项目详情", item_type_for_log)
            return False

        current_emby_cast_raw = item_details.get("People", [])
        current_cast_internal_format: List[Dict[str, Any]] = []
        for person in current_emby_cast_raw:
            if person.get("Type") == "Actor":
                actor_entry = {
                    "Name": person.get("Name"),
                    "Character": person.get("Role"),
                    "Id": person.get("Id"), # Emby Person ID
                    "OriginalName": person.get("original_name"), # 假设Emby有这个字段
                }
                current_cast_internal_format.append(actor_entry)
        logger.info(f"媒体 '{item_name_for_log}' 原始演员数量 (内部格式): {len(current_cast_internal_format)}")

        if self.is_stop_requested():
            logger.info(f"停止信号：处理演员列表前中止 (Item ID: {emby_item_id})")
            return False
        processed_cast_internal_format = self._process_cast_list(current_cast_internal_format, item_details)

        if self.is_stop_requested():
            logger.info(f"停止信号：更新Emby前中止 (Item ID: {emby_item_id})")
            return False

        cast_for_emby_update: List[Dict[str, Any]] = []
        for actor in processed_cast_internal_format:
            cast_for_emby_update.append({
                "name": actor.get("Name"),
                "character": actor.get("Character"),
                "emby_person_id": actor.get("Id")
            })

        update_success = False
        try:
            update_success = emby_handler.update_emby_item_cast(
                emby_item_id,
                cast_for_emby_update,
                self.emby_url,
                self.emby_api_key,
                self.emby_user_id
            )
        except Exception as e_update_cast:
            logger.error(f"更新Emby项目 {emby_item_id} 演员信息时发生异常: {e_update_cast}", exc_info=True)
            self.save_to_failed_log(emby_item_id, item_name_for_log, f"更新Emby演员异常: {e_update_cast}", item_type_for_log)
            return False


        if update_success:
            logger.info(f"Emby项目 {emby_item_id} ('{item_name_for_log}') 演员信息更新成功。")
            self.save_to_processed_log(emby_item_id, item_name_for_log)
            if self.config.get("refresh_emby_after_update", True):
                if self.is_stop_requested():
                    logger.info(f"停止信号：刷新Emby元数据前中止 (Item ID: {emby_item_id})")
                    return True # 更新已成功，只是刷新被中止
                logger.info(f"准备为项目 {emby_item_id} 触发Emby元数据刷新...")
                try:
                    emby_handler.refresh_emby_item_metadata(
                        item_emby_id=emby_item_id,
                        emby_server_url=self.emby_url,
                        emby_api_key=self.emby_api_key,
                        recursive=(item_details.get("Type") == "Series"),
                    )
                except Exception as e_refresh:
                     logger.error(f"刷新Emby元数据失败 for {emby_item_id}: {e_refresh}", exc_info=True)
            return True
        else:
            logger.error(f"Emby项目 {emby_item_id} ('{item_name_for_log}') 演员信息更新失败 (API调用返回失败)。")
            self.save_to_failed_log(emby_item_id, item_name_for_log, "更新Emby演员信息失败 (API返回失败)", item_type_for_log)
            return False


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