# web_app.py
import os
import sqlite3
import emby_handler
import configparser
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import threading
import time
from typing import Optional, Dict, Any, List # 确保 List 被导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
import atexit # 用于应用退出处理

# --- 核心模块导入 ---
import constants # 你的常量定义
from core_processor import MediaProcessor # 核心处理逻辑
from logger_setup import logger # 日志记录器
# emby_handler 和 utils 会在需要的地方被 core_processor 或此文件中的函数调用
# 如果直接在此文件中使用它们的功能，也需要在这里导入
import emby_handler # 例如，用于 /api/search_media
import utils       # 例如，用于 /api/search_media
# from douban import DoubanApi # 通常不需要在 web_app.py 直接导入 DoubanApi，由 MediaProcessor 管理
# --- 核心模块导入结束 ---

app = Flask(__name__)
app.secret_key = os.urandom(24) # 用于 flash 消息等

# --- 路径和配置定义 ---
PERSISTENT_DATA_PATH = "/config" # Docker 卷挂载的持久化数据目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # 当前文件所在目录
CONFIG_FILE_NAME = "config.ini" # 配置文件名，建议也放入 constants.py
CONFIG_FILE_PATH = os.path.join(PERSISTENT_DATA_PATH, CONFIG_FILE_NAME)

DB_NAME = "emby_actor_processor.sqlite" # 数据库文件名
DB_PATH = os.path.join(PERSISTENT_DATA_PATH, DB_NAME)
# --- 路径和配置定义结束 ---

# --- 全局变量 ---
media_processor_instance: Optional[MediaProcessor] = None
background_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务"
}
task_lock = threading.Lock() # 用于确保后台任务串行执行

scheduler = BackgroundScheduler(timezone=str(pytz.timezone(constants.TIMEZONE)))
JOB_ID_FULL_SCAN = "scheduled_full_scan"
# --- 全局变量结束 ---

# --- 数据库辅助函数 ---
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # 方便按列名访问数据
    return conn

def init_db():
    """初始化数据库表结构"""
    try:
        if not os.path.exists(PERSISTENT_DATA_PATH):
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
            logger.info(f"持久化数据目录已创建: {PERSISTENT_DATA_PATH}")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_log (
                item_id TEXT PRIMARY KEY,
                item_name TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS failed_log (
                item_id TEXT PRIMARY KEY,
                item_name TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error_message TEXT,
                item_type TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS translation_cache (
                original_text TEXT PRIMARY KEY,
                translated_text TEXT,
                engine_used TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_original_text ON translation_cache (original_text)")
        conn.commit()
        logger.info(f"数据库表已在 '{DB_PATH}' 初始化/检查完毕。")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}", exc_info=True)
    finally:
        if 'conn' in locals() and conn: # 确保 conn 已定义
            conn.close()
# --- 数据库辅助函数结束 ---

# --- 配置加载与保存 ---
def load_config() -> dict:
    # (与你之前的 load_config 逻辑基本一致，确保所有默认值和读取逻辑正确)
    # 为简洁起见，这里使用一个简化的结构，你需要用你完整的版本替换
    config_parser = configparser.ConfigParser(defaults={
        constants.CONFIG_OPTION_EMBY_SERVER_URL: "",
        constants.CONFIG_OPTION_EMBY_API_KEY: "",
        # ... 其他所有默认值 ...
        "schedule_cron": "0 3 * * *",
        constants.CONFIG_OPTION_EMBY_LIBRARIES_TO_PROCESS: "",
    })
    if os.path.exists(CONFIG_FILE_PATH):
        config_parser.read(CONFIG_FILE_PATH, encoding='utf-8')
    else:
        logger.warning(f"配置文件 {CONFIG_FILE_PATH} 未找到，将使用默认值。请通过设置页面保存一次以创建文件。")

    app_cfg = {}
    # 示例：
    app_cfg["emby_server_url"] = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL, fallback="")
    app_cfg["emby_api_key"] = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY, fallback="")
    app_cfg["emby_user_id"] = config_parser.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_USER_ID, fallback="")
    app_cfg["refresh_emby_after_update"] = config_parser.getboolean(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", fallback=True)
    app_cfg["tmdb_api_key"] = config_parser.get(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY, fallback=constants.FALLBACK_TMDB_API_KEY)
    app_cfg["api_douban_default_cooldown_seconds"] = config_parser.getfloat(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, fallback=constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK)
    engines_str = config_parser.get(constants.CONFIG_SECTION_TRANSLATION, constants.CONFIG_OPTION_TRANSLATOR_ENGINES, fallback=",".join(constants.DEFAULT_TRANSLATOR_ENGINES_ORDER))
    app_cfg["translator_engines_order"] = [eng.strip() for eng in engines_str.split(',') if eng.strip()]
    if not app_cfg["translator_engines_order"]: app_cfg["translator_engines_order"] = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
    app_cfg["domestic_source_mode"] = config_parser.get(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, fallback=constants.DEFAULT_DOMESTIC_SOURCE_MODE)
    app_cfg["delay_between_items_sec"] = config_parser.getfloat("General", "delay_between_items_sec", fallback=0.5)
    app_cfg["schedule_enabled"] = config_parser.getboolean("Scheduler", "schedule_enabled", fallback=False)
    app_cfg["schedule_cron"] = config_parser.get("Scheduler", "schedule_cron", fallback="0 3 * * *")
    app_cfg["schedule_force_reprocess"] = config_parser.getboolean("Scheduler", "schedule_force_reprocess", fallback=False)
    # 确保在 [Emby] 节下读取，或者你定义的其他节
    if not config_parser.has_section(constants.CONFIG_SECTION_LOCAL_DATA):
        config_parser.add_section(constants.CONFIG_SECTION_LOCAL_DATA)
    app_cfg["local_data_path"] = config_parser.get(
        constants.CONFIG_SECTION_LOCAL_DATA,
        constants.CONFIG_OPTION_LOCAL_DATA_PATH,
        fallback=constants.DEFAULT_LOCAL_DATA_PATH
    ).strip()
    app_cfg["data_source_mode"] = config_parser.get(
        constants.CONFIG_SECTION_DOMESTIC_SOURCE, # 或者你改的新节名
        constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, # 或者你改的新选项名
        fallback=constants.DEFAULT_DATA_SOURCE_MODE
    )

    logger.info(f"配置已从 '{CONFIG_FILE_PATH}' 加载。")
    logger.debug(f"load_config: 返回的 app_cfg 中的 libraries_to_process = {app_cfg.get('libraries_to_process')}")
    return app_cfg

def save_config(new_config: Dict[str, Any]):
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE_PATH):
        config.read(CONFIG_FILE_PATH, encoding='utf-8')

    # 确保所有需要的节都存在
    sections_to_ensure = [
        constants.CONFIG_SECTION_EMBY, constants.CONFIG_SECTION_TMDB,
        constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_SECTION_TRANSLATION,
        constants.CONFIG_SECTION_DOMESTIC_SOURCE, "General", "Scheduler"
    ]
    for section in sections_to_ensure:
        if not config.has_section(constants.CONFIG_SECTION_LOCAL_DATA):
            config.add_section(constants.CONFIG_SECTION_LOCAL_DATA)
        config.set(constants.CONFIG_SECTION_LOCAL_DATA, constants.CONFIG_OPTION_LOCAL_DATA_PATH, str(new_config.get("local_data_path", "")))

    # --- 新增：保存在 Emby 节下的媒体库列表 ---
    libraries_list = new_config.get("libraries_to_process", []) # 期望是一个ID列表
    if not isinstance(libraries_list, list): # 做个类型检查和转换
        if isinstance(libraries_list, str) and libraries_list:
            libraries_list = [lib_id.strip() for lib_id in libraries_list.split(',') if lib_id.strip()]
        else:
            libraries_list = []
    config.set(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, str(new_config.get("data_source_mode", constants.DEFAULT_DATA_SOURCE_MODE)))
    # --- 新增结束 ---

    # --- Emby Section ---
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL, str(new_config.get("emby_server_url", "")))
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY, str(new_config.get("emby_api_key", "")))
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_USER_ID, str(new_config.get("emby_user_id", "")))
    config.set(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", str(new_config.get("refresh_emby_after_update", True)).lower())

    # --- TMDB Section ---
    config.set(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY, str(new_config.get("tmdb_api_key", constants.FALLBACK_TMDB_API_KEY))) # 使用常量中的 fallback

    # --- Douban API Section ---
    config.set(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, str(new_config.get("api_douban_default_cooldown_seconds", constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK)))
    # TODO: 如果还有其他豆瓣 API 配置项 (max_cooldown, increment_cooldown)，也在这里添加

    # --- Translation Section ---
    engines_list = new_config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
    if not isinstance(engines_list, list) or not engines_list: # 确保是列表且非空
        engines_list = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
    config.set(constants.CONFIG_SECTION_TRANSLATION, constants.CONFIG_OPTION_TRANSLATOR_ENGINES, ",".join(engines_list))

    # --- Domestic Source Section ---
    config.set(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, str(new_config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)))

    # --- General Section ---
    config.set("General", "delay_between_items_sec", str(new_config.get("delay_between_items_sec", "0.5"))) # 确保默认值也是字符串或能转为字符串

    # --- Scheduler Section ---
    config.set("Scheduler", "schedule_enabled", str(new_config.get("schedule_enabled", False)).lower())
    config.set("Scheduler", "schedule_cron", str(new_config.get("schedule_cron", "0 3 * * *")))
    config.set("Scheduler", "schedule_force_reprocess", str(new_config.get("schedule_force_reprocess", False)).lower())

    # --- 打印即将写入的配置 (用于调试) ---
    logger.debug("save_config: 即将写入文件的 ConfigParser 内容:")
    for section_name_debug in config.sections():
        logger.debug(f"  [{section_name_debug}]")
        for key_debug, value_debug in config.items(section_name_debug):
            logger.debug(f"    {key_debug} = {value_debug}")
    # --- 调试日志结束 ---

    try:
        if not os.path.exists(PERSISTENT_DATA_PATH):
            logger.info(f"save_config: 持久化目录 {PERSISTENT_DATA_PATH} 不存在，尝试创建...")
            os.makedirs(PERSISTENT_DATA_PATH, exist_ok=True)
        logger.info(f"save_config: 准备将配置写入到文件: {CONFIG_FILE_PATH}")
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
            config.write(configfile)
        logger.info(f"配置已成功写入到 {CONFIG_FILE_PATH}。")
    except Exception as e:
        logger.error(f"保存配置文件 {CONFIG_FILE_PATH} 失败: {e}", exc_info=True)
        # flash(f"保存配置文件失败: {e}", "error") # 在非请求上下文中 flash 会报错
    finally:
        initialize_media_processor() # 重新加载配置并初始化处理器
        setup_scheduled_tasks()    # 根据新配置更新定时任务
# --- 配置加载与保存结束 ---

# --- MediaProcessor 初始化 ---
def initialize_media_processor():
    global media_processor_instance
    current_config = load_config() # load_config 现在会包含 libraries_to_process
    current_config['db_path'] = DB_PATH

    # ... (关闭旧实例的逻辑不变) ...
    if media_processor_instance and hasattr(media_processor_instance, 'close'):
        logger.info("准备关闭旧的 MediaProcessor 实例...")
        try:
            media_processor_instance.close()
            logger.info("旧的 MediaProcessor 实例已关闭。")
        except Exception as e_close_old:
            logger.error(f"关闭旧 MediaProcessor 实例时出错: {e_close_old}", exc_info=True)

    logger.info("准备创建新的 MediaProcessor 实例...")
    try:
        media_processor_instance = MediaProcessor(config=current_config) # current_config 已包含所需信息
        logger.info("新的 MediaProcessor 实例已创建/更新。")
    except Exception as e_init_mp:
        logger.error(f"创建 MediaProcessor 实例失败: {e_init_mp}", exc_info=True)
        media_processor_instance = None
        print(f"CRITICAL ERROR: MediaProcessor 核心处理器初始化失败: {e_init_mp}. 应用可能无法正常工作。")
# --- MediaProcessor 初始化结束 ---

# --- 后台任务回调 ---
def update_status_from_thread(progress: int, message: str):
    global background_task_status
    if progress >= 0:
        background_task_status["progress"] = progress
    background_task_status["message"] = message
    # logger.debug(f"状态更新回调: Progress={progress}%, Message='{message}'") # 这条日志太频繁，可以注释掉
# --- 后台任务回调结束 ---

# --- 后台任务封装 ---
def _execute_task_with_lock(task_function, task_name: str, *args, **kwargs):
    """通用后台任务执行器，包含锁和状态管理"""
    global background_task_status
    if task_lock.locked():
        logger.warning(f"任务 '{task_name}' 触发：但已有其他后台任务运行，本次跳过。")
        if 'update_status_callback' in kwargs: # 如果有回调，通知它
             kwargs['update_status_callback'](-1, "已有任务运行，本次跳过")
        return

    with task_lock:
        if media_processor_instance:
            media_processor_instance.clear_stop_signal()
        else: # 如果 media_processor_instance 未初始化成功
            logger.error(f"任务 '{task_name}' 无法启动：MediaProcessor 未初始化。")
            background_task_status["message"] = "错误：核心处理器未就绪"
            background_task_status["is_running"] = False
            return


        background_task_status["is_running"] = True
        background_task_status["current_action"] = task_name
        background_task_status["progress"] = 0
        background_task_status["message"] = f"{task_name} 初始化..."
        logger.info(f"后台任务 '{task_name}' 开始执行。")

        task_completed_normally = False
        try:
            task_function(*args, **kwargs) # 执行实际的任务函数
            if not (media_processor_instance and media_processor_instance.is_stop_requested()):
                task_completed_normally = True
        except Exception as e:
            logger.error(f"后台任务 '{task_name}' 执行失败: {e}", exc_info=True)
            update_status_from_thread(-1, f"任务失败: {str(e)[:100]}...")
        finally:
            final_message_for_status = "未知结束状态"
            current_progress = background_task_status["progress"] # 获取当前进度

            if media_processor_instance and media_processor_instance.is_stop_requested():
                final_message_for_status = "任务已成功中断。"
            elif task_completed_normally:
                final_message_for_status = "处理完成。"
                current_progress = 100 # 正常完成则进度100%
            # else: 异常退出时，消息已在except中通过update_status_from_thread设置

            update_status_from_thread(current_progress, final_message_for_status)
            logger.info(f"后台任务 '{task_name}' 结束，最终状态: {final_message_for_status}")

            if media_processor_instance and hasattr(media_processor_instance, 'close'):
                logger.info(f"任务 '{task_name}' 结束 (finally块)，准备调用 media_processor_instance.close() ...")
                try:
                    media_processor_instance.close()
                    logger.info(f"media_processor_instance.close() 调用完毕 (任务 '{task_name}' finally块)。")
                except Exception as e_close_proc:
                    logger.error(f"调用 media_processor_instance.close() 时发生错误: {e_close_proc}", exc_info=True)

            time.sleep(1) # 给前端一点时间抓取最终状态
            background_task_status["is_running"] = False
            background_task_status["current_action"] = "无"
            background_task_status["progress"] = 0
            background_task_status["message"] = "等待任务"
            if media_processor_instance:
                media_processor_instance.clear_stop_signal()
            logger.info(f"后台任务 '{task_name}' 状态已重置。")

# --- 定时任务 ---
def scheduled_task_job_internal(force_reprocess: bool):
    """定时任务的实际执行内容 (被 _execute_task_with_lock 调用)"""
    if media_processor_instance:
        media_processor_instance.process_full_library(
            update_status_callback=update_status_from_thread,
            force_reprocess_all=force_reprocess
        )
    else:
        logger.error("定时任务无法执行：MediaProcessor 未初始化。")

def scheduled_task_job_wrapper(force_reprocess: bool):
    """定时任务的包装器，用于被 APScheduler 调用"""
    task_name = "定时全量扫描"
    if force_reprocess: task_name += " (强制)"
    _execute_task_with_lock(scheduled_task_job_internal, task_name, force_reprocess)


def setup_scheduled_tasks():
    config = load_config()
    schedule_enabled = config.get("schedule_enabled", False)
    cron_expression = config.get("schedule_cron", "0 3 * * *")
    force_reprocess_scheduled = config.get("schedule_force_reprocess", False)

    if scheduler.get_job(JOB_ID_FULL_SCAN):
        scheduler.remove_job(JOB_ID_FULL_SCAN)
        logger.info("已移除旧的定时全量扫描任务。")

    if schedule_enabled:
        try:
            scheduler.add_job(
                func=scheduled_task_job_wrapper, # 调用包装器
                trigger=CronTrigger.from_crontab(cron_expression, timezone=str(pytz.timezone(constants.TIMEZONE))),
                id=JOB_ID_FULL_SCAN,
                name="定时全量媒体库扫描",
                replace_existing=True,
                args=[force_reprocess_scheduled]
            )
            logger.info(f"已设置定时全量扫描任务: CRON='{cron_expression}', 强制={force_reprocess_scheduled}")
            if not scheduler.running: scheduler.start() # 确保调度器已启动
            # scheduler.print_jobs() # 打印任务列表以供调试
        except Exception as e:
            logger.error(f"设置定时任务失败: CRON='{cron_expression}', 错误: {e}", exc_info=True)
            flash(f"设置定时任务失败: {e}", "error")
    else:
        logger.info("定时全量扫描任务未启用。")
        if scheduler.running and not scheduler.get_jobs(): # 如果没有其他任务了，可以考虑关闭
            # scheduler.shutdown()
            pass
# --- 定时任务结束 ---

# --- Flask 路由 ---
@app.route('/')
def index():
    return redirect(url_for('settings_page'))

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        new_conf = {
            "emby_server_url": request.form.get("emby_server_url", "").strip(),
            "emby_api_key": request.form.get("emby_api_key", "").strip(),
            "emby_user_id": request.form.get("emby_user_id", "").strip(),
            "tmdb_api_key": request.form.get("tmdb_api_key", "").strip(),
            "translator_engines_order": [eng.strip() for eng in request.form.get("translator_engines_order", "").split(',') if eng.strip()],
            "domestic_source_mode": request.form.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE),
            "delay_between_items_sec": float(request.form.get("delay_between_items_sec", 0.5)),
            "refresh_emby_after_update": "refresh_emby_after_update" in request.form,
            "api_douban_default_cooldown_seconds": float(request.form.get("api_douban_default_cooldown_seconds", constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK)),
            "schedule_enabled": "schedule_enabled" in request.form,
            "schedule_cron": request.form.get("schedule_cron", "0 3 * * *").strip(),
            "schedule_force_reprocess": "schedule_force_reprocess" in request.form,
        }
        new_conf["local_data_path"] = request.form.get("local_data_path", "").strip()
        new_conf["data_source_mode"] = request.form.get("data_source_mode", constants.DEFAULT_DATA_SOURCE_MODE)
        selected_libs_from_form = request.form.getlist("libraries_to_process")
        # 如果你的 HTML checkbox 的 name 是 "libraries_to_process[]"，用下面这行：
        # selected_libs_from_form = request.form.getlist("libraries_to_process[]")
        logger.debug(f"settings_page POST - 从表单获取的 libraries_to_process: {selected_libs_from_form}")
        # --- 新增结束 ---
        logger.debug(f"settings_page POST - 从表单获取的 new_conf: {new_conf}")
        if not new_conf.get("translator_engines_order"): # 确保键存在
            new_conf["translator_engines_order"] = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER

        save_config(new_conf)
        flash("配置已保存！媒体库选择和定时任务已根据新配置更新。", "success")
        return redirect(url_for('settings_page'))

    # GET 请求逻辑
    # --- GET 请求逻辑 ---
    current_config = load_config()
    available_engines = constants.AVAILABLE_TRANSLATOR_ENGINES # 假设这个常量存在
    current_engines_list = current_config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
    current_engine_str = ",".join(current_engines_list)

    data_source_options_to_template = constants.DATA_SOURCE_OPTIONS # 从 constants.py 获取选项列表

    selected_libraries = current_config.get("libraries_to_process", [])

    return render_template('settings.html',
                           config=current_config,
                           available_engines=available_engines,
                           current_engine_str=current_engine_str,
                           data_source_options=data_source_options_to_template, # <--- 传递给模板
                           task_status=background_task_status,
                           app_version=constants.APP_VERSION,
                           selected_libraries=selected_libraries
                           )

@app.route('/webhook/emby', methods=['POST'])
def emby_webhook():
    data = request.json
    logger.info(f"收到Emby Webhook: {data.get('Event') if data else '未知数据'}")
    item_id = data.get("Item", {}).get("Id") if data else None
    event_type = data.get("Event") if data else None
    trigger_events = ["item.add", "library.new"] # 你想触发处理的事件

    if item_id and event_type in trigger_events:
        logger.info(f"Webhook事件 '{event_type}'，准备处理 Item ID: {item_id}")
        def webhook_task_internal(item_id_to_process):
            if media_processor_instance:
                media_processor_instance.process_single_item(item_id_to_process)
            else:
                logger.error(f"Webhook处理 Item ID {item_id_to_process} 失败：MediaProcessor 未初始化。")
        # 使用通用任务执行器
        thread = threading.Thread(target=_execute_task_with_lock, args=(webhook_task_internal, f"Webhook处理 Item ID: {item_id}", item_id))
        thread.start()
        return jsonify({"status": "processing_triggered", "item_id": item_id}), 202
    return jsonify({"status": "event_not_handled"}), 200


@app.route('/trigger_full_scan', methods=['POST'])
def trigger_full_scan():
    force_reprocess = request.form.get('force_reprocess_all') == 'on'
    action_message = "全量媒体库扫描"
    if force_reprocess: action_message += " (强制重处理所有)"

    def full_scan_task_internal(force_flag): # 内部任务函数
        if media_processor_instance:
            media_processor_instance.process_full_library(
                update_status_callback=update_status_from_thread,
                force_reprocess_all=force_flag
            )
        else:
            logger.error("全量扫描无法执行：MediaProcessor 未初始化。")
            update_status_from_thread(-1, "错误：核心处理器未就绪")

    thread = threading.Thread(target=_execute_task_with_lock, args=(full_scan_task_internal, action_message, force_reprocess))
    thread.start()
    flash(f"{action_message}任务已在后台启动。", "info")
    return redirect(url_for('settings_page'))

@app.route('/trigger_stop_task', methods=['POST'])
def trigger_stop_task():
    global background_task_status
    if media_processor_instance and hasattr(media_processor_instance, 'signal_stop'):
        media_processor_instance.signal_stop()
        flash("已发送停止后台任务的请求。任务将在当前步骤完成后停止。", "info")
        background_task_status["message"] = "正在尝试停止任务..."
    else:
        flash("错误：服务未就绪或不支持停止操作。", "error")
    return redirect(url_for('settings_page'))

@app.route('/status')
def get_status():
    return jsonify(background_task_status)

# --- API 端点 for 搜索 (骨架，需要你填充 Emby 搜索和更完善的状态判断) ---
@app.route('/api/search_media')
def api_search_media():
    query = request.args.get('query', '', type=str).strip()
    scope = request.args.get('scope', 'all', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    if page < 1: page = 1
    if per_page < 1: per_page = 10
    if per_page > 100: per_page = 100

    offset = (page - 1) * per_page
    results: List[Dict[str, Any]] = [] # 明确类型
    total_items = 0
    item_ids_on_page: List[str] = [] # 明确类型

    conn = get_db_connection()
    cursor = conn.cursor()

    # TODO: scope 'all' 需要真实的 Emby 搜索集成
    if scope == "all":
        logger.warning("'/api/search_media' scope 'all' 的 Emby 搜索部分尚未完全实现。将临时使用已处理数据。")
        # 临时使用已处理数据代替，你需要替换为真实的 Emby 搜索逻辑
        # emby_search_results = emby_handler.search_emby_library(query, page, per_page, media_processor_instance.emby_url, ...)
        # total_items = emby_search_results.get('total_count', 0)
        # item_ids_on_page = [item['Id'] for item in emby_search_results.get('items', [])]
        # --- 临时代码 ---
        count_sql = "SELECT COUNT(*) FROM processed_log WHERE item_name LIKE ?" if query else "SELECT COUNT(*) FROM processed_log"
        items_sql = "SELECT item_id FROM processed_log WHERE item_name LIKE ? ORDER BY processed_at DESC LIMIT ? OFFSET ?" if query else "SELECT item_id FROM processed_log ORDER BY processed_at DESC LIMIT ? OFFSET ?"
        params = ('%' + query + '%',) if query else ()
        total_items = cursor.execute(count_sql, params).fetchone()[0]
        item_ids_on_page = [row['item_id'] for row in cursor.execute(items_sql, params + (per_page, offset) if query else (per_page, offset)).fetchall()]
        # --- 临时代码结束 ---
    elif scope == "processed":
        count_sql = "SELECT COUNT(*) FROM processed_log WHERE item_name LIKE ?" if query else "SELECT COUNT(*) FROM processed_log"
        items_sql = "SELECT item_id FROM processed_log WHERE item_name LIKE ? ORDER BY processed_at DESC LIMIT ? OFFSET ?" if query else "SELECT item_id FROM processed_log ORDER BY processed_at DESC LIMIT ? OFFSET ?"
        params = ('%' + query + '%',) if query else ()
        total_items = cursor.execute(count_sql, params).fetchone()[0]
        item_ids_on_page = [row['item_id'] for row in cursor.execute(items_sql, params + (per_page, offset) if query else (per_page, offset)).fetchall()]
    elif scope == "failed":
        count_sql = "SELECT COUNT(*) FROM failed_log WHERE item_name LIKE ?" if query else "SELECT COUNT(*) FROM failed_log"
        items_sql = "SELECT item_id FROM failed_log WHERE item_name LIKE ? ORDER BY failed_at DESC LIMIT ? OFFSET ?" if query else "SELECT item_id FROM failed_log ORDER BY failed_at DESC LIMIT ? OFFSET ?"
        params = ('%' + query + '%',) if query else ()
        total_items = cursor.execute(count_sql, params).fetchone()[0]
        item_ids_on_page = [row['item_id'] for row in cursor.execute(items_sql, params + (per_page, offset) if query else (per_page, offset)).fetchall()]
    else:
        conn.close()
        return jsonify({"error": "无效的搜索范围"}), 400

    for item_id_str in item_ids_on_page:
        if not media_processor_instance: break
        emby_details = None
        try:
            emby_details = emby_handler.get_emby_item_details(item_id_str, media_processor_instance.emby_url, media_processor_instance.emby_api_key, media_processor_instance.emby_user_id)
        except Exception as e: logger.error(f"API search: 获取Emby详情失败 for ID {item_id_str}: {e}")

        item_result = {
            "emby_id": item_id_str,
            "name": emby_details.get("Name", "未知") if emby_details else "获取Emby信息失败",
            "year": emby_details.get("ProductionYear") if emby_details else None,
            "type": emby_details.get("Type") if emby_details else None,
            "overall_status": "pending", "failure_reason": None, "actors": []
        }
        processed_entry = cursor.execute("SELECT * FROM processed_log WHERE item_id = ?", (item_id_str,)).fetchone()
        failed_entry = cursor.execute("SELECT * FROM failed_log WHERE item_id = ?", (item_id_str,)).fetchone()
        if failed_entry: item_result["overall_status"], item_result["failure_reason"] = "failed", failed_entry["error_message"]
        elif processed_entry: item_result["overall_status"] = "processed"

        if emby_details and emby_details.get("People"):
            for person in emby_details.get("People", []):
                if person.get("Type") == "Actor":
                    actor_name, actor_role = person.get("Name"), person.get("Role")
                    actor_status_code = "ok"
                    if item_result["overall_status"] == "failed": actor_status_code = "parent_failed"
                    elif item_result["overall_status"] == "pending":
                        actor_status_code = "pending_translation"
                        # TODO: 更精确的状态判断，例如检查翻译缓存或 utils.contains_chinese
                        # if actor_name and not utils.contains_chinese(actor_name): actor_status_code = "name_untranslated"
                        # if actor_role and not utils.contains_chinese(actor_role): actor_status_code = "character_untranslated"
                    item_result["actors"].append({
                        "name": actor_name, "character": actor_role,
                        "status_code": actor_status_code,
                        "status_text": constants.ACTOR_STATUS_TEXT_MAP.get(actor_status_code, "未知")
                    })
        results.append(item_result)
    conn.close()
    total_pages = (total_items + per_page - 1) // per_page if total_items > 0 else 0
    return jsonify({
        "items": results, "total_items": total_items, "total_pages": total_pages,
        "current_page": page, "per_page": per_page, "query": query, "scope": scope
    })

@app.route('/api/emby_libraries')
def api_get_emby_libraries():
    # 确保 media_processor_instance 已初始化并且 Emby 配置有效
    if not media_processor_instance or \
       not media_processor_instance.emby_url or \
       not media_processor_instance.emby_api_key:
        logger.warning("/api/emby_libraries: Emby配置不完整或服务未就绪。")
        return jsonify({"error": "Emby配置不完整或服务未就绪"}), 500

    libraries = emby_handler.get_emby_libraries(
        media_processor_instance.emby_url,
        media_processor_instance.emby_api_key,
        media_processor_instance.emby_user_id # 传递 user_id
    )

    if libraries is not None: # get_emby_libraries 成功返回列表 (可能为空列表)
        return jsonify(libraries)
    else: # get_emby_libraries 返回了 None，表示获取失败
        logger.error("/api/emby_libraries: 无法获取Emby媒体库列表 (emby_handler返回None)。")
        return jsonify({"error": "无法获取Emby媒体库列表，请检查Emby连接和日志"}), 500
# --- API 端点结束 ---


# --- 应用退出处理 ---
def application_exit_handler():
    global media_processor_instance, scheduler
    logger.info("应用程序正在退出 (atexit)，执行清理操作...")
    if media_processor_instance and hasattr(media_processor_instance, 'close'):
        logger.info("正在关闭 MediaProcessor 实例 (atexit)...")
        try: media_processor_instance.close()
        except Exception as e: logger.error(f"atexit 关闭 MediaProcessor 时出错: {e}", exc_info=True)
        else: logger.info("MediaProcessor 实例已关闭 (atexit)。")

    if scheduler and scheduler.running:
        logger.info("正在关闭 APScheduler (atexit)...")
        try: scheduler.shutdown(wait=False) # wait=False 避免阻塞退出
        except Exception as e: logger.error(f"关闭 APScheduler 时发生错误 (atexit): {e}", exc_info=True)
        else: logger.info("APScheduler 已关闭 (atexit)。")
    logger.info("atexit 清理操作执行完毕。")

atexit.register(application_exit_handler)
logger.info("已注册应用程序退出处理程序 (atexit)。")
# --- 应用退出处理结束 ---

# --- 主程序入口 ---
if __name__ == '__main__':
    logger.info(f"应用程序启动... 版本: {constants.APP_VERSION}, 调试模式: {constants.DEBUG_MODE}")
    init_db() # 初始化数据库
    initialize_media_processor() # 初始化核心处理器
    if not scheduler.running: # 确保调度器只启动一次
        try:
            scheduler.start()
            logger.info("APScheduler 已启动。")
        except Exception as e_scheduler_start: # 捕获可能的启动错误
            logger.error(f"APScheduler 启动失败: {e_scheduler_start}", exc_info=True)
    setup_scheduled_tasks() # 设置定时任务

    # 生产环境建议使用 Gunicorn 等 WSGI 服务器
    app.run(host='0.0.0.0', port=constants.WEB_APP_PORT, debug=constants.DEBUG_MODE, use_reloader=not constants.DEBUG_MODE)
    # 注意: debug=True 配合 use_reloader=True (Flask默认) 会导致 atexit 执行两次或行为异常。
    # 在生产中，use_reloader 应为 False。为了开发方便，可以暂时接受 atexit 的一些小问题。
    # 或者在 debug 模式下，考虑不依赖 atexit，而是通过其他方式（如信号处理）来触发清理。
    # 最简单的是，开发时接受它，部署时确保 use_reloader=False。
# --- 主程序入口结束 ---