# emby_handler.py

import requests
import os
import json
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
YOUR_EMBY_USER_ID_FOR_TESTING = "e274948e690043c9a86c9067ead73af4"  # 已用您日志中的ID替换，请确认

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
    logger.debug(
        f"准备获取Emby项目详情 (UserSpecific)：ItemID='{item_id}', UserID='{user_id}', BaseURL='{url}', Params='{params}'")

    try:
        response = requests.get(url, params=params, timeout=15)

        logger.debug(f"实际请求的完整URL: {response.url}")
        logger.debug(f"响应状态码: {response.status_code}")
        if response.status_code != 200:
            logger.debug(f"响应头部: {response.headers}")
            logger.debug(f"响应内容 (前500字符): {response.text[:500]}")

        response.raise_for_status()
        item_data = response.json()
        logger.info(
            f"成功获取Emby项目 '{item_data.get('Name', item_id)}' (ID: {item_id}, User: {user_id}) 的详情。")

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
        logger.info(f"成功获取项目 {item_id} (UserID: {user_id}) 的当前信息用于更新。")
    except requests.exceptions.RequestException as e:
        logger.error(
            f"update_emby_item_cast: 获取Emby项目 {item_id} (UserID: {user_id}) 失败: {e}", exc_info=True)
        return False
    except json.JSONDecodeError as e:
        logger.error(
            f"update_emby_item_cast: 解析Emby项目 {item_id} (UserID: {user_id}) 响应失败: {e}", exc_info=True)
        return False

    if not item_to_update:  # 如果获取失败
        logger.error(f"update_emby_item_cast: 未能获取到项目 {item_id} 的当前信息，更新中止。")
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
                f"update_emby_item_cast: 项目 {item_id} 的 Cast 字段之前是锁定的，将尝试在本次更新中临时移除锁定（如果Emby API允许）。")
        current_locked_fields = set(item_to_update.get("LockedFields", []))
        if "Cast" in current_locked_fields:
            current_locked_fields.remove("Cast")
            item_to_update["LockedFields"] = list(current_locked_fields)
            logger.debug(
                f"项目 {item_id} 的 LockedFields 更新为 (移除了Cast): {item_to_update['LockedFields']}")
    # 步骤3: POST 更新项目信息
    # 更新通常用不带 UserID 的端点
    update_url = f"{emby_server_url.rstrip('/')}/Items/{item_id}"
    headers = {'Content-Type': 'application/json'}
    params_post = {"api_key": emby_api_key}

    logger.debug(f"准备POST更新Emby项目 {item_id} 的演员信息。URL: {update_url}")
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
            logger.info(f"成功更新Emby项目 {item_id} 的演员信息。")
            return True
        else:
            logger.warning(
                f"更新Emby项目 {item_id} 演员信息请求已发送，但状态码为: {response_post.status_code}。响应 (前200字符): {response_post.text[:200]}")
            # 即使不是204，只要没抛异常，也可能意味着Emby接受了请求并在后台处理
            return True
    except requests.exceptions.HTTPError as e:
        response_text = e.response.text[:500] if e.response else "无响应体"
        logger.error(
            f"更新Emby项目 {item_id} 演员信息时发生HTTP错误: {e.response.status_code if e.response else 'N/A'} - {response_text}", exc_info=True)
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"更新Emby项目 {item_id} 演员信息时发生请求错误: {e}", exc_info=True)
        return False
    except Exception as e:  # 捕获其他所有未知异常
        logger.error(f"更新Emby项目 {item_id} 演员信息时发生未知错误: {e}", exc_info=True)
        return False


def get_emby_libraries(base_url: str, api_key: str, user_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    获取 Emby 用户可见的所有顶层媒体库列表。
    返回一个列表，每个元素是一个包含 'Name' 和 'Id' 的字典。
    """
    # API 端点可能因 Emby 版本而异，常见的有 /Library/VirtualFolders 或通过用户视图获取
    # 这是一个示例，你可能需要查找确切的 API 端点
    # 方案一：获取用户的主视图，然后遍历其中的文件夹
    # 另一种可能是直接获取 VirtualFolders，但这可能包含非媒体库的文件夹
    # 我们先尝试获取用户视图下的文件夹，这通常更准确地代表媒体库

    if not user_id:  # 如果没有提供 user_id，可能需要先获取一个管理员用户或默认用户
        logger.warning("get_emby_libraries: 未提供 user_id，可能无法准确获取用户可见的库。")
        # 可以尝试获取系统信息中的第一个本地用户ID作为后备，但这不可靠
        # system_info = get_system_info(base_url, api_key)
        # if system_info and system_info.get("LocalAdminPassword"): # 只是个例子，实际API不同
        #     user_id = ...
        # else:
        #     return None # 或者尝试一个公共的获取库的API（如果存在）

    # 尝试获取用户视图 (User Views)
    # 端点通常是 /Users/{UserId}/Views
    # 或者直接获取 /Library/VirtualFolders，然后用户根据名称判断
    # 我们先用一个更通用的 /Library/VirtualFolders，然后让用户自己识别哪些是他们的主媒体库
    # 因为 /Users/{UserId}/Views 返回的是用户配置的视图，可能不是直接的物理库列表

    api_url = f"{base_url}/Library/VirtualFolders"
    params = {"api_key": api_key}
    if user_id:  # 有些API端点可能接受 UserId 作为过滤条件
        # params["UserId"] = user_id # 取决于具体API是否支持
        pass

    logger.debug(f"get_emby_libraries: Requesting URL: {api_url}")
    try:
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()  # 假设返回的是一个包含 Items 列表的结构

        libraries = []
        # 返回的结构可能直接是文件夹列表，或者在 Items 键下
        items_to_check = data if isinstance(
            data, list) else data.get("Items", [])

        for item in items_to_check:
            # 我们需要判断哪些是真正的媒体库文件夹
            # 通常媒体库有 CollectionType (e.g., "movies", "tvshows", "music")
            # 并且 LocationType 应该是 FileSystem
            # 简单的判断：如果它有 Name 和 Id，并且看起来像个顶层文件夹
            if item.get("Name") and item.get("Id"):
                # 为了更准确，可以检查 item.get("CollectionType") 是否为 movies, tvshows 等
                # 或者检查 item.get("IsFolder") == True 并且它在顶层
                # 这里我们先简单地将所有看起来像文件夹的都列出来，让用户自己选
                # 更好的做法是只列出 CollectionType 为 movies, tvshows, homevideos, music 的
                collection_type = item.get("CollectionType")
                # 常见媒体库类型
                if collection_type in ["movies", "tvshows", "homevideos", "music", "mixed", "boxsets"]:
                    libraries.append({
                        "Name": item.get("Name"),
                        "Id": item.get("Id"),
                        "CollectionType": collection_type
                    })
        logger.info(f"get_emby_libraries: 成功获取到 {len(libraries)} 个可能的媒体库。")
        return libraries
    except requests.exceptions.RequestException as e:
        logger.error(f"get_emby_libraries: 请求 Emby 库列表失败: {e}", exc_info=True)
        return None
    except json.JSONDecodeError as e:
        logger.error(
            f"get_emby_libraries: 解析 Emby 库列表响应失败: {e}", exc_info=True)
        return None


def get_emby_library_items(
    base_url: str,
    api_key: str,
    media_type_filter: Optional[str] = None,  # 例如 "Movie", "Series"
    user_id: Optional[str] = None,
    library_ids: Optional[List[str]] = None  # <--- 新增这个参数
) -> Optional[List[Dict[str, Any]]]:
    """
    获取指定媒体库中特定类型的媒体项目列表。
    :param base_url: Emby 服务器 URL
    :param api_key: Emby API Key
    :param media_type_filter: 要获取的媒体类型 (例如 "Movie", "Series")
    :param user_id: Emby 用户 ID (可选, 但推荐)
    :param library_ids: 一个包含媒体库ID的列表。如果为 None 或空，则不获取任何项目。
    :return: 媒体项目列表，或在失败时返回 None。
    """
    if not base_url or not api_key:
        logger.error("get_emby_library_items: base_url 或 api_key 未提供。")
        return None

    # 如果没有指定 library_ids 或者列表为空，则不处理任何库
    if not library_ids:
        logger.info(
            "get_emby_library_items: 未指定要处理的媒体库ID (library_ids为空或None)，不获取任何项目。")
        return []  # 返回空列表表示没有项目

    all_items_from_selected_libraries: List[Dict[str, Any]] = []

    for lib_id in library_ids:
        if not lib_id or not lib_id.strip():  # 跳过空的库ID
            logger.warning(f"get_emby_library_items: 遇到一个空的库ID，已跳过。")
            continue

        api_url = f"{base_url.rstrip('/')}/Items"
        params: Dict[str, Any] = {  # 明确类型
            "api_key": api_key,
            "Recursive": "true",  # 递归获取子文件夹中的项目
            "ParentId": lib_id,  # <--- 这是核心：按库ID过滤
            "Fields": "Id,Name,Type,ProductionYear,ProviderIds,Path,OriginalTitle,DateCreated,PremiereDate,ChildCount,RecursiveItemCount,Overview,CommunityRating,OfficialRating,Genres,Studios,Taglines",  # 获取更多有用的字段
            # "SortBy": "SortName", # 可以根据需要添加排序
            # "SortOrder": "Ascending",
        }
        if media_type_filter:  # 如果指定了媒体类型，则加入过滤
            params["IncludeItemTypes"] = media_type_filter
        else:  # 否则获取常见的媒体类型
            params["IncludeItemTypes"] = "Movie,Series,Video"

        if user_id:  # 如果提供了用户ID，加入参数以获取该用户可见的项目
            # 对于 /Items 端点，通常是通过 /Users/{UserId}/Items 来获取用户特定视图
            # 或者在请求 /Items 时，某些 Emby 版本可能接受 UserId 参数
            # 更可靠的方式是调整 api_url
            # api_url = f"{base_url.rstrip('/')}/Users/{user_id}/Items"
            # 但 ParentId 应该仍然有效。如果 ParentId 和 UserId 一起用在 /Items 上效果不好，
            # 可能需要先获取库的 UserData (如果适用) 或调整策略。
            # 我们先假设 ParentId 在 /Items 上是主要的过滤方式。
            # 如果要严格按用户视图，可能需要先获取用户视图下的库ID。
            # 为简单起见，我们先这样，如果发现权限问题再调整。
            params["UserId"] = user_id  # 尝试添加 UserId，看是否有效
            logger.debug(
                f"get_emby_library_items: 将使用 UserID '{user_id}' 进行过滤。")

        logger.debug(
            f"get_emby_library_items: Requesting items from library ID '{lib_id}'. URL: {api_url}, Params: {params}")
        try:
            response = requests.get(api_url, params=params, timeout=30)  # 增加超时
            response.raise_for_status()
            data = response.json()
            items_in_lib = data.get("Items", [])
            if items_in_lib:
                logger.info(
                    f"从库 ID '{lib_id}' (类型: {media_type_filter or '所有'}) 获取到 {len(items_in_lib)} 个项目。")
                all_items_from_selected_libraries.extend(items_in_lib)
            else:
                logger.info(
                    f"库 ID '{lib_id}' (类型: {media_type_filter or '所有'}) 中未找到项目。")
        except requests.exceptions.RequestException as e:
            logger.error(f"请求库 '{lib_id}' 中的项目失败: {e}", exc_info=True)
            # 可以选择让整个函数失败返回 None，或者跳过这个库继续处理其他库
            # return None # 如果一个库失败则整体失败
            continue  # 跳过这个失败的库
        except json.JSONDecodeError as e:
            logger.error(
                f"解析库 '{lib_id}' 项目响应失败: {e}. Response: {response.text[:500] if response else 'N/A'}", exc_info=True)
            continue  # 跳过这个失败的库
        except Exception as e:
            logger.error(f"获取库 '{lib_id}' 项目时发生未知错误: {e}", exc_info=True)
            continue

    logger.info(
        f"总共从选定的 {len(library_ids)} 个库中获取到 {len(all_items_from_selected_libraries)} 个项目。")
    return all_items_from_selected_libraries


def refresh_emby_item_metadata(item_emby_id: str,
                               emby_server_url: str,
                               emby_api_key: str,
                               recursive: bool = False,
                               # --- 修改默认参数，尝试“仅刷新缺失” ---
                               metadata_refresh_mode: str = "Default", # 或者 "MissingMetadata" 如果Emby支持
                               image_refresh_mode: str = "Default",   # 或者 "MissingImages"
                               replace_all_metadata_param: bool = False, # <--- 关键：设为 False
                               replace_all_images_param: bool = False    # 通常图片也不替换所有
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
    logger.info(f"{log_message_prefix} 发送刷新请求 (ReplaceAllMetadata: {replace_all_metadata_param}, ImageRefreshMode: {image_refresh_mode})...")

    try:
        response = requests.post(refresh_url, params=params, timeout=30) # 增加超时
        if response.status_code == 204:
            logger.info(f"{log_message_prefix} 刷新请求成功发送。Emby将在后台处理。")
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
        logger.error(
            f"{log_message_prefix} 刷新请求时发生未知错误: {e}\n{traceback.format_exc()}")
        return False

def get_all_persons_from_emby(base_url: str, api_key: str, user_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """获取 Emby 中所有的 Person 条目及其 ProviderIds。"""
    api_url = f"{base_url.rstrip('/')}/Persons" # 通常是这个端点
    params = {
        "api_key": api_key,
        "Fields": "ProviderIds,Name", # 我们需要 ProviderIds 和 Name
        "Recursive": "true", # 通常 Person 是顶层对象，但以防万一
        "IncludeItemTypes": "Person", # 明确只要 Person
        "Limit": 50000 # 获取足够多的数量，或者需要实现分页获取
    }
    if user_id: params["UserId"] = user_id # 根据API是否支持

    all_persons = []
    start_index = 0
    batch_size = 500 # Emby API 可能对返回数量有限制，需要分页

    logger.info("开始从 Emby 获取所有 Person 数据...")
    while True:
        params["StartIndex"] = start_index
        params["Limit"] = batch_size
        logger.debug(f"  获取 Person 批次: StartIndex={start_index}, Limit={batch_size}")
        try:
            response = requests.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            items = data.get("Items", [])
            if not items: break # 没有更多数据了
            all_persons.extend(items)
            if len(items) < batch_size: break # 这是最后一页了
            start_index += len(items)
            time.sleep(0.2) # 批次间稍作停顿，避免请求过快
        except requests.exceptions.RequestException as e:
            logger.error(f"获取 Emby Person 列表失败 (批次 StartIndex={start_index}): {e}", exc_info=True)
            return None # 或者可以返回部分已获取的数据
        except json.JSONDecodeError as e:
            logger.error(f"解析 Emby Person 列表响应失败 (批次 StartIndex={start_index}): {e}", exc_info=True)
            return None
    logger.info(f"成功从 Emby 获取到 {len(all_persons)} 个 Person 条目。")
    return all_persons

# if __name__ == '__main__':
#     TEST_EMBY_SERVER_URL = "http://192.168.31.163:8096"
#     TEST_EMBY_API_KEY = "eaa73b828ac04b1bb6d3687a0117572c"
#     TEST_EMBY_USER_ID = YOUR_EMBY_USER_ID_FOR_TESTING # 使用文件顶部定义的，确保已替换

#     SERIES_ID_TO_TEST = "436062"
#     MOVIE_ID_TO_TEST = "459188"

#     logger.info(f"--- 开始Emby Handler测试 (使用 /Users/UserID/Items/ItemID 端点) ---")

#     placeholders_not_set = False
#     if TEST_EMBY_SERVER_URL == "YOUR_EMBY_SERVER_URL" or not TEST_EMBY_SERVER_URL:
#         logger.error("错误：TEST_EMBY_SERVER_URL 未设置或仍为占位符。")
#         placeholders_not_set = True
#     if TEST_EMBY_API_KEY == "YOUR_EMBY_API_KEY" or not TEST_EMBY_API_KEY:
#         logger.error("错误：TEST_EMBY_API_KEY 未设置或仍为占位符。")
#         placeholders_not_set = True
#     if TEST_EMBY_USER_ID == "YOUR_EMBY_USER_ID_REPLACE_ME" or not TEST_EMBY_USER_ID:
#         logger.error("错误：TEST_EMBY_USER_ID 未设置或仍为占位符 (YOUR_EMBY_USER_ID_FOR_TESTING)。请在脚本顶部和此处修改。")
#         placeholders_not_set = True

#     if placeholders_not_set:
#         logger.error("由于一个或多个关键测试参数未正确设置，测试无法继续。请编辑脚本并替换占位符。")
#     else:
#         # --- 测试获取电影详情 (因为 update_emby_item_cast 会用到它) ---
#         logger.info(f"\n--- 首先测试 get_emby_item_details (Movie ID: {MOVIE_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID}) ---")
#         movie_details = get_emby_item_details(MOVIE_ID_TO_TEST, TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
#         if movie_details:
#             logger.info(f"  获取到电影详情 - 标题: {movie_details.get('Name')}, 类型: {movie_details.get('Type')}")
#             logger.info(f"  当前演员数量: {len(movie_details.get('People', []))}")
#             if movie_details.get('People'):
#                 logger.debug(f"    当前前2位演员: {movie_details.get('People')[:2]}")

#             # --- 测试更新演员信息 ---
#             logger.info(f"\n--- 测试 update_emby_item_cast (Movie ID: {MOVIE_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID}) ---")
#             test_cast_for_update = [
#                 {"name": "演员甲PyTest", "character": "角色一PyTest"},
#                 {"name": "演员乙PyTest", "character": "角色二PyTest"}
#             ]
#             # 调用时传入 UserID
#             update_success = update_emby_item_cast(MOVIE_ID_TO_TEST, test_cast_for_update,
#                                                    TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
#             if update_success:
#                 logger.info(f"  电影 {MOVIE_ID_TO_TEST} 演员信息更新请求已发送。请检查Emby。")
#                 time.sleep(3) # 给Emby一点时间处理
#                 logger.info(f"    验证更新结果 (Movie ID: {MOVIE_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID})...")
#                 updated_details_after_post = get_emby_item_details(MOVIE_ID_TO_TEST, TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
#                 if updated_details_after_post:
#                     logger.info(f"      更新后标题: {updated_details_after_post.get('Name')}")
#                     logger.info(f"      更新后演员数量: {len(updated_details_after_post.get('People', []))}")
#                     if updated_details_after_post.get('People'):
#                         logger.debug(f"        更新后演员: {updated_details_after_post.get('People')}") # 打印所有演员以确认
#                 else:
#                     logger.error(f"      未能获取更新后的电影 {MOVIE_ID_TO_TEST} 详情进行验证。")
#             else:
#                 logger.error(f"  电影 {MOVIE_ID_TO_TEST} 演员信息更新失败。")
#         else:
#             logger.error(f"  未能获取电影ID {MOVIE_ID_TO_TEST} (UserID: {TEST_EMBY_USER_ID}) 的详细信息，无法进行更新测试。")

#         time.sleep(1)

#         # --- 测试获取电视剧详情 ---
#         logger.info(f"\n--- 测试 get_emby_item_details (Series ID: {SERIES_ID_TO_TEST}, UserID: {TEST_EMBY_USER_ID}) ---")
#         series_details = get_emby_item_details(SERIES_ID_TO_TEST, TEST_EMBY_SERVER_URL, TEST_EMBY_API_KEY, TEST_EMBY_USER_ID)
#         if series_details:
#             logger.info(f"  获取到电视剧详情 - 标题: {series_details.get('Name')}, 类型: {series_details.get('Type')}")
#             logger.info(f"  TMDb ID: {series_details.get('ProviderIds', {}).get('Tmdb')}")
#             logger.info(f"  演员数量: {len(series_details.get('People', []))}")
#         else:
#             logger.error(f"  未能获取电视剧ID {SERIES_ID_TO_TEST} (UserID: {TEST_EMBY_USER_ID}) 的详细信息。")

#     logger.info("\n--- Emby Handler测试结束 ---")
