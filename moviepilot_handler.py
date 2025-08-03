# moviepilot_handler.py

import requests
import logging
from typing import Dict, Any

# 从你的常量模块导入，这不会造成循环
import constants 

logger = logging.getLogger(__name__)

def subscribe_movie_to_moviepilot(movie_info: dict, config: Dict[str, Any]) -> bool:
    """一个独立的、可复用的函数，用于订阅单部电影到MoviePilot。"""
    try:
        moviepilot_url = config.get(constants.CONFIG_OPTION_MOVIEPILOT_URL, '').rstrip('/')
        mp_username = config.get(constants.CONFIG_OPTION_MOVIEPILOT_USERNAME, '')
        mp_password = config.get(constants.CONFIG_OPTION_MOVIEPILOT_PASSWORD, '')
        if not all([moviepilot_url, mp_username, mp_password]):
            logger.warning("MoviePilot订阅跳过：配置不完整。")
            return False

        login_url = f"{moviepilot_url}/api/v1/login/access-token"
        login_data = {"username": mp_username, "password": mp_password}
        login_response = requests.post(login_url, data=login_data, timeout=10)
        login_response.raise_for_status()
        access_token = login_response.json().get("access_token")
        if not access_token:
            logger.error("MoviePilot订阅失败：认证失败，未能获取到 Token。")
            return False

        subscribe_url = f"{moviepilot_url}/api/v1/subscribe/"
        subscribe_headers = {"Authorization": f"Bearer {access_token}"}
        subscribe_payload = {
            "name": movie_info['title'],
            "tmdbid": int(movie_info['tmdb_id']),
            "type": "电影"
        }
        
        logger.info(f"【MoviePilot】正在提交电影任务: '{movie_info['title']}'")
        sub_response = requests.post(subscribe_url, headers=subscribe_headers, json=subscribe_payload, timeout=15)
        
        if sub_response.status_code in [200, 201, 204]:
            logger.info(f"  -> 成功！MoviePilot 已接受订阅任务。")
            return True
        else:
            logger.error(f"  -> 失败！MoviePilot 返回错误: {sub_response.status_code} - {sub_response.text}")
            return False
    except Exception as e:
        logger.error(f"订阅电影到MoviePilot过程中发生网络或认证错误: {e}")
        return False

def subscribe_series_to_moviepilot(series_info: dict, season_number: int, config: Dict[str, Any]) -> bool:
    """
    【V3 - 终极兼容版】一个独立的、可复用的函数，用于订阅单季剧集到MoviePilot。
    此版本具有最终的向后兼容性，可以智能处理以 'title' 或 'item_name' 为键的剧集标题，
    从根本上解决所有调用方因键名不一致导致的 KeyError。
    """
    try:
        moviepilot_url = config.get(constants.CONFIG_OPTION_MOVIEPILOT_URL, '').rstrip('/')
        mp_username = config.get(constants.CONFIG_OPTION_MOVIEPILOT_USERNAME, '')
        mp_password = config.get(constants.CONFIG_OPTION_MOVIEPILOT_PASSWORD, '')
        if not all([moviepilot_url, mp_username, mp_password]):
            logger.warning("MoviePilot订阅跳过：配置不完整。")
            return False

        login_url = f"{moviepilot_url}/api/v1/login/access-token"
        login_data = {"username": mp_username, "password": mp_password}
        login_response = requests.post(login_url, data=login_data, timeout=10)
        login_response.raise_for_status()
        access_token = login_response.json().get("access_token")
        if not access_token:
            logger.error("MoviePilot订阅失败：认证失败，未能获取到 Token。")
            return False

        # --- ▼▼▼ 核心修复：智能、安全地获取标题，增强兼容性 ▼▼▼ ---
        # 使用 .get() 方法安全地尝试获取 'title'，如果失败，再尝试获取 'item_name'。
        series_title = series_info.get('title') or series_info.get('item_name')
        
        # 如果两种可能的键都没有提供，则记录错误并返回 False
        if not series_title:
            logger.error(f"MoviePilot订阅失败：传入的 series_info 字典中缺少 'title' 或 'item_name' 键。字典内容: {series_info}")
            return False
        # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---

        subscribe_url = f"{moviepilot_url}/api/v1/subscribe/"
        subscribe_headers = {"Authorization": f"Bearer {access_token}"}
        subscribe_payload = {
            "name": series_title, # 使用我们安全获取到的标题
            "tmdbid": int(series_info['tmdb_id']),
            "type": "电视剧"
        }
        if season_number is not None:
            subscribe_payload["season"] = season_number
        
        # 使用获取到的 series_title 进行日志记录
        logger.info(f"【MoviePilot】正在提交任务: '{series_title}'" + (f" 第 {season_number} 季" if season_number is not None else ""))
        sub_response = requests.post(subscribe_url, headers=subscribe_headers, json=subscribe_payload, timeout=15)
        
        if sub_response.status_code in [200, 201, 204]:
            logger.info(f"  -> 成功！MoviePilot 已接受订阅任务。")
            return True
        else:
            logger.error(f"  -> 失败！MoviePilot 返回错误: {sub_response.status_code} - {sub_response.text}")
            return False
            
    except KeyError as e:
        # 增加一个针对KeyError的特定捕获，以防万一
        logger.error(f"订阅剧集到MoviePilot时发生KeyError: 键 {e} 不存在。传入的字典: {series_info}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"订阅剧集到MoviePilot过程中发生未知错误: {e}", exc_info=True)
        return False