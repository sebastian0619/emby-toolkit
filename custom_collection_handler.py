# custom_collection_handler.py
import logging
import requests
import xml.etree.ElementTree as ET
import re
import os
from typing import List, Dict, Any, Optional
import json

import tmdb_handler
import config_manager
import db_handler # 新增导入

logger = logging.getLogger(__name__)

class ListImporter:
    # ... (这部分代码保持不变) ...
    def __init__(self, tmdb_api_key: str):
        if not tmdb_api_key:
            raise ValueError("ListImporter 必须使用 TMDb API Key 进行初始化。")
        self.tmdb_api_key = tmdb_api_key
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})

    def _fetch_rss_content(self, url: str) -> Optional[str]:
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"抓取RSS源 {url} 失败: {e}")
            return None

    def _parse_rss_for_titles(self, rss_content: str) -> List[str]:
        titles = []
        try:
            root = ET.fromstring(rss_content)
            for item in root.findall('.//item'):
                title_element = item.find('title')
                if title_element is not None and title_element.text:
                    cleaned_title = re.sub(r'^\d+\.\s*', '', title_element.text).strip()
                    year_match = re.search(r'\((\d{4})\)$', cleaned_title)
                    if year_match:
                        cleaned_title = cleaned_title[:year_match.start()].strip()
                    titles.append(cleaned_title)
            return titles
        except ET.ParseError as e:
            logger.error(f"解析RSS内容失败: {e}")
            return []

    def _match_title_to_tmdb(self, title: str) -> Optional[int]:
        """(V4 - 最终校对版) 使用 tmdb_handler 中经过升级的通用搜索函数。"""
        try:
            # ★★★ 核心修复：调用我们100%确定存在的 tmdb_handler.search_media 函数 ★★★
            search_results = tmdb_handler.search_media(
                query=title,
                api_key=self.tmdb_api_key,
                item_type='movie' # 明确告诉它我们只找电影
            )
            
            if search_results:
                best_match = search_results[0]
                logger.debug(f"标题 '{title}' 成功匹配到 TMDb 电影: '{best_match.get('title')}' (ID: {best_match.get('id')})")
                return best_match.get('id')
            else:
                logger.warning(f"标题 '{title}' 未能在TMDb上找到匹配的电影。")
                return None
        except Exception as e:
            logger.error(f"通过TMDb API匹配标题 '{title}' 时出错: {e}", exc_info=True)
            return None
        
    def process(self, definition: Dict[str, Any]) -> List[int]:
        """
        【V2 - 双核版】处理 'list' 类型合集的总入口。
        能根据 definition 中的内容，智能选择使用 TMDb列表导入 或 RSS导入。
        """
        # ★★★ 核心升级：优先检查 TMDb 列表 ID ★★★
        tmdb_list_id = definition.get('tmdb_list_id')
        if tmdb_list_id:
            logger.info(f"检测到TMDb列表ID: {tmdb_list_id}，将使用TMDb API进行导入。")
            # 直接调用我们刚刚创建的新函数
            return tmdb_handler.get_movies_from_tmdb_list(tmdb_list_id, self.tmdb_api_key)

        # --- 如果没有TMDb列表ID，则回退到旧的RSS逻辑 ---
        url = definition.get('url')
        if not url:
            logger.error("榜单合集定义中既没有 'tmdb_list_id' 也没有 'url'。")
            return []

        logger.info(f"开始处理RSS榜单合集，URL: {url}")
        rss_content = self._fetch_rss_content(url)
        if not rss_content: return []
        titles = self._parse_rss_for_titles(rss_content)
        if not titles:
            logger.warning(f"未能从 {url} 中解析出任何电影标题。")
            return []
        
        logger.info(f"从RSS中解析出 {len(titles)} 个标题，开始匹配TMDb ID...")
        tmdb_ids = [self._match_title_to_tmdb(title) for title in titles if self._match_title_to_tmdb(title)]
        logger.info(f"RSS匹配完成，成功获得 {len(tmdb_ids)} 个TMDb ID。")
        return tmdb_ids

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
            if op == 'contains':
                if field in ['actors', 'directors']:
                    if any(v.get('name') and value_to_compare.lower() in v['name'].lower() for v in actual_values):
                        match = True
                else:
                    if any(value_to_compare.lower() in str(v).lower() for v in actual_values):
                        match = True
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
        【新增】为单个媒体项查找所有匹配的自定义合集。
        :param item_metadata: 新入库媒体的元数据。
        :return: 匹配到的合集列表，e.g., [{'id': 1, 'emby_collection_id': '12345'}]
        """
        logger.info(f"正在为影片《{item_metadata.get('title')}》实时匹配自定义合集...")
        matched_collections = []
        
        # 1. 获取所有启用的、基于筛选的自定义合集定义
        all_filter_collections = [
            c for c in db_handler.get_all_custom_collections(self.db_path) 
            if c['type'] == 'filter' and c['status'] == 'active' and c['emby_collection_id']
        ]

        if not all_filter_collections:
            logger.debug("没有发现任何已启用的筛选类合集，跳过匹配。")
            return []

        # 2. 遍历每个合集定义，进行匹配
        for collection_def in all_filter_collections:
            try:
                definition = json.loads(collection_def['definition_json'])
                rules = definition.get('rules', [])
                logic = definition.get('logic', 'AND')

                if self._item_matches_rules(item_metadata, rules, logic):
                    logger.info(f"  -> 匹配成功！影片《{item_metadata.get('title')}》属于合集《{collection_def['name']}》。")
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
        logger.info(f"筛选使用的 item_type_to_process: {item_type_to_process}")

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


