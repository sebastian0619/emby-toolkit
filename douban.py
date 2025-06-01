import base64
import hashlib
import hmac
import re 
import os
import json
from datetime import datetime
from enum import Enum
from random import choice
from typing import Optional, Dict 
from urllib import parse
import json # 确保导入json，因为测试代码中用到了
import constants

import requests
# --- 之前定义的 PERSISTENT_DATA_PATH ---
PERSISTENT_DATA_PATH = os.environ.get("APP_DATA_DIR", "/config") # 与 web_app.py 和 core_processor.py 保持一致
CACHE_SUBDIR = os.path.join(PERSISTENT_DATA_PATH, "cache") # 建议将缓存放在子目录
os.makedirs(CACHE_SUBDIR, exist_ok=True)
_translation_cache_file_path = os.path.join(CACHE_SUBDIR, constants.TRANSLATION_CACHE_FILE)
# --- PERSISTENT_DATA_PATH 定义结束 ---


try:
    from logger_setup import logger # 尝试从主项目导入logger
    import constants  # <--- 新增导入 constants
except ImportError:
    # 如果导入失败（例如独立运行douban.py），使用内置的SimpleLogger
    class SimpleLogger:
        def info(self, msg): print(f"[DOUBAN_INFO] {msg}")
        def error(self, msg): print(f"[DOUBAN_ERROR] {msg}")
        def warning(self, msg): print(f"[DOUBAN_WARN] {msg}")
        def debug(self, msg): print(f"[DOUBAN_DEBUG] {msg}")
        def success(self, msg): print(f"[DOUBAN_SUCCESS] {msg}")
    logger = SimpleLogger()
    logger.info("DoubanApi using internal SimpleLogger.")
    class TempConstants: # <--- 如果 douban.py 可能独立运行，需要一个临时的
        TRANSLATION_CACHE_FILE = "translation_cache_test.json" # 独立测试用名
    constants = TempConstants()
    logger.warning("DoubanApi using temporary constants for TRANSLATION_CACHE_FILE.")
try:
    from utils import get_base_path_for_files
except ImportError:
    # 如果 utils.py 或 get_base_path_for_files 不可用，提供一个回退
    def get_base_path_for_files():
        logger.warning("get_base_path_for_files not found, using current working directory for cache.")
        return os.getcwd()
    logger.warning("DoubanApi using fallback get_base_path_for_files.")


class MediaType(Enum):
    MOVIE = '电影'
    TV = '电视剧'
    COLLECTION = '系列'
    UNKNOWN = '未知'

def clean_character_name_static(character_name: Optional[str]) -> str:
    """静态辅助函数：移除角色名前的 '饰 ' 或 '饰' 及前后空格"""
    if not character_name:
        return ""
    name = str(character_name) 
    if name.startswith("饰 "):
        name = name[2:].strip()
    elif name.startswith("饰"):
        name = name[1:].strip()
    else:
        name = name.strip()
    return name

class DoubanApi:
    _translation_cache = {}
    _translation_cache_file_path = os.path.join(get_base_path_for_files(), constants.TRANSLATION_CACHE_FILE) # <--- 新增：缓存文件完整路径
    _cache_loaded = False # <--- 新增：标记缓存是否已从文件加载
    _urls = {
        "search": "/search/weixin", # 用于名称搜索
        # "search_agg": "/search", # 备用搜索，结构可能不同
        # "search_subject": "/search/subjects", # 备用搜索
        "imdbid": "/movie/imdb/%s", # 用于通过IMDB ID获取豆瓣条目信息 (POST)
        "movie_detail": "/movie/",  # 用于通过豆瓣数字ID获取电影详情 (GET)
        "tv_detail": "/tv/",      # 用于通过豆瓣数字ID获取电视剧详情 (GET)
        "movie_celebrities": "/movie/%s/celebrities", # 获取电影演职员 (GET)
        "tv_celebrities": "/tv/%s/celebrities",     # 获取电视剧演职员 (GET)
    }

    _user_agents = [
        "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad",
        "api-client/1 com.douban.frodo/7.18.0(230) Android/22 product/MI 9 vendor/Xiaomi model/MI 9 brand/Android  rom/miui6  network/wifi  platform/mobile nd/1",
    ]
    _api_secret_key = "bf7dddc7c9cfe6f7"
    _api_key = "0dad551ec0f84ed02907ff5c42e8ec70" # 用于 Frodo API (GET)
    _api_key2 = "0ab215a8b1977939201640fa14c66bab" # 用于 api.douban.com (POST)
    _base_url = "https://frodo.douban.com/api/v2" # Frodo API 基础URL
    _api_url = "https://api.douban.com/v2"      # api.douban.com 基础URL
    _session = None
    _default_timeout = 10

    @classmethod
    def _load_translation_cache_from_file(cls):
        """从文件加载翻译缓存到 _translation_cache"""
        if cls._cache_loaded: # 如果已经加载过了，就不用再加载了
            return
        try:
            if os.path.exists(cls._translation_cache_file_path):
                with open(cls._translation_cache_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict): # 确保加载的是字典
                        cls._translation_cache = data
                        logger.info(f"成功从 '{cls._translation_cache_file_path}' 加载 {len(cls._translation_cache)} 条翻译缓存。")
                    else:
                        logger.warning(f"翻译缓存文件 '{cls._translation_cache_file_path}' 内容不是有效的JSON对象（字典），初始化为空缓存。")
                        cls._translation_cache = {}
            else:
                cls._translation_cache = {} # 文件不存在，初始化为空字典
                logger.info(f"翻译缓存文件 '{cls._translation_cache_file_path}' 未找到，初始化为空缓存。")
        except json.JSONDecodeError:
            logger.error(f"解析翻译缓存文件 '{cls._translation_cache_file_path}' 失败 (JSON格式错误)，初始化为空缓存。")
            cls._translation_cache = {}
        except Exception as e:
            logger.error(f"加载翻译缓存文件 '{cls._translation_cache_file_path}' 时发生未知错误: {e}，初始化为空缓存。")
            cls._translation_cache = {}
        finally:
            cls._cache_loaded = True # 标记已尝试加载（无论成功与否）

    @classmethod
    def _save_translation_cache_to_file(cls):
        """将 _translation_cache 的内容保存到文件"""
        if not cls._translation_cache: # 如果缓存是空的，可能不需要保存空文件
            logger.info("翻译缓存为空，跳过保存到文件。")
            # 如果希望即使为空也创建文件，可以去掉这个if判断
            # 或者检查文件是否存在，如果存在且缓存为空，可以考虑删除它或写入空JSON对象 {}
            # if os.path.exists(cls._translation_cache_file_path):
            #    try: os.remove(cls._translation_cache_file_path) except: pass
            return

        try:
            # 创建缓存文件所在的目录（如果不存在）
            cache_dir = os.path.dirname(cls._translation_cache_file_path)
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
                logger.info(f"创建了翻译缓存目录: {cache_dir}")

            with open(cls._translation_cache_file_path, 'w', encoding='utf-8') as f:
                json.dump(cls._translation_cache, f, ensure_ascii=False, indent=4)
            logger.info(f"翻译缓存已成功保存到 '{cls._translation_cache_file_path}' (共 {len(cls._translation_cache)} 条)。")
        except Exception as e:
            logger.error(f"保存翻译缓存文件 '{cls._translation_cache_file_path}' 失败: {e}")

    def __init__(self):
        if DoubanApi._session is None: 
            DoubanApi._session = requests.Session()
        # --- 新增：在实例化时加载翻译缓存 ---
        if not DoubanApi._cache_loaded: # 确保只加载一次
            DoubanApi._load_translation_cache_from_file()
        # --- 加载缓存结束 ---

    @classmethod
    def __sign(cls, url: str, ts: str, method='GET') -> str:
        url_path = parse.urlparse(url).path
        raw_sign = '&'.join(
            [method.upper(), parse.quote(url_path, safe=''), ts])
        return base64.b64encode(
            hmac.new(cls._api_secret_key.encode(),
                     raw_sign.encode(), hashlib.sha1).digest()
        ).decode()

    def __invoke(self, url: str, **kwargs) -> dict: # 用于 GET 请求 (Frodo API)
        req_url = self._base_url + url
        params: dict = {'apiKey': self._api_key}
        if kwargs:
            params.update(kwargs)
        ts = params.pop('_ts', datetime.strftime(datetime.now(), '%Y%m%d'))
        params.update({'os_rom': 'android', 'apiKey': self._api_key,
                      '_ts': ts, '_sig': self.__sign(url=req_url, ts=ts)})
        headers = {'User-Agent': choice(self._user_agents)}
        resp = None
        try:
            logger.debug(f"GET Request URL: {req_url}, Params: {params}")
            resp = self._session.get(
                req_url, params=params, headers=headers, timeout=self._default_timeout)
            logger.debug(f"GET Response Status: {resp.status_code}")
            resp.raise_for_status()
            # 豆瓣API有时会在成功响应中也包含code，例如速率限制时status_code可能是200但body里有code
            response_json = resp.json()
            if response_json.get("code") == 1080: # 明确处理速率限制
                 logger.warning(f"GET请求触发豆瓣API速率限制: {response_json.get('msg')}")
                 return {"error": "rate_limit", "message": response_json.get("msg", "Rate limit triggered."), "code": 1080}
            return response_json
        except requests.exceptions.HTTPError as e:
            logger.error(
                f"HTTP error: {e} - URL: {resp.request.url if resp and resp.request else req_url} - Response: {e.response.text[:200] if e.response else 'N/A'}")
            try:
                error_json = e.response.json() if e.response else {}
                if error_json.get("code") == 1080:
                     return {"error": "rate_limit", "message": error_json.get("msg", "Rate limit triggered."), "code": 1080}
                return error_json if error_json else {"error": str(e), "message": "HTTP error, no response body"}
            except requests.exceptions.JSONDecodeError: # requests.JSONDecodeError for requests > 2.27
                return {"error": str(e), "message": "HTTP error and non-JSON response"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e} - URL: {req_url}")
            return {"error": str(e), "message": "Request exception"}
        except json.JSONDecodeError: # Changed from requests.exceptions.JSONDecodeError for general json issues
            logger.error(
                f"Failed to decode JSON. URL: {req_url}, Status: {resp.status_code if resp else 'N/A'}, Text: {resp.text[:200] if resp else 'N/A'}")
            return {"error": "JSONDecodeError", "message": "Invalid JSON response"}


    def __post(self, url: str, **kwargs) -> dict: # 用于 POST 请求 (api.douban.com)
        req_url = self._api_url + url
        data_payload: dict = {'apikey': self._api_key2}
        if kwargs:
            data_payload.update(kwargs)
        if '_ts' in data_payload: # _ts 和 _sig 通常用于Frodo API的GET请求
            data_payload.pop('_ts')
        headers = {'User-Agent': choice(self._user_agents),
                   "Content-Type": "application/x-www-form-urlencoded; charset=utf-8", "Cookie": "bid=J9zb1zA5sJc"} # Cookie可能需要更新或动态获取
        resp = None
        try:
            logger.debug(f"POST Request URL: {req_url}, Data: {data_payload}")
            resp = self._session.post(
                req_url, data=data_payload, headers=headers, timeout=self._default_timeout)
            logger.debug(f"POST Response Status: {resp.status_code}")
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080: # 明确处理速率限制
                 logger.warning(f"POST请求触发豆瓣API速率限制: {response_json.get('msg')}")
                 return {"error": "rate_limit", "message": response_json.get("msg", "Rate limit triggered."), "code": 1080}
            return response_json
        except requests.exceptions.HTTPError as e:
            logger.error(
                f"POST HTTP error: {e} - URL: {resp.request.url if resp and resp.request else req_url} - Response: {e.response.text[:200] if e.response else 'N/A'}")
            try:
                error_json = e.response.json() if e.response else {}
                if error_json.get("code") == 1080:
                     return {"error": "rate_limit", "message": error_json.get("msg", "Rate limit triggered."), "code": 1080}
                return error_json if error_json else {"error": str(e), "message": "POST HTTP error, no response body"}
            except requests.exceptions.JSONDecodeError:
                return {"error": str(e), "message": "POST HTTP error and non-JSON response"}
        except requests.exceptions.RequestException as e:
            logger.error(f"POST Request failed: {e} - URL: {req_url}")
            return {"error": str(e), "message": "POST Request exception"}
        except json.JSONDecodeError:
            logger.error(
                f"Failed to decode JSON from POST. URL: {req_url}, Status: {resp.status_code if resp else 'N/A'}, Text: {resp.text[:200] if resp else 'N/A'}")
            return {"error": "JSONDecodeError", "message": "Invalid JSON response from POST"}

    def imdbid(self, imdbid: str, ts=None) -> Optional[Dict]:
        params = {}
        if ts:
            params['_ts'] = ts
        return self.__post(self._urls["imdbid"] % imdbid, **params)

    def search(self, keyword: str, start: Optional[int] = 0, count: Optional[int] = 20,
               ts=datetime.strftime(datetime.now(), '%Y%m%d')) -> dict:
        return self.__invoke(self._urls["search"], q=keyword, start=start, count=count, _ts=ts)

    def _get_subject_details(self, subject_id: str, subject_type: str = "movie") -> Optional[Dict]:
        if not subject_id or not str(subject_id).isdigit():
            logger.warning(f"无效的豆瓣 subject_id: {subject_id}")
            return None

        url_key = f"{subject_type}_detail"
        if url_key not in self._urls:
            logger.error(f"未知的 subject_type for detail: {subject_type}")
            return None

        detail_url = self._urls[url_key] + subject_id
        logger.info(f"通过豆瓣ID获取详情: {detail_url}")
        details = self.__invoke(detail_url)

        if details and not details.get("code"): # 假设没有错误代码表示成功 (除了1080这种已明确处理的)
            return details
        elif details and details.get("code") == 1080: # 如果详情接口也返回速率限制
            logger.warning(f"获取豆瓣ID {subject_id} ({subject_type}) 详情时触发速率限制: {details.get('msg')}")
            return details # 将包含错误信息的details返回
        else:
            logger.warning(
                f"获取豆瓣ID {subject_id} ({subject_type}) 详情失败: {details.get('msg') if details else '无结果'}")
            return None

    def match_info(self, name: str, imdbid: str = None,
                   mtype: Optional[str] = None, year: Optional[str] = None, season: Optional[int] = None,
                   raise_exception: bool = False) -> Dict:
        if imdbid and imdbid.strip().startswith("tt"):
            actual_imdbid = imdbid.strip()
            logger.info(f"开始使用IMDBID {actual_imdbid} 查询豆瓣信息 ...")
            result_from_imdb_lookup = self.imdbid(actual_imdbid)
            
            if result_from_imdb_lookup and result_from_imdb_lookup.get("code") == 1080: # IMDBID查询触发速率限制
                logger.warning(f"IMDBID {actual_imdbid} 查询触发速率限制。")
                if raise_exception: raise Exception(result_from_imdb_lookup.get("msg", "Rate limit from IMDB lookup"))
                return result_from_imdb_lookup # 直接返回错误信息

            if result_from_imdb_lookup and result_from_imdb_lookup.get("id"):
                douban_id_url = str(result_from_imdb_lookup.get("id"))
                # 尝试匹配 .../movie/数字ID/ 或 .../tv/数字ID/
                match_re = re.search(r'/(movie|tv)/(\d+)/?$', douban_id_url)
                
                if match_re:
                    api_type = match_re.group(1)
                    actual_douban_id = match_re.group(2)
                    logger.info(
                        f"通过 IMDB ID '{actual_imdbid}' 解析得到豆瓣数字 ID: {actual_douban_id}, 类型: {api_type}")

                    title = result_from_imdb_lookup.get(
                        "title", result_from_imdb_lookup.get("alt_title", name))
                    original_title = result_from_imdb_lookup.get("original_title")
                    year_from_api = str(result_from_imdb_lookup.get("year", "")).strip() # Frodo API直接有year
                    if not year_from_api and result_from_imdb_lookup.get("attrs", {}).get("year"): # 兼容旧的attrs结构
                        year_list = result_from_imdb_lookup.get("attrs").get("year")
                        if isinstance(year_list, list) and year_list:
                            year_from_api = str(year_list[0])
                    
                    final_mtype = mtype if mtype else api_type

                    return {
                        "id": actual_douban_id, "title": title, "original_title": original_title,
                        "year": year_from_api or year, "type": final_mtype,
                        "source": "imdb_lookup"
                    }
                else:
                    logger.warning(
                        f"IMDBID {actual_imdbid} 查询到的豆瓣ID URL '{douban_id_url}' 无法解析出数字ID和类型，尝试名称搜索。")
            else: # result_from_imdb_lookup 为空或没有id字段 (且不是速率限制)
                if imdbid and imdbid.strip().startswith("tt"):
                    logger.warning(
                        f"IMDBID {imdbid.strip()} 查询无结果或结果无效 (msg: {result_from_imdb_lookup.get('msg') if result_from_imdb_lookup else 'N/A'})，尝试名称搜索。")
        
        # 如果没有IMDB ID，或者IMDB ID查询失败，则执行名称搜索
        return self._search_by_name_for_match_info(name, mtype, year, season, raise_exception)

    def _search_by_name_for_match_info(self, name: str, mtype: Optional[str], 
                                       year: Optional[str] = None, season: Optional[int] = None, 
                                       raise_exception: bool = False) -> Dict:
        logger.info(f"开始使用名称 '{name}'{(', 年份: '+year) if year else ''}{(', 类型: '+mtype) if mtype else ''} 匹配豆瓣信息 ...")
        search_query = f"{name} {year or ''}".strip()
        result = self.search(search_query) 
        
        if not result: 
            logger.warning(f"名称搜索 '{search_query}' 无返回结果")
            return {"error": "no_search_result", "message": "豆瓣名称搜索无返回。"}
        if result.get("code") == 1080: 
            msg = f"触发豆瓣API速率限制: {result.get('msg', '无详细信息')}"
            logger.warning(msg)
            if raise_exception: raise Exception(msg)
            return {"error": "rate_limit", "message": msg}
        
        items = result.get("items")
        if not items: 
            logger.warning(f"名称搜索 '{search_query}' 未找到条目 (items为空)")
            return {"error": "no_items_found", "message": f"豆瓣名称搜索 '{search_query}' 未找到条目。"}

        candidates = []
        exact_match = None
        logger.debug(f"  [开始遍历搜索结果] 共 {len(items)} 个原始条目。")

        for i, item_obj in enumerate(items):
            logger.debug(f"  [处理原始条目 {i+1}/{len(items)}]")
            item_layout = item_obj.get("layout")
            api_item_type_outer = item_obj.get("target_type")
            
            logger.debug(f"    [原始条目信息] Layout: {item_layout}, TargetType: {api_item_type_outer}, Title: {item_obj.get('target', {}).get('title')}")

            if item_layout != "subject" or api_item_type_outer not in ["movie", "tv"]:
                logger.debug(f"      [跳过] 非影视条目或类型不符。")
                continue

            target = item_obj.get("target", {})
            if not target:
                logger.debug(f"      [跳过] Target为空。")
                continue

            if mtype and mtype != api_item_type_outer:
                logger.debug(f"      [跳过] 类型不匹配: API返回类型='{api_item_type_outer}', 期望类型='{mtype}', API标题='{target.get('title')}'")
                continue
            logger.debug(f"      [类型通过] API类型='{api_item_type_outer}', 期望类型='{mtype or '任意影视'}'")

            api_item_year_str = str(target.get("year", "")).strip()
            title = target.get("title")
            douban_id_from_search = str(target.get("id", "")).strip()

            if not title or not douban_id_from_search.isdigit():
                logger.debug(f"      [跳过] 缺少标题或ID非数字: Title='{title}', ID='{douban_id_from_search}'")
                continue
            logger.debug(f"      [提取信息] Title='{title}', Year='{api_item_year_str}', ID='{douban_id_from_search}'")
            
            year_match = False
            if year and api_item_year_str: 
                try:
                    if abs(int(api_item_year_str) - int(year)) <= 1: 
                        year_match = True
                    else:
                        logger.debug(f"        [年份不符] API年份='{api_item_year_str}', 搜索年份='{year}' (差异过大)")
                except ValueError:
                    logger.debug(f"        [年份比较错误] 无法将年份转换为整数: API='{api_item_year_str}', 搜索='{year}'")
            elif not year: 
                year_match = True
            
            logger.debug(f"      年份匹配结果 (year_match): {year_match} for Title='{title}', APIYear='{api_item_year_str}'")

            if year_match:
                logger.debug(f"      [进入年份匹配块] 比较候选: API标题='{title}' (len={len(title)}), 搜索名='{name}' (len={len(name)})")
                logger.debug(f"      API标题lower+strip='{title.lower().strip()}', 搜索名lower+strip='{name.lower().strip()}'")
                
                is_title_match = (title.lower().strip() == name.lower().strip())
                is_year_exact_match_for_this_check = (api_item_year_str == year) if year else True 
                
                logger.debug(f"      标题精确匹配: {is_title_match}, 年份精确匹配 (若提供): {is_year_exact_match_for_this_check}")

                candidate_info = {
                    "id": douban_id_from_search, 
                    "title": title, 
                    "original_title": target.get("original_title"),
                    "year": api_item_year_str, 
                    "type": api_item_type_outer, 
                    "source": "name_search_candidate"
                }
                
                if is_title_match and is_year_exact_match_for_this_check:
                    exact_match = candidate_info
                    exact_match["source"] = "name_search_exact" 
                    logger.info(f"      名称搜索找到精确匹配 (标题和年份完全一致): ID={exact_match['id']}, Title='{exact_match['title']}'")
                    break 

                candidates.append(candidate_info)
                logger.debug(f"      已添加 '{title}' 到候选列表。当前候选数量: {len(candidates)}")
            else:
                logger.debug(f"      [跳过添加候选] 年份不匹配 for Title='{title}'")
        
        logger.debug(f"  [结束遍历搜索结果] ExactMatch找到: {'是' if exact_match else '否'}, 候选数量: {len(candidates)}")

        if exact_match:
            return exact_match 

        if candidates:
            if len(candidates) == 1: 
                logger.info(f"名称搜索找到唯一候选匹配 (非精确但唯一): ID={candidates[0]['id']}, Title='{candidates[0]['title']}'")
                return candidates[0] 
            else: 
                logger.info(f"名称搜索找到 {len(candidates)} 个候选匹配项 for '{name}' ({mtype}, {year})。")
                return {"search_candidates": candidates, "message": "找到多个可能的匹配项，请选择。"}
        
        logger.warning(f"未能在名称搜索结果中找到 '{name}' ({mtype}, {year}) 的任何匹配。")
        return {"error": "no_suitable_match", "message": f"豆瓣名称搜索未能为 '{name}' 找到合适的匹配项。"}

    def get_acting(self, name: str, imdbid: str = None, mtype: str = None, year: str = None, season: int = None, 
                   douban_id_override: Optional[str] = None) -> Optional[dict]:
        douban_subject_id = None
        final_mtype = mtype 

        if douban_id_override and str(douban_id_override).isdigit():
            douban_subject_id = str(douban_id_override)
            logger.info(f"使用提供的豆瓣ID覆盖: {douban_subject_id}")
            if not final_mtype and douban_subject_id:
                details_for_type = self._get_subject_details(douban_subject_id, "movie") 
                if details_for_type and details_for_type.get("type"):
                    final_mtype = details_for_type.get("type")
                elif not final_mtype: # 如果猜电影失败，再猜电视剧
                    details_for_type_tv = self._get_subject_details(douban_subject_id, "tv")
                    if details_for_type_tv and details_for_type_tv.get("type"):
                        final_mtype = details_for_type_tv.get("type")

                if final_mtype:
                    logger.info(f"通过详情推断豆瓣ID {douban_subject_id} 的类型为: {final_mtype}")
                else: # 如果两种都获取不到类型，且外部也没传入mtype，则是个问题
                    logger.warning(f"无法通过详情推断豆瓣ID {douban_subject_id} 的类型，且外部未提供mtype。")
                    # 此时 final_mtype 仍然是 None，后续会报错或返回空
        else: 
            match_info_result = self.match_info(name=name, imdbid=imdbid, mtype=mtype, year=year, season=season)

            if match_info_result and match_info_result.get("id") and str(match_info_result.get("id")).isdigit():
                douban_subject_id = str(match_info_result.get("id"))
                if not final_mtype and match_info_result.get("type"): 
                    final_mtype = match_info_result.get("type")
                    logger.info(f"从匹配结果中推断媒体类型为: {final_mtype}")
            elif match_info_result and match_info_result.get("error") == "rate_limit":
                 logger.error(f"豆瓣API在信息匹配时触发速率限制。")
                 return {"error": "rate_limit", "message": match_info_result.get("message"), "cast": []}
            elif match_info_result and match_info_result.get("search_candidates"):
                logger.info("match_info 返回多个候选结果，get_acting 将其透传。")
                return match_info_result 
        
        if douban_subject_id and final_mtype:
            logger.info(f"获取豆瓣ID '{douban_subject_id}' (类型: {final_mtype}) 的演职员信息...")
            response = None
            if final_mtype == "tv":
                response = self.tv_celebrities(douban_subject_id)
            elif final_mtype == "movie":
                response = self.movie_celebrities(douban_subject_id)
            else:
                logger.error(f"未知的媒体类型 '{final_mtype}' (豆瓣ID: {douban_subject_id}) 无法获取演职员。")
                return {"error": f"未知的媒体类型 '{final_mtype}'", "cast": []}

            if not response:
                logger.error(f"豆瓣API未能返回ID '{douban_subject_id}' (类型: {final_mtype}) 的演职员信息。")
                return {"error": f"未能获取ID '{douban_subject_id}' 的演职员信息", "cast": []}
            if response.get("code") == 1080: 
                 logger.error(f"获取演职员信息时触发豆瓣API速率限制 (ID: {douban_subject_id})。")
                 return {"error": "rate_limit", "message": response.get("msg"), "cast": []}

            data = {"cast": []}
            actors_list = response.get("celebrities", response.get("actors", [])) 
            if actors_list is None: actors_list = [] # 确保是可迭代的

            for idx, item in enumerate(actors_list): # 使用 idx 作为 order 的备选
                if not isinstance(item, dict): continue

                character_str_raw = ""
                if item.get("character"): 
                    character_str_raw = item.get("character")
                elif item.get("attrs", {}).get("role"): 
                    roles = item.get("attrs").get("role")
                    if isinstance(roles, list) and roles: # 确保 roles 非空
                        character_str_raw = " / ".join(r for r in roles if isinstance(r, str))
                
                cleaned_char_name = clean_character_name_static(character_str_raw)
                
                actor_id_str = str(item.get("id", "")).strip()
                actor_id_int = None
                if actor_id_str.isdigit():
                    actor_id_int = int(actor_id_str)

                profile_img_obj = item.get("avatar", item.get("cover_url")) 
                profile_path_val = None
                if isinstance(profile_img_obj, dict):
                    profile_path_val = profile_img_obj.get("large", profile_img_obj.get("normal"))
                elif isinstance(profile_img_obj, str):
                    profile_path_val = profile_img_obj

                cast_list_item = {
                    "name": item.get("name"),
                    "character": cleaned_char_name, 
                    "id": actor_id_int,
                    "original_name": item.get("latin_name", item.get("name_en")), 
                    "profile_path": profile_path_val, 
                    "adult": False, 
                    "gender": 0, 
                    "known_for_department": "Acting",
                    "popularity": 0.0,
                    "cast_id": None, 
                    "credit_id": None,
                    "order": item.get("rank", idx) # 使用 idx 作为 order 的备选
                }
                data["cast"].append(cast_list_item)
            return data
        else: 
            logger.error(f"未能为 '{name}' (IMDB: {imdbid}, DoubanIDOverride: {douban_id_override}) 找到有效的豆瓣ID或媒体类型进行查询。")
            return {"error": f"未能为 '{name}' 找到有效的豆瓣ID或媒体类型", "cast": []}

    def movie_celebrities(self, subject_id: str):
        return self.__invoke(self._urls["movie_celebrities"] % subject_id)

    def tv_celebrities(self, subject_id: str):
        return self.__invoke(self._urls["tv_celebrities"] % subject_id)
        
    def close(self):
        if DoubanApi._session:
            DoubanApi._session.close()
            DoubanApi._session = None
            logger.info("DoubanApi session closed.")
        # --- 新增：在关闭时保存翻译缓存 ---
        DoubanApi._save_translation_cache_to_file()
        # --- 保存缓存结束 ---

# --- Main test block ---
if __name__ == '__main__':
    import json
    import time

    douban = DoubanApi() 
    try:
        # --- 用例1: 精确IMDB ID ---
        print(f"\n--- 测试电影 (精确IMDB ID): 神秘海域 (tt1464335) ---")
        result1 = douban.match_info(name="神秘海域", imdbid="tt1464335", mtype="movie", year="2022")
        print("match_info 返回:")
        print(json.dumps(result1, indent=4, ensure_ascii=False))
        if result1 and result1.get("id") and not result1.get("search_candidates") and not result1.get("error"):
            print(f"获取到豆瓣ID: {result1.get('id')}, 准备获取演员表...")
            time.sleep(1) 
            cast1 = douban.get_acting(name=result1.get('title'), imdbid="tt1464335", mtype=result1.get('type'), year=result1.get('year'))
            print("get_acting 返回:")
            print(json.dumps(cast1, indent=4, ensure_ascii=False))
        
        print("-" * 50); time.sleep(3)

        # --- 用例2a: 名称搜索 - 流浪地球 ---
        print(f"\n--- 测试名称搜索: 流浪地球 (2019) ---")
        result2a = douban.match_info(name="流浪地球", mtype="movie", year="2019")
        print("match_info 返回:")
        print(json.dumps(result2a, indent=4, ensure_ascii=False))
        if result2a and result2a.get("id") and not result2a.get("search_candidates") and not result2a.get("error"):
            print(f"获取到豆瓣ID: {result2a.get('id')}, 准备获取演员表...")
            time.sleep(1)
            cast2a = douban.get_acting(name="流浪地球", mtype="movie", year="2019")
            print("get_acting 返回:")
            print(json.dumps(cast2a, indent=4, ensure_ascii=False))

        print("-" * 50); time.sleep(3)

        # --- 用例2b: 名称搜索 - 英雄 ---
        print(f"\n--- 测试名称搜索: 英雄 (2002) ---")
        result2b = douban.match_info(name="英雄", mtype="movie", year="2002")
        print("match_info 返回:")
        print(json.dumps(result2b, indent=4, ensure_ascii=False))
        if result2b and result2b.get("id") and not result2b.get("search_candidates") and not result2b.get("error"):
            print(f"获取到豆瓣ID: {result2b.get('id')}, 准备获取演员表...")
            time.sleep(1)
            cast2b = douban.get_acting(name="英雄", mtype="movie", year="2002")
            print("get_acting 返回:")
            print(json.dumps(cast2b, indent=4, ensure_ascii=False))
        elif result2b and result2b.get("search_candidates"):
             print(f"找到多个候选，选择第一个进行测试 (ID: {result2b['search_candidates'][0]['id']})")
             time.sleep(1)
             cast2b_selected = douban.get_acting(douban_id_override=result2b['search_candidates'][0]['id'], mtype="movie") # 假设类型是movie
             print("get_acting (使用候选ID) 返回:")
             print(json.dumps(cast2b_selected, indent=4, ensure_ascii=False))


        print("-" * 50); time.sleep(3)
        
        # --- 用例2c: 名称搜索 - 变形金刚 ---
        print(f"\n--- 测试名称搜索: 变形金刚 (2007) ---")
        result2c = douban.match_info(name="变形金刚", mtype="movie", year="2007")
        print("match_info 返回:")
        print(json.dumps(result2c, indent=4, ensure_ascii=False))
        if result2c and result2c.get("id") and not result2c.get("search_candidates") and not result2c.get("error"):
            print(f"获取到豆瓣ID: {result2c.get('id')}, 准备获取演员表...")
            time.sleep(1)
            cast2c = douban.get_acting(name="变形金刚", mtype="movie", year="2007")
            print("get_acting 返回:")
            print(json.dumps(cast2c, indent=4, ensure_ascii=False))

        print("-" * 50); time.sleep(3)

        # --- 用例3: 不存在的电影 ---
        print(f"\n--- 测试名称搜索 (不存在的电影): 一个不存在的电影名字123XYZ (2023) ---")
        result3 = douban.match_info(name="一个不存在的电影名字123XYZ", mtype="movie", year="2023")
        print("match_info 返回:")
        print(json.dumps(result3, indent=4, ensure_ascii=False))

    except Exception as e:
        import traceback
        print(f"测试过程中发生错误: {e}")
        print(traceback.format_exc())
    finally:
        douban.close()