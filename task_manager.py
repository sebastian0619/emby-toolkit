# task_manager.py
import threading
import json
import sqlite3
import logging
from queue import Queue
from typing import Optional, Callable, Any, Union

from db_handler import get_db_connection
from core_processor import MediaProcessor
from watchlist_processor import WatchlistProcessor
from actor_subscription_processor import ActorSubscriptionProcessor
import config_manager
# --- 核心修改：改为定义全局变量，等待被注入 ---
media_processor_instance: Optional['MediaProcessor'] = None
watchlist_processor_instance: Optional['WatchlistProcessor'] = None
actor_subscription_processor_instance: Optional['ActorSubscriptionProcessor'] = None
update_status_from_thread: Optional[Callable] = None

# 导入类型提示，注意使用字符串避免循环导入
from core_processor import MediaProcessor
from watchlist_processor import WatchlistProcessor
from actor_subscription_processor import ActorSubscriptionProcessor

logger = logging.getLogger(__name__)

# --- 任务状态和控制 ---
background_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务"
}
task_lock = threading.Lock()  # 用于确保后台任务串行执行

# --- 任务队列和工人线程 ---
task_queue = Queue()
task_worker_thread: Optional[threading.Thread] = None
task_worker_lock = threading.Lock()

def initialize_task_manager(
    media_proc: MediaProcessor,
    watchlist_proc: WatchlistProcessor,
    actor_sub_proc: ActorSubscriptionProcessor,
    status_callback: Callable
):
    """
    【公共接口】由主应用调用，注入所有必要的处理器实例和回调函数。
    """
    global media_processor_instance, watchlist_processor_instance, actor_subscription_processor_instance, update_status_from_thread
    
    media_processor_instance = media_proc
    watchlist_processor_instance = watchlist_proc
    actor_subscription_processor_instance = actor_sub_proc
    update_status_from_thread = status_callback
    
    logger.info("任务管理器 (TaskManager) 已成功接收并初始化所有处理器实例。")

def get_task_status() -> dict:
    """获取当前后台任务的状态。"""
    return background_task_status.copy()


def is_task_running() -> bool:
    """检查是否有后台任务正在运行。"""
    return task_lock.locked()


def _execute_task_with_lock(task_function: Callable, task_name: str, processor: Union[MediaProcessor, WatchlistProcessor, ActorSubscriptionProcessor], *args, **kwargs):
    """
    【V2 - 工人专用版】通用后台任务执行器。
    这个函数现在是 task_manager 的私有部分。
    """
    global background_task_status
    
    with task_lock:
        if not processor:
            logger.error(f"任务 '{task_name}' 无法启动：对应的处理器未初始化。")
            return

        processor.clear_stop_signal()

        background_task_status["is_running"] = True
        background_task_status["current_action"] = task_name
        background_task_status["progress"] = 0
        background_task_status["message"] = f"{task_name} 初始化..."
        logger.info(f"后台任务 '{task_name}' 开始执行。")

        task_completed_normally = False
        try:
            if processor.is_stop_requested():
                raise InterruptedError("任务被取消")

            task_function(processor, *args, **kwargs)
            
            if not processor.is_stop_requested():
                task_completed_normally = True
        finally:
            final_message_for_status = "未知结束状态"
            current_progress = background_task_status["progress"]

            if processor and processor.is_stop_requested():
                final_message_for_status = "任务已成功中断。"
            elif task_completed_normally:
                final_message_for_status = "处理完成。"
                current_progress = 100
            
            update_status_from_thread(current_progress, final_message_for_status)
            logger.info(f"后台任务 '{task_name}' 结束，最终状态: {final_message_for_status}")

            # 注意：这里的 close() 逻辑可能需要根据实际情况调整
            # 如果处理器是单例，我们可能不应该在这里关闭它，而是在应用退出时关闭
            # 暂时保留，但这是一个潜在的优化点
            # if processor:
            #     processor.close()

            background_task_status["is_running"] = False
            background_task_status["current_action"] = "无"
            background_task_status["progress"] = 0
            background_task_status["message"] = "等待任务"
            if processor:
                processor.clear_stop_signal()
            logger.trace(f"后台任务 '{task_name}' 状态已重置。")


def task_worker_function():
    """
    通用工人线程，从队列中获取并处理各种后台任务。
    """
    logger.info("通用任务线程已启动，等待任务...")
    while True:
        try:
            task_info = task_queue.get()

            if task_info is None:
                logger.info("工人线程收到停止信号，即将退出。")
                break

            task_function, task_name, args, kwargs = task_info
            
            processor_to_use = None
            
            if "追剧" in task_name or "watchlist" in task_function.__name__:
                processor_to_use = watchlist_processor_instance
                logger.debug(f"任务 '{task_name}' 将使用 WatchlistProcessor。")
            elif task_function.__name__ in ['task_process_actor_subscriptions', 'task_scan_actor_media']:
                processor_to_use = actor_subscription_processor_instance
                logger.debug(f"任务 '{task_name}' 将使用 ActorSubscriptionProcessor。")
            else:
                processor_to_use = media_processor_instance
                logger.debug(f"任务 '{task_name}' 将使用 MediaProcessor。")

            if not processor_to_use:
                logger.error(f"任务 '{task_name}' 无法执行：对应的处理器未初始化。")
                task_queue.task_done()
                continue

            _execute_task_with_lock(task_function, task_name, processor_to_use, *args, **kwargs)
            
            task_queue.task_done()
        except Exception as e:
            logger.error(f"通用工人线程发生未知错误: {e}", exc_info=True)


def start_task_worker_if_not_running():
    """
    安全地启动通用工人线程。
    """
    global task_worker_thread
    with task_worker_lock:
        if task_worker_thread is None or not task_worker_thread.is_alive():
            logger.trace("通用任务线程未运行，正在启动...")
            task_worker_thread = threading.Thread(target=task_worker_function, daemon=True)
            task_worker_thread.start()
        else:
            logger.debug("通用任务线程已在运行。")


def submit_task(task_function: Callable, task_name: str, *args, **kwargs) -> bool:
    """
    【公共接口】将一个任务提交到通用队列中。
    返回 True 表示提交成功，False 表示提交失败。
    """
    from logger_setup import frontend_log_queue # 延迟导入以避免循环

    with task_lock:
        if background_task_status["is_running"]:
            logger.warning(f"任务 '{task_name}' 提交失败：已有任务正在运行。")
            return False

        frontend_log_queue.clear()
        logger.info(f"任务 '{task_name}' 已提交到队列，并已清空前端日志。")
        
        task_info = (task_function, task_name, args, kwargs)
        task_queue.put(task_info)
        start_task_worker_if_not_running()
        return True


def stop_task_worker():
    """【公共接口】停止工人线程，用于应用退出。"""
    global task_worker_thread
    if task_worker_thread and task_worker_thread.is_alive():
        logger.info("正在发送停止信号给任务工人线程...")
        task_queue.put(None) # 发送“毒丸”
        task_worker_thread.join(timeout=5)
        if task_worker_thread.is_alive():
            logger.warning("任务工人线程在5秒内未能正常退出。")
        else:
            logger.info("任务工人线程已成功停止。")


def clear_task_queue():
    """【公共接口】清空任务队列，用于应用退出。"""
    if not task_queue.empty():
        logger.info(f"队列中还有 {task_queue.qsize()} 个任务，正在清空...")
        while not task_queue.empty():
            try:
                task_queue.get_nowait()
            except Queue.Empty:
                break
        logger.info("任务队列已清空。")
