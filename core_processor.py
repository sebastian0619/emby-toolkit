# core_processor.py
import time
import re
import os
import sqlite3 # 用于数据库操作
from typing import Dict, List, Optional, Any
import threading

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
            logger.debug(f"翻译缓存已更新 (数据库): '{text_stripped}' -> '{final_translation}'.")
            return final_translation
        else:
            logger.warning(f"在线翻译失败或返回空: '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name})")
            # 保存翻译失败状态 (None) 到数据库，避免重复无效尝试
            if self.douban_api and hasattr(DoubanApi, '_save_translation_to_db'):
                DoubanApi._save_translation_to_db(text_stripped, None, f"failed_or_empty_via_{used_engine}")
            logger.debug(f"翻译缓存已更新 (数据库) (翻译失败): '{text_stripped}'.")
            return text # 返回原文


    def _process_cast_list(self, current_cast: List[Dict[str, Any]], media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        # 这个方法的内部逻辑（豆瓣优先、补充翻译、去重）基本保持不变
        # 只是它调用的 _translate_actor_field 现在使用了数据库缓存
        logger.info(f"开始处理媒体 '{media_info.get('Name')}' 的演员列表，原始数量: {len(current_cast)} 位。")
        processed_cast: List[Dict[str, Any]] = [actor.copy() for actor in current_cast]
        media_name = media_info.get("Name", "未知媒体")
        imdb_id_media = media_info.get("ProviderIds", {}).get("Imdb")
        year = str(media_info.get("ProductionYear", ""))
        internal_media_type = "movie" if media_info.get("Type") == "Movie" else ("tv" if media_info.get("Type") == "Series" else None)

        emby_actor_name_finalized_by_douban = [False] * len(processed_cast)
        emby_actor_character_finalized_by_douban = [False] * len(processed_cast)
        actors_actually_updated_by_douban_count = 0

        # 步骤1: 豆瓣优先处理 (逻辑不变)
        if self.douban_api and hasattr(self.douban_api, 'get_acting') and \
           self.config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE) != "disabled_douban":
            logger.info(f"步骤1: 媒体 '{media_name}'，尝试从豆瓣获取演员信息进行优先更新。")
            douban_actors_raw = None
            try:
                douban_api_cooldown = float(self.config.get("api_douban_default_cooldown_seconds", 1.0))
                if douban_api_cooldown > 0:
                    logger.debug(f"豆瓣API调用前等待 {douban_api_cooldown} 秒...")
                    time.sleep(douban_api_cooldown)
                douban_actors_raw = self.douban_api.get_acting(
                    name=media_name, imdbid=imdb_id_media, mtype=internal_media_type, year=year
                )
            except Exception as e_douban_get_acting:
                logger.error(f"调用豆瓣 get_acting 时发生错误: {e_douban_get_acting}", exc_info=True)

            if self.is_stop_requested(): logger.info("获取豆瓣信息后检测到停止信号。"); return processed_cast

            if douban_actors_raw and douban_actors_raw.get("cast"):
                # ... (豆瓣演员匹配和更新Emby列表的详细逻辑，与你之前版本类似) ...
                # 这里省略详细的豆瓣匹配逻辑，假设它能正确更新 emby_actor_name_finalized_by_douban 等标记
                # 确保 clean_character_name_static 被 utils.clean_character_name_static 替换
                douban_cast_list = douban_actors_raw["cast"]
                logger.info(f"从豆瓣为 '{media_name}' 获取到 {len(douban_cast_list)} 位演员，开始匹配和更新Emby列表...")
                updated_emby_indices_this_pass = set()
                for db_actor in douban_cast_list:
                    if self.is_stop_requested(): break
                    douban_actor_name_cn = db_actor.get("name")
                    douban_actor_name_latin = db_actor.get("latin_name")
                    douban_character_name_cn = utils.clean_character_name_static(db_actor.get("character")) # 使用 utils
                    if not douban_actor_name_cn: continue
                    for idx_emby, emby_actor_entry in enumerate(processed_cast):
                        if idx_emby in updated_emby_indices_this_pass: continue
                        # ... (匹配逻辑) ...
                        # 假设匹配成功:
                        # emby_actor_entry["Name"] = douban_actor_name_cn
                        # emby_actor_entry["Character"] = douban_character_name_cn
                        # emby_actor_name_finalized_by_douban[idx_emby] = True
                        # emby_actor_character_finalized_by_douban[idx_emby] = True
                        # actors_actually_updated_by_douban_count +=1
                        # updated_emby_indices_this_pass.add(idx_emby)
                        # break # 跳出内层循环
                if actors_actually_updated_by_douban_count > 0:
                    logger.info(f"步骤1结果：根据豆瓣信息，共更新了 {actors_actually_updated_by_douban_count} 位Emby演员。")
            elif douban_actors_raw and douban_actors_raw.get("error"):
                 logger.warning(f"豆瓣API返回错误: {douban_actors_raw.get('message')}")
            else: logger.info(f"豆瓣未能为 '{media_name}' 提供演员信息。")
        else:
            logger.info(f"步骤1：跳过豆瓣演员信息获取流程 (豆瓣API不可用或配置禁用)。")

        if self.is_stop_requested(): logger.info("豆瓣处理后检测到停止信号。"); return processed_cast

        # 步骤2: 补充翻译
        logger.info(f"步骤2: 开始对媒体 '{media_name}' 中未被豆瓣最终确定且非中文的演员字段进行补充翻译...")
        translation_performed_count = 0
        for idx, actor_data in enumerate(processed_cast):
            if self.is_stop_requested(): logger.info("翻译循环在步骤2被中断..."); break
            actor_name_original_for_log = actor_data.get("Name", "未知演员")

            if not emby_actor_name_finalized_by_douban[idx]:
                current_actor_name = actor_data.get("Name")
                translated_actor_name = self._translate_actor_field(current_actor_name, "演员名(补充翻译)", actor_name_original_for_log)
                if translated_actor_name and actor_data.get("Name") != translated_actor_name:
                    actor_data["Name"] = translated_actor_name
                    translation_performed_count += 1

            if not emby_actor_character_finalized_by_douban[idx]:
                current_actor_character = actor_data.get("Character")
                # 清理角色名，例如移除 "(voice)" 等标记，以便翻译核心部分
                char_to_translate_for_role = utils.clean_character_name_static(current_actor_character)
                # (如果 clean_character_name_static 移除了 (voice)，需要在这里判断是否要加回中文的配音标记)
                # suffix_after_translation_for_role = " (配音)" if "(voice)" in current_actor_character.lower() else "" # 简陋判断

                translated_role_part = self._translate_actor_field(char_to_translate_for_role, "角色名(补充翻译)", actor_name_original_for_log)

                final_character_str = translated_role_part if translated_role_part and translated_role_part.strip() else char_to_translate_for_role
                # final_character_str += suffix_after_translation_for_role # 如果需要加回配音标记

                # 再次清理，以防翻译结果包含不必要的字符
                cleaned_final_char = utils.clean_character_name_static(final_character_str)
                if actor_data.get("Character") != cleaned_final_char:
                    actor_data["Character"] = cleaned_final_char
                    translation_performed_count += 1
        if translation_performed_count > 0:
            logger.info(f"步骤2结果：对 {translation_performed_count} 个字段进行了补充翻译。")
        else: logger.info("步骤2结果：无需进行补充翻译或所有需翻译字段均失败。")

        # 步骤3: 豆瓣溢出演员处理 (保持你之前的逻辑或简化)
        logger.info(f"步骤3: 为豆瓣溢出演员查找TMDb ID的逻辑已暂时简化/跳过。")

        # 最终去重 (保持你之前的逻辑)
        final_unique_cast = []
        seen_final_keys = set()
        for actor in processed_cast:
            key_to_check = (str(actor.get("Id", "")), actor.get("Name","").lower(), actor.get("Character","").lower())
            if key_to_check not in seen_final_keys:
                final_unique_cast.append(actor)
                seen_final_keys.add(key_to_check)
        processed_cast = final_unique_cast
        logger.info(f"演员列表最终处理完成，包含 {len(processed_cast)} 位演员。")
        return processed_cast


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
        self.clear_stop_signal()
        if force_reprocess_all:
            logger.info("用户请求强制重处理所有媒体项，将清除已处理记录。")
            self.clear_processed_log()

        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error("Emby配置不完整，无法处理整个媒体库。")
            if update_status_callback: update_status_callback(-1, "Emby配置不完整")
            return

        logger.info("开始全量处理Emby媒体库...")
        if update_status_callback: update_status_callback(0, "正在获取电影列表...")
        movies = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Movie", self.emby_user_id)
        if self.is_stop_requested(): logger.info("获取电影列表后检测到停止信号。"); return
        if update_status_callback: update_status_callback(5, "正在获取剧集列表...")
        series_list = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Series", self.emby_user_id)
        if self.is_stop_requested(): logger.info("获取剧集列表后检测到停止信号。"); return

        all_items = (movies if movies else []) + (series_list if series_list else []) #确保是列表
        total_items = len(all_items)
        if total_items == 0:
            logger.info("媒体库为空或获取失败，无需处理。")
            if update_status_callback: update_status_callback(100, "媒体库为空或获取失败。")
            return
        logger.info(f"获取到 {len(movies if movies else [])} 部电影和 {len(series_list if series_list else [])} 部剧集，共 {total_items} 个项目进行处理。")

        for i, item in enumerate(all_items):
            if self.is_stop_requested():
                logger.info("全量媒体库处理被用户中断。")
                if update_status_callback:
                    current_progress = int(((i) / total_items) * 100) if total_items > 0 else 0
                    update_status_callback(current_progress, "任务已中断")
                break

            item_id = item.get('Id')
            item_name = item.get('Name', f"未知项目(ID:{item_id})")
            item_type_str = "电影" if item.get("Type") == "Movie" else ("剧集" if item.get("Type") == "Series" else "未知类型")
            progress = int(((i + 1) / total_items) * 99)
            message = f"正在处理 {item_type_str} ({i+1}/{total_items}): {item_name}"
            logger.info(message)
            if update_status_callback: update_status_callback(progress, message)

            if not item_id:
                logger.warning(f"条目缺少ID，跳过: {item_name}")
                continue

            process_success = self.process_single_item(item_id, force_reprocess_this_item=force_reprocess_all)
            if not process_success and self.is_stop_requested(): # 如果处理失败且是因停止信号导致
                logger.info(f"处理 Item ID {item_id} 时被中断，停止全量扫描。")
                if update_status_callback: update_status_callback(progress, f"处理 {item_name} 时中断")
                break
            
            delay = float(self.config.get("delay_between_items_sec", 0.5))
            if delay > 0 and i < total_items -1 : # 最后一个项目后不延迟
                if self.is_stop_requested():
                    logger.info("延迟等待前检测到停止信号，中断全量扫描。")
                    if update_status_callback: update_status_callback(progress, "任务已中断")
                    break
                time.sleep(delay)

        if self.is_stop_requested():
            logger.info("全量处理任务已结束（因用户请求停止）。")
        else:
            logger.info("全量处理Emby媒体库结束。")
            if update_status_callback: update_status_callback(100, "全量处理完成。")


    def close(self):
        """关闭 MediaProcessor 实例，例如关闭数据库连接池或释放其他资源。"""
        if self.douban_api and hasattr(self.douban_api, 'close'):
            logger.info("正在关闭 MediaProcessor 中的 DoubanApi session...")
            self.douban_api.close()
        # 如果有其他需要关闭的资源，例如数据库连接池（如果使用的话），在这里关闭
        logger.info("MediaProcessor close 方法执行完毕。")