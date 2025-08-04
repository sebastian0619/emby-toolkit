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
    """返回一份与原插件UI匹配的、完整的默认配置"""
    return {
        # 基础设置
        "enabled": False,
        "onlyonce": False,
        "transfer_monitor": True,
        "cron": "",
        "delay": 60,
        "selected_servers": [],
        "exclude_libraries": [],
        "sort_by": "Random",
        
        # 封面风格
        "cover_style": "single_1",
        "tab": "style-tab", # 默认打开的标签页

        # 封面标题
        "title_config": "# 配置封面标题（按媒体库名称对应）\n# 格式如下：\n#\n# 媒体库名称:\n#   - 中文标题\n#   - 英文标题\n#\n",

        # 单图风格设置
        "zh_font_path_local": "",
        "en_font_path_local": "",
        "zh_font_url": "",
        "en_font_url": "",
        "zh_font_size": 1.0,
        "en_font_size": 1.0,
        "blur_size": 50,
        "color_ratio": 0.8,
        "single_use_primary": False,

        # 多图风格1设置
        "zh_font_path_multi_1_local": "",
        "en_font_path_multi_1_local": "",
        "zh_font_url_multi_1": "",
        "en_font_url_multi_1": "",
        "zh_font_size_multi_1": 1.0,
        "en_font_size_multi_1": 1.0,
        "blur_size_multi_1": 50,
        "color_ratio_multi_1": 0.8,
        "multi_1_blur": False,
        "multi_1_use_main_font": False,
        "multi_1_use_primary": True,

        # 其他设置
        "covers_output": "",
        "covers_input": ""
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
            # 确保所有键都存在，不存在则用默认值补充
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
@cover_generator_config_bp.route('/servers', methods=['GET'])
@login_required
def get_available_servers():
    """获取所有已配置的 Emby/Jellyfin 服务器列表"""
    try:
        # 假设您的 config_manager.APP_CONFIG 存有所有服务器配置
        # 您需要根据您项目的实际结构来获取服务器列表
        # 这里是一个示例实现：
        
        # 注意：此处的逻辑需要您根据自己项目的实际情况进行调整
        # 以下代码假设您的主配置文件中有多个 [Emby] [Emby2] 这样的节
        
        # 一个更通用的方法是，如果您的项目支持多个服务器，
        # 您应该有一个专门的函数来获取所有服务器的配置。
        # 为简化，我们先假设只有一个服务器。
        
        server_url = config_manager.APP_CONFIG.get('emby_server_url')
        if server_url:
            # 假设服务器的唯一标识就是它的URL
            servers = [{'label': '主 Emby 服务器', 'value': 'main_emby'}] # 您需要一个更好的方式来命名和标识服务器
            return jsonify(servers)
        return jsonify([])
        
    except Exception as e:
        logger.error(f"获取可用服务器列表失败: {e}", exc_info=True)
        return jsonify({"error": "获取服务器列表失败"}), 500


# --- 获取所有已选服务器下的所有媒体库 ---
@cover_generator_config_bp.route('/libraries', methods=['GET'])
@login_required
def get_all_libraries():
    """获取所有已选服务器下的所有媒体库"""
    try:
        # 同样，这里的逻辑高度依赖于您如何管理服务器
        # 我们继续以单服务器为例
        server_id = 'main_emby' # 这应该与上面 get_available_servers 返回的 value 对应
        
        # 调用您项目中的 emby_handler 来获取媒体库
        libraries = emby_handler.get_emby_libraries(
            base_url=config_manager.APP_CONFIG.get('emby_server_url'),
            api_key=config_manager.APP_CONFIG.get('emby_api_key'),
            user_id=config_manager.APP_CONFIG.get('emby_user_id')
        )
        
        if libraries:
            # 将返回的数据格式化为前端 n-select 需要的格式
            formatted_libs = [
                {
                    'label': f"{lib.get('Name')} ({server_id})", 
                    'value': f"{server_id}-{lib.get('Id')}" # 使用 '服务器ID-库ID' 作为唯一值
                } 
                for lib in libraries
            ]
            return jsonify(formatted_libs)
        return jsonify([])

    except Exception as e:
        logger.error(f"获取媒体库列表失败: {e}", exc_info=True)
        return jsonify({"error": "获取媒体库列表失败"}), 500
    
# ★★★ 新增：专门用于执行缓存刷新任务的API接口 ★★★
@cover_generator_config_bp.route('/run-cache-task', methods=['POST'])
@login_required
def run_cache_task():
    """
    专门用于触发“刷新媒体库路径缓存”任务的接口。
    """
    data = request.get_json()
    if not data or 'task_name' not in data:
        return jsonify({"error": "请求体中缺少 'task_name' 参数"}), 400

    task_name_str = data['task_name'] # 这是字符串，例如 'update-library-cache'
    logger.info(f"收到来自前端的任务执行请求: {task_name_str}")

    try:
        # ★★★ 核心适配逻辑：在这里将任务名（字符串）转换为任务函数（对象） ★★★
        task_registry = get_task_registry()
        task_info = task_registry.get(task_name_str)

        if not task_info:
            logger.error(f"错误：未在任务注册表中找到名为 '{task_name_str}' 的任务。")
            return jsonify({"error": f"未知的任务名称: {task_name_str}"}), 404

        # task_info 是一个元组 (task_function, description)
        task_function_obj, task_description = task_info
        
        # ★★★ 现在，我们调用您现有的、稳定的 submit_task 函数 ★★★
        # 我们传递的是【函数对象】和【任务描述】，完全匹配您系统的要求
        success = task_manager.submit_task(
            task_function=task_function_obj, 
            task_name=task_description # 使用友好的描述作为任务名
        )
        
        if success:
            return jsonify({"message": f"任务 '{task_description}' 已成功提交到后台执行。"}), 202
        else:
            return jsonify({"error": "任务提交失败，可能有其他任务正在运行。"}), 409 # 409 Conflict

    except Exception as e:
        logger.error(f"提交任务 '{task_name_str}' 时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": f"提交任务时发生服务器内部错误: {e}"}), 500