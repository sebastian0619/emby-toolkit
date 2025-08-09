# reverse_proxy.py (最终完美版 V4 - 诊断增强版)

import logging
import requests
import re
import json
from flask import Flask, request, Response
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import time
from PIL import Image, ImageDraw, ImageFont
import io

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

def handle_get_views():
    real_server_id = extensions.EMBY_SERVER_ID
    if not real_server_id:
        return "Proxy is not ready", 503
    try:
        collections = db_handler.get_all_active_custom_collections(config_manager.DB_PATH)
        fake_views = {"Items": [], "TotalRecordCount": len(collections)}
        
        for coll in collections:
            real_emby_collection_id = coll.get('emby_collection_id')
            
            # --- 核心升级点 ---
            image_tags = {}
            if real_emby_collection_id:
                # 我们不再只传递 ID，而是传递一个包含动态时间戳的完整 tag
                # Emby 客户端会将这个 tag 作为 URL 查询参数 ( ?tag=... )
                # 这样每次刷新首页，URL 都会变化，从而绕过缓存
                image_tags["Primary"] = f"{real_emby_collection_id}?timestamp={int(time.time())}"
            # --- 升级结束 ---

            definition = json.loads(coll.get('definition_json', '{}'))
            item_type_from_db = definition.get('item_type', 'Movie')
            authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
            collection_type = "tvshows" if authoritative_type == 'Series' else "movies"

            fake_view = {
                "Name": coll['name'], "ServerId": real_server_id, "Id": f"{_MIMICKED_LIBRARY_ID_PREFIX}{coll['id']}",
                "Type": "View", "CollectionType": collection_type, "IsFolder": True, "ImageTags": image_tags,
                "BackdropImageTags": [], "PrimaryImageAspectRatio": 0.6666666666666666, "DisplayPreferencesId": "movies",
                "LibraryOptions": {
                    "EnablePhotos": False, "EnableRealtimeMonitor": False, "EnableChapterImageExtraction": False,
                    "ExtractChapterImagesDuringLibraryScan": False, "EnableInternetProviders": True, "SaveLocalMetadata": False,
                    "PathInfos": [{"Path": f"//custom-collection/{coll['id']}", "NetworkPath": None}], "TypeOptions": []
                }
            }
            fake_views["Items"].append(fake_view)
            
        return Response(json.dumps(fake_views), mimetype='application/json')
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
            "PrimaryImageAspectRatio": 0.6666666666666666,
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
    logger.info(f"[PROXY] 开始处理盖章水印图片请求, Tag: '{tag}'")
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
            new_params['Limit'] = 1000   # <-- 新增，取消默认的50限制
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
            # 删除跟播相关的is_watching判断
            # is_watching = tmdb_id in watching_tmdb_ids
            
            # 核心判断逻辑
            if is_in_library:
                # 条件2: 已入库 -> 直接显示真实项目
                logger.debug(f"-> '{title}' (TMDB: {tmdb_id}): 满足[已入库]，直接显示真实项目。")
                final_items.append(real_items_map[tmdb_id])
                processed_real_tmdb_ids.add(tmdb_id)
            else:
                # 条件3: 未入库 -> 根据状态打“缺失”、“订阅”等水印
                status = db_item.get('status', 'missing')
                if status != 'ok':
                    logger.debug(f"-> '{title}' (TMDB: {tmdb_id}): 满足[未入库]，状态为'{status}'，准备打相应水印。")
                    poster_path = db_item.get('poster_path')
                    if poster_path and poster_path.lstrip('/'):
                        image_tag = f"watermark_{status}_{poster_path.lstrip('/')}"
                        fake_item = {
                            "Name": title, "Id": f"fake_{status}_{tmdb_id}", "ServerId": real_server_id, "Type": emby_item_type, "IsFolder": emby_item_type == "Series",
                            "ProductionYear": db_item.get('year'), "ImageTags": {"Primary": image_tag}, "BackdropImageTags": [],
                            "UserData": {}, "ProviderIds": {"Tmdb": tmdb_id}
                        }
                        final_items.append(fake_item)
                    else:
                        logger.warning(f"-> '{title}' (TMDB: {tmdb_id}): 想打'{status}'水印，但无海报路径，此项将不显示。")
                else:
                    logger.debug(f"-> '{title}' (TMDB: {tmdb_id}): 状态为'ok'但未在Emby中找到，此项将不显示。")
        # 4. 添加那些在真实合集中存在，但由于某种原因不在我们数据库列表中的项目
        for tmdb_id, real_item in real_items_map.items():
            if tmdb_id not in processed_real_tmdb_ids:
                logger.warning(f"发现一个在Emby合集中但不在数据库列表的真实项目: '{real_item.get('Name')}' (TMDB: {tmdb_id})，将直接添加。")
                final_items.append(real_item)
        
        logger.debug(f"--- 遍历结束，最终将返回 {len(final_items)} 个项目 ---")
        final_response = {"Items": final_items, "TotalRecordCount": len(final_items)}
        return Response(json.dumps(final_response), mimetype='application/json')
    except Exception as e:
        logger.error(f"处理伪造库内容时发生严重错误: {e}", exc_info=True)
        return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')

def handle_get_latest_items(user_id, params):
    try:
        base_url, _ = _get_real_emby_url_and_key()
        user_token = params.get('api_key') or params.get('X-Emby-Token')
        if not user_token: 
            return Response(json.dumps([]), mimetype='application/json')
        
        # 从请求参数拿虚拟库ID，比如用 ParentId 或自定义参数 customViewId （前端调用时需保证带上）
        virtual_library_id = params.get('ParentId') or params.get('customViewId')
        if not virtual_library_id:
            # 没指定虚拟库，无法获取最新媒体
            return Response(json.dumps([]), mimetype='application/json')

        # 从数据库根据虚拟库ID获取真实Emby合集ID
        try:
            virtual_library_db_id = int(virtual_library_id.replace(_MIMICKED_LIBRARY_ID_PREFIX, '')) if virtual_library_id.startswith(_MIMICKED_LIBRARY_ID_PREFIX) else int(virtual_library_id)
        except Exception:
            logger.warning(f"无效的虚拟库ID格式：{virtual_library_id}")
            return Response(json.dumps([]), mimetype='application/json')

        collection_info = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, virtual_library_db_id)
        if not collection_info:
            logger.info(f"未找到虚拟库ID {virtual_library_db_id} 对应的数据")
            return Response(json.dumps([]), mimetype='application/json')
        
        real_emby_collection_id = collection_info.get('emby_collection_id')
        if not real_emby_collection_id:
            logger.info(f"虚拟库ID {virtual_library_db_id} 未绑定真实Emby合集ID")
            return Response(json.dumps([]), mimetype='application/json')
        
        # 构造参数请求真实Emby最新媒体
        latest_params = {
            "ParentId": real_emby_collection_id, 
            "Limit": int(params.get('Limit', '20')),
            "Fields": "PrimaryImageAspectRatio,BasicSyncInfo,DateCreated",
            "SortBy": "DateCreated", 
            "SortOrder": "Descending", 
            "api_key": user_token,
        }
        url = f"{base_url}/emby/Users/{user_id}/Items"
        resp = requests.get(url, params=latest_params, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("Items", [])
            return Response(json.dumps(items), mimetype='application/json')
        else:
            logger.warning(f"调用真实Emby失败，状态码: {resp.status_code}")
            return Response(json.dumps([]), mimetype='application/json')
    except Exception as e:
        logger.error(f"处理最新媒体时出错: {e}", exc_info=True)
        return Response(json.dumps([]), mimetype='application/json')

proxy_app = Flask(__name__)

@proxy_app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'])
@proxy_app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'])
def proxy_all(path):
    if 'Upgrade' in request.headers and request.headers.get('Upgrade', '').lower() == 'websocket':
        return Response()
    
    try:
        if path.endswith('/Views') and path.startswith('emby/Users/'):
            return handle_get_views()
        
        details_match = MIMICKED_ITEM_DETAILS_RE.search(f'/{path}')
        if details_match:
            return handle_get_mimicked_library_details(details_match.group(1))
        if path.endswith('/Items/Latest'):
            user_id_match = re.search(r'/emby/Users/([^/]+)/', f'/{path}')
            if user_id_match:
                # 这里确保调用时request.args中包含虚拟库标识，前端或客户端请求时需加上ParentId或customViewId参数
                return handle_get_latest_items(user_id_match.group(1), request.args)
        
        tag = request.args.get('tag')
        if tag and tag.startswith('watermark_'):
            return handle_get_watermarked_image(tag)
        parent_id = request.args.get("ParentId")
        if parent_id and parent_id.startswith(_MIMICKED_LIBRARY_ID_PREFIX):
            return handle_get_mimicked_library_items(parent_id, request.args)
        items_match = MIMICKED_ITEMS_RE.match(f'/{path}')
        if items_match:
            return handle_get_mimicked_library_items(items_match.group(1), request.args)
        if path.startswith(f'emby/Items/{_MIMICKED_LIBRARY_ID_PREFIX}') and '/Images/' in path:
            return handle_get_mimicked_library_image(path)
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
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' in content_type or 'javascript' in content_type or 'application/json' in content_type:
            raw_content = resp.content
            try:
                text_content = raw_content.decode('utf-8')
                modified_text = text_content.replace('/web/', '/')
                modified_content_bytes = modified_text.encode('utf-8')
                new_resp = Response(modified_content_bytes, resp.status_code, dict(resp.headers))
                new_resp.headers['Content-Length'] = str(len(modified_content_bytes))
                return new_resp
            except UnicodeDecodeError:
                pass
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(name, value) for name, value in resp.raw.headers.items() if name.lower() not in excluded_headers]
        return Response(resp.iter_content(chunk_size=8192), resp.status_code, response_headers)
        
    except Exception as e:
        logger.error(f"[PROXY] 代理时发生未知错误: {e}", exc_info=True)
        return "Internal Server Error", 500