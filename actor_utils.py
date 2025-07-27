# actor_utils.py
import sqlite3
import re
import json
import threading
import concurrent.futures
import time
import constants
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, Set
# 导入底层工具箱和日志
import logging
import db_handler
from db_handler import ActorDBManager
import utils
import tmdb_handler
from douban import DoubanApi
from ai_translator import AITranslator

logger = logging.getLogger(__name__)

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
    2. 有内容的本地中文角色名
    3. 有内容的英文角色名 (豆瓣来源优先)
    4. '演员' (或其他占位符)
    5. 空字符串
    """
    # --- 步骤 1: 清理和规范化输入 ---
    original_current = current_role # 保存原始值用于日志
    original_candidate = candidate_role # 保存原始值用于日志
    
    current_role = str(current_role or '').strip()
    candidate_role = str(candidate_role or '').strip()

    # --- 步骤 2: 准备日志和判断标志 ---
    logger.debug(f"  备选角色名: 当前='{current_role}', 豆瓣='{candidate_role}'")

    current_is_chinese = utils.contains_chinese(current_role)
    candidate_is_chinese = utils.contains_chinese(candidate_role)
    
    # 定义一个更广泛的占位符列表
    placeholders = {"actor", "actress", "演员", "配音"}
    current_is_placeholder = current_role.lower() in placeholders
    candidate_is_placeholder = candidate_role.lower() in placeholders

    # --- 步骤 3: 应用优先级规则并记录决策 ---

    # 优先级 1: 豆瓣角色是有效的中文名
    if candidate_is_chinese and not candidate_is_placeholder:
        logger.trace(f"  决策: [优先级1] 豆瓣角色是有效中文名。选择豆瓣角色。")
        logger.debug(f"  选择: '{candidate_role}'")
        return candidate_role

    # 优先级 2: 当前角色是有效的中文名，而豆瓣角色不是。必须保留当前角色！
    if current_is_chinese and not current_is_placeholder and not candidate_is_chinese:
        logger.trace(f"  决策: [优先级2] 当前角色是有效中文名，而豆瓣不是。保留当前角色。")
        logger.debug(f"  选择: '{current_role}'")
        return current_role

    # 优先级 3: 两者都不是有效的中文名（或都是）。选择一个非占位符的，豆瓣者优先。
    if candidate_role and not candidate_is_placeholder:
        logger.trace(f"  决策: [优先级3] 豆瓣角色是有效的非中文名/占位符。选择豆瓣角色。")
        logger.debug(f"  选择: '{candidate_role}'")
        return candidate_role
    
    if current_role and not current_is_placeholder:
        logger.trace(f"  决策: [优先级4] 当前角色是有效的非中文名/占位符，而豆瓣角色是无效的。保留当前角色。")
        logger.debug(f"  选择: '{current_role}'")
        return current_role

    # 优先级 4: 处理占位符。如果两者之一是占位符，则返回一个（豆瓣优先）。
    if candidate_role: # 如果豆瓣有内容（此时只能是占位符）
        logger.trace(f"  决策: [优先级5] 豆瓣角色是占位符。选择豆瓣角色。")
        logger.debug(f"  选择: '{candidate_role}'")
        return candidate_role
        
    if current_role: # 如果当前有内容（此时只能是占位符）
        logger.trace(f"  决策: [优先级6] 当前角色是占位符，豆瓣为空。保留当前角色。")
        logger.debug(f"  选择: '{current_role}'")
        return current_role

    # 优先级 5: 所有情况都处理完，只剩下两者都为空。
    logger.trace(f"  决策: [优先级7] 所有输入均为空或无效。返回空字符串。")
    logger.debug(f"  选择: ''")
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
            logger.warning("  - 处理后演员列表为空！评为 0.0 分。")
            return 0.0
        
    total_actors = len(final_cast)
    accumulated_score = 0.0
    
    logger.debug(f"--- 质量评估开始 ---")
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
        logger.debug("  - 惩罚: 检测到为动画片或纪录片，跳过所有数量相关的惩罚。")
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


def translate_actor_field(text: Optional[str], db_manager: ActorDBManager, db_cursor: sqlite3.Cursor, ai_translator: Optional[AITranslator], translator_engines: List[str], ai_enabled: bool) -> Optional[str]:
    """翻译演员的特定字段，智能选择AI或传统翻译引擎。"""
    # 1. 前置检查：如果文本为空、是纯空格，或已包含中文，则直接返回原文
    if not text or not text.strip() or utils.contains_chinese(text):
        return text
    
    text_stripped = text.strip()

    # 2. 前置检查：跳过短的大写字母缩写
    if len(text_stripped) <= 2 and text_stripped.isupper():
        return text

    # 3. 核心修复：优先从数据库读取缓存，并处理所有情况
    cached_entry = db_manager.get_translation_from_db(db_cursor, text_stripped)
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
        logger.info(f"在线翻译成功: '{text_stripped}' -> '{final_translation}' (使用引擎: {final_engine})")
        db_manager.save_translation_to_db(db_cursor, text_stripped, final_translation, final_engine)
        return final_translation
    else:
        # 翻译失败或返回原文，将失败状态存入缓存，并返回原文
        logger.warning(f"在线翻译未能翻译 '{text_stripped}' 或返回了原文 (使用引擎: {final_engine})。")
        db_manager.save_translation_to_db(db_cursor, text_stripped, None, f"failed_or_same_via_{final_engine}")
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
    seen_names = set()
    for item in douban_api_actors_raw:
        name_zh = str(item.get("name", "")).strip()
        if not name_zh: 
            continue
            
        douban_id = str(item.get("id", "")).strip() or None

        # 【★★★ 核心修复：严格的去重逻辑 ★★★】
        # 1. 如果有豆瓣ID，且ID已存在，则跳过。
        if douban_id and douban_id in seen_douban_ids:
            continue
        
        # 2. 如果名字已存在，则跳过。
        if name_zh in seen_names:
            continue

        # 如果能走到这里，说明是唯一的演员，记录下来
        if douban_id:
            seen_douban_ids.add(douban_id)
        seen_names.add(name_zh)
        
        formatted_candidates.append({
            "Name": name_zh,
            "OriginalName": str(item.get("original_name", "")).strip(),
            "Role": str(item.get("character", "")).strip(),
            "DoubanCelebrityId": douban_id,
            "ProviderIds": {"Douban": douban_id} if douban_id else {},
        })
        
    return formatted_candidates
# ✨✨✨格式化演员表✨✨✨
def format_and_complete_cast_list(
    cast_list: List[Dict[str, Any]], 
    is_animation: bool, 
    config: Dict[str, Any],
    mode: str = 'auto'  # ★★★ 核心参数: 'auto' 或 'manual' ★★★
) -> List[Dict[str, Any]]:
    """
    【V9 - 最终策略版】根据调用模式格式化并排序演员列表。
    - 'auto': 自动处理流程。严格按原始TMDb的 'order' 字段排序。
    - 'manual': 手动编辑流程。以传入列表的顺序为基准，并将通用角色排到末尾。
    """
    processed_cast = []
    add_role_prefix = config.get(constants.CONFIG_OPTION_ACTOR_ROLE_ADD_PREFIX, False)
    generic_roles = {"演员", "配音"}

    logger.debug(f"格式化演员列表，调用模式: '{mode}' (前缀开关: {'开' if add_role_prefix else '关'})")

    # --- 阶段1: 统一的角色名格式化 (所有模式通用) ---
    for idx, actor in enumerate(cast_list):
        new_actor = actor.copy()
        
        # (角色名处理逻辑保持不变)
        character_name = new_actor.get("character")
        final_role = character_name.strip() if character_name else ""
        if utils.contains_chinese(final_role):
            final_role = final_role.replace(" ", "").replace("　", "")
        if add_role_prefix:
            if final_role and final_role not in generic_roles:
                prefix = "配 " if is_animation else "饰 "
                final_role = f"{prefix}{final_role}"
            elif not final_role:
                final_role = "配音" if is_animation else "演员"
        else:
            if not final_role:
                final_role = "配音" if is_animation else "演员"
        new_actor["character"] = final_role
        
        # 为 'manual' 模式记录原始顺序
        new_actor['original_index'] = idx
        
        processed_cast.append(new_actor)

    # --- 阶段2: 根据模式执行不同的排序策略 ---
    if mode == 'manual':
        # 【手动模式】：以用户自定义顺序为基础，并增强（通用角色后置）
        logger.debug("应用 'manual' 排序策略：保留用户自定义顺序，并将通用角色后置。")
        processed_cast.sort(key=lambda actor: (
            1 if actor.get("character") in generic_roles else 0,  # 1. 通用角色排在后面
            actor.get("original_index")                          # 2. 在此基础上，保持原始手动顺序
        ))
    else: # mode == 'auto' 或其他任何默认情况
        # 【自动模式】：严格按照TMDb原始的 'order' 字段排序
        logger.debug("应用 'auto' 排序策略：严格按原始TMDb 'order' 字段排序。")
        processed_cast.sort(key=lambda actor: actor.get('order', 999))
        
    # --- 阶段3: 最终重置 order 索引 (所有模式通用) ---
    for new_idx, actor in enumerate(processed_cast):
        actor["order"] = new_idx
        if 'original_index' in actor:
            del actor['original_index'] # 清理临时key
            
    return processed_cast
# --- 用于获取单个演员的TMDb详情 ---
def fetch_tmdb_details_for_actor(actor_info: Dict, tmdb_api_key: str) -> Optional[Dict]:
    """一个独立的、可在线程中运行的函数，用于获取单个演员的TMDb详情。"""
    tmdb_id = actor_info.get("tmdb_person_id")
    if not tmdb_id:
        return None
    try:
        details = tmdb_handler.get_person_details_tmdb(
            person_id=int(tmdb_id), 
            api_key=tmdb_api_key, 
            append_to_response="external_ids" 
        )
        if details:
            # 成功获取，返回详情
            return {"tmdb_id": tmdb_id, "status": "found", "details": details}
        else:
            # API调用成功但返回空，也标记为未找到
            return {"tmdb_id": tmdb_id, "status": "not_found"}

    except tmdb_handler.TMDbResourceNotFound:
        # ★★★ 捕获到404异常，返回一个明确的“未找到”状态 ★★★
        return {"tmdb_id": tmdb_id, "status": "not_found"}
    
    except tmdb_handler.TMDbAPIError as e:
        # 其他API错误（如网络问题），记录日志并返回失败状态
        logger.warning(f"获取演员 {tmdb_id} 详情时遇到API错误: {e}")
        return {"tmdb_id": tmdb_id, "status": "failed"}
# --- 演员元数据增强 ---
def enrich_all_actor_aliases_task(
    db_path: str, 
    tmdb_api_key: str, 
    run_duration_minutes: int,
    sync_interval_days: int,
    stop_event: Optional[threading.Event] = None
):
    """
    【V3 - 元数据增强版】
    在补充IMDb ID的同时，从TMDb获取演员的详细元数据（头像、性别等）并存入 ActorMetadata 表。
    """
    logger.info("--- 开始执行“演员元数据增强”计划任务 ---")
    
    start_time = time.time()
    if run_duration_minutes > 0:
        end_time = start_time + run_duration_minutes * 60
        end_time_str = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"任务将运行 {run_duration_minutes} 分钟，预计在 {end_time_str} 左右自动停止。")
    else:
        end_time = float('inf')
        logger.info("任务未设置运行时长，将持续运行。")

    SYNC_INTERVAL_DAYS = sync_interval_days
    logger.info(f"同步冷却时间设置为 {SYNC_INTERVAL_DAYS} 天。")

    try:
        with db_handler.get_db_connection(db_path) as conn:
            # --- 阶段一：从 TMDb 补充元数据 (并发执行) ---
            logger.info("--- 阶段一：从 TMDb 补充演员元数据 (IMDb ID, 头像等) ---")
            cursor = conn.cursor()
            
            # ★★★ 1. 改造SQL查询：现在我们的目标是所有需要同步的演员 ★★★
            #    我们不仅关心 imdb_id 为空的，也关心 ActorMetadata 表中没有记录的。
            sql_find_tmdb_needy = f"""
                SELECT p.* FROM person_identity_map p
                LEFT JOIN ActorMetadata m ON p.tmdb_person_id = m.tmdb_id
                WHERE
                    -- 前提1：演员必须有 TMDb ID
                    p.tmdb_person_id IS NOT NULL
                AND
                    -- 前提2：数据是不完整的 (这个定义不变)
                    (
                        p.imdb_id IS NULL OR
                        m.tmdb_id IS NULL OR      -- 这也间接说明 ActorMetadata 中无记录
                        m.profile_path IS NULL OR
                        m.gender IS NULL
                    )
                AND
                    -- 核心调度逻辑：
                    (
                        -- 条件A (高优先级): 从未成功同步过元数据 (ActorMetadata中没有它的时间戳)
                        m.last_updated_at IS NULL
                        OR
                        -- 条件B (低优先级): 同步过，但已过了冷却期，可以重试
                        m.last_updated_at < datetime('now', '-{SYNC_INTERVAL_DAYS} days')
                    )
                ORDER BY m.last_updated_at ASC -- NULLs 会被优先排在前面，确保新条目最先处理
            """
            actors_for_tmdb = cursor.execute(sql_find_tmdb_needy).fetchall()
            
            if actors_for_tmdb:
                total_tmdb = len(actors_for_tmdb)
                logger.info(f"找到 {total_tmdb} 位演员需要从 TMDb 补充元数据。")
                
                CHUNK_SIZE = 200
                MAX_TMDB_WORKERS = 5

                for i in range(0, total_tmdb, CHUNK_SIZE):
                    if (stop_event and stop_event.is_set()) or (time.time() >= end_time):
                        logger.info("达到运行时长或收到停止信号，在 TMDb 下批次开始前结束。")
                        break

                    chunk = actors_for_tmdb[i:i + CHUNK_SIZE]
                    logger.info(f"--- 开始处理 TMDb 第 {i//CHUNK_SIZE + 1} 批次，共 {len(chunk)} 个演员 ---")

                    # ★★★ 2. 改造数据容器：我们需要为两个表准备数据 ★★★
                    imdb_updates_to_commit = []      # 存储 (imdb_id, tmdb_id)
                    metadata_to_commit = []          # 存储元数据字典列表
                    invalid_tmdb_ids = []            # 存储需要置空的 tmdb_id

                    # ▼▼▼ 1. 在批次循环前初始化计数器 ▼▼▼
                    tmdb_success_count = 0
                    imdb_found_count = 0
                    metadata_added_count = 0
                    not_found_count = 0

                    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_TMDB_WORKERS) as executor:
                        # 假设 fetch_tmdb_details_for_actor 现在会返回更完整的详情
                        future_to_actor = {executor.submit(fetch_tmdb_details_for_actor, dict(actor), tmdb_api_key): actor for actor in chunk}
                        
                        for future in concurrent.futures.as_completed(future_to_actor):
                            if stop_event and stop_event.is_set():
                                for f in future_to_actor: f.cancel()
                                raise InterruptedError("任务在TMDb处理批次中被中止")

                            result = future.result()
                            if not result: continue

                            status = result.get("status")
                            tmdb_id = result.get("tmdb_id")
                            details = result.get("details", {})

                            if status == "found" and details:
                                tmdb_success_count += 1 # 增加成功计数
                                # 2.1 准备 IMDb ID 更新数据
                                imdb_id = details.get("external_ids", {}).get("imdb_id")
                                if imdb_id:
                                    imdb_found_count += 1 # 增加IMDb计数
                                    imdb_updates_to_commit.append((imdb_id, tmdb_id))
                                
                                # 2.2 准备 ActorMetadata 更新数据
                                metadata_entry = {
                                    "tmdb_id": tmdb_id,
                                    "profile_path": details.get("profile_path"),
                                    "gender": details.get("gender"),
                                    "adult": details.get("adult", False),
                                    "popularity": details.get("popularity"),
                                    "original_name": details.get("original_name")
                                }
                                metadata_to_commit.append(metadata_entry)
                                metadata_added_count += 1 # 增加元数据计数
                            
                            elif status == "not_found":
                                not_found_count += 1 # 增加未找到计数
                                logger.warning(f"  -> 演员 (TMDb ID: {tmdb_id}) 在TMDb上已不存在(404)，将清除此无效ID。")
                                invalid_tmdb_ids.append(tmdb_id)

                    # ▼▼▼ 3. 在批次处理完成后，打印一条摘要日志 ▼▼▼
                    logger.info(
                        f"  -> 批次处理完成。摘要: "
                        f"成功获取({tmdb_success_count}), "
                        f"新增IMDb({imdb_found_count}), "
                        f"新增元数据({metadata_added_count}), "
                        f"未找到({not_found_count})."
                    )
                    
                    # ★★★ 3. 改造数据库写入：同时操作两个表 ★★★
                    if imdb_updates_to_commit or metadata_to_commit or invalid_tmdb_ids:
                        try:
                            logger.info(f"  -> 批次完成，准备写入数据库 (IMDb更新: {len(imdb_updates_to_commit)}, 元数据更新: {len(metadata_to_commit)}, 清理: {len(invalid_tmdb_ids)})...")
                            
                            # 3.1 更新 IMDb ID
                            if imdb_updates_to_commit:
                                sql_update_imdb = "UPDATE person_identity_map SET imdb_id = ? WHERE tmdb_person_id = ?"
                                cursor.executemany(sql_update_imdb, imdb_updates_to_commit)
                            
                            # 3.2 插入或替换元数据
                            if metadata_to_commit:
                                sql_upsert_metadata = """
                                    INSERT OR REPLACE INTO ActorMetadata 
                                    (tmdb_id, profile_path, gender, adult, popularity, original_name, last_updated_at)
                                    VALUES (:tmdb_id, :profile_path, :gender, :adult, :popularity, :original_name, CURRENT_TIMESTAMP)
                                """
                                cursor.executemany(sql_upsert_metadata, metadata_to_commit)

                            # 3.3 清理无效ID
                            if invalid_tmdb_ids:
                                placeholders = ','.join('?' for _ in invalid_tmdb_ids)
                                sql_clear_tmdb = f"UPDATE person_identity_map SET tmdb_person_id = NULL WHERE tmdb_person_id IN ({placeholders})"
                                cursor.execute(sql_clear_tmdb, invalid_tmdb_ids)

                            # 3.4 统一更新同步时间
                            processed_ids_in_chunk = [actor['tmdb_person_id'] for actor in chunk]
                            if processed_ids_in_chunk:
                                placeholders_sync = ','.join('?' for _ in processed_ids_in_chunk)
                                sql_update_sync = f"UPDATE person_identity_map SET last_synced_at = CURRENT_TIMESTAMP WHERE tmdb_person_id IN ({placeholders_sync})"
                                cursor.execute(sql_update_sync, processed_ids_in_chunk)
                            
                            conn.commit()
                            logger.info("数据库更改已成功提交。")

                        except Exception as db_e:
                            logger.error(f"数据库操作失败: {db_e}", exc_info=True)
                            conn.rollback()
            else:
                logger.info("没有需要从 TMDb 补充或清理的演员。")

            # --- 阶段二：从 豆瓣 补充 IMDb ID (串行执行) ---
            if (stop_event and stop_event.is_set()) or (time.time() >= end_time): raise InterruptedError("任务中止")
            
            douban_api = DoubanApi()
            logger.info("--- 阶段二：从 豆瓣 补充 IMDb ID ---")
            cursor = conn.cursor()
            sql_find_douban_needy = f"""
                SELECT * FROM person_identity_map 
                WHERE douban_celebrity_id IS NOT NULL AND imdb_id IS NULL AND tmdb_person_id IS NULL
                AND (last_synced_at IS NULL OR last_synced_at < datetime('now', '-{SYNC_INTERVAL_DAYS} days'))
                ORDER BY last_synced_at ASC
            """
            actors_for_douban = cursor.execute(sql_find_douban_needy).fetchall()

            if actors_for_douban:
                total_douban = len(actors_for_douban)
                logger.info(f"找到 {total_douban} 位演员需要从豆瓣补充 IMDb ID。")
                
                processed_count = 0
                for i, actor in enumerate(actors_for_douban):
                    if (stop_event and stop_event.is_set()) or (time.time() >= end_time): break
                    
                    processed_count = i + 1
                    actor_map_id = actor['map_id']
                    actor_douban_id = actor['douban_celebrity_id']
                    actor_primary_name = actor['primary_name']
                    
                    try:
                        # 无论如何，先更新同步时间，避免因任何错误导致反复请求
                        sql_update_sync = "UPDATE person_identity_map SET last_synced_at = CURRENT_TIMESTAMP WHERE map_id = ?"
                        cursor.execute(sql_update_sync, (actor_map_id,))

                        details = douban_api.celebrity_details(actor_douban_id)
                        
                        if details and not details.get("error"):
                            new_imdb_id = None
                            for item in details.get("extra", {}).get("info", []):
                                if isinstance(item, list) and len(item) == 2 and item[0] == 'IMDb编号':
                                    new_imdb_id = item[1]
                                    break
                            
                            if new_imdb_id:
                                logger.info(f"  ({i+1}/{total_douban}) 为演员 '{actor_primary_name}' (Douban: {actor_douban_id}) 找到 IMDb ID: {new_imdb_id}")
                                
                                try:
                                    # 尝试直接更新
                                    sql_update_imdb = "UPDATE person_identity_map SET imdb_id = ? WHERE map_id = ?"
                                    cursor.execute(sql_update_imdb, (new_imdb_id, actor_map_id))
                                
                                # ★★★ 核心修复：捕获唯一性约束冲突的特定异常 ★★★
                                except sqlite3.IntegrityError as ie:
                                    if "UNIQUE constraint failed" in str(ie):
                                        logger.warning(f"  -> 检测到 IMDb ID '{new_imdb_id}' 冲突。将尝试合并记录。")
                                        
                                        # 1. 找到已存在该 IMDb ID 的目标记录
                                        sql_find_target = "SELECT map_id FROM person_identity_map WHERE imdb_id = ?"
                                        target_actor = cursor.execute(sql_find_target, (new_imdb_id,)).fetchone()
                                        
                                        if target_actor:
                                            target_map_id = target_actor['map_id']
                                            
                                            # 2. 将当前记录的 douban_id 合并到目标记录
                                            sql_merge_douban = "UPDATE person_identity_map SET douban_celebrity_id = ? WHERE map_id = ?"
                                            cursor.execute(sql_merge_douban, (actor_douban_id, target_map_id))
                                            
                                            # 3. 删除当前这条重复的记录
                                            sql_delete_source = "DELETE FROM person_identity_map WHERE map_id = ?"
                                            cursor.execute(sql_delete_source, (actor_map_id,))
                                            
                                            logger.info(f"  -> 成功将 '{actor_primary_name}' (map_id: {actor_map_id}) 的豆瓣ID合并到记录 (map_id: {target_map_id}) 并删除原记录。")
                                        else:
                                            # 理论上不应该发生，但作为保护
                                            logger.error(f"  -> 发生冲突但未能找到 IMDb ID '{new_imdb_id}' 的目标记录，合并失败。")
                                    else:
                                        # 如果是其他完整性错误，则重新抛出
                                        raise ie

                        # 每处理50条提交一次事务
                        if (i + 1) % 50 == 0:
                            logger.info(f"  -> 已处理50条，提交数据库事务...")
                            conn.commit()

                    except Exception as e:
                        # 修改这里的日志，使其更准确
                        logger.error(f"处理演员 '{actor_primary_name}' (Douban: {actor_douban_id}) 时发生错误: {e}")
                
                # 循环结束后，提交剩余的更改
                conn.commit()
                logger.info(f"豆瓣信息补充完成，本轮共处理 {processed_count} 个。")
            else:
                logger.info("没有需要从豆瓣补充 IMDb ID 的演员。")
            
            if douban_api:
                douban_api.close()

    except InterruptedError:
        logger.info("演员元数据增强任务被中止。")
        if conn and conn.in_transaction: conn.rollback()
    except Exception as e:
        logger.error(f"演员元数据增强任务发生严重错误: {e}", exc_info=True)
        if conn and conn.in_transaction: conn.rollback()
    finally:
        logger.info("--- “演员元数据增强”计划任务已退出 ---")