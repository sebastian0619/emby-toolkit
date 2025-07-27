# routes/logs.py

from flask import Blueprint, request, jsonify, abort, Response
import logging
import os
from werkzeug.utils import secure_filename
import re

import config_manager
from extensions import login_required

logs_bp = Blueprint('logs', __name__, url_prefix='/api/logs')
logger = logging.getLogger(__name__)

@logs_bp.route('/list', methods=['GET'])
@login_required
def list_log_files():
    """列出日志目录下的所有日志文件 (app.log*)"""
    try:
        # config_manager.PERSISTENT_DATA_PATH 变量在当前作用域中可以直接使用
        all_files = os.listdir(config_manager.LOG_DIRECTORY)
        log_files = [f for f in all_files if f.startswith('app.log')]
        
        # 对日志文件进行智能排序，确保 app.log 在最前，然后是 .1.gz, .2.gz ...
        def sort_key(filename):
            if filename == 'app.log':
                return -1
            parts = filename.split('.')
            # 适用于 'app.log.1.gz' 这样的格式
            if len(parts) > 2 and parts[-1] == 'gz' and parts[-2].isdigit():
                return int(parts[-2])
            return float('inf') # 其他不规范的格式排在最后

        log_files.sort(key=sort_key)
        return jsonify(log_files)
    except Exception as e:
        logging.error(f"API: 无法列出日志文件: {e}", exc_info=True)
        return jsonify({"error": "无法读取日志文件列表"}), 500

@logs_bp.route('/view', methods=['GET'])
@login_required
def view_log_file():
    """查看指定日志文件的内容，自动处理 .gz 文件"""
    # 安全性第一：防止目录遍历攻击
    filename = secure_filename(request.args.get('filename', ''))
    if not filename or not filename.startswith('app.log'):
        abort(403, "禁止访问非日志文件或无效的文件名。")

    full_path = os.path.join(config_manager.LOG_DIRECTORY, filename)

    # 再次确认最终路径仍然在合法的日志目录下
    if not os.path.abspath(full_path).startswith(os.path.abspath(config_manager.LOG_DIRECTORY)):
        abort(403, "检测到非法路径访问。")
        
    if not os.path.exists(full_path):
        abort(404, "文件未找到。")

    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        return Response(content, mimetype='text/plain')
        
    except Exception as e:
        logging.error(f"API: 读取日志文件 '{filename}' 时出错: {e}", exc_info=True)
        abort(500, f"读取文件 '{filename}' 时发生内部错误。")

@logs_bp.route('/search', methods=['GET'])
@login_required
def search_all_logs():
    """
    在所有日志文件 (app.log*) 中搜索关键词。
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "搜索关键词不能为空"}), 400
    TIMESTAMP_REGEX = re.compile(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})")

    search_results = []
    
    try:
        # 1. 获取并排序所有日志文件，确保从新到旧搜索
        all_files = os.listdir(config_manager.LOG_DIRECTORY)
        log_files = [f for f in all_files if f.startswith('app.log')]
        
        # --- 代码修改点 ---
        # 简化了排序键，不再处理 .gz 后缀
        def sort_key(filename):
            if filename == 'app.log':
                return -1  # app.log 永远排在最前面
            parts = filename.split('.')
            # 适用于 app.log.1, app.log.2 等格式
            if len(parts) == 3 and parts[0] == 'app' and parts[1] == 'log' and parts[2].isdigit():
                return int(parts[2])
            return float('inf') # 其他不符合格式的文件排在最后
        
        log_files.sort(key=sort_key)

        # 2. 遍历每个文件进行搜索
        for filename in log_files:
            full_path = os.path.join(config_manager.LOG_DIRECTORY, filename)
            try:
                # --- 代码修改点 ---
                # 移除了 opener 的判断，直接使用 open 函数
                with open(full_path, 'rt', encoding='utf-8', errors='ignore') as f:
                    # 逐行读取，避免内存爆炸
                    for line_num, line in enumerate(f, 1):
                        # 不区分大小写搜索
                        if query.lower() in line.lower():
                            match = TIMESTAMP_REGEX.search(line)
                            line_date = match.group(1) if match else "" # 如果匹配失败则为空字符串
                            
                            # 2. 将提取到的日期添加到返回结果中
                            search_results.append({
                                "file": filename,
                                "line_num": line_num,
                                "content": line.strip(),
                                "date": line_date  # <--- 新增的日期字段
                            })
            except Exception as e:
                # 如果单个文件读取失败，记录错误并继续
                logging.warning(f"API: 搜索时无法读取文件 '{filename}': {e}")

        search_results.sort(key=lambda x: x['date'])
        return jsonify(search_results)

    except Exception as e:
        logging.error(f"API: 全局日志搜索时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "搜索过程中发生服务器内部错误"}), 500

@logs_bp.route('/search_context', methods=['GET'])
@login_required
def search_logs_with_context():
    """
    【最终修正版】在所有日志文件中定位包含关键词的完整“处理块”，
    并根据块内的时间戳进行精确排序，同时保留日期信息。
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "搜索关键词不能为空"}), 400

    # 正则表达式保持不变
    START_MARKER = re.compile(r"成功获取Emby演员 '(.+?)' \(ID: .*?\) 的详情")
    END_MARKER = re.compile(r"(✨✨✨处理完成|最终状态: 处理完成)")
    TIMESTAMP_REGEX = re.compile(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})")

    found_blocks = []
    
    try:
        # 获取所有 app.log* 文件，无需预先排序
        all_files = os.listdir(config_manager.LOG_DIRECTORY)
        log_files = [f for f in all_files if f.startswith('app.log')]

        for filename in log_files:
            full_path = os.path.join(config_manager.LOG_DIRECTORY, filename)
            
            in_block = False
            current_block = []
            current_item_name = None

            try:
                with open(full_path, 'rt', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue

                        is_start_marker = START_MARKER.search(line)

                        if is_start_marker:
                            if in_block: pass
                            in_block = True
                            current_block = [line]
                            current_item_name = is_start_marker.group(1)
                        
                        elif in_block:
                            current_block.append(line)
                            
                            is_end_marker = END_MARKER.search(line)
                            
                            if is_end_marker and current_item_name and current_item_name in line:
                                block_content = "\n".join(current_block)
                                if query.lower() in block_content.lower():
                                    
                                    # ★★★ 核心修改 1: 提取时间戳，并将其存储在名为 'date' 的键中 ★★★
                                    block_date = "Unknown Date" # 默认值
                                    if current_block:
                                        match = TIMESTAMP_REGEX.search(current_block[0])
                                        if match:
                                            # match.group(1) 的结果是 "YYYY-MM-DD HH:MM:SS"
                                            block_date = match.group(1)

                                    found_blocks.append({
                                        "file": filename,
                                        "date": block_date, # <--- 使用 'date' 键，前端需要它
                                        "lines": current_block
                                    })
                                
                                in_block = False
                                current_block = []
                                current_item_name = None
            except Exception as e:
                logging.warning(f"API: 上下文搜索时无法读取文件 '{filename}': {e}")
        
        # ★★★ 核心修改 2: 根据我们刚刚添加的 'date' 键进行排序 ★★★
        found_blocks.sort(key=lambda x: x['date'])
        
        return jsonify(found_blocks)

    except Exception as e:
        logging.error(f"API: 上下文日志搜索时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "搜索过程中发生服务器内部错误"}), 500
