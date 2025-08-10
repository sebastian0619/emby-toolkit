# main_fastapi.py

import asyncio
import logging
from contextlib import asynccontextmanager
import aiohttp
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from urllib.parse import urlparse, urlunparse

# --- 从您原有的项目中导入必要的模块 ---
# 假设您的项目结构是扁平的
import config_manager
import db_handler
import extensions
# 导入您原有的蓝图和初始化函数
from web_app import app as flask_admin_app, initialize_processors, init_db, init_auth_from_blueprint
from routes.auth import auth_bp # 假设您的认证逻辑在这里
# 导入您原有的代理处理函数
from reverse_proxy import (
    handle_get_views,
    handle_get_mimicked_library_details,
    handle_get_mimicked_library_image,
    handle_get_watermarked_image,
    handle_get_mimicked_library_items,
    handle_get_latest_items,
    _get_real_emby_url_and_key,
    MIMICKED_ITEM_DETAILS_RE,
    MIMICKED_ITEMS_RE,
    _MIMICKED_LIBRARY_ID_PREFIX
)
# 用于将 Flask 应用挂载到 FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
import re

# --- 日志和配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
config_manager.load_config()
init_db()
initialize_processors()
init_auth_from_blueprint()


# --- FastAPI 应用生命周期管理 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在应用启动时创建 aiohttp session
    app.state.aiohttp_session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    logger.info("FastAPI App started, AIOHTTP ClientSession created.")
    yield
    # 在应用关闭时关闭 session
    await app.state.aiohttp_session.close()
    logger.info("FastAPI App shutting down, AIOHTTP ClientSession closed.")

# --- 创建 FastAPI 主应用 ---
app = FastAPI(title="Emby Virtual Manager & Proxy", lifespan=lifespan)


# --- 1. WebSocket 代理 (借鉴参考代码) ---
@app.websocket("/emby/socket")
async def websocket_proxy(client_ws: WebSocket):
    await client_ws.accept()
    
    try:
        base_url, _ = _get_real_emby_url_and_key()
        parsed_url = urlparse(base_url)
        ws_scheme = 'wss' if parsed_url.scheme == 'https' else 'ws'
        # 构造目标 WebSocket URL，并带上客户端的查询参数
        target_ws_url = urlunparse((ws_scheme, parsed_url.netloc, client_ws.url.path, '', client_ws.url.query, ''))
        
        session = app.state.aiohttp_session
        headers = {k: v for k, v in client_ws.headers.items() if k.lower() not in ['host']}

        async with session.ws_connect(target_ws_url, headers=headers) as server_ws:
            logger.info(f"WebSocket proxy connected to {target_ws_url}")

            # 并发任务：客户端 -> 服务器
            async def forward_client_to_server():
                try:
                    while True:
                        data = await client_ws.receive_text()
                        await server_ws.send_str(data)
                except WebSocketDisconnect:
                    logger.info("Client WebSocket disconnected.")

            # 并发任务：服务器 -> 客户端
            async def forward_server_to_client():
                try:
                    async for msg in server_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await client_ws.send_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await client_ws.send_bytes(msg.data)
                except Exception as e:
                    logger.warning(f"Error receiving from server websocket: {e}")

            await asyncio.gather(forward_client_to_server(), forward_server_to_client())

    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}", exc_info=True)
    finally:
        await client_ws.close()
        logger.info("WebSocket session closed.")


# --- 2. HTTP 反向代理 ---
# 注意：这里我们将您原有的 Flask 函数包装成可以在 FastAPI 中调用的形式
# 这部分改动较大，需要将 requests 库的调用改为 aiohttp
@app.api_route("/{full_path:path}", methods=["GET", "POST", "DELETE", "PUT", "HEAD", "OPTIONS"])
async def http_reverse_proxy(request: Request, full_path: str):
    # --- 路由匹配逻辑 (从您的 reverse_proxy.py 移植) ---
    path_with_prefix = f'/{full_path}'

    # 优先处理我们自己的虚拟路由
    if full_path.endswith('/Views') and full_path.startswith('emby/Users/'):
        # 注意：您原有的 handle_get_views 是同步函数，需要适配
        # 这里为了演示，我们假设它能直接工作，但实际可能需要改为异步
        return handle_get_views() # 实际应用中需要改造此函数为异步

    details_match = MIMICKED_ITEM_DETAILS_RE.search(path_with_prefix)
    if details_match:
        return handle_get_mimicked_library_details(details_match.group(1))

    if full_path.endswith('/Items/Latest'):
        user_id_match = re.search(r'/emby/Users/([^/]+)/', path_with_prefix)
        if user_id_match:
            return handle_get_latest_items(user_id_match.group(1), request.query_params)

    # ... 此处省略了其他虚拟路由的 if/else 判断，移植方法类似 ...
    # ... 例如 handle_get_mimicked_library_items 等 ...
    
    # --- 默认转发逻辑 (使用 aiohttp) ---
    try:
        base_url, api_key = _get_real_emby_url_and_key()
        target_url = f"{base_url}/{full_path}"
        
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host']}
        params = request.query_params.copy()
        # FastAPI 的 query_params 是不可变的，所以要 copy
        mutable_params = dict(params)
        mutable_params['api_key'] = api_key
        
        session = request.app.state.aiohttp_session
        
        async with session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            params=mutable_params,
            data=await request.body()
        ) as resp:
            # 流式传输响应，支持大文件和视频播放
            response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ['content-encoding', 'transfer-encoding']}
            
            return StreamingResponse(
                resp.content,
                status_code=resp.status,
                headers=response_headers,
                media_type=resp.content_type
            )

    except Exception as e:
        logger.error(f"HTTP proxy error for path '{full_path}': {e}", exc_info=True)
        return Response("Internal Server Error", status_code=500)


# --- 3. 挂载您的 Flask 管理后台 ---
# 这使得您可以在同一个端口下，通过 /admin 路径访问旧的管理界面
app.mount("/admin", WSGIMiddleware(flask_admin_app))

@app.get("/")
async def root():
    return {"message": "Welcome to the new FastAPI-based Emby Proxy! Access your admin panel at /admin"}

# --- 运行 ---
if __name__ == "__main__":
    import uvicorn
    # 从配置中读取端口
    proxy_port = int(config_manager.APP_CONFIG.get("proxy_port", 8097)) # 假设配置中有 proxy_port
    uvicorn.run(app, host="0.0.0.0", port=proxy_port, log_level="info")