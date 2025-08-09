# reverse_proxy.py (稳定回退版 - “文件夹”状态 & 水印健壮版)

import logging
import requests
import re
import json
from flask import Flask, request, Response
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

# --- 新增的导入 ---
from PIL import Image, ImageDraw, ImageFont
import io
# -----------------

import config_manager
import db_handler
import extensions

logger = logging.getLogger(__name__)

_MIMICKED_LIBRARY_ID_PREFIX = "custom_coll_"
MIMICKED_ITEMS_RE = re.compile(f'/emby/Users/[^/]+/Items/({_MIMICKED_LIBRARY_ID_PREFIX}\\d+)')

# --- 水印配置 ---
WATERMARK_MAP = {
    "missing": "缺失",
    "subscribed": "已订阅",
    "not_released": "未上映"
}

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

# ★★★ 已更新：增强了日志和错误处理 ★★★
def handle_get_watermarked_image(tag):
    """
    根据传入的tag获取TMDB图片并添加醒目的“盖章”风格水印。
    """
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

        # 1. 获取TMDB海报
        resp = requests.get(tmdb_image_url, stream=True, timeout=20)
        resp.raise_for_status()
        base_image = Image.open(io.BytesIO(resp.content)).convert("RGBA")

        # 2. 设置字体和颜色
        try:
            # 字体加大
            font = ImageFont.truetype("font.ttf", 65)
        except IOError:
            logger.warning("字体文件 'font.ttf' 未找到，使用默认字体。")
            font = ImageFont.load_default()
        
        # 颜色改为醒目的黄色
        text_color = (255, 255, 0, 255)      # 亮黄色文字
        bg_color = (0, 0, 0, 170)            # 半透明黑色背景
        border_color = (255, 255, 0, 220)    # 亮黄色边框

        # 3. 创建一个单独的、未旋转的“印章”画布
        padding = 20
        text_bbox = font.getbbox(watermark_text)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        
        stamp_width = text_width + padding * 2
        stamp_height = text_height + padding * 2
        
        stamp_unrotated = Image.new('RGBA', (stamp_width, stamp_height), (0,0,0,0))
        draw = ImageDraw.Draw(stamp_unrotated)

        # 4. 在“印章”画布上绘制带边框的背景和文字
        draw.rectangle(
            (0, 0, stamp_width, stamp_height),
            fill=bg_color,
            outline=border_color,
            width=4 # 边框宽度
        )
        # 居中绘制文字
        draw.text(
            (padding, padding - text_bbox[1]),
            watermark_text, 
            font=font, 
            fill=text_color
        )

        # 5. 旋转“印章”
        angle = -15 # 旋转角度
        stamp_rotated = stamp_unrotated.rotate(angle, expand=True, resample=Image.BICUBIC)

        # 6. 将旋转后的“印章”粘贴到海报右上角
        paste_x = base_image.width - stamp_rotated.width - 10
        paste_y = 10
        
        base_image.paste(stamp_rotated, (paste_x, paste_y), stamp_rotated)

        # 7. 输出最终图片
        img_buffer = io.BytesIO()
        base_image.convert("RGB").save(img_buffer, format='JPEG', quality=90)
        
        return Response(img_buffer.getvalue(), mimetype='image/jpeg')

    except Exception as e:
        logger.error(f"生成盖章水印图片时出错 (Tag: {tag}): {e}", exc_info=True)
        return "Internal Server Error", 500

# ★★★ 已更新：修复了海报路径处理逻辑 ★★★
def handle_get_mimicked_library_items(mimicked_id, params):
    try:
        real_db_id = int(mimicked_id.replace(_MIMICKED_LIBRARY_ID_PREFIX, ""))
        collection_info = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, real_db_id)
        if not collection_info:
            return Response(json.dumps({"Items": [], "TotalRecordCount": 0}), mimetype='application/json')

        # 1. 获取真实入库的媒体项
        real_items = []
        existing_tmdb_ids = set()
        real_emby_collection_id = collection_info.get('emby_collection_id')
        if real_emby_collection_id:
            base_url, api_key = _get_real_emby_url_and_key()
            target_url = f"{base_url}/emby/Items"
            new_params = params.copy()
            new_params["ParentId"] = real_emby_collection_id
            new_params['api_key'] = api_key
            new_params['Fields'] = new_params.get('Fields', '') + ',ProviderIds'
            resp = requests.get(target_url, params=new_params, timeout=30.0)
            resp.raise_for_status()
            real_items = resp.json().get("Items", [])
            for item in real_items:
                if 'ProviderIds' in item and 'Tmdb' in item['ProviderIds']:
                    existing_tmdb_ids.add(str(item['ProviderIds']['Tmdb']))

        # 2. 获取数据库中定义的所有媒体项，并创建虚拟项
        fake_items = []
        all_db_items = json.loads(collection_info.get('generated_media_info_json', '[]'))
        real_server_id = extensions.EMBY_SERVER_ID
        
        definition = json.loads(collection_info.get('definition_json', '{}'))
        item_type_from_db = definition.get('item_type', 'Movie')
        authoritative_type = item_type_from_db[0] if isinstance(item_type_from_db, list) and item_type_from_db else item_type_from_db if isinstance(item_type_from_db, str) else 'Movie'
        emby_item_type = "Series" if authoritative_type == 'Series' else "Movie"

        for db_item in all_db_items:
            tmdb_id = str(db_item.get('tmdb_id', ''))
            status = db_item.get('status', 'missing')

            if tmdb_id and tmdb_id not in existing_tmdb_ids and status != 'ok' and db_item.get('poster_path'):
                # --- 核心修复点 ---
                # 1. 使用 lstrip('/') 安全地移除开头的斜杠
                # 2. 检查清理后的路径是否为空，防止生成无效的tag
                poster_path_cleaned = db_item['poster_path'].lstrip('/')
                if poster_path_cleaned:
                    image_tag = f"watermark_{status}_{poster_path_cleaned}"

                    fake_item = {
                        "Name": db_item.get('title', '未知标题'),
                        "Id": f"fake_{tmdb_id}",
                        "ServerId": real_server_id,
                        "Type": emby_item_type,
                        "IsFolder": emby_item_type == "Series",
                        "RunTimeTicks": 0,
                        "ProductionYear": db_item.get('year'),
                        "ImageTags": {"Primary": image_tag},
                        "BackdropImageTags": [],
                        "UserData": {"Played": False, "IsFavorite": False, "PlayCount": 0, "PlaybackPositionTicks": 0},
                        "ProviderIds": {"Tmdb": tmdb_id}
                    }
                    fake_items.append(fake_item)
                # --- 修复结束 ---

        # 3. 合并真实和虚拟列表并返回
        combined_items = real_items + fake_items
        final_response = {"Items": combined_items, "TotalRecordCount": len(combined_items)}
        return Response(json.dumps(final_response), mimetype='application/json')

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
        
        tag = request.args.get('tag')
        if tag and tag.startswith('watermark_'):
            return handle_get_watermarked_image(tag)

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