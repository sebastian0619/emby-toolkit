# actor_utils.py
import sqlite3
import re
import json
import threading
import concurrent.futures
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Set
# 导入底层工具箱和日志
import logging
import utils
import tmdb_handler
from douban import DoubanApi
from ai_translator import AITranslator

logger = logging.getLogger(__name__)

# ======================================================================
# 模块 1: 数据库管理器 (The Unified Data Access Layer)
# ======================================================================

class ActorDBManager:
    """
    一个专门负责与演员身份相关的数据库表进行交互的类。
    这是所有数据库操作的唯一入口，确保逻辑统一。
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        logger.debug(f"ActorDBManager 初始化，使用数据库: {self.db_path}")

    def get_db_connection(self) -> sqlite3.Connection:
        """
        获取一个配置好 WAL 模式和 row_factory 的数据库连接。
        调用者负责关闭连接。
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=20.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            return conn
        except sqlite3.Error as e:
            logger.error(f"获取数据库连接失败: {e}", exc_info=True)
            raise

    def find_person_by_any_id(self, cursor: sqlite3.Cursor, **kwargs) -> Optional[sqlite3.Row]:
        search_criteria = [
            ("tmdb_person_id", kwargs.get("tmdb_id")),
            ("emby_person_id", kwargs.get("emby_id")),
            ("imdb_id", kwargs.get("imdb_id")),
            ("douban_celebrity_id", kwargs.get("douban_celebrity_id")),
        ]
        for column, value in search_criteria:
            if not value: continue
            try:
                cursor.execute(f"SELECT * FROM person_identity_map WHERE {column} = ?", (value,))
                result = cursor.fetchone()
                if result:
                    logger.debug(f"通过 {column}='{value}' 找到了演员记录 (map_id: {result['map_id']})。")
                    return result
            except sqlite3.Error as e:
                logger.error(f"查询 person_identity_map 时出错 ({column}={value}): {e}")
        return None

    def upsert_person(self, cursor: sqlite3.Cursor, person_data: Dict[str, Any]):
        """
        【V-Final 终极简化版】
        只关心ID和主名的关联与合并，不再处理别名。
        """
        # 1. 标准化和清理输入数据
        data_to_process = {
            "primary_name": str(person_data.get("name") or '').strip(),
            "emby_person_id": str(person_data.get("emby_id") or '').strip() or None,
            "tmdb_person_id": str(person_data.get("tmdb_id") or '').strip() or None,
            "imdb_id": str(person_data.get("imdb_id") or '').strip() or None,
            "douban_celebrity_id": str(person_data.get("douban_id") or '').strip() or None,
        }
        id_fields = ["emby_person_id", "tmdb_person_id", "imdb_id", "douban_celebrity_id"]
        
        if not data_to_process["primary_name"] and not any(data_to_process.get(k) for k in id_fields):
            return -1

        # 2. 通过 ID 和 名字 共同查找所有相关的“分身”
        all_related_entries = []
        unique_map_ids = set()
        query_parts = []
        query_values = []

        for column in id_fields:
            value = data_to_process.get(column)
            if value:
                query_parts.append(f"{column} = ?")
                query_values.append(value)
        
        if data_to_process["primary_name"]:
            query_parts.append("primary_name = ?")
            query_values.append(data_to_process["primary_name"])

        if not query_parts: return -1

        sql_find = f"SELECT * FROM person_identity_map WHERE {' OR '.join(query_parts)}"
        cursor.execute(sql_find, tuple(query_values))
        for row in cursor.fetchall():
            entry = dict(row)
            if entry['map_id'] not in unique_map_ids:
                all_related_entries.append(entry)
                unique_map_ids.add(entry['map_id'])

        try:
            # --- 情况A: 全新的人 ---
            if not all_related_entries:
                if not data_to_process["primary_name"]: return -1
                cols = ["primary_name"] + id_fields + ["last_synced_at", "last_updated_at"]
                vals = [data_to_process.get(col) for col in cols if "last_" not in col]
                placeholders = ["?" for _ in vals] + ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]
                sql = f"INSERT INTO person_identity_map ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
                cursor.execute(sql, tuple(vals))
                logger.debug(f"创建了新的演员记录: {data_to_process['primary_name']} (ID: {cursor.lastrowid})")
                return cursor.lastrowid

            # --- 情况B: 需要合并 ---
            else:
                all_related_entries.sort(key=lambda x: x['map_id'])
                primary_record = all_related_entries[0]
                other_records = all_related_entries[1:]

                if other_records:
                    logger.warning(f"检测到数据冲突，将合并 Map IDs {[r['map_id'] for r in other_records]} 到主记录 {primary_record['map_id']} ('{primary_record['primary_name']}')。")

                if other_records:
                    for rec in other_records:
                        set_null_clauses = [f"{col} = NULL" for col in id_fields if rec.get(col)]
                        if set_null_clauses:
                            sql_clear_ids = f"UPDATE person_identity_map SET {', '.join(set_null_clauses)} WHERE map_id = ?"
                            cursor.execute(sql_clear_ids, (rec['map_id'],))
                    logger.debug(f"已预先清空待合并记录的唯一ID。")

                # 信息大融合 (简化版)
                all_names_pool = {primary_record.get('primary_name')}
                for source in [data_to_process] + other_records:
                    for key in id_fields:
                        if source.get(key) and not primary_record.get(key): primary_record[key] = source.get(key)
                    if source.get('primary_name'): all_names_pool.add(source.get('primary_name'))
                
                valid_names = {name for name in all_names_pool if name and name.strip()}
                chinese_names = {name for name in valid_names if utils.contains_chinese(name)}
                if chinese_names:
                    primary_record['primary_name'] = max(chinese_names, key=len)
                elif valid_names:
                    primary_record['primary_name'] = max(valid_names, key=len)

                # 更新主记录并删除分身
                update_cols = ["primary_name"] + id_fields
                params = [primary_record.get(col) for col in update_cols]
                set_clauses = [f"{col} = ?" for col in update_cols] + ["last_updated_at = CURRENT_TIMESTAMP"]
                sql_update = f"UPDATE person_identity_map SET {', '.join(set_clauses)} WHERE map_id = ?"
                cursor.execute(sql_update, tuple(params + [primary_record['map_id']]))

                if other_records:
                    ids_to_delete = [r['map_id'] for r in other_records]
                    placeholders = ','.join('?' * len(ids_to_delete))
                    cursor.execute(f"DELETE FROM person_identity_map WHERE map_id IN ({placeholders})", ids_to_delete)
                    logger.info(f"已成功合并并删除了旧记录: {ids_to_delete}")
                
                return primary_record['map_id']
        except sqlite3.IntegrityError as e:
            logger.error(f"在执行合并后，仍然发生了意外的完整性冲突: {e}", exc_info=True)
            raise e
# ======================================================================
# 模块 2: 通用的业务逻辑函数 (Business Logic Helpers)
# ======================================================================
# --- 演员选择 ---
def select_best_role(current_role: str, candidate_role: str) -> str:
    """
    根据优先级选择最佳角色名。
    【最终修正版】确保有价值的中文名不会被英文名覆盖。

    优先级顺序:
    1. 有内容的豆瓣中文角色名
    2. 有内容的本地中文角色名  <-- 这是保护您本地数据的关键
    3. 有内容的英文角色名 (候选来源优先)
    4. '演员' (或其他占位符)
    5. 空字符串
    """
    # --- 步骤 1: 清理和规范化输入 ---
    original_current = current_role # 保存原始值用于日志
    original_candidate = candidate_role # 保存原始值用于日志
    
    current_role = str(current_role or '').strip()
    candidate_role = str(candidate_role or '').strip()

    # --- 步骤 2: 准备日志和判断标志 ---
    # 使用 self.logger，如果您的类中是这样命名的
    # 如果不是，请替换为正确的 logger 对象名
    logger.debug(f"--- [角色选择开始] ---")
    logger.debug(f"  输入: current='{original_current}', candidate='{original_candidate}'")
    logger.debug(f"  清理后: current='{current_role}', candidate='{candidate_role}'")

    current_is_chinese = utils.contains_chinese(current_role)
    candidate_is_chinese = utils.contains_chinese(candidate_role)
    
    # 定义一个更广泛的占位符列表
    placeholders = {"actor", "actress", "演员", "配音"}
    current_is_placeholder = current_role.lower() in placeholders
    candidate_is_placeholder = candidate_role.lower() in placeholders

    logger.debug(f"  分析: current_is_chinese={current_is_chinese}, current_is_placeholder={current_is_placeholder}")
    logger.debug(f"  分析: candidate_is_chinese={candidate_is_chinese}, candidate_is_placeholder={candidate_is_placeholder}")

    # --- 步骤 3: 应用优先级规则并记录决策 ---

    # 优先级 1: 候选角色是有效的中文名
    if candidate_is_chinese and not candidate_is_placeholder:
        logger.debug(f"  决策: [优先级1] 候选角色是有效中文名。选择候选角色。")
        logger.debug(f"  选择: '{candidate_role}'")
        logger.debug(f"--- [角色选择结束] ---")
        return candidate_role

    # 优先级 2: 当前角色是有效的中文名，而候选角色不是。必须保留当前角色！
    if current_is_chinese and not current_is_placeholder and not candidate_is_chinese:
        logger.debug(f"  决策: [优先级2] 当前角色是有效中文名，而候选不是。保留当前角色。")
        logger.debug(f"  选择: '{current_role}'")
        logger.debug(f"--- [角色选择结束] ---")
        return current_role

    # 优先级 3: 两者都不是有效的中文名（或都是）。选择一个非占位符的，候选者优先。
    if candidate_role and not candidate_is_placeholder:
        logger.debug(f"  决策: [优先级3a] 候选角色是有效的非中文名/占位符。选择候选角色。")
        logger.debug(f"  选择: '{candidate_role}'")
        logger.debug(f"--- [角色选择结束] ---")
        return candidate_role
    
    if current_role and not current_is_placeholder:
        logger.debug(f"  决策: [优先级3b] 当前角色是有效的非中文名/占位符，而候选是无效的。保留当前角色。")
        logger.debug(f"  选择: '{current_role}'")
        logger.debug(f"--- [角色选择结束] ---")
        return current_role

    # 优先级 4: 处理占位符。如果两者之一是占位符，则返回一个（候选优先）。
    if candidate_role: # 如果候选有内容（此时只能是占位符）
        logger.debug(f"  决策: [优先级4a] 候选角色是占位符。选择候选角色。")
        logger.debug(f"  选择: '{candidate_role}'")
        logger.debug(f"--- [角色选择结束] ---")
        return candidate_role
        
    if current_role: # 如果当前有内容（此时只能是占位符）
        logger.debug(f"  决策: [优先级4b] 当前角色是占位符，候选为空。保留当前角色。")
        logger.debug(f"  选择: '{current_role}'")
        logger.debug(f"--- [角色选择结束] ---")
        return current_role

    # 优先级 5: 所有情况都处理完，只剩下两者都为空。
    logger.debug(f"  决策: [优先级5] 所有输入均为空或无效。返回空字符串。")
    logger.debug(f"  选择: ''")
    logger.debug(f"--- [角色选择结束] ---")
    return ""
# --- 质量评估 ---
def evaluate_cast_processing_quality(
    final_cast: List[Dict[str, Any]], 
    original_cast_count: int, 
    expected_final_count: Optional[int] = None,
    is_animation: bool = False  # ✨✨✨ 新增参数，默认为 False ✨✨✨
) -> float:
    """
    【V-Final 极简版 - 动画片优化】
    只关心最终产出的中文化质量和演员数量。
    如果检测到是动画片，则跳过所有关于数量的惩罚。
    """
    if not final_cast:
        # ✨ 如果是动画片且演员列表为空，可以给一个基础通过分，避免进手动列表
        if is_animation:
            logger.info("  质量评估：动画片演员列表为空，属于正常情况，给予基础通过分 7.0。")
            return 7.0
        else:
            logger.warning("  质量评估：处理后演员列表为空！评为 0.0 分。")
            return 0.0
        
    total_actors = len(final_cast)
    accumulated_score = 0.0
    
    logger.debug(f"--- 质量评估开始 (极简版) ---")
    logger.debug(f"  - 原始演员数: {original_cast_count}")
    logger.debug(f"  - 处理后演员数: {total_actors}")
    logger.debug(f"------------------------------------")

    for i, actor_data in enumerate(final_cast):
        # 每个演员的基础分是 0.0，通过加分项累加
        score = 0.0
        
        # --- 智能获取数据 ---
        actor_name = actor_data.get("name") or actor_data.get("Name")
        actor_role = actor_data.get("character") or actor_data.get("Role")
        
        # --- 演员名评分 (满分 5.0) ---
        if actor_name and utils.contains_chinese(actor_name):
            score += 5.0
        elif actor_name:
            score += 1.0 # 保留一个较低的基础分给英文名

        # --- 角色名评分 (满分 5.0) ---
        placeholders = {"演员", "配音"}
        is_placeholder = (str(actor_role).endswith("(配音)")) or (str(actor_role) in placeholders)

        if actor_role and utils.contains_chinese(actor_role) and not is_placeholder:
            score += 5.0 # 有意义的中文角色名
        elif actor_role and utils.contains_chinese(actor_role) and is_placeholder:
            score += 2.5 # 中文占位符
        elif actor_role:
            score += 0.5 # 英文角色名

        final_actor_score = min(10.0, score)
        accumulated_score += final_actor_score
        
        logger.debug(f"  [{i+1}/{total_actors}] 演员: '{actor_name}' (角色: '{actor_role}') | 单项评分: {final_actor_score:.1f}")

    avg_score = accumulated_score / total_actors if total_actors > 0 else 0.0
    
    # --- ✨✨✨ 核心修改：条件化的数量惩罚逻辑 ✨✨✨ ---
    logger.debug(f"------------------------------------")
    logger.debug(f"  - 基础平均分 (惩罚前): {avg_score:.2f}")

    if is_animation:
        logger.debug("  - 惩罚: 检测到为动画片，跳过所有数量相关的惩罚。")
    else:
        # 只有在不是动画片时，才执行原来的数量惩罚逻辑
        if total_actors < 10:
            penalty_factor = total_actors / 10.0
            logger.warning(f"  - 惩罚: 最终演员数({total_actors})少于10个，乘以惩罚因子 {penalty_factor:.2f}")
            avg_score *= penalty_factor
            
        elif expected_final_count is not None:
            if total_actors < expected_final_count * 0.8:
                penalty_factor = total_actors / expected_final_count
                logger.warning(f"  - 惩罚: 数量({total_actors})远少于预期({expected_final_count})，乘以惩罚因子 {penalty_factor:.2f}")
                avg_score *= penalty_factor
        elif total_actors < original_cast_count * 0.8:
            penalty_factor = total_actors / original_cast_count
            logger.warning(f"  - 惩罚: 数量从{original_cast_count}大幅减少到{total_actors}，乘以惩罚因子 {penalty_factor:.2f}")
            avg_score *= penalty_factor
        else:
            logger.debug(f"  - 惩罚: 数量正常，不进行惩罚。")
    
    final_score_rounded = round(avg_score, 1)
    logger.info(f"  - 最终评分: {final_score_rounded:.1f}")
    return final_score_rounded


def translate_actor_field(text: Optional[str], db_cursor: sqlite3.Cursor, ai_translator: Optional[AITranslator], translator_engines: List[str], ai_enabled: bool) -> Optional[str]:
    """翻译演员的特定字段，智能选择AI或传统翻译引擎。"""
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
            logger.info(f"数据库翻译缓存命中 for '{text_stripped}' -> '{cached_translation}'")
            return cached_translation
        # 情况 B: 缓存中明确记录了这是一个失败的翻译
        else:
            logger.debug(f"数据库翻译缓存命中 (失败记录) for '{text_stripped}'，不再尝试在线翻译。")
            return text # 直接返回原文，避免重复请求

    # 4. 如果缓存中完全没有记录，才进行在线翻译
    logger.debug(f"'{text_stripped}' 在翻译缓存中未找到，将进行在线翻译...")
    final_translation = None
    final_engine = "unknown"

    # 根据配置选择翻译方式
    ai_translation_attempted = False

    # 步骤 1: 如果AI翻译启用，优先尝试AI
    if ai_translator and ai_enabled:
        ai_translation_attempted = True
        logger.debug(f"AI翻译已启用，优先尝试使用 '{ai_translator.provider}' 进行翻译...")
        try:
            # ai_translator.translate 应该在失败时返回 None 或抛出异常
            ai_result = ai_translator.translate(text_stripped)
            if ai_result: # 确保AI返回了有效结果
                final_translation = ai_result
                final_engine = ai_translator.provider
        except Exception as e_ai:
            # 如果AI翻译器内部抛出异常，在这里捕获
            logger.error(f"AI翻译器在翻译 '{text_stripped}' 时发生异常: {e_ai}")
            # 不做任何事，让流程继续往下走，尝试传统引擎

    # 步骤 2: 如果AI翻译未启用，或AI翻译失败/未返回结果，则使用传统引擎
    if not final_translation:
        if ai_translation_attempted:
            logger.warning(f"AI翻译未能获取有效结果，将降级使用传统翻译引擎...")
        
        translation_result = utils.translate_text_with_translators(
            text_stripped,
            engine_order=translator_engines
        )
        if translation_result and translation_result.get("text"):
            final_translation = translation_result["text"]
            final_engine = translation_result["engine"]

    # 5. 处理在线翻译的结果，并更新缓存
    if final_translation and final_translation.strip() and final_translation.strip().lower() != text_stripped.lower():
        # 翻译成功，存入缓存并返回结果
        logger.debug(f"在线翻译成功: '{text_stripped}' -> '{final_translation}' (使用引擎: {final_engine})")
        DoubanApi._save_translation_to_db(text_stripped, final_translation, final_engine, cursor=db_cursor)
        return final_translation
    else:
        # 翻译失败或返回原文，将失败状态存入缓存，并返回原文
        logger.warning(f"在线翻译未能翻译 '{text_stripped}' 或返回了原文 (使用引擎: {final_engine})。")
        DoubanApi._save_translation_to_db(text_stripped, None, f"failed_or_same_via_{final_engine}", cursor=db_cursor)
        return text
# ✨✨✨从豆瓣API获取指定媒体的演员原始数据列表✨✨✨
def find_douban_cast(douban_api: DoubanApi, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从豆瓣API获取演员原始数据。"""
        # 假设 constants 和 self.douban_api 已经存在
        # if not (getattr(constants, 'DOUBAN_API_AVAILABLE', False) and self.douban_api and \
        #         self.data_source_mode in [constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY]):
        #     return []
        if not douban_api:
            logger.warning("未提供 DoubanApi 实例，无法获取豆瓣演员。")
            return []
        logger.debug("调用豆瓣 API get_acting...")
        douban_data = douban_api.get_acting(
            name=media_info.get("Name"),
            imdbid=media_info.get("ProviderIds", {}).get("Imdb"),
            mtype="movie" if media_info.get("Type") == "Movie" else ("tv" if media_info.get("Type") == "Series" else None),
            year=str(media_info.get("ProductionYear", "")),
            douban_id_override=media_info.get("ProviderIds", {}).get("Douban")
        )
        if douban_data and not douban_data.get("error") and isinstance(douban_data.get("cast"), list):
            return douban_data["cast"]
        return []
# ✨✨✨格式化从豆瓣获取的原始演员数据，进行初步清理和去重，使其符合内部处理格式✨✨✨
def format_douban_cast(douban_api_actors_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """格式化豆瓣原始演员数据并进行初步去重。"""
    formatted_candidates = []
    seen_douban_ids = set()
    seen_name_sigs = set()
    for item in douban_api_actors_raw:
        name_zh = str(item.get("name", "")).strip()
        # ★★★ 核心加固：确保名字不为空字符串 ★★★
        if not name_zh: 
            continue
        douban_id = str(item.get("id", "")).strip() or None
        name_zh = str(item.get("name", "")).strip()
        if not name_zh: continue

        # 基于ID或名字进行初步去重
        if douban_id:
            if douban_id in seen_douban_ids: continue
            seen_douban_ids.add(douban_id)
        else:
            name_sig = f"{name_zh.lower()}|{str(item.get('original_name', '')).lower().strip()}"
            if name_sig in seen_name_sigs: continue
            seen_name_sigs.add(name_sig)
        
        formatted_candidates.append({
            "Name": name_zh,
            "OriginalName": str(item.get("original_name", "")).strip(),
            "Role": str(item.get("character", "")).strip(),
            "DoubanCelebrityId": douban_id,
            "ProviderIds": {"Douban": douban_id} if douban_id else {},
        })
    return formatted_candidates
# ✨✨✨批量翻译辅助方法✨✨✨
def batch_translate_cast(cast_list: List[Dict[str, Any]], db_cursor: sqlite3.Cursor, ai_translator: Optional[AITranslator], translator_engines: List[str], ai_enabled: bool) -> List[Dict[str, Any]]:
    """
    使用AI批量翻译演员列表中的姓名和角色。
    """
    logger.info("  (AI批量模式) 开始收集需要翻译的字段...")
    
    texts_to_translate = set()
    translation_cache = {} # 用于存储从数据库或API获取的翻译结果

    # 步骤 1: 收集所有需要翻译的文本，并优先使用数据库缓存
    for actor in cast_list:
        for field in ["Name", "Role"]:
            original_text = actor.get(field)
            if not original_text or not original_text.strip() or utils.contains_chinese(original_text):
                continue

            # 检查数据库缓存
            cached_entry = DoubanApi._get_translation_from_db(original_text)
            if cached_entry and cached_entry.get("translated_text"):
                cached_translation = cached_entry.get("translated_text")
                engine_used = cached_entry.get("engine_used")
                logger.debug(f"    数据库翻译缓存命中 for '{original_text}' -> '{cached_translation}' (引擎: {engine_used})")
                translation_cache[original_text] = cached_translation
            else:
                # 如果缓存未命中，则加入待翻译集合
                texts_to_translate.add(original_text)

    # 步骤 2: 如果有需要翻译的文本，则进行一次性批量API调用
    if texts_to_translate:
        logger.info(f"  (AI批量模式) 收集到 {len(texts_to_translate)} 个独立词条需要通过API翻译。")
        
        # 调用AITranslator的批量翻译方法
        # 注意：你需要确保你的 AITranslator 类有 batch_translate 方法
        try:
            # 将 set 转换为 list
            api_results = ai_translator.batch_translate(list(texts_to_translate))
            
            # 更新我们的翻译缓存，并存入数据库
            if api_results:
                logger.info(f"  (AI批量模式) API成功返回 {len(api_results)} 个翻译结果。")
                translation_cache.update(api_results)
                
                # 将新翻译的结果存入数据库缓存
                for original, translated in api_results.items():
                    DoubanApi._save_translation_to_db(
                        original, 
                        translated, 
                        ai_translator.provider, 
                        cursor=db_cursor
                    )
            else:
                logger.warning("  (AI批量模式) AI批量翻译API没有返回有效结果。")

        except Exception as e:
            logger.error(f"  (AI批量模式) 调用AI批量翻译API时发生错误: {e}", exc_info=True)
    else:
        logger.info("  (AI批量模式) 所有需要翻译的字段均在数据库缓存中找到，无需调用API。")

    # 步骤 3: 映射回填，使用完整的 translation_cache 更新演员列表
    logger.info("  (AI批量模式) 开始将翻译结果回填到演员列表...")
    for actor in cast_list:
        # 翻译名字
        original_name = actor.get("Name")
        if original_name in translation_cache:
            actor["Name"] = translation_cache[original_name]
        
        # 翻译角色 (先清理)
        original_role = utils.clean_character_name_static(actor.get("Role"))
        if original_role in translation_cache:
            actor["Role"] = translation_cache[original_role] # 更新时使用已翻译的结果
        else:
            actor["Role"] = original_role # 即使没翻译，也要用清理后的结果

    return cast_list
# ✨✨✨格式化演员表✨✨✨
def format_and_complete_cast_list(cast_list: List[Dict[str, Any]], is_animation: bool) -> List[Dict[str, Any]]:
    """【共享工具】对最终的演员列表进行格式化（角色名、排序）。"""
    perfect_cast = []
    logger.info("格式化演员列表：开始处理角色名和排序。")

    for idx, actor in enumerate(cast_list):
        final_role = actor.get("character", "").strip()
        if utils.contains_chinese(final_role):
            final_role = final_role.replace(" ", "").replace("　", "")
        
        if is_animation:
            if final_role and not final_role.endswith("(配音)"):
                final_role = f"{final_role} (配音)"
            elif not final_role:
                final_role = "配音"
        elif not final_role:
            final_role = "演员"

        actor["character"] = final_role
        actor["order"] = idx
        perfect_cast.append(actor)
            
    return perfect_cast
# --- 用于获取单个演员的TMDb详情 ---
def fetch_tmdb_details_for_actor(actor_info: Dict, tmdb_api_key: str) -> Optional[Dict]:
    """一个独立的、可在线程中运行的函数，用于获取单个演员的TMDb详情。"""
    tmdb_id = actor_info.get("tmdb_person_id")
    if not tmdb_id:
        return None
    try:
        details = tmdb_handler.get_person_details_tmdb(int(tmdb_id), tmdb_api_key, "external_ids,also_known_as")
        if details:
            return {"tmdb_id": tmdb_id, "details": details}
        return None
    except Exception as e:
        logger.warning(f"获取 TMDb 详情 (ID: {tmdb_id}) 时失败: {e}")
        return None
# --- 补充演员外部ID ---
def enrich_all_actor_aliases_task(
    db_path: str, 
    tmdb_api_key: str, 
    run_duration_minutes: int,
    stop_event: Optional[threading.Event] = None
):
    logger.info("--- 开始执行“演员外部ID补充”计划任务 ---")
    
    start_time = time.time()
    if run_duration_minutes > 0:
        end_time = start_time + run_duration_minutes * 60
        end_time_str = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"任务将运行 {run_duration_minutes} 分钟，预计在 {end_time_str} 左右自动停止。")
    else:
        end_time = float('inf')
        logger.info("任务未设置运行时长，将持续运行。")

    actor_db_manager = ActorDBManager(db_path)

    try:
        with actor_db_manager.get_db_connection() as conn:
            # --- 阶段一：从 TMDb 补充 IMDb ID (并发执行) ---
            logger.info("--- 阶段一：从 TMDb 补充 IMDb ID ---")
            cursor = conn.cursor()
            sql_find_tmdb_needy = "SELECT * FROM person_identity_map WHERE tmdb_person_id IS NOT NULL AND imdb_id IS NULL ORDER BY last_synced_at ASC"
            actors_for_tmdb = cursor.execute(sql_find_tmdb_needy).fetchall()
            
            if actors_for_tmdb:
                total_tmdb = len(actors_for_tmdb)
                logger.info(f"找到 {total_tmdb} 位演员需要从 TMDb 补充 IMDb ID。")
                
                CHUNK_SIZE = 200  # 每批处理500个
                MAX_TMDB_WORKERS = 5 # 最多10个并发线程

                for i in range(0, total_tmdb, CHUNK_SIZE):
                    if (stop_event and stop_event.is_set()) or (time.time() >= end_time):
                        logger.info("达到运行时长或收到停止信号，在 TMDb 下批次开始前结束。")
                        break

                    chunk = actors_for_tmdb[i:i + CHUNK_SIZE]
                    logger.info(f"--- 开始处理 TMDb 第 {i//CHUNK_SIZE + 1} 批次，共 {len(chunk)} 个演员 ---")

                    tmdb_updates_chunk = []
                    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_TMDB_WORKERS) as executor:
                        future_to_actor = {executor.submit(fetch_tmdb_details_for_actor, dict(actor), tmdb_api_key): actor for actor in chunk}
                        
                        for future in concurrent.futures.as_completed(future_to_actor):
                            if stop_event and stop_event.is_set():
                                for f in future_to_actor: f.cancel()
                                raise InterruptedError("任务在TMDb处理批次中被中止")

                            result = future.result()
                            if result and result.get("details"):
                                tmdb_updates_chunk.append(result)
                    
                    if tmdb_updates_chunk:
                        logger.info(f"  -> 批次完成，获取到 {len(tmdb_updates_chunk)} 条新信息，准备写入数据库...")
                        cursor.execute("BEGIN TRANSACTION;")
                        for update_data in tmdb_updates_chunk:
                            details = update_data.get("details")
                            imdb_id = details.get("external_ids", {}).get("imdb_id")
                            if update_data.get("tmdb_id") and imdb_id:
                                actor_db_manager.upsert_person(
                                    cursor, 
                                    {"tmdb_id": update_data["tmdb_id"], "imdb_id": imdb_id}
                                )
                        conn.commit()
                        logger.info(f"  -> 批次数据写入成功！")
            else:
                logger.info("没有需要从 TMDb 补充 IMDb ID 的演员。")

            # --- 阶段二：从 豆瓣 补充 IMDb ID (串行执行) ---
            if (stop_event and stop_event.is_set()) or (time.time() >= end_time): raise InterruptedError("任务中止")
            
            douban_api = DoubanApi(db_path=db_path)
            logger.info("--- 阶段二：从 豆瓣 补充 IMDb ID ---")
            sql_find_douban_needy = "SELECT * FROM person_identity_map WHERE douban_celebrity_id IS NOT NULL AND imdb_id IS NULL ORDER BY last_synced_at ASC"
            actors_for_douban = cursor.execute(sql_find_douban_needy).fetchall()

            if actors_for_douban:
                logger.info(f"找到 {len(actors_for_douban)} 位演员需要从豆瓣补充 IMDb ID。")
                cursor.execute("BEGIN TRANSACTION;")
                processed_in_douban_run = 0
                for i, actor in enumerate(actors_for_douban):
                    if (stop_event and stop_event.is_set()) or (time.time() >= end_time): break
                    
                    try:
                        details = douban_api.celebrity_details(actor['douban_celebrity_id'])
                        if details and not details.get("error"):
                            new_imdb_id = None
                            for item in details.get("extra", {}).get("info", []):
                                if isinstance(item, list) and len(item) == 2 and item[0] == 'IMDb编号':
                                    new_imdb_id = item[1]
                                    break
                            if new_imdb_id:
                                logger.info(f"  ({i+1}/{len(actors_for_douban)}) 为演员 '{actor['primary_name']}' (Douban: {actor['douban_celebrity_id']}) 找到 IMDb ID: {new_imdb_id}")
                                actor_db_manager.upsert_person(cursor, {"douban_id": actor['douban_celebrity_id'], "imdb_id": new_imdb_id})
                        
                        cursor.execute("UPDATE person_identity_map SET last_synced_at = CURRENT_TIMESTAMP WHERE map_id = ?", (actor['map_id'],))
                        processed_in_douban_run += 1
                        if processed_in_douban_run % 50 == 0:
                            conn.commit()
                            cursor.execute("BEGIN TRANSACTION;")
                    except Exception as e:
                        logger.error(f"从豆瓣获取详情失败 (ID: {actor['douban_celebrity_id']}): {e}")
                
                conn.commit()
                logger.info(f"豆瓣信息补充完成，本轮共处理 {processed_in_douban_run} 个。")
            else:
                logger.info("没有需要从豆瓣补充 IMDb ID 的演员。")
            
            if douban_api:
                douban_api.close()

    except InterruptedError:
        logger.info("演员ID补充任务被中止。")
        if conn and conn.in_transaction: conn.rollback()
    except Exception as e:
        logger.error(f"演员ID补充任务发生严重错误: {e}", exc_info=True)
        if conn and conn.in_transaction: conn.rollback()
    finally:
        logger.info("--- “演员ID补充”计划任务已退出 ---")

