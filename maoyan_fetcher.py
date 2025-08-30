# maoyan_fetcher.py (V3.0 - 无 Playwright 终极版)
import logging
import requests
import argparse
import json
import random
from typing import List, Dict, Tuple
import sys
import os
import time
# ★★★ 不再需要 Playwright ★★★

# -- 关键：确保可以导入项目中的其他模块 --
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tmdb_handler
except ImportError as e:
    print(f"错误：缺少 tmdb_handler 模块。请确保路径正确。详细信息: {e}")
    sys.exit(1)

# --- 日志记录设置 ---
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

def get_random_user_agent() -> str:
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]
    return random.choice(user_agents)

# ★★★ 核心修改：用一个空函数替换掉整个 Playwright 逻辑 ★★★
def get_cookies() -> Dict[str, str]:
    """
    V3版：根据测试，API不需要Cookie即可访问。此函数保留为空，以备将来需要。
    """
    logger.debug("当前 API 无需 Cookie，跳过 Playwright 浏览器操作。")
    return {}

def get_maoyan_rank_titles(types_to_fetch: List[str], platform: str, num: int) -> Tuple[List[Dict], List[Dict]]:
    movies_list = []
    tv_list = []
    
    headers = {'User-Agent': get_random_user_agent()}
    cookies = get_cookies()

    maoyan_url = 'https://piaofang.maoyan.com'

    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 3

    # --- 1. 获取电影票房榜 (只保留重试逻辑) ---
    if 'movie' in types_to_fetch:
        url = f'{maoyan_url}/dashboard-ajax/movie'
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"正在获取电影票房榜 (第 {attempt + 1}/{MAX_RETRIES} 次尝试)...")
                response = requests.get(url, headers=headers, cookies=cookies, timeout=30)
                response.raise_for_status()
                data = response.json().get('movieList', {}).get('list', [])
                movies_list.extend([
                    {"title": movie.get('movieInfo', {}).get('movieName')}
                    for movie in data if movie.get('movieInfo', {}).get('movieName')
                ][:num])
                logger.info("电影票房榜获取成功。")
                break # 成功后立即跳出重试循环
            except Exception as e:
                logger.warning(f"获取电影票房榜失败 (第 {attempt + 1} 次尝试): {e}")
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_SECONDS * (attempt + 1)
                    logger.info(f"将在 {delay} 秒后重试...")
                    time.sleep(delay) # 这个sleep只在失败后触发，必须保留
                else:
                    logger.error("获取电影票房榜在多次重试后彻底失败。")

    # --- 2. 获取电视剧/综艺热度榜 (只保留重试逻辑) ---
    tv_heat_map = {'web-heat': '0', 'web-tv': '1', 'zongyi': '2'}
    platform_code_map = {'all': '', 'tencent': '3', 'iqiyi': '2', 'youku': '1', 'mango': '7'}
    platform_code = platform_code_map.get(platform, '')
    
    tv_types_to_fetch = [t for t in types_to_fetch if t in tv_heat_map]
    if tv_types_to_fetch:
        for tv_type in tv_types_to_fetch:
            series_type_code = tv_heat_map[tv_type]
            url = f'{maoyan_url}/dashboard/webHeatData?seriesType={series_type_code}&platformType={platform_code}&showDate=2'
            for attempt in range(MAX_RETRIES):
                try:
                    logger.info(f"正在获取热度榜 (类型: {tv_type}, 第 {attempt + 1}/{MAX_RETRIES} 次尝试)...")
                    response = requests.get(url, headers=headers, cookies=cookies, timeout=30)
                    response.raise_for_status()
                    data = response.json().get('dataList', {}).get('list', [])
                    tv_list.extend([
                        {"title": item.get('seriesInfo', {}).get('name')}
                        for item in data if item.get('seriesInfo', {}).get('name')
                    ][:num])
                    logger.info(f"热度榜 '{tv_type}' 获取成功。")
                    break # 成功后立即跳出重试循环
                except Exception as e:
                    logger.warning(f"获取 {tv_type} 热度榜失败 (第 {attempt + 1} 次尝试): {e}")
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAY_SECONDS * (attempt + 1)
                        logger.info(f"将在 {delay} 秒后重试...")
                        time.sleep(delay) # 这个sleep只在失败后触发，必须保留
                    else:
                        logger.error(f"获取 {tv_type} 热度榜在多次重试后彻底失败。")

    unique_tv_list = list({item['title']: item for item in tv_list}.values())
    return movies_list, unique_tv_list

# ... main() 和 match_titles_to_tmdb() 函数保持不变 ...
def match_titles_to_tmdb(titles: List[Dict], item_type: str, tmdb_api_key: str) -> List[Dict[str, str]]:
    matched_items = []
    for item in titles:
        title = item.get('title')
        if not title:
            continue
        
        logger.info(f"正在为 {item_type} '{title}' 搜索TMDb匹配...")
        results = tmdb_handler.search_media(title, tmdb_api_key, item_type)
        if results:
            best_match = results[0]
            tmdb_id = str(best_match.get('id'))
            match_name = best_match.get('title') if item_type == 'Movie' else best_match.get('name')
            logger.info(f"  -> 匹配成功: {match_name} (ID: {tmdb_id})")
            matched_items.append({'id': tmdb_id, 'type': item_type})
        else:
            logger.warning(f"  -> 未能为 '{title}' 找到任何TMDb匹配项。")
            
    return matched_items

def main():
    parser = argparse.ArgumentParser(description="独立的猫眼榜单获取和TMDb匹配器。")
    parser.add_argument('--api-key', required=True, help="TMDb API Key。")
    parser.add_argument('--output-file', required=True, help="用于存储结果的JSON文件路径。")
    parser.add_argument('--num', type=int, default=10, help="每个榜单获取的项目数量。")
    parser.add_argument('--types', nargs='+', default=['movie'], help="要获取的榜单类型 (例如: movie web-heat zongyi)。")
    parser.add_argument('--platform', default='all', help="平台来源 (all, tencent, iqiyi, youku, mango)。")
    args = parser.parse_args()

    logger.info(f"开始执行猫眼榜单数据抓取和匹配任务 (平台: {args.platform})...")
    
    movie_titles, tv_titles = get_maoyan_rank_titles(args.types, args.platform, args.num)
    
    matched_movies = match_titles_to_tmdb(movie_titles, 'Movie', args.api_key)
    matched_series = match_titles_to_tmdb(tv_titles, 'Series', args.api_key)
    
    all_items = matched_movies + matched_series
    unique_items = list({f"{item['type']}-{item['id']}": item for item in all_items}.values())
    
    try:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(unique_items, f, ensure_ascii=False, indent=4)
        logger.info(f"成功将 {len(unique_items)} 个项目写入到缓存文件: {args.output_file}")
    except Exception as e:
        logger.error(f"写入JSON结果文件时出错: {e}")
        sys.exit(1)
        
    logger.info("任务执行完毕。")

if __name__ == "__main__":
    main()