# api_processor.py
# (此文件包含了 main 分支的核心处理逻辑，并已修改为无类模式)

import time
import sqlite3
import threading
from typing import Dict, List, Optional, Any

# 导入项目内的其他模块
import emby_handler
import utils
import constants
from logger_setup import logger

# 尝试导入 DoubanApi，如果失败则使用一个假的替代品
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

# --- 全局的停止事件，用于控制任务中止 ---
_stop_event = threading.Event()

# --- 辅助函数 (从 main 分支的 MediaProcessor 类中提取并修改) ---

def _get_db_connection(config: dict) -> sqlite3.Connection:
    """获取数据库连接"""
    db_path = config.get('db_path')
    if not db_path:
        raise ValueError("数据库路径 (db_path) 未在配置中提供。")
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

def _translate_actor_field(text: Optional[str], config: dict, db_cursor: sqlite3.Cursor) -> Optional[str]:
    """翻译演员字段 (简化版，只处理翻译，不关心AI/传统引擎细节)"""
    if not text or not text.strip() or utils.contains_chinese(text):
        return text
    
    # 这里的翻译逻辑可以根据需要从 main 分支的 _translate_actor_field 完整复制过来
    # 为简化，我们先用一个通用的翻译调用
    translation_result = utils.translate_text_with_translators(
        text.strip(),
        engine_order=config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
    )
    if translation_result and translation_result.get("text"):
        return translation_result["text"]
    return text

def _process_cast_list(current_emby_cast: List[Dict[str, Any]], media_info: Dict[str, Any], config: dict) -> List[Dict[str, Any]]:
    """
    处理演员列表的核心逻辑 (从 main 分支的 _process_cast_list 移植并简化)。
    这个函数只负责丰富信息，不新增演员。
    """
    final_cast_list = [dict(actor) for actor in current_emby_cast] # 创建一个副本进行操作

    # 如果需要，可以在这里加入从豆瓣获取数据并进行匹配、丰富的逻辑
    # 为确保最小改动能跑通，我们先只做翻译
    
    try:
        with _get_db_connection(config) as conn:
            cursor = conn.cursor()
            for actor in final_cast_list:
                if _stop_event.is_set(): break
                
                # 翻译演员名
                actor['Name'] = _translate_actor_field(actor.get('Name'), config, cursor)
                # 翻译角色名
                actor['Role'] = _translate_actor_field(actor.get('Role'), config, cursor)
    except Exception as e:
        logger.error(f"【API模式】处理演员列表时数据库操作失败: {e}", exc_info=True)

    return final_cast_list

def _process_item_core_logic(item_details: Dict[str, Any], config: dict) -> bool:
    """处理单个媒体项的核心逻辑 (从 main 分支移植)"""
    item_id = item_details.get("Id")
    item_name = item_details.get("Name", f"ID:{item_id}")
    
    original_cast = item_details.get("People", [])
    logger.info(f"【API模式】为 '{item_name}' 处理演员，原始数量: {len(original_cast)}")

    # 调用处理函数
    final_cast = _process_cast_list(original_cast, item_details, config)

    # 写回 Emby
    logger.info(f"【API模式】准备将 {len(final_cast)} 位演员写回 '{item_name}'...")
    update_success = emby_handler.update_emby_item_cast(
        item_id=item_id,
        new_cast_list_for_handler=final_cast,
        emby_server_url=config.get("emby_server_url"),
        emby_api_key=config.get("emby_api_key"),
        user_id=config.get("emby_user_id")
    )

    if update_success:
        logger.info(f"【API模式】成功更新 '{item_name}' 的演员信息。")
        # API 模式下，我们简化逻辑，默认处理成功
        return True
    else:
        logger.error(f"【API模式】更新 '{item_name}' 的演员信息失败。")
        return False

# --- 主要任务函数 (供 web_app.py 调用) ---

def process_full_library(config: dict, update_status_callback: callable, force_reprocess_all: bool, process_episodes: bool):
    """
    API模式下的全量扫描任务 (从 main 分支的 process_full_library 移植并修改)
    """
    _stop_event.clear()
    logger.info("【API模式】开始全量扫描...")
    
    if not all([config.get("emby_server_url"), config.get("emby_api_key"), config.get("emby_user_id")]):
        logger.error("【API模式】Emby配置不完整，无法处理。")
        if update_status_callback: update_status_callback(-1, "Emby配置不完整")
        return

    libs_to_process = config.get("libraries_to_process", [])
    if not libs_to_process:
        logger.warning("【API模式】未配置要处理的媒体库。")
        if update_status_callback: update_status_callback(100, "未配置媒体库")
        return

    movies = emby_handler.get_emby_library_items(config.get("emby_url"), config.get("emby_api_key"), "Movie", config.get("emby_user_id"), libs_to_process) or []
    if _stop_event.is_set(): return
    series = emby_handler.get_emby_library_items(config.get("emby_url"), config.get("emby_api_key"), "Series", config.get("emby_user_id"), libs_to_process) or []
    if _stop_event.is_set(): return

    all_items = movies + series
    total = len(all_items)
    logger.info(f"【API模式】获取到 {total} 个项目待处理。")

    for i, item in enumerate(all_items):
        if _stop_event.is_set():
            logger.info("【API模式】全量扫描被用户中止。")
            break

        item_id = item.get('Id')
        item_name = item.get('Name', f"ID:{item_id}")
        
        if update_status_callback:
            progress = int(((i + 1) / total) * 100)
            update_status_callback(progress, f"API模式处理 ({i+1}/{total}): {item_name}")

        # 获取完整详情并处理
        item_details = emby_handler.get_emby_item_details(item_id, config.get("emby_server_url"), config.get("emby_api_key"), config.get("emby_user_id"))
        if item_details:
            _process_item_core_logic(item_details, config)
        else:
            logger.error(f"【API模式】无法获取项目 {item_id} 的详情，跳过。")

        delay = float(config.get("delay_between_items_sec", 0.5))
        time.sleep(delay)

    if not _stop_event.is_set() and update_status_callback:
        update_status_callback(100, "API模式处理完成")

def process_single_item_via_api(config: dict, item_id: str):
    """
    API模式下处理单个项目的入口 (供 webhook 或手动重新处理调用)
    """
    _stop_event.clear()
    
    item_details = emby_handler.get_emby_item_details(item_id, config.get("emby_server_url"), config.get("emby_api_key"), config.get("emby_user_id"))
    if item_details:
        _process_item_core_logic(item_details, config)
    else:
        logger.error(f"【API模式】无法获取项目 {item_id} 的详情，处理中止。")

def signal_stop_api_task():
    """外部调用此函数来停止任务"""
    logger.info("【API模式】收到停止信号。")
    _stop_event.set()