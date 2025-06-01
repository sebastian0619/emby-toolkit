# emby_handler.py

import requests
import os
import time
from typing import Optional, List, Dict, Any

# (SimpleLogger 和 logger 的导入保持不变)
class SimpleLogger:
    def info(self, msg): print(f"[EMBY_INFO] {msg}")
    def error(self, msg): print(f"[EMBY_ERROR] {msg}")
    def warning(self, msg): print(f"[EMBY_WARN] {msg}")
    def debug(self, msg): print(f"[EMBY_DEBUG] {msg}")
    def success(self, msg): print(f"[EMBY_SUCCESS] {msg}")

try:
    from logger_setup import logger
except ImportError:
    logger = SimpleLogger()
    logger.warning("emby_handler using SimpleLogger as fallback.")

# --- 在文件顶部定义一个用于测试的用户ID占位符 ---
# !!! 请务必在测试前替换为真实的Emby用户ID !!!
YOUR_EMBY_USER_ID_FOR_TESTING = "e274948e690043c9a86c9067ead73af4" # 已用您日志中的ID替换，请确认

_emby_id_cache = {}
_emby_season_cache = {}
_emby_episode_cache = {}


def get_emby_item_details(item_id: str, emby_server_url: str, emby_api_key: str, user_id: str) -> Optional[Dict[str, Any]]:
    if not all([item_id, emby_server_url, emby_api_key, user_id]):
        logger.error("获取Emby项目详情参数不足：缺少ItemID、服务器URL、API Key或UserID。")
        return None

    url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items/{item_id}"
    
    params = {
        "api_key": emby_api_key,
        "Fields": "ProviderIds,People,Path,OriginalTitle,DateCreated,PremiereDate,ProductionYear,ChildCount,RecursiveItemCount,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines"
    }
    logger.debug(f"准备获取Emby项目详情 (UserSpecific)：ItemID='{item_id}', UserID='{user_id}', BaseURL='{url}', Params='{params}'")

    try:
        response = requests.get(url, params=params, timeout=15)
        
        logger.debug(f"实际请求的完整URL: {response.url}") 
        logger.debug(f"响应状态码: {response.status_code}")
        if response.status_code != 200:
            logger.debug(f"响应头部: {response.headers}")
            logger.debug(f"响应内容 (前500字符): {response.text[:500]}")

        response.raise_for_status()
        item_data = response.json()
        logger.success(f"成功获取Emby项目 '{item_data.get('Name', item_id)}' (ID: {item_id}, User: {user_id}) 的详情。")
        
        if not item_data.get('Name') or not item_data.get('Type'):
            logger.warning(f"Emby项目 {item_id} 返回的数据缺少Name或Type字段。")
            
        return item_data

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"Emby API未找到项目ID: {item_id} (UserID: {user_id})。URL: {e.request.url}")
        elif e.response.status_code == 401 or e.response.status_code == 403:
            logger.error(f"获取Emby项目详情时发生认证/授权错误 (ItemID: {item_id}, UserID: {user_id}): {e.response.status_code} - {e.response.text[:200]}. URL: {e.request.url}. 请检查API Key和UserID权限。")
        else:
            logger.error(f"获取Emby项目详情时发生HTTP错误 (ItemID: {item_id}, UserID: {user_id}): {e.response.status_code} - {e.response.text[:200]}. URL: {e.request.url}")
        return None
    except requests.exceptions.RequestException as e:
        url_requested = e.request.url if e.request else url
        logger.error(f"获取Emby项目详情时发生请求错误 (ItemID: {item_id}, UserID: {user_id}): {e}. URL: {url_requested}")
        return None
    except Exception as e:
        import traceback
        logger.error(f"获取Emby项目详情时发生未知错误 (ItemID: {item_id}, UserID: {user_id}): {e}\n{traceback.format_exc()}")
        return None

def update_emby_item_cast(item_id: str, new_cast_list: List[Dict[str, Any]],
                          emby_server_url: str, emby_api_key: str, user_id: str) -> bool: # <--- 增加 user_id 参数
    if not all([item_id, emby_server_url, emby_api_key, user_id]): # <--- 检查 user_id
        logger.error("更新Emby演员信息参数不足：缺少ItemID、服务器URL、API Key或UserID。")
        return False
    if new_cast_list is None:
        logger.warning(f"传递给 update_emby_item_cast 的 new_cast_list 为 None，将尝试清空演员。")
        new_cast_list = []
    
    # 获取当前项目信息，使用带UserID的端点
    current_item_url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items/{item_id}" # <--- 修改URL
    params_get = {
        "api_key": emby_api_key,
        # 可以只请求必要的字段，或者不传Fields获取所有，以确保item_to_update对象完整性
        # "Fields": "People,LockedFields,ProviderIds,Name,Type" 
    }
    logger.debug(f"更新演员前，准备获取Emby项目 {item_id} (UserID: {user_id}) 的当前信息...")
    
    try:
        response_get = requests.get(current_item_url, params=params_get, timeout=15)
        response_get.raise_for_status()
        item_to_update = response_get.json()
        logger.success(f"成功获取项目 {item_id} (UserID: {user_id}) 的当前信息用于更新。")
    except requests.exceptions.RequestException as e:
        logger.error(f"更新演员前获取Emby项目 {item_id} (UserID: {user_id}) 失败: {e}")
        return False

    formatted_people_for_emby = []
    for actor_entry in new_cast_list: # new_cast_list 是我们内部格式，角色键是 "Character"
        if not actor_entry.get("name") or actor_entry.get("character") is None: # 允许角色名为空字符串
            logger.warning(f"跳过无效的演员条目（缺少name）：{actor_entry}")
            continue
        person_obj = {
            "Name": actor_entry.get("name"),
            "Role": actor_entry.get("character"), # <--- 我们内部的 "character" 对应到Emby的 "Role" (角色名)
            "Type": "Actor" 
        }
        # 如果我们有Emby Person ID，并且想要Emby链接到已有的Person条目
        if actor_entry.get("emby_person_id") and isinstance(actor_entry.get("emby_person_id"), str):
             person_obj["Id"] = actor_entry.get("emby_person_id")
        
        formatted_people_for_emby.append(person_obj)

    item_to_update["People"] = formatted_people_for_emby
    
    if "LockedFields" not in item_to_update or not isinstance(item_to_update.get("LockedFields"), list):
        item_to_update["LockedFields"] = []
    if "Cast" not in item_to_update["LockedFields"]:
        item_to_update["LockedFields"].append("Cast")
    logger.debug(f"项目 {item_id} 的 LockedFields 将被设置为: {item_to_update.get('LockedFields')}")

    update_url = f"{emby_server_url.rstrip('/')}/Items/{item_id}"
    headers = {'Content-Type': 'application/json'}
    params_post = {"api_key": emby_api_key}

    logger.debug(f"准备POST更新Emby项目 {item_id} 的演员信息。URL: {update_url}")
    if formatted_people_for_emby:
        logger.debug(f"  更新数据 (People部分的前2条): {formatted_people_for_emby[:2]}")
    else:
        logger.debug(f"  更新数据 (People部分): 将设置为空列表。")

    try:
        response_post = requests.post(update_url, json=item_to_update, headers=headers, params=params_post, timeout=20)
        response_post.raise_for_status() 
        
        if response_post.status_code == 204:
            logger.success(f"成功更新Emby项目 {item_id} 的演员信息。")
            return True
        else:
            logger.warning(f"更新Emby项目 {item_id} 演员信息请求已发送，但状态码为: {response_post.status_code}。响应: {response_post.text[:200]}")
            return True 

    except requests.exceptions.HTTPError as e:
        logger.error(f"更新Emby项目 {item_id} 演员信息时发生HTTP错误: {e.response.status_code} - {e.response.text[:500]}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"更新Emby项目 {item_id} 演员信息时发生请求错误: {e}")
        return False
    except Exception as e:
        import traceback
        logger.error(f"更新Emby项目 {item_id} 演员信息时发生未知错误: {e}\n{traceback.format_exc()}")
        return False

def get_emby_library_items(emby_server_url: str, emby_api_key: str,
                           media_type_filter: Optional[str] = None, 
                           parent_id: Optional[str] = None, 
                           start_index: int = 0,
                           limit: Optional[int] = None, 
                           recursive: bool = True,
                           search_term: Optional[str] = None,
                           user_id: Optional[str] = None
                           ) -> List[Dict[str, Any]]:
    if not all([emby_server_url, emby_api_key]):
        logger.error("获取Emby媒体库项目参数不足：缺少服务器URL或API Key。")
        return []

    if user_id:
        items_url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items"
        logger.debug(f"获取Emby媒体库项目 (UserSpecific, UserID: {user_id})")
    else:
        items_url = f"{emby_server_url.rstrip('/')}/Items"
        logger.debug(f"获取Emby媒体库项目 (SystemLevel)")

    all_items: List[Dict[str, Any]] = []
    current_start_index = start_index
    page_limit = 50 if search_term else 200

    while True:
        params = {
            "api_key": emby_api_key,
            "Recursive": str(recursive).lower(),
            "Fields": "ProviderIds,Path,OriginalTitle,DateCreated,PremiereDate,ProductionYear,Type,Name,Id",
            "StartIndex": current_start_index,
            "Limit": page_limit
        }
        if media_type_filter:
            params["IncludeItemTypes"] = media_type_filter
        if parent_id:
            params["ParentId"] = parent_id
        if search_term:
            params["SearchTerm"] = search_term
        
        log_message = f"请求Emby媒体库项目: URL='{items_url}', StartIndex={current_start_index}, Limit={page_limit}, Type={media_type_filter or 'Any'}, ParentId={parent_id or 'None'}"
        if search_term:
            log_message += f", SearchTerm='{search_term}'"
        logger.debug(log_message)

        try:
            response = requests.get(items_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            current_page_items = data.get("Items", [])
            if not current_page_items:
                if search_term:
                    logger.info(f"Emby名称搜索 '{search_term}' 未找到匹配项目。")
                else:
                    logger.info(f"Emby媒体库分页获取：在StartIndex={current_start_index} 未找到更多项目。")
                break
            
            all_items.extend(current_page_items)
            logger.info(f"Emby媒体库/搜索获取：已获取 {len(current_page_items)} 个项目，总计 {len(all_items)}。")

            if search_term and limit is None: 
                logger.info(f"名称搜索 '{search_term}' 完成，获取到第一页 {len(all_items)} 个结果。")
                break 

            if limit is not None and len(all_items) >= limit:
                logger.info(f"已达到用户指定的获取上限 {limit} 个项目。")
                return all_items[:limit]

            if len(current_page_items) < page_limit:
                logger.info("当前页返回项目数小于页面限制，判定为最后一页。")
                break
            
            current_start_index += len(current_page_items)
            time.sleep(0.1) 

        except requests.exceptions.RequestException as e:
            logger.error(f"获取Emby媒体库项目时发生请求错误: {e}")
            break 
        except Exception as e:
            import traceback
            logger.error(f"获取Emby媒体库项目时发生未知错误: {e}\n{traceback.format_exc()}")
            break
            
    logger.success(f"Emby媒体库/搜索项目获取完成，共获取到 {len(all_items)} 个项目。")
    return all_items

def refresh_emby_item_metadata(item_emby_id: str,
                               emby_server_url: str,
                               emby_api_key: str,
                               recursive: bool = False,
                               metadata_refresh_mode: str = "FullRefresh",
                               image_refresh_mode: str = "Default",
                               replace_all_metadata_param: bool = True,
                               replace_all_images_param: bool = False
                               ) -> bool:
    if not all([item_emby_id, emby_server_url, emby_api_key]):
        logger.error("刷新Emby元数据参数不足：缺少ItemID、服务器URL或API Key。")
        return False

    refresh_url = f"{emby_server_url.rstrip('/')}/Items/{item_emby_id}/Refresh"
    
    params = {
        "api_key": emby_api_key,
        "Recursive": str(recursive).lower(),
        "MetadataRefreshMode": metadata_refresh_mode,
        "ImageRefreshMode": image_refresh_mode,
        "ReplaceAllMetadata": str(replace_all_metadata_param).lower(),
        "ReplaceAllImages": str(replace_all_images_param).lower()
    }
    
    log_message_prefix = f"EMBY_REFRESH_HANDLER (ItemID: {item_emby_id}):"
    logger.debug(f"{log_message_prefix} Preparing to send refresh request. URL: {refresh_url}, Params: {params}")
    logger.info(f"{log_message_prefix} 发送刷新请求...")

    try:
        response = requests.post(refresh_url, params=params, timeout=20)
        if response.status_code == 204:
            logger.success(f"{log_message_prefix} 刷新请求成功发送。Emby将在后台处理。")
            return True
        else:
            logger.error(f"{log_message_prefix} 刷新请求失败: HTTP状态码 {response.status_code}")
            try: logger.error(f"  响应内容: {response.text[:500]}")
            except Exception: pass
            return False
    except requests.exceptions.Timeout:
        logger.error(f"{log_message_prefix} 刷新请求超时。")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"{log_message_prefix} 刷新请求时发生网络错误: {e}")
        return False
    except Exception as e:
        import traceback
        logger.error(f"{log_message_prefix} 刷新请求时发生未知错误: {e}\n{traceback.format_exc()}")
        return False

if __name__ == '__main__':
    TEST_EMBY_SERVER_URL = "http://192.168.31.163:8096"
    TEST_EMBY_API_KEY = "eaa73b828ac04b1bb6d3687a0117572c" 
    TEST_EMBY_USER_ID = YOUR_EMBY_USER_ID_FOR_TESTING # 使用文件顶部定义的，确保已替换
    
    SERIES_ID_TO_TEST = "436062" 
    MOVIE_ID_TO_TEST = "459188"

    logger.info(f"--- 开始Emby Handler测试 (使用 /Users/UserID/Items/ItemID 端点) ---")

    placeholders_not_set = False
    if TEST_EMBY_SERVER_URL == "YOUR_EMBY_SERVER_URL" or not TEST_EMBY_SERVER_URL:
        logger.error("错误：TEST_EMBY_SERVER_URL 未设置或仍为占位符。")
        placeholders_not_set = True
    if TEST_EMBY_API_KEY == "YOUR_EMBY_API_KEY" or not TEST_EMBY_API_KEY:
        logger.error("错误：TEST_EMBY_API_KEY 未设置或仍为占位符。")
        placeholders_not_set = True
    if TEST_EMBY_USER_ID == "YOUR_EMBY_USER_ID_REPLACE_ME" or not TEST_EMBY_USER_ID:
        logger.error("错误：TEST_EMBY_USER_ID 未设置或仍为占位符 (YOUR_EMBY_USER_ID_FOR_TESTING)。请在脚本顶部和此处修改。")
        placeholders_not_set = True
    
    if placeholders_not_set:
        logger.error("由于一个或多个关键测试参数未正确设置，测试无法继续。请编辑脚本并替换占位符。")
    else:
        # --- 测试获取电影详情 (因为 update_emby_item_cast 会用到它) ---
        logger.info(f"\n--- 首先测试 get_emby_item_details (Movie ID: {MOVIE_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID}) ---")
        movie_details = get_emby_item_details(MOVIE_ID_TO_TEST, TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
        if movie_details:
            logger.info(f"  获取到电影详情 - 标题: {movie_details.get('Name')}, 类型: {movie_details.get('Type')}")
            logger.info(f"  当前演员数量: {len(movie_details.get('People', []))}")
            if movie_details.get('People'):
                logger.debug(f"    当前前2位演员: {movie_details.get('People')[:2]}")

            # --- 测试更新演员信息 ---
            logger.info(f"\n--- 测试 update_emby_item_cast (Movie ID: {MOVIE_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID}) ---")
            test_cast_for_update = [
                {"name": "演员甲PyTest", "character": "角色一PyTest"},
                {"name": "演员乙PyTest", "character": "角色二PyTest"}
            ]
            # 调用时传入 UserID
            update_success = update_emby_item_cast(MOVIE_ID_TO_TEST, test_cast_for_update, 
                                                   TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
            if update_success:
                logger.success(f"  电影 {MOVIE_ID_TO_TEST} 演员信息更新请求已发送。请检查Emby。")
                time.sleep(3) # 给Emby一点时间处理
                logger.info(f"    验证更新结果 (Movie ID: {MOVIE_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID})...")
                updated_details_after_post = get_emby_item_details(MOVIE_ID_TO_TEST, TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
                if updated_details_after_post:
                    logger.info(f"      更新后标题: {updated_details_after_post.get('Name')}")
                    logger.info(f"      更新后演员数量: {len(updated_details_after_post.get('People', []))}")
                    if updated_details_after_post.get('People'):
                        logger.debug(f"        更新后演员: {updated_details_after_post.get('People')}") # 打印所有演员以确认
                else:
                    logger.error(f"      未能获取更新后的电影 {MOVIE_ID_TO_TEST} 详情进行验证。")
            else:
                logger.error(f"  电影 {MOVIE_ID_TO_TEST} 演员信息更新失败。")
        else:
            logger.error(f"  未能获取电影ID {MOVIE_ID_TO_TEST} (UserID: {TEST_EMBY_USER_ID}) 的详细信息，无法进行更新测试。")

        time.sleep(1)

        # --- 测试获取电视剧详情 ---
        logger.info(f"\n--- 测试 get_emby_item_details (Series ID: {SERIES_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID}) ---")
        series_details = get_emby_item_details(SERIES_ID_TO_TEST, TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
        if series_details:
            logger.info(f"  获取到电视剧详情 - 标题: {series_details.get('Name')}, 类型: {series_details.get('Type')}")
            logger.info(f"  TMDb ID: {series_details.get('ProviderIds', {}).get('Tmdb')}")
            logger.info(f"  演员数量: {len(series_details.get('People', []))}")
        else:
            logger.error(f"  未能获取电视剧ID {SERIES_ID_TO_TEST} (UserID: {TEST_EMBY_USER_ID}) 的详细信息。")

    logger.info("\n--- Emby Handler测试结束 ---")