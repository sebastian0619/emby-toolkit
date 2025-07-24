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
    """一个独立的、可复用的函数，用于订阅单季剧集到MoviePilot。"""
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
            "name": series_info['item_name'],
            "tmdbid": int(series_info['tmdb_id']),
            "type": "电视剧"
        }
        if season_number is not None:
            subscribe_payload["season"] = season_number
        
        logger.info(f"【MoviePilot】正在提交任务: '{series_info['item_name']}'" + (f" 第 {season_number} 季" if season_number is not None else ""))
        sub_response = requests.post(subscribe_url, headers=subscribe_headers, json=subscribe_payload, timeout=15)
        
        if sub_response.status_code in [200, 201, 204]:
            logger.info(f"  -> 成功！MoviePilot 已接受订阅任务。")
            return True
        else:
            logger.error(f"  -> 失败！MoviePilot 返回错误: {sub_response.status_code} - {sub_response.text}")
            return False
    except Exception as e:
        logger.error(f"订阅剧集到MoviePilot过程中发生网络或认证错误: {e}")
        return False