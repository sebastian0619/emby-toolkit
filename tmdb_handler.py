# tmdb_handler.py

import requests
import json
import os
import concurrent.futures
from utils import contains_chinese, normalize_name_for_matching
from typing import Optional, List, Dict, Any, Union
import logging
logger = logging.getLogger(__name__)
# TMDb API 的基础 URL
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

# 默认语言设置
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_REGION = "CN"


def _tmdb_request(endpoint: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not api_key:
        logger.error("TMDb API Key 未提供，无法发起请求。")
        return None

    full_url = f"{TMDB_API_BASE_URL}{endpoint}"
    base_params = {
        "api_key": api_key,
        "language": DEFAULT_LANGUAGE
    }
    if params:
        base_params.update(params)

    try:
        # logger.debug(f"TMDb Request: URL={full_url}, Params={base_params}")
        response = requests.get(full_url, params=base_params, timeout=15) # 增加超时
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.HTTPError as e:
        error_details = ""
        try:
            error_data = e.response.json() # type: ignore
            error_details = error_data.get("status_message", str(e))
        except json.JSONDecodeError:
            error_details = str(e)
        logger.error(f"TMDb API HTTP Error: {e.response.status_code} - {error_details}. URL: {full_url}", exc_info=False) # 减少日志冗余
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb API Request Error: {e}. URL: {full_url}", exc_info=False)
        return None
    except json.JSONDecodeError as e:
        logger.error(f"TMDb API JSON Decode Error: {e}. URL: {full_url}. Response: {response.text[:200] if response else 'N/A'}", exc_info=False)
        return None
# --- 获取电影的详细信息 ---
def get_movie_details(movie_id: int, api_key: str, append_to_response: Optional[str] = "credits,videos,images,keywords,external_ids,translations,release_dates") -> Optional[Dict[str, Any]]:
    """
    【新增】获取电影的详细信息。
    """
    endpoint = f"/movie/{movie_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response or ""
    }
    logger.trace(f"TMDb: 获取电影详情 (ID: {movie_id})")
    details = _tmdb_request(endpoint, api_key, params)
    
    # 同样为电影补充英文标题，保持逻辑一致性
    if details and details.get("original_language") != "en" and DEFAULT_LANGUAGE.startswith("zh"):
        # 优先从 translations 获取
        if "translations" in (append_to_response or "") and details.get("translations", {}).get("translations"):
            for trans in details["translations"]["translations"]:
                if trans.get("iso_639_1") == "en" and trans.get("data", {}).get("title"):
                    details["english_title"] = trans["data"]["title"]
                    logger.trace(f"  从translations补充电影英文名: {details['english_title']}")
                    break
        # 如果没有，再单独请求一次英文版
        if not details.get("english_title"):
            logger.trace(f"  尝试获取电影 {movie_id} 的英文名...")
            en_params = {"language": "en-US"}
            en_details = _tmdb_request(f"/movie/{movie_id}", api_key, en_params)
            if en_details and en_details.get("title"):
                details["english_title"] = en_details.get("title")
                logger.trace(f"  通过请求英文版补充电影英文名: {details['english_title']}")
    elif details and details.get("original_language") == "en":
        details["english_title"] = details.get("original_title")

    return details
# --- 获取电视剧的详细信息 ---
def get_tv_details_tmdb(tv_id: int, api_key: str, append_to_response: Optional[str] = "credits,videos,images,keywords,external_ids,translations,content_ratings") -> Optional[Dict[str, Any]]:
    """
    【已升级】获取电视剧的详细信息。
    """
    endpoint = f"/tv/{tv_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        # ★★★ 确保 append_to_response 不为空，即使外部没传 ★★★
        "append_to_response": append_to_response or "" 
    }
    logger.trace(f"TMDb: 获取电视剧详情 (ID: {tv_id})")
    details = _tmdb_request(endpoint, api_key, params)
    
    # 同样可以为剧集补充英文标题
    if details and details.get("original_language") != "en" and DEFAULT_LANGUAGE.startswith("zh"):
        if "translations" in (append_to_response or "") and details.get("translations", {}).get("translations"):
            for trans in details["translations"]["translations"]:
                if trans.get("iso_639_1") == "en" and trans.get("data", {}).get("name"):
                    details["english_name"] = trans["data"]["name"]
                    logger.trace(f"  从translations补充剧集英文名: {details['english_name']}")
                    break
        if not details.get("english_name"):
            logger.trace(f"  尝试获取剧集 {tv_id} 的英文名...")
            en_params = {"language": "en-US"}
            en_details = _tmdb_request(f"/tv/{tv_id}", api_key, en_params)
            if en_details and en_details.get("name"):
                details["english_name"] = en_details.get("name")
                logger.trace(f"  通过请求英文版补充剧集英文名: {details['english_name']}")
    elif details and details.get("original_language") == "en":
        details["english_name"] = details.get("original_name")

    return details
# --- 获取演员详情 ---
def get_person_details_tmdb(person_id: int, api_key: str, append_to_response: Optional[str] = "movie_credits,tv_credits,images,external_ids,translations") -> Optional[Dict[str, Any]]:
    endpoint = f"/person/{person_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    details = _tmdb_request(endpoint, api_key, params)

    # 尝试补充英文名，如果主语言是中文且original_name不是英文 (TMDb人物的original_name通常是其母语名)
    if details and details.get("name") != details.get("original_name") and DEFAULT_LANGUAGE.startswith("zh"):
        # 检查 translations 是否包含英文名
        if "translations" in (append_to_response or "") and details.get("translations", {}).get("translations"):
            for trans in details["translations"]["translations"]:
                if trans.get("iso_639_1") == "en" and trans.get("data", {}).get("name"):
                    details["english_name_from_translations"] = trans["data"]["name"]
                    logger.trace(f"  从translations补充人物英文名: {details['english_name_from_translations']}")
                    break
        # 如果 original_name 本身是英文，也可以用 (需要判断 original_name 的语言，较复杂)
        # 简单处理：如果 original_name 和 name 不同，且 name 是中文，可以认为 original_name 可能是外文名
        if details.get("original_name") and not contains_chinese(details.get("original_name", "")): # 假设 contains_chinese 在这里可用
             details["foreign_name_from_original"] = details.get("original_name")


    return details
# --- 获取电视剧某一季的详细信息 ---
def get_season_details_tmdb(tv_id: int, season_number: int, api_key: str, append_to_response: Optional[str] = "credits", item_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    【已升级】获取电视剧某一季的详细信息，并支持 item_name 用于日志。
    """
    endpoint = f"/tv/{tv_id}/season/{season_number}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    
    item_name_for_log = f"'{item_name}' " if item_name else ""
    logger.debug(f"  -> TMDb API: 获取电视剧 {item_name_for_log}(ID: {tv_id}) 第 {season_number} 季的详情...")
    
    return _tmdb_request(endpoint, api_key, params)
# --- 并发获取剧集详情 ---
def aggregate_full_series_data_from_tmdb(
    tv_id: int,
    api_key: str,
    max_workers: int = 5  # ★★★ 并发数，可以从外部配置传入 ★★★
) -> Optional[Dict[str, Any]]:
    """
    【V1 - 并发聚合版】
    通过并发请求，从 TMDB API 高效地聚合一部剧集的完整元数据（剧集、所有季、所有集）。
    """
    if not tv_id or not api_key:
        return None

    logger.info(f"  -> 开始为剧集 ID {tv_id} 并发聚合 TMDB 数据 (并发数: {max_workers})...")
    
    # --- 步骤 1: 获取顶层剧集详情，这是所有后续操作的基础 ---
    series_details = get_tv_details_tmdb(tv_id, api_key)
    if not series_details:
        logger.error(f"  -> 聚合失败：无法获取顶层剧集 {tv_id} 的详情。")
        return None
    
    logger.info(f"  -> 成功获取剧集 '{series_details.get('name')}' 的顶层信息，共 {len(series_details.get('seasons', []))} 季。")

    # --- 步骤 2: 构建所有需要并发执行的“任务” ---
    tasks = []
    # 添加获取每一季详情的任务
    for season in series_details.get("seasons", []):
        season_number = season.get("season_number")
        if season_number is not None:
            # (任务类型, tv_id, season_number)
            tasks.append(("season", tv_id, season_number))
            
            # 添加获取该季下每一集详情的任务
            episode_count = season.get("episode_count", 0)
            for episode_number in range(1, episode_count + 1):
                # (任务类型, tv_id, season_number, episode_number)
                tasks.append(("episode", tv_id, season_number, episode_number))

    if not tasks:
        logger.warning("  -> 未找到任何季或集需要获取，聚合结束。")
        return {"series_details": series_details, "seasons_details": {}, "episodes_details": {}}

    logger.info(f"  -> 共构建了 {len(tasks)} 个并发任务 (获取所有季和集的详情)。")

    # --- 步骤 3: 使用线程池并发执行所有任务 ---
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 使用字典来映射 future 和它的任务描述，方便后续处理
        future_to_task = {}
        for task in tasks:
            if task[0] == "season":
                _, tvid, s_num = task
                future = executor.submit(get_season_details_tmdb, tvid, s_num, api_key)
                future_to_task[future] = f"S{s_num}"
            elif task[0] == "episode":
                _, tvid, s_num, e_num = task
                future = executor.submit(get_episode_details_tmdb, tvid, s_num, e_num, api_key)
                future_to_task[future] = f"S{s_num}E{e_num}"

        # 收集结果
        for i, future in enumerate(concurrent.futures.as_completed(future_to_task)):
            task_key = future_to_task[future]
            try:
                result_data = future.result()
                if result_data:
                    results[task_key] = result_data
                logger.debug(f"    ({i+1}/{len(tasks)}) 任务 {task_key} 完成。")
            except Exception as exc:
                logger.error(f"    任务 {task_key} 执行时产生错误: {exc}")

    # --- 步骤 4: 将所有结果智能地聚合到一个大字典中 ---
    final_aggregated_data = {
        "series_details": series_details,
        "seasons_details": {}, # key 是季号, e.g., {1: {...}, 2: {...}}
        "episodes_details": {} # key 是 "S1E1", "S1E2", ...
    }

    for key, data in results.items():
        if key.startswith("S") and "E" not in key: # 是季
            season_num = int(key[1:])
            final_aggregated_data["seasons_details"][season_num] = data
        elif key.startswith("S") and "E" in key: # 是集
            final_aggregated_data["episodes_details"][key] = data
            
    logger.info(f"🚀 TMDB 数据并发聚合完成！成功获取 {len(final_aggregated_data['seasons_details'])} 季和 {len(final_aggregated_data['episodes_details'])} 集的详情。")
    
    return final_aggregated_data
# +++ 获取集详情 +++
def get_episode_details_tmdb(tv_id: int, season_number: int, episode_number: int, api_key: str, append_to_response: Optional[str] = "credits,videos,images,external_ids") -> Optional[Dict[str, Any]]:
    """
    【新增】获取电视剧某一集的详细信息。
    """
    endpoint = f"/tv/{tv_id}/season/{season_number}/episode/{episode_number}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.trace(f"  -> TMDb API: 获取电视剧 (ID: {tv_id}) S{season_number}E{episode_number} 的详情...")
    return _tmdb_request(endpoint, api_key, params)
# --- 通过外部ID (如 IMDb ID) 在 TMDb 上查找人物 ---
def find_person_by_external_id(external_id: str, api_key: str, source: str = "imdb_id",
                               names_for_verification: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """
    【V5 - 精确匹配版】通过外部ID查找TMDb名人信息。
    只使用最可靠的外文名 (original_name) 进行精确匹配验证。
    """
    if not all([external_id, api_key, source]):
        return None
    api_url = f"https://api.themoviedb.org/3/find/{external_id}"
    params = {"api_key": api_key, "external_source": source, "language": "en-US"}
    logger.debug(f"TMDb: 正在通过 {source} '{external_id}' 查找人物...")
    try:
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        person_results = data.get("person_results", [])
        if not person_results:
            logger.debug(f"  -> 未能通过 {source} '{external_id}' 找到任何人物。")
            return None

        person_found = person_results[0]
        tmdb_name = person_found.get('name')
        logger.debug(f"  -> 查找成功: 找到了 '{tmdb_name}' (TMDb ID: {person_found.get('id')})")

        # ★★★★★★★★★★★★★★★ 精简后的精确验证逻辑 ★★★★★★★★★★★★★★★
        if names_for_verification:
            # 1. 标准化 TMDb 返回的英文名
            normalized_tmdb_name = normalize_name_for_matching(tmdb_name)
            
            # 2. 获取我们期望的外文名 (通常来自豆瓣的 OriginalName)
            expected_original_name = names_for_verification.get("original_name")
            
            # 3. 只有在期望的外文名存在时，才进行验证
            if expected_original_name:
                normalized_expected_name = normalize_name_for_matching(expected_original_name)
                
                # 4. 进行精确比较
                if normalized_tmdb_name == normalized_expected_name:
                    logger.debug(f"  -> [验证成功 - 精确匹配] TMDb name '{tmdb_name}' 与期望的 original_name '{expected_original_name}' 匹配。")
                else:
                    # 如果不匹配，检查一下姓和名颠倒的情况
                    parts = expected_original_name.split()
                    if len(parts) > 1:
                        reversed_name = " ".join(reversed(parts))
                        if normalize_name_for_matching(reversed_name) == normalized_tmdb_name:
                            logger.debug(f"  -> [验证成功 - 精确匹配] 名字为颠倒顺序匹配。")
                            return person_found # 颠倒匹配也算成功

                    # 如果精确匹配和颠倒匹配都失败，则拒绝
                    logger.error(f"  -> [验证失败] TMDb返回的名字 '{tmdb_name}' 与期望的 '{expected_original_name}' 不符。拒绝此结果！")
                    return None
            else:
                # 如果豆瓣没有提供外文名，我们无法进行精确验证，可以选择信任或拒绝
                # 当前选择信任，但打印一条警告
                logger.warning(f"  -> [验证跳过] 未提供用于精确匹配的 original_name，将直接接受TMDb结果。")
        
        return person_found

    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb: 通过外部ID查找时发生网络错误: {e}")
        return None
# --- 获取合集的详细信息 ---
def get_collection_details_tmdb(collection_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    """
    【新】获取指定 TMDb 合集的详细信息，包含其所有影片部分。
    """
    if not collection_id or not api_key:
        return None
        
    endpoint = f"/collection/{collection_id}"
    params = {"language": DEFAULT_LANGUAGE}
    
    logger.debug(f"TMDb: 获取合集详情 (ID: {collection_id})")
    return _tmdb_request(endpoint, api_key, params)
# --- 搜索媒体 ---
def search_media(query: str, api_key: str, item_type: str = 'movie') -> Optional[List[Dict[str, Any]]]:
    """
    【V2 - 通用版】通过名字在 TMDb 上搜索媒体（电影、电视剧、演员）。
    """
    if not query or not api_key:
        return None
    
    # 根据 item_type 决定 API 的端点
    endpoint_map = {
        'movie': '/search/movie',
        'tv': '/search/tv',
        'series': '/search/tv', # series 是 tv 的别名
        'person': '/search/person'
    }
    endpoint = endpoint_map.get(item_type.lower())
    
    if not endpoint:
        logger.error(f"不支持的搜索类型: '{item_type}'")
        return None

    params = {
        "query": query,
        "include_adult": "true", # 电影搜索通常需要包含成人内容
        "language": DEFAULT_LANGUAGE
    }
    
    logger.debug(f"TMDb: 正在搜索 {item_type}: '{query}'")
    data = _tmdb_request(endpoint, api_key, params)
    
    # 如果中文搜索不到，可以尝试用英文再搜一次
    if data and not data.get("results") and params['language'].startswith("zh"):
        logger.debug(f"中文搜索 '{query}' 未找到结果，尝试使用英文再次搜索...")
        params['language'] = 'en-US'
        data = _tmdb_request(endpoint, api_key, params)

    return data.get("results") if data else None
# --- 搜索演员 ---
def search_person_tmdb(query: str, api_key: str) -> Optional[List[Dict[str, Any]]]:
    """
    【新】通过名字在 TMDb 上搜索演员。
    """
    if not query or not api_key:
        return None
    endpoint = "/search/person"
    # 我们可以添加一些参数来优化搜索，比如只搜索非成人内容，并优先中文结果
    params = {
        "query": query,
        "include_adult": "false",
        "language": DEFAULT_LANGUAGE # 使用模块内定义的默认语言
    }
    logger.debug(f"TMDb: 正在搜索演员: '{query}'")
    data = _tmdb_request(endpoint, api_key, params)
    return data.get("results") if data else None
# --- 获取演员的所有影视作品 ---
def get_person_credits_tmdb(person_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    """
    【新】获取一个演员参与的所有电影和电视剧作品。
    使用 append_to_response 来一次性获取 movie_credits 和 tv_credits。
    """
    if not person_id or not api_key:
        return None
    
    endpoint = f"/person/{person_id}"
    # ★★★ 关键：一次请求同时获取电影和电视剧作品 ★★★
    params = {
        "append_to_response": "movie_credits,tv_credits"
    }
    logger.trace(f"TMDb: 正在获取演员 (ID: {person_id}) 的所有作品...")
    
    # 这里我们直接调用 get_person_details_tmdb，因为它内部已经包含了 _tmdb_request 的逻辑
    # 并且我们不需要它的其他附加信息，所以第三个参数传我们自己的 append_to_response
    details = get_person_details_tmdb(person_id, api_key, append_to_response="movie_credits,tv_credits")

    return details
