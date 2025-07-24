# tmdb_handler.py

import requests
import json
import os
from utils import contains_chinese
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
    logger.debug(f"TMDb: 获取电视剧详情 (ID: {tv_id})")
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
                    logger.debug(f"  从translations补充人物英文名: {details['english_name_from_translations']}")
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
    logger.debug(f"TMDb API: 获取电视剧 {item_name_for_log}(ID: {tv_id}) 第 {season_number} 季的详情...")
    
    return _tmdb_request(endpoint, api_key, params)
# --- 通过外部ID (如 IMDb ID) 在 TMDb 上查找人物 ---
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
