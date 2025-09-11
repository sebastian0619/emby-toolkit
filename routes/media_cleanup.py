# routes/media_cleanup.py

from flask import Blueprint, jsonify, request
from extensions import task_lock_required, processor_ready_required
import db_handler
import task_manager
import config_manager
from tasks import task_execute_cleanup

media_cleanup_bp = Blueprint('media_cleanup_bp', __name__)

@media_cleanup_bp.route('/api/cleanup/tasks', methods=['GET'])
def get_cleanup_tasks():
    """获取所有待处理的媒体清理任务。"""
    try:
        tasks = db_handler.get_all_cleanup_tasks()
        return jsonify(tasks)
    except Exception as e:
        return jsonify({"error": f"获取清理任务失败: {e}"}), 500

@media_cleanup_bp.route('/api/cleanup/execute', methods=['POST'])
@task_lock_required
@processor_ready_required
def execute_cleanup_tasks():
    """执行指定的清理任务。"""
    data = request.get_json()
    task_ids = data.get('task_ids')
    if not task_ids or not isinstance(task_ids, list):
        return jsonify({"error": "缺少或无效的 task_ids 参数"}), 400

    task_manager.submit_task(
        task_execute_cleanup,
        f"执行 {len(task_ids)} 项媒体清理", # task_name 是第二个位置参数
        'media',                         # processor_type 是第三个位置参数
        task_ids                         # task_ids 现在是第四个位置参数 (*args)
    )
    return jsonify({"message": "清理任务已提交到后台执行。"}), 202

@media_cleanup_bp.route('/api/cleanup/ignore', methods=['POST'])
def ignore_cleanup_tasks():
    """将指定的清理任务标记为已忽略。"""
    data = request.get_json()
    task_ids = data.get('task_ids')
    if not task_ids or not isinstance(task_ids, list):
        return jsonify({"error": "缺少或无效的 task_ids 参数"}), 400
    
    try:
        updated_count = db_handler.batch_update_cleanup_task_status(task_ids, 'ignored')
        return jsonify({"message": f"成功忽略 {updated_count} 个任务。"}), 200
    except Exception as e:
        return jsonify({"error": f"忽略任务时失败: {e}"}), 500

@media_cleanup_bp.route('/api/cleanup/delete', methods=['POST'])
def delete_cleanup_tasks():
    """从列表中删除指定的清理任务（不执行清理）。"""
    data = request.get_json()
    task_ids = data.get('task_ids')
    if not task_ids or not isinstance(task_ids, list):
        return jsonify({"error": "缺少或无效的 task_ids 参数"}), 400

    try:
        deleted_count = db_handler.batch_delete_cleanup_tasks(task_ids)
        return jsonify({"message": f"成功删除 {deleted_count} 个任务。"}), 200
    except Exception as e:
        return jsonify({"error": f"删除任务时失败: {e}"}), 500
    
@media_cleanup_bp.route('/api/cleanup/rules', methods=['GET'])
def get_cleanup_rules():
    """获取当前的媒体清理规则。"""
    try:
        rules = db_handler.get_setting('media_cleanup_rules')
        if not rules:
            # 如果数据库中没有，返回一个安全的默认结构
            rules = [
                {"id": "quality", "enabled": True, "priority": ["Remux", "BluRay", "WEB-DL", "HDTV"]},
                {"id": "resolution", "enabled": True, "priority": ["2160p", "1080p", "720p"]},
                {"id": "filesize", "enabled": True, "priority": "desc"}
            ]
        return jsonify(rules)
    except Exception as e:
        return jsonify({"error": f"获取清理规则失败: {e}"}), 500

@media_cleanup_bp.route('/api/cleanup/rules', methods=['POST'])
def save_cleanup_rules():
    """保存新的媒体清理规则。"""
    new_rules = request.get_json()
    if not isinstance(new_rules, list):
        return jsonify({"error": "无效的规则格式，必须是一个列表。"}), 400
    
    try:
        # 直接使用 db_handler 保存设置
        db_handler.save_setting('media_cleanup_rules', new_rules)
        # 通知配置管理器重新加载内存中的设置，确保后续任务使用新规则
        return jsonify({"message": "清理规则已成功保存！"}), 200
    except Exception as e:
        return jsonify({"error": f"保存清理规则时失败: {e}"}), 500