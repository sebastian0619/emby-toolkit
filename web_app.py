# web_app.py

import os
import configparser
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import threading
import time 
from typing import Optional, Dict, Any 
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz # 用于处理时区
from douban import DoubanApi

import constants
from core_processor import MediaProcessor 
from logger_setup import logger 

app = Flask(__name__)
app.secret_key = os.urandom(24) 
PERSISTENT_DATA_PATH = "/config"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE_PATH = os.path.join(PERSISTENT_DATA_PATH, constants.CONFIG_FILE)

media_processor_instance: Optional[MediaProcessor] = None
background_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务"
}
task_lock = threading.Lock()

# --- APScheduler 初始化 ---
scheduler = BackgroundScheduler(timezone=str(pytz.timezone(constants.TIMEZONE))) # 假设constants.py定义了TIMEZONE
scheduler.start()
JOB_ID_FULL_SCAN = "scheduled_full_scan"

def load_config() -> dict:
    config = configparser.ConfigParser(defaults={
        constants.CONFIG_OPTION_EMBY_SERVER_URL: "",
        constants.CONFIG_OPTION_EMBY_API_KEY: "",
        constants.CONFIG_OPTION_EMBY_USER_ID: "", 
        constants.CONFIG_OPTION_TMDB_API_KEY: constants.FALLBACK_TMDB_API_KEY,
        constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN: str(constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK),
        constants.CONFIG_OPTION_DOUBAN_MAX_COOLDOWN: str(constants.MAX_API_COOLDOWN_SECONDS_FALLBACK),
        constants.CONFIG_OPTION_DOUBAN_INCREMENT_COOLDOWN: str(constants.COOLDOWN_INCREMENT_SECONDS_FALLBACK),
        constants.CONFIG_OPTION_TRANSLATOR_ENGINES: ",".join(constants.DEFAULT_TRANSLATOR_ENGINES_ORDER),
        constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE: constants.DEFAULT_DOMESTIC_SOURCE_MODE,
        "delay_between_items_sec": "0.5",
        "refresh_emby_after_update": "true",
        "schedule_enabled": "false",
        "schedule_cron": "0 3 * * *", # 默认每天凌晨3点
        "schedule_force_reprocess": "false"
    })
    if os.path.exists(CONFIG_FILE_PATH):
        config.read(CONFIG_FILE_PATH, encoding='utf-8')
    
    app_config = {}
    if constants.CONFIG_SECTION_EMBY not in config: config.add_section(constants.CONFIG_SECTION_EMBY)
    app_config["emby_server_url"] = config.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL)
    app_config["emby_api_key"] = config.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY)
    app_config["emby_user_id"] = config.get(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_USER_ID)
    app_config["refresh_emby_after_update"] = config.getboolean(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", fallback=True)

    if constants.CONFIG_SECTION_TMDB not in config: config.add_section(constants.CONFIG_SECTION_TMDB)
    app_config["tmdb_api_key"] = config.get(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY)

    if constants.CONFIG_SECTION_API_DOUBAN not in config: config.add_section(constants.CONFIG_SECTION_API_DOUBAN)
    app_config["api_douban_default_cooldown_seconds"] = config.getfloat(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN)
    
    if constants.CONFIG_SECTION_TRANSLATION not in config: config.add_section(constants.CONFIG_SECTION_TRANSLATION)
    engines_str = config.get(constants.CONFIG_SECTION_TRANSLATION, constants.CONFIG_OPTION_TRANSLATOR_ENGINES)
    app_config["translator_engines_order"] = [eng.strip() for eng in engines_str.split(',') if eng.strip()]
    
    if constants.CONFIG_SECTION_DOMESTIC_SOURCE not in config: config.add_section(constants.CONFIG_SECTION_DOMESTIC_SOURCE)
    app_config["domestic_source_mode"] = config.get(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE)

    if "General" not in config: config.add_section("General")
    app_config["delay_between_items_sec"] = config.getfloat("General", "delay_between_items_sec", fallback=0.5)
    
    logger.info("配置已从 config.ini 加载。")

    if "Scheduler" not in config: config.add_section("Scheduler")
    app_config["schedule_enabled"] = config.getboolean("Scheduler", "schedule_enabled", fallback=False)
    app_config["schedule_cron"] = config.get("Scheduler", "schedule_cron", fallback="0 3 * * *")
    app_config["schedule_force_reprocess"] = config.getboolean("Scheduler", "schedule_force_reprocess", fallback=False)
    
    return app_config


def save_config(new_config: Dict[str, Any]):
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE_PATH):
        config.read(CONFIG_FILE_PATH, encoding='utf-8')

    if constants.CONFIG_SECTION_EMBY not in config: config.add_section(constants.CONFIG_SECTION_EMBY)
    if constants.CONFIG_SECTION_TMDB not in config: config.add_section(constants.CONFIG_SECTION_TMDB)
    if constants.CONFIG_SECTION_API_DOUBAN not in config: config.add_section(constants.CONFIG_SECTION_API_DOUBAN)
    if constants.CONFIG_SECTION_TRANSLATION not in config: config.add_section(constants.CONFIG_SECTION_TRANSLATION)
    if constants.CONFIG_SECTION_DOMESTIC_SOURCE not in config: config.add_section(constants.CONFIG_SECTION_DOMESTIC_SOURCE)
    if "General" not in config: config.add_section("General")
    if "Scheduler" not in config: config.add_section("Scheduler")

    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_SERVER_URL, new_config.get("emby_server_url", ""))
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_API_KEY, new_config.get("emby_api_key", ""))
    config.set(constants.CONFIG_SECTION_EMBY, constants.CONFIG_OPTION_EMBY_USER_ID, new_config.get("emby_user_id", ""))
    config.set(constants.CONFIG_SECTION_EMBY, "refresh_emby_after_update", str(new_config.get("refresh_emby_after_update", True)).lower())

    config.set(constants.CONFIG_SECTION_TMDB, constants.CONFIG_OPTION_TMDB_API_KEY, new_config.get("tmdb_api_key", ""))
    
    config.set(constants.CONFIG_SECTION_API_DOUBAN, constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, str(new_config.get("api_douban_default_cooldown_seconds", constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK)))
    
    engines_list = new_config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
    config.set(constants.CONFIG_SECTION_TRANSLATION, constants.CONFIG_OPTION_TRANSLATOR_ENGINES, ",".join(engines_list))

    config.set(constants.CONFIG_SECTION_DOMESTIC_SOURCE, constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE, new_config.get("domestic_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE))

    config.set("General", "delay_between_items_sec", str(new_config.get("delay_between_items_sec", 0.5)))

    config.set("Scheduler", "schedule_enabled", str(new_config.get("schedule_enabled", False)).lower())
    config.set("Scheduler", "schedule_cron", new_config.get("schedule_cron", "0 3 * * *"))
    config.set("Scheduler", "schedule_force_reprocess", str(new_config.get("schedule_force_reprocess", False)).lower())

    with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
        config.write(configfile)
    logger.info("配置已保存到 config.ini。")
    
    
    initialize_media_processor()

    setup_scheduled_tasks()

def initialize_media_processor():
    global media_processor_instance
    current_config = load_config()
    if media_processor_instance and hasattr(media_processor_instance, 'close'):
        logger.info("关闭旧的 MediaProcessor 实例...")
        media_processor_instance.close()
    media_processor_instance = MediaProcessor(config=current_config)
    logger.info("MediaProcessor 实例已更新/初始化。")

def scheduled_task_job(force_reprocess: bool):
    """计划任务实际执行的函数体"""
    global background_task_status
    logger.info(f"定时全量扫描任务启动 (强制重处理: {force_reprocess})。")
    
    if task_lock.locked():
        logger.warning("定时任务触发：但已有其他后台任务运行，本次跳过。")
        return

    # 定义状态更新回调 (与手动触发的类似)
    def update_status_from_scheduler(progress: int, message: str):
        global background_task_status
        if progress >= 0:
            background_task_status["progress"] = progress
        background_task_status["message"] = message
        # 注意：定时任务的状态更新主要看日志，Web界面的实时状态可能不是主要关注点

    action_msg = "定时全量扫描"
    if force_reprocess: action_msg += " (强制)"

    with task_lock:
        if media_processor_instance:
            media_processor_instance.clear_stop_signal()

        background_task_status["is_running"] = True
        background_task_status["current_action"] = action_msg
        background_task_status["progress"] = 0
        background_task_status["message"] = f"{action_msg} 初始化..."
        
        task_completed_normally = False
        try:
            media_processor_instance.process_full_library(
                update_status_callback=update_status_from_scheduler, # 可以复用，但定时任务的进度主要看日志
                force_reprocess_all=force_reprocess
            )
            if not (media_processor_instance and media_processor_instance.is_stop_requested()):
                task_completed_normally = True
        except Exception as e:
            logger.error(f"定时全量扫描任务失败: {e}", exc_info=True)
            update_status_from_scheduler(-1, f"定时扫描失败: {e}")
        finally:
            final_msg = "未知"
            if media_processor_instance and media_processor_instance.is_stop_requested():
                final_msg = "定时任务被外部信号中断。"
                update_status_from_scheduler(background_task_status["progress"], final_msg)
            elif task_completed_normally:
                final_msg = "定时全量扫描处理完成。"
                update_status_from_scheduler(100, final_msg)
            
            logger.info(final_msg) # 定时任务的完成主要通过日志体现
            
            # 短暂保留is_running状态，以便极少数情况下Web查看能看到完成状态
            time.sleep(5) 
            background_task_status["is_running"] = False
            background_task_status["current_action"] = "无"
            background_task_status["progress"] = 0
            if media_processor_instance:
                media_processor_instance.clear_stop_signal()

def setup_scheduled_tasks():
    """根据配置设置或更新定时任务"""
    config = load_config()
    schedule_enabled = config.get("schedule_enabled", False)
    cron_expression = config.get("schedule_cron", "0 3 * * *")
    force_reprocess_scheduled = config.get("schedule_force_reprocess", False)

    # 先移除可能已存在的旧任务
    if scheduler.get_job(JOB_ID_FULL_SCAN):
        scheduler.remove_job(JOB_ID_FULL_SCAN)
        logger.info("已移除旧的定时全量扫描任务。")

    if schedule_enabled:
        try:
            scheduler.add_job(
                func=scheduled_task_job,
                trigger=CronTrigger.from_crontab(cron_expression),
                id=JOB_ID_FULL_SCAN,
                name="定时全量媒体库扫描",
                replace_existing=True,
                args=[force_reprocess_scheduled] # 将强制标志作为参数传递
            )
            logger.info(f"已设置定时全量扫描任务: CRON='{cron_expression}', 强制重处理={force_reprocess_scheduled}")
            scheduler.print_jobs()
        except Exception as e:
            logger.error(f"设置定时任务失败: CRON='{cron_expression}', 错误: {e}")
            flash(f"设置定时任务失败: {e}", "error")
    else:
        logger.info("定时全量扫描任务未启用。")

@app.route('/')
def index():
    return redirect(url_for('settings_page'))

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        # 从表单获取数据
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
            # TODO: 从表单获取其他豆瓣API相关的冷却时间配置 (max_cooldown, increment_cooldown)
            # 例如:
            # "api_douban_max_cooldown_seconds": float(request.form.get("api_douban_max_cooldown_seconds", constants.MAX_API_COOLDOWN_SECONDS_FALLBACK)),
            # "api_douban_increment_cooldown_seconds": float(request.form.get("api_douban_increment_cooldown_seconds", constants.COOLDOWN_INCREMENT_SECONDS_FALLBACK)),
            
            # 定时任务相关配置
            "schedule_enabled": "schedule_enabled" in request.form,
            "schedule_cron": request.form.get("schedule_cron", "0 3 * * *").strip(),
            "schedule_force_reprocess": "schedule_force_reprocess" in request.form,
        }
        
        # 对可能为空的引擎列表做处理，确保至少有一个默认值
        if not new_conf["translator_engines_order"]:
            new_conf["translator_engines_order"] = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
            logger.warning("翻译引擎顺序未配置或为空，已重置为默认值。")

        save_config(new_conf) # save_config 内部会调用 setup_scheduled_tasks
        flash("配置已保存！定时任务已根据新配置更新。", "success")
        return redirect(url_for('settings_page'))

    # GET 请求的逻辑
    current_config = load_config() # load_config 已包含定时任务配置
    
    available_engines = constants.AVAILABLE_TRANSLATOR_ENGINES
    # 确保从配置加载的引擎顺序是列表，如果配置中为空或不存在，使用默认值
    current_engines_list = current_config.get("translator_engines_order", [])
    if not isinstance(current_engines_list, list) or not current_engines_list:
        current_engines_list = constants.DEFAULT_TRANSLATOR_ENGINES_ORDER
    current_engine_str = ",".join(current_engines_list)
    
    domestic_source_options = [
        {"value": constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, "text": "豆瓣本地优先，在线备选 (推荐)"},
        {"value": constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY, "text": "仅在线豆瓣API"},
        {"value": constants.DOMESTIC_SOURCE_MODE_LOCAL_ONLY, "text": "仅豆瓣本地数据 (神医刮削)"},
        {"value": "disabled_douban", "text": "禁用豆瓣数据源"} # 假设 core_processor 中用 "disabled_douban" 判断
    ]

    return render_template('settings.html', 
                           config=current_config, 
                           available_engines=available_engines,
                           current_engine_str=current_engine_str,
                           domestic_source_options=domestic_source_options,
                           task_status=background_task_status,
                           app_version=constants.APP_VERSION)

@app.route('/webhook/emby', methods=['POST'])
def emby_webhook():
    global background_task_status
    if not media_processor_instance:
        logger.error("Webhook触发失败：MediaProcessor未初始化。")
        return jsonify({"status": "error", "message": "服务未就绪"}), 500

    data = request.json
    logger.info(f"收到Emby Webhook: {data.get('Event') if data else '未知数据'}")
    
    item_id = None
    if data and data.get("Item") and data.get("Item").get("Id"):
        item_id = data.get("Item").get("Id")
        event_type = data.get("Event")
        
        trigger_events = ["item.add", "library.new"] 
        if event_type in trigger_events: 
            logger.info(f"Webhook事件 '{event_type}'，准备处理 Item ID: {item_id}")
            
            if task_lock.locked():
                logger.warning(f"Webhook处理 Item ID {item_id}：检测到已有主要后台任务运行，本次忽略。")
                return jsonify({"status": "ignored_due_to_active_task", "item_id": item_id}), 202

            def task_wrapper(item_id_to_process):
                with task_lock: 
                    if media_processor_instance:
                        media_processor_instance.clear_stop_signal()

                    background_task_status["is_running"] = True
                    background_task_status["current_action"] = f"Webhook处理 Item ID: {item_id_to_process}"
                    background_task_status["progress"] = 0 
                    background_task_status["message"] = "处理中..."
                    
                    processing_success = False
                    error_message = ""
                    try:
                        processing_success = media_processor_instance.process_single_item(item_id_to_process)
                        if processing_success:
                            background_task_status["message"] = f"Item ID: {item_id_to_process} 处理完成。"
                        else:
                            if media_processor_instance and media_processor_instance.is_stop_requested():
                                background_task_status["message"] = f"Item ID: {item_id_to_process} 处理被用户中断。"
                                error_message = "用户中断" 
                            else:
                                background_task_status["message"] = f"Item ID: {item_id_to_process} 处理失败（具体原因请看日志）。"
                                error_message = "处理失败" 
                        background_task_status["progress"] = 100 
                    except Exception as e:
                        logger.error(f"Webhook后台任务处理 Item ID {item_id_to_process} 发生严重异常: {e}", exc_info=True)
                        background_task_status["message"] = f"Item ID: {item_id_to_process} 处理时发生严重异常: {e}"
                        error_message = f"异常: {e}"
                    finally:
                        log_final_status = "完成" if processing_success else (error_message or "未知原因失败")
                        logger.info(f"Webhook处理 Item ID {item_id_to_process} 结束，状态: {log_final_status}")
                        
                        # 尝试保存翻译缓存
                        if media_processor_instance and \
                           media_processor_instance.douban_api and \
                           hasattr(DoubanApi, '_save_translation_cache_to_file') and \
                           callable(DoubanApi._save_translation_cache_to_file): # DoubanApi是类名
                            try:
                                logger.info("Webhook任务结束，尝试保存翻译缓存...")
                                DoubanApi._save_translation_cache_to_file() # 调用类方法
                            except Exception as e_save_cache:
                                logger.error(f"保存翻译缓存时发生错误 (Webhook): {e_save_cache}", exc_info=True)
                        
                        time.sleep(1) 
                        background_task_status["is_running"] = False
                        background_task_status["current_action"] = "无"
                        background_task_status["progress"] = 0 
                        if media_processor_instance: 
                            media_processor_instance.clear_stop_signal()

@app.route('/trigger_full_scan', methods=['POST'])
def trigger_full_scan():
    global background_task_status
    if not media_processor_instance:
        flash("错误：服务未就绪，无法开始全量扫描。", "error")
        return redirect(url_for('settings_page'))

    if task_lock.locked():
        flash("已有后台任务正在运行，请稍后再试。", "warning")
        return redirect(url_for('settings_page'))

    force_reprocess = request.form.get('force_reprocess_all') == 'on'
    action_message = "全量媒体库扫描与处理"
    if force_reprocess:
        action_message += " (强制重处理所有)"
        logger.info("收到手动触发全量扫描的请求 (强制重处理所有)。")
    else:
        logger.info("收到手动触发全量扫描的请求。")
    
    def update_status_from_thread(progress: int, message: str):
        global background_task_status
        if progress >= 0: 
            background_task_status["progress"] = progress
        background_task_status["message"] = message

    def task_wrapper_full_scan(force_flag):
        with task_lock:
            if media_processor_instance: 
                media_processor_instance.clear_stop_signal()

            background_task_status["is_running"] = True
            background_task_status["current_action"] = action_message # action_message 在外部定义
            background_task_status["progress"] = 0
            background_task_status["message"] = "全量扫描初始化..."
            
            task_completed_normally = False
            try:
                media_processor_instance.process_full_library(
                    update_status_callback=update_status_from_thread,
                    force_reprocess_all=force_flag
                )
                if not (media_processor_instance and media_processor_instance.is_stop_requested()):
                    task_completed_normally = True
            except Exception as e:
                logger.error(f"全量扫描后台任务失败: {e}", exc_info=True)
                update_status_from_thread(-1, f"全量扫描失败: {e}")
            finally:
                final_message_for_status = "未知结束状态"
                if media_processor_instance and media_processor_instance.is_stop_requested():
                    final_message_for_status = "任务已成功中断。"
                    update_status_from_thread(background_task_status["progress"], final_message_for_status)
                elif task_completed_normally:
                    final_message_for_status = "全量扫描处理完成。"
                    update_status_from_thread(100, final_message_for_status)
                # else: 异常退出时，消息已在except中通过update_status_from_thread设置
                
                logger.info(f"全量扫描任务结束，最终状态: {final_message_for_status}")

                # 尝试保存翻译缓存
                if media_processor_instance and \
                   media_processor_instance.douban_api and \
                   hasattr(DoubanApi, '_save_translation_cache_to_file') and \
                   callable(DoubanApi._save_translation_cache_to_file): # DoubanApi是类名
                    try:
                        logger.info("全量扫描任务结束，尝试保存翻译缓存...")
                        DoubanApi._save_translation_cache_to_file() # 调用类方法
                    except Exception as e_save_cache:
                        logger.error(f"保存翻译缓存时发生错误 (全量扫描): {e_save_cache}", exc_info=True)

                time.sleep(2) 
                background_task_status["is_running"] = False
                background_task_status["current_action"] = "无"
                background_task_status["progress"] = 0
                if media_processor_instance:
                    media_processor_instance.clear_stop_signal()

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

# --- 初始化 ---
if __name__ == '__main__':
    initialize_media_processor() 
    setup_scheduled_tasks() # 应用启动时根据配置设置定时任务
    app.run(host='0.0.0.0', port=5257, debug=True)