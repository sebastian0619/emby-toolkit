# tmdb_handler.py

import requests
import json
import os
from utils import contains_chinese
from typing import Optional, List, Dict, Any, Union
import logging
# import constants # 如果直接使用 constants.TMDB_API_KEY
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

# --- 电影相关 ---

def search_movie_tmdb(query: str, api_key: str, year: Optional[Union[int, str]] = None, page: int = 1) -> Optional[Dict[str, Any]]: # year可以是str
    endpoint = "/search/movie"
    params = {
        "query": query,
        "page": page,
        "include_adult": "false",
        "language": DEFAULT_LANGUAGE,
        "region": DEFAULT_REGION # 添加区域，可能影响搜索结果排序
    }
    if year:
        # TMDb API 文档中 primary_release_year 和 year 都可以用于电影搜索的年份过滤
        # primary_release_year 更精确，但 year 也可以工作
        params["primary_release_year"] = str(year) 
    logger.info(f"TMDb: 搜索电影 '{query}' (年份: {year or '任意'})")
    return _tmdb_request(endpoint, api_key, params)

def select_best_movie_match(query_title: str, search_results: Optional[Dict[str, Any]], original_language: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    从电影搜索结果中选择最佳匹配。
    """
    if not search_results or not search_results.get("results"):
        return None

    results = search_results["results"]
    query_title_lower = query_title.lower()

    # 优先选择标题完全匹配的
    exact_matches = [
        r for r in results 
        if r.get("title", "").lower() == query_title_lower or \
           r.get("original_title", "").lower() == query_title_lower
    ]

    if exact_matches:
        if len(exact_matches) == 1:
            return exact_matches[0]
        else:
            # 如果有多个完全匹配，尝试匹配原始语言
            if original_language:
                lang_matches = [m for m in exact_matches if m.get("original_language") == original_language.lower()]
                if lang_matches:
                    # 如果有原始语言匹配，按流行度排序选第一个
                    lang_matches.sort(key=lambda x: x.get("popularity", 0), reverse=True)
                    return lang_matches[0]
            # 如果没有原始语言信息或匹配不上，则在精确匹配中按流行度排序选第一个
            exact_matches.sort(key=lambda x: x.get("popularity", 0), reverse=True)
            return exact_matches[0]
    
    # 如果没有完全匹配的，退回到原始结果列表（通常已按相关性排序），取第一个
    # 可以增加更多模糊匹配逻辑，但会更复杂
    if results:
        logger.debug(f"电影 '{query_title}' 未找到完全匹配，返回第一个搜索结果 '{results[0].get('title')}'。")
        return results[0]
        
    return None


def get_movie_details_tmdb(movie_id: int, api_key: str, append_to_response: Optional[str] = "credits,videos,images,keywords,external_ids,translations") -> Optional[Dict[str, Any]]:
    endpoint = f"/movie/{movie_id}"
    params = {
        "language": DEFAULT_LANGUAGE, # 获取中文详情
        "append_to_response": append_to_response
    }
    # 为了获取英文标题等信息，可以考虑再请求一次英文版或查看translations
    logger.info(f"TMDb: 获取电影详情 (ID: {movie_id})")
    details = _tmdb_request(endpoint, api_key, params)
    
    # 尝试补充英文标题，如果主语言是中文且original_title不是英文
    if details and details.get("original_language") != "en" and DEFAULT_LANGUAGE.startswith("zh"):
        if "translations" in (append_to_response or "") and details.get("translations", {}).get("translations"):
            for trans in details["translations"]["translations"]:
                if trans.get("iso_639_1") == "en" and trans.get("data", {}).get("title"):
                    details["english_title"] = trans["data"]["title"]
                    logger.debug(f"  从translations补充英文标题: {details['english_title']}")
                    break
        if not details.get("english_title"): # 如果translations里没有，尝试请求一次英文版详情
            logger.debug(f"  尝试获取电影 {movie_id} 的英文标题...")
            en_params = {"language": "en-US"}
            en_details = _tmdb_request(f"/movie/{movie_id}", api_key, en_params)
            if en_details and en_details.get("title"):
                details["english_title"] = en_details.get("title")
                logger.debug(f"  通过请求英文版补充英文标题: {details['english_title']}")
    elif details and details.get("original_language") == "en":
        details["english_title"] = details.get("original_title")

    return details

def get_tv_details_tmdb(tv_id: int, api_key: str, append_to_response: Optional[str] = "credits,videos,images,keywords,external_ids,translations,content_ratings") -> Optional[Dict[str, Any]]:
    """
    【新增】获取电视剧的详细信息。
    """
    endpoint = f"/tv/{tv_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.info(f"TMDb: 获取电视剧详情 (ID: {tv_id})")
    details = _tmdb_request(endpoint, api_key, params)
    
    # 同样可以为剧集补充英文标题
    if details and details.get("original_language") != "en" and DEFAULT_LANGUAGE.startswith("zh"):
        if "translations" in (append_to_response or "") and details.get("translations", {}).get("translations"):
            for trans in details["translations"]["translations"]:
                if trans.get("iso_639_1") == "en" and trans.get("data", {}).get("name"):
                    details["english_name"] = trans["data"]["name"]
                    logger.debug(f"  从translations补充剧集英文名: {details['english_name']}")
                    break
        if not details.get("english_name"):
            logger.debug(f"  尝试获取剧集 {tv_id} 的英文名...")
            en_params = {"language": "en-US"}
            en_details = _tmdb_request(f"/tv/{tv_id}", api_key, en_params)
            if en_details and en_details.get("name"):
                details["english_name"] = en_details.get("name")
                logger.debug(f"  通过请求英文版补充剧集英文名: {details['english_name']}")
    elif details and details.get("original_language") == "en":
        details["english_name"] = details.get("original_name")

    return details

def search_person_tmdb(query: str, api_key: str, page: int = 1) -> Optional[Dict[str, Any]]:
    endpoint = "/search/person"
    params = {
        "query": query,
        "page": page,
        "include_adult": "false",
        "language": DEFAULT_LANGUAGE # 搜索时也用中文，结果可能包含中文名
    }
    logger.info(f"TMDb: 搜索人物 '{query}'")
    return _tmdb_request(endpoint, api_key, params)

def select_best_person_match(
    query_name: str, 
    search_results: Optional[Dict[str, Any]], 
    target_media_year: Optional[int] = None,
    known_for_titles: Optional[List[str]] = None # 提供一些演员的已知作品名用于匹配
) -> Optional[Dict[str, Any]]:
    """
    从人物搜索结果中选择最佳匹配。
    """
    if not search_results or not search_results.get("results"):
        return None

    results = search_results["results"]
    query_name_lower = query_name.lower()

    acting_candidates = [p for p in results if p.get("known_for_department") == "Acting"]
    if not acting_candidates:
        logger.debug(f"人物 '{query_name}' 的搜索结果中没有 'Acting' 部门的候选人。")
        return None

    # 1. 优先选择名字完全匹配的
    exact_name_matches = [
        p for p in acting_candidates
        if p.get("name", "").lower() == query_name_lower or \
           (p.get("original_name") and p.get("original_name", "").lower() == query_name_lower)
           # 可以考虑加入 p.get("also_known_as") 的匹配，但这需要先获取详情
    ]

    if not exact_name_matches: # 如果没有精确匹配，尝试模糊一点，比如包含
        logger.debug(f"人物 '{query_name}' 未找到精确名字匹配，尝试在 'Acting' 候选人中查找包含的名字。")
        # 这里可以添加更复杂的模糊匹配，或者直接使用第一个 acting_candidate
        # 为简单起见，如果没有精确匹配，我们可能返回第一个 acting_candidate (通常按TMDb相关性排序)
        # 或者，如果提供了 known_for_titles，可以尝试用它来筛选
        if known_for_titles and acting_candidates:
            best_by_known_for = None
            max_known_for_score = 0
            for candidate in acting_candidates:
                score = 0
                for work in candidate.get("known_for", []):
                    work_title = work.get("title") or work.get("name") # 电影用title, 剧集用name
                    if work_title and any(kft.lower() in work_title.lower() for kft in known_for_titles):
                        score +=1
                if score > max_known_for_score:
                    max_known_for_score = score
                    best_by_known_for = candidate
                elif score == max_known_for_score and best_by_known_for and candidate.get("popularity",0) > best_by_known_for.get("popularity",0):
                    best_by_known_for = candidate
            if best_by_known_for:
                logger.info(f"人物 '{query_name}': 通过已知作品匹配到 '{best_by_known_for.get('name')}' (ID: {best_by_known_for.get('id')})")
                return best_by_known_for
        
        if acting_candidates: # 如果还是没有，返回最受欢迎的acting candidate
             acting_candidates.sort(key=lambda p: p.get("popularity", 0), reverse=True)
             logger.info(f"人物 '{query_name}': 无精确匹配，返回最受欢迎的候选人 '{acting_candidates[0].get('name')}' (ID: {acting_candidates[0].get('id')})")
             return acting_candidates[0]
        return None


    # 如果有精确名字匹配的候选人
    if len(exact_name_matches) == 1:
        logger.info(f"人物 '{query_name}': 找到唯一精确名字匹配 '{exact_name_matches[0].get('name')}' (ID: {exact_name_matches[0].get('id')})")
        return exact_name_matches[0]
    else: # 多个精确名字匹配，需要进一步筛选
        logger.debug(f"人物 '{query_name}': 找到 {len(exact_name_matches)} 个精确名字匹配，尝试进一步筛选。")
        best_candidate = None
        highest_score = -1

        for candidate in exact_name_matches:
            current_candidate_score = candidate.get("popularity", 0) # 基础分是流行度

            # 尝试用 target_media_year 筛选
            if target_media_year:
                year_match_bonus = 0
                for work in candidate.get("known_for", []):
                    work_year_str = None
                    if work.get("media_type") == "movie" and work.get("release_date"):
                        work_year_str = work.get("release_date")[:4]
                    elif work.get("media_type") == "tv" and work.get("first_air_date"):
                        work_year_str = work.get("first_air_date")[:4]
                    
                    if work_year_str and work_year_str.isdigit():
                        work_year = int(work_year_str)
                        if abs(work_year - target_media_year) <= 2: # 年份相差2年以内，算强相关
                            year_match_bonus += 50 # 给一个较大的奖励分
                        elif abs(work_year - target_media_year) <= 5: # 5年以内，弱相关
                            year_match_bonus += 20
                current_candidate_score += year_match_bonus
            
            # 尝试用 known_for_titles 筛选
            if known_for_titles:
                known_for_bonus = 0
                for work in candidate.get("known_for", []):
                    work_title = work.get("title") or work.get("name")
                    if work_title and any(kft.lower() in work_title.lower() for kft in known_for_titles):
                        known_for_bonus += 100 # 匹配到已知作品给很高奖励
                current_candidate_score += known_for_bonus

            if current_candidate_score > highest_score:
                highest_score = current_candidate_score
                best_candidate = candidate
        
        if best_candidate:
            logger.info(f"人物 '{query_name}': 从多个精确匹配中选择 '{best_candidate.get('name')}' (ID: {best_candidate.get('id')}), Score: {highest_score}")
            return best_candidate
        else: # 理论上不应到这里，因为 exact_name_matches 不为空
            logger.warning(f"人物 '{query_name}': 无法从多个精确匹配中选出最佳，返回第一个。")
            return exact_name_matches[0]


def get_person_details_tmdb(person_id: int, api_key: str, append_to_response: Optional[str] = "movie_credits,tv_credits,images,external_ids,translations") -> Optional[Dict[str, Any]]:
    endpoint = f"/person/{person_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.info(f"TMDb: 获取人物详情 (ID: {person_id})")
    details = _tmdb_request(endpoint, api_key, params)

    # 尝试补充英文名，如果主语言是中文且original_name不是英文 (TMDb人物的original_name通常是其母语名)
    if details and details.get("name") != details.get("original_name") and DEFAULT_LANGUAGE.startswith("zh"):
        # 检查 translations 是否包含英文名
        if "translations" in (append_to_response or "") and details.get("translations", {}).get("translations"):
            for trans in details["translations"]["translations"]:
                if trans.get("iso_639_1") == "en" and trans.get("data", {}).get("name"):
                    details["english_name_from_translations"] = trans["data"]["name"]
                    logger.debug(f"  从translations补充人物英文名: {details['english_name_from_translations']}")
                    break
        # 如果 original_name 本身是英文，也可以用 (需要判断 original_name 的语言，较复杂)
        # 简单处理：如果 original_name 和 name 不同，且 name 是中文，可以认为 original_name 可能是外文名
        if details.get("original_name") and not contains_chinese(details.get("original_name", "")): # 假设 contains_chinese 在这里可用
             details["foreign_name_from_original"] = details.get("original_name")


    return details


# --- 辅助函数，用于 core_processor.py 调用 ---
def search_movie_and_get_imdb_id(
    movie_title: str, 
    api_key: str, 
    movie_year: Optional[Union[int, str]] = None,
    original_language: Optional[str] = None
) -> Optional[str]:
    """
    搜索电影，选择最佳匹配，并返回其 IMDb ID。
    """
    logger.info(f"TMDb辅助: 为电影 '{movie_title}' ({movie_year or 'N/A'}) 获取 IMDb ID...")
    search_results = search_movie_tmdb(movie_title, api_key, year=movie_year)
    best_match = select_best_movie_match(movie_title, search_results, original_language=original_language)

    if best_match and best_match.get("id"):
        movie_id = best_match["id"]
        logger.debug(f"  最佳电影匹配: '{best_match.get('title')}' (ID: {movie_id})")
        details = get_movie_details_tmdb(movie_id, api_key, append_to_response="external_ids")
        if details and details.get("external_ids", {}).get("imdb_id"):
            imdb_id = details["external_ids"]["imdb_id"]
            logger.info(f"  成功获取 IMDb ID: {imdb_id} for '{best_match.get('title')}'")
            return imdb_id
        else:
            logger.warning(f"  未能从电影详情 (ID: {movie_id}) 中获取 IMDb ID。")
    else:
        logger.warning(f"  未能为电影 '{movie_title}' 找到合适的 TMDb 匹配。")
    return None

def get_season_details_tmdb(tv_id: int, season_number: int, api_key: str, append_to_response: Optional[str] = "credits") -> Optional[Dict[str, Any]]:
    """
    【已升级】获取电视剧某一季的详细信息，默认附加credits。
    """
    endpoint = f"/tv/{tv_id}/season/{season_number}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.info(f"TMDb: 获取电视剧 {tv_id} 第 {season_number} 季的详情...")
    return _tmdb_request(endpoint, api_key, params)

def get_episode_details_tmdb(tv_id: int, season_number: int, episode_number: int, api_key: str, append_to_response: Optional[str] = "credits,guest_stars") -> Optional[Dict[str, Any]]:
    """
    【已升级】获取电视剧某一集的详细信息，默认附加credits和guest_stars。
    """
    endpoint = f"/tv/{tv_id}/season/{season_number}/episode/{episode_number}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.info(f"TMDb: 获取电视剧 {tv_id} S{season_number:02d}E{episode_number:02d} 的详情...")
    return _tmdb_request(endpoint, api_key, params)
# --- 获取完整演员表 ---
def get_full_tv_details_online(tv_id: int, api_key: str, aggregation_level: str = 'first_episode') -> Optional[Dict[str, Any]]:
    """
    【新 - 在线聚合核心】在线获取完整的电视剧详情，并聚合演员表。
    
    Args:
        tv_id (int): 电视剧的 TMDb ID.
        api_key (str): TMDb API Key.
        aggregation_level (str): 聚合级别。
            'series': 只获取剧集根级别的演员。
            'first_episode': (推荐) 聚合剧集+所有季+每季第一集。(默认)
            'full': (API消耗大) 聚合剧集+所有季+所有集。
    
    Returns:
        Optional[Dict[str, Any]]: 一个包含了聚合后演员表的、与本地缓存格式兼容的JSON对象。
    """
    # 1. 获取剧集根详情，这将是我们的基础模板
    base_details = get_tv_details_tmdb(tv_id, api_key, append_to_response="credits,casts")
    if not base_details:
        logger.error(f"无法获取电视剧 {tv_id} 的基础详情，在线聚合中止。")
        return None
    
    logger.info(f"开始为电视剧 '{base_details.get('name')}' (ID: {tv_id}) 进行在线演员聚合 (级别: {aggregation_level})...")
    
    # 使用字典来高效去重
    full_cast_map = {}

    def _add_cast_to_map(cast_list: List[Dict[str, Any]]):
        if not cast_list: return
        for actor_data in cast_list:
            actor_id = actor_data.get('id')
            if isinstance(actor_data, dict) and actor_id and actor_id not in full_cast_map:
                full_cast_map[actor_id] = actor_data

    # a. 添加根级别的演员
    root_cast = base_details.get("credits", {}).get("cast", []) or base_details.get("casts", {}).get("cast", [])
    _add_cast_to_map(root_cast)
    
    if aggregation_level == 'series':
        logger.info(f"聚合级别为 'series'，聚合完成。")
    else:
        # b. 遍历所有季
        number_of_seasons = base_details.get("number_of_seasons", 0)
        for season_num in range(1, number_of_seasons + 1): # 季号从1开始
            season_details = get_season_details_tmdb(tv_id, season_num, api_key, append_to_response="credits")
            if season_details:
                # 添加季级别的演员
                _add_cast_to_map(season_details.get("credits", {}).get("cast", []))
                
                if aggregation_level == 'first_episode':
                    # 只获取第一集
                    if season_details.get("episodes") and len(season_details["episodes"]) > 0:
                        ep_details = get_episode_details_tmdb(tv_id, season_num, 1, api_key, append_to_response="credits,guest_stars")
                        if ep_details:
                            _add_cast_to_map(ep_details.get("credits", {}).get("cast", []))
                            _add_cast_to_map(ep_details.get("guest_stars", []))
                
                elif aggregation_level == 'full':
                    # 获取所有集 (API消耗大)
                    for episode in season_details.get("episodes", []):
                        ep_num = episode.get("episode_number")
                        if ep_num:
                            ep_details = get_episode_details_tmdb(tv_id, season_num, ep_num, api_key, append_to_response="credits,guest_stars")
                            if ep_details:
                                _add_cast_to_map(ep_details.get("credits", {}).get("cast", []))
                                _add_cast_to_map(ep_details.get("guest_stars", []))

    # 4. 将聚合后的完整演员列表写回基础模板
    final_cast_list = list(full_cast_map.values())
    # 保持TMDb原始的order排序
    final_cast_list.sort(key=lambda x: x.get('order') if x.get('order') is not None else 999)
    
    # 确保 credits.cast 存在
    if "credits" not in base_details: base_details["credits"] = {}
    base_details["credits"]["cast"] = final_cast_list
    
    logger.info(f"在线聚合完成，共获得 {len(final_cast_list)} 位独立演员。")
    
    return base_details
def get_person_details_for_cast(person_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    """
    【新增】获取单个演员的详细信息，并初步格式化以用于 cast 列表。
    这个函数只负责获取数据，不处理中文名和角色。
    """
    # 我们复用已有的 get_person_details_tmdb 函数，但只请求最基础的信息以提高效率
    details = get_person_details_tmdb(person_id, api_key, append_to_response=None)
    
    if not details:
        return None
    
    # 返回一个符合 cast 列表基本结构的字典
    # 这个结构是基于您提供的 JSON 示例
    return {
        "adult": details.get("adult", False),
        "gender": details.get("gender"),
        "id": details.get("id"),
        "known_for_department": details.get("known_for_department"),
        "name": details.get("name"), # 这是 TMDB 的名字，后面会被我们的中文名覆盖
        "original_name": details.get("original_name"), # 这是真正的原始名
        "popularity": details.get("popularity"),
        "profile_path": details.get("profile_path"),
        "cast_id": None, # 这些字段通常由电影/剧集详情提供，这里先设为None或默认值
        "character": "", # 后面会被我们的角色名覆盖
        "credit_id": None,
        "order": 0
    }

def find_person_by_external_id(external_id: str, api_key: str, source: str = "imdb_id") -> Optional[Dict[str, Any]]:
    """
    【新】通过外部ID (如 IMDb ID) 在 TMDb 上查找人物。
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
        if person_results:
            person_found = person_results[0]
            logger.debug(f"  -> 查找成功: 找到了 '{person_found.get('name')}' (TMDb ID: {person_found.get('id')})")
            return person_found
        else:
            logger.debug(f"  -> 未能通过 {source} '{external_id}' 找到任何人物。")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb: 通过外部ID查找时发生网络错误: {e}")
        return None

# --- 示例用法 (测试时可以取消注释) ---
# if __name__ == '__main__':
#     TEST_API_KEY = os.environ.get("TMDB_API_KEY_TEST", None)
#     if not TEST_API_KEY:
#         try:
#             # from dotenv import load_dotenv
#             # from dotenv import find_dotenv
#             # 尝试从当前工作目录或上级目录加载 .env 文件
#             env_path = find_dotenv(usecwd=True, raise_error_if_not_found=False)
#             if env_path:
#                 logger.info(f"从 {env_path} 加载 .env 文件")
#                 load_dotenv(env_path)
#                 TEST_API_KEY = os.environ.get("TMDB_API_KEY_TEST", None)
#             else:
#                 logger.info(".env 文件未找到。")
#         except ImportError:
#             logger.info("'python-dotenv' 未安装，无法从 .env 文件加载 API Key。")
#             pass

#     if not TEST_API_KEY:
#         # 如果仍然没有API Key，可以提示用户或使用一个占位符（但实际请求会失败）
#         TEST_API_KEY = "42985eb2e0cbdf2b2c88f2f30990be40" # 替换为你的真实API Key进行测试
#         if TEST_API_KEY == "YOUR_TMDB_API_KEY_PLACEHOLDER":
#             logger.error("错误：请设置 TMDB_API_KEY_TEST 环境变量，或在项目根目录创建 .env 文件并定义 TMDB_API_KEY_TEST，或直接在代码中提供测试 API Key。")
#             # exit() # 如果没有key，可以选择退出测试

#     if TEST_API_KEY and TEST_API_KEY != "YOUR_TMDB_API_KEY_PLACEHOLDER":
#         logger.info(f"使用 TMDb API Key: {TEST_API_KEY[:5]}... 进行测试")

#         # --- 测试电影搜索与选择 ---
#         logger.info("\n=== 测试电影搜索与选择 ===")
        
#         movie_test_cases = [
#             {"query": "盗梦空间", "year": 2010, "original_language": "en", "expected_id": 27205, "comment": "中文名，精确年份"},
#             {"query": "Inception", "year": 2010, "original_language": "en", "expected_id": 27205, "comment": "英文名，精确年份"},
#             {"query": "黑客帝国", "year": None, "original_language": "en", "expected_id": 603, "comment": "无年份，应选第一部"},
#             {"query": "玩具总动员", "year": 1995, "original_language": "en", "expected_id": 862, "comment": "经典动画"},
#             {"query": "流浪地球", "year": 2019, "original_language": "zh", "expected_id": 530175, "comment": "中文电影"},
#             {"query": "一个不存在的电影标题XYZ123", "year": 2023, "comment": "测试无结果"},
#         ]

#         for case in movie_test_cases:
#             logger.info(f"\n--- 测试电影: '{case['query']}' (年份: {case.get('year', 'N/A')}, 原始语言: {case.get('original_language', 'N/A')}) ---")
#             raw_search_results = search_movie_tmdb(case["query"], TEST_API_KEY, year=case.get("year"))
            
#             if raw_search_results and raw_search_results.get("results"):
#                 logger.debug(f"  原始搜索结果 (前3条):")
#                 for i, r_movie in enumerate(raw_search_results["results"]):
#                     if i < 3:
#                         logger.debug(f"    - '{r_movie.get('title')}' (ID: {r_movie.get('id')}, Pop: {r_movie.get('popularity')}, Year: {r_movie.get('release_date', '')[:4]}, Lang: {r_movie.get('original_language')})")
#                     else:
#                         break
#             elif raw_search_results:
#                  logger.debug(f"  原始搜索结果为空列表。")
#             else:
#                 logger.debug(f"  原始搜索API调用失败或未返回结果。")

#             selected_movie = select_best_movie_match(case["query"], raw_search_results, original_language=case.get("original_language"))
            
#             if selected_movie:
#                 logger.info(f"  >> 选中电影: '{selected_movie.get('title')}' (ID: {selected_movie.get('id')}, Pop: {selected_movie.get('popularity')}, Year: {selected_movie.get('release_date', '')[:4]}, Lang: {selected_movie.get('original_language')})")
#                 if "expected_id" in case and selected_movie.get("id") != case["expected_id"]:
#                     logger.warning(f"    !! 注意: 选中ID {selected_movie.get('id')} 与期望ID {case['expected_id']} 不符。")
                
#                 # 测试 get_movie_details_tmdb 和补充英文标题
#                 movie_details = get_movie_details_tmdb(selected_movie.get("id"), TEST_API_KEY)
#                 if movie_details:
#                     logger.debug(f"    获取详情: 标题='{movie_details.get('title')}', 原标题='{movie_details.get('original_title')}', 补充英文标题='{movie_details.get('english_title', 'N/A')}'")
#                     logger.debug(f"    IMDb ID from details: {movie_details.get('external_ids', {}).get('imdb_id', 'N/A')}")

#             else:
#                 logger.info(f"  >> 未能为 '{case['query']}' 选中任何电影。")
#                 if "expected_id" in case: # 如果期望有结果但没有，也提示
#                     logger.warning(f"    !! 注意: 期望找到电影 (ID: {case['expected_id']}) 但未选中任何结果。")
#             logger.info("--------------------------------------------------")

#         # --- 测试人物搜索与选择 ---
#         logger.info("\n\n=== 测试人物搜索与选择 ===")
#         person_test_cases = [
#             {"query": "莱昂纳多·迪卡普里奥", "target_media_year": 2010, "known_for_titles": ["Inception", "盗梦空间"], "expected_id": 6193, "comment": "中文名，带年份和作品提示"},
#             {"query": "Leonardo DiCaprio", "target_media_year": 1997, "known_for_titles": ["Titanic"], "expected_id": 6193, "comment": "英文名，带年份和作品提示"},
#             {"query": "刘德华", "target_media_year": None, "known_for_titles": ["无间道"], "expected_id": 3810, "comment": "无年份，有作品提示"},
#             {"query": "斯嘉丽·约翰逊", "target_media_year": 2019, "known_for_titles": ["Avengers", "Marriage Story"], "expected_id": 1245, "comment": "多作品提示"},
#             {"query": "张三李四王五XYZ", "comment": "测试无结果"}, # 测试一个不太可能存在的名字
#             {"query": "李", "comment": "测试常见姓氏，可能匹配不准或返回多个"}, # 测试常见姓氏
#         ]

#         for case in person_test_cases:
#             logger.info(f"\n--- 测试人物: '{case['query']}' (年份提示: {case.get('target_media_year', 'N/A')}, 作品提示: {case.get('known_for_titles', 'N/A')}) ---")
#             raw_person_results = search_person_tmdb(case["query"], TEST_API_KEY)

#             if raw_person_results and raw_person_results.get("results"):
#                 logger.debug(f"  原始搜索结果 (前3条 'Acting' 部门):")
#                 count = 0
#                 for r_person in raw_person_results["results"]:
#                     if r_person.get("known_for_department") == "Acting":
#                         logger.debug(f"    - '{r_person.get('name')}' (ID: {r_person.get('id')}, Pop: {r_person.get('popularity')}, Dept: {r_person.get('known_for_department')})")
#                         known_for_preview = [wk.get('title', wk.get('name', 'N/A')) for wk in r_person.get("known_for", [])[:2]]
#                         logger.debug(f"      Known for (preview): {known_for_preview}")
#                         count += 1
#                     if count >= 3:
#                         break
#                 if count == 0:
#                      logger.debug(f"  原始搜索结果中未找到 'Acting' 部门的演员。")
#             elif raw_person_results:
#                 logger.debug(f"  原始搜索结果为空列表。")
#             else:
#                 logger.debug(f"  原始搜索API调用失败或未返回结果。")

#             selected_person = select_best_person_match(
#                 case["query"], 
#                 raw_person_results, 
#                 target_media_year=case.get("target_media_year"),
#                 known_for_titles=case.get("known_for_titles")
#             )

#             if selected_person:
#                 logger.info(f"  >> 选中人物: '{selected_person.get('name')}' (ID: {selected_person.get('id')}, Pop: {selected_person.get('popularity')})")
#                 if "expected_id" in case and selected_person.get("id") != case["expected_id"]:
#                     logger.warning(f"    !! 注意: 选中ID {selected_person.get('id')} 与期望ID {case['expected_id']} 不符。")
                
#                 # 测试 get_person_details_tmdb
#                 person_details = get_person_details_tmdb(selected_person.get("id"), TEST_API_KEY)
#                 if person_details:
#                     logger.debug(f"    获取详情: 姓名='{person_details.get('name')}', 原名='{person_details.get('original_name')}', 补充英文名='{person_details.get('english_name_from_translations', 'N/A')}', 补充外文原名='{person_details.get('foreign_name_from_original', 'N/A')}'")
#                     logger.debug(f"    IMDb ID from details: {person_details.get('external_ids', {}).get('imdb_id', 'N/A')}")
#             else:
#                 logger.info(f"  >> 未能为 '{case['query']}' 选中任何人物。")
#                 if "expected_id" in case:
#                     logger.warning(f"    !! 注意: 期望找到人物 (ID: {case['expected_id']}) 但未选中任何结果。")
#             logger.info("--------------------------------------------------")
            
#         # 测试辅助函数 search_movie_and_get_imdb_id
#         logger.info("\n\n=== 测试辅助函数 search_movie_and_get_imdb_id ===")
#         test_movie_for_imdb = "阿凡达"
#         test_movie_year_for_imdb = 2009
#         imdb_id_result = search_movie_and_get_imdb_id(test_movie_for_imdb, TEST_API_KEY, movie_year=test_movie_year_for_imdb)
#         if imdb_id_result:
#             logger.info(f"为 '{test_movie_for_imdb}' ({test_movie_year_for_imdb}) 获取到的 IMDb ID: {imdb_id_result} (期望: tt0499549)")
#         else:
#             logger.warning(f"未能为 '{test_movie_for_imdb}' ({test_movie_year_for_imdb}) 获取 IMDb ID。")


#         logger.info("\n--- TMDb Handler 所有测试结束 ---")

#     else: # API Key 未配置
#         logger.error("TMDb API Key 未配置，跳过 TMDb Handler 测试。")