# emby_handler.py

import requests
import os
import shutil
import json
import time
import utils
import threading
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
def get_item_count(base_url: str, api_key: str, user_id: Optional[str], item_type: str) -> Optional[int]:
    """
    【新】快速获取指定类型的项目总数，不获取项目本身。
    """
    if not all([base_url, api_key, user_id, item_type]):
        logger.error(f"get_item_count: 缺少必要的参数 (需要 user_id)。")
        return None
    
    # Emby API 获取项目列表的端点
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "IncludeItemTypes": item_type,
        "Recursive": "true",
        "Limit": 0 # ★★★ 核心：Limit=0 只返回元数据（包括总数），不返回任何项目，速度极快
    }
    
    logger.debug(f"正在获取 {item_type} 的总数...")
    try:
        response = requests.get(api_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # TotalRecordCount 是 Emby API 返回的总记录数字段
        total_count = data.get("TotalRecordCount")
        if total_count is not None:
            logger.debug(f"成功获取到 {item_type} 总数: {total_count}")
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
            logger.debug(f"响应头部: {response.headers}")
            logger.debug(f"响应内容 (前500字符): {response.text[:500]}")

        response.raise_for_status()
        item_data = response.json()
        logger.debug(
            f"成功获取Emby演员 '{item_data.get('Name', item_id)}' (ID: {item_id}) 的详情。")

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
                logger.debug(f"  发现媒体库: '{item.get('Name')}' (ID: {item.get('Id')}, 类型: {collection_type})")
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
    library_name_map: Optional[Dict[str, str]] = None
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
            params = {
                "api_key": api_key, "Recursive": "true", "ParentId": lib_id,
                "Fields": "Id,Name,Type,ProductionYear,ProviderIds,Path,OriginalTitle,DateCreated,PremiereDate,ChildCount,RecursiveItemCount,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines",
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
                               item_name_for_log: Optional[str] = None
                               ) -> bool:
    if not all([item_emby_id, emby_server_url, emby_api_key]):
        logger.error("刷新Emby元数据参数不足：缺少ItemID、服务器URL或API Key。")
        return False
    
    # 1. 定义一个清晰的日志标识符，优先用片名
    log_identifier = f"'{item_name_for_log}'" if item_name_for_log else f"ItemID: {item_emby_id}"
    
    # 2. 打印一条总的开始日志
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
            # 4. 打印成功的消息，也用缩进
            logger.info(f"  - 刷新请求已成功发送，Emby将在后台处理。")
            return True
        else:
            # 5. 打印失败的消息，用 ERROR 级别并缩进
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
def get_series_children(series_id: str, base_url: str, api_key: str, user_id: str, series_name_for_log: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    【修改】获取指定剧集 (Series) ID 下的所有子项目 (季和集)。
    """
    # ✨ 1. 定义一个日志标识符，优先用片名 ✨
    log_identifier = f"'{series_name_for_log}' (ID: {series_id})" if series_name_for_log else f"ID {series_id}"

    if not all([series_id, base_url, api_key, user_id]):
        logger.error("get_series_children: 参数不足。")
        return None

    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "ParentId": series_id,
        "IncludeItemTypes": "Season,Episode", # ✨ 同时获取季和集 ✨
        "Recursive": "true",
        "Fields": "ProviderIds,Path,OriginalTitle,DateCreated,PremiereDate,ProductionYear,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines,ParentIndexNumber,IndexNumber", # 确保有季号和集号
    }
    
    logger.debug(f"准备获取剧集 {series_id} 的所有子项目 (季和集)...")
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
# ✨✨✨ 通过API解除所有演员关联 ✨✨✨
def clear_all_persons_via_api(base_url: str, api_key: str, user_id: str,
                              update_status_callback: Optional[callable] = None,
                              stop_event: Optional[threading.Event] = None) -> bool:
    """
    【V6 - 终极优雅版】通过API解除所有演员关联，并让Emby自动清理。
    1. 遍历所有电影/剧集，清空其People列表。
    2. 对每个剧集，获取其所有子项目（季/集），并清空它们的People列表。
    3. 最后触发一次全库扫描，Emby的内置维护任务会自动清理掉所有未被引用的演员。
    """
    def _update_status(progress, message):
        if update_status_callback:
            update_status_callback(progress, message)
        if stop_event and stop_event.is_set():
            raise InterruptedError("任务被用户中止")

    logger.warning("将解除所有演员关联，并通知Emby自动清理...")
    
    try:
        _update_status(0, "正在获取所有媒体库...")
        libraries = get_emby_libraries(base_url, api_key, user_id)
        if not libraries:
            logger.warning("未找到任何媒体库，任务完成。")
            _update_status(100, "未找到媒体库")
            return True

        library_ids = [lib['Id'] for lib in libraries]
        
        _update_status(5, "正在获取所有电影和剧集...")
        top_level_items = get_emby_library_items(base_url, api_key, user_id=user_id, library_ids=library_ids, media_type_filter="Movie,Series")
        
        if not top_level_items:
            logger.info("媒体库中没有找到电影或剧集。")
            _update_status(100, "媒体库为空")
            return True

        items_to_process = list(top_level_items)
        
        # --- 动态获取所有分集并加入处理列表 ---
        _update_status(10, "正在获取所有剧集的分集信息...")
        series_items = [item for item in top_level_items if item.get("Type") == "Series"]
        if series_items:
            total_series = len(series_items)
            for i, series in enumerate(series_items):
                _update_status(10 + int((i / total_series) * 20), f"获取分集: {series.get('Name', '')[:20]}...")
                children = get_series_children(series['Id'], base_url, api_key, user_id)
                if children:
                    items_to_process.extend(children)
        
        # --- 统一解除所有项目的关联 ---
        total_items = len(items_to_process)
        logger.info(f"总共需要处理 {total_items} 个媒体项（包括电影、剧集和分集），请耐心等待...")
        _update_status(30, f"开始解除 {total_items} 个媒体项的演员关联...")

        for i, item in enumerate(items_to_process):
            # 将总进度的 30%-100% 分配给这个核心步骤
            _update_status(30 + int((i / total_items) * 70), f"处理中: {item.get('Name', '')[:20]}... ({i+1}/{total_items})")
            
            # 只有当项目详情里确实有演员时，才发送更新请求，减少不必要的API调用
            item_details = get_emby_item_details(item['Id'], base_url, api_key, user_id, fields="People")
            if item_details and item_details.get("People"):
                update_payload = item_details.copy()
                update_payload["People"] = []
                
                update_url = f"{base_url.rstrip('/')}/Items/{item['Id']}"
                params = {"api_key": api_key}
                response = requests.post(update_url, json=update_payload, params=params, timeout=15)
                response.raise_for_status()
                logger.debug(f"已清空项目 '{item.get('Name')}' (ID: {item['Id']}) 的演员关联。")

        logger.info("✅ 所有媒体项的演员关联已全部解除。")
        _update_status(100, "所有演员关联已解除！")
        # 我们不再需要手动删除演员，后续的Emby刷新会自动完成清理
        return True

    except InterruptedError:
        logger.info("演员关联解除任务被用户中止。")
        return False
    except Exception as e:
        logger.error(f"通过【纯API】解除演员关联时发生严重错误: {e}", exc_info=True)
        _update_status(-1, f"错误: 解除关联失败 - {e}")
        return False
# ✨✨✨ 遍历所有媒体库，并对每个库单独触发一次刷新 ✨✨✨
def start_library_scan(base_url: str, api_key: str, user_id: str) -> bool:
    """
    【V4 - 借鉴成功经验版】遍历所有媒体库，并对每个库单独触发一次
    带有精确控制参数的深度刷新。
    """
    if not all([base_url, api_key, user_id]):
        logger.error("start_library_scan: 缺少必要的参数。")
        return False

    try:
        # --- 步骤 1: 获取所有媒体库的列表 ---
        logger.info("正在获取所有媒体库，准备逐个触发深度刷新...")
        libraries = get_emby_libraries(base_url, api_key, user_id)
        if not libraries:
            logger.error("未能获取到任何媒体库，无法触发刷新。")
            return False
        
        logger.info(f"将对以下 {len(libraries)} 个媒体库触发深度刷新: {[lib['Name'] for lib in libraries]}")

        # --- 步骤 2: 遍历每个库，调用带参数的刷新API ---
        all_success = True
        for library in libraries:
            library_id = library.get("Id")
            library_name = library.get("Name")
            if not library_id:
                continue

            # 这就是我们借鉴的、针对单个项目的刷新API，现在用在了媒体库上
            refresh_url = f"{base_url.rstrip('/')}/Items/{library_id}/Refresh"
            
            # ★★★ 使用与你成功的函数完全相同的、强大的刷新参数 ★★★
            params = {
                "api_key": api_key,
                "Recursive": "true", # 确保递归刷新整个库
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "false", # ★★★ 核心：强制替换所有元数据
                "ReplaceAllImages": "false"
            }
            
            logger.info(f"  -> 正在为媒体库 '{library_name}' (ID: {library_id}) 发送深度刷新请求...")
            logger.debug(f"     刷新URL: {refresh_url}")
            logger.debug(f"     刷新参数: {params}")
            
            try:
                response = requests.post(refresh_url, params=params, timeout=30)
                if response.status_code == 204:
                    logger.info(f"  ✅ 成功为媒体库 '{library_name}' 发送刷新请求。")
                else:
                    logger.error(f"  ❌ 为媒体库 '{library_name}' 发送刷新请求失败: HTTP {response.status_code}")
                    all_success = False
            except requests.exceptions.RequestException as e:
                logger.error(f"  ❌ 请求刷新媒体库 '{library_name}' 时发生网络错误: {e}")
                all_success = False
            
            # 在每个库之间稍微延时，避免请求过于密集
            time.sleep(2)

        return all_success

    except Exception as e:
        logger.error(f"在触发Emby全库扫描时发生未知严重错误: {e}", exc_info=True)
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
# ✨✨✨ 获取所有合集及其包含的电影 ✨✨✨
def get_all_collections_with_items(base_url: str, api_key: str, user_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    【新】获取 Emby 中所有的电影合集 (BoxSet)，并包含每个合集内的项目。
    """
    if not all([base_url, api_key, user_id]):
        logger.error("get_all_collections_with_items: 缺少必要的参数。")
        return None

    logger.info("正在从 Emby 获取所有电影合集...")
    
    # 1. 首先，获取所有类型为 BoxSet 的项目
    api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
    params = {
        "api_key": api_key,
        "IncludeItemTypes": "BoxSet",
        "Recursive": "true",
        "Fields": "ProviderIds,Name,ImageTags"
    }
    
    try:
        response = requests.get(api_url, params=params, timeout=20)
        response.raise_for_status()
        collections_data = response.json().get("Items", [])
        logger.info(f"成功获取到 {len(collections_data)} 个合集。")

        # 2. 遍历每个合集，获取其包含的电影
        detailed_collections = []
        for collection in collections_data:
            collection_id = collection.get("Id")
            if not collection_id:
                continue

            logger.debug(f"  正在获取合集 '{collection.get('Name')}' (ID: {collection_id}) 的内容...")
            
            # 获取合集下的所有子项目 (即电影)
            children_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
            children_params = {
                "api_key": api_key,
                "ParentId": collection_id,
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "Fields": "ProviderIds" # 我们只需要 ProviderIds 来做对比
            }
            
            children_response = requests.get(children_url, params=children_params, timeout=20)
            children_response.raise_for_status()
            movies_in_collection = children_response.json().get("Items", [])
            
            # 提取已有电影的 TMDb ID
            existing_movie_tmdb_ids = []
            for movie in movies_in_collection:
                tmdb_id = movie.get("ProviderIds", {}).get("Tmdb")
                if tmdb_id:
                    existing_movie_tmdb_ids.append(tmdb_id)

            collection['ExistingMovieTmdbIds'] = existing_movie_tmdb_ids
            detailed_collections.append(collection)
            
        return detailed_collections

    except requests.exceptions.RequestException as e:
        logger.error(f"获取 Emby 合集时发生网络错误: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"处理 Emby 合集时发生未知错误: {e}", exc_info=True)
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