# douban.py

import sqlite3
import os
import requests # type: ignore
from typing import Optional, Dict, Any, List
from logger_setup import logger

# --- 标准库导入 ---
import json
import re
import base64
import hashlib
import hmac
from urllib import parse
from datetime import datetime
from random import choice
import threading
# --- 标准库导入结束 ---

# --- 辅助函数 ---
def clean_character_name_static(character_name: Optional[str]) -> str:
    if not character_name:
        return ""
    name = str(character_name).strip()
    if name.startswith("饰 "): name = name[2:].strip()
    elif name.startswith("饰"): name = name[1:].strip()
    if '/' in name: name = name.split('/')[0].strip()
    voice_patterns = [
        r'\s*\((voice|Voice|VOICE)\)\s*$', r'\s*\[voice\]\s*$',
        r'\s*\(v\.o\.\)\s*$', r'\s*\(V\.O\.\)\s*$',
    ]
    for pattern in voice_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE).strip()
    return name
# --- 辅助函数结束 ---

class DoubanApi:
    _session: Optional[requests.Session] = None
    _db_path: Optional[str] = None
    _session_lock = threading.Lock()

    _urls = {
        "search": "/search/weixin", "imdbid": "/movie/imdb/%s",
        "movie_detail": "/movie/", "tv_detail": "/tv/",
        "movie_celebrities": "/movie/%s/celebrities", "tv_celebrities": "/tv/%s/celebrities",
        "celebrity_detail": "/celebrity/%s/",
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
    _default_timeout = 15 # 稍微增加超时

    def __init__(self, db_path: Optional[str] = None):
        if DoubanApi._session is None:
            DoubanApi._session = requests.Session()
            logger.debug("DoubanApi requests.Session 已初始化。")
        if db_path:
            DoubanApi._db_path = db_path
            logger.info(f"DoubanApi 将使用数据库路径进行缓存: {DoubanApi._db_path}")
        elif not DoubanApi._db_path:
            logger.warning("DoubanApi 初始化：未提供数据库路径 (db_path)，翻译缓存功能将不可用或受限。")

    @classmethod
    def _get_translation_from_db(cls, text: str, by_translated_text: bool = False, cursor: Optional[sqlite3.Cursor] = None) -> Optional[Dict[str, Any]]:
        """
        从数据库获取翻译缓存。
        :param text: 要查询的文本 (可以是原文或译文)。
        :param by_translated_text: 如果为 True，则通过译文反查原文。
        :param cursor: (可选) 使用外部提供的数据库游标，避免重复开关连接。
        :return: 包含原文、译文和引擎的字典，或 None。
        """
        conn_was_provided = cursor is not None
        internal_conn: Optional[sqlite3.Connection] = None

        if not conn_was_provided and not cls._db_path:
            logger.warning("DoubanApi._get_translation_from_db: DB path not set, cannot get cache.")
            return None

        try:
            if not cursor:
                internal_conn = sqlite3.connect(cls._db_path, timeout=10.0)
                internal_conn.row_factory = sqlite3.Row
                cursor = internal_conn.cursor()

            # 根据查询模式选择不同的SQL语句
            if by_translated_text:
                # 通过译文反查
                sql = "SELECT original_text, translated_text, engine_used FROM translation_cache WHERE translated_text = ?"
            else:
                # 默认通过原文查询
                sql = "SELECT original_text, translated_text, engine_used FROM translation_cache WHERE original_text = ?"
            
            cursor.execute(sql, (text,))
            row = cursor.fetchone()
            
            # 如果是内部创建的连接，在这里就关闭它
            if not conn_was_provided and internal_conn:
                internal_conn.close()

            return dict(row) if row else None

        except Exception as e:
            logger.error(f"DB读取翻译缓存失败 for '{text}' (by_translated: {by_translated_text}): {e}", exc_info=True)
            # 如果出错，也确保关闭内部连接
            if not conn_was_provided and internal_conn:
                try: internal_conn.close()
                except Exception: pass
            return None

    @classmethod
    def _save_translation_to_db(cls, original_text: str, translated_text: Optional[str], 
                                engine_used: Optional[str], cursor: Optional[sqlite3.Cursor] = None): # 添加 cursor 参数
        
        conn_was_provided = cursor is not None # 标记是否使用了外部游标
        internal_conn = None

        try:
            if not cursor: # 如果没有提供游标，则自己管理连接
                if not cls._db_path: 
                    logger.warning("DoubanApi._save_translation_to_db: DB path not set, cannot save cache.")
                    return
                internal_conn = sqlite3.connect(cls._db_path, timeout=10.0)
                cursor = internal_conn.cursor()
            
            # cursor 现在肯定不是 None
            cursor.execute(
                "REPLACE INTO translation_cache (original_text, translated_text, engine_used, last_updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (original_text, translated_text, engine_used)
            )
            
            if not conn_was_provided and internal_conn: # 如果是自己创建的连接，则自己提交
                internal_conn.commit()
            
            logger.debug(f"翻译缓存存DB: '{original_text}' -> '{translated_text}' (引擎: {engine_used}) (Conn provided: {conn_was_provided})")

        except Exception as e:
            logger.error(f"DB保存翻译缓存失败 for '{original_text}': {e}", exc_info=True)
            if not conn_was_provided and internal_conn: # 如果是自己创建的连接且出错，尝试回滚
                try: internal_conn.rollback()
                except Exception as rb_e: logger.error(f"DoubanApi: 翻译缓存保存回滚失败: {rb_e}")
        finally:
            if not conn_was_provided and internal_conn: # 如果是自己创建的连接，则自己关闭
                try: internal_conn.close()
                except Exception as cl_e: logger.error(f"DoubanApi: 关闭内部翻译缓存DB连接失败: {cl_e}")

    @classmethod
    def _ensure_session(cls):
        """确保 requests.Session 已初始化。线程安全。"""
        if cls._session is None:
            with cls._session_lock: # 加锁确保只有一个线程创建 session
                if cls._session is None: # 双重检查锁定模式
                    cls._session = requests.Session()
                    logger.info("DoubanApi: requests.Session 已重新初始化 (ensure_session)。")

    @classmethod
    def _sign(cls, url: str, ts: str, method='GET') -> str:
        url_path = parse.urlparse(url).path
        raw_sign = '&'.join([method.upper(), parse.quote(url_path, safe=''), ts])
        return base64.b64encode(hmac.new(cls._api_secret_key.encode(), raw_sign.encode(), hashlib.sha1).digest()).decode()

    def _make_error_dict(self, error_code: str, message: str, original_response: Optional[Dict]=None) -> Dict[str, Any]:
        """辅助函数，创建统一的错误返回字典"""
        err_dict = {"error": error_code, "message": message}
        if original_response and isinstance(original_response, dict) and original_response.get("code"):
            err_dict["douban_code"] = original_response.get("code")
        return err_dict

    def __invoke(self, url: str, **kwargs) -> Dict[str, Any]:
        DoubanApi._ensure_session() # <--- 在每次请求前确保 session 存在
        if DoubanApi._session is None: return self._make_error_dict("session_not_initialized", "Session未初始化")
        req_url = DoubanApi._base_url + url
        params: Dict[str, Any] = {'apiKey': DoubanApi._api_key, **kwargs}
        ts = params.pop('_ts', datetime.strftime(datetime.now(), '%Y%m%d'))
        params.update({'os_rom': 'android', '_ts': ts, '_sig': DoubanApi._sign(url=req_url, ts=ts)})
        headers = {'User-Agent': choice(DoubanApi._user_agents)}
        resp = None
        try:
            logger.debug(f"GET Request: {req_url}, Params: {params.get('q', params)}") # 简化日志
            resp = DoubanApi._session.get(req_url, params=params, headers=headers, timeout=DoubanApi._default_timeout)
            logger.debug(f"GET Response Status: {resp.status_code} for {params.get('q', url)}")
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                msg = response_json.get('msg', "豆瓣API速率限制")
                logger.warning(f"GET触发豆瓣速率限制: {msg}")
                return self._make_error_dict("rate_limit", msg, response_json)
            return response_json
        except requests.exceptions.HTTPError as e:
            msg = str(e)
            if e.response is not None:
                try: error_json = e.response.json(); msg = error_json.get("msg", str(e))
                except json.JSONDecodeError: msg = f"{str(e)} (响应非JSON: {e.response.text[:100]})"
            logger.error(f"HTTP error on GET {req_url}: {msg}", exc_info=True)
            return self._make_error_dict("http_error", msg, getattr(e.response, 'json', lambda: None)())
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed on GET {req_url}: {e}", exc_info=True)
            return self._make_error_dict("request_exception", str(e))
        except json.JSONDecodeError as e:
            logger.error(f"JSONDecodeError on GET {req_url}: {e}. Response text: {resp.text[:200] if resp else 'N/A'}", exc_info=True)
            return self._make_error_dict("json_decode_error", "无效的JSON响应")

    def __post(self, url: str, **kwargs) -> Dict[str, Any]:
        DoubanApi._ensure_session() # <--- 在每次请求前确保 session 存在
        if DoubanApi._session is None: return self._make_error_dict("session_not_initialized", "Session未初始化")
        req_url = DoubanApi._api_url + url
        data_payload: Dict[str, Any] = {'apikey': DoubanApi._api_key2, **kwargs}
        if '_ts' in data_payload: data_payload.pop('_ts')
        headers = {'User-Agent': choice(DoubanApi._user_agents), "Content-Type": "application/x-www-form-urlencoded; charset=utf-8", "Cookie": "bid=J9zb1zA5sJc"}
        resp = None
        try:
            logger.debug(f"POST Request: {req_url}, Data: {data_payload}")
            resp = DoubanApi._session.post(req_url, data=data_payload, headers=headers, timeout=DoubanApi._default_timeout)
            logger.debug(f"POST Response Status: {resp.status_code} for {url}")
            resp.raise_for_status()
            response_json = resp.json()
            if response_json.get("code") == 1080:
                msg = response_json.get('msg', "豆瓣API速率限制")
                logger.warning(f"POST触发豆瓣速率限制: {msg}")
                return self._make_error_dict("rate_limit", msg, response_json)
            return response_json
        except requests.exceptions.HTTPError as e:
            msg = str(e)
            if e.response is not None:
                try: error_json = e.response.json(); msg = error_json.get("msg", str(e))
                except json.JSONDecodeError: msg = f"{str(e)} (响应非JSON: {e.response.text[:100]})"
            logger.error(f"HTTP error on POST {req_url}: {msg}", exc_info=True)
            return self._make_error_dict("http_error", msg, getattr(e.response, 'json', lambda: None)())
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed on POST {req_url}: {e}", exc_info=True)
            return self._make_error_dict("request_exception", str(e))
        except json.JSONDecodeError as e:
            logger.error(f"JSONDecodeError on POST {req_url}: {e}. Response text: {resp.text[:200] if resp else 'N/A'}", exc_info=True)
            return self._make_error_dict("json_decode_error", "无效的JSON响应 (POST)")

    def imdbid(self, imdbid: str, ts: Optional[str] = None) -> Dict[str, Any]:
        params = {}
        if ts: params['_ts'] = ts
        return self.__post(DoubanApi._urls["imdbid"] % imdbid, **params)

    def search(self, keyword: str, start: int = 0, count: int = 20, ts: Optional[str] = None) -> Dict[str, Any]:
        if ts is None: ts = datetime.strftime(datetime.now(), '%Y%m%d')
        return self.__invoke(DoubanApi._urls["search"], q=keyword, start=start, count=count, _ts=ts)

    def _get_subject_details(self, subject_id: str, subject_type: str = "movie") -> Dict[str, Any]:
        if not subject_id or not str(subject_id).isdigit():
            return self._make_error_dict("invalid_param", f"无效的豆瓣 subject_id: {subject_id}")
        url_key = f"{subject_type}_detail"
        if url_key not in DoubanApi._urls:
            return self._make_error_dict("invalid_param", f"未知的 subject_type for detail: {subject_type}")
        detail_url = DoubanApi._urls[url_key] + subject_id
        logger.info(f"通过豆瓣ID获取详情: {detail_url}")
        details = self.__invoke(detail_url)
        if details.get("error"): # __invoke 返回了错误
            logger.warning(f"获取豆瓣ID {subject_id} ({subject_type}) 详情失败: {details.get('message')}")
        return details # 直接返回 __invoke 的结果 (成功或错误字典)

    def match_info(self, name: str, imdbid: Optional[str] = None, mtype: Optional[str] = None,
                   year: Optional[str] = None, season: Optional[int] = None) -> Dict[str, Any]:
        logger.debug(f"match_info called: name='{name}', imdbid='{imdbid}', mtype='{mtype}', year='{year}'")
        if imdbid and imdbid.strip().startswith("tt"):
            actual_imdbid = imdbid.strip()
            logger.info(f"尝试通过IMDBID {actual_imdbid} 查询豆瓣信息...")
            result_from_imdb = self.imdbid(actual_imdbid)
            logger.debug(f"IMDBID lookup result: {result_from_imdb}")
            if result_from_imdb.get("error"):
                logger.warning(f"IMDBID {actual_imdbid} 查询失败: {result_from_imdb.get('message')}")
                # 继续尝试名称搜索
            elif result_from_imdb.get("id"): # 豆瓣的 "id" 字段是类似 "/movie/12345/" 的URL
                douban_id_url = str(result_from_imdb.get("id"))
                match = re.search(r'/(movie|tv)/(\d+)/?$', douban_id_url)
                if match:
                    api_type, actual_douban_id = match.groups()
                    logger.info(f"IMDBID '{actual_imdbid}' -> 豆瓣ID: {actual_douban_id}, 类型: {api_type}")
                    title = result_from_imdb.get("title", result_from_imdb.get("alt_title", name))
                    original_title = result_from_imdb.get("original_title")
                    year_from_api = str(result_from_imdb.get("year", "")).strip()
                    # ... (更完善的年份提取) ...
                    return {"id": actual_douban_id, "title": title, "original_title": original_title,
                            "year": year_from_api or year, "type": mtype or api_type, "source": "imdb_lookup"}
                else:
                    logger.warning(f"IMDBID {actual_imdbid} 查询到的豆瓣ID URL '{douban_id_url}' 无法解析。")
            else:
                logger.warning(f"IMDBID {actual_imdbid} 查询结果无效或无ID。")
        # 如果IMDb查询失败或无IMDbID，则进行名称搜索
        return self._search_by_name_for_match_info(name, mtype, year, season)

    def _search_by_name_for_match_info(self, name: str, mtype: Optional[str],
                                       year: Optional[str] = None, season: Optional[int] = None) -> Dict[str, Any]:
        logger.info(f"开始使用名称 '{name}'{(', 年份: '+year) if year else ''}{(', 类型: '+mtype) if mtype else ''} 匹配豆瓣信息 ...")
        search_query = f"{name} {year or ''}".strip()
        if not search_query: return self._make_error_dict("invalid_param", "搜索关键词为空")

        search_result = self.search(search_query)
        logger.debug(f"名称搜索 '{search_query}' 原始结果: {search_result}")

        if search_result.get("error"):
            return search_result # 直接返回搜索的错误

        items = search_result.get("items")
        if not items or not isinstance(items, list):
            return self._make_error_dict("no_items_found", f"豆瓣名称搜索 '{search_query}' 未找到条目或格式错误。")

        candidates = []
        exact_match = None
        for item_obj in items:
            if not isinstance(item_obj, dict): continue
            target = item_obj.get("target", {})
            if not isinstance(target, dict): continue

            api_item_type = item_obj.get("target_type")
            if api_item_type not in ["movie", "tv"]: continue
            if mtype and mtype != api_item_type: continue # 类型不匹配

            title_from_api = target.get("title") # 先获取原始值
            douban_id = str(target.get("id", "")).strip()

            # 检查 title_from_api 是否是有效字符串
            if not isinstance(title_from_api, str) or not title_from_api.strip() or not douban_id.isdigit():
                logger.debug(f"_search_by_name_for_match_info: 跳过无效条目，title='{title_from_api}' (类型: {type(title_from_api).__name__}), douban_id='{douban_id}'")
                continue

            # 到这里，title_from_api 肯定是字符串且非空
            title_str = title_from_api # 现在可以安全地称之为 title_str

            api_item_year = str(target.get("year", "")).strip()
            year_match = (not year) or (year and api_item_year and abs(int(api_item_year) - int(year)) <= 1)

            if year_match:
                candidate_info = {"id": douban_id, "title": title_str, "original_title": target.get("original_title"),
                                  "year": api_item_year, "type": api_item_type, "source": "name_search_candidate"}
                
                # 在调用 .lower() 前确保 name 也是字符串 (虽然类型提示是 str，但多一层保险)
                name_to_compare = str(name).strip() # name 是函数传入的参数

                if title_str.lower().strip() == name_to_compare.lower() and (not year or api_item_year == year):
                    exact_match = candidate_info
                    exact_match["source"] = "name_search_exact"
                    break
                candidates.append(candidate_info)

        if exact_match: return exact_match
        if candidates:
            if len(candidates) == 1: return candidates[0]
            # 多个候选，可以考虑返回一个包含所有候选的列表，让调用者处理
            # 或者根据某种规则选择最佳的一个（例如，最接近年份的）
            # 为简单起见，这里返回第一个，但实际应用可能需要更复杂的逻辑
            logger.info(f"找到多个候选匹配项 for '{name}', 返回第一个。 Candidates: {candidates}")
            return candidates[0] # 或者: return {"search_candidates": candidates, "message": "找到多个可能的匹配项"}

        return self._make_error_dict("no_suitable_match", f"豆瓣名称搜索未能为 '{name}' 找到合适的匹配项。")

    def get_acting(self, name: str, imdbid: Optional[str] = None, mtype: Optional[str] = None,
                   year: Optional[str] = None, season: Optional[int] = None,
                   douban_id_override: Optional[str] = None) -> Dict[str, Any]:
        logger.debug(f"get_acting CALLED: name='{name}', imdbid='{imdbid}', mtype='{mtype}', year='{year}', override='{douban_id_override}'")
        douban_subject_id = None
        final_mtype = mtype

        if douban_id_override and str(douban_id_override).isdigit():
            douban_subject_id = str(douban_id_override)
            logger.info(f"使用提供的豆瓣ID覆盖: {douban_subject_id}")
            if not final_mtype: # 尝试推断类型
                details_movie = self._get_subject_details(douban_subject_id, "movie")
                if details_movie and not details_movie.get("error") and details_movie.get("type"): final_mtype = details_movie.get("type")
                else:
                    details_tv = self._get_subject_details(douban_subject_id, "tv")
                    if details_tv and not details_tv.get("error") and details_tv.get("type"): final_mtype = details_tv.get("type")
                if final_mtype: logger.info(f"推断豆瓣ID {douban_subject_id} 类型为: {final_mtype}")
                else: return self._make_error_dict("type_inference_failed", f"无法为豆瓣ID '{douban_subject_id}' 推断媒体类型", {"cast": []})
        else:
            match_info_result = self.match_info(name=name, imdbid=imdbid, mtype=mtype, year=year, season=season)
            logger.debug(f"get_acting: match_info_result = {match_info_result}")
            if match_info_result.get("error"):
                return {**match_info_result, "cast": []} # 合并错误信息并添加空cast
            if match_info_result.get("id") and str(match_info_result.get("id")).isdigit():
                douban_subject_id = str(match_info_result.get("id"))
                if not final_mtype and match_info_result.get("type"): final_mtype = match_info_result.get("type")
            # elif match_info_result.get("search_candidates"): # 如果 match_info 返回候选列表
            #     return {**match_info_result, "cast": []} # 透传并加空cast
            else: # 未找到ID
                return self._make_error_dict("no_match_id_found", f"未能为 '{name}' 匹配到豆瓣ID", {"cast": []})

        logger.debug(f"get_acting: Determined douban_subject_id='{douban_subject_id}', final_mtype='{final_mtype}'")
        if not douban_subject_id or not final_mtype:
            return self._make_error_dict("missing_id_or_type", f"获取演职员信息前豆瓣ID或类型无效 (ID: {douban_subject_id}, Type: {final_mtype})", {"cast": []})

        logger.info(f"获取豆瓣ID '{douban_subject_id}' (类型: {final_mtype}) 的演职员信息...")
        response = None
        if final_mtype == "tv": response = self.tv_celebrities(douban_subject_id)
        elif final_mtype == "movie": response = self.movie_celebrities(douban_subject_id)
        else: return self._make_error_dict("unknown_media_type", f"未知的媒体类型 '{final_mtype}'", {"cast": []})

        logger.debug(f"get_acting: celebrities_response = {response}")
        if not response or response.get("error"): # 检查错误
            err_msg = response.get("message", "获取演职员信息失败") if response else "获取演职员信息无响应"
            return self._make_error_dict(response.get("error", "api_error") if response else "no_response", err_msg, {"cast": []})

        data: Dict[str, List[Dict[str, Any]]] = {"cast": []}
        actors_list = response.get("celebrities", response.get("actors", []))
        if actors_list is None: actors_list = []

        for idx, item in enumerate(actors_list):
            if not isinstance(item, dict): continue
            character_str_raw = item.get("character", "")
            if not character_str_raw and item.get("attrs", {}).get("role"):
                roles = item.get("attrs").get("role")
                if isinstance(roles, list) and roles: character_str_raw = " / ".join(r for r in roles if isinstance(r, str))
            
            cleaned_char_name = clean_character_name_static(character_str_raw)
            actor_id_str = str(item.get("id", "")).strip()
            actor_id_int = int(actor_id_str) if actor_id_str.isdigit() else None
            profile_img_obj = item.get("avatar", item.get("cover_url"))
            profile_path_val = None
            if isinstance(profile_img_obj, dict): profile_path_val = profile_img_obj.get("large", profile_img_obj.get("normal"))
            elif isinstance(profile_img_obj, str): profile_path_val = profile_img_obj

            data["cast"].append({
                "name": item.get("name"), "character": cleaned_char_name, "id": actor_id_int,
                "original_name": item.get("latin_name", item.get("name_en")),
                "profile_path": profile_path_val, "order": item.get("rank", idx)
                # 可以添加更多TMDb兼容的字段，如果豆瓣API提供或可以转换
            })
        return data # 成功时返回包含 cast 的字典

    def movie_celebrities(self, subject_id: str) -> Dict[str, Any]:
        return self.__invoke(DoubanApi._urls["movie_celebrities"] % subject_id)

    def tv_celebrities(self, subject_id: str) -> Dict[str, Any]:
        return self.__invoke(DoubanApi._urls["tv_celebrities"] % subject_id)

    def close(self):
        with DoubanApi._session_lock: # 关闭时也加锁
            if DoubanApi._session:
                try: DoubanApi._session.close(); logger.debug("DoubanApi requests.Session 已关闭。")
                except Exception as e: logger.error(f"关闭 DoubanApi session 时出错: {e}")
                finally: DoubanApi._session = None
            logger.debug("DoubanApi close 方法执行完毕。")

    # ✨✨✨ 获取演员详细信息方法 ✨✨✨
    def celebrity_details(self, celebrity_id: str) -> Dict[str, Any]:
        """获取单个名人（演员/导演）的详细信息。"""
        if not celebrity_id or not str(celebrity_id).isdigit():
            return self._make_error_dict("invalid_param", f"无效的名人 celebrity_id: {celebrity_id}")
        
        detail_url = DoubanApi._urls["celebrity_detail"] % celebrity_id
        logger.debug(f"获取名人详情: {detail_url}")
        details = self.__invoke(detail_url)
        return details

      
    @classmethod
    def _save_translation_to_db(cls, original_text: str, translated_text: Optional[str], 
                                engine_used: Optional[str], cursor: Optional[sqlite3.Cursor] = None): # <--- 添加可选的 cursor 参数
        
        conn_was_provided = cursor is not None
        internal_conn: Optional[sqlite3.Connection] = None # 明确类型

        try:
            if not cursor: # 如果外部没有提供游标，则自己管理连接和游标
                if not cls._db_path: 
                    logger.warning("DoubanApi._save_translation_to_db: DB path not set, cannot save cache.")
                    return
                logger.debug(f"DoubanApi._save_translation_to_db: No external cursor, creating internal connection to {cls._db_path}")
                internal_conn = sqlite3.connect(cls._db_path, timeout=10.0) # 可以设置超时
                cursor = internal_conn.cursor()
            
            # 现在 cursor 肯定不是 None
            logger.debug(f"DoubanApi._save_translation_to_db: Executing REPLACE with cursor. Original: '{original_text}', Translated: '{translated_text}', Engine: '{engine_used}'")
            cursor.execute(
                "REPLACE INTO translation_cache (original_text, translated_text, engine_used, last_updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (original_text, translated_text, engine_used)
            )
            
            if not conn_was_provided and internal_conn: # 如果是自己创建的连接，则自己提交
                internal_conn.commit()
                logger.debug(f"DoubanApi._save_translation_to_db: Internal connection committed for '{original_text}'.")
            
            # logger.debug(f"翻译缓存存DB: '{original_text}' -> '{translated_text}' (引擎: {engine_used}) (External cursor: {conn_was_provided})")

        except Exception as e:
            logger.error(f"DB保存翻译缓存失败 for '{original_text}': {e}", exc_info=True)
            if not conn_was_provided and internal_conn:
                try: internal_conn.rollback()
                except Exception as rb_e: logger.error(f"DoubanApi: 翻译缓存保存回滚失败 (internal conn): {rb_e}")
        finally:
            if not conn_was_provided and internal_conn: # 如果是自己创建的连接，则自己关闭
                try: internal_conn.close()
                except Exception as cl_e: logger.error(f"DoubanApi: 关闭内部翻译缓存DB连接失败: {cl_e}")

if __name__ == '__main__':
    # (测试代码与之前版本类似，确保 TEST_DB_PATH 和表结构正确)
    TEST_DB_PATH = "test_douban_api_cache.sqlite"
    try:
        if os.path.exists(TEST_DB_PATH): os.remove(TEST_DB_PATH)
        conn_test = sqlite3.connect(TEST_DB_PATH); cursor_test = conn_test.cursor()
        cursor_test.execute("CREATE TABLE IF NOT EXISTS translation_cache (original_text TEXT PRIMARY KEY, translated_text TEXT, engine_used TEXT, last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn_test.commit(); conn_test.close(); logger.info(f"测试数据库 '{TEST_DB_PATH}' 已创建。")

        api = DoubanApi(db_path=TEST_DB_PATH)
        # 测试1: 缓存
        api._save_translation_to_db("Test", "测试", "manual")
        cached = api._get_translation_from_db("Test")
        logger.info(f"缓存测试: {cached}")
        # 测试2: 搜索 (需要网络)
        # search_res = api.search("你好，李焕英")
        # logger.info(f"搜索结果: {json.dumps(search_res, indent=2, ensure_ascii=False)}")
        # 测试3: 获取演员 (需要网络，并替换为有效的电影名/ID)
        # acting_res = api.get_acting(name="你好，李焕英", mtype="movie", year="2021")
        # logger.info(f"演职员结果: {json.dumps(acting_res, indent=2, ensure_ascii=False)}")
    except Exception as e: logger.error(f"DoubanApi 测试异常: {e}", exc_info=True)
    finally:
        if 'api' in locals() and api: api.close()
        # if os.path.exists(TEST_DB_PATH): os.remove(TEST_DB_PATH) # 测试后清理
        logger.info("--- DoubanApi 测试结束 ---")