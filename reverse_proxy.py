# reverse_proxy.py (稳定回退版 - “文件夹”状态)

import logging
import requests
import re
import json
from flask import Flask, request, Response
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import config_manager
import db_handler
import extensions

logger = logging.getLogger(__name__)

_MIMICKED_LIBRARY_ID_PREFIX = "custom_coll_"
MIMICKED_ITEMS_RE = re.compile(f'/emby/Users/[^/]+/Items/({_MIMICKED_LIBRARY_ID_PREFIX}\\d+)')

# --- 辅助函数 ---
def _get_real_emby_url_and_key():
    base_url = config_manager.APP_CONFIG.get("emby_server_url", "").rstrip('/')
    api_key = config_manager.APP_CONFIG.get("emby_api_key", "")
    if not base_url or not api_key:
        raise ValueError("Emby服务器地址或API Key未配置")
    return base_url, api_key

# --- 核心逻辑函数 ---
def handle_get_views():
    """【真正正确的稳定版】伪造媒体库列表，使用 'movies' 类型来确保首页功能正常。"""
    real_server_id = extensions.EMBY_SERVER_ID
    if not real_server_id:
        return "Proxy is not ready", 503
    try:
        collections = db_handler.get_all_active_custom_collections(config_manager.DB_PATH)
        fake_views = {"Items": [], "TotalRecordCount": len(collections)}
        for coll in collections:
            real_emby_collection_id = coll.get('emby_collection_id')
            image_tags = {"Primary": real_emby_collection_id} if real_emby_collection_id else {}
            
            fake_views["Items"].append({
                "Name": coll['name'], "ServerId": real_server_id,
                "Id": f"{_MIMICKED_LIBRARY_ID_PREFIX}{coll['id']}",
                "Type": "CollectionFolder",
                
                # ★★★ 拨乱反正：必须使用 'movies' 才能激活首页的“最新媒体” ★★★
                "CollectionType": "movies",
                
                "IsFolder": True, "ImageTags": image_tags, "BackdropImageTags": [],
                "PrimaryImageAspectRatio": 0.6666666666666666,
            })
        return Response(json.dumps(fake_views), mimetype='application/json')
    except Exception as e:
        logger.error(f"[PROXY] 获取视图数据时出错: {e}", exc_info=True)
        return "Internal Proxy Error", 500

def handle_get_mimicked_library_image(path):
    try:
        real_emby_collection_id = request.args.get('tag') or request.args.get('Tag')
        if not real_emby_collection_id: return "Bad Request", 400
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

def handle_get_mimicked_library_items(mimicked_id, params):
    try:
        real_db_id = int(mimicked_id.replace(_MIMICKED_LIBRARY_ID_PREFIX, ""))
        collection_info = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, real_db_id)
        if not collection_info or not collection_info.get('emby_collection_id'):
            return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')
        
        real_emby_collection_id = collection_info.get('emby_collection_id')
        base_url, api_key = _get_real_emby_url_and_key()
        target_url = f"{base_url}/emby/Items"
        
        new_params = params.copy()
        new_params["ParentId"] = real_emby_collection_id
        new_params['api_key'] = api_key

        resp = requests.get(target_url, params=new_params, timeout=30.0, stream=True)
        resp.raise_for_status()
        return Response(resp.iter_content(chunk_size=8192), resp.status_code, content_type=resp.headers['Content-Type'])
    except Exception as e:
        logger.error(f"处理伪造库内容时出错: {e}", exc_info=True)
        return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')

def handle_get_latest_items(user_id, params):
    try:
        base_url, _ = _get_real_emby_url_and_key()
        user_token = params.get('api_key') or params.get('X-Emby-Token')
        if not user_token:
            return Response(json.dumps([]), mimetype='application/json')

        active_collections = db_handler.get_all_active_custom_collections(config_manager.DB_PATH)
        real_emby_collection_ids = [c.get('emby_collection_id') for c in active_collections if c.get('emby_collection_id')]
        if not real_emby_collection_ids:
            return Response(json.dumps([]), mimetype='application/json')

        all_latest_items = []
        def fetch_latest_from_collection(collection_id):
            try:
                latest_params = {
                    "ParentId": collection_id, "Limit": 20, "Fields": "PrimaryImageAspectRatio,BasicSyncInfo,DateCreated",
                    "SortBy": "DateCreated", "SortOrder": "Descending", "api_key": user_token,
                }
                url = f"{base_url}/emby/Users/{user_id}/Items"
                resp = requests.get(url, params=latest_params, timeout=10)
                if resp.status_code == 200: return resp.json().get("Items", [])
            except Exception: pass
            return []

        with ThreadPoolExecutor(max_workers=5) as executor:
            for items in executor.map(fetch_latest_from_collection, real_emby_collection_ids):
                all_latest_items.extend(items)

        unique_items = {item['Id']: item for item in all_latest_items}
        sorted_items = sorted(unique_items.values(), key=lambda x: x.get('DateCreated', ''), reverse=True)
        limit = int(params.get('Limit', '20'))
        final_items = sorted_items[:limit]
        return Response(json.dumps(final_items), mimetype='application/json')
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
        
        if path.endswith('/Items/Latest'):
            user_id_match = re.search(r'/emby/Users/([^/]+)/', f'/{path}')
            if user_id_match:
                return handle_get_latest_items(user_id_match.group(1), request.args)
        
        parent_id = request.args.get("ParentId")
        if parent_id and parent_id.startswith(_MIMICKED_LIBRARY_ID_PREFIX):
            return handle_get_mimicked_library_items(parent_id, request.args)

        match = MIMICKED_ITEMS_RE.match(f'/{path}')
        if match:
            return handle_get_mimicked_library_items(match.group(1), request.args)

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