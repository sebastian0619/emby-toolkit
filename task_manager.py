# task_manager.py (V3 - 队列与即时双模式版)
import threading
import logging
from queue import Queue
from typing import Optional, Callable, Any, Union, Literal, Set

# 导入类型提示，使用字符串避免循环导入
from core_processor import MediaProcessor
from watchlist_processor import WatchlistProcessor
from actor_subscription_processor import ActorSubscriptionProcessor
import extensions

logger = logging.getLogger(__name__)

# 定义处理器类型的字面量
ProcessorType = Literal['media', 'watchlist', 'actor']

# --- 1. 队列任务系统 (用于 item.add, library.new 等需要排队和去重的任务) ---

# 任务状态现在只与主队列任务绑定，这是前端UI主要关心的状态
queued_task_status = {
    "is_running": False,
    "current_action": "无",
    "progress": 0,
    "message": "等待任务",
    "last_action": None
}
# 主任务执行锁，确保同一时间只有一个队列任务在运行
queued_task_lock = threading.Lock()

# 任务队列
queued_task_queue = Queue()
# 队列的工人线程
queued_task_worker_thread: Optional[threading.Thread] = None
# 用于启动工人线程的锁，防止重复启动
queued_task_worker_lock = threading.Lock()

# ✨ 新增：用于跟踪已在队列中的任务ID，实现去重功能的核心
pending_task_ids: Set[str] = set()
pending_ids_lock = threading.Lock()  # 保护 pending_task_ids 的线程安全

def get_task_status() -> dict:
    """获取主后台任务队列的状态。"""
    return queued_task_status.copy()

def is_task_running() -> bool:
    """检查是否有主后台任务（队列任务）正在运行。"""
    return queued_task_lock.locked()

def _update_status_from_thread(progress: int, message: str):
    """由处理器或任务函数调用，用于更新队列任务的状态。"""
    if progress >= 0:
        queued_task_status["progress"] = progress
    queued_task_status["message"] = message

def _execute_queued_task(task_function: Callable, task_name: str, processor, *args, **kwargs):
    """【队列工人专用】执行单个队列任务，并管理状态和锁。"""
    global queued_task_status
    
    with queued_task_lock:
        if not processor:
            logger.error(f"队列任务 '{task_name}' 无法启动：对应的处理器未初始化。")
            return

        processor.clear_stop_signal()
        queued_task_status.update({
            "is_running": True, "current_action": task_name, "last_action": task_name,
            "progress": 0, "message": f"{task_name} 初始化..."
        })
        logger.info(f"--- 队列任务 '{task_name}' 开始执行 ---")

        task_completed_normally = False
        try:
            task_function(processor, *args, **kwargs)
            if not processor.is_stop_requested():
                task_completed_normally = True
        finally:
            final_message = "未知结束状态"
            if processor.is_stop_requested():
                final_message = "任务已成功中断。"
            elif task_completed_normally:
                final_message = "处理完成。"
                _update_status_from_thread(100, final_message)
            
            logger.info(f"--- 队列任务 '{task_name}' 结束，最终状态: {final_message} ---")
            queued_task_status.update({
                "is_running": False, "current_action": "无", "progress": 0, "message": "等待任务"
            })
            processor.clear_stop_signal()

def _queued_task_worker_loop():
    """队列任务的工人线程主循环。"""
    logger.info("队列任务工人线程已启动，等待任务...")
    while True:
        try:
            task_info = queued_task_queue.get()
            if task_info is None:
                logger.info("队列工人线程收到停止信号，即将退出。")
                break

            task_function, task_name, processor_type, item_id, args, kwargs = task_info
            
            # 在任务执行前，将其ID从“待处理”集合中移除
            with pending_ids_lock:
                if item_id in pending_task_ids:
                    pending_task_ids.remove(item_id)

            processor_map = {
                'media': extensions.media_processor_instance,
                'watchlist': extensions.watchlist_processor_instance,
                'actor': extensions.actor_subscription_processor_instance
            }
            processor_to_use = processor_map.get(processor_type)

            if not processor_to_use:
                logger.error(f"任务 '{task_name}' 无法执行：类型为 '{processor_type}' 的处理器未初始化或不存在。")
                queued_task_queue.task_done()
                continue

            _execute_queued_task(task_function, task_name, processor_to_use, *args, **kwargs)
            queued_task_queue.task_done()
        except Exception as e:
            logger.error(f"队列工人线程发生未知错误: {e}", exc_info=True)


# --- 2. 即时任务系统 (用于 library.deleted 等需要立即、并发执行的任务) ---

def _execute_immediate_task(task_function: Callable, task_name: str, processor, *args, **kwargs):
    """【即时任务专用】在一个独立的线程中执行任务，不使用全局锁和状态。"""
    logger.info(f"--- 即时任务 '{task_name}' 在独立线程中开始执行 ---")
    try:
        if not processor:
            logger.error(f"即时任务 '{task_name}' 无法启动：处理器不存在。")
            return
        # 注意：即时任务通常是快速的，这里不处理 stop_signal，如有需要可添加
        task_function(processor, *args, **kwargs)
        logger.info(f"--- 即时任务 '{task_name}' 成功完成 ---")
    except Exception as e:
        logger.error(f"即时任务 '{task_name}' 执行时发生错误: {e}", exc_info=True)


# --- 3. 公共接口 (Public API) ---

def start_task_worker_if_not_running():
    """安全地启动队列任务的工人线程。"""
    global queued_task_worker_thread
    with queued_task_worker_lock:
        if queued_task_worker_thread is None or not queued_task_worker_thread.is_alive():
            logger.trace("队列任务工人线程未运行，正在启动...")
            queued_task_worker_thread = threading.Thread(target=_queued_task_worker_loop, daemon=True)
            queued_task_worker_thread.start()
        else:
            logger.debug("队列任务工人线程已在运行。")

def dispatch_task(
    task_function: Callable,
    task_name: str,
    *,  # 强制后面的参数使用关键字传递，增加代码可读性
    processor_type: ProcessorType = 'media',
    immediate: bool = False,
    item_id: Optional[str] = None,
    **kwargs
) -> bool:
    """
    【V3 - 核心调度器】根据参数决定任务是进入队列还是立即执行。

    :param task_function: 要执行的函数。
    :param task_name: 任务的名称（用于日志）。
    :param processor_type: 需要的处理器类型 ('media', 'watchlist', 'actor')。
    :param immediate: 如果为 True，任务将立即在独立线程中执行；否则进入队列。
    :param item_id: 媒体项的ID。对于队列任务是必需的，用于去重。
    :param kwargs: 传递给 task_function 的其他关键字参数。
    :return: 任务是否成功分发。
    """
    from logger_setup import frontend_log_queue # 延迟导入避免循环

    # --- 分发即时任务 ---
    if immediate:
        logger.info(f"收到一个即时任务请求: '{task_name}'。将立即启动独立线程处理。")
        processor_map = {
            'media': extensions.media_processor_instance,
            'watchlist': extensions.watchlist_processor_instance,
            'actor': extensions.actor_subscription_processor_instance
        }
        processor_to_use = processor_map.get(processor_type)
        if not processor_to_use:
            logger.error(f"即时任务 '{task_name}' 提交失败：处理器 '{processor_type}' 不存在。")
            return False
            
        thread = threading.Thread(
            target=_execute_immediate_task,
            args=(task_function, task_name, processor_to_use),
            kwargs=kwargs
        )
        thread.daemon = True
        thread.start()
        return True

    # --- 分发队列任务 ---
    if not item_id:
        logger.error(f"队列任务 '{task_name}' 提交失败：必须提供 item_id 用于去重。")
        return False

    # 检查主任务是否正在运行
    if is_task_running():
        logger.warning(f"任务 '{task_name}' 提交失败：已有主任务正在运行。")
        return False
        
    # ✨ 核心去重逻辑 ✨
    with pending_ids_lock:
        if item_id in pending_task_ids:
            logger.info(f"任务 '{task_name}' (ID: {item_id}) 已在队列中，本次提交被智能忽略。")
            return True # 返回成功，因为任务最终会被处理

        # 如果没有重复，则加入队列和去重集合
        frontend_log_queue.clear()
        logger.info(f"任务 '{task_name}' (ID: {item_id}, 处理器: {processor_type}) 已提交到队列。")
        
        pending_task_ids.add(item_id)
        task_info = (task_function, task_name, processor_type, item_id, (), kwargs)
        queued_task_queue.put(task_info)
        start_task_worker_if_not_running()
        return True

# --- 应用退出时的清理函数 ---

def stop_task_worker():
    """停止队列任务的工人线程。"""
    global queued_task_worker_thread
    if queued_task_worker_thread and queued_task_worker_thread.is_alive():
        logger.info("正在发送停止信号给队列工人线程...")
        queued_task_queue.put(None)
        queued_task_worker_thread.join(timeout=5)
        if queued_task_worker_thread.is_alive():
            logger.warning("队列工人线程在5秒内未能正常退出。")
        else:
            logger.info("队列工人线程已成功停止。")

def clear_task_queue():
    """清空任务队列。"""
    if not queued_task_queue.empty():
        logger.info(f"队列中还有 {queued_task_queue.qsize()} 个任务，正在清空...")
        with pending_ids_lock:
            pending_task_ids.clear()
            while not queued_task_queue.empty():
                try:
                    queued_task_queue.get_nowait()
                except Queue.Empty:
                    break
        logger.info("任务队列和待处理ID集合已清空。")