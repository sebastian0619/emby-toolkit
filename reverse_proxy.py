# reverse_proxy.py (最终完美版 V4 - 诊断增强版)

import logging
import requests
import re
import json
from flask import Flask, request, Response
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor
import time
from PIL import Image, ImageDraw, ImageFont
import io
import threading  # <-- 新增
from gevent import spawn
from geventwebsocket.websocket import WebSocket
from websocket import create_connection

import config_manager
import db_handler
import extensions

logger = logging.getLogger(__name__)

_MIMICKED_LIBRARY_ID_PREFIX = "custom_coll_"
MIMICKED_ITEMS_RE = re.compile(f'/emby/Users/[^/]+/Items/({_MIMICKED_LIBRARY_ID_PREFIX}\\d+)')
MIMICKED_ITEM_DETAILS_RE = re.compile(f'emby/Users/[^/]+/Items/({_MIMICKED_LIBRARY_ID_PREFIX}\\d+)$')

WATERMARK_MAP = {
    "missing": "缺失",
    "subscribed": "已订阅",
    "not_released": "未上映"
}

def _get_real_emby_url_and_key():
    base_url = config_manager.APP_CONFIG.get("emby_server_url", "").rstrip('/')
    api_key = config_manager.APP_CONFIG.get("emby_api_key", "")
    if not base_url or not api_key:
        raise ValueError("Emby服务器地址或API Key未配置")
    return base_url, api_key

# ★★★ 新增辅助函数：获取原生Emby媒体库 ★★★
def _get_native_emby_views(user_id):
    """
    连接到真实的Emby服务器，获取其原生的媒体库视图。
    """
    try:
        base_url, api_key = _get_real_emby_url_and_key()
        # 这个是获取用户视图（即主页媒体库列表）的真实API端点
        target_url = f"{base_url}/emby/Users/{user_id}/Views"
        params = {'api_key': api_key}
        headers = {'Accept': 'application/json'}
        
        resp = requests.get(target_url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        
        data = resp.json()
        logger.info(f"成功从Emby获取到 {len(data.get('Items', []))} 个原生媒体库视图。")
        return data.get('Items', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"连接真实Emby服务器获取原生媒体库失败: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"处理原生Emby媒体库数据时发生未知错误: {e}", exc_info=True)
        return []

# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★★★ 已重构：handle_get_views 函数，实现原生库与自定义库的合并逻辑 ★★★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
def handle_get_views():
    real_server_id = extensions.EMBY_SERVER_ID
    if not real_server_id:
        return "Proxy is not ready", 503

    try:
        # --- 步骤 1: 获取所有自定义合集（伪造的视图） ---
        collections = db_handler.get_all_active_custom_collections(config_manager.DB_PATH)
        fake_views_items = []
        for coll in collections:
            real_emby_collection_id = coll.get('emby_collection_id')
            image_tags = {}
            if real_emby_collection_id:
                image_tags["Primary"] = f"{real_emby_collection_id}?timestamp={int(time.time())}"

            definition = json.loads(coll.get('definition_json', '{}'))
            merged_libraries = definition.get('merged_libraries', [])
            name_suffix = f" (合并库: {len(merged_libraries)}个)" if merged_libraries else ""
            
            item_type_from_db = definition.get('item_type', 'Movie')
            authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
            collection_type = "tvshows" if authoritative_type == 'Series' else "movies"

            fake_view = {
                "Name": coll['name'] + name_suffix, "ServerId": real_server_id, "Id": f"{_MIMICKED_LIBRARY_ID_PREFIX}{coll['id']}",
                "Type": "View", "CollectionType": collection_type, "IsFolder": True, "ImageTags": image_tags,
                "BackdropImageTags": [], "PrimaryImageAspectRatio": 1.7777777777777777, "DisplayPreferencesId": "movies",
                "LibraryOptions": {
                    "EnablePhotos": False, "EnableRealtimeMonitor": False, "EnableChapterImageExtraction": False,
                    "ExtractChapterImagesDuringLibraryScan": False, "EnableInternetProviders": True, "SaveLocalMetadata": False,
                    "PathInfos": [{"Path": f"//custom-collection/{coll['id']}", "NetworkPath": None}], "TypeOptions": []
                }
            }
            fake_views_items.append(fake_view)
        logger.debug(f"已生成 {len(fake_views_items)} 个自定义合集视图。")

        # --- 步骤 2: 根据配置，获取并处理原生Emby媒体库 ---
        native_views_items = []
        # 读取配置，判断是否要合并原生库
        should_merge_native = config_manager.APP_CONFIG.get('proxy_merge_native_libraries', True)
        if should_merge_native:
            logger.info("配置已启用原生库合并功能，开始获取原生库...")
            # 从请求路径中提取 user_id
            user_id_match = re.search(r'/emby/Users/([^/]+)/Views', request.path)
            if user_id_match:
                user_id = user_id_match.group(1)
                all_native_views = _get_native_emby_views(user_id)

                # 读取要显示哪些原生库的配置，尝试拆分成列表
                raw_selection = config_manager.APP_CONFIG.get('proxy_native_view_selection', '')
                if isinstance(raw_selection, str):
                    selected_native_view_ids = [x.strip() for x in raw_selection.split(',') if x.strip()]
                else:
                    selected_native_view_ids = raw_selection

                if not selected_native_view_ids:
                    # 如果配置为空，则默认显示所有原生库
                    native_views_items = all_native_views
                    logger.info("原生库选择列表为空，将显示所有获取到的原生库。")
                else:
                    # 根据ID字段筛选，这里注意ID前缀可能要和_fake_views一致
                    native_views_items = [view for view in all_native_views if view.get("Id") in selected_native_view_ids]
                    logger.info(f"根据配置筛选出 {len(native_views_items)} 个原生库: {[v.get('Name') for v in native_views_items]}")
            else:
                logger.warning("无法从请求路径中解析出 UserId，无法获取原生媒体库。")
        else:
            logger.info("配置未启用原生库合并功能，将仅显示自定义合集。")

        # --- 步骤 3: 合并自定义库和原生库 ---
        final_items = []
        # 读取排序配置
        native_order = config_manager.APP_CONFIG.get('proxy_native_view_order', 'before')
        
        if native_order == 'after':
            final_items.extend(fake_views_items)
            final_items.extend(native_views_items)
            logger.debug("排序方式'after': 自定义库在前，原生库在后。")
        else: # 默认为 'before'
            final_items.extend(native_views_items)
            final_items.extend(fake_views_items)
            logger.debug("排序方式'before': 原生库在前，自定义库在后。")

        final_response = {"Items": final_items, "TotalRecordCount": len(final_items)}
        return Response(json.dumps(final_response), mimetype='application/json')
        
    except Exception as e:
        logger.error(f"[PROXY] 获取视图数据时出错: {e}", exc_info=True)
        return "Internal Proxy Error", 500

def handle_get_mimicked_library_details(mimicked_id):
    try:
        real_db_id = int(mimicked_id.replace(_MIMICKED_LIBRARY_ID_PREFIX, ""))
        coll = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, real_db_id)
        if not coll: return "Not Found", 404

        real_server_id = extensions.EMBY_SERVER_ID
        real_emby_collection_id = coll.get('emby_collection_id')
        image_tags = {"Primary": real_emby_collection_id} if real_emby_collection_id else {}
        
        definition = json.loads(coll.get('definition_json', '{}'))
        item_type_from_db = definition.get('item_type', 'Movie')
        authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
        collection_type = "tvshows" if authoritative_type == 'Series' else "movies"

        fake_library_details = {
            "Name": coll['name'], "ServerId": real_server_id, "Id": mimicked_id, "Type": "CollectionFolder",
            "CollectionType": collection_type, "IsFolder": True, "ImageTags": image_tags, "BackdropImageTags": [],
            "PrimaryImageAspectRatio": 1.7777777777777777,
        }
        return Response(json.dumps(fake_library_details), mimetype='application/json')
    except Exception as e:
        logger.error(f"获取伪造库详情时出错: {e}", exc_info=True)
        return "Internal Server Error", 500

def handle_get_mimicked_library_image(path):
    try:
        tag_with_timestamp = request.args.get('tag') or request.args.get('Tag')
        if not tag_with_timestamp: return "Bad Request", 400
        
        # 从 tag 中分离出真实的 collection ID
        real_emby_collection_id = tag_with_timestamp.split('?')[0]

        base_url, _ = _get_real_emby_url_and_key()
        image_url = f"{base_url}/Items/{real_emby_collection_id}/Images/Primary"
        
        # 转发时，把原始的查询参数（包括时间戳）都带上
        headers = {key: value for key, value in request.headers if key.lower() != 'host'}
        headers['Host'] = urlparse(base_url).netloc
        resp = requests.get(image_url, headers=headers, stream=True, params=request.args)
        
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for name, value in resp.raw.headers.items() if name.lower() not in excluded_headers]
        return Response(resp.iter_content(chunk_size=8192), resp.status_code, response_headers)
    except Exception as e:
        return "Internal Proxy Error", 500

def handle_get_watermarked_image(tag):
    logger.trace(f"[PROXY] 开始处理盖章水印图片请求, Tag: '{tag}'")
    try:
        parts = tag.split('_', 2)
        if len(parts) != 3:
            logger.error(f"[PROXY] 无效的水印Tag格式: '{tag}'")
            return "Bad Request: Invalid Tag Format", 400

        _, status, image_path_part = parts
        image_path = f"/{image_path_part}"
        
        watermark_text = WATERMARK_MAP.get(status)
        tmdb_image_url = f"https://image.tmdb.org/t/p/w500{image_path}"

        if not watermark_text:
            resp = requests.get(tmdb_image_url, stream=True)
            return Response(resp.iter_content(chunk_size=8192), resp.status_code, content_type=resp.headers['Content-Type'])

        resp = requests.get(tmdb_image_url, stream=True, timeout=20)
        resp.raise_for_status()
        base_image = Image.open(io.BytesIO(resp.content)).convert("RGBA")

        try:
            font = ImageFont.truetype("font.ttf", 65)
        except IOError:
            logger.warning("字体文件 'font.ttf' 未找到，使用默认字体。")
            font = ImageFont.load_default()
        
        text_color = (255, 255, 0, 255); bg_color = (0, 0, 0, 170); border_color = (255, 255, 0, 220)
        padding = 20
        text_bbox = font.getbbox(watermark_text)
        text_width = text_bbox[2] - text_bbox[0]; text_height = text_bbox[3] - text_bbox[1]
        stamp_width = text_width + padding * 2; stamp_height = text_height + padding * 2
        stamp_unrotated = Image.new('RGBA', (stamp_width, stamp_height), (0,0,0,0))
        draw = ImageDraw.Draw(stamp_unrotated)
        draw.rectangle((0, 0, stamp_width, stamp_height), fill=bg_color, outline=border_color, width=4)
        draw.text((padding, padding - text_bbox[1]), watermark_text, font=font, fill=text_color)
        angle = -15
        stamp_rotated = stamp_unrotated.rotate(angle, expand=True, resample=Image.BICUBIC)
        paste_x = base_image.width - stamp_rotated.width - 10; paste_y = 10
        base_image.paste(stamp_rotated, (paste_x, paste_y), stamp_rotated)
        img_buffer = io.BytesIO()
        base_image.convert("RGB").save(img_buffer, format='JPEG', quality=90)
        
        return Response(img_buffer.getvalue(), mimetype='image/jpeg')
    except Exception as e:
        logger.error(f"生成盖章水印图片时出错 (Tag: {tag}): {e}", exc_info=True)
        return "Internal Server Error", 500

# ★★★ 已重构：增加大量诊断日志，并优化逻辑 ★★★
def handle_get_mimicked_library_items(mimicked_id, params):
    try:
        logger.debug(f"--- 开始处理虚拟库 {mimicked_id} 的内容 ---")
        real_db_id = int(mimicked_id.replace(_MIMICKED_LIBRARY_ID_PREFIX, ""))
        collection_info = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, real_db_id)
        if not collection_info:
            logger.error(f"数据库中未找到 ID 为 {real_db_id} 的自定义合集。")
            return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')
        # 1. 获取 Emby 真实合集中的项目
        real_items_map = {}
        real_emby_collection_id = collection_info.get('emby_collection_id')
        if real_emby_collection_id:
            base_url, api_key = _get_real_emby_url_and_key()
            target_url = f"{base_url}/emby/Items"
            new_params = params.copy()
            new_params["ParentId"] = real_emby_collection_id
            new_params['api_key'] = api_key
            new_params['Fields'] = new_params.get('Fields', '') + ',ProviderIds'
            new_params['Limit'] = 1000   # 取消默认的50限制
            resp = requests.get(target_url, params=new_params, timeout=30.0)
            resp.raise_for_status()
            for item in resp.json().get("Items", []):
                tmdb_id = str(item.get('ProviderIds', {}).get('Tmdb'))
                if tmdb_id:
                    real_items_map[tmdb_id] = item
        logger.debug(f"步骤1: 从Emby合集 '{collection_info['name']}' 中获取到 {len(real_items_map)} 个真实媒体项。")
        # 2. 获取所有相关元数据
        watching_tmdb_ids = db_handler.get_watching_tmdb_ids(config_manager.DB_PATH)
        all_db_items = json.loads(collection_info.get('generated_media_info_json', '[]'))
        real_server_id = extensions.EMBY_SERVER_ID
        definition = json.loads(collection_info.get('definition_json', '{}'))
        item_type_from_db = definition.get('item_type', 'Movie')
        authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
        emby_item_type = "Series" if authoritative_type == 'Series' else "Movie"
        logger.debug(f"步骤2.1: 从追剧列表(watchlist)中获取到 {len(watching_tmdb_ids)} 个正在追的剧集ID: {watching_tmdb_ids if watching_tmdb_ids else '无'}")
        logger.debug(f"步骤2.2: 此虚拟库的媒体类型被判定为: {emby_item_type}")
        # 3. 遍历数据库中的完整列表，构建最终返回结果
        final_items = []
        processed_real_tmdb_ids = set()
        logger.debug("--- 开始遍历数据库媒体列表，决定每个项目的显示方式 ---")
        for db_item in all_db_items:
            tmdb_id = str(db_item.get('tmdb_id', ''))
            title = db_item.get('title', '未知标题')
            if not tmdb_id:
                continue
            is_in_library = tmdb_id in real_items_map
            if is_in_library:
                # 条件: 已入库 -> 直接显示真实项目
                logger.debug(f"-> '{title}' (TMDB: {tmdb_id}): 满足[已入库]，直接显示真实项目。")
                final_items.append(real_items_map[tmdb_id])
                processed_real_tmdb_ids.add(tmdb_id)
            else:
                # 条件: 未入库 -> 不显示任何假项，直接跳过
                logger.debug(f"-> '{title}' (TMDB: {tmdb_id}): 不在库中，跳过显示。")
                continue
        # 4. 添加那些在真实合集中存在，但由于某种原因不在我们数据库列表中的项目
        for tmdb_id, real_item in real_items_map.items():
            if tmdb_id not in processed_real_tmdb_ids:
                logger.debug(f"发现一个在Emby合集中但不在数据库列表的真实项目: '{real_item.get('Name')}' (TMDB: {tmdb_id})，将直接添加。")
                final_items.append(real_item)
        logger.debug(f"--- 遍历结束，最终将返回 {len(final_items)} 个项目 ---")
        final_response = {"Items": final_items, "TotalRecordCount": len(final_items)}
        return Response(json.dumps(final_response), mimetype='application/json')
    except Exception as e:
        logger.error(f"处理伪造库内容时发生严重错误: {e}", exc_info=True)
        return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')

def handle_get_latest_items(user_id, params):
    """
    【最终修复版】处理最新媒体请求，智能区分虚拟库和原生全局请求。
    """
    try:
        base_url, api_key = _get_real_emby_url_and_key()
        
        # 尝试从请求参数中获取虚拟库ID
        virtual_library_id = params.get('ParentId') or params.get('customViewId')

        # --- 场景一：为单个虚拟库获取最新媒体 (服务于Web端) ---
        if virtual_library_id and virtual_library_id.startswith(_MIMICKED_LIBRARY_ID_PREFIX):
            logger.info(f"处理针对虚拟库 '{virtual_library_id}' 的最新媒体请求...")
            
            try:
                virtual_library_db_id = int(virtual_library_id.replace(_MIMICKED_LIBRARY_ID_PREFIX, ''))
            except (ValueError, TypeError):
                logger.warning(f"无效的虚拟库ID格式：{virtual_library_id}")
                return Response(json.dumps([]), mimetype='application/json')

            collection_info = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, virtual_library_db_id)
            if not collection_info or not collection_info.get('emby_collection_id'):
                logger.warning(f"未找到虚拟库 {virtual_library_id} 对应的真实Emby合集ID。")
                return Response(json.dumps([]), mimetype='application/json')

            real_emby_collection_id = collection_info.get('emby_collection_id')
            
            # 构造请求，从真实合集中获取按日期排序的项目
            latest_params = {
                "ParentId": real_emby_collection_id,
                "Limit": int(params.get('Limit', '20')),
                "Fields": "PrimaryImageAspectRatio,BasicSyncInfo,DateCreated",
                "SortBy": "DateCreated", 
                "SortOrder": "Descending",
                "Recursive": "true", # 确保能搜索到合集内的内容
                "IncludeItemTypes": "Movie,Series", # 限定类型
                'api_key': api_key,
            }
            
            target_url = f"{base_url}/emby/Users/{user_id}/Items"
            resp = requests.get(target_url, params=latest_params, timeout=15)
            resp.raise_for_status()
            
            # Emby的 /Items 接口返回的是带 "Items" 键的字典
            items_data = resp.json()
            return Response(json.dumps(items_data.get("Items", [])), mimetype='application/json')

        # --- 场景二：转发原生的全局最新媒体请求 (服务于TV端) ---
        else:
            logger.info("未提供虚拟库ID，将请求作为原生全局最新媒体请求转发...")
            
            # 直接使用原始请求的路径和参数进行转发
            target_url = f"{base_url}/{request.path.lstrip('/')}"
            
            forward_headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'accept-encoding']}
            forward_headers['Host'] = urlparse(base_url).netloc
            
            forward_params = request.args.copy()
            forward_params['api_key'] = api_key

            resp = requests.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                params=forward_params,
                data=request.get_data(),
                stream=True,
                timeout=30.0
            )
            
            excluded_resp_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            response_headers = [(name, value) for name, value in resp.raw.headers.items()
                                if name.lower() not in excluded_resp_headers]

            return Response(resp.iter_content(chunk_size=8192), resp.status_code, response_headers)

    except Exception as e:
        logger.error(f"处理最新媒体时发生未知错误: {e}", exc_info=True)
        return Response(json.dumps([]), mimetype='application/json')

proxy_app = Flask(__name__)

@proxy_app.route('/', defaults={'path': ''})
@proxy_app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'])
def proxy_all(path):
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
        parent_id = request.args.get("ParentId")
        if parent_id and parent_id.startswith(_MIMICKED_LIBRARY_ID_PREFIX):
            return handle_get_mimicked_library_items(parent_id, request.args)
        if path.startswith(f'emby/Items/{_MIMICKED_LIBRARY_ID_PREFIX}') and '/Images/' in path:
            return handle_get_mimicked_library_image(path)
        if path.endswith('/Views') and path.startswith('emby/Users/'):
            return handle_get_views()
        details_match = MIMICKED_ITEM_DETAILS_RE.search(f'/{path}')
        if details_match:
            return handle_get_mimicked_library_details(details_match.group(1))
        if path.endswith('/Items/Latest'):
            user_id_match = re.search(r'/emby/Users/([^/]+)/', f'/{path}')
            if user_id_match:
                return handle_get_latest_items(user_id_match.group(1), request.args)
        tag = request.args.get('tag')
        if tag and tag.startswith('watermark_'):
            return handle_get_watermarked_image(tag)
        items_match = MIMICKED_ITEMS_RE.match(f'/{path}')
        if items_match:
            return handle_get_mimicked_library_items(items_match.group(1), request.args)
        path_to_forward = path
        if path and not path.startswith(('emby/', 'socket.io/')):
             path_to_forward = f'web/{path}'
        base_url, api_key = _get_real_emby_url_and_key()
        target_url = f"{base_url}/{path_to_forward}"
        headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'accept-encoding']}
        headers['Host'] = urlparse(base_url).netloc
        headers['Accept-Encoding'] = 'identity'
        params = request.args.copy()
        params['api_key'] = api_key
        resp = requests.request(
            method=request.method, url=target_url, headers=headers, params=params,
            data=request.get_data(), cookies=request.cookies, stream=True, timeout=30.0
        )
        excluded_resp_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for name, value in resp.raw.headers.items()
                            if name.lower() not in excluded_resp_headers]
        return Response(resp.iter_content(chunk_size=8192), resp.status_code, response_headers)
    except Exception as e:
        logger.error(f"[PROXY] HTTP 代理时发生未知错误: {e}", exc_info=True)
        return "Internal Server Error", 500