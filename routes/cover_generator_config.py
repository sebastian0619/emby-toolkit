# routes/cover_generator_config.py

import os
import json
import logging
from flask import Blueprint, request, jsonify
import task_manager
from extensions import media_processor_instance
import config_manager # 导入以获取数据路径
from extensions import login_required
from tasks import get_task_registry
import emby_handler
logger = logging.getLogger(__name__)

# 创建一个新的蓝图
cover_generator_config_bp = Blueprint('cover_generator_config', __name__, url_prefix='/api/config/cover_generator')

# 定义配置文件的路径
CONFIG_FILE_PATH = os.path.join(config_manager.PERSISTENT_DATA_PATH, "cover_generator.json")

def get_default_config():
    """返回一份与新UI匹配的、精简后的默认配置"""
    return {
        # 基础设置
        "enabled": False,
        "transfer_monitor": True,
        "exclude_libraries": [], # 现在是勾选框
        "sort_by": "Latest", # 默认改为最新添加

        # 媒体数量开关
        "show_item_count": False, # 默认为关闭
        
        # 封面风格
        "cover_style": "single_1",
        "tab": "style-tab",

        # 封面标题
        "title_config": "# 配置封面标题（按媒体库名称对应）\n# 格式如下：\n#\n# 媒体库名称:\n#   - 中文标题\n#   - 英文标题\n#\n",

        # 单图风格设置
        "zh_font_path_local": "", "en_font_path_local": "",
        "zh_font_url": "", "en_font_url": "",
        "zh_font_size": 1.0, "en_font_size": 1.0,
        "blur_size": 50, "color_ratio": 0.8,
        "single_use_primary": False,

        # 多图风格1设置
        "zh_font_path_multi_1_local": "", "en_font_path_multi_1_local": "",
        "zh_font_url_multi_1": "", "en_font_url_multi_1": "",
        "zh_font_size_multi_1": 1.0, "en_font_size_multi_1": 1.0,
        "blur_size_multi_1": 50, "color_ratio_multi_1": 0.8,
        "multi_1_blur": False, "multi_1_use_main_font": False,
        "multi_1_use_primary": True,
    }

# --- 获取封面生成器的配置 ---
@cover_generator_config_bp.route('', methods=['GET'])
@login_required
def get_cover_generator_config():
    """获取封面生成器的配置"""
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
            default_config = get_default_config()
            for key, value in default_config.items():
                if key not in config:
                    config[key] = value
            return jsonify(config)
        else:
            return jsonify(get_default_config())
    except Exception as e:
        logger.error(f"读取封面生成器配置失败: {e}", exc_info=True)
        return jsonify({"error": "读取配置失败"}), 500

# --- 保存封面生成器的配置 ---
@cover_generator_config_bp.route('', methods=['POST'])
@login_required
def save_cover_generator_config():
    """保存封面生成器的配置"""
    try:
        new_config = request.json
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        logger.info("封面生成器配置已保存。")
        return jsonify({"message": "配置已成功保存！"})
    except Exception as e:
        logger.error(f"保存封面生成器配置失败: {e}", exc_info=True)
        return jsonify({"error": "保存配置失败"}), 500
    
# --- 获取所有已配置的 Emby/Jellyfin 服务器列表 ---
@cover_generator_config_bp.route('/libraries', methods=['GET'])
@login_required
def get_all_libraries():
    """获取所有媒体库，用于UI勾选"""
    try:
        libraries = emby_handler.get_emby_libraries(
            base_url=config_manager.APP_CONFIG.get('emby_server_url'),
            api_key=config_manager.APP_CONFIG.get('emby_api_key'),
            user_id=config_manager.APP_CONFIG.get('emby_user_id')
        )
        if libraries:
            # 格式化为勾选框组需要的数据格式
            formatted_libs = [
                {
                    'label': lib.get('Name'), 
                    'value': lib.get('Id') # 使用库ID作为唯一值
                } 
                for lib in libraries
            ]
            return jsonify(formatted_libs)
        return jsonify([])
    except Exception as e:
        logger.error(f"获取媒体库列表失败: {e}", exc_info=True)
        return jsonify({"error": "获取媒体库列表失败"}), 500


# --- 获取所有已选服务器下的所有媒体库 ---
# @cover_generator_config_bp.route('/libraries', methods=['GET'])
# @login_required
# def get_all_libraries():
#     """获取所有已选服务器下的所有媒体库"""
#     try:
#         # 同样，这里的逻辑高度依赖于您如何管理服务器
#         # 我们继续以单服务器为例
#         server_id = 'main_emby' # 这应该与上面 get_available_servers 返回的 value 对应
        
#         # 调用您项目中的 emby_handler 来获取媒体库
#         libraries = emby_handler.get_emby_libraries(
#             base_url=config_manager.APP_CONFIG.get('emby_server_url'),
#             api_key=config_manager.APP_CONFIG.get('emby_api_key'),
#             user_id=config_manager.APP_CONFIG.get('emby_user_id')
#         )
        
#         if libraries:
#             # 将返回的数据格式化为前端 n-select 需要的格式
#             formatted_libs = [
#                 {
#                     'label': f"{lib.get('Name')} ({server_id})", 
#                     'value': f"{server_id}-{lib.get('Id')}" # 使用 '服务器ID-库ID' 作为唯一值
#                 } 
#                 for lib in libraries
#             ]
#             return jsonify(formatted_libs)
#         return jsonify([])

#     except Exception as e:
#         logger.error(f"获取媒体库列表失败: {e}", exc_info=True)
#         return jsonify({"error": "获取媒体库列表失败"}), 500
    
