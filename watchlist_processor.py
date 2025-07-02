# watchlist_processor.py

import sqlite3
import time
import json
import os
import copy
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import threading
from actor_utils import get_db_connection as get_central_db_connection
# 导入我们需要的辅助模块
import tmdb_handler
import emby_handler
import logging

logger = logging.getLogger(__name__)
# ✨✨✨ 状态翻译字典 ✨✨✨
TMDB_STATUS_TRANSLATION = {
    "Ended": "已完结",
    "Canceled": "已取消",
    "Returning Series": "连载中",
    "In Production": "制作中",
    "Planned": "计划中"
}

def translate_status(status: str) -> str:
    """一个简单的辅助函数，用于翻译状态，如果找不到翻译则返回原文。"""
    return TMDB_STATUS_TRANSLATION.get(status, status)
class WatchlistProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            raise ValueError("数据库路径 (db_path) 未在配置中提供。")
        
        self.tmdb_api_key = self.config.get("tmdb_api_key", "")
        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.local_data_path = self.config.get("local_data_path", "")
        self._stop_event = threading.Event()
        logger.debug("WatchlistProcessor 初始化完成。")

    # --- 线程控制方法 ---
    def signal_stop(self): self._stop_event.set()
    def clear_stop_signal(self): self._stop_event.clear()
    def is_stop_requested(self) -> bool: return self._stop_event.is_set()
    def close(self): logger.debug("WatchlistProcessor closed.")

    # --- 数据库和文件辅助方法 ---
    def _read_local_json(self, file_path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取本地JSON文件失败: {file_path}, 错误: {e}")
            return None

    # --- 自动添加追剧列表的方法 ---
    def add_series_to_watchlist(self, item_details: Dict[str, Any]):
        """
        检查剧集状态，如果是“在播中”，则添加到追剧列表。
        """
        if item_details.get("Type") != "Series":
            return

        tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
        item_name = item_details.get("Name")
        item_id = item_details.get("Id")

        if not tmdb_id:
            logger.debug(f"剧集 '{item_name}' 缺少 TMDb ID，无法进行自动追剧判断。")
            return

        if not self.tmdb_api_key:
            logger.warning("未配置TMDb API Key，无法进行自动追剧判断。")
            return
            
        tmdb_details = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key)
        if not tmdb_details:
            logger.warning(f"无法从TMDb获取 '{item_name}' 的详情，无法进行自动追剧判断。")
            return

        tmdb_status = tmdb_details.get("status")
        translated_tmdb_status = translate_status(tmdb_status)
        if tmdb_status in ["Returning Series", "In Production", "Planned"]:
            try:
                with get_central_db_connection(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT OR IGNORE INTO watchlist (item_id, tmdb_id, item_name, item_type, status)
                        VALUES (?, ?, ?, ?, 'Watching')
                    """, (item_id, tmdb_id, item_name, "Series"))
                    
                    if cursor.rowcount > 0:
                        logger.info(f"剧集 '{item_name}' (TMDb状态: {translated_tmdb_status}) 已自动加入追剧列表。")
                    else:
                        logger.debug(f"剧集 '{item_name}' 已存在于追剧列表，无需重复添加。")
            except Exception as e:
                logger.error(f"自动添加剧集 '{item_name}' 到追剧列表时发生数据库错误: {e}", exc_info=True)
        else:
            logger.debug(f"剧集 '{item_name}' 的TMDb状态为 '{translated_tmdb_status}'，不符合自动添加条件。")

    # ★★★ 新增：处理单个剧集的核心逻辑，私有方法 ★★★
    def _process_one_series(self, series_data: Dict[str, Any]):
        """
        处理单个剧集的完整逻辑：刷新元数据、检查状态、更新数据库。
        这是所有追剧处理的统一入口点。
        """
        item_id = series_data['item_id']
        tmdb_id = series_data['tmdb_id']
        item_name = series_data['item_name']
        
        logger.info(f"正在处理剧集: '{item_name}' (TMDb ID: {tmdb_id})")

        if not self.tmdb_api_key:
            logger.warning("未配置TMDb API Key，跳过。")
            return
        
        # 准备路径和缓存数据
        override_dir = os.path.join(self.local_data_path, "override", "tmdb-tv", tmdb_id)
        os.makedirs(override_dir, exist_ok=True)
        cache_dir = os.path.join(self.local_data_path, "cache", "tmdb-tv", tmdb_id)
        cache_series_json = self._read_local_json(os.path.join(cache_dir, "series.json")) or {}
        actor_cast_data = cache_series_json.get("credits", {}).get("cast", [])

        # 1. 无论如何，先执行一次完整的元数据刷新
        refresh_success, all_episodes = self._refresh_and_save_series_metadata(
            tmdb_id, item_name, override_dir, actor_cast_data
        )

        if not refresh_success:
            logger.error(f"'{item_name}' 元数据刷新失败，将在下次任务中重试。")
            return

        # 2. 刷新后，检查TMDb上的最新状态
        try:
            with open(os.path.join(override_dir, "series.json"), 'r', encoding='utf-8') as f:
                latest_series_data = json.load(f)
            new_status = latest_series_data.get("status")
        except Exception as e:
            logger.error(f"读取最新的 series.json 文件失败: {e}，无法检查剧集状态。")
            return

        # 3. 根据最新状态决定下一步操作
        is_ended = new_status in ["Ended", "Canceled"]
        
        translated_new_status = translate_status(new_status)
        
        if is_ended:
            logger.info(f"剧集 '{item_name}' 的TMDb状态为 '{translated_new_status}'，开始检查元数据完整性...")
            if self._check_all_episodes_have_overview(all_episodes):
                logger.info(f"'{item_name}' 已完结且所有集信息完整，将从追剧列表移除（状态更新为 'Completed'）。")
                self._update_watchlist_status(item_id, 'Completed', item_name)
            else:
                logger.warning(f"'{item_name}' 虽然已完结，但部分集信息不完整。将保留在追剧列表中，下次继续检查。")
                self._update_watchlist_timestamp(item_id, item_name)
        else:
            logger.info(f"剧集 '{item_name}' 仍在连载中 (状态: {translated_new_status})，已更新元数据。")
            self._update_watchlist_timestamp(item_id, item_name)

        # 4. 触发Emby/Jellyfin刷新
        emby_handler.refresh_emby_item_metadata(item_id, self.emby_url, self.emby_api_key, item_name_for_log=item_name)

    # ★★★ 统一的、公开的追剧处理入口 ★★★
    def process_watching_list(self, item_id: Optional[str] = None):
        """
        【V7 - 统一重构版】处理追剧列表。
        如果提供了 item_id，则只处理该项目。
        否则，处理所有状态为 'Watching' 的项目。
        """
        if item_id:
            logger.info(f"--- 开始执行单项追剧更新任务 (ItemID: {item_id}) ---")
        else:
            logger.info("--- 开始执行全量追剧列表更新任务 ---")
        
        series_to_process = []
        try:
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                if item_id:
                    cursor.execute("SELECT * FROM watchlist WHERE item_id = ?", (item_id,))
                else:
                    cursor.execute("SELECT * FROM watchlist WHERE status = 'Watching'")
                
                series_to_process = [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"获取追剧列表时发生数据库错误: {e}")
            return

        if not series_to_process:
            if item_id:
                logger.warning(f"未在追剧列表中找到项目 {item_id} 或其状态不为 'Watching'，任务中止。")
            else:
                logger.info("追剧列表中没有需要检查的剧集。")
            return

        if not item_id:
             logger.info(f"发现 {len(series_to_process)} 部剧集需要检查更新...")

        for series in series_to_process:
            if self.is_stop_requested():
                logger.info("追剧列表更新任务被中止。")
                break
            
            # 调用统一的处理方法
            self._process_one_series(series)
            
            time.sleep(1) # 在处理下一个剧集前稍作停顿

        logger.info("--- 追剧列表更新任务结束 ---")

    # --- 智能完结逻辑所需的辅助方法 ---
    def _refresh_and_save_series_metadata(self, tmdb_id: str, item_name: str, override_dir: str, actor_cast_data: List[Dict]) -> Tuple[bool, List[Dict[str, Any]]]:
        """刷新并保存一个剧集的完整元数据（剧集、季、集）。"""
        base_series_data = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key)
        if not base_series_data:
            logger.warning(f"无法从TMDb获取 '{item_name}' 的基础详情，跳过本次更新。")
            return False, []

        final_series_data = base_series_data
        final_series_data.setdefault("credits", {})["cast"] = actor_cast_data
        try:
            with open(os.path.join(override_dir, "series.json"), 'w', encoding='utf-8') as f:
                json.dump(final_series_data, f, ensure_ascii=False, indent=4)
            logger.info(f"  已为 '{item_name}' 更新总的 series.json。")
        except Exception as e:
            logger.error(f"  写入 series.json 时失败: {e}")
            return False, []

        all_episodes_data = []
        # 直接从剧集详情中获取实际存在的季列表
        existing_seasons = base_series_data.get("seasons", [])
        logger.info(f"'{item_name}' 在TMDb上找到 {len(existing_seasons)} 个季/特别篇，开始生成元数据文件...")
        
        # ★★★ 这是新的、精准的循环方式 ★★★
        for season_info in existing_seasons:
            season_num = season_info.get("season_number")
            
            # 如果 season_number 不存在，则跳过，保证代码健壮性
            if season_num is None:
                continue

            # 日志现在会更精确地指出正在获取哪一季
            logger.info(f"  正在获取第 {season_num} 季 ('{season_info.get('name')}') 的详情...")
            season_details = tmdb_handler.get_season_details_tmdb(tmdb_id, season_num, self.tmdb_api_key)
            
            if not season_details:
                # 这里的警告现在更有意义，因为它代表一个明确存在的季获取失败了
                logger.warning(f"  获取第 {season_num} 季的详情失败，跳过。")
                continue
            
            season_details.setdefault("credits", {})["cast"] = actor_cast_data
            try:
                with open(os.path.join(override_dir, f"season-{season_num}.json"), 'w', encoding='utf-8') as f:
                    json.dump(season_details, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"    写入 season-{season_num}.json 时失败: {e}")

            for episode_details in season_details.get("episodes", []):
                episode_num = episode_details.get("episode_number")
                if episode_num is None: continue
                
                episode_details.setdefault("credits", {})["cast"] = actor_cast_data
                all_episodes_data.append(episode_details)
                
                try:
                    with open(os.path.join(override_dir, f"season-{season_num}-episode-{episode_num}.json"), 'w', encoding='utf-8') as f:
                        json.dump(episode_details, f, ensure_ascii=False, indent=4)
                except Exception as e:
                    logger.error(f"    写入 season-{season_num}-episode-{episode_num}.json 时失败: {e}")
            time.sleep(0.2)
        return True, all_episodes_data

    def _check_all_episodes_have_overview(self, all_episodes: List[Dict[str, Any]]) -> bool:
        """检查一个剧集的所有集是否都有简介(overview)。"""
        if not all_episodes:
            return True

        missing_overview_episodes = [
            f"S{ep.get('season_number', 'N/A'):02d}E{ep.get('episode_number', 'N/A'):02d}"
            for ep in all_episodes if not ep.get("overview")
        ]

        if missing_overview_episodes:
            logger.warning(f"  元数据不完整，以下集缺少简介: {', '.join(missing_overview_episodes)}")
            return False
        
        logger.info("  元数据完整性检查通过，所有集都有简介。")
        return True

    def _update_watchlist_status(self, item_id: str, status: str, item_name: str):
        """更新数据库中指定项目的状态。"""
        try:
            with get_central_db_connection(self.db_path) as conn:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE watchlist SET status = ?, last_checked_at = ? WHERE item_id = ?", (status, current_time, item_id))
            logger.info(f"  成功更新 '{item_name}' 在数据库中的状态为 '{status}'。")
        except Exception as e:
            logger.error(f"  更新 '{item_name}' 状态为 '{status}' 时数据库出错: {e}")

    def _update_watchlist_timestamp(self, item_id: str, item_name: str):
        """仅更新数据库中指定项目的 last_checked_at 时间戳。"""
        try:
            with get_central_db_connection(self.db_path) as conn:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE watchlist SET last_checked_at = ? WHERE item_id = ?", (current_time, item_id))
        except Exception as e:
            logger.error(f"更新 '{item_name}' 的 last_checked_at 时间戳时失败: {e}")
    

