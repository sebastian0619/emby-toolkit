# utils.py
import os
import sys
import re
from typing import Optional, Dict, List # 确保 Dict 和 List 已导入
import time
import requests # 用于TMDB API调用
import translators as ts # 用于翻译
from logger_setup import logger # 从主项目导入logger


# --- 翻译函数 (支持多引擎回退) ---
def translate_text_with_translators(
    text_to_translate: str,
    to_language: str = 'zh',
    from_language: str = 'auto',
    engine_order: Optional[List[str]] = None,
    overall_timeout_seconds: int = 45 # 新增一个总超时参数
) -> Optional[str]:
    if not text_to_translate or not text_to_translate.strip():
        return None

    function_start_time = time.time() # 记录函数开始时间

    active_engines = engine_order
    try:
        import constants as app_constants
        if not active_engines:
            active_engines = list(app_constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
            print(f"[翻译工具-警告] 未提供翻译引擎顺序，使用默认配置引擎: {', '.join(active_engines)}")
        valid_engines_to_try = [eng for eng in active_engines if eng in app_constants.AVAILABLE_TRANSLATOR_ENGINES]
        if len(valid_engines_to_try) != len(active_engines):
            print(f"[翻译工具-警告] 配置的引擎列表包含无效引擎，已过滤。有效尝试顺序: {', '.join(valid_engines_to_try)}")
        active_engines = valid_engines_to_try
        if not active_engines:
             active_engines = ["bing"]
             print(f"[翻译工具-错误] 有效翻译引擎列表为空！使用最终回退引擎: {', '.join(active_engines)}")
    except ImportError:
        if not active_engines:
            active_engines = ["bing", "youdao"]
            print(f"[翻译工具-警告] constants.py 未加载。未使用配置引擎顺序，使用硬编码回退: {', '.join(active_engines)}")
    
    print(f"[翻译工具-信息] 将尝试以下引擎顺序: {', '.join(active_engines)} 对文本: '{text_to_translate}'")

    raw_translated_text = None
    last_exception_str = "No successful translation."
    engine_timeout = 10 # 每个引擎的超时时间缩短到10秒

    for engine_name in active_engines:
        # 检查总体函数是否超时
        if time.time() - function_start_time > overall_timeout_seconds:
            print(f"[翻译工具-总超时] 翻译 '{text_to_translate}' 已超过总时长 {overall_timeout_seconds}s，放弃。")
            last_exception_str = f"Overall function timeout after {overall_timeout_seconds}s."
            break # 跳出引擎循环

        engine_attempt_start_time = time.time()
        print(f"  [翻译工具-引擎尝试-{engine_name}] START @ {time.strftime('%H:%M:%S')}")
        
        current_translation = None # 初始化
        try:
            current_translation = ts.translate_text(
                query_text=text_to_translate,
                translator=engine_name,
                from_language=from_language,
                to_language=to_language,
                timeout=engine_timeout, # 使用缩短的超时
            )
            engine_attempt_duration = time.time() - engine_attempt_start_time
            print(f"  [翻译工具-引擎尝试-{engine_name}] END @ {time.strftime('%H:%M:%S')}. Duration: {engine_attempt_duration:.2f}s. Result: '{str(current_translation)[:50]}...'")


            if current_translation and current_translation.strip():
                if current_translation.strip().lower() != text_to_translate.strip().lower():
                    print(f"    [翻译工具-成功] 引擎 [{engine_name}] 翻译结果有效。")
                    raw_translated_text = current_translation.strip()
                    break
                else:
                    print(f"    [翻译工具-警告] 引擎 [{engine_name}] 返回结果与原文相同。")
                    last_exception_str = f"Engine {engine_name} returned same as original."
            else:
                print(f"    [翻译工具-警告] 引擎 [{engine_name}] 返回空结果。")
                last_exception_str = f"Engine {engine_name} returned empty."
        
        except requests.exceptions.Timeout:
            engine_attempt_duration = time.time() - engine_attempt_start_time
            print(f"  [翻译工具-引擎尝试-{engine_name}] TIMEOUT @ {time.strftime('%H:%M:%S')}. Duration: {engine_attempt_duration:.2f}s.")
            last_exception_str = f"Engine {engine_name} timed out (requests.exceptions.Timeout)."
        except Exception as e:
            engine_attempt_duration = time.time() - engine_attempt_start_time
            import traceback
            error_detail = traceback.format_exc()
            print(f"  [翻译工具-引擎尝试-{engine_name}] ERROR @ {time.strftime('%H:%M:%S')}. Duration: {engine_attempt_duration:.2f}s. Error: {e}\n    Detail: {error_detail.splitlines()[-1]}")
            last_exception_str = f"Engine {engine_name} error: {e}"
        
        if not raw_translated_text and engine_name != active_engines[-1]:
             time.sleep(0.1) # 失败后切换引擎的延时缩短

    if raw_translated_text:
        formatted_translation = format_translation_with_original(raw_translated_text, text_to_translate)
        print(f"[翻译工具-格式化后] 最终结果: '{formatted_translation}' (原文: '{text_to_translate}')")
        return formatted_translation
    else:
        total_duration = time.time() - function_start_time
        print(f"[翻译工具-最终失败] 所有引擎尝试完毕或总超时. 未能翻译: '{text_to_translate}'. 总耗时: {total_duration:.2f}s. Last status: {last_exception_str}")
        return None

# --- 文件路径工具 ---
def get_base_path_for_files():
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    else:
        try:
            application_path = os.path.dirname(os.path.abspath(__file__))
        except NameError: 
            application_path = os.getcwd()
    return application_path

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

# --- 媒体项Key生成 ---
def create_media_item_key(media_type, tmdb_id_str):
    if not media_type or not tmdb_id_str:
        logger.error(f"创建媒体键失败：类型 '{media_type}' 或 ID '{tmdb_id_str}' 无效。")
        return None
    return f"{str(media_type).lower()}_{str(tmdb_id_str)}"

# --- 中文字符判断 ---
def is_chinese_char(char):
    return '\u4e00' <= char <= '\u9fff'

def contains_chinese(text):
    if not text:
        return False
    for char in text:
        if is_chinese_char(char):
            return True
    return False

# --- 英文/拼音判断 (辅助) ---
def is_predominantly_english_or_pinyin(text):
    if not text:
        return False
    chinese_char_count = 0
    total_meaningful_chars = 0
    has_letter = False
    for char in text:
        if char.strip():
            total_meaningful_chars += 1
            if is_chinese_char(char):
                chinese_char_count += 1
            elif 'a' <= char.lower() <= 'z':
                has_letter = True
    if total_meaningful_chars == 0:
        return False
    if chinese_char_count == 0 and has_letter:
        return True
    return False

# --- 角色名有效性判断 ---
def is_role_name_valid(character_name, actor_name_for_log=""):
    # 1. 处理 None, 空字符串, 或纯空白字符串的情况 -> 直接视为有效
    if character_name is None:
        logger.debug(f"角色名 (演员: {actor_name_for_log}): 为 None，判断为有效。")
        return True
    
    name_after_strip = str(character_name).strip()
    if not name_after_strip: # 如果 strip 后为空字符串
        logger.debug(f"角色名 (演员: {actor_name_for_log}): 为空或仅含空白 ('{character_name}'), 判断为有效。")
        return True

    # 2. 处理引号包裹的情况，如果移除引号后为空，也视为有效
    name_to_check = name_after_strip # 从这里开始，name_to_check 肯定是非空白字符串
    if (name_to_check.startswith("'") and name_to_check.endswith("'")) or \
       (name_to_check.startswith('"') and name_to_check.endswith('"')):
        if len(name_to_check) >= 2:
            name_to_check = name_to_check[1:-1].strip() # 再次 strip 以防引号内有空格
        if not name_to_check: # 如果移除引号后变为空
            logger.debug(f"角色名 (演员: {actor_name_for_log}): 移除引号后为空 (原始: '{character_name}'), 判断为有效。")
            return True
    
    # 如果到这里 name_to_check 仍然是有效的非空白字符串，则进行后续判断

    # 3. 特定允许的短代码或模式 -> 视为有效
    allowed_single_char_codes = ["m", "q", "r"] # 这些通常是特定含义的代号
    if len(name_to_check) == 1 and name_to_check.lower() in allowed_single_char_codes:
        logger.debug(f"角色名 '{character_name}' (处理后为 '{name_to_check}', 演员: {actor_name_for_log}) 是允许的单字符短代号，判断为有效。")
        return True

    if len(name_to_check) == 1 and 'A' <= name_to_check <= 'Z': # 单个大写英文字母
        logger.debug(f"角色名 '{character_name}' (处理后为 '{name_to_check}', 演员: {actor_name_for_log}) 是一位大写字母，判断为有效。")
        return True
    if len(name_to_check) == 2 and name_to_check.isupper() and name_to_check.isalpha(): # 两位大写英文字母
        logger.debug(f"角色名 '{character_name}' (处理后为 '{name_to_check}', 演员: {actor_name_for_log}) 是两位大写字母，判断为有效。")
        return True

    # 4. 包含中文字符 -> 视为有效
    if contains_chinese(name_to_check):
        logger.debug(f"角色名 '{character_name}' (处理后为 '{name_to_check}', 演员: {actor_name_for_log}) 包含中文字符，判断为有效。")
        return True

    # 5. 如果以上所有“直接通过”的条件都不满足，则认为该角色名需要进一步处理（例如翻译），
    #    因此在此校验函数中返回 False (表示“当前状态不是最终有效状态”)。
    logger.debug(f"角色名 '{character_name}' (处理后为 '{name_to_check}', 演员: {actor_name_for_log}) 不符合任何“直接通过”的规范，判断为“待处理/当前无效”。")
    return False

# --- TMDB API 相关函数 ---
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

def get_tmdb_person_details(person_id: int, api_key: str) -> Optional[Dict]:
    if not api_key:
        logger.error("TMDB API Key未配置，无法获取演员信息。")
        return None
    if not person_id:
        logger.error("无效的TMDB person_id。")
        return None
    url = f"{TMDB_API_BASE_URL}/person/{person_id}"
    params = {"api_key": api_key, "language": "zh-CN"}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        person_data = response.json()
        logger.debug(f"成功从TMDB API获取演员 {person_id} 的信息: {person_data.get('name')}")
        return person_data
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401: logger.error(f"TMDB API请求失败 (演员ID: {person_id}): 无效的API Key或未授权。")
        elif e.response.status_code == 404: logger.warning(f"TMDB API未找到演员ID: {person_id}。")
        else: logger.error(f"TMDB API请求HTTP错误 (演员ID: {person_id}): {e.response.status_code} - {e.response.text[:200]}")
        return None
    except requests.exceptions.RequestException as e: logger.error(f"TMDB API请求失败 (演员ID: {person_id}): {e}"); return None
    except Exception as e: import traceback; logger.error(f"处理TMDB API响应时发生未知错误 (演员ID: {person_id}): {e}\n{traceback.format_exc()}"); return None

def format_tmdb_person_to_cast_entry(person_data: Dict, character_name: str = "未知角色", order: int = 999) -> Optional[Dict]:
    if not person_data or not isinstance(person_data, dict): return None
    original_name_val = person_data.get("name")
    if contains_chinese(person_data.get("name", "")):
        if person_data.get("also_known_as"):
            for aka_name in person_data.get("also_known_as"):
                if isinstance(aka_name, str) and not contains_chinese(aka_name) and aka_name.strip():
                    original_name_val = aka_name.strip(); break
    if person_data.get("original_name"): original_name_val = person_data.get("original_name")

    cast_entry = {
        "adult": person_data.get("adult", False), "gender": person_data.get("gender"),
        "id": person_data.get("id"), "known_for_department": person_data.get("known_for_department", "Acting"),
        "name": person_data.get("name"), "original_name": original_name_val,
        "popularity": person_data.get("popularity", 0.0), "profile_path": person_data.get("profile_path"),
        "cast_id": None, "character": character_name,
        "credit_id": f"nm{person_data.get('id')}_{int(time.time())}", "order": order
    }
    if not cast_entry["id"] or not cast_entry["name"]:
        logger.error(f"从TMDB person_data格式化演员条目失败：缺少ID或名称。Data: {person_data.get('id')}, {person_data.get('name')}")
        return None
    return cast_entry

def search_tmdb_media_by_name(query: str, api_key: str, media_type: Optional[str] = None, year: Optional[str] = None) -> List[Dict]:
    if not api_key: logger.error("TMDB API Key未配置，无法进行影视搜索。"); return []
    if not query or not query.strip(): logger.warning("影视搜索查询为空。"); return []
    results = []
    search_endpoints = []
    if media_type == "movie": search_endpoints.append(f"{TMDB_API_BASE_URL}/search/movie")
    elif media_type == "tv": search_endpoints.append(f"{TMDB_API_BASE_URL}/search/tv")
    else: search_endpoints.extend([f"{TMDB_API_BASE_URL}/search/movie", f"{TMDB_API_BASE_URL}/search/tv"])

    for endpoint_url in search_endpoints:
        params = {"api_key": api_key, "query": query, "language": "zh-CN", "page": 1, "include_adult": False}
        base_search_type = "movie" if "/search/movie" in endpoint_url else ("tv" if "/search/tv" in endpoint_url else "unknown")
        if base_search_type == "movie" and year: params["primary_release_year"] = year
        elif base_search_type == "tv" and year: params["first_air_date_year"] = year
        logger.debug(f"TMDB影视搜索: URL='{endpoint_url}', Params='{params}'")
        try:
            response = requests.get(endpoint_url, params=params, timeout=10)
            logger.debug(f"TMDB Raw Response Status: {response.status_code} for URL: {response.url}")
            # logger.debug(f"TMDB Raw Response Text (first 500 chars): {response.text[:500]}")
            response.raise_for_status()
            data = response.json()
            for item in data.get("results", []):
                item_id = item.get("id")
                item_media_type = item.get("media_type", base_search_type)
                if item_media_type == "unknown": # 再次修正
                    if "/search/movie" in endpoint_url: item_media_type = "movie"
                    elif "/search/tv" in endpoint_url: item_media_type = "tv"
                if not item_id or item_media_type not in ["movie", "tv"]: continue
                title = item.get("title") if item_media_type == "movie" else item.get("name")
                release_date_str = item.get("release_date") if item_media_type == "movie" else item.get("first_air_date")
                item_year = release_date_str[:4] if release_date_str and len(release_date_str) >= 4 else ""
                if year and item_year and item_year != year:
                    if not (("primary_release_year" in params and base_search_type == "movie") or \
                            ("first_air_date_year" in params and base_search_type == "tv")):
                        continue
                logger.debug(f"  添加结果: ID={item_id}, Title='{title}', Year='{item_year}', Type='{item_media_type}'")
                results.append({
                    "id": item_id, "title": title, "year": item_year, "media_type": item_media_type,
                    "overview": item.get("overview", ""), "poster_path": item.get("poster_path", "")
                })
        except requests.exceptions.HTTPError as e: logger.error(f"TMDB影视搜索HTTP错误 ({query}): {e.response.status_code} - {e.response.text[:100]}")
        except requests.exceptions.RequestException as e: logger.error(f"TMDB影视搜索请求失败 ({query}): {e}")
        except Exception as e: import traceback; logger.error(f"处理TMDB影视搜索响应时发生未知错误 ({query}): {e}\n{traceback.format_exc()}")
    logger.info(f"TMDB影视搜索 '{query}' (类型: {media_type or 'any'}, 年份: {year or 'any'}) 完成，找到 {len(results)} 个结果。")
    # --- <<< 新增日志：打印详细结果 >>> ---
    if results:
        logger.debug(f"  详细搜索结果 (前3条):")
        for i, res_item in enumerate(results[:3]):
            logger.debug(f"    {i+1}: ID={res_item.get('id')}, Title='{res_item.get('title')}', Year='{res_item.get('year')}', Type='{res_item.get('media_type')}'")
    # --- <<< 新增结束 >>> ---
    return results

def search_tmdb_person_by_name(actor_name: str, api_key: str, actor_original_name: Optional[str] = None) -> List[Dict]:
    if not api_key: logger.error("TMDB API Key未配置，无法进行演员搜索。"); return []
    if not actor_name or not actor_name.strip(): logger.warning("演员搜索查询为空。"); return []
    query = actor_name.strip()
    url = f"{TMDB_API_BASE_URL}/search/person"
    params = {"api_key": api_key, "query": query, "language": "zh-CN", "page": 1, "include_adult": False}
    logger.debug(f"TMDB演员搜索: URL='{url}', Query='{query}'")
    results = []
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        for item in data.get("results", []):
            person_id = item.get("id"); name_from_api = item.get("name"); original_name_from_api = item.get("original_name")
            if not person_id or not name_from_api: continue
            match_score = 0
            if actor_original_name:
                if original_name_from_api and actor_original_name.lower() in original_name_from_api.lower(): match_score += 2
                elif name_from_api and actor_original_name.lower() in name_from_api.lower(): match_score +=1
            known_for_titles = [kf.get("title", kf.get("name")) for kf in item.get("known_for", []) if kf.get("title") or kf.get("name")]
            results.append({
                "id": person_id, "name": name_from_api, "original_name": original_name_from_api or name_from_api,
                "known_for_department": item.get("known_for_department"), "profile_path": item.get("profile_path"),
                "popularity": item.get("popularity", 0.0), "known_for_titles": ", ".join(known_for_titles[:2]),
                "_match_score": match_score
            })
    except requests.exceptions.HTTPError as e: logger.error(f"TMDB演员搜索HTTP错误 ({query}): {e.response.status_code} - {e.response.text[:100]}")
    except requests.exceptions.RequestException as e: logger.error(f"TMDB演员搜索请求失败 ({query}): {e}")
    except Exception as e: import traceback; logger.error(f"处理TMDB演员搜索响应时发生未知错误 ({query}): {e}\n{traceback.format_exc()}")
    logger.info(f"TMDB演员搜索 '{query}' 完成，找到 {len(results)} 个初步结果。")
    return results

def should_translate_text(text: Optional[str]) -> bool:
    """
    判断给定的文本是否需要翻译。
    规则：非空、非空白、不包含中文，并且不是特定的短英文模式。
    """
    if not text or not text.strip():
        return False  # 空或空白，不需要翻译

    if contains_chinese(text): # contains_chinese 函数您已经有了
        return False  # 已包含中文，不需要翻译

    text_stripped = text.strip()

    # 短名规则 (如果这些名字您不想翻译)
    if len(text_stripped) == 1 and 'A' <= text_stripped <= 'Z':
        # logger.debug(f"文本 '{text_stripped}' 为单大写字母，跳过翻译。") # 可以选择在这里或调用处加日志
        return False
    if len(text_stripped) == 2 and text_stripped.isupper() and text_stripped.isalpha():
        # logger.debug(f"文本 '{text_stripped}' 为双大写字母，跳过翻译。")
        return False
    
    # 如果以上条件都不满足，则认为需要翻译
    return True

#翻译格式化
def format_translation_with_original(translated_text: Optional[str], original_text: str) -> str:
    """
    将翻译结果格式化为 "译文（原文）" 的形式。
    如果翻译结果本身已经包含了原文和括号（类似Google的返回），则尽量保持或微调。
    """
    if not translated_text or not translated_text.strip(): # 如果翻译失败或为空
        return original_text # 返回原文

    # 检查 translated_text 是否已经包含了 original_text (可能带括号)
    # 这是一个简单的检查，可能需要更复杂的逻辑来完美处理所有情况
    # 例如，Google 可能返回 "中文译文 (Original Text)" 或 "中文译文（Original Text）"
    original_text_lower = original_text.lower()
    translated_text_lower = translated_text.lower()

    # 简单的判断：如果翻译结果中已经包含了原文（不区分大小写）
    # 并且长度显著大于原文（意味着可能包含了括号和译文），则认为它已经处理好了
    if original_text_lower in translated_text_lower and len(translated_text) > len(original_text) + 2:
        # 可以尝试统一括号为中文括号
        temp_text = translated_text.replace("(", "（").replace(")", "）")
        # 确保原文部分确实被括号包围 (这是一个更强的检查，可选)
        # pattern = re.compile(re.escape(original_text) + r'\s*[（(].*[)）]\s*$', re.IGNORECASE) # 检查译文后是否有括号包围的原文
        # if not pattern.search(temp_text): # 如果不是 "译文 (原文)" 结构
        #    return f"{temp_text}（{original_text}）" # 强制追加
        return temp_text # 假设引擎返回的格式可接受或已微调

    # 否则，我们自己构建 "译文（原文）" 格式
    return f"{translated_text.strip()}（{original_text.strip()}）"


def translate_text_with_translators(
    text_to_translate: str,
    to_language: str = 'zh',
    from_language: str = 'auto',
    engine_order: Optional[List[str]] = None
) -> Optional[str]: # 返回的仍然是最终的字符串，或者 None（如果所有引擎都失败）
    if not text_to_translate or not text_to_translate.strip():
        return None # 或者返回 text_to_translate 本身？取决于需求

    active_engines = engine_order
    if not active_engines: # 后备引擎列表
        active_engines = ["youdao", "sogou", "alibaba", "bing"] # 移除了 google 作为无配置时的后备，因为其特殊性
        print(f"[翻译工具-警告] 未提供翻译引擎顺序，使用默认回退引擎: {', '.join(active_engines)}")
    else:
        print(f"[翻译工具-信息] 使用配置的翻译引擎顺序: {', '.join(active_engines)}")

    raw_translated_text = None # 用于存储未经格式化的原始翻译结果

    for engine_name in active_engines:
        time.sleep(0.5)
        engine_params = {}
        # if engine_name == 'google': # Google的特殊参数已在测试脚本中处理，这里不再默认添加
        #     pass # engine_params['if_use_cn_host'] = True (根据测试结果，代理环境下不需要)

        print(f"[翻译工具-调试] 尝试使用引擎 [{engine_name}] 翻译: '{text_to_translate}'")
        try:
            current_translation = ts.translate_text(
                query_text=text_to_translate,
                translator=engine_name,
                from_language=from_language,
                to_language=to_language,
                timeout=15, # 可以考虑也做成可配置的
                **engine_params
            )
            if current_translation and current_translation.strip() and \
               current_translation.strip().lower() != text_to_translate.strip().lower():
                print(f"[翻译工具-成功] 引擎 [{engine_name}] 成功翻译 '{text_to_translate}' -> '{current_translation.strip()}'")
                raw_translated_text = current_translation.strip() # 保存原始翻译结果
                break # 只要有一个引擎成功，就跳出循环
            else:
                print(f"[翻译工具-警告] 引擎 [{engine_name}] 翻译 '{text_to_translate}' 返回空结果或与原文相同: '{current_translation}'")
        except Exception as e:
            # ... (错误处理和打印日志，保持不变) ...
            print(f"[翻译工具-错误] 引擎 [{engine_name}] 翻译 '{text_to_translate}' 失败: {e}")


    if raw_translated_text:
        # 在这里调用格式化函数
        formatted_translation = format_translation_with_original(raw_translated_text, text_to_translate)
        print(f"[翻译工具-格式化后] 最终结果: '{formatted_translation}'")
        return formatted_translation
    else:
        print(f"[翻译工具-最终失败] 所有尝试的引擎都未能成功翻译: '{text_to_translate}'")
        return None # 或者返回 text_to_translate 如果希望失败时显示原文
# ... (文件末尾)