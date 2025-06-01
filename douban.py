# douban.py
import base64
import hashlib
import hmac
import re 
import os
import json
from datetime import datetime
from enum import Enum # 确保导入 Enum
from random import choice # 确保导入 choice
from typing import Optional, Dict, List, Any # 确保导入
from urllib import parse # 确保导入 parse

import requests

import constants # 导入constants模块

# --- 统一的持久化数据路径 ---
PERSISTENT_DATA_PATH = os.environ.get("APP_DATA_DIR", "/config") 
CACHE_SUBDIR = os.path.join(PERSISTENT_DATA_PATH, "cache") 
try:
    os.makedirs(CACHE_SUBDIR, exist_ok=True)
except OSError as e:
    # 在某些受限环境中，应用可能没有权限在任意路径创建目录
    # Docker volume映射通常会处理好顶层 /config 目录的创建
    # 如果 CACHE_SUBDIR 创建失败，可以考虑直接使用 PERSISTENT_DATA_PATH
    # 为了简单起见，如果创建失败，我们让后续的路径直接指向 PERSISTENT_DATA_PATH
    # 但更好的做法是在应用层面有更统一的路径管理和错误处理
    print(f"[DOUBAN_WARN] 创建缓存子目录 {CACHE_SUBDIR} 失败: {e}。将尝试使用 {PERSISTENT_DATA_PATH}。")
    CACHE_SUBDIR = PERSISTENT_DATA_PATH 
    try:
        os.makedirs(CACHE_SUBDIR, exist_ok=True) 
    except OSError as e2:
        # 如果连主数据目录都创建失败，那问题比较严重了
        print(f"[DOUBAN_ERROR] 创建主数据目录 {CACHE_SUBDIR} 也失败: {e2}。缓存可能无法保存。")
# --- 持久化数据路径定义结束 ---


# logger_setup 的导入逻辑
try:
    from logger_setup import logger 
except ImportError:
    class SimpleLogger: 
        def info(self, msg): print(f"[DOUBAN_INFO] {msg}")
        def error(self, msg): print(f"[DOUBAN_ERROR] {msg}")
        def warning(self, msg): print(f"[DOUBAN_WARN] {msg}")
        def debug(self, msg): print(f"[DOUBAN_DEBUG] {msg}")
        def success(self, msg): print(f"[DOUBAN_SUCCESS] {msg}")
    logger = SimpleLogger()
    logger.info("DoubanApi using internal SimpleLogger.")


class MediaType(Enum): # 确保 MediaType Enum 被定义
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
    # --- 类属性 ---
    _translation_cache: Dict[str, Optional[str]] = {} # 明确类型
    _translation_cache_file_path: str = os.path.join(CACHE_SUBDIR, constants.TRANSLATION_CACHE_FILE) 
    _cache_loaded: bool = False
    _session: Optional[requests.Session] = None # 类级别的Session

    _urls = {
        "search": "/search/weixin",
        "imdbid": "/movie/imdb/%s",
        "movie_detail": "/movie/",
        "tv_detail": "/tv/",
        "movie_celebrities": "/movie/%s/celebrities",
        "tv_celebrities": "/tv/%s/celebrities",
    }
    _user_agents = [
        "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad",
        "api-client/1 com.douban.frodo/7.18.0(230) Android/22 product/MI 9 vendor/Xiaomi model/MI 9 brand/Android  rom/miui6  network/wifi  platform/mobile nd/1",
    ]
    _api_secret_key = "bf7dddc7c9cfe6f7"
    _api_key = "0dad551ec0f84ed02907ff5c42e8ec70" 
    _api_key2 = "0ab215a8b1977939201640fa14c66bab" 
    _base_url = "https://frodo.douban.com/api/v2" 
    _api_url = "https://api.douban.com/v2"      
    _default_timeout = 10

    def __init__(self):
        # 确保session只被初始化一次 (作为类属性)
        if DoubanApi._session is None: 
            DoubanApi._session = requests.Session()
            logger.debug("DoubanApi requests.Session 已初始化。")
        
        # 确保翻译缓存只被加载一次
        if not DoubanApi._cache_loaded:
            DoubanApi._load_translation_cache_from_file()

    @classmethod
    def _load_translation_cache_from_file(cls):
        if cls._cache_loaded:
            return
        if not cls._translation_cache_file_path: # 防御性编程
            logger.error("翻译缓存文件路径未初始化，无法加载缓存。")
            cls._cache_loaded = True # 标记为已尝试加载
            return
            
        logger.debug(f"尝试从 '{cls._translation_cache_file_path}' 加载翻译缓存。")
        try:
            if os.path.exists(cls._translation_cache_file_path):
                with open(cls._translation_cache_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        cls._translation_cache = data
                        logger.info(f"成功从 '{cls._translation_cache_file_path}' 加载 {len(cls._translation_cache)} 条翻译缓存。")
                    else:
                        logger.warning(f"翻译缓存文件 '{cls._translation_cache_file_path}' 内容不是有效的JSON字典，初始化为空缓存。")
                        cls._translation_cache = {} # 重置为空字典
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
        if not cls._translation_cache_file_path: # 防御性编程
            logger.error("翻译缓存文件路径未初始化，无法保存缓存。")
            return
        
        # 确保目标目录存在 (通常在模块加载时已创建，这里再次检查以防万一)
        cache_dir_for_save = os.path.dirname(cls._translation_cache_file_path)
        try:
            if not os.path.exists(cache_dir_for_save):
                os.makedirs(cache_dir_for_save, exist_ok=True)
                logger.info(f"保存翻译缓存时，创建了尚不存在的目录: {cache_dir_for_save}")
        except OSError as e_mkdir_save:
            logger.error(f"保存翻译缓存时创建目录 '{cache_dir_for_save}' 失败: {e_mkdir_save}。可能无法保存。")
            return 

        if not cls._translation_cache: 
            logger.info("翻译缓存为空，跳过保存到文件。")
            # (可选) 如果文件存在且缓存为空，可以考虑删除或写入空JSON {}
            # if os.path.exists(cls._translation_cache_file_path):
            #     try: os.remove(cls._translation_cache_file_path) except: pass
            return

        try:
            with open(cls._translation_cache_file_path, 'w', encoding='utf-8') as f:
                json.dump(cls._translation_cache, f, ensure_ascii=False, indent=4)
            logger.info(f"翻译缓存已成功保存到 '{cls._translation_cache_file_path}' (共 {len(cls._translation_cache)} 条)。")
        except Exception as e:
            logger.error(f"保存翻译缓存文件 '{cls._translation_cache_file_path}' 失败: {e}")

    @classmethod # 签名方法是类方法，因为它使用了类属性 _api_secret_key
    def _sign(cls, url: str, ts: str, method='GET') -> str:
        url_path = parse.urlparse(url).path
        raw_sign = '&'.join(
            [method.upper(), parse.quote(url_path, safe=''), ts])
        return base64.b64encode(
            hmac.new(cls._api_secret_key.encode(),
                     raw_sign.encode(), hashlib.sha1).digest()
        ).decode()

    # __invoke 和 __post 是实例方法，因为它们使用了实例化的 _session (虽然目前_session是类属性，但通常网络请求session是实例相关的)
    # 但为了与现有代码兼容，并且如果_session确实是共享的，可以保持它们为非静态/非类方法，依赖于类属性_session
    def __invoke(self, url: str, **kwargs) -> dict:
        # ... (方法体与之前版本相同，确保使用 DoubanApi._base_url, DoubanApi._api_key, DoubanApi._session, DoubanApi._user_agents, DoubanApi._default_timeout)
        # ... (以及调用 DoubanApi._sign)
        req_url = DoubanApi._base_url + url
        params: dict = {'apiKey': DoubanApi._api_key}
        if kwargs:
            params.update(kwargs)
        ts = params.pop('_ts', datetime.strftime(datetime.now(), '%Y%m%d'))
        params.update({'os_rom': 'android', 'apiKey': DoubanApi._api_key,
                      '_ts': ts, '_sig': DoubanApi._sign(url=req_url, ts=ts)}) # 调用类方法_sign
        headers = {'User-Agent': choice(DoubanApi._user_agents)}
        resp = None
        try:
            logger.debug(f"GET Request URL: {req_url}, Params: {params}")
            resp = DoubanApi._session.get(
                req_url, params=params, headers=headers, timeout=DoubanApi._default_timeout)
            logger.debug(f"GET Response Status: {resp.status_code}")
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                 logger.warning(f"GET请求触发豆瓣API速率限制: {response_json.get('msg')}")
                 return {"error": "rate_limit", "message": response_json.get("msg", "Rate limit triggered."), "code": 1080}
            return response_json
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e} - URL: {resp.request.url if resp and resp.request else req_url} - Response: {e.response.text[:200] if e.response else 'N/A'}")
            try:
                error_json = e.response.json() if e.response else {}
                if error_json.get("code") == 1080:
                     return {"error": "rate_limit", "message": error_json.get("msg", "Rate limit triggered."), "code": 1080}
                return error_json if error_json else {"error": str(e), "message": "HTTP error, no response body"}
            except json.JSONDecodeError: # Changed from requests.JSONDecodeError
                return {"error": str(e), "message": "HTTP error and non-JSON response"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e} - URL: {req_url}")
            return {"error": str(e), "message": "Request exception"}
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON. URL: {req_url}, Status: {resp.status_code if resp else 'N/A'}, Text: {resp.text[:200] if resp else 'N/A'}")
            return {"error": "JSONDecodeError", "message": "Invalid JSON response"}


    def __post(self, url: str, **kwargs) -> dict:
        # ... (方法体与之前版本相同，确保使用 DoubanApi._api_url, DoubanApi._api_key2, DoubanApi._session, DoubanApi._user_agents, DoubanApi._default_timeout)
        req_url = DoubanApi._api_url + url
        data_payload: dict = {'apikey': DoubanApi._api_key2}
        if kwargs:
            data_payload.update(kwargs)
        if '_ts' in data_payload: data_payload.pop('_ts')
        headers = {'User-Agent': choice(DoubanApi._user_agents),
                   "Content-Type": "application/x-www-form-urlencoded; charset=utf-8", "Cookie": "bid=J9zb1zA5sJc"}
        resp = None
        try:
            logger.debug(f"POST Request URL: {req_url}, Data: {data_payload}")
            resp = DoubanApi._session.post(
                req_url, data=data_payload, headers=headers, timeout=DoubanApi._default_timeout)
            logger.debug(f"POST Response Status: {resp.status_code}")
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                 logger.warning(f"POST请求触发豆瓣API速率限制: {response_json.get('msg')}")
                 return {"error": "rate_limit", "message": response_json.get("msg", "Rate limit triggered."), "code": 1080}
            return response_json
        except requests.exceptions.HTTPError as e:
            logger.error(f"POST HTTP error: {e} - URL: {resp.request.url if resp and resp.request else req_url} - Response: {e.response.text[:200] if e.response else 'N/A'}")
            try:
                error_json = e.response.json() if e.response else {}
                if error_json.get("code") == 1080:
                     return {"error": "rate_limit", "message": error_json.get("msg", "Rate limit triggered."), "code": 1080}
                return error_json if error_json else {"error": str(e), "message": "POST HTTP error, no response body"}
            except json.JSONDecodeError: # Changed from requests.JSONDecodeError
                return {"error": str(e), "message": "POST HTTP error and non-JSON response"}
        except requests.exceptions.RequestException as e:
            logger.error(f"POST Request failed: {e} - URL: {req_url}")
            return {"error": str(e), "message": "POST Request exception"}
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON from POST. URL: {req_url}, Status: {resp.status_code if resp else 'N/A'}, Text: {resp.text[:200] if resp else 'N/A'}")
            return {"error": "JSONDecodeError", "message": "Invalid JSON response from POST"}

    # --- 后续的 imdbid, search, _get_subject_details, match_info, _search_by_name_for_match_info, get_acting, movie_celebrities, tv_celebrities ---
    # --- 这些方法都是实例方法，调用 self.__invoke 或 self.__post，或者其他实例方法，保持不变 ---
    def imdbid(self, imdbid: str, ts=None) -> Optional[Dict]:
        params = {}
        if ts: params['_ts'] = ts
        return self.__post(DoubanApi._urls["imdbid"] % imdbid, **params)

    def search(self, keyword: str, start: Optional[int] = 0, count: Optional[int] = 20,
               ts=datetime.strftime(datetime.now(), '%Y%m%d')) -> dict:
        return self.__invoke(DoubanApi._urls["search"], q=keyword, start=start, count=count, _ts=ts)

    def _get_subject_details(self, subject_id: str, subject_type: str = "movie") -> Optional[Dict]:
        if not subject_id or not str(subject_id).isdigit(): logger.warning(f"无效的豆瓣 subject_id: {subject_id}"); return None
        url_key = f"{subject_type}_detail"
        if url_key not in DoubanApi._urls: logger.error(f"未知的 subject_type for detail: {subject_type}"); return None
        detail_url = DoubanApi._urls[url_key] + subject_id
        logger.info(f"通过豆瓣ID获取详情: {detail_url}")
        details = self.__invoke(detail_url)
        if details and not details.get("code"): return details
        elif details and details.get("code") == 1080: logger.warning(f"获取豆瓣ID {subject_id} ({subject_type}) 详情时触发速率限制: {details.get('msg')}"); return details
        else: logger.warning(f"获取豆瓣ID {subject_id} ({subject_type}) 详情失败: {details.get('msg') if details else '无结果'}"); return None

    def match_info(self, name: str, imdbid: str = None, mtype: Optional[str] = None, year: Optional[str] = None, season: Optional[int] = None, raise_exception: bool = False) -> Dict:
        # ... (此方法内部逻辑不变，它调用 self.imdbid 和 self._search_by_name_for_match_info) ...
        if imdbid and imdbid.strip().startswith("tt"):
            actual_imdbid = imdbid.strip()
            logger.info(f"开始使用IMDBID {actual_imdbid} 查询豆瓣信息 ...")
            result_from_imdb_lookup = self.imdbid(actual_imdbid)
            if result_from_imdb_lookup and result_from_imdb_lookup.get("code") == 1080:
                logger.warning(f"IMDBID {actual_imdbid} 查询触发速率限制。")
                if raise_exception: raise Exception(result_from_imdb_lookup.get("msg", "Rate limit from IMDB lookup"))
                return result_from_imdb_lookup
            if result_from_imdb_lookup and result_from_imdb_lookup.get("id"):
                douban_id_url = str(result_from_imdb_lookup.get("id"))
                match_re = re.search(r'/(movie|tv)/(\d+)/?$', douban_id_url)
                if match_re:
                    api_type = match_re.group(1); actual_douban_id = match_re.group(2)
                    logger.info(f"通过 IMDB ID '{actual_imdbid}' 解析得到豆瓣数字 ID: {actual_douban_id}, 类型: {api_type}")
                    title = result_from_imdb_lookup.get("title", result_from_imdb_lookup.get("alt_title", name))
                    original_title = result_from_imdb_lookup.get("original_title")
                    year_from_api = str(result_from_imdb_lookup.get("year", "")).strip()
                    if not year_from_api and result_from_imdb_lookup.get("attrs", {}).get("year"):
                        year_list = result_from_imdb_lookup.get("attrs").get("year")
                        if isinstance(year_list, list) and year_list: year_from_api = str(year_list[0])
                    final_mtype = mtype if mtype else api_type
                    return {"id": actual_douban_id, "title": title, "original_title": original_title, "year": year_from_api or year, "type": final_mtype, "source": "imdb_lookup"}
                else: logger.warning(f"IMDBID {actual_imdbid} 查询到的豆瓣ID URL '{douban_id_url}' 无法解析，尝试名称搜索。")
            else:
                if imdbid and imdbid.strip().startswith("tt"): logger.warning(f"IMDBID {imdbid.strip()} 查询无结果或结果无效，尝试名称搜索。")
        return self._search_by_name_for_match_info(name, mtype, year, season, raise_exception)

    def _search_by_name_for_match_info(self, name: str, mtype: Optional[str], year: Optional[str] = None, season: Optional[int] = None, raise_exception: bool = False) -> Dict:
        # ... (此方法内部逻辑不变，它调用 self.search) ...
        logger.info(f"开始使用名称 '{name}'{(', 年份: '+year) if year else ''}{(', 类型: '+mtype) if mtype else ''} 匹配豆瓣信息 ...")
        search_query = f"{name} {year or ''}".strip()
        result = self.search(search_query) 
        if not result: logger.warning(f"名称搜索 '{search_query}' 无返回结果"); return {"error": "no_search_result", "message": "豆瓣名称搜索无返回。"}
        if result.get("code") == 1080: 
            msg = f"触发豆瓣API速率限制: {result.get('msg', '无详细信息')}"; logger.warning(msg)
            if raise_exception: raise Exception(msg)
            return {"error": "rate_limit", "message": msg}
        items = result.get("items")
        if not items: logger.warning(f"名称搜索 '{search_query}' 未找到条目 (items为空)"); return {"error": "no_items_found", "message": f"豆瓣名称搜索 '{search_query}' 未找到条目。"}
        candidates = []; exact_match = None
        for i, item_obj in enumerate(items):
            item_layout = item_obj.get("layout"); api_item_type_outer = item_obj.get("target_type")
            if item_layout != "subject" or api_item_type_outer not in ["movie", "tv"]: continue
            target = item_obj.get("target", {}); 
            if not target: continue
            if mtype and mtype != api_item_type_outer: continue
            api_item_year_str = str(target.get("year", "")).strip(); title = target.get("title"); douban_id_from_search = str(target.get("id", "")).strip()
            if not title or not douban_id_from_search.isdigit(): continue
            year_match = False
            if year and api_item_year_str: 
                try:
                    if abs(int(api_item_year_str) - int(year)) <= 1: year_match = True
                except ValueError: pass
            elif not year: year_match = True
            if year_match:
                is_title_match = (title.lower().strip() == name.lower().strip()); is_year_exact_match_for_this_check = (api_item_year_str == year) if year else True 
                candidate_info = {"id": douban_id_from_search, "title": title, "original_title": target.get("original_title"), "year": api_item_year_str, "type": api_item_type_outer, "source": "name_search_candidate"}
                if is_title_match and is_year_exact_match_for_this_check: exact_match = candidate_info; exact_match["source"] = "name_search_exact"; break 
                candidates.append(candidate_info)
        if exact_match: return exact_match 
        if candidates:
            if len(candidates) == 1: return candidates[0] 
            else: return {"search_candidates": candidates, "message": "找到多个可能的匹配项，请选择。"}
        return {"error": "no_suitable_match", "message": f"豆瓣名称搜索未能为 '{name}' 找到合适的匹配项。"}

    def get_acting(self, name: str, imdbid: str = None, mtype: str = None, year: str = None, season: int = None, douban_id_override: Optional[str] = None) -> Optional[dict]:
        # ... (此方法内部逻辑不变，它调用 self.match_info, self._get_subject_details, self.tv_celebrities, self.movie_celebrities) ...
        douban_subject_id = None; final_mtype = mtype 
        if douban_id_override and str(douban_id_override).isdigit():
            douban_subject_id = str(douban_id_override)
            logger.info(f"使用提供的豆瓣ID覆盖: {douban_subject_id}")
            if not final_mtype and douban_subject_id:
                details_for_type = self._get_subject_details(douban_subject_id, "movie") 
                if details_for_type and details_for_type.get("type"): final_mtype = details_for_type.get("type")
                elif not final_mtype:
                    details_for_type_tv = self._get_subject_details(douban_subject_id, "tv")
                    if details_for_type_tv and details_for_type_tv.get("type"): final_mtype = details_for_type_tv.get("type")
                if final_mtype: logger.info(f"通过详情推断豆瓣ID {douban_subject_id} 的类型为: {final_mtype}")
                else: logger.warning(f"无法通过详情推断豆瓣ID {douban_subject_id} 的类型，且外部未提供mtype。")
        else: 
            match_info_result = self.match_info(name=name, imdbid=imdbid, mtype=mtype, year=year, season=season)
            if match_info_result and match_info_result.get("id") and str(match_info_result.get("id")).isdigit():
                douban_subject_id = str(match_info_result.get("id"))
                if not final_mtype and match_info_result.get("type"): final_mtype = match_info_result.get("type"); logger.info(f"从匹配结果中推断媒体类型为: {final_mtype}")
            elif match_info_result and match_info_result.get("error") == "rate_limit": logger.error(f"豆瓣API在信息匹配时触发速率限制。"); return {"error": "rate_limit", "message": match_info_result.get("message"), "cast": []}
            elif match_info_result and match_info_result.get("search_candidates"): logger.info("match_info 返回多个候选结果，get_acting 将其透传。"); return match_info_result 
        if douban_subject_id and final_mtype:
            logger.info(f"获取豆瓣ID '{douban_subject_id}' (类型: {final_mtype}) 的演职员信息...")
            response = None
            if final_mtype == "tv": response = self.tv_celebrities(douban_subject_id)
            elif final_mtype == "movie": response = self.movie_celebrities(douban_subject_id)
            else: logger.error(f"未知的媒体类型 '{final_mtype}' (豆瓣ID: {douban_subject_id}) 无法获取演职员。"); return {"error": f"未知的媒体类型 '{final_mtype}'", "cast": []}
            if not response: logger.error(f"豆瓣API未能返回ID '{douban_subject_id}' (类型: {final_mtype}) 的演职员信息。"); return {"error": f"未能获取ID '{douban_subject_id}' 的演职员信息", "cast": []}
            if response.get("code") == 1080: logger.error(f"获取演职员信息时触发豆瓣API速率限制 (ID: {douban_subject_id})。"); return {"error": "rate_limit", "message": response.get("msg"), "cast": []}
            data = {"cast": []}; actors_list = response.get("celebrities", response.get("actors", [])); 
            if actors_list is None: actors_list = []
            for idx, item in enumerate(actors_list):
                if not isinstance(item, dict): continue
                character_str_raw = ""; 
                if item.get("character"): character_str_raw = item.get("character")
                elif item.get("attrs", {}).get("role"): 
                    roles = item.get("attrs").get("role")
                    if isinstance(roles, list) and roles: character_str_raw = " / ".join(r for r in roles if isinstance(r, str))
                cleaned_char_name = clean_character_name_static(character_str_raw)
                actor_id_str = str(item.get("id", "")).strip(); actor_id_int = int(actor_id_str) if actor_id_str.isdigit() else None
                profile_img_obj = item.get("avatar", item.get("cover_url")); profile_path_val = None
                if isinstance(profile_img_obj, dict): profile_path_val = profile_img_obj.get("large", profile_img_obj.get("normal"))
                elif isinstance(profile_img_obj, str): profile_path_val = profile_img_obj
                cast_list_item = {"name": item.get("name"), "character": cleaned_char_name, "id": actor_id_int, "original_name": item.get("latin_name", item.get("name_en")), "profile_path": profile_path_val, "adult": False, "gender": 0, "known_for_department": "Acting", "popularity": 0.0, "cast_id": None, "credit_id": None, "order": item.get("rank", idx)}
                data["cast"].append(cast_list_item)
            return data
        else: logger.error(f"未能为 '{name}' (IMDB: {imdbid}, DoubanIDOverride: {douban_id_override}) 找到有效的豆瓣ID或媒体类型进行查询。"); return {"error": f"未能为 '{name}' 找到有效的豆瓣ID或媒体类型", "cast": []}

    def movie_celebrities(self, subject_id: str):
        return self.__invoke(DoubanApi._urls["movie_celebrities"] % subject_id)

    def tv_celebrities(self, subject_id: str):
        return self.__invoke(DoubanApi._urls["tv_celebrities"] % subject_id)
        
    def close(self):
        # close 方法应该是实例方法，因为它关闭的是实例持有的资源（即使session是类属性，关闭操作也应通过实例发起）
        # 或者，如果session严格是类共享的，并且希望通过类来关闭，那么close也应是@classmethod
        # 但通常session管理与实例生命周期绑定更常见。
        # 为了保持与之前代码的兼容性（MediaProcessor中调用 instance.close()），这里作为实例方法。
        if DoubanApi._session:
            DoubanApi._session.close()
            DoubanApi._session = None # 重置类属性session，以便下次实例化时重新创建
            logger.info("DoubanApi session closed.")
        DoubanApi._save_translation_cache_to_file() # 保存翻译缓存

# --- Main test block (如果需要独立测试 douban.py) ---
# if __name__ == '__main__':
#     # ... (您的测试代码可以放在这里) ...
#     logger.info("--- DoubanApi独立测试开始 ---")
#     douban_instance = DoubanApi()
#     try:
#         # 示例：测试搜索
#         # search_results = douban_instance.search("流浪地球")
#         # logger.info(f"搜索 '流浪地球' 结果: {json.dumps(search_results, indent=2, ensure_ascii=False)}")
        
#         # 示例：测试获取演员 (需要一个已知电影的IMDb ID或名称+年份)
#         # 您可以替换为实际的测试用例
#         # cast_info = douban_instance.get_acting(name="神秘海域", imdbid="tt1464335", mtype="movie", year="2022")
#         # logger.info(f"获取 '神秘海域' 演员表结果: {json.dumps(cast_info, indent=2, ensure_ascii=False)}")
#         pass
#     except Exception as e_test:
#         logger.error(f"DoubanApi测试时发生错误: {e_test}")
#     finally:
#         douban_instance.close()
#         logger.info("--- DoubanApi独立测试结束 ---")