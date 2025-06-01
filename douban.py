# douban.py

import sqlite3
import os
import requests
from typing import Optional, Dict, Any, List
from logger_setup import logger # 假设 logger_setup.py 定义了 logger
# import constants # 如果 DoubanApi 内部直接用到 constants 中的值

# --- 标准库导入 ---
import json                 # 用于处理 JSON 数据 (主要是在解析HTTP错误响应时)
import re                   # 用于正则表达式
import base64               # 用于 _sign 方法
import hashlib              # 用于 _sign 方法
import hmac                 # 用于 _sign 方法
from urllib import parse    # 用于 _sign 方法中的 urlparse 和 quote
from datetime import datetime # 用于生成时间戳 _ts
from random import choice   # 用于随机选择 User-Agent
# --- 标准库导入结束 ---

# --- 辅助函数 (如果不想从 utils.py 导入，可以在这里定义) ---
def clean_character_name_static(character_name: Optional[str]) -> str:
    """
    静态辅助函数：移除角色名前的 '饰 ' 或 '饰' 及前后空格。
    处理 "角色名1 / 角色名2"，只取第一个。
    移除末尾的常见英文配音标记。
    """
    if not character_name:
        return ""
    name = str(character_name).strip()

    if name.startswith("饰 "):
        name = name[2:].strip()
    elif name.startswith("饰"):
        name = name[1:].strip()

    if '/' in name: # 取第一个角色名
        name = name.split('/')[0].strip()

    # 移除常见的英文配音标记
    voice_patterns = [
        r'\s*\((voice|Voice|VOICE)\)\s*$',
        r'\s*\[voice\]\s*$',
        r'\s*\(v\.o\.\)\s*$',
        r'\s*\(V\.O\.\)\s*$',
    ]
    for pattern in voice_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE).strip()
    return name
# --- 辅助函数结束 ---


class DoubanApi:
    _session: Optional[requests.Session] = None
    _db_path: Optional[str] = None # 类属性，用于存储数据库路径

    # API 配置 (类属性)
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
    _api_secret_key = "bf7dddc7c9cfe6f7"  # 注意：硬编码密钥可能不是最佳实践
    _api_key = "0dad551ec0f84ed02907ff5c42e8ec70"
    _api_key2 = "0ab215a8b1977939201640fa14c66bab"
    _base_url = "https://frodo.douban.com/api/v2"
    _api_url = "https://api.douban.com/v2"
    _default_timeout = 10 # seconds

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化 DoubanApi 实例。
        :param db_path: SQLite 数据库文件的路径，用于存储翻译缓存。
        """
        if DoubanApi._session is None:
            DoubanApi._session = requests.Session()
            logger.debug("DoubanApi requests.Session 已初始化。")

        if db_path:
            DoubanApi._db_path = db_path # 设置类属性 _db_path
            logger.info(f"DoubanApi 将使用数据库路径进行缓存: {DoubanApi._db_path}")
        elif not DoubanApi._db_path: # 只有当类属性 _db_path 也未被之前的实例设置过才警告
            logger.warning("DoubanApi 初始化：未提供数据库路径 (db_path)，翻译缓存功能将不可用或受限。")
        # 如果 DoubanApi._db_path 已被设置，则当前实例沿用该设置

    @classmethod
    def _get_translation_from_db(cls, original_text: str) -> Optional[Dict[str, Any]]:
        """从数据库获取翻译缓存。"""
        if not cls._db_path:
            logger.debug("DoubanApi: 数据库路径未设置，无法从DB读取翻译缓存。")
            return None
        try:
            conn = sqlite3.connect(cls._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT translated_text, engine_used FROM translation_cache WHERE original_text = ?",
                (original_text,)
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return {"translated_text": row["translated_text"], "engine_used": row["engine_used"]}
            return None
        except Exception as e:
            logger.error(f"从数据库读取翻译缓存失败 for '{original_text}': {e}", exc_info=True)
            return None

    @classmethod
    def _save_translation_to_db(cls, original_text: str, translated_text: Optional[str], engine_used: Optional[str]):
        """将翻译结果保存到数据库。"""
        if not cls._db_path:
            logger.warning("DoubanApi: 数据库路径未设置，无法保存翻译缓存到DB。")
            return
        try:
            conn = sqlite3.connect(cls._db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                REPLACE INTO translation_cache (original_text, translated_text, engine_used, last_updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (original_text, translated_text, engine_used)
            )
            conn.commit()
            conn.close()
            logger.debug(f"翻译缓存已保存到数据库: '{original_text}' -> '{translated_text}' (引擎: {engine_used})")
        except Exception as e:
            logger.error(f"保存翻译缓存到数据库失败 for '{original_text}': {e}", exc_info=True)

    @classmethod
    def _sign(cls, url: str, ts: str, method='GET') -> str:
        url_path = parse.urlparse(url).path
        raw_sign = '&'.join([method.upper(), parse.quote(url_path, safe=''), ts])
        return base64.b64encode(
            hmac.new(cls._api_secret_key.encode(), raw_sign.encode(), hashlib.sha1).digest()
        ).decode()

    def __invoke(self, url: str, **kwargs) -> Dict[str, Any]:
        """执行 GET 请求 (Frodo API)"""
        if DoubanApi._session is None: # 防御性编程，确保 session 存在
            logger.error("DoubanApi: requests.Session 未初始化，无法执行请求。")
            return {"error": "session_not_initialized", "message": "Session not initialized."}

        req_url = DoubanApi._base_url + url
        params: Dict[str, Any] = {'apiKey': DoubanApi._api_key}
        if kwargs:
            params.update(kwargs)
        ts = params.pop('_ts', datetime.strftime(datetime.now(), '%Y%m%d'))
        params.update({'os_rom': 'android', 'apiKey': DoubanApi._api_key,
                       '_ts': ts, '_sig': DoubanApi._sign(url=req_url, ts=ts)})
        headers = {'User-Agent': choice(DoubanApi._user_agents)}
        resp = None
        try:
            logger.debug(f"GET Request URL: {req_url}, Params: {params}")
            resp = DoubanApi._session.get(req_url, params=params, headers=headers, timeout=DoubanApi._default_timeout)
            logger.debug(f"GET Response Status: {resp.status_code}")
            resp.raise_for_status() # 如果状态码是 4xx 或 5xx，则抛出 HTTPError
            response_json = resp.json() # 使用 requests 内置的 JSON 解析
            if response_json.get("code") == 1080: # 豆瓣API速率限制的特定代码
                 logger.warning(f"GET请求触发豆瓣API速率限制: {response_json.get('msg')}")
                 return {"error": "rate_limit", "message": response_json.get("msg", "Rate limit triggered."), "code": 1080}
            return response_json
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error: {e}"
            response_text = e.response.text[:200] if e.response else "N/A"
            logger.error(f"{error_msg} - URL: {getattr(resp, 'request', {}).get('url', req_url)} - Response: {response_text}")
            try:
                error_json = e.response.json() if e.response else {}
                if error_json.get("code") == 1080:
                     return {"error": "rate_limit", "message": error_json.get("msg", "Rate limit triggered."), "code": 1080}
                return error_json if error_json else {"error": "http_error", "message": str(e)}
            except json.JSONDecodeError:
                return {"error": "http_error_non_json", "message": f"{str(e)} (Non-JSON response)"}
        except requests.exceptions.RequestException as e: # 其他网络问题，如超时、DNS错误
            logger.error(f"Request failed: {e} - URL: {req_url}", exc_info=True)
            return {"error": "request_exception", "message": str(e)}
        except json.JSONDecodeError: # 如果 resp.json() 解析失败
            status_code = resp.status_code if resp else "N/A"
            text_preview = resp.text[:200] if resp else "N/A"
            logger.error(f"Failed to decode JSON. URL: {req_url}, Status: {status_code}, Text: {text_preview}", exc_info=True)
            return {"error": "json_decode_error", "message": "Invalid JSON response"}


    def __post(self, url: str, **kwargs) -> Dict[str, Any]:
        """执行 POST 请求 (API v2)"""
        if DoubanApi._session is None:
            logger.error("DoubanApi: requests.Session 未初始化，无法执行请求。")
            return {"error": "session_not_initialized", "message": "Session not initialized."}

        req_url = DoubanApi._api_url + url
        data_payload: Dict[str, Any] = {'apikey': DoubanApi._api_key2}
        if kwargs:
            data_payload.update(kwargs)
        if '_ts' in data_payload: data_payload.pop('_ts') # POST 请求通常不需要 _ts 和 _sig
        headers = {'User-Agent': choice(DoubanApi._user_agents),
                   "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                   "Cookie": "bid=J9zb1zA5sJc"} # 这个 Cookie 可能需要更新或动态获取
        resp = None
        try:
            logger.debug(f"POST Request URL: {req_url}, Data: {data_payload}")
            resp = DoubanApi._session.post(req_url, data=data_payload, headers=headers, timeout=DoubanApi._default_timeout)
            logger.debug(f"POST Response Status: {resp.status_code}")
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                 logger.warning(f"POST请求触发豆瓣API速率限制: {response_json.get('msg')}")
                 return {"error": "rate_limit", "message": response_json.get("msg", "Rate limit triggered."), "code": 1080}
            return response_json
        except requests.exceptions.HTTPError as e:
            error_msg = f"POST HTTP error: {e}"
            response_text = e.response.text[:200] if e.response else "N/A"
            logger.error(f"{error_msg} - URL: {getattr(resp, 'request', {}).get('url', req_url)} - Response: {response_text}")
            try:
                error_json = e.response.json() if e.response else {}
                if error_json.get("code") == 1080:
                     return {"error": "rate_limit", "message": error_json.get("msg", "Rate limit triggered."), "code": 1080}
                return error_json if error_json else {"error": "http_error", "message": str(e)}
            except json.JSONDecodeError:
                return {"error": "http_error_non_json", "message": f"{str(e)} (Non-JSON response from POST)"}
        except requests.exceptions.RequestException as e:
            logger.error(f"POST Request failed: {e} - URL: {req_url}", exc_info=True)
            return {"error": "request_exception", "message": str(e)}
        except json.JSONDecodeError:
            status_code = resp.status_code if resp else "N/A"
            text_preview = resp.text[:200] if resp else "N/A"
            logger.error(f"Failed to decode JSON from POST. URL: {req_url}, Status: {status_code}, Text: {text_preview}", exc_info=True)
            return {"error": "json_decode_error", "message": "Invalid JSON response from POST"}

    # --- 公共 API 方法 ---
    def imdbid(self, imdbid: str, ts: Optional[str] = None) -> Dict[str, Any]:
        params = {}
        if ts: params['_ts'] = ts
        return self.__post(DoubanApi._urls["imdbid"] % imdbid, **params)

    def search(self, keyword: str, start: int = 0, count: int = 20,
               ts: Optional[str] = None) -> Dict[str, Any]:
        if ts is None:
            ts = datetime.strftime(datetime.now(), '%Y%m%d')
        return self.__invoke(DoubanApi._urls["search"], q=keyword, start=start, count=count, _ts=ts)

    def _get_subject_details(self, subject_id: str, subject_type: str = "movie") -> Optional[Dict[str, Any]]:
        if not subject_id or not str(subject_id).isdigit():
            logger.warning(f"无效的豆瓣 subject_id: {subject_id}")
            return None
        url_key = f"{subject_type}_detail"
        if url_key not in DoubanApi._urls:
            logger.error(f"未知的 subject_type for detail: {subject_type}")
            return None
        detail_url = DoubanApi._urls[url_key] + subject_id
        logger.info(f"通过豆瓣ID获取详情: {detail_url}")
        details = self.__invoke(detail_url)
        if details and not details.get("code"): # 假设无 code 字段表示成功
            return details
        elif details and details.get("code") == 1080: # 速率限制
            logger.warning(f"获取豆瓣ID {subject_id} ({subject_type}) 详情时触发速率限制: {details.get('msg')}")
            return details # 返回包含错误信息的字典
        else:
            logger.warning(f"获取豆瓣ID {subject_id} ({subject_type}) 详情失败: {details.get('msg') if details else '无结果'}")
            return None # 或者返回包含错误信息的字典

    def match_info(self, name: str, imdbid: Optional[str] = None, mtype: Optional[str] = None,
                   year: Optional[str] = None, season: Optional[int] = None,
                   raise_exception: bool = False) -> Dict[str, Any]:
        # (这个方法的逻辑与你之前的版本类似，确保它调用 self.imdbid 和 self._search_by_name_for_match_info)
        # 为简洁起见，这里省略详细实现，假设它能正常工作
        if imdbid and imdbid.strip().startswith("tt"):
            # ... (通过 IMDb ID 查找的逻辑) ...
            # 示例：
            result_imdb = self.imdbid(imdbid.strip())
            if result_imdb and result_imdb.get("id"): # 假设成功
                 # 解析 result_imdb 得到豆瓣ID、类型等
                 # return {"id": douban_id, "title": title, ... "source": "imdb_lookup"}
                 pass # 你需要填充这里的逻辑
        return self._search_by_name_for_match_info(name, mtype, year, season, raise_exception)


    def _search_by_name_for_match_info(self, name: str, mtype: Optional[str],
                                       year: Optional[str] = None, season: Optional[int] = None,
                                       raise_exception: bool = False) -> Dict[str, Any]:
        # (这个方法的逻辑与你之前的版本类似，确保它调用 self.search 并处理结果)
        # 为简洁起见，这里省略详细实现
        logger.info(f"开始使用名称 '{name}' 匹配豆瓣信息...")
        # ... (调用 self.search, 筛选结果) ...
        # return {"id": best_match_id, "title": title, ... "source": "name_search"}
        # 或 return {"error": "no_match", ...}
        return {"error": "not_implemented", "message": "_search_by_name_for_match_info not fully implemented"}


    def get_acting(self, name: str, imdbid: Optional[str] = None, mtype: Optional[str] = None,
                   year: Optional[str] = None, season: Optional[int] = None,
                   douban_id_override: Optional[str] = None) -> Dict[str, Any]:
        # (这个方法的逻辑与你之前的版本类似，确保它调用 self.match_info, self._get_subject_details,
        #  self.tv_celebrities, self.movie_celebrities，并使用 clean_character_name_static)
        # 为简洁起见，这里省略详细实现
        douban_subject_id = None
        final_mtype = mtype
        # ... (获取 douban_subject_id 和 final_mtype 的逻辑) ...

        if douban_subject_id and final_mtype:
            logger.info(f"获取豆瓣ID '{douban_subject_id}' (类型: {final_mtype}) 的演职员信息...")
            response = None
            if final_mtype == "tv": response = self.tv_celebrities(douban_subject_id)
            elif final_mtype == "movie": response = self.movie_celebrities(douban_subject_id)
            else:
                return {"error": f"未知的媒体类型 '{final_mtype}'", "cast": []}

            if not response or response.get("code"): # 处理API错误或速率限制
                err_msg = response.get("msg", "获取演职员信息失败") if response else "获取演职员信息无响应"
                logger.error(f"获取演职员信息失败/错误 (ID: {douban_subject_id}): {err_msg}")
                return {"error": "api_error", "message": err_msg, "cast": []}

            data: Dict[str, List[Dict[str, Any]]] = {"cast": []}
            actors_list = response.get("celebrities", response.get("actors", []))
            for idx, item in enumerate(actors_list):
                if not isinstance(item, dict): continue
                # ... (解析演员信息，使用 clean_character_name_static) ...
                # cleaned_char = clean_character_name_static(item.get("character"))
                # data["cast"].append({...})
            return data
        else:
            return {"error": f"未能为 '{name}' 找到有效的豆瓣ID或媒体类型", "cast": []}


    def movie_celebrities(self, subject_id: str) -> Dict[str, Any]:
        return self.__invoke(DoubanApi._urls["movie_celebrities"] % subject_id)

    def tv_celebrities(self, subject_id: str) -> Dict[str, Any]:
        return self.__invoke(DoubanApi._urls["tv_celebrities"] % subject_id)

    def close(self):
        """关闭与此 DoubanApi 实例相关的资源，例如 requests.Session。"""
        if DoubanApi._session: # Session 是类共享的
            try:
                DoubanApi._session.close()
                logger.info("DoubanApi requests.Session 已关闭。")
            except Exception as e_session_close:
                logger.error(f"关闭 DoubanApi session 时发生错误: {e_session_close}")
            finally:
                DoubanApi._session = None # 重置，以便下次可以重新初始化
        # 数据库连接是即用即关的，所以这里不需要额外关闭数据库相关的
        logger.info("DoubanApi close 方法执行完毕。")


if __name__ == '__main__':
    # --- 测试代码 ---
    # 为了测试，你需要一个临时的数据库文件
    TEST_DB_PATH = "test_douban_cache.sqlite"
    # 初始化测试数据库表 (与 web_app.py 中的 init_db 类似)
    try:
        if os.path.exists(TEST_DB_PATH): os.remove(TEST_DB_PATH) # 清理旧的测试库
        conn_test = sqlite3.connect(TEST_DB_PATH)
        cursor_test = conn_test.cursor()
        cursor_test.execute('''
            CREATE TABLE IF NOT EXISTS translation_cache (
                original_text TEXT PRIMARY KEY,
                translated_text TEXT,
                engine_used TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn_test.commit()
        conn_test.close()
        logger.info(f"测试数据库 '{TEST_DB_PATH}' 已创建。")

        # 创建 DoubanApi 实例并传入测试数据库路径
        douban_api_instance = DoubanApi(db_path=TEST_DB_PATH)

        # 1. 测试翻译缓存保存
        logger.info("测试翻译缓存保存...")
        DoubanApi._save_translation_to_db("Hello", "你好", "test_engine")
        DoubanApi._save_translation_to_db("World", "世界", "test_engine")

        # 2. 测试翻译缓存读取
        logger.info("测试翻译缓存读取...")
        cached_hello = DoubanApi._get_translation_from_db("Hello")
        cached_world = DoubanApi._get_translation_from_db("World")
        cached_none = DoubanApi._get_translation_from_db("Unknown")
        logger.info(f"Cached 'Hello': {cached_hello}")
        logger.info(f"Cached 'World': {cached_world}")
        logger.info(f"Cached 'Unknown': {cached_none}")

        # 3. 测试搜索 (需要网络)
        # logger.info("测试搜索 API...")
        # search_result = douban_api_instance.search("流浪地球")
        # logger.info(f"搜索 '流浪地球' 结果: {json.dumps(search_result, indent=2, ensure_ascii=False)}")

        # 4. 测试获取演职员 (需要网络，并替换为有效的电影名/ID)
        # logger.info("测试获取演职员 API...")
        # acting_info = douban_api_instance.get_acting(name="神秘海域", imdbid="tt1464335")
        # logger.info(f"获取 '神秘海域' 演职员结果: {json.dumps(acting_info, indent=2, ensure_ascii=False)}")

    except Exception as e_test:
        logger.error(f"DoubanApi 测试时发生错误: {e_test}", exc_info=True)
    finally:
        if 'douban_api_instance' in locals() and douban_api_instance:
            douban_api_instance.close()
        if os.path.exists(TEST_DB_PATH): # 清理测试数据库
            # os.remove(TEST_DB_PATH)
            logger.info(f"测试结束，测试数据库 '{TEST_DB_PATH}' 已保留，请手动删除。")
        logger.info("--- DoubanApi 测试结束 ---")