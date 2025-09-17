# reverse_proxy.py (最终完美版 V4 - 诊断增强版 - 无水印版)

import logging
import requests
import re
import json
from flask import Flask, request, Response
from urllib.parse import urlparse, urlunparse
import time
import uuid 
from datetime import datetime, timezone
from gevent import spawn
from geventwebsocket.websocket import WebSocket
from websocket import create_connection

from custom_collection_handler import FilterEngine
import config_manager
import db_handler
import extensions
import emby_handler
logger = logging.getLogger(__name__)

# --- 【核心修改】---
# 不再使用字符串前缀，而是定义一个数字转换基数
# 这将把数据库ID (例如 7) 转换为一个唯一的、负数的、看起来像原生ID的数字 (例如 -900007)
MIMICKED_ID_BASE = 900000

def to_mimicked_id(db_id):
    """将数据库内部ID转换为代理使用的外部ID"""
    return str(-(MIMICKED_ID_BASE + db_id))

def from_mimicked_id(mimicked_id):
    """将代理的外部ID转换回数据库内部ID"""
    return -(int(mimicked_id)) - MIMICKED_ID_BASE

def is_mimicked_id(item_id):
    """检查一个ID是否是我们的虚拟ID"""
    try:
        # 我们的ID是负数
        return isinstance(item_id, str) and item_id.startswith('-')
    except:
        return False

# --- 【核心修改】---
# 更新正则表达式以匹配新的数字ID格式（负数）
MIMICKED_ITEMS_RE = re.compile(r'/emby/Users/([^/]+)/Items/(-(\d+))')
MIMICKED_ITEM_DETAILS_RE = re.compile(r'emby/Users/([^/]+)/Items/(-(\d+))$')


def _get_real_emby_url_and_key():
    base_url = config_manager.APP_CONFIG.get("emby_server_url", "").rstrip('/')
    api_key = config_manager.APP_CONFIG.get("emby_api_key", "")
    if not base_url or not api_key:
        raise ValueError("Emby服务器地址或API Key未配置")
    return base_url, api_key

def handle_get_views():
    """
    【V8 - 实时权限隐藏最终版】
    - 终极解决方案：在返回虚拟库列表时，为每个库和当前用户执行一次“快速权限探测”。
    - 调用一个新的、超快速的API检查，判断当前用户是否能在该虚拟库中看到至少一个媒体项。
    - 只有用户有权看到内容时，虚拟库才会在主页显示，完美解决了管理员和受限用户视野不一致的问题。
    """
    real_server_id = extensions.EMBY_SERVER_ID
    if not real_server_id:
        return "Proxy is not ready", 503

    try:
        user_id_match = re.search(r'/emby/Users/([^/]+)/Views', request.path)
        if not user_id_match:
            return "Could not determine user from request path", 400
        user_id = user_id_match.group(1)

        # 获取用户可见的原生库，这个后续会用到
        user_visible_native_libs = emby_handler.get_emby_libraries(
            config_manager.APP_CONFIG.get("emby_server_url", ""),
            config_manager.APP_CONFIG.get("emby_api_key", ""),
            user_id
        )
        if user_visible_native_libs is None: user_visible_native_libs = []

        collections = db_handler.get_all_active_custom_collections()
        fake_views_items = []
        for coll in collections:
            # 1. 物理检查 (依然保留)
            real_emby_collection_id = coll.get('emby_collection_id')
            if not real_emby_collection_id:
                logger.debug(f"  -> 虚拟库 '{coll['name']}' 被隐藏，原因: 无对应Emby实体")
                continue

            # ★★★ 核心修复：执行实时、动态的权限检查 ★★★
            # a. 从数据库获取这个库包含的所有Emby ID
            db_media_list = coll.get('generated_media_info_json') or []
            ordered_emby_ids = [item.get('emby_id') for item in db_media_list if item.get('emby_id')]

            if not ordered_emby_ids:
                logger.debug(f"  -> 虚拟库 '{coll['name']}' 被隐藏，原因: 库内无项目 (物理)")
                continue

            # b. 调用我们的新式武器进行“快速权限探测”
            user_can_see_content = emby_handler.check_user_has_visible_items_in_id_list(
                user_id=user_id,
                item_ids=ordered_emby_ids,
                base_url=config_manager.APP_CONFIG.get("emby_server_url", ""),
                api_key=config_manager.APP_CONFIG.get("emby_api_key", "")
            )

            if not user_can_see_content:
                logger.debug(f"  -> 虚拟库 '{coll['name']}' 被隐藏，原因: 库内无【用户可见】项目 (权限)")
                continue
            
            # --- 所有检查通过，生成虚拟库 ---
            db_id = coll['id']
            mimicked_id = to_mimicked_id(db_id)
            image_tags = {"Primary": f"{real_emby_collection_id}?timestamp={int(time.time())}"}
            definition = coll.get('definition_json') or {}
            
            merged_libraries = definition.get('merged_libraries', [])
            name_suffix = f" (合并库: {len(merged_libraries)}个)" if merged_libraries else ""
            
            item_type_from_db = definition.get('item_type', 'Movie')
            collection_type = "mixed"
            if not (isinstance(item_type_from_db, list) and len(item_type_from_db) > 1):
                 authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
                 collection_type = "tvshows" if authoritative_type == 'Series' else "movies"

            fake_view = {
                "Name": coll['name'] + name_suffix, "ServerId": real_server_id, "Id": mimicked_id,
                "Guid": str(uuid.uuid4()), "Etag": f"{db_id}{int(time.time())}",
                "DateCreated": "2025-01-01T00:00:00.0000000Z", "CanDelete": False, "CanDownload": False,
                "SortName": coll['name'], "ExternalUrls": [], "ProviderIds": {}, "IsFolder": True,
                "ParentId": "2", "Type": "CollectionFolder", "PresentationUniqueKey": str(uuid.uuid4()),
                "DisplayPreferencesId": f"custom-{db_id}", "ForcedSortName": coll['name'],
                "Taglines": [], "RemoteTrailers": [],
                "UserData": {"PlaybackPositionTicks": 0, "IsFavorite": False, "Played": False},
                "ChildCount": len(ordered_emby_ids), # 使用更准确的计数
                "PrimaryImageAspectRatio": 1.7777777777777777, 
                "CollectionType": collection_type, "ImageTags": image_tags, "BackdropImageTags": [], 
                "LockedFields": [], "LockData": False
            }
            fake_views_items.append(fake_view)
        
        logger.debug(f"已为用户 {user_id} 生成 {len(fake_views_items)} 个可见的虚拟库。")

        # --- 原生库合并逻辑 (保持不变) ---
        native_views_items = []
        should_merge_native = config_manager.APP_CONFIG.get('proxy_merge_native_libraries', True)
        if should_merge_native:
            all_native_views = user_visible_native_libs
            raw_selection = config_manager.APP_CONFIG.get('proxy_native_view_selection', '')
            selected_native_view_ids = [x.strip() for x in raw_selection.split(',') if x.strip()] if isinstance(raw_selection, str) else raw_selection
            if not selected_native_view_ids:
                native_views_items = all_native_views
            else:
                native_views_items = [view for view in all_native_views if view.get("Id") in selected_native_view_ids]
        
        final_items = []
        native_order = config_manager.APP_CONFIG.get('proxy_native_view_order', 'before')
        if native_order == 'after':
            final_items.extend(fake_views_items)
            final_items.extend(native_views_items)
        else:
            final_items.extend(native_views_items)
            final_items.extend(fake_views_items)

        final_response = {"Items": final_items, "TotalRecordCount": len(final_items)}
        return Response(json.dumps(final_response), mimetype='application/json')
        
    except Exception as e:
        logger.error(f"[PROXY] 获取视图数据时出错: {e}", exc_info=True)
        return "Internal Proxy Error", 500

def handle_get_mimicked_library_details(user_id, mimicked_id):
    """
    【V2 - PG JSON 兼容版】
    - 修复了因 psycopg2 自动解析 JSON 字段而导致的 TypeError。
    """
    try:
        real_db_id = from_mimicked_id(mimicked_id)
        coll = db_handler.get_custom_collection_by_id(real_db_id)
        if not coll: return "Not Found", 404

        real_server_id = extensions.EMBY_SERVER_ID
        real_emby_collection_id = coll.get('emby_collection_id')
        image_tags = {"Primary": real_emby_collection_id} if real_emby_collection_id else {}
        
        # ★★★ 核心修复：直接使用已经是字典的 definition_json 字段 ★★★
        definition = coll.get('definition_json') or {}
        item_type_from_db = definition.get('item_type', 'Movie')
        collection_type = "mixed"
        if not (isinstance(item_type_from_db, list) and len(item_type_from_db) > 1):
             authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
             collection_type = "tvshows" if authoritative_type == 'Series' else "movies"

        fake_library_details = {
            "Name": coll['name'], "ServerId": real_server_id, "Id": mimicked_id,
            "Type": "CollectionFolder",
            "CollectionType": collection_type, "IsFolder": True, "ImageTags": image_tags,
        }
        return Response(json.dumps(fake_library_details), mimetype='application/json')
    except Exception as e:
        logger.error(f"获取伪造库详情时出错: {e}", exc_info=True)
        return "Internal Server Error", 500

def handle_get_mimicked_library_image(path):
    try:
        tag_with_timestamp = request.args.get('tag') or request.args.get('Tag')
        if not tag_with_timestamp: return "Bad Request", 400
        real_emby_collection_id = tag_with_timestamp.split('?')[0]
        base_url, _ = _get_real_emby_url_and_key()
        image_url = f"{base_url}/Items/{real_emby_collection_id}/Images/Primary"
        headers = {key: value for key, value in request.headers if key.lower() != 'host'}
        headers['Host'] = urlparse(base_url).netloc
        resp = requests.get(image_url, headers=headers, stream=True, params=request.args)
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for name, value in resp.raw.headers.items() if name.lower() not in excluded_headers]
        return Response(resp.iter_content(chunk_size=8192), resp.status_code, response_headers)
    except Exception as e:
        return "Internal Proxy Error", 500

UNSUPPORTED_METADATA_ENDPOINTS = [
        '/Items/Prefixes', # A-Z 首字母索引
        '/Genres',         # 类型筛选
        '/Studios',        # 工作室筛选
        '/Tags',           # 标签筛选
        '/OfficialRatings',# 官方评级筛选
        '/Years'           # 年份筛选
    ]

# --- ★★★ 核心修复 #1：用下面这个通用的“万能翻译”函数，替换掉旧的 a_prefixes 函数 ★★★ ---
def handle_mimicked_library_metadata_endpoint(path, mimicked_id, params):
    """
    【V3 - URL修正版】
    智能处理所有针对虚拟库的元数据类请求。
    """
    # 检查当前请求的路径是否在我们定义的“不支持列表”中
    if any(path.endswith(endpoint) for endpoint in UNSUPPORTED_METADATA_ENDPOINTS):
        logger.trace(f"检测到对虚拟库的不支持的元数据请求 '{path}'，将直接返回空列表以避免后端错误。")
        # 直接返回一个空的JSON数组，客户端会优雅地处理它（不显示相关筛选器）
        return Response(json.dumps([]), mimetype='application/json')

    try:
        real_db_id = from_mimicked_id(mimicked_id)
        collection_info = db_handler.get_custom_collection_by_id(real_db_id)
        if not collection_info or not collection_info.get('emby_collection_id'):
            return Response(json.dumps([]), mimetype='application/json')

        real_emby_collection_id = collection_info.get('emby_collection_id')
        
        base_url, api_key = _get_real_emby_url_and_key()
        
        # ★★★ 核心修复：在这里加上一个至关重要的斜杠！ ★★★
        target_url = f"{base_url}/{path}"
        
        headers = {k: v for k, v in request.headers if k.lower() not in ['host']}
        headers['Host'] = urlparse(base_url).netloc
        
        new_params = params.copy()
        new_params['ParentId'] = real_emby_collection_id
        new_params['api_key'] = api_key
        
        resp = requests.get(target_url, headers=headers, params=new_params, timeout=15)
        resp.raise_for_status()
        
        return Response(resp.content, resp.status_code, content_type=resp.headers.get('Content-Type'))

    except Exception as e:
        logger.error(f"处理虚拟库元数据请求 '{path}' 时出错: {e}", exc_info=True)
        return Response(json.dumps([]), mimetype='application/json')
    
def handle_get_mimicked_library_items(user_id, mimicked_id, params):
    """
    【V5 - Emby ID 权威数据源 & 排序保持重构版】
    - 直接从数据库 `generated_media_info_json` 读取权威的、有序的 Emby ID 列表。
    - 使用批量接口精确获取媒体项，然后根据数据库中的顺序重新排序。
    - 完美支持 'original' (榜单原始顺序) 排序。
    """
    try:
        real_db_id = from_mimicked_id(mimicked_id)
        collection_info = db_handler.get_custom_collection_by_id(real_db_id)
        if not collection_info:
            return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')

        definition = collection_info.get('definition_json') or {}
        
        # --- 阶段一：从数据库获取权威的、有序的 Emby ID 列表 ---
        logger.trace(f"  -> 阶段1：为虚拟库 '{collection_info['name']}' 从DB读取有序Emby ID列表...")
        db_media_list = collection_info.get('generated_media_info_json') or []
        
        # 提取所有有效的 Emby ID，这个列表的顺序就是我们的“原始榜单顺序”
        ordered_emby_ids = [
            item.get('emby_id') 
            for item in db_media_list 
            if item.get('emby_id')
        ]
        
        if not ordered_emby_ids:
            logger.trace("  -> 数据库中无 Emby ID 记录，返回空列表。")
            return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')
        
        logger.trace(f"  -> 阶段1完成：获取到 {len(ordered_emby_ids)} 个有序的 Emby ID。")

        # --- 阶段二：使用权威 ID 列表，从 Emby 精确获取实时数据 ---
        logger.trace(f"  -> 阶段2：正在从 Emby 批量获取这 {len(ordered_emby_ids)} 个媒体项的实时信息...")
        base_url, api_key = _get_real_emby_url_and_key()
        
        live_items_unordered = emby_handler.get_emby_items_by_id(
            base_url=base_url, api_key=api_key, user_id=user_id,
            item_ids=ordered_emby_ids,
            fields="PrimaryImageAspectRatio,ProviderIds,UserData,Name,ProductionYear,CommunityRating,DateCreated,PremiereDate,Type,RecursiveItemCount,SortName"
        )
        
        # ★★★ 关键点: Emby返回的可能是乱序的，我们必须根据DB中的顺序重新排序 ★★★
        live_items_map = {item['Id']: item for item in live_items_unordered}
        ordered_items = [live_items_map[emby_id] for emby_id in ordered_emby_ids if emby_id in live_items_map]
        
        logger.trace(f"  -> 阶段2完成：成功获取并按原始顺序排序了 {len(ordered_items)} 个实时媒体项。")

        # --- 阶段三：动态筛选 ---
        final_items = ordered_items
        if definition.get('dynamic_filter_enabled'):
            logger.trace("  -> 阶段3：执行实时用户筛选...")
            dynamic_definition = {
                'rules': definition.get('dynamic_rules', []),
                'logic': definition.get('dynamic_logic', 'AND')
            }
            engine = FilterEngine()
            final_items = engine.execute_dynamic_filter(ordered_items, dynamic_definition)
            logger.trace(f"  -> 阶段3完成：筛选后剩下 {len(final_items)} 个媒体项。")
        else:
            logger.trace("  -> 阶段3跳过：未启用实时用户筛选。")

        # --- 阶段四：处理最终排序 ---
        sort_by_field = definition.get('default_sort_by')
        
        # ▼▼▼ 2. 替换整个排序逻辑块 ▼▼▼
        if sort_by_field and sort_by_field not in ['original', 'none']:
            sort_order = definition.get('default_sort_order', 'Ascending')
            is_descending = (sort_order == 'Descending')
            logger.trace(f"执行虚拟库排序劫持: '{sort_by_field}' ({sort_order})")
            
            # ★★★ 新增：处理“最后更新”排序 (类型安全版) ★★★
            if sort_by_field == 'last_synced_at':
                movie_tmdb_ids = []
                series_tmdb_ids = []
                
                # 1. 按类型分离TMDb ID
                for item in final_items:
                    tmdb_id = item.get('ProviderIds', {}).get('Tmdb')
                    if not tmdb_id: continue
                    
                    item_type = item.get('Type')
                    if item_type == 'Movie':
                        movie_tmdb_ids.append(tmdb_id)
                    elif item_type == 'Series':
                        series_tmdb_ids.append(tmdb_id)
                
                logger.trace(f"  -> 分离出 {len(movie_tmdb_ids)} 个电影和 {len(series_tmdb_ids)} 个剧集的TMDb ID用于查询时间戳。")

                timestamp_map = {}
                default_timestamp = datetime.min.replace(tzinfo=timezone.utc)
                
                # 2. 分别查询电影和剧集的时间戳
                if movie_tmdb_ids:
                    movie_metadata = db_handler.get_media_metadata_by_tmdb_ids(movie_tmdb_ids, 'Movie')
                    for meta in movie_metadata:
                        # 1. 优先用 last_synced_at
                        # 2. 如果没有，则用 date_added
                        # 3. 如果连 date_added 都没有，用我们最终的 default_timestamp
                        timestamp = meta.get('last_synced_at') or meta.get('date_added') or default_timestamp
                        timestamp_map[f"{meta['tmdb_id']}-Movie"] = timestamp
                
                if series_tmdb_ids:
                    series_metadata = db_handler.get_media_metadata_by_tmdb_ids(series_tmdb_ids, 'Series')
                    for meta in series_metadata:
                        timestamp = meta.get('last_synced_at') or meta.get('date_added') or default_timestamp
                        timestamp_map[f"{meta['tmdb_id']}-Series"] = timestamp

                # 3. 使用复合键进行安全排序
                final_items.sort(
                    key=lambda item: timestamp_map.get(
                        f"{item.get('ProviderIds', {}).get('Tmdb')}-{item.get('Type')}", 
                        default_timestamp
                    ),
                    reverse=is_descending
                )
            # ★★★ 原有排序逻辑 ★★★
            else:
                default_sort_value = 0 if sort_by_field in ['CommunityRating', 'ProductionYear'] else "0"
                try:
                    final_items.sort(
                        key=lambda item: item.get(sort_by_field, default_sort_value),
                        reverse=is_descending
                    )
                except TypeError:
                    final_items.sort(key=lambda item: item.get('SortName', ''))
        elif sort_by_field == 'original':
             logger.trace("已应用 'original' (榜单原始顺序) 排序。")
        else:
            logger.trace("未设置或禁用虚拟库排序，将保持榜单原始顺序。")
        # ▲▲▲ 替换结束 ▲▲▲

        final_response = {"Items": final_items, "TotalRecordCount": len(final_items)}
        return Response(json.dumps(final_response), mimetype='application/json')

    except Exception as e:
        logger.error(f"处理混合虚拟库时发生严重错误: {e}", exc_info=True)
        return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')

def handle_get_latest_items(user_id, params):
    try:
        base_url, api_key = _get_real_emby_url_and_key()
        virtual_library_id = params.get('ParentId') or params.get('customViewId')

        if virtual_library_id and is_mimicked_id(virtual_library_id): # <-- 【核心修改】
            logger.trace(f"处理针对虚拟库 '{virtual_library_id}' 的最新媒体请求...")
            try:
                virtual_library_db_id = from_mimicked_id(virtual_library_id) # <-- 【核心修改】
            except (ValueError, TypeError):
                return Response(json.dumps([]), mimetype='application/json')

            collection_info = db_handler.get_custom_collection_by_id(virtual_library_db_id)
            if not collection_info or not collection_info.get('emby_collection_id'):
                return Response(json.dumps([]), mimetype='application/json')

            real_emby_collection_id = collection_info.get('emby_collection_id')
            limit_value = params.get('Limit') or params.get('limit') or '20'
            
            latest_params = {
                "ParentId": real_emby_collection_id,
                "Limit": int(limit_value), # 使用我们兼容处理后的值
                "Fields": "PrimaryImageAspectRatio,BasicSyncInfo,DateCreated",
                "SortBy": "DateCreated", 
                "SortOrder": "Descending",
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Series",
                'api_key': api_key,
            }
            target_url = f"{base_url}/emby/Users/{user_id}/Items"
            resp = requests.get(target_url, params=latest_params, timeout=15)
            resp.raise_for_status()
            items_data = resp.json()
            return Response(json.dumps(items_data.get("Items", [])), mimetype='application/json')
        else:
            target_url = f"{base_url}/{request.path.lstrip('/')}"
            forward_headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'accept-encoding']}
            forward_headers['Host'] = urlparse(base_url).netloc
            forward_params = request.args.copy()
            forward_params['api_key'] = api_key
            resp = requests.request(
                method=request.method, url=target_url, headers=forward_headers, params=forward_params,
                data=request.get_data(), stream=True, timeout=30.0
            )
            excluded_resp_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            response_headers = [(name, value) for name, value in resp.raw.headers.items() if name.lower() not in excluded_resp_headers]
            return Response(resp.iter_content(chunk_size=8192), resp.status_code, response_headers)
    except Exception as e:
        logger.error(f"处理最新媒体时发生未知错误: {e}", exc_info=True)
        return Response(json.dumps([]), mimetype='application/json')

proxy_app = Flask(__name__)

@proxy_app.route('/', defaults={'path': ''})
@proxy_app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'])
def proxy_all(path):
    # --- ★★★ 新增：PlaybackInfo 智能劫持逻辑 (最终简化版) ★★★ ---
    # 这个逻辑块专门处理对“虚拟库内真实媒体项”的播放前问询
    if 'PlaybackInfo' in path and '/Items/' in path:
        # 1. 从路径中提取媒体项的ID。根据您的描述，这已经是“真实ID”。
        item_id_match = re.search(r'/Items/(\d+)/PlaybackInfo', path) # 注意：正则表达式改为了 \d+，只匹配纯数字的真实ID
        
        # 我们还需要检查这个请求是否来自一个虚拟库的上下文。
        # Emby客户端在请求PlaybackInfo时，通常会带上ParentId或类似的参数。
        # 但更可靠的方法是检查Referer头，或者依赖Nginx路由。
        # 既然Nginx已经把所有虚拟库的请求都发过来了，我们可以假设在这里处理是安全的。
        
        if item_id_match:
            real_emby_id = item_id_match.group(1)
            logger.info(f"截获到针对真实项目 '{real_emby_id}' 的 PlaybackInfo 请求（可能来自虚拟库上下文）。")
            
            try:
                # 2. 幕后请求：用这个真实ID向Emby索要完整的PlaybackInfo
                base_url, api_key = _get_real_emby_url_and_key()
                user_id_match = re.search(r'/Users/([^/]+)/', path)
                user_id = user_id_match.group(1) if user_id_match else ''
                
                real_playback_info_url = f"{base_url}/Items/{real_emby_id}/PlaybackInfo"
                
                # 转发原始请求的参数和部分头
                forward_params = request.args.copy()
                forward_params['api_key'] = api_key
                forward_params['UserId'] = user_id

                headers = {'Accept': 'application/json'}
                
                logger.debug(f"正在向真实Emby请求PlaybackInfo: {real_playback_info_url} with params {forward_params}")
                resp = requests.get(real_playback_info_url, params=forward_params, headers=headers)
                resp.raise_for_status()
                
                playback_info_data = resp.json()
                
                # 3. 偷梁换柱：修改Path，指向我们的302重定向服务
                if 'MediaSources' in playback_info_data and len(playback_info_data['MediaSources']) > 0:
                    logger.info("成功获取真实PlaybackInfo，正在修改播放路径...")
                    
                    original_path = playback_info_data['MediaSources'][0].get('Path')
                    file_name = original_path.split('/')[-1] if original_path else f"stream.mkv"
                    
                    # ★★★ 核心修改点 ★★★
                    # 将路径指向一个能被Nginx的“直接播放拦截规则”捕获的URL格式。
                    # 这个URL需要包含真实ID，以便302服务知道要为哪个项目获取直链。
                    # 格式: /emby/videos/{real_emby_id}/{filename}?....
                    # 我们的Nginx规则 `~* (?i)(/videos/.*stream|(\.strm|\.mkv...))` 会匹配到这个。
                    playback_info_data['MediaSources'][0]['Path'] = f"/emby/videos/{real_emby_id}/{file_name}"
                    
                    # 强制协议为Http，因为这是Emby内部识别的协议类型
                    playback_info_data['MediaSources'][0]['Protocol'] = 'Http' 
                    
                    # 清理掉可能引起问题的字段，让Emby客户端只认我们给的Path
                    playback_info_data['MediaSources'][0].pop('PathType', None)
                    playback_info_data['MediaSources'][0].pop('SupportsDirectStream', None)
                    playback_info_data['MediaSources'][0].pop('SupportsTranscoding', None)

                    logger.debug(f"修改后的PlaybackInfo: {json.dumps(playback_info_data, indent=2)}")
                    
                    # 4. 返回修改后的完整信息
                    return Response(json.dumps(playback_info_data), mimetype='application/json')
                else:
                    # 如果原始PlaybackInfo没有MediaSources，我们也无能为力，直接转发
                    logger.warning(f"获取到的PlaybackInfo中不包含MediaSources，无法修改路径。")
                    return Response(json.dumps(playback_info_data), mimetype='application/json')

            except Exception as e:
                logger.error(f"处理PlaybackInfo劫持时出错: {e}", exc_info=True)
                # 如果出错，返回一个错误，让客户端走标准流程
                return Response("Proxy error during PlaybackInfo handling.", status=500, mimetype='text/plain')
    # --- 1. WebSocket 代理逻辑 (已添加超详细日志) ---
    if 'Upgrade' in request.headers and request.headers.get('Upgrade', '').lower() == 'websocket':
        logger.info("--- 收到一个新的 WebSocket 连接请求 ---")
        ws_client = request.environ.get('wsgi.websocket')
        if not ws_client:
            logger.error("!!! WebSocket请求，但未找到 wsgi.websocket 对象。请确保以正确的 handler_class 运行。")
            return "WebSocket upgrade failed", 400

        try:
            # 1. 记录客户端信息
            logger.debug(f"  [C->P] 客户端路径: /{path}")
            logger.debug(f"  [C->P] 客户端查询参数: {request.query_string.decode()}")
            logger.debug(f"  [C->P] 客户端 Headers: {dict(request.headers)}")

            # 2. 构造目标 URL
            base_url, _ = _get_real_emby_url_and_key()
            parsed_url = urlparse(base_url)
            ws_scheme = 'wss' if parsed_url.scheme == 'https' else 'ws'
            target_ws_url = urlunparse((ws_scheme, parsed_url.netloc, f'/{path}', '', request.query_string.decode(), ''))
            logger.info(f"  [P->S] 准备连接到目标 Emby WebSocket: {target_ws_url}")

            # 3. 提取 Headers 并尝试连接
            headers_to_server = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'upgrade', 'connection', 'sec-websocket-key', 'sec-websocket-version']}
            logger.debug(f"  [P->S] 转发给服务器的 Headers: {headers_to_server}")
            
            ws_server = None
            try:
                ws_server = create_connection(target_ws_url, header=headers_to_server, timeout=10)
                logger.info("  [P<->S] ✅ 成功连接到远程 Emby WebSocket 服务器。")
            except Exception as e_connect:
                logger.error(f"  [P<->S] ❌ 连接到远程 Emby WebSocket 失败! 错误: {e_connect}", exc_info=True)
                ws_client.close()
                return Response()

            # 4. 创建双向转发协程
            def forward_to_server():
                try:
                    while not ws_client.closed and ws_server.connected:
                        message = ws_client.receive()
                        if message is not None:
                            logger.trace(f"  [C->S] 转发消息: {message[:200] if message else 'None'}") # 只记录前200字符
                            ws_server.send(message)
                        else:
                            logger.info("  [C->P] 客户端连接已关闭 (receive返回None)。")
                            break
                except Exception as e_fwd_s:
                    logger.warning(f"  [C->S] 转发到服务器时出错: {e_fwd_s}")
                finally:
                    if ws_server.connected:
                        ws_server.close()
                        logger.info("  [P->S] 已关闭到服务器的连接。")

            def forward_to_client():
                try:
                    while ws_server.connected and not ws_client.closed:
                        message = ws_server.recv()
                        if message is not None:
                            logger.trace(f"  [S->C] 转发消息: {message[:200] if message else 'None'}") # 只记录前200字符
                            ws_client.send(message)
                        else:
                            logger.info("  [P<-S] 服务器连接已关闭 (recv返回None)。")
                            break
                except Exception as e_fwd_c:
                    logger.warning(f"  [S->C] 转发到客户端时出错: {e_fwd_c}")
                finally:
                    if not ws_client.closed:
                        ws_client.close()
                        logger.info("  [P->C] 已关闭到客户端的连接。")
            
            greenlets = [spawn(forward_to_server), spawn(forward_to_client)]
            from gevent.event import Event
            exit_event = Event()
            def on_exit(g): exit_event.set()
            for g in greenlets: g.link(on_exit)
            
            logger.info("  [P<->S] WebSocket 双向转发已启动。等待连接关闭...")
            exit_event.wait()
            logger.info("--- WebSocket 会话结束 ---")

        except Exception as e:
            logger.error(f"WebSocket 代理主逻辑发生严重错误: {e}", exc_info=True)
        
        return Response()

    # --- 2. HTTP 代理逻辑 (保持不变) ---
    try:
        # 1. 定义所有需要“翻译”ParentId的元数据端点
        METADATA_ENDPOINTS = [
            '/Items/Prefixes', '/Genres', '/Studios', 
            '/Tags', '/OfficialRatings', '/Years'
        ]

        # 2. 优先处理所有元数据请求
        if any(path.endswith(endpoint) for endpoint in METADATA_ENDPOINTS):
            parent_id = request.args.get("ParentId")
            if parent_id and is_mimicked_id(parent_id):
                # 所有这类请求，都交给我们的“万能翻译”函数处理
                return handle_mimicked_library_metadata_endpoint(path, parent_id, request.args)

        # 3. 其次，处理获取库“内容”的请求 (这个逻辑我们之前已经修复好了)
        parent_id = request.args.get("ParentId")
        if parent_id and is_mimicked_id(parent_id):
            user_id_match = re.search(r'/emby/Users/([^/]+)/Items', request.path)
            if user_id_match:
                user_id = user_id_match.group(1)
                return handle_get_mimicked_library_items(user_id, parent_id, request.args)
            else:
                # 这条日志现在只会在极少数未知情况下出现，是我们的最后防线
                logger.warning(f"无法从路径 '{request.path}' 中为虚拟库请求提取user_id。")

        if path.startswith('emby/Items/') and '/Images/' in path and is_mimicked_id(path.split('/')[2]):
             return handle_get_mimicked_library_image(path)

        # 检查是否是请求主页媒体库列表
        if path.endswith('/Views') and path.startswith('emby/Users/'):
            return handle_get_views()

        # 检查是否是请求虚拟库的详情
        details_match = MIMICKED_ITEM_DETAILS_RE.search(f'/{path}')
        if details_match:
            user_id = details_match.group(1)
            mimicked_id = details_match.group(2) # 注意，这里是 group(2)
            return handle_get_mimicked_library_details(user_id, mimicked_id)

        # 检查是否是请求最新项目
        if path.endswith('/Items/Latest'):
            user_id_match = re.search(r'/emby/Users/([^/]+)/', f'/{path}')
            if user_id_match:
                return handle_get_latest_items(user_id_match.group(1), request.args)

        # 捕获所有对虚拟库内容的请求
        items_match = MIMICKED_ITEMS_RE.match(f'/{path}')
        if items_match:
            user_id = items_match.group(1)
            mimicked_id = items_match.group(2) # 注意，这里是 group(2)
            return handle_get_mimicked_library_items(user_id, mimicked_id, request.args)

        # --- 默认转发逻辑 (保持不变) ---
        logger.warning(f"反代服务收到了一个未处理的请求: '{path}'。这通常意味着Nginx配置有误，请检查路由规则。")
        return Response("Path not handled by virtual library proxy.", status=404, mimetype='text/plain')
    except Exception as e:
        logger.error(f"[PROXY] HTTP 代理时发生未知错误: {e}", exc_info=True)
        return "Internal Server Error", 500