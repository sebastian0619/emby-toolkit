# custom_collection_handler.py
import logging
import requests
import xml.etree.ElementTree as ET
import re
import os
from typing import List, Dict, Any, Optional, Tuple
import json
from datetime import datetime, timedelta

import tmdb_handler
import config_manager
import db_handler # 新增导入
from tmdb_handler import search_media, get_tv_details_tmdb

logger = logging.getLogger(__name__)


class ListImporter:
    """
    【V5 - 姿势正确版】
    采用“先搜索、再验证”的正确流程处理带季号的剧集标题。
    """
    
    SEASON_PATTERN = re.compile(r'(.*?)\s*[（(]?\s*(第?[一二三四五六七八九十百]+)\s*季\s*[)）]?$')
    CHINESE_NUM_MAP = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
        '第一': 1, '第二': 2, '第三': 3, '第四': 4, '第五': 5, '第六': 6, '第七': 7, '第八': 8, '第九': 9, '第十': 10,
        '第十一': 11, '第十二': 12, '第十三': 13, '第十四': 14, '第十五': 15
    }

    def __init__(self, tmdb_api_key: str):
        self.tmdb_api_key = tmdb_api_key
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})

    def _get_titles_from_url(self, url: str) -> List[str]:
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            content = response.text
            titles = re.findall(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', content)
            return titles[1:] if titles else []
        except requests.RequestException as e:
            logger.error(f"从URL '{url}' 获取榜单时出错: {e}")
            return []

    def _parse_series_title(self, title: str) -> Tuple[str, Optional[int]]:
        """
        解析标题，返回 (剧集主名称, 季号或None)。
        """
        match = self.SEASON_PATTERN.search(title)
        if not match:
            return title, None # 没有季号信息

        show_name = match.group(1).strip()
        season_word = match.group(2)
        season_number = self.CHINESE_NUM_MAP.get(season_word)
        
        if season_number is None:
            return title, None # 无法识别的季号，当作没有处理
            
        logger.debug(f"标题解析: '{title}' -> 名称='{show_name}', 季号='{season_number}'")
        return show_name, season_number

    def _match_title_to_tmdb(self, title: str, item_type: str) -> Optional[str]:
        """
        在TMDb上查找标题并返回TMDb ID，对剧集进行季号验证。
        """
        if item_type == 'Movie':
            results = search_media(title, self.tmdb_api_key, 'Movie')
            if results:
                tmdb_id = str(results[0].get('id'))
                logger.debug(f"电影标题 '{title}' 成功匹配到: {results[0].get('title')} (ID: {tmdb_id})")
                return tmdb_id
            else:
                logger.warning(f"电影标题 '{title}' 未能在TMDb上找到匹配项。")
                return None

        elif item_type == 'Series':
            # 1. 解析标题
            show_name, season_number_to_validate = self._parse_series_title(title)
            
            # 2. 搜索剧集主名称
            results = search_media(show_name, self.tmdb_api_key, 'Series')
            if not results:
                logger.warning(f"剧集标题 '{title}' (搜索词: '{show_name}') 未能在TMDb上找到匹配项。")
                return None
            
            # 拿到最匹配的剧集的ID
            series_result = results[0]
            series_id = str(series_result.get('id'))
            
            # 3. 如果没有季号要求，直接返回成功
            if season_number_to_validate is None:
                logger.debug(f"剧集标题 '{title}' 成功匹配到: {series_result.get('name')} (ID: {series_id})")
                return series_id

            # 4. 如果有季号要求，则必须进行验证
            logger.debug(f"剧集 '{show_name}' (ID: {series_id}) 已找到，正在验证是否存在第 {season_number_to_validate} 季...")
            series_details = get_tv_details_tmdb(int(series_id), self.tmdb_api_key, append_to_response="seasons")
            
            if series_details and 'seasons' in series_details:
                for season in series_details['seasons']:
                    if season.get('season_number') == season_number_to_validate:
                        logger.info(f"验证成功！剧集 '{show_name}' 存在第 {season_number_to_validate} 季。最终匹配ID为 {series_id}。")
                        return series_id # 验证成功，返回剧集主ID
            
            logger.warning(f"验证失败！剧集 '{show_name}' (ID: {series_id}) 存在，但未找到第 {season_number_to_validate} 季。")
            return None
            
        return None

    def process(self, definition: Dict) -> List[str]:
        url = definition.get('url')
        item_type = definition.get('item_type', 'Movie')
        if not url:
            return []

        titles = self._get_titles_from_url(url)
        if not titles:
            return []

        tmdb_ids = []
        for title in titles:
            cleaned_title = re.sub(r'\s*\(\d{4}\)$', '', title).strip()
            tmdb_id = self._match_title_to_tmdb(cleaned_title, item_type)
            if tmdb_id:
                tmdb_ids.append(tmdb_id)
        
        logger.info(f"RSS匹配完成，成功获得 {len(tmdb_ids)} 个TMDb ID。")
        return list(dict.fromkeys(tmdb_ids))

class FilterEngine:
    """
    【V2 - 升级版】负责处理 'filter' 类型的自定义合集。
    新增了对单个媒体项进行实时匹配的功能。
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.country_map = self._load_country_map()

    def _load_country_map(self) -> Dict[str, str]:
        """加载国家/地区中英文映射文件。"""
        # 注意：这里的路径是相对于后端运行位置的。
        # 在你的项目中，可能需要调整为绝对路径或相对于项目根目录的路径。
        # 假设 countries.json 与你的前端资源文件放在一起，并且后端可以访问到。
        # 一个常见的做法是把这种共享配置放在一个后端也能访问的目录。
        # 为简单起见，我们假设它在 'assets' 目录下。
        try:
            # 这是一个示例路径，你可能需要根据你的项目结构调整！
            # 假设你的前端代码在 'frontend' 或 'dist' 文件夹内
            # 这里我们用一个相对路径，假设 assets 文件夹在上一级的某个地方
            # 更健壮的方法是使用绝对路径或环境变量
            map_path = os.path.join(config_manager.PERSISTENT_DATA_PATH, 'countries.json')
            if not os.path.exists(map_path):
                 # 备用路径，适配开发环境
                 map_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'assets', 'countries.json')

            with open(map_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 我们需要一个 "中文 -> 英文" 的反向映射
                return {v: k for k, v in data.items()}
        except Exception as e:
            logger.error(f"加载国家/地区映射文件失败: {e}。国家/地区筛选可能无法正常工作。")
            return {}

    def _item_matches_rules(self, item_metadata: Dict[str, Any], rules: List[Dict[str, Any]], logic: str) -> bool:
        if not rules: return True
        results = []
        for rule in rules:
            field, op, value = rule.get("field"), rule.get("operator"), rule.get("value")
            
            # ★★★ 核心修复：在这里进行反向映射 ★★★
            value_to_compare = value
            if field == 'countries' and value in self.country_map:
                value_to_compare = self.country_map[value] # e.g., "香港" -> "Hong Kong"
                logger.debug(f"国家/地区反向映射: '{value}' -> '{value_to_compare}'")

            # ... (后续的匹配逻辑完全复用我们之前的代码) ...
            if field == 'release_year':
                item_value_raw = item_metadata.get('release_year')
                actual_values = [item_value_raw] if item_value_raw is not None else []
            else:
                item_value_raw = item_metadata.get(f"{field}_json")
                try: actual_values = json.loads(item_value_raw) if item_value_raw else []
                except (json.JSONDecodeError, TypeError): actual_values = []

            match = False
            if field in ['release_date', 'date_added']:
                item_date_str = item_metadata.get(field)
                if item_date_str:
                    try:
                        item_date = datetime.strptime(item_date_str, '%Y-%m-%d').date()
                        today = datetime.now().date()
                        
                        if op == 'in_last_days':
                            days = int(value)
                            cutoff_date = today - timedelta(days=days)
                            if item_date >= cutoff_date and item_date <= today:
                                match = True
                        elif op == 'not_in_last_days':
                            days = int(value)
                            cutoff_date = today - timedelta(days=days)
                            if item_date < cutoff_date:
                                match = True
                    except (ValueError, TypeError):
                        pass # 日期格式错误或值无效，跳过
            elif op == 'gte':
                if actual_values and actual_values[0] >= int(value_to_compare): match = True
            elif op == 'lte': # ★★★ 补全 'lte' 和 'eq' 的逻辑 ★★★
                if actual_values and actual_values[0] <= int(value_to_compare): match = True
            elif op == 'eq':
                if actual_values and actual_values[0] == int(value_to_compare): match = True
            
            results.append(match)

        if logic.upper() == 'AND': return all(results)
        else: return any(results)

    def find_matching_collections(self, item_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        【V2 - 修正版】为单个媒体项查找所有匹配的自定义合集。
        增加了对内容类型的严格检查。
        """
        # ★★★ 核心修正 1：动态生成日志中的媒体类型名称 ★★★
        media_item_type = item_metadata.get('item_type')
        media_type_cn = "剧集" if media_item_type == "Series" else "影片"
        
        logger.info(f"正在为{media_type_cn}《{item_metadata.get('title')}》实时匹配自定义合集...")
        matched_collections = []
        
        all_filter_collections = [
            c for c in db_handler.get_all_custom_collections(self.db_path) 
            if c['type'] == 'filter' and c['status'] == 'active' and c['emby_collection_id']
        ]

        if not all_filter_collections:
            logger.debug("没有发现任何已启用的筛选类合集，跳过匹配。")
            return []

        for collection_def in all_filter_collections:
            try:
                definition = json.loads(collection_def['definition_json'])
                
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                # ★★★ 核心修正 2：在检查规则之前，首先检查内容类型是否匹配！ ★★★
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                collection_item_type = definition.get('item_type', 'Movie') # 合集定义的内容类型
                if media_item_type != collection_item_type:
                    logger.trace(f"  -> 跳过合集《{collection_def['name']}》，因为内容类型不匹配 (需要: {collection_item_type}, 实际: {media_item_type})。")
                    continue # 如果类型不匹配，直接跳过这个合集，不进行后续规则检查

                rules = definition.get('rules', [])
                logic = definition.get('logic', 'AND')

                if self._item_matches_rules(item_metadata, rules, logic):
                    logger.info(f"  -> 匹配成功！{media_type_cn}《{item_metadata.get('title')}》属于合集《{collection_def['name']}》。")
                    matched_collections.append({
                        'id': collection_def['id'],
                        'name': collection_def['name'],
                        'emby_collection_id': collection_def['emby_collection_id']
                    })
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"解析合集《{collection_def['name']}》的定义时出错: {e}，跳过。")
                continue
        
        return matched_collections
    
    def execute_filter(self, definition: Dict[str, Any]) -> List[int]:
        """
        【V3 - 绝对最终修复版】根据规则，从整个媒体库中筛选出所有匹配的电影或剧集。
        修正了未正确使用 item_type 的Bug。
        """
        logger.info("筛选引擎：开始执行全库扫描以生成合集...")
        
        rules = definition.get('rules', [])
        logic = definition.get('logic', 'AND')
        # ★★★ 核心修复：从定义中获取要筛选的媒体类型，默认为 Movie ★★★
        item_type_to_process = definition.get('item_type', 'Movie')
        logger.trace(f"筛选使用的 item_type_to_process: {item_type_to_process}")

        if not rules:
            logger.warning("合集定义中没有任何规则，将返回空列表。")
            return []

        # ★★★ 核心修复：将 item_type_to_process 传递给数据库函数 ★★★
        all_media_metadata = db_handler.get_all_media_metadata(self.db_path, item_type=item_type_to_process)
        
        log_item_type_cn = "电影" if item_type_to_process == "Movie" else "电视剧"

        if not all_media_metadata:
            logger.warning(f"本地媒体元数据缓存中没有找到任何 {log_item_type_cn} 类型的项目。")
            return []
            
        logger.info(f"已加载 {len(all_media_metadata)} 条{log_item_type_cn}元数据，开始应用筛选规则...")

        matched_tmdb_ids = []
        for media_metadata in all_media_metadata:
            if self._item_matches_rules(media_metadata, rules, logic):
                tmdb_id_str = media_metadata.get('tmdb_id')
                if tmdb_id_str:
                    try:
                        matched_tmdb_ids.append(int(tmdb_id_str))
                    except (ValueError, TypeError):
                        logger.warning(f"发现无效的TMDb ID: {tmdb_id_str}，已跳过。")

        logger.info(f"筛选完成！共找到 {len(matched_tmdb_ids)} 部匹配的 {log_item_type_cn}。")
        return matched_tmdb_ids


