# emby_handler.py

import requests
import concurrent.futures
import os
import shutil
import json
import time
import utils
import threading
import config_manager
from typing import Optional, List, Dict, Any, Generator, Tuple, Set
import logging
logger = logging.getLogger(__name__)
# (SimpleLogger 和 logger 的导入保持不变)

class SimpleLogger:
    def info(self, msg): print(f"[EMBY_INFO] {msg}")
    def error(self, msg): print(f"[EMBY_ERROR] {msg}")
    def warning(self, msg): print(f"[EMBY_WARN] {msg}")
    def debug(self, msg): print(f"[EMBY_DEBUG] {msg}")
    def success(self, msg): print(f"[EMBY_SUCCESS] {msg}")
_emby_id_cache = {}
_emby_season_cache = {}
_emby_episode_cache = {}
# ✨✨✨ 快速获取指定类型的项目总数，不获取项目本身 ✨✨✨
def get_item_count(base_url: str, api_key: str, user_id: Optional[str], item_type: str, parent_id: Optional[str] = None) -> Optional[int]:
    """
    【增强版】快速获取指定类型的项目总数。
    新增 parent_id 参数，用于统计特定媒体库或合集内的项目数量。
    """
    if not all([base_url, api_key, user_id, item_type]):
        logger.error(f"get_item_count: 缺少必要的参数 (需要 user_id)。")
        return None
    
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "IncludeItemTypes": item_type,
        "Recursive": "true",
        "Limit": 0 # ★★★ 核心：Limit=0 只返回元数据（包括总数），不返回任何项目，速度极快
    }
    
    # ★★★ 新增的核心逻辑：如果提供了父ID，就加入查询参数 ★★★
    if parent_id:
        params["ParentId"] = parent_id
        logger.debug(f"正在获取父级 {parent_id} 下 {item_type} 的总数...")
    else:
        logger.debug(f"正在获取所有 {item_type} 的总数...")
            
    try:
        response = requests.get(api_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        total_count = data.get("TotalRecordCount")
        if total_count is not None:
            logger.debug(f"成功获取到总数: {total_count}")
            return int(total_count)
        else:
            logger.warning(f"Emby API 响应中未找到 'TotalRecordCount' 字段。")
            return None
            
    except Exception as e:
        logger.error(f"通过 API 获取 {item_type} 总数时失败: {e}")
        return None
# ✨✨✨ 获取Emby项目详情 ✨✨✨
def get_emby_item_details(item_id: str, emby_server_url: str, emby_api_key: str, user_id: str, fields: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not all([item_id, emby_server_url, emby_api_key, user_id]):
        logger.error("获取Emby项目详情参数不足：缺少ItemID、服务器URL、API Key或UserID。")
        return None

    url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items/{item_id}"

    # 2. 动态决定 Fields 参数的值
    if fields:
        fields_to_request = fields
    else:
        fields_to_request = "ProviderIds,People,Path,OriginalTitle,DateCreated,PremiereDate,ProductionYear,ChildCount,RecursiveItemCount,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines"

    params = {
        "api_key": emby_api_key,
        "Fields": fields_to_request
    }
    
    # ✨✨✨ 新增：告诉 Emby 返回的 People 对象里要包含哪些字段 ✨✨✨
    # 这是一个更可靠的方法
    params["PersonFields"] = "ImageTags,ProviderIds"
    
    # --- 函数的其余部分保持不变 ---

    try:
        response = requests.get(url, params=params, timeout=15)

        if response.status_code != 200:
            logger.trace(f"响应头部: {response.headers}")
            logger.trace(f"响应内容 (前500字符): {response.text[:500]}")

        response.raise_for_status()
        item_data = response.json()
        logger.trace(
            f"成功获取Emby项目 '{item_data.get('Name', item_id)}' (ID: {item_id}) 的详情。")

        if not item_data.get('Name') or not item_data.get('Type'):
            logger.warning(f"Emby项目 {item_id} 返回的数据缺少Name或Type字段。")

        return item_data

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(
                f"Emby API未找到项目ID: {item_id} (UserID: {user_id})。URL: {e.request.url}")
        elif e.response.status_code == 401 or e.response.status_code == 403:
            logger.error(
                f"获取Emby项目详情时发生认证/授权错误 (ItemID: {item_id}, UserID: {user_id}): {e.response.status_code} - {e.response.text[:200]}. URL: {e.request.url}. 请检查API Key和UserID权限。")
        else:
            logger.error(
                f"获取Emby项目详情时发生HTTP错误 (ItemID: {item_id}, UserID: {user_id}): {e.response.status_code} - {e.response.text[:200]}. URL: {e.request.url}")
        return None
    except requests.exceptions.RequestException as e:
        url_requested = e.request.url if e.request else url
        logger.error(
            f"获取Emby项目详情时发生请求错误 (ItemID: {item_id}, UserID: {user_id}): {e}. URL: {url_requested}")
        return None
    except Exception as e:
        import traceback
        logger.error(
            f"获取Emby项目详情时发生未知错误 (ItemID: {item_id}, UserID: {user_id}): {e}\n{traceback.format_exc()}")
        return None
# ✨✨✨ 更新一个 Person 条目本身的信息 ✨✨✨
def update_person_details(person_id: str, new_data: Dict[str, Any], emby_server_url: str, emby_api_key: str, user_id: str) -> bool:
    """
    更新一个 Person 条目本身的信息 (例如，只更新名字)。
    使用 /Users/{UserId}/Items/{ItemId} 端点，因为它对所有 Item 类型都更可靠。
    """
    if not all([person_id, new_data, emby_server_url, emby_api_key, user_id]): # <--- 新增 user_id 检查
        logger.error("update_person_details: 参数不足 (需要 user_id)。")
        return False

    # ✨✨✨ 关键修改：使用包含 UserID 的端点 ✨✨✨
    api_url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items/{person_id}"
    params = {"api_key": emby_api_key}
    
    try:
        # 步骤 1: 获取 Person 的当前完整信息
        logger.trace(f"准备获取 Person 详情 (ID: {person_id}, UserID: {user_id}) at {api_url}")
        response_get = requests.get(api_url, params=params, timeout=10)
        response_get.raise_for_status()
        person_to_update = response_get.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"更新Person前获取其详情失败 (ID: {person_id}, UserID: {user_id}): {e}")
        return False

    # 步骤 2: 将新数据合并到获取到的对象中
    for key, value in new_data.items():
        person_to_update[key] = value
    
    # 步骤 3: 使用 POST /Items/{ItemId} (不带UserID) 来更新
    # 更新操作通常是全局的，不针对特定用户
    update_url = f"{emby_server_url.rstrip('/')}/Items/{person_id}"
    headers = {'Content-Type': 'application/json'}

    logger.debug(f"准备更新 Person (ID: {person_id}) 的信息，新数据: {new_data}")
    try:
        response_post = requests.post(update_url, json=person_to_update, headers=headers, params=params, timeout=15)
        response_post.raise_for_status()
        logger.trace(f"成功更新 Person (ID: {person_id}) 的信息。")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"更新 Person (ID: {person_id}) 时发生错误: {e}")
        return False
# ✨✨✨ 更新 Emby 媒体项目的演员列表 ✨✨✨
def update_emby_item_cast(item_id: str, new_cast_list_for_handler: List[Dict[str, Any]],
                          emby_server_url: str, emby_api_key: str, user_id: str) -> bool:
    """
    更新 Emby 媒体项目的演员列表。
    :param item_id: Emby 媒体项目的 ID。
    :param new_cast_list_for_handler: 包含演员信息的列表，每个演员字典期望的键：
                                      "name" (str, 必需),
                                      "character" (str, 角色名, 如果为None则视为空字符串),
                                      "emby_person_id" (str, 可选, 如果是已存在的Emby Person的ID),
                                      "provider_ids" (dict, 可选, 例如 {"Tmdb": "123", "Imdb": "nm456"})
    :param emby_server_url: Emby 服务器 URL。
    :param emby_api_key: Emby API Key。
    :param user_id: Emby 用户 ID (用于获取项目当前信息)。
    :return: True 如果更新成功或被Emby接受，False 如果失败。
    """
    if not all([item_id, emby_server_url, emby_api_key, user_id]):
        logger.error(
            "update_emby_item_cast: 参数不足：缺少ItemID、服务器URL、API Key或UserID。")
        return False
    if new_cast_list_for_handler is None:
        logger.warning(
            f"update_emby_item_cast: new_cast_list_for_handler 为 None，将视为空列表处理，尝试清空演员。")
        new_cast_list_for_handler = []

    # 步骤1: 获取当前项目的完整信息，因为更新时需要整个对象
    current_item_url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items/{item_id}"
    params_get = {"api_key": emby_api_key}
    logger.debug(
        f"update_emby_item_cast: 准备获取项目 {item_id} (UserID: {user_id}) 的当前信息...")

    item_to_update: Optional[Dict[str, Any]] = None
    try:
        response_get = requests.get(
            current_item_url, params=params_get, timeout=15)
        response_get.raise_for_status()
        item_to_update = response_get.json()
        item_name_for_log = item_to_update.get("Name", f"ID:{item_id}")
        logger.debug(f"成功获取项目 {item_name_for_log} (UserID: {user_id}) 的当前信息用于更新。")
    except requests.exceptions.RequestException as e:
        logger.error(
            f"update_emby_item_cast: 获取Emby项目 {item_name_for_log} (UserID: {user_id}) 失败: {e}", exc_info=True)
        return False
    except json.JSONDecodeError as e:
        logger.error(
            f"update_emby_item_cast: 解析Emby项目 {item_name_for_log} (UserID: {user_id}) 响应失败: {e}", exc_info=True)
        return False

    if not item_to_update:  # 如果获取失败
        logger.error(f"update_emby_item_cast: 未能获取到项目 {item_name_for_log} 的当前信息，更新中止。")
        return False

    # 步骤2: 构建新的 People 列表以发送给 Emby
    formatted_people_for_emby: List[Dict[str, Any]] = []
    for actor_entry in new_cast_list_for_handler:
        actor_name = actor_entry.get("name")
        if not actor_name or not str(actor_name).strip():  # 名字是必须的，且不能为空白
            logger.warning(
                f"update_emby_item_cast: 跳过无效的演员条目（缺少或空白name）：{actor_entry}")
            continue

        person_obj: Dict[str, Any] = {
            "Name": str(actor_name).strip(),  # 确保名字是字符串且去除首尾空白
            # 确保 Role 是字符串且去除首尾空白
            "Role": str(actor_entry.get("character", "")).strip(),
            "Type": "Actor"  # 明确指定类型为 Actor
        }

        emby_person_id_from_core = actor_entry.get("emby_person_id")
        provider_ids_from_core = actor_entry.get("provider_ids")

        # 如果有有效的 Emby Person ID
        if emby_person_id_from_core and str(emby_person_id_from_core).strip():
            person_obj["Id"] = str(emby_person_id_from_core).strip()
            # logger.debug(f"  演员 '{person_obj['Name']}': 更新现有 Emby Person ID '{person_obj['Id']}'") # <--- 已注释或删除
            if isinstance(provider_ids_from_core, dict) and provider_ids_from_core:
                sanitized_provider_ids = {k: str(v) for k, v in provider_ids_from_core.items() if v is not None and str(v).strip()}
                if sanitized_provider_ids:
                    person_obj["ProviderIds"] = sanitized_provider_ids
                    logger.debug(f"    尝试为现有演员 '{person_obj['Name']}' (ID: {person_obj['Id']}) 更新/设置 ProviderIds: {person_obj['ProviderIds']}") # 保留这条，但加上ID
        else: # 新增演员
            logger.debug(f"  演员 '{person_obj['Name']}': 作为新演员添加。")
            if isinstance(provider_ids_from_core, dict) and provider_ids_from_core:
                sanitized_provider_ids = {k: str(v) for k, v in provider_ids_from_core.items() if v is not None and str(v).strip()}
                if sanitized_provider_ids:
                    person_obj["ProviderIds"] = sanitized_provider_ids
                    logger.debug(f"    为新演员 '{person_obj['Name']}' 设置 ProviderIds: {person_obj['ProviderIds']}")
            # 对于新增演员，不包含 "Id" 字段，让 Emby 自动生成

        formatted_people_for_emby.append(person_obj)

    # 更新 item_to_update 对象中的 People 字段
    item_to_update["People"] = formatted_people_for_emby

    # 处理 LockedFields
    if "LockedFields" in item_to_update and isinstance(item_to_update["LockedFields"], list):
        if "Cast" in item_to_update["LockedFields"]:
            logger.info(
                f"update_emby_item_cast: 项目 {item_name_for_log} 的 Cast 字段之前是锁定的，将尝试在本次更新中临时移除锁定（如果Emby API允许）。")
        current_locked_fields = set(item_to_update.get("LockedFields", []))
        if "Cast" in current_locked_fields:
            current_locked_fields.remove("Cast")
            item_to_update["LockedFields"] = list(current_locked_fields)
            logger.debug(
                f"项目 {item_name_for_log} 的 LockedFields 更新为 (移除了Cast): {item_to_update['LockedFields']}")
    # 步骤3: POST 更新项目信息
    # 更新通常用不带 UserID 的端点
    update_url = f"{emby_server_url.rstrip('/')}/Items/{item_id}"
    headers = {'Content-Type': 'application/json'}
    params_post = {"api_key": emby_api_key}

    logger.debug(f"准备POST更新Emby项目 {item_name_for_log} 的演员信息。URL: {update_url}")
    if formatted_people_for_emby:
        logger.debug(
            f"  更新数据 (People部分的前2条，共{len(formatted_people_for_emby)}条): {formatted_people_for_emby[:2]}")
    else:
        logger.debug(f"  更新数据 (People部分): 将设置为空列表。")

    try:
        response_post = requests.post(
            update_url, json=item_to_update, headers=headers, params=params_post, timeout=20)
        response_post.raise_for_status()

        if response_post.status_code == 204:  # No Content，表示成功
            logger.debug(f"成功更新Emby项目 {item_name_for_log} 的演员信息。")
            return True
        else:
            logger.warning(
                f"更新Emby项目 {item_name_for_log} 演员信息请求已发送，但状态码为: {response_post.status_code}。响应 (前200字符): {response_post.text[:200]}")
            # 即使不是204，只要没抛异常，也可能意味着Emby接受了请求并在后台处理
            return True
    except requests.exceptions.HTTPError as e:
        response_text = e.response.text[:500] if e.response else "无响应体"
        logger.error(
            f"更新Emby项目 {item_name_for_log} 演员信息时发生HTTP错误: {e.response.status_code if e.response else 'N/A'} - {response_text}", exc_info=True)
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"更新Emby项目 {item_name_for_log} 演员信息时发生请求错误: {e}", exc_info=True)
        return False
    except Exception as e:  # 捕获其他所有未知异常
        logger.error(f"更新Emby项目 {item_name_for_log} 演员信息时发生未知错误: {e}", exc_info=True)
        return False
# ✨✨✨ 获取 Emby 用户可见的所有顶层媒体库列表 ✨✨✨
def get_emby_libraries(base_url: str, api_key: str, user_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    【V2 - 修复版】获取 Emby 用户可见的所有顶层媒体库列表。
    使用 /Users/{UserId}/Views 端点，这通常更准确。
    """
    if not user_id:
        logger.error("get_emby_libraries: 必须提供 user_id 才能准确获取用户可见的媒体库。")
        return None
    if not base_url or not api_key:
        logger.error("get_emby_libraries: 缺少 base_url 或 api_key。")
        return None

    # ★★★ 核心修复：使用更可靠的 /Users/{UserId}/Views API 端点 ★★★
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Views"
    params = {"api_key": api_key}

    logger.trace(f"get_emby_libraries: 正在从 URL 请求用户视图: {api_url}")
    try:
        response = requests.get(api_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        libraries = []
        # 这个端点返回的 Items 就是用户的主屏幕视图
        items_to_check = data.get("Items", [])

        for item in items_to_check:
            # 真正的媒体库通常有 CollectionType 字段
            collection_type = item.get("CollectionType")
            if item.get("Name") and item.get("Id") and collection_type:
                logger.trace(f"  发现媒体库: '{item.get('Name')}' (ID: {item.get('Id')}, 类型: {collection_type})")
                libraries.append({
                    "Name": item.get("Name"),
                    "Id": item.get("Id"),
                    "CollectionType": collection_type
                })
        
        if not libraries:
            logger.warning("未能找到任何有效的媒体库,请检查Emby设置。")
        else:
            logger.debug(f"成功获取到 {len(libraries)} 个媒体库。")
        
        return libraries
        
    except requests.exceptions.RequestException as e:
        logger.error(f"get_emby_libraries: 请求 Emby 用户视图失败: {e}", exc_info=True)
        return None
    except json.JSONDecodeError as e:
        logger.error(f"get_emby_libraries: 解析 Emby 用户视图响应失败: {e}", exc_info=True)
        return None
# ✨✨✨ 获取项目，并为每个项目添加来源库ID ✨✨✨
def get_emby_library_items(
    base_url: str,
    api_key: str,
    media_type_filter: Optional[str] = None,
    user_id: Optional[str] = None,
    library_ids: Optional[List[str]] = None,
    search_term: Optional[str] = None,
    library_name_map: Optional[Dict[str, str]] = None,
    fields: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """
    【V3 - 安静且信息补充版】
    获取项目，并为每个项目添加来源库ID，不再打印每个库的日志。
    """
    if not base_url or not api_key:
        logger.error("get_emby_library_items: base_url 或 api_key 未提供。")
        return None

    # --- 搜索模式 (保持不变) ---
    if search_term and search_term.strip():
        logger.info(f"进入搜索模式，关键词: '{search_term}'")
        api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
        params = {
            "api_key": api_key,
            "SearchTerm": search_term.strip(),
            "IncludeItemTypes": media_type_filter or "Movie,Series",
            "Recursive": "true",
            "Fields": "Id,Name,Type,ProductionYear,ProviderIds,Path",
            "Limit": 100
        }
        try:
            response = requests.get(api_url, params=params, timeout=20)
            response.raise_for_status()
            items = response.json().get("Items", [])
            logger.info(f"搜索到 {len(items)} 个匹配项。")
            return items
        except requests.exceptions.RequestException as e:
            logger.error(f"搜索 Emby 时发生网络错误: {e}")
            return None

    # --- 非搜索模式 ---
    if not library_ids:
        return []

    all_items_from_selected_libraries: List[Dict[str, Any]] = []
    for lib_id in library_ids:
        if not lib_id or not lib_id.strip():
            continue
        
        library_name = library_name_map.get(lib_id, lib_id) if library_name_map else lib_id
        
        try:
            api_url = f"{base_url.rstrip('/')}/Items"
            
            # ★★★ 核心修复：在这里决定 Fields 参数的值 ★★★
            # 如果调用者提供了 fields，就用它；否则，使用我们原来的默认值。
            fields_to_request = fields if fields else "Id,Name,Type,ProductionYear,ProviderIds,Path,OriginalTitle,DateCreated,PremiereDate,ChildCount,RecursiveItemCount,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines,People,ProductionLocations"

            params = {
                "api_key": api_key, "Recursive": "true", "ParentId": lib_id,
                "Fields": fields_to_request, # ★★★ 使用我们新决定的值 ★★★
            }
            if media_type_filter:
                params["IncludeItemTypes"] = media_type_filter
            else:
                params["IncludeItemTypes"] = "Movie,Series,Video"

            if user_id:
                params["UserId"] = user_id

            logger.trace(f"Requesting items from library '{library_name}' (ID: {lib_id}).")
            
            response = requests.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            items_in_lib = response.json().get("Items", [])
            
            if items_in_lib:
                for item in items_in_lib:
                    item['_SourceLibraryId'] = lib_id
                all_items_from_selected_libraries.extend(items_in_lib)
        
        except Exception as e:
            logger.error(f"请求库 '{library_name}' 中的项目失败: {e}", exc_info=True)
            continue

    type_to_chinese = {"Movie": "电影", "Series": "电视剧", "Video": "视频"}
    media_type_in_chinese = ""

    if media_type_filter:
        # 分割字符串，例如 "Movie,Series" -> ["Movie", "Series"]
        types = media_type_filter.split(',')
        # 为每个类型查找翻译，如果找不到就用原名
        translated_types = [type_to_chinese.get(t, t) for t in types]
        # 将翻译后的列表组合成一个字符串，例如 ["电影", "电视剧"] -> "电影、电视剧"
        media_type_in_chinese = "、".join(translated_types)
    else:
        # 如果 media_type_filter 未提供，则为“所有”
        media_type_in_chinese = '所有'

    logger.debug(f"总共从 {len(library_ids)} 个选定库中获取到 {len(all_items_from_selected_libraries)} 个 {media_type_in_chinese} 项目。")
    
    return all_items_from_selected_libraries
# ✨✨✨ 刷新Emby元数据 ✨✨✨
def refresh_emby_item_metadata(item_emby_id: str,
                               emby_server_url: str,
                               emby_api_key: str,
                               recursive: bool = False,
                               metadata_refresh_mode: str = "Default",
                               image_refresh_mode: str = "Default",
                               replace_all_metadata_param: bool = True,
                               replace_all_images_param: bool = False,
                               item_name_for_log: Optional[str] = None,
                               user_id_for_unlock: Optional[str] = None
                               ) -> bool:
    if not all([item_emby_id, emby_server_url, emby_api_key]):
        logger.error("刷新Emby元数据参数不足：缺少ItemID、服务器URL或API Key。")
        return False
    
    log_identifier = f"'{item_name_for_log}'" if item_name_for_log else f"ItemID: {item_emby_id}"
    
    # --- ✨✨✨ 新增：刷新前自动解锁元数据 ✨✨✨ ---
    if replace_all_metadata_param and user_id_for_unlock:
        logger.debug(f"检测到 ReplaceAllMetadata=True，尝试在刷新前解锁项目 {log_identifier} 的元数据...")
        try:
            item_data = get_emby_item_details(item_emby_id, emby_server_url, emby_api_key, user_id_for_unlock)
            
            item_needs_update = False
            if item_data:
                # 1. 检查并解锁全局锁 (LockData)
                if item_data.get("LockData") is True:
                    logger.info(f"  - 项目 {log_identifier} 当前被全局锁定,将尝试解锁...")
                    item_data["LockData"] = False
                    item_needs_update = True

                # 2. 检查并解锁字段锁 (LockedFields)
                if item_data.get("LockedFields"):
                    original_locks = item_data["LockedFields"]
                    logger.info(f"  - 项目 {log_identifier} 当前锁定的字段: {original_locks},将尝试解锁...")
                    item_data["LockedFields"] = []
                    item_needs_update = True
                
                # 3. 如果有任何一种锁被修改，则发送更新请求
                if item_needs_update:
                    update_url = f"{emby_server_url.rstrip('/')}/Items/{item_emby_id}"
                    update_params = {"api_key": emby_api_key}
                    headers = {'Content-Type': 'application/json'}
                    update_response = requests.post(update_url, json=item_data, headers=headers, params=update_params, timeout=15)
                    update_response.raise_for_status()
                    logger.info(f"  - 成功为 {log_identifier} 发送解锁请求。")
                else:
                    logger.debug(f"  - 项目 {log_identifier} 没有任何锁定，无需解锁。")

        except Exception as e:
            logger.warning(f"  - 尝试为 {log_identifier} 解锁元数据时失败: {e}。刷新将继续，但可能受影响。")
    # --- ✨✨✨ 解锁逻辑结束 ✨✨✨ ---

    logger.debug(f"开始为 {log_identifier} 通知Emby刷新...")

    refresh_url = f"{emby_server_url.rstrip('/')}/Items/{item_emby_id}/Refresh"
    params = {
        "api_key": emby_api_key,
        "Recursive": str(recursive).lower(),
        "MetadataRefreshMode": metadata_refresh_mode,
        "ImageRefreshMode": image_refresh_mode,
        "ReplaceAllMetadata": str(replace_all_metadata_param).lower(),
        "ReplaceAllImages": str(replace_all_images_param).lower()
    }
    
    try:
        response = requests.post(refresh_url, params=params, timeout=30)
        if response.status_code == 204:
            logger.info(f"  - 刷新请求已成功发送，Emby将在后台处理。")
            return True
        else:
            logger.error(f"  - 刷新请求失败: HTTP状态码 {response.status_code}")
            try:
                logger.error(f"    - 响应内容: {response.text[:500]}")
            except Exception:
                pass
            return False
    except requests.exceptions.Timeout:
        logger.error(f"  - 刷新请求超时。")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"  - 刷新请求时发生网络错误: {e}")
        return False
    except Exception as e:
        import traceback
        logger.error(f"  - 刷新请求时发生未知错误: {e}\n{traceback.format_exc()}")
        return False
    
# --- 获取媒体项所有演员详情 ---
def enrich_cast_details(
    cast_list: List[Dict[str, Any]],
    emby_server_url: str,
    emby_api_key: str,
    user_id: str
) -> List[Dict[str, Any]]:
    """
    【V1 - 增强模块】
    接收一个可能不完整的演员列表，通过他们的 Emby Person ID，
    批量查询并返回包含完整详情（特别是 ProviderIds）的新列表。
    """
    if not cast_list:
        return []
    if not all([emby_server_url, emby_api_key, user_id]):
        logger.error("enrich_cast_details: 参数不足。")
        return cast_list # 返回原始列表，避免流程中断

    # 1. 提取所有演员的 Emby Person ID
    person_ids = [str(actor.get("Id")) for actor in cast_list if actor.get("Id")]
    if not person_ids:
        logger.warning("enrich_cast_details: 传入的演员列表中没有任何有效的 Emby Person ID，无法增强。")
        return cast_list

    logger.info(f"🔍 开始二次查询，增强 {len(person_ids)} 位演员的详细信息...")

    # 2. 使用 /Users/{UserId}/Items 端点进行批量查询
    # 这个端点接受一个用逗号分隔的 Ids 列表
    url = f"{emby_server_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": emby_api_key,
        "Ids": ",".join(person_ids),
        "Fields": "ProviderIds,Name,Role,Type,PrimaryImageTag" # 请求我们所有需要的字段
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        full_details_list = response.json().get("Items", [])
        
        if not full_details_list:
            logger.warning("二次查询未能返回任何演员的详细信息。")
            return cast_list

        # 3. 构建一个以 ID 为键的完整详情映射表，方便查找
        full_details_map = {str(person.get("Id")): person for person in full_details_list}

        # 4. 遍历原始列表，用完整数据替换，同时保留原始的角色信息
        enriched_cast = []
        for original_actor in cast_list:
            actor_id = str(original_actor.get("Id"))
            full_detail = full_details_map.get(actor_id)
            
            if full_detail:
                # 使用获取到的完整详情作为基础
                new_actor_data = full_detail
                # ★ 关键：将原始的角色信息保留下来，因为批量查询可能不返回角色信息
                if "Role" in original_actor:
                    new_actor_data["Role"] = original_actor["Role"]
                enriched_cast.append(new_actor_data)
            else:
                # 如果某个演员在二次查询中没找到，仍然保留原始信息
                enriched_cast.append(original_actor)
        
        logger.info(f"🔍 演员信息增强完成。")
        return enriched_cast

    except requests.exceptions.RequestException as e:
        logger.error(f"二次查询增强演员详情时发生网络错误: {e}", exc_info=True)
        return cast_list # 失败时返回原始列表

# ✨✨✨ 分批次地从 Emby 获取所有 Person 条目 ✨✨✨
def get_all_persons_from_emby(base_url: str, api_key: str, user_id: Optional[str], stop_event: Optional[threading.Event] = None) -> Generator[List[Dict[str, Any]], None, None]:
    """
    【健壮修复版】分批次地从 Emby 获取所有 Person 条目。
    - 改用更稳定的 /Users/{UserId}/Items endpoint。
    - 移除了不可靠的 `len(items) < batch_size` 判断。
    """
    if not user_id:
        logger.error("获取所有演员需要提供 User ID，但未提供。任务中止。")
        return

    # ★★★ 核心修复 1: 改用更稳定、官方推荐的 Endpoint ★★★
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    
    headers = {
        "X-Emby-Token": api_key,
        "Accept": "application/json",
    }
    
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Person",
        "Fields": "ProviderIds,Name", # 确保字段正确
    }

    start_index = 0
    batch_size = 5000 # 使用更稳定的 endpoint，可以适当调大批次大小，提高效率

    logger.info(f"开始从 Emby 分批次获取所有演员数据 (每批: {batch_size})...")
    
    while True:
        if stop_event and stop_event.is_set():
            logger.info("Emby Person 获取任务被中止。")
            return

        # 将分页参数加入请求
        request_params = params.copy()
        request_params["StartIndex"] = start_index
        request_params["Limit"] = batch_size
        
        logger.debug(f"  获取 Person 批次: StartIndex={start_index}, Limit={batch_size}")
        
        try:
            # 注意：使用 headers 传递 token，而不是作为 URL 参数
            response = requests.get(api_url, headers=headers, params=request_params, timeout=30)
            response.raise_for_status()
            data = response.json()
            items = data.get("Items", [])
            
            # ★★★ 核心修复 2: 只保留这一个最可靠的退出条件 ★★★
            if not items:
                logger.info("API 返回空列表，已获取所有 Person 数据。")
                break # 没有更多数据了，正常结束循环

            # 使用 yield 返回这一批数据
            yield items
            
            # ★★★ 核心修复 3: 移除不可靠的 len(items) < batch_size 判断 ★★★
            # 无论返回多少，都用实际返回的数量来增加索引，这是最安全的方式
            start_index += len(items)
            
            # 稍微延时，避免请求过于频繁
            time.sleep(0.1) 

        except requests.exceptions.RequestException as e:
            logger.error(f"请求 Emby API 失败 (批次 StartIndex={start_index}): {e}", exc_info=True)
            return
        except Exception as e:
            logger.error(f"处理 Emby 响应时发生未知错误 (批次 StartIndex={start_index}): {e}", exc_info=True)
            return
# ✨✨✨ 获取剧集下所有剧集的函数 ✨✨✨
def get_series_children(
    series_id: str,
    base_url: str,
    api_key: str,
    user_id: str,
    series_name_for_log: Optional[str] = None,
    # ★★★★★★★★★★★★★★★ 新增参数 ★★★★★★★★★★★★★★★
    include_item_types: str = "Season,Episode" # 默认同时获取季和集
) -> Optional[List[Dict[str, Any]]]:
    """
    【V2 - 灵活版】获取指定剧集下的子项目，可以指定类型。
    """
    log_identifier = f"'{series_name_for_log}' (ID: {series_id})" if series_name_for_log else f"ID {series_id}"

    if not all([series_id, base_url, api_key, user_id]):
        logger.error("get_series_children: 参数不足。")
        return None

    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "ParentId": series_id,
        # ★★★★★★★★★★★★★★★ 使用新参数 ★★★★★★★★★★★★★★★
        "IncludeItemTypes": include_item_types,
        "Recursive": "true",
        "Fields": "Id,Name,ParentIndexNumber,IndexNumber", # 只请求必要的字段
    }
    
    logger.debug(f"准备获取剧集 {log_identifier} 的子项目 (类型: {include_item_types})...")
    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        children = data.get("Items", [])
        logger.debug(f"成功为剧集 {log_identifier} 获取到 {len(children)} 个子项目。")
        return children
    except requests.exceptions.RequestException as e:
        logger.error(f"获取剧集 {log_identifier} 的子项目列表时发生错误: {e}", exc_info=True)
        return None
# ✨✨✨ 根据子项目ID（如分集或季）获取其所属的剧集（Series）的ID ✨✨✨    
def get_series_id_from_child_id(item_id: str, base_url: str, api_key: str, user_id: Optional[str]) -> Optional[str]:
    """
    【修复版】根据子项目ID（如分集或季）获取其所属的剧集（Series）的ID。

    Args:
        item_id: 子项目的Emby ID。
        base_url: Emby服务器地址。
        api_key: Emby API Key。
        user_id: Emby用户ID。

    Returns:
        如果找到，返回剧集的ID字符串；否则返回None。
    """
    if not all([item_id, base_url, api_key, user_id]):
        logger.error("get_series_id_from_child_id: 缺少必要的参数。")
        return None

    # 1. 先获取子项目本身的详情
    # 注意：这里我们不需要请求 People 等重量级字段，可以简化
    item_details = get_emby_item_details(
        item_id=item_id,
        emby_server_url=base_url,
        emby_api_key=api_key,
        user_id=user_id,
        fields="Type,SeriesId"  # 只请求我们需要的字段，提高效率
    )
    
    if not item_details:
        logger.warning(f"无法获取项目 {item_id} 的详情，无法向上查找剧集ID。")
        return None

    # 2. 检查项目类型
    item_type = item_details.get("Type")
    
    if item_type == "Series":
        # 如果本身就是剧集，直接返回其ID
        logger.info(f"项目 {item_id} 本身就是剧集，直接返回其ID。")
        return item_id
    
    # 3. 核心逻辑：从详情中直接获取 SeriesId
    # 无论是分集(Episode)还是季(Season)，Emby API 返回的详情中通常都直接包含了 SeriesId
    series_id = item_details.get("SeriesId")
    if series_id:
        logger.info(f"项目 {item_id} (类型: {item_type}) 的所属剧集ID为: {series_id}。")
        return str(series_id) # 确保返回的是字符串
    
    # 4. 如果是其他类型，或者详情中没有 SeriesId，记录日志并返回None
    logger.warning(f"项目 {item_id} (类型: {item_type}) 的详情中未找到 'SeriesId' 字段，无法确定所属剧集。")
    return None
# ✨✨✨ 从 Emby 下载指定类型的图片并保存到本地 ✨✨✨
def download_emby_image(
    item_id: str,
    image_type: str,
    save_path: str,
    emby_server_url: str,
    emby_api_key: str,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None
) -> bool:
    """
    从 Emby 下载指定类型的图片并保存到本地。
    """
    if not all([item_id, image_type, save_path, emby_server_url, emby_api_key]):
        logger.error("download_emby_image: 参数不足。")
        return False

    image_url = f"{emby_server_url.rstrip('/')}/Items/{item_id}/Images/{image_type}"
    params = {"api_key": emby_api_key}
    if max_width: params["maxWidth"] = max_width
    if max_height: params["maxHeight"] = max_height

    logger.trace(f"准备下载图片: 类型='{image_type}', 从 URL: {image_url}")
    
    try:
        with requests.get(image_url, params=params, stream=True, timeout=30) as r:
            r.raise_for_status()
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        logger.trace(f"成功下载图片并保存到: {save_path}")
        return True
    except requests.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response is not None and e.response.status_code == 404:
            logger.debug(f"图片类型 '{image_type}' 在 Emby 项目 '{item_id}' 中不存在。")
        else:
            logger.error(f"下载图片时发生网络错误: {e}")
        return False
    except Exception as e:
        logger.error(f"保存图片到 '{save_path}' 时发生未知错误: {e}")
        return False
# --- 定时翻译演员 ---
def prepare_actor_translation_data(
    emby_url: str,
    emby_api_key: str,
    user_id: str,
    ai_translator, # 直接传入已初始化的翻译器实例
    stop_event: threading.Event = None
) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
    """
    【数据准备版】采集、筛选并翻译演员名，然后返回待处理的数据。
    它不再执行写回操作，而是将结果返回给调用者处理。

    :param emby_url: Emby 服务器 URL。
    :param emby_api_key: Emby API Key。
    :param user_id: Emby 用户 ID。
    :param ai_translator: 已初始化的AI翻译器实例。
    :param stop_event: 用于从外部中断任务的线程事件。
    :return: 一个元组，包含两个字典：
             1. translation_map (Dict[str, str]): {'英文名': '中文名', ...}
             2. name_to_persons_map (Dict[str, List[Dict[str, Any]]]): {'英文名': [演员信息字典, ...], ...}
    """
    logger.info("【演员数据准备】开始采集、筛选和翻译...")

    # --- 阶段一：数据采集 ---
    logger.info("【演员数据准备】正在从Emby获取所有演员列表...")
    all_persons = []
    try:
        # 使用现有的、高效的 get_all_persons_from_emby 生成器
        person_generator = get_all_persons_from_emby(
            base_url=emby_url,
            api_key=emby_api_key,
            user_id=user_id,
            stop_event=stop_event
        )
        
        for person_batch in person_generator:
            # 在处理每批次后检查是否需要停止
            if stop_event and stop_event.is_set():
                logger.info("【演员数据准备】在获取演员阶段任务被中止。")
                return {}, {} # 返回空结果

            all_persons.extend(person_batch)

    except Exception as e:
        logger.error(f"【演员数据准备】从Emby获取演员列表时发生错误: {e}", exc_info=True)
        return {}, {} # 发生错误时返回空结果

    # --- 阶段二：数据筛选 ---
    logger.info(f"【演员数据准备】已获取 {len(all_persons)} 位演员，正在筛选需要翻译的名字...")
    names_to_translate: Set[str] = set()
    name_to_persons_map: Dict[str, List[Dict[str, Any]]] = {}
    
    for person in all_persons:
        name = person.get("Name")
        person_id = person.get("Id")
        # 使用 utils.contains_chinese
        if name and person_id and not utils.contains_chinese(name):
            names_to_translate.add(name)
            if name not in name_to_persons_map:
                name_to_persons_map[name] = []
            name_to_persons_map[name].append(person)

    if not names_to_translate:
        logger.info("【演员数据准备】任务完成，没有发现需要翻译的演员名。")
        return {}, {}

    logger.info(f"【演员数据准备】筛选出 {len(names_to_translate)} 个外文名需要翻译。")

    # --- 阶段三：批量翻译 ---
    logger.info(f"【演员数据准备】正在调用AI批量翻译 {len(names_to_translate)} 个名字...")
    translation_map: Dict[str, str] = {}
    try:
        # 调用AI翻译模块
        translation_map = ai_translator.batch_translate(
            texts=list(names_to_translate),
            mode="fast"
        )
        if not translation_map:
            logger.warning("【演员数据准备】翻译引擎未能返回任何有效结果。")
            return {}, name_to_persons_map # 即使翻译失败，也返回映射表，避免上层出错

    except Exception as e:
        logger.error(f"【演员数据准备】批量翻译时发生错误: {e}", exc_info=True)
        return {}, name_to_persons_map # 翻译失败

    logger.info("所有演员名翻译完毕，正在写回Emby数据库...")
    
    # --- 核心修改：返回两个关键的数据结构，而不是执行写回 ---
    return translation_map, name_to_persons_map
# --- 获取所有合集 ---
def get_all_collections_from_emby_generic(base_url: str, api_key: str, user_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    【新增】一个通用的、无过滤的函数，用于获取Emby中所有类型为'BoxSet'的合集。
    这个函数是其他合集处理函数的基础。
    """
    if not all([base_url, api_key, user_id]):
        logger.error("get_all_collections_from_emby_generic: 缺少必要的参数。")
        return None

    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "IncludeItemTypes": "BoxSet",
        "Recursive": "true",
        "Fields": "ProviderIds,Name,ImageTags"
    }
    
    try:
        response = requests.get(api_url, params=params, timeout=60)
        response.raise_for_status()
        all_collections = response.json().get("Items", [])
        logger.debug(f"通用函数成功从 Emby 获取到 {len(all_collections)} 个BoxSet合集。")
        return all_collections
    except Exception as e:
        logger.error(f"通用函数在获取所有Emby合集时发生错误: {e}", exc_info=True)
        return None
# ✨✨✨ 获取所有合集（过滤自建） ✨✨✨
def get_all_collections_with_items(base_url: str, api_key: str, user_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    【V8 - 隔离版】
    只获取 Emby 中拥有 TMDB ID 的“常规”电影合集，
    从而在源头上阻止“自建合集”流入常规合集的处理流程。
    """
    if not all([base_url, api_key, user_id]):
        logger.error("get_all_collections_with_items: 缺少必要的参数。")
        return None

    logger.info("正在从 Emby 获取所有合集...")
    
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "IncludeItemTypes": "BoxSet",
        "Recursive": "true",
        "Fields": "ProviderIds,Name,ImageTags"
    }
    
    try:
        response = requests.get(api_url, params=params, timeout=60)
        response.raise_for_status()
        all_collections_from_emby = response.json().get("Items", [])
        
        # ★★★ 核心修改：在这里设置“门卫”，检查合集是否有TMDB ID ★★★
        regular_collections = []
        for coll in all_collections_from_emby:
            # 只有当 ProviderIds 字典中存在 'Tmdb' 这个键时，才认为是常规合集
            if coll.get("ProviderIds", {}).get("Tmdb"):
                regular_collections.append(coll)
            else:
                logger.debug(f"  - 已跳过自建合集: '{coll.get('Name')}' (ID: {coll.get('Id')})。")

        logger.info(f"成功从 Emby 获取到 {len(regular_collections)} 个合集，准备获取其内容...")

        detailed_collections = []
        
        def _fetch_collection_children(collection):
            collection_id = collection.get("Id")
            if not collection_id: return None
            
            logger.debug(f"  (线程) 正在获取合集 '{collection.get('Name')}' (ID: {collection_id}) 的内容...")
            children_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
            children_params = {
                "api_key": api_key, "ParentId": collection_id,
                "IncludeItemTypes": "Movie",
                "Fields": "ProviderIds"
            }
            try:
                children_response = requests.get(children_url, params=children_params, timeout=60)
                children_response.raise_for_status()
                media_in_collection = children_response.json().get("Items", [])
                
                existing_media_tmdb_ids = [
                    media.get("ProviderIds", {}).get("Tmdb")
                    for media in media_in_collection if media.get("ProviderIds", {}).get("Tmdb")
                ]
                collection['ExistingMovieTmdbIds'] = existing_media_tmdb_ids
                return collection
            except requests.exceptions.RequestException as e:
                logger.error(f"  (线程) 获取合集 '{collection.get('Name')}' 内容时失败: {e}")
                collection['ExistingMovieTmdbIds'] = []
                return collection

        # 使用过滤后的 regular_collections 列表进行后续操作
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_to_collection = {}
            for coll in regular_collections:
                future = executor.submit(_fetch_collection_children, coll)
                future_to_collection[future] = coll
                time.sleep(0.1)

            for future in concurrent.futures.as_completed(future_to_collection):
                result = future.result()
                if result:
                    detailed_collections.append(result)

        logger.info(f"所有合集内容获取完成，共成功处理 {len(detailed_collections)} 个合集。")
        return detailed_collections

    except Exception as e:
        logger.error(f"处理 Emby 电影合集时发生未知错误: {e}", exc_info=True)
        return None

# ✨✨✨ 获取 Emby 服务器信息 (如 Server ID) ✨✨✨
def get_emby_server_info(base_url: str, api_key: str) -> Optional[Dict[str, Any]]:
    """
    【新】获取 Emby 服务器的系统信息，主要用于获取 Server ID。
    """
    if not base_url or not api_key:
        return None
    
    api_url = f"{base_url.rstrip('/')}/System/Info"
    params = {"api_key": api_key}
    
    logger.debug("正在获取 Emby 服务器信息...")
    try:
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        logger.error(f"获取 Emby 服务器信息失败: {e}")
        return None

# --- 根据名称查找一个特定的电影合集 ---
def get_collection_by_name(name: str, base_url: str, api_key: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    【V2 - 修正版】根据名称查找一个特定的电影合集 (BoxSet)，不再过滤自建合集。
    """
    # ★★★ 核心修复：调用新的、不过滤的通用函数 ★★★
    all_collections = get_all_collections_from_emby_generic(base_url, api_key, user_id)
    if all_collections is None:
        return None
    
    for collection in all_collections:
        if collection.get('Name', '').lower() == name.lower():
            logger.debug(f"根据名称 '{name}' 找到了已存在的合集 (ID: {collection.get('Id')})。")
            return collection
    
    logger.debug(f"未找到名为 '{name}' 的合集。")
    return None

def get_collection_members(collection_id: str, base_url: str, api_key: str, user_id: str) -> Optional[List[str]]:
    """获取一个合集内所有媒体项的ID列表。"""
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {'api_key': api_key, 'ParentId': collection_id, 'Fields': 'Id'}
    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        items = response.json().get("Items", [])
        return [item['Id'] for item in items]
    except Exception as e:
        logger.error(f"获取合集 {collection_id} 成员时失败: {e}")
        return None

def add_items_to_collection(collection_id: str, item_ids: List[str], base_url: str, api_key: str) -> bool:
    """【原子操作】只负责向合集添加项目。"""
    if not item_ids: return True
    api_url = f"{base_url.rstrip('/')}/Collections/{collection_id}/Items"
    params = {'api_key': api_key, 'Ids': ",".join(item_ids)}
    try:
        response = requests.post(api_url, params=params, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException:
        return False

def remove_items_from_collection(collection_id: str, item_ids: List[str], base_url: str, api_key: str) -> bool:
    """【原子操作】只负责从合集移除项目。"""
    if not item_ids: return True
    api_url = f"{base_url.rstrip('/')}/Collections/{collection_id}/Items"
    params = {'api_key': api_key, 'Ids': ",".join(item_ids)}
    try:
        # ★★★ 使用 DELETE 方法 ★★★
        response = requests.delete(api_url, params=params, timeout=30)
        response.raise_for_status()
        return True
    except requests.RequestException:
        return False

def empty_collection_in_emby(collection_id: str, base_url: str, api_key: str, user_id: str) -> bool:
    """
    【通过移除所有成员的方式，来间接“清空”并删除一个Emby合集。
    """
    logger.info(f"开始清空 Emby 合集 {collection_id} 的所有成员...")
    
    # 步骤 1: 获取当前所有成员的ID
    member_ids = get_collection_members(collection_id, base_url, api_key, user_id)
    
    if member_ids is None:
        logger.error("  - 无法获取合集成员，清空操作中止。")
        return False # 获取成员失败
        
    if not member_ids:
        logger.info("  - 合集本身已为空，无需清空。")
        return True # 合集已是空的，视为成功

    # 步骤 2: 调用我们已经验证过的 remove_items_from_collection 函数，移除所有成员
    logger.info(f"  - 正在从合集 {collection_id} 中移除 {len(member_ids)} 个成员...")
    success = remove_items_from_collection(collection_id, member_ids, base_url, api_key)
    
    if success:
        logger.info(f"✅ 成功发送清空合集 {collection_id} 的请求。")
    else:
        logger.error(f"❌ 发送清空合集 {collection_id} 的请求失败。")
        
    return success

def create_or_update_collection_with_tmdb_ids(
    collection_name: str, 
    tmdb_ids: list, 
    base_url: str, 
    api_key: str, 
    user_id: str, 
    library_ids: list = None,
    # ★★★ 核心修复 1: 在函数定义中添加 item_types 参数 ★★★
    # 默认为只处理电影，以兼容旧的调用方式
    item_types: list = ['Movie'],
    prefetched_emby_items: Optional[list] = None,
    prefetched_collection_map: Optional[dict] = None
) -> Optional[Tuple[str, List[str]]]: 
    """
    【V2 - 多类型兼容版】
    通过精确计算差异，实现完美的合集同步。
    现在可以处理包含多种媒体类型（如 Movie, Series）的合集。
    """
    # ★★★ 核心修复 2: 根据 item_types 列表生成日志信息 ★★★
    type_map = {'Movie': '电影', 'Series': '电视剧'}
    log_item_types = "、".join([type_map.get(t, t) for t in item_types])
    logger.info(f"开始在Emby中处理名为 '{collection_name}' 的{log_item_types}合集...")
    
    try:
        # 1. & 2. 获取媒体项并计算出“应该有”的成员列表 (desired_emby_ids)
        if prefetched_emby_items is not None:
            all_media_items = prefetched_emby_items
        else:
            if not library_ids: raise ValueError("非预加载模式下必须提供 library_ids。")
            # ★★★ 核心修复 3: media_type_filter 现在使用 item_types 列表 ★★★
            media_type_filter_str = ",".join(item_types)
            all_media_items = get_emby_library_items(
                base_url=base_url, api_key=api_key, user_id=user_id, 
                media_type_filter=media_type_filter_str, 
                library_ids=library_ids
            )
        if all_media_items is None: return None
            
        tmdb_to_emby_id_map = {
            item['ProviderIds']['Tmdb']: item['Id']
            for item in all_media_items
            # ★★★ 核心修复 4: 确保只匹配指定类型的媒体 ★★★
            if item.get('Type') in item_types and 'ProviderIds' in item and 'Tmdb' in item['ProviderIds']
        }
        tmdb_ids_in_library = [str(tid) for tid in tmdb_ids if str(tid) in tmdb_to_emby_id_map]
        desired_emby_ids = [tmdb_to_emby_id_map[tid] for tid in tmdb_ids_in_library]
        
        # 3. 检查合集是否存在
        collection = prefetched_collection_map.get(collection_name.lower()) if prefetched_collection_map is not None else get_collection_by_name(collection_name, base_url, api_key, user_id)
        
        emby_collection_id = None

        if collection:
            # --- 更新逻辑 (保持不变) ---
            emby_collection_id = collection['Id']
            logger.info(f"发现已存在的合集 '{collection_name}' (ID: {emby_collection_id})，开始同步...")
            
            current_emby_ids = get_collection_members(emby_collection_id, base_url, api_key, user_id)
            if current_emby_ids is None:
                raise Exception("无法获取当前合集成员，同步中止。")

            set_current = set(current_emby_ids)
            set_desired = set(desired_emby_ids)
            
            ids_to_remove = list(set_current - set_desired)
            ids_to_add = list(set_desired - set_current)

            if ids_to_remove:
                logger.info(f"  - 发现 {len(ids_to_remove)} 个项目需要移除...")
                remove_items_from_collection(emby_collection_id, ids_to_remove, base_url, api_key)
            
            if ids_to_add:
                logger.info(f"  - 发现 {len(ids_to_add)} 个新项目需要添加...")
                add_items_to_collection(emby_collection_id, ids_to_add, base_url, api_key)

            if not ids_to_remove and not ids_to_add:
                logger.info("  - 完成，合集内容已是最新，无需改动。")

            return (emby_collection_id, tmdb_ids_in_library)
        else:
            # --- 创建逻辑 (保持不变) ---
            logger.info(f"未找到合集 '{collection_name}'，将开始创建...")
            if not desired_emby_ids:
                logger.warning(f"合集 '{collection_name}' 在媒体库中没有任何匹配项，跳过创建。")
                return (None, [])

            api_url = f"{base_url.rstrip('/')}/Collections"
            params = {'api_key': api_key}
            payload = {'Name': collection_name, 'Ids': ",".join(desired_emby_ids)}
            
            response = requests.post(api_url, params=params, data=payload, timeout=30)
            response.raise_for_status()
            new_collection_info = response.json()
            emby_collection_id = new_collection_info.get('Id')
            
            if emby_collection_id:
                return (emby_collection_id, tmdb_ids_in_library)
            return None

    except Exception as e:
        logger.error(f"处理Emby合集 '{collection_name}' 时发生未知错误: {e}", exc_info=True)
        return None
    
# ★★★ 新增：向合集追加单个项目的函数 ★★★
def append_item_to_collection(collection_id: str, item_emby_id: str, base_url: str, api_key: str, user_id: str) -> bool:
    """
    【V2 - 高效修正版】向一个已存在的合集中追加单个媒体项。
    此版本直接调用API添加单个项目，避免了获取和重传整个列表，解决了URL过长的问题。
    :param collection_id: 目标合集的ID。
    :param item_emby_id: 要追加的媒体项的Emby ID。
    :return: True 如果成功，否则 False。
    """
    logger.trace(f"准备将项目 {item_emby_id} 追加到合集 {collection_id}...")
    
    # Emby API的 /Collections/{Id}/Items 端点本身就是追加逻辑
    api_url = f"{base_url.rstrip('/')}/Collections/{collection_id}/Items"
    
    # ★★★ 核心修复：只发送需要添加的单个ID ★★★
    params = {
        'api_key': api_key,
        'Ids': item_emby_id  # 只传递单个ID，URL长度绝对安全
    }
    
    try:
        # 使用POST请求添加
        response = requests.post(api_url, params=params, timeout=20)
        response.raise_for_status()
        
        # Emby成功后通常返回 204 No Content
        logger.trace(f"成功发送追加请求：将项目 {item_emby_id} 添加到合集 {collection_id}。")
        return True
        
    except requests.RequestException as e:
        # 检查是否是因为项目已存在而导致的特定错误（虽然通常Emby会直接返回成功）
        if e.response is not None:
            logger.error(f"向合集 {collection_id} 追加项目 {item_emby_id} 时失败: HTTP {e.response.status_code} - {e.response.text[:200]}")
        else:
            logger.error(f"向合集 {collection_id} 追加项目 {item_emby_id} 时发生网络错误: {e}")
        return False
    except Exception as e:
        logger.error(f"向合集 {collection_id} 追加项目时发生未知错误: {e}", exc_info=True)
        return False
    
# --- 实时获取媒体库路径 ---
def get_all_libraries_with_paths(base_url: str, api_key: str) -> List[Dict[str, Any]]:
    """
    【V16 - 实时获取版】
    使用 /Library/VirtualFolders API 实时获取所有媒体库及其源文件夹(Locations)。
    返回一个列表，每个元素包含 info 和 paths。
    """
    logger.debug("正在实时获取所有媒体库及其源文件夹路径...")
    try:
        folders_url = f"{base_url.rstrip('/')}/Library/VirtualFolders"
        params = {"api_key": api_key}
        response = requests.get(folders_url, params=params, timeout=20)
        response.raise_for_status()
        virtual_folders_data = response.json()

        libraries_with_paths = []
        for folder in virtual_folders_data:
            if not folder.get("CollectionType"):
                continue

            lib_id = folder.get("ItemId")
            lib_name = folder.get("Name")
            locations = folder.get("Locations", [])

            if lib_id and lib_name and locations:
                libraries_with_paths.append({
                    "info": {
                        "Name": lib_name,
                        "Id": lib_id,
                        "CollectionType": folder.get("CollectionType")
                    },
                    "paths": locations
                })
        
        logger.debug(f"实时获取到 {len(libraries_with_paths)} 个媒体库的路径信息。")
        return libraries_with_paths

    except Exception as e:
        logger.error(f"实时获取媒体库路径时发生错误: {e}", exc_info=True)
        return []

# ✨✨✨ 【V16 - 最终简化版】根据项目ID找到其所属媒体库 ✨✨✨
def get_library_root_for_item(item_id: str, base_url: str, api_key: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    【V16 - 最终简化版】
    此版本依赖实时获取的路径信息进行匹配。
    """
    logger.debug("正在为项目ID {item_id} 定位媒体库...")
    try:
        # 步骤 1: 实时获取所有媒体库和它们的路径
        all_libraries_data = get_all_libraries_with_paths(base_url, api_key)
        if not all_libraries_data:
            logger.error("无法获取任何媒体库的路径信息，定位失败。")
            return None

        # 步骤 2: 获取当前项目的文件路径
        item_details = get_emby_item_details(item_id, base_url, api_key, user_id, fields="Path")
        if not item_details or not item_details.get("Path"):
            logger.error(f"无法获取项目 {item_id} 的文件路径，定位失败。")
            return None
        item_path = item_details["Path"]

        # 步骤 3: 遍历所有库，进行最长路径匹配
        best_match_library = None
        longest_match_length = 0
        for lib_data in all_libraries_data:
            for library_source_path in lib_data["paths"]:
                source_path_with_slash = os.path.join(library_source_path, "")
                if item_path.startswith(source_path_with_slash):
                    if len(source_path_with_slash) > longest_match_length:
                        longest_match_length = len(source_path_with_slash)
                        best_match_library = lib_data["info"]
        
        if best_match_library:
            logger.info(f"  -> 匹配到媒体库 '{best_match_library.get('Name')}'。")
            return best_match_library
        else:
            logger.error(f"项目路径 '{item_path}' 未能匹配任何媒体库的源文件夹。")
            return None

    except Exception as e:
        logger.error(f"定位媒体库时发生未知严重错误: {e}", exc_info=True)
        return None