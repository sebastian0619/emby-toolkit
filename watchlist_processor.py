# watchlist_processor.py

import sqlite3
import time
import json
import os
import copy
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
import threading
from db_handler import get_db_connection as get_central_db_connection
# 导入我们需要的辅助模块
import tmdb_handler
import emby_handler
import logging

logger = logging.getLogger(__name__)
# ✨✨✨ Tmdb状态翻译字典 ✨✨✨
TMDB_STATUS_TRANSLATION = {
    "Ended": "已完结",
    "Canceled": "已取消",
    "Returning Series": "连载中",
    "In Production": "制作中",
    "Planned": "计划中"
}
# ★★★ 内部状态翻译字典，用于日志显示 ★★★
INTERNAL_STATUS_TRANSLATION = {
    'Watching': '追剧中',
    'Paused': '已暂停',
    'Completed': '已完结'
}
# ★★★ 新增：定义状态常量，便于维护 ★★★
STATUS_WATCHING = 'Watching'
STATUS_PAUSED = 'Paused'
STATUS_COMPLETED = 'Completed'
def translate_status(status: str) -> str:
    """一个简单的辅助函数，用于翻译状态，如果找不到翻译则返回原文。"""
    return TMDB_STATUS_TRANSLATION.get(status, status)
def translate_internal_status(status: str) -> str:
    """★★★ 新增：一个辅助函数，用于翻译内部状态，用于日志显示 ★★★"""
    return INTERNAL_STATUS_TRANSLATION.get(status, status)
class WatchlistProcessor:
    """
    【V11 - 最终版智能管家】
    实现基于待播日期的三态(Watching, Paused, Completed)自动转换，
    并包含一个独立的、用于低频检查已完结剧集“复活”的方法。
    """
    def __init__(self, config: Dict[str, Any]):
        if not isinstance(config, dict):
            raise TypeError(f"配置参数(config)必须是一个字典，但收到了 {type(config).__name__} 类型。")
        self.config = config
        self.db_path = self.config.get('db_path')
        if not self.db_path:
            raise ValueError("数据库路径 (db_path) 未在配置中提供。")
        self.tmdb_api_key = self.config.get("tmdb_api_key", "")
        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.local_data_path = self.config.get("local_data_path", "")
        self._stop_event = threading.Event()
        self.progress_callback = None
        logger.trace("WatchlistProcessor 初始化完成。")

    # --- 线程控制 ---
    def signal_stop(self): self._stop_event.set()
    def clear_stop_signal(self): self._stop_event.clear()
    def is_stop_requested(self) -> bool: return self._stop_event.is_set()
    def close(self): logger.trace("WatchlistProcessor closed.")

    # --- 数据库和文件辅助方法 ---
    def _read_local_json(self, file_path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(file_path): return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception as e:
            logger.error(f"读取本地JSON文件失败: {file_path}, 错误: {e}")
            return None

    def _update_watchlist_entry(self, item_id: str, item_name: str, updates: Dict[str, Any]):
        """统一更新追剧列表中的一个条目。"""
        try:
            with get_central_db_connection(self.db_path) as conn:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updates['last_checked_at'] = current_time
                
                set_clauses = [f"{key} = ?" for key in updates.keys()]
                values = list(updates.values())
                values.append(item_id)
                
                sql = f"UPDATE watchlist SET {', '.join(set_clauses)} WHERE item_id = ?"
                conn.execute(sql, tuple(values))
                logger.info(f"  成功更新数据库中 '{item_name}' 的追剧信息。")
        except Exception as e:
            logger.error(f"  更新 '{item_name}' 的追剧信息时数据库出错: {e}")
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

    # --- 核心任务启动器 ---
    def run_regular_processing_task(self, progress_callback: callable, item_id: Optional[str] = None):
        """【常规任务】处理所有活跃的（Watching/Paused到期）剧集。"""
        self.progress_callback = progress_callback
        task_name = "常规追剧更新"
        if item_id: task_name = f"单项追剧更新 (ID: {item_id})"
        
        self.progress_callback(0, "准备检查待更新剧集...")
        try:
            today_str = datetime.now().date().isoformat()
            active_series = self._get_series_to_process(
                f"WHERE status = '{STATUS_WATCHING}' OR (status = '{STATUS_PAUSED}' AND paused_until <= '{today_str}')",
                item_id
            )
            total = len(active_series)
            if total > 0:
                self.progress_callback(5, f"开始处理 {total} 部待更新/待唤醒的剧集...")
                for i, series in enumerate(active_series):
                    if self.is_stop_requested(): break
                    progress = 5 + int(((i + 1) / total) * 95)
                    self.progress_callback(progress, f"处理中: {series['item_name'][:15]}... ({i+1}/{total})")
                    self._process_one_series(series)
                    time.sleep(1)
            else:
                self.progress_callback(100, "没有需要立即处理的剧集。")
            
            if not self.is_stop_requested():
                self.progress_callback(100, "常规追剧检查任务完成。")
        except Exception as e:
            logger.error(f"执行 '{task_name}' 时发生严重错误: {e}", exc_info=True)
            self.progress_callback(-1, f"错误: {e}")
        finally:
            self.progress_callback = None

    # ★★★ 专门用于“复活检查”的任务方法 ★★★
    def run_revival_check_task(self, progress_callback: callable):
        """【低频任务】检查所有已完结剧集是否“复活”。"""
        self.progress_callback = progress_callback
        task_name = "已完结剧集复活检查"
        self.progress_callback(0, "准备开始复活检查...")
        try:
            completed_series = self._get_series_to_process(f"WHERE status = '{STATUS_COMPLETED}'")
            total = len(completed_series)
            if not completed_series:
                self.progress_callback(100, "没有已完结的剧集需要检查。")
                return

            logger.info(f"开始低频检查 {total} 部已完结剧集是否复活...")
            self.progress_callback(10, f"发现 {total} 部已完结剧集，开始检查...")
            revived_count = 0

            for i, series in enumerate(completed_series):
                if self.is_stop_requested(): break
                progress = 10 + int(((i + 1) / total) * 90)
                self.progress_callback(progress, f"检查中: {series['item_name'][:20]}... ({i+1}/{total})")

                tmdb_details = tmdb_handler.get_tv_details_tmdb(series['tmdb_id'], self.tmdb_api_key)
                if not tmdb_details: continue

                new_status = tmdb_details.get('status')
                if new_status not in ["Ended", "Canceled"]:
                    logger.warning(f"检测到剧集 '{series['item_name']}' 已复活！状态从 '{series.get('tmdb_status')}' 变为 '{new_status}'。")
                    revived_count += 1
                    self._update_watchlist_entry(series['item_id'], series['item_name'], {
                        "status": STATUS_WATCHING,
                        "paused_until": None,
                        "tmdb_status": new_status
                    })
                time.sleep(2)
            
            final_message = f"复活检查完成。共发现 {revived_count} 部剧集回归。"
            self.progress_callback(100, final_message)

        except Exception as e:
            logger.error(f"执行 '{task_name}' 时发生严重错误: {e}", exc_info=True)
            self.progress_callback(-1, f"错误: {e}")
        finally:
            self.progress_callback = None

    def _get_series_to_process(self, where_clause: str, item_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        【V11 - 修复版】从数据库获取需要处理的剧集列表。
        - 支持传入自定义的 WHERE 子句来筛选剧集。
        - 如果提供了 item_id，则无视 WHERE 子句，强制只处理该项目。
        """
        try:
            with get_central_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # ★★★ 核心修复：将传入的 where_clause 作为查询的基础 ★★★
                query = f"SELECT * FROM watchlist {where_clause}"
                params = []
                
                # 如果指定了item_id，则无视where_clause，强制处理这一部
                if item_id:
                    query = "SELECT * FROM watchlist WHERE item_id = ?"
                    params.append(item_id)
                
                cursor.execute(query, tuple(params))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取追剧列表时发生数据库错误: {e}")
            return []
    # ★★★ 核心处理逻辑：单个剧集的所有操作在此完成 ★★★
    def _process_one_series(self, series_data: Dict[str, Any]):
        item_id = series_data['item_id']
        tmdb_id = series_data['tmdb_id']
        item_name = series_data['item_name']
        
        logger.info(f"正在处理剧集: '{item_name}' (TMDb ID: {tmdb_id})")
        if not self.tmdb_api_key:
            logger.warning("未配置TMDb API Key，跳过。")
            return
        
        override_dir = os.path.join(self.local_data_path, "override", "tmdb-tv", tmdb_id)
        os.makedirs(override_dir, exist_ok=True)
        cache_dir = os.path.join(self.local_data_path, "cache", "tmdb-tv", tmdb_id)
        cache_series_json = self._read_local_json(os.path.join(cache_dir, "series.json")) or {}
        actor_cast_data = cache_series_json.get("credits", {}).get("cast", [])

        refresh_success, all_tmdb_episodes = self._refresh_and_save_series_metadata(
            tmdb_id, item_name, override_dir, actor_cast_data
        )
        if not refresh_success:
            logger.error(f"'{item_name}' 元数据刷新失败，将在下次任务中重试。")
            return

        try:
            with open(os.path.join(override_dir, "series.json"), 'r', encoding='utf-8') as f:
                latest_series_data = json.load(f)
        except Exception as e:
            logger.error(f"读取最新的 series.json 文件失败: {e}，无法继续处理。")
            return

        emby_children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
        emby_seasons = {}
        if emby_children:
            for child in emby_children:
                s_num, e_num = child.get('ParentIndexNumber'), child.get('IndexNumber')
                if s_num is not None and e_num is not None:
                    emby_seasons.setdefault(s_num, set()).add(e_num)

        new_tmdb_status = latest_series_data.get("status")
        is_ended_on_tmdb = new_tmdb_status in ["Ended", "Canceled"]
        
        real_next_episode_to_air = self._calculate_real_next_episode(all_tmdb_episodes, emby_seasons)
        missing_info = self._calculate_missing_info(latest_series_data.get('seasons', []), all_tmdb_episodes, emby_seasons)
        has_missing_media = bool(missing_info["missing_seasons"] or missing_info["missing_episodes"])

        final_status = STATUS_WATCHING
        paused_until_date = None

        if is_ended_on_tmdb and not has_missing_media:
            final_status = STATUS_COMPLETED
            logger.info(f"  -> 剧集已完结且本地完整，状态变更为: {translate_internal_status(final_status)}")
        elif real_next_episode_to_air and real_next_episode_to_air.get('air_date'):
            air_date_str = real_next_episode_to_air['air_date']
            air_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()
            days_until_air = (air_date - datetime.now().date()).days
            
            logger.debug(f"  -> 下一集播出日期: {air_date_str}, 距离今天: {days_until_air} 天。")

            if days_until_air > 3:
                final_status = STATUS_PAUSED
                paused_until_date = air_date - timedelta(days=1)
                logger.info(f"  -> 下一集在3天后播出，状态变更为: {translate_internal_status(final_status)}，暂停至 {paused_until_date}。")
            else:
                final_status = STATUS_WATCHING
                logger.info(f"  -> 下一集即将在3天内播出或已播出，状态保持为: {translate_internal_status(final_status)}。")
        else:
            final_status = STATUS_PAUSED
            paused_until_date = datetime.now().date() + timedelta(days=7)
            logger.info(f"  -> 暂无待播信息 (季歇期)，状态变更为: {translate_internal_status(final_status)}，暂停7天。")

        updates_to_db = {
            "status": final_status,
            "paused_until": paused_until_date.isoformat() if paused_until_date else None,
            "tmdb_status": new_tmdb_status,
            "next_episode_to_air_json": json.dumps(real_next_episode_to_air) if real_next_episode_to_air else None,
            "missing_info_json": json.dumps(missing_info)
        }
        self._update_watchlist_entry(item_id, item_name, updates_to_db)
        emby_handler.refresh_emby_item_metadata(item_id, self.emby_url, self.emby_api_key, item_name_for_log=item_name)

    # ★★★ 统一的、公开的追剧处理入口 ★★★
    def process_watching_list(self, item_id: Optional[str] = None):
        if item_id:
            logger.info(f"--- 开始执行单项追剧更新任务 (ItemID: {item_id}) ---")
        else:
            logger.info("--- 开始执行全量追剧列表更新任务 ---")
        
        series_to_process = []
        try:
            with get_central_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                query = "SELECT * FROM watchlist WHERE status = 'Watching'"
                params = []
                if item_id:
                    query = "SELECT * FROM watchlist WHERE item_id = ?"
                    params.append(item_id)
                cursor.execute(query, params)
                series_to_process = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取追剧列表时发生数据库错误: {e}")
            return

        if not series_to_process:
            logger.info("追剧列表中没有需要检查的剧集。")
            return

        total = len(series_to_process)
        logger.info(f"发现 {total} 部剧集需要检查更新...")

        for i, series in enumerate(series_to_process):
            if self.is_stop_requested():
                logger.info("追剧列表更新任务被中止。")
                break
            
            if self.progress_callback:
                progress = 10 + int(((i + 1) / total) * 90)
                self.progress_callback(progress, f"正在处理: {series['item_name'][:20]}... ({i+1}/{total})")

            self._process_one_series(series)
            time.sleep(1)

        logger.info("--- 追剧列表更新任务结束 ---")

    # --- 通过对比计算真正的下一待看集 ---
    def _calculate_real_next_episode(self, all_tmdb_episodes: List[Dict], emby_seasons: Dict) -> Optional[Dict]:
        """
        【逻辑重生】通过对比本地和TMDb全量数据，计算用户真正缺失的第一集。
        """
        # 1. 获取TMDb上所有非特别季的剧集，并严格按季号、集号排序
        all_episodes_sorted = sorted([
            ep for ep in all_tmdb_episodes 
            if ep.get('season_number') is not None and ep.get('season_number') != 0
        ], key=lambda x: (x.get('season_number', 0), x.get('episode_number', 0)))
        
        # 2. 遍历这个完整列表，找到第一个本地没有的剧集
        for episode in all_episodes_sorted:
            s_num = episode.get('season_number')
            e_num = episode.get('episode_number')
            
            if s_num not in emby_seasons or e_num not in emby_seasons.get(s_num, set()):
                # 找到了！这无论是否播出，都是用户最关心的下一集
                logger.info(f"  找到本地缺失的第一集: S{s_num}E{e_num} ('{episode.get('name')}'), 将其设为待播集。")
                return episode
        
        # 3. 如果循环完成，说明本地拥有TMDb上所有的剧集
        logger.info("  本地媒体库已拥有TMDb上所有剧集，无待播信息。")
        return None
    # --- 计算缺失的季和集 ---
    def _calculate_missing_info(self, tmdb_seasons: List[Dict], all_tmdb_episodes: List[Dict], emby_seasons: Dict) -> Dict:
        """
        【逻辑重生】计算所有缺失的季和集，不再关心播出日期。
        """
        missing_info = {"missing_seasons": [], "missing_episodes": []}
        
        tmdb_episodes_by_season = {}
        for ep in all_tmdb_episodes:
            s_num = ep.get('season_number')
            if s_num is not None and s_num != 0:
                tmdb_episodes_by_season.setdefault(s_num, []).append(ep)

        for season_summary in tmdb_seasons:
            s_num = season_summary.get('season_number')
            if s_num is None or s_num == 0: 
                continue

            # 如果本地没有这个季，则整个季都算缺失
            if s_num not in emby_seasons:
                missing_info["missing_seasons"].append(season_summary)
            else:
                # 如果季存在，则逐集检查缺失
                if s_num in tmdb_episodes_by_season:
                    for episode in tmdb_episodes_by_season[s_num]:
                        e_num = episode.get('episode_number')
                        if e_num is not None and e_num not in emby_seasons.get(s_num, set()):
                            missing_info["missing_episodes"].append(episode)
        return missing_info
    # --- 智能完结逻辑所需的辅助方法 ---
    def _refresh_and_save_series_metadata(self, tmdb_id: str, item_name: str, override_dir: str, actor_cast_data: List[Dict]) -> Tuple[bool, List[Dict[str, Any]]]:
        """刷新并保存一个剧集的完整元数据（剧集、季、集）。"""
        base_series_data = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key)
        if not base_series_data:
            logger.warning(f"无法从TMDb获取 '{item_name}' 的基础详情，跳过本次更新。")
            return False, []

        final_series_data = base_series_data
        final_series_data.setdefault("credits", {})["cast"] = actor_cast_data
        final_series_data["name"] = item_name
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
        logger.info(f"'{item_name}' 在TMDb上找到 {len(existing_seasons)} 个季，开始生成元数据文件...")
        
        # ★★★ 这是新的、精准的循环方式 ★★★
        for season_info in existing_seasons:
            season_num = season_info.get("season_number")
            
            # ★★★ 新增：硬编码忽略所有第0季（特别篇）★★★
            if season_num == 0:
                logger.info(f"  -> 忽略第 0 季 (特别篇)。")
                continue
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

        # ★★★ 修改：硬编码忽略所有第0季（特别篇）★★★
        missing_overview_episodes = [
            f"S{ep.get('season_number', 'N/A'):02d}E{ep.get('episode_number', 'N/A'):02d}"
            for ep in all_episodes if not ep.get("overview") and ep.get("season_number") != 0
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
    

