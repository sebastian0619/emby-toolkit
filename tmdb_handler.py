# tmdb_handler.py

import requests
import json
import os
from typing import Optional, List, Dict, Any
from logger_setup import logger # 假设你的 logger 在这里
# import constants # 如果直接使用 constants.TMDB_API_KEY

# TMDb API 的基础 URL
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

# 默认语言设置 (可以考虑从配置中读取)
DEFAULT_LANGUAGE = "zh-CN" # 中文优先
DEFAULT_REGION = "CN"    # 区域设置，影响某些结果的本地化

def _tmdb_request(endpoint: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    通用的 TMDb API 请求函数。
    :param endpoint: API 端点路径 (例如 "/movie/{movie_id}")
    :param api_key: TMDb API Key
    :param params: 请求参数字典
    :return: 解析后的 JSON 数据字典，或在失败时返回 None
    """
    if not api_key:
        logger.error("TMDb API Key 未提供，无法发起请求。")
        return None

    full_url = f"{TMDB_API_BASE_URL}{endpoint}"
    base_params = {
        "api_key": api_key,
        "language": DEFAULT_LANGUAGE # 默认语言
    }
    if params:
        base_params.update(params)

    try:
        # logger.debug(f"TMDb Request: URL={full_url}, Params={base_params}")
        response = requests.get(full_url, params=base_params, timeout=10)
        response.raise_for_status() # 如果是 4xx 或 5xx 错误，则抛出异常
        data = response.json()
        # logger.debug(f"TMDb Response: Status={response.status_code}, Data (preview)={str(data)[:200]}")
        return data
    except requests.exceptions.HTTPError as e:
        error_details = ""
        try:
            error_data = e.response.json() # type: ignore
            error_details = error_data.get("status_message", str(e))
        except json.JSONDecodeError:
            error_details = str(e)
        logger.error(f"TMDb API HTTP Error: {e.response.status_code} - {error_details}. URL: {full_url}", exc_info=True) # type: ignore
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb API Request Error: {e}. URL: {full_url}", exc_info=True)
        return None
    except json.JSONDecodeError as e:
        logger.error(f"TMDb API JSON Decode Error: {e}. URL: {full_url}. Response: {response.text[:200] if response else 'N/A'}", exc_info=True)
        return None

def search_movie_tmdb(query: str, api_key: str, year: Optional[int] = None, page: int = 1) -> Optional[Dict[str, Any]]:
    """
    通过名称搜索电影。
    :param query: 搜索关键词 (电影名称)
    :param api_key: TMDb API Key
    :param year: 电影年份 (可选，用于精确搜索)
    :param page: 分页页码 (可选)
    :return: 搜索结果字典，包含 'results', 'page', 'total_pages', 'total_results'
    """
    endpoint = "/search/movie"
    params = {
        "query": query,
        "page": page,
        "include_adult": "false", # 通常不搜索成人内容，除非特别需要
        "language": DEFAULT_LANGUAGE,
        "region": DEFAULT_REGION
    }
    if year:
        params["primary_release_year"] = year # 或 year，取决于API文档
    logger.info(f"TMDb: 搜索电影 '{query}' (年份: {year or '任意'})")
    return _tmdb_request(endpoint, api_key, params)

def get_movie_details_tmdb(movie_id: int, api_key: str, append_to_response: Optional[str] = "credits,videos,images,keywords,external_ids") -> Optional[Dict[str, Any]]:
    """
    获取指定电影的详细信息，包括演员表 (credits)。
    :param movie_id: TMDb 电影 ID
    :param api_key: TMDb API Key
    :param append_to_response: 附加到响应的数据，例如 'credits,videos,images'
    :return: 电影详情字典
    """
    endpoint = f"/movie/{movie_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.info(f"TMDb: 获取电影详情 (ID: {movie_id})")
    return _tmdb_request(endpoint, api_key, params)

def get_movie_credits_tmdb(movie_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    """
    单独获取指定电影的演职员信息。
    :param movie_id: TMDb 电影 ID
    :param api_key: TMDb API Key
    :return: 演职员信息字典，包含 'cast' 和 'crew'
    """
    # get_movie_details_tmdb 已经可以通过 append_to_response="credits" 获取，
    # 但如果只需要 credits，可以调用这个专用端点。
    endpoint = f"/movie/{movie_id}/credits"
    params = {"language": DEFAULT_LANGUAGE}
    logger.info(f"TMDb: 获取电影演职员 (ID: {movie_id})")
    return _tmdb_request(endpoint, api_key, params)

def search_person_tmdb(query: str, api_key: str, page: int = 1) -> Optional[Dict[str, Any]]:
    """
    通过名称搜索演员/人物。
    :param query: 搜索关键词 (演员名称)
    :param api_key: TMDb API Key
    :param page: 分页页码 (可选)
    :return: 搜索结果字典
    """
    endpoint = "/search/person"
    params = {
        "query": query,
        "page": page,
        "include_adult": "false",
        "language": DEFAULT_LANGUAGE
    }
    logger.info(f"TMDb: 搜索人物 '{query}'")
    return _tmdb_request(endpoint, api_key, params)

def get_person_details_tmdb(person_id: int, api_key: str, append_to_response: Optional[str] = "movie_credits,tv_credits,images,external_ids") -> Optional[Dict[str, Any]]:
    """
    获取指定人物（演员）的详细信息，包括参演的电影和电视剧。
    :param person_id: TMDb 人物 ID
    :param api_key: TMDb API Key
    :param append_to_response: 附加到响应的数据
    :return: 人物详情字典
    """
    endpoint = f"/person/{person_id}"
    params = {
        "language": DEFAULT_LANGUAGE,
        "append_to_response": append_to_response
    }
    logger.info(f"TMDb: 获取人物详情 (ID: {person_id})")
    return _tmdb_request(endpoint, api_key, params)

# --- 示例用法 (测试时可以取消注释) ---
if __name__ == '__main__':
    # 你需要将 'YOUR_TMDB_API_KEY' 替换为你的真实 TMDb API Key
    # 或者从配置文件或环境变量中读取
    TEST_API_KEY = os.environ.get("TMDB_API_KEY_TEST", None) # 尝试从环境变量获取
    if not TEST_API_KEY:
        print("错误：请设置 TMDB_API_KEY_TEST 环境变量或直接在代码中提供测试 API Key。")
    else:
        logger.info(f"使用 TMDb API Key: {TEST_API_KEY[:5]}... 进行测试")

        # 1. 测试搜索电影
        logger.info("\n--- 测试搜索电影 'Inception' ---")
        search_results = search_movie_tmdb("Inception", TEST_API_KEY, year=2010)
        if search_results and search_results.get("results"):
            inception_movie = search_results["results"][0]
            logger.info(f"找到电影: {inception_movie.get('title')} (ID: {inception_movie.get('id')})")
            movie_id_to_test = inception_movie.get('id')

            if movie_id_to_test:
                # 2. 测试获取电影详情
                logger.info(f"\n--- 测试获取电影详情 (ID: {movie_id_to_test}) ---")
                movie_details = get_movie_details_tmdb(movie_id_to_test, TEST_API_KEY)
                if movie_details:
                    logger.info(f"电影标题: {movie_details.get('title')}")
                    logger.info(f"电影概述: {movie_details.get('overview')[:100]}...")
                    if movie_details.get("credits") and movie_details["credits"].get("cast"):
                        logger.info("部分演员:")
                        for i, actor in enumerate(movie_details["credits"]["cast"]):
                            if i < 3: # 只打印前3个
                                logger.info(f"  - {actor.get('name')} 饰 {actor.get('character')} (Person ID: {actor.get('id')})")
                            else:
                                break
                        # 假设我们想获取第一个演员的详情
                        if movie_details["credits"]["cast"]:
                            first_actor_id = movie_details["credits"]["cast"][0].get("id")
                            first_actor_name = movie_details["credits"]["cast"][0].get("name")
                            if first_actor_id:
                                # 3. 测试获取人物详情
                                logger.info(f"\n--- 测试获取人物 '{first_actor_name}' 详情 (ID: {first_actor_id}) ---")
                                person_details = get_person_details_tmdb(first_actor_id, TEST_API_KEY)
                                if person_details:
                                    logger.info(f"人物名称: {person_details.get('name')}")
                                    logger.info(f"出生日期: {person_details.get('birthday')}")
                                    logger.info(f"简介 (部分): {person_details.get('biography')[:100] if person_details.get('biography') else 'N/A'}...")
        else:
            logger.warning("搜索电影 'Inception' 未返回结果或结果格式不正确。")

        # 4. 测试搜索人物
        logger.info("\n--- 测试搜索人物 'Leonardo DiCaprio' ---")
        person_search_results = search_person_tmdb("Leonardo DiCaprio", TEST_API_KEY)
        if person_search_results and person_search_results.get("results"):
            leo = person_search_results["results"][0]
            logger.info(f"找到人物: {leo.get('name')} (ID: {leo.get('id')}), Known for: {leo.get('known_for_department')}")
        else:
            logger.warning("搜索人物 'Leonardo DiCaprio' 未返回结果。")

        logger.info("\n--- TMDb Handler 测试结束 ---")