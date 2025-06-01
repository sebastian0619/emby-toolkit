# core_processor.py

import time
import re 
import os # 确保 os 被导入
from typing import Dict, List, Optional, Any
import threading

import emby_handler 
from utils import translate_text_with_translators, contains_chinese, clean_character_name_static
from logger_setup import logger
import constants

PERSISTENT_DATA_PATH = "/config"

# --- 获取基础路径，用于定位配置文件等 ---
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError: 
    BASE_DIR = os.getcwd()
# --- 基础路径结束 ---

try:
    from douban import DoubanApi
    DOUBAN_API_AVAILABLE = True
    logger.info("DoubanApi 模块已成功导入到 core_processor。")
except ImportError:
    logger.error("错误: douban.py 文件未找到或 DoubanApi 类无法导入 (core_processor)。")
    DOUBAN_API_AVAILABLE = False
    class DoubanApi:
        def __init__(self, *args, **kwargs):
            logger.warning("使用的是假的 DoubanApi 实例 (core_processor)。")
        def get_acting(self, *args, **kwargs):
            logger.warning("假的 DoubanApi.get_acting 被调用，返回空。")
            return {"error": "DoubanApi not available", "cast": []}
        def close(self):
            logger.warning("假的 DoubanApi.close 被调用。")
            pass

class MediaProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.douban_api = None
        self.processed_log_file = os.path.join(PERSISTENT_DATA_PATH, constants.PROCESSED_MEDIA_LOG_FILE)
        if DOUBAN_API_AVAILABLE: 
            try:
                self.douban_api = DoubanApi()
                logger.info("DoubanApi 实例已在 MediaProcessor 中创建。")
            except Exception as e:
                logger.error(f"MediaProcessor 初始化 DoubanApi 失败: {e}")
        else:
            logger.warning("DoubanApi 在 MediaProcessor 中不可用 (将使用假的实例或无实例)。")
            if not hasattr(self, 'douban_api') or self.douban_api is None:
                self.douban_api = DoubanApi()

        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.translator_engines = self.config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        self.domestic_source_mode = self.config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)
        
        self._stop_event = threading.Event()
        
        # 已处理记录相关
        self.processed_log_file = os.path.join(PERSISTENT_DATA_PATH, constants.PROCESSED_MEDIA_LOG_FILE)
        self.processed_items_cache = self._load_processed_log()
        logger.info(f"已加载 {len(self.processed_items_cache)} 个已处理媒体记录。")

        logger.info(f"MediaProcessor 初始化完成。Emby URL: {self.emby_url}, UserID: {self.emby_user_id}")
        logger.debug(f"  翻译引擎顺序: {self.translator_engines}")
        logger.debug(f"  国产片豆瓣策略(现统一处理，此配置控制豆瓣API使用): {self.domestic_source_mode}")


    def signal_stop(self):
        logger.info("MediaProcessor 收到停止信号。")
        self._stop_event.set()

    def clear_stop_signal(self):
        self._stop_event.clear()
        logger.debug("MediaProcessor 停止信号已清除。")

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _load_processed_log(self) -> set:
        log_set = set()
        log_dir = os.path.dirname(self.processed_log_file)
        if not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
                logger.info(f"创建持久化数据目录 (用于加载日志): {log_dir}")
            except Exception as e:
                logger.error(f"创建持久化数据目录 '{log_dir}' 失败: {e}")
                return log_set # 如果目录创建失败，直接返回空集合
        if os.path.exists(self.processed_log_file):
            try:
                with open(self.processed_log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped_line = line.strip()
                        if stripped_line:
                            log_set.add(stripped_line)
            except Exception as e:
                logger.error(f"读取已处理记录文件 '{self.processed_log_file}' 失败: {e}")
        return log_set

    def save_to_processed_log(self, item_id: str):
        if item_id in self.processed_items_cache:
            return
        try:
            with open(self.processed_log_file, 'a', encoding='utf-8') as f:
                f.write(f"{item_id}\n")
            self.processed_items_cache.add(item_id)
            logger.debug(f"Item ID '{item_id}' 已添加到已处理记录。")
        except Exception as e:
            logger.error(f"保存已处理记录到 '{self.processed_log_file}' 失败: {e}")

    def clear_processed_log(self):
        try:
            if os.path.exists(self.processed_log_file):
                os.remove(self.processed_log_file)
            self.processed_items_cache.clear()
            logger.info("已处理记录已清除（用于强制重处理）。")
        except Exception as e:
            logger.error(f"清除已处理记录文件 '{self.processed_log_file}' 失败: {e}")

    def _translate_actor_field(self, text: Optional[str], field_name: str, actor_name_for_log: str) -> Optional[str]:
        if self.is_stop_requested(): return text

        if not text or not text.strip() or contains_chinese(text):
            return text 

        text_stripped = text.strip()
        if len(text_stripped) == 1 and 'A' <= text_stripped <= 'Z':
            logger.debug(f"字段 '{field_name}' ({text_stripped}) 为单大写字母，跳过翻译 (演员: {actor_name_for_log})。")
            return text
        if len(text_stripped) == 2 and text_stripped.isupper() and text_stripped.isalpha():
            logger.debug(f"字段 '{field_name}' ({text_stripped}) 为双大写字母，跳过翻译 (演员: {actor_name_for_log})。")
            return text
        
        if self.douban_api and hasattr(DoubanApi, '_translation_cache') and text_stripped in DoubanApi._translation_cache:
            cached_translation = DoubanApi._translation_cache[text_stripped]
            if cached_translation and cached_translation.strip():
                logger.info(f"翻译缓存命中 for '{text_stripped}' -> '{cached_translation}' (演员: {actor_name_for_log}, 字段: {field_name})")
                return cached_translation
            else:
                logger.info(f"翻译缓存命中 (空值) for '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name})，将使用原文。")
                return text

        logger.info(f"准备在线翻译字段 '{field_name}': '{text_stripped}' (演员: {actor_name_for_log})")
        translated = translate_text_with_translators(text_stripped, engine_order=self.translator_engines)
        
        if translated and translated.strip():
            logger.info(f"在线翻译成功: '{text_stripped}' -> '{translated}' (演员: {actor_name_for_log}, 字段: {field_name})")
            if self.douban_api and hasattr(DoubanApi, '_translation_cache'):
                DoubanApi._translation_cache[text_stripped] = translated.strip()
                logger.debug(f"翻译缓存已更新: '{text_stripped}' -> '{translated.strip()}'. 当前缓存大小: {len(DoubanApi._translation_cache)}")
            return translated.strip()
        else:
            logger.warning(f"在线翻译失败或返回空: '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name})")
            if self.douban_api and hasattr(DoubanApi, '_translation_cache'):
                 DoubanApi._translation_cache[text_stripped] = None
                 logger.debug(f"翻译缓存已更新 (None): '{text_stripped}'. 当前缓存大小: {len(DoubanApi._translation_cache)}")
            return text

    def _process_cast_list(self, current_cast: List[Dict[str, Any]], media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info(f"开始处理媒体 '{media_info.get('Name')}' 的演员列表，原始数量: {len(current_cast)} 位。")
        
        processed_cast: List[Dict[str, Any]] = [actor.copy() for actor in current_cast]
        
        media_name = media_info.get("Name")
        # media_type_str = media_info.get("Type") # 暂时未使用
        imdb_id_media = media_info.get("ProviderIds", {}).get("Imdb")
        year = str(media_info.get("ProductionYear", ""))
        internal_media_type = "movie" if media_info.get("Type") == "Movie" else ("tv" if media_info.get("Type") == "Series" else None)

        # 标记哪些Emby演员的字段被豆瓣成功提供了“最终”中文版本，从而无需后续翻译
        emby_actor_name_finalized_by_douban = [False] * len(processed_cast)
        emby_actor_character_finalized_by_douban = [False] * len(processed_cast)
        
        actors_actually_updated_by_douban_count = 0
        
        # 步骤1: (豆瓣优先) 尝试从豆瓣获取演员信息并直接更新匹配的Emby演员
        if self.douban_api and hasattr(self.douban_api, 'get_acting') and callable(self.douban_api.get_acting) and \
           self.config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE) != "disabled_douban":
            logger.info(f"步骤1: 媒体 '{media_name}'，尝试从豆瓣获取演员信息进行优先更新。")
            
            douban_actors_raw = None
            try:
                douban_api_cooldown = float(self.config.get("api_douban_default_cooldown_seconds", 1.0))
                logger.debug(f"豆瓣API调用前等待 {douban_api_cooldown} 秒...")
                time.sleep(douban_api_cooldown)
                douban_actors_raw = self.douban_api.get_acting(
                    name=media_name, imdbid=imdb_id_media, mtype=internal_media_type, year=year
                )
            except Exception as e_douban_get_acting:
                logger.error(f"调用豆瓣 get_acting 时发生错误: {e_douban_get_acting}")

            if self.is_stop_requested(): logger.info("获取豆瓣信息后检测到停止信号。"); return processed_cast

            if douban_actors_raw and douban_actors_raw.get("cast"):
                douban_cast_list = douban_actors_raw["cast"]
                logger.info(f"从豆瓣为 '{media_name}' 获取到 {len(douban_cast_list)} 位演员，开始匹配和更新Emby列表...")
                
                updated_emby_indices_this_pass = set()

                for db_actor in douban_cast_list:
                    if self.is_stop_requested(): logger.info("豆瓣演员匹配循环被中断..."); break
                    
                    douban_actor_name_cn = db_actor.get("name")
                    douban_actor_name_latin = db_actor.get("latin_name")
                    douban_character_name_cn = clean_character_name_static(db_actor.get("character"))

                    if not douban_actor_name_cn: continue

                    for idx_emby, emby_actor_entry in enumerate(processed_cast):
                        if idx_emby in updated_emby_indices_this_pass: continue

                        emby_actor_name = emby_actor_entry.get("Name")
                        emby_actor_name_original_or_latin = emby_actor_entry.get("OriginalName")
                        if not emby_actor_name_original_or_latin and emby_actor_name and not contains_chinese(emby_actor_name):
                            emby_actor_name_original_or_latin = emby_actor_name
                        
                        match_found = False; match_type = ""
                        if douban_actor_name_cn and emby_actor_name and douban_actor_name_cn.strip().lower() == emby_actor_name.strip().lower():
                            match_found = True; match_type = "豆瓣中文名与Emby名"
                        if not match_found and douban_actor_name_latin and emby_actor_name_original_or_latin and douban_actor_name_latin.strip().lower() == emby_actor_name_original_or_latin.strip().lower():
                            match_found = True; match_type = "豆瓣外文名与Emby外文名/名"
                        
                        if match_found:
                            logger.info(f"  豆瓣优先更新匹配 ({match_type}): 豆瓣演员 '{douban_actor_name_cn}' -> Emby演员 '{emby_actor_name}'")
                            
                            current_emby_name = emby_actor_entry.get("Name", "")
                            current_emby_character = emby_actor_entry.get("Character", "")
                            name_field_changed = False; character_field_changed = False

                            if douban_actor_name_cn and current_emby_name != douban_actor_name_cn:
                                emby_actor_entry["Name"] = douban_actor_name_cn; name_field_changed = True
                                logger.info(f"    演员名被豆瓣更新: '{current_emby_name}' -> '{douban_actor_name_cn}'")
                            emby_actor_name_finalized_by_douban[idx_emby] = True

                            if douban_character_name_cn and douban_character_name_cn != "演员":
                                if current_emby_character != douban_character_name_cn:
                                    emby_actor_entry["Character"] = douban_character_name_cn; character_field_changed = True
                                    logger.info(f"    角色名被豆瓣具体中文更新: '{current_emby_character}' -> '{douban_character_name_cn}'")
                                emby_actor_character_finalized_by_douban[idx_emby] = True
                            elif douban_character_name_cn == "演员":
                                # 如果Emby当前角色是空的或者也是“演员”，或者是非中文，才考虑用豆瓣的“演员”
                                emby_char_cn_part = current_emby_character
                                match_emby_fmt = re.match(r"^(.*?)\s*（.*）$", current_emby_character)
                                if match_emby_fmt: emby_char_cn_part = match_emby_fmt.group(1).strip()
                                
                                if not emby_char_cn_part or emby_char_cn_part == "演员" or not contains_chinese(emby_char_cn_part):
                                    if current_emby_character != "演员": # 避免不必要的更新
                                        emby_actor_entry["Character"] = "演员"; character_field_changed = True
                                        logger.info(f"    角色名被豆瓣更新为通用'演员' (原Emby: '{current_emby_character}')")
                                    emby_actor_character_finalized_by_douban[idx_emby] = True # 即使是“演员”，也算豆瓣提供了信息
                                else: # Emby有更具体的中文，豆瓣是“演员”，则保留Emby的，标记为不由豆瓣最终确定
                                    emby_actor_character_finalized_by_douban[idx_emby] = False
                                    logger.debug(f"    豆瓣角色为'演员', Emby角色'{current_emby_character}'更具体，后续可能翻译。")
                            else: 
                                emby_actor_character_finalized_by_douban[idx_emby] = False
                            
                            if name_field_changed or character_field_changed:
                                actors_actually_updated_by_douban_count +=1
                            
                            updated_emby_indices_this_pass.add(idx_emby)
                            break 
                
                if actors_actually_updated_by_douban_count > 0:
                    logger.info(f"步骤1结果：根据豆瓣信息，共更新了 {actors_actually_updated_by_douban_count} 位Emby演员。")
                # ... (其他日志)
            # ... (豆瓣API调用失败的日志)
        else: 
            logger.info(f"步骤1：跳过豆瓣演员信息获取流程。")

        if self.is_stop_requested(): logger.info("豆瓣处理后检测到停止信号。"); return processed_cast

        # 步骤2: 对那些未被豆瓣成功提供最终中文名/角色名的字段，且本身非中文的，进行翻译
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
                    translation_performed_count +=1
            
            if not emby_actor_character_finalized_by_douban[idx]:
                current_actor_character = actor_data.get("Character")
                char_to_translate_for_role = current_actor_character
                suffix_after_translation_for_role = ""

                if current_actor_character and current_actor_character.strip():
                    role_stripped = current_actor_character.strip()
                    voice_match = re.search(r'\s*\((voice)\)\s*$', role_stripped, re.IGNORECASE) 
                    if voice_match:
                        char_to_translate_for_role = role_stripped[:voice_match.start()].strip()
                        suffix_after_translation_for_role = " (配音)"
                
                translated_role_part = self._translate_actor_field(char_to_translate_for_role, "角色名(补充翻译)", actor_name_original_for_log)
                
                final_character_str = ""
                if translated_role_part and translated_role_part.strip():
                    final_character_str = translated_role_part.strip() + suffix_after_translation_for_role
                elif suffix_after_translation_for_role:
                    final_character_str = suffix_after_translation_for_role.strip()
                else:
                    final_character_str = current_actor_character if current_actor_character else ""
                
                cleaned_final_char = clean_character_name_static(final_character_str)
                if actor_data.get("Character") != cleaned_final_char:
                    actor_data["Character"] = cleaned_final_char
                    translation_performed_count += 1
        
        if translation_performed_count > 0:
            logger.info(f"步骤2结果：对 {translation_performed_count} 个未被豆瓣最终确定的字段进行了补充翻译。")
        else:
            logger.info("步骤2结果：无需进行补充翻译。")

        # 步骤3: (暂时注释掉或简化) 为豆瓣溢出演员查找TMDb ID并尝试添加
        logger.info(f"步骤3: 为豆瓣溢出演员查找TMDb ID的逻辑已暂时简化/跳过以确保稳定性。")
        # if self.douban_api and ... and self.config.get("tmdb_api_key"):
            # ... (复杂的溢出演员处理逻辑，我们先注释掉这整块) ...
        
        # 最终去重（这个去重逻辑可能也需要审视，但我们先保持）
        final_unique_cast = []
        seen_final_keys = set()
        for actor in processed_cast:
            key_to_check = None
            # 优先使用Emby Person ID去重，因为它在单个媒体项内应该是唯一的
            if actor.get("Id"): 
                key_to_check = ("emby_person_id", str(actor.get("Id")))
            elif actor.get("tmdb_person_id"): # 其次用TMDb ID
                key_to_check = ("tmdb_id", str(actor.get("tmdb_person_id")))
            else: # 最后用名字+角色
                key_to_check = ("name_char", actor.get("Name","").lower(), actor.get("Character","").lower())
            
            if key_to_check not in seen_final_keys:
                final_unique_cast.append(actor)
                seen_final_keys.add(key_to_check)
            else:
                logger.debug(f"最终去重时跳过演员: {actor.get('Name')} (Key: {key_to_check})")
        
        processed_cast = final_unique_cast
        logger.info(f"演员列表最终处理完成，包含 {len(processed_cast)} 位演员。")
        return processed_cast

    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False) -> bool:
        if self.is_stop_requested():
            logger.info(f"任务已请求停止，跳过处理 Item ID: {emby_item_id}")
            return False

        if not force_reprocess_this_item and emby_item_id in self.processed_items_cache:
            logger.info(f"Item ID '{emby_item_id}' 已处理过且未强制重处理，跳过。")
            return True 

        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error(f"Emby配置不完整，无法处理 Item ID: {emby_item_id}")
            return False
            
        logger.info(f"开始处理单个Emby Item ID: {emby_item_id}")

        item_details = emby_handler.get_emby_item_details(
            emby_item_id, 
            self.emby_url, 
            self.emby_api_key, 
            self.emby_user_id
        )

        if self.is_stop_requested(): logger.info(f"停止信号：获取Emby详情后中止 (Item ID: {emby_item_id})"); return False
        if not item_details:
            logger.error(f"无法获取Emby项目 {emby_item_id} 的详情，处理中止。")
            return False

        current_emby_cast_raw = item_details.get("People", [])
        current_cast_internal_format: List[Dict[str, Any]] = []
        for person in current_emby_cast_raw:
            if person.get("Type") == "Actor":
                logger.debug(f"  原始Emby Person对象: {person}")
                actor_entry = {
                    "Name": person.get("Name"),
                    "Character": person.get("Role"), # Emby的角色名
                    "Id": person.get("Id"), # Emby Person ID
                    # 从Emby Person对象中提取尽可能多的信息
                    "adult": person.get("adult", False),
                    "gender": person.get("gender"),
                    "known_for_department": person.get("known_for_department", "Acting"),
                    "OriginalName": person.get("original_name"), # 原始外文名
                    "popularity": person.get("popularity", 0.0),
                    "profile_path": person.get("profile_path"), # 头像路径
                    "credit_id": person.get("credit_id"),
                    "department": person.get("department"),
                    "job": person.get("job"),
                    "_source": "emby_original" # 标记来源
                    # "tmdb_person_id": person.get("ProviderIds", {}).get("Tmdb"), # 移除，不再尝试从这里获取
                    # 我们将通过名称搜索来获取TMDb ID
                }
                current_cast_internal_format.append(actor_entry)
        
        logger.info(f"媒体 '{item_details.get('Name')}' 原始演员数量 (内部格式): {len(current_cast_internal_format)}")

        if self.is_stop_requested(): logger.info(f"停止信号：处理演员列表前中止 (Item ID: {emby_item_id})"); return False
        processed_cast_internal_format = self._process_cast_list(current_cast_internal_format, item_details)
        
        if self.is_stop_requested(): logger.info(f"停止信号：更新Emby前中止 (Item ID: {emby_item_id})"); return False

        cast_for_emby_update: List[Dict[str, Any]] = []
        for actor in processed_cast_internal_format:
            cast_for_emby_update.append({
                "name": actor.get("Name"),
                "character": actor.get("Character"),
                "emby_person_id": actor.get("Id") 
            })

        update_success = emby_handler.update_emby_item_cast(
            emby_item_id, 
            cast_for_emby_update, 
            self.emby_url, 
            self.emby_api_key,
            self.emby_user_id
        )

        if update_success:
            logger.info(f"Emby项目 {emby_item_id} ('{item_details.get('Name')}') 演员信息更新成功。")
            self.save_to_processed_log(emby_item_id)
            
            if self.config.get("refresh_emby_after_update", True):
                if self.is_stop_requested(): 
                    logger.info(f"停止信号：刷新Emby元数据前中止 (Item ID: {emby_item_id})")
                    return True 
                
                logger.info(f"准备为项目 {emby_item_id} 触发Emby元数据刷新 (确保更改生效)...")
                emby_handler.refresh_emby_item_metadata(
                    item_emby_id=emby_item_id, 
                    emby_server_url=self.emby_url, 
                    emby_api_key=self.emby_api_key,
                    recursive= (item_details.get("Type") == "Series"),
                    metadata_refresh_mode="Default", 
                    image_refresh_mode="Default",    
                    replace_all_metadata_param=False, 
                    replace_all_images_param=False
                )
            return True
        else:
            logger.error(f"Emby项目 {emby_item_id} ('{item_details.get('Name')}') 演员信息更新失败。")
            return False

    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False):
        self.clear_stop_signal() 

        if force_reprocess_all:
            logger.info("用户请求强制重处理所有媒体项，将清除已处理记录。")
            self.clear_processed_log()
        
        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error("Emby配置不完整，无法处理整个媒体库。")
            if update_status_callback:
                update_status_callback(-1, "Emby配置不完整")
            return
            
        logger.info("开始全量处理Emby媒体库...")
        if update_status_callback:
            update_status_callback(0, "正在获取电影列表...")
        
        movies = emby_handler.get_emby_library_items(
            self.emby_url, self.emby_api_key, 
            media_type_filter="Movie", 
            user_id=self.emby_user_id
        )
        if self.is_stop_requested(): logger.info("获取电影列表后检测到停止信号。"); return

        if update_status_callback:
            update_status_callback(5, "正在获取剧集列表...")
        series_list = emby_handler.get_emby_library_items(
            self.emby_url, self.emby_api_key, 
            media_type_filter="Series", 
            user_id=self.emby_user_id
        )
        if self.is_stop_requested(): logger.info("获取剧集列表后检测到停止信号。"); return
        
        all_items = movies + series_list
        total_items = len(all_items)
        if total_items == 0:
            logger.info("媒体库为空，无需处理。")
            if update_status_callback:
                update_status_callback(100, "媒体库为空，处理完成。")
            return

        logger.info(f"获取到 {len(movies)} 部电影和 {len(series_list)} 部剧集，共 {total_items} 个项目进行处理。")
        
        for i, item in enumerate(all_items):
            if self.is_stop_requested():
                logger.info("全量媒体库处理被用户中断。")
                if update_status_callback:
                    current_progress = int(((i) / total_items) * 100) if total_items > 0 else 0
                    update_status_callback(current_progress, "任务已中断")
                break 

            item_id = item.get('Id')
            item_name = item.get('Name', '未知项目')
            item_type_str = "电影" if item.get("Type") == "Movie" else ("剧集" if item.get("Type") == "Series" else "未知类型")
            
            progress = int(((i + 1) / total_items) * 99) 
            message = f"正在处理 {item_type_str} ({i+1}/{total_items}): {item_name}"
            logger.info(message)
            if update_status_callback:
                update_status_callback(progress, message)

            if not item_id:
                logger.warning(f"条目缺少ID，跳过: {item_name}")
                continue
            
            process_success = self.process_single_item(item_id, force_reprocess_this_item=force_reprocess_all)
            if not process_success and self.is_stop_requested():
                logger.info(f"处理 Item ID {item_id} 时被中断，停止全量扫描。")
                if update_status_callback:
                     update_status_callback(progress, f"处理 {item_name} 时中断")
                break
            
            delay = float(self.config.get("delay_between_items_sec", 0.5))
            if delay > 0:
                if self.is_stop_requested():
                    logger.info("延迟等待前检测到停止信号，中断全量扫描。")
                    if update_status_callback: update_status_callback(progress, "任务已中断")
                    break
                time.sleep(delay)
            
        if self.is_stop_requested():
            logger.info("全量处理任务已结束（因用户请求停止）。")
        else:
            logger.info("全量处理Emby媒体库结束。")
            if update_status_callback:
                update_status_callback(100, "全量处理完成。")

    def close(self):
        logger.info("MediaProcessor.close() 方法开始执行。") # <-- 新增日志
        if self.douban_api and hasattr(self.douban_api, 'close'):
            logger.info("MediaProcessor.close(): 准备调用 self.douban_api.close()") # <-- 新增日志
            self.douban_api.close() # <--- 核心调用
            logger.info("MediaProcessor.close(): self.douban_api.close() 调用完毕。") # <-- 新增日志
        else:
            logger.info("MediaProcessor.close(): Douban API 实例不存在或没有 close 方法。") # <-- 新增日志
        logger.info("MediaProcessor close 方法执行完毕。")

# if __name__ == '__main__':
#     mock_config = {
#         "emby_server_url": "http://192.168.31.163:8096",
#         "emby_api_key": "eaa73b828ac04b1bb6d3687a0117572c", 
#         "emby_user_id": "e274948e690043c9a86c9067ead73af4",
#         "translator_engines_order": ["bing", "youdao"], 
#         "domestic_source_mode": constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, 
#         "delay_between_items_sec": 0.5, 
#         "refresh_emby_after_update": True, 
#         "api_douban_default_cooldown_seconds": 1.0,
#         "tmdb_api_key": "42985eb2e0cbdf2b2c88f2f30990be40"
#     }

#     if mock_config["emby_user_id"] == "YOUR_EMBY_USER_ID_REPLACE_ME" or \
#        mock_config["emby_api_key"] == "YOUR_EMBY_API_KEY_PLACEHOLDER":
#         logger.error("错误：请在脚本中修改 mock_config 和文件顶部的占位符为您的真实Emby配置！")
#     else:
#         processor = MediaProcessor(config=mock_config)

#         MOVIE_ID_TO_PROCESS = "435075"
#         logger.info(f"\n--- 测试处理单个电影 (ID: {MOVIE_ID_TO_PROCESS}) ---")
#         processor.process_single_item(MOVIE_ID_TO_PROCESS)

#         time.sleep(2)

#         SERIES_ID_TO_PROCESS = "436062"
#         logger.info(f"\n--- 测试处理单个剧集 (ID: {SERIES_ID_TO_PROCESS}) ---")
#         processor.process_single_item(SERIES_ID_TO_PROCESS)
        
#         processor.close()
#         logger.info("\n--- MediaProcessor 测试结束 ---")