# routes/tasks.py

import logging
from flask import Blueprint, request, jsonify

# 导入您项目中用于管理和执行任务的核心模块
import task_manager 
from extensions import login_required
# ★★★ 导入任务注册表，这是“翻译”的关键 ★★★
from tasks import get_task_registry

logger = logging.getLogger(__name__)

# 创建一个新的蓝图
tasks_bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

@tasks_bp.route('/run', methods=['POST'])
@login_required
def run_task():
    """
    一个通用的、用于从前端触发后台任务的API端点。
    """
    data = request.get_json()
    if not data or 'task_name' not in data:
        return jsonify({"error": "请求体中缺少 'task_name' 参数"}), 400

    task_name_str = data['task_name']
    logger.info(f"收到来自前端的通用任务执行请求: {task_name_str}")

    try:
        # 步骤 1: “翻译” - 在这里将任务名（字符串）转换为任务函数（对象）
        task_registry = get_task_registry()
        task_info = task_registry.get(task_name_str)

        if not task_info:
            logger.error(f"错误：未在任务注册表中找到名为 '{task_name_str}' 的任务。")
            return jsonify({"error": f"未知的任务名称: {task_name_str}"}), 404

        # task_info 是一个元组 (task_function, description)
        task_function_obj, task_description = task_info
        
        # 步骤 2: “提交” - 调用您现有的、稳定的 submit_task 函数
        success = task_manager.submit_task(
            task_function=task_function_obj, 
            task_name=task_description
        )
        
        if success:
            return jsonify({"message": f"任务 '{task_description}' 已成功提交到后台执行。"}), 202
        else:
            return jsonify({"error": "任务提交失败，可能有其他任务正在运行。"}), 409

    except Exception as e:
        logger.error(f"提交任务 '{task_name_str}' 时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": f"提交任务时发生服务器内部错误: {e}"}), 500