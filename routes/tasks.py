# routes/tasks.py

import logging
from flask import Blueprint, request, jsonify

# 导入您项目中用于管理和执行任务的核心模块
import task_manager 
from extensions import login_required, processor_ready_required
# ★★★ 导入任务注册表，这是“翻译”的关键 ★★★
from tasks import get_task_registry

logger = logging.getLogger(__name__)

# 创建一个新的蓝图
tasks_bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

# ★★★ 新增API：获取所有可供选择的任务 ★★★
@tasks_bp.route('/available', methods=['GET'])
@login_required
def get_available_tasks():
    """
    【V2】返回一个可用于任务链配置的、有序的、人类可读的任务列表。
    它现在只返回那些被标记为适合在任务链中运行的任务。
    """
    try:
        # 调用 get_task_registry 时，明确告诉它我们需要用于“任务链”的上下文
        registry = get_task_registry(context='chain')
        
        available_tasks = [
            {"key": key, "name": info[1]} 
            for key, info in registry.items()
        ]
        return jsonify(available_tasks), 200
    except Exception as e:
        logger.error(f"获取可用任务列表时出错: {e}", exc_info=True)
        return jsonify({"error": "无法获取可用任务列表"}), 500

@tasks_bp.route('/run', methods=['POST'])
@login_required
@processor_ready_required
def run_task():
    """
    一个通用的、用于从前端触发后台任务的API端点。
    """
    # ★★★ 步骤 1: 智能门卫 - 检查状态并提供即时反馈 ★★★
    if task_manager.is_task_running():
        running_task_name = task_manager.get_task_status().get('current_action', '未知任务')
        logger.warning(f"任务提交被拒绝：已有任务 '{running_task_name}' 正在运行。")
        return jsonify({"error": f"任务提交失败，已有任务 '{running_task_name}' 正在运行。"}), 409

    # --- 如果门卫放行，才继续处理 ---
    data = request.get_json()
    if not data or 'task_name' not in data:
        return jsonify({"error": "请求体中缺少 'task_name' 参数"}), 400

    task_name_str = data.pop('task_name')
    logger.trace(f"收到来自前端的通用任务执行请求: {task_name_str}, 参数: {data}")

    try:
        task_registry = get_task_registry()
        task_info = task_registry.get(task_name_str)
        if not task_info:
            return jsonify({"error": f"未知的任务名称: {task_name_str}"}), 404

        task_function_obj, task_description = task_info
        
        # ★★★ 步骤 2: 将任务交给 task_manager，它内部有自己的锁来保证安全 ★★★
        success = task_manager.submit_task(
            task_function=task_function_obj, 
            task_name=task_description,
            **data
        )
        
        # submit_task 内部的锁会防止这里的 success 出现竞争问题
        if success:
            return jsonify({"message": f"任务 '{task_description}' 已成功提交。"}), 202
        else:
            # 理论上，由于我们前面的检查，代码不会走到这里。但作为保险，还是保留。
            return jsonify({"error": "任务提交失败，未知错误。"}), 500

    except Exception as e:
        logger.error(f"提交任务 '{task_name_str}' 时出错: {e}", exc_info=True)
        return jsonify({"error": f"服务器内部错误: {e}"}), 500