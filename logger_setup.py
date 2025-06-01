# logger_setup.py
from PyQt6.QtCore import QTimer 
from PyQt6.QtWidgets import QStatusBar   
import constants # <--- 确保这行存在且在顶部

AppMainWindow_instance = None 

PERSISTENT_DATA_PATH = "/config" 
LOG_SUBDIR = os.path.join(PERSISTENT_DATA_PATH, "logs")
os.makedirs(LOG_SUBDIR, exist_ok=True)
log_file = os.path.join(LOG_SUBDIR, "app.log")

class SimpleLogger:
    def __init__(self):
        self.debug_mode = False 
        self.status_bar = None 
        self.gui_log_pending_messages = [] 
        self._status_clear_timer = None 

    def set_status_bar(self, status_bar: 'QStatusBar'): 
        self.status_bar = status_bar
        if self.status_bar:
            try:
                self.status_bar.showMessage("状态就绪", 0) 
            except RuntimeError: 
                 if constants.DEBUG:
                    print(f"[LOGGER_DEBUG] Error setting initial status bar message (likely during shutdown).")

            if hasattr(AppMainWindow_instance, 'log_to_progress_text_qt'):
                pending_copy = self.gui_log_pending_messages[:] 
                self.gui_log_pending_messages.clear()
                for level, msg in pending_copy:
                    try:
                        AppMainWindow_instance.log_to_progress_text_qt(msg, level)
                    except Exception as e:
                        print(f"[LOGGER_ERROR] Error processing pending GUI log message: {e}")

    def set_debug(self, debug_on):
        self.debug_mode = debug_on

    def _log(self, message, level, color="black", clear_after_seconds=None): 
        print(f"[{level.upper()}] {message}") 
        
        can_update_gui = False
        if AppMainWindow_instance and \
           hasattr(AppMainWindow_instance, 'root') and \
           AppMainWindow_instance.root is not None:
            try:
                if AppMainWindow_instance.root.isVisible(): 
                    can_update_gui = True
            except RuntimeError: 
                can_update_gui = False 
                if constants.DEBUG: # 使用导入的 constants
                    print(f"[LOGGER_DEBUG] Main window for GUI updates likely destroyed (RuntimeError).")
            except Exception as e_winfo: 
                if constants.DEBUG: # 使用导入的 constants
                    print(f"[LOGGER_DEBUG] Error checking main window existence for GUI updates: {e_winfo}")
                can_update_gui = False

        if can_update_gui and self.status_bar: 
            try:
                timeout_ms = 0
                if clear_after_seconds and clear_after_seconds > 0:
                    timeout_ms = clear_after_seconds * 1000
                
                self.status_bar.showMessage(message, timeout_ms)
                
                if timeout_ms > 0:
                    if self._status_clear_timer and self._status_clear_timer.isActive():
                        self._status_clear_timer.stop()
                    
                    self._status_clear_timer = QTimer()
                    self._status_clear_timer.setSingleShot(True)
                    self._status_clear_timer.timeout.connect(self._restore_default_status)
                    self._status_clear_timer.start(timeout_ms + 50) 
                elif message != "状态就绪": 
                    if self._status_clear_timer and self._status_clear_timer.isActive():
                        self._status_clear_timer.stop()
            except RuntimeError as e_config: 
                if constants.DEBUG: # 使用导入的 constants
                     print(f"[LOGGER_DEBUG] Error configuring status bar (app likely destroyed during config): {e_config}")
            except Exception as e_gen:
                 if constants.DEBUG: # 使用导入的 constants
                    print(f"[LOGGER_ERROR] Unexpected error updating QStatusBar: {e_gen}")
        
        if hasattr(AppMainWindow_instance, 'log_to_progress_text_qt'):
            if can_update_gui: 
                 try:
                    AppMainWindow_instance.log_to_progress_text_qt(message, level.upper())
                 except RuntimeError as e_progress_runtime: 
                    if constants.DEBUG: # 使用导入的 constants
                        print(f"[LOGGER_DEBUG] Error calling log_to_progress_text_qt (app likely destroyed): {e_progress_runtime}")
                 except Exception as e_progress_generic:
                    if constants.DEBUG: # 使用导入的 constants
                        print(f"[LOGGER_ERROR] Unexpected error in log_to_progress_text_qt: {e_progress_generic}")
            elif not self.status_bar : 
                 if level.upper() != "DEBUG" or self.debug_mode:
                    self.gui_log_pending_messages.append((level.upper(), message))
        elif not self.status_bar : 
            if level.upper() != "DEBUG" or self.debug_mode:
                self.gui_log_pending_messages.append((level.upper(), message))
    
    def _restore_default_status(self):
        if self.status_bar:
            try:
                if AppMainWindow_instance and hasattr(AppMainWindow_instance, 'root') and \
                   AppMainWindow_instance.root is not None and AppMainWindow_instance.root.isVisible():
                    self.status_bar.showMessage("状态就绪", 0)
            except RuntimeError: 
                pass 
            except Exception as e:
                if constants.DEBUG: # 使用导入的 constants
                    print(f"[LOGGER_ERROR] Unexpected error restoring default QStatusBar message: {e}")
        if self._status_clear_timer: 
            try:
                self._status_clear_timer.timeout.disconnect(self._restore_default_status) 
            except TypeError: 
                pass
            self._status_clear_timer = None

    def info(self, message, clear_after_seconds=None): 
        self._log(message, "INFO", clear_after_seconds=clear_after_seconds)

    def success(self, message, clear_after_seconds=5):
        self._log(message, "SUCCESS", clear_after_seconds=clear_after_seconds)

    def warning(self, message, clear_after_seconds=7): 
        self._log(message, "WARNING", clear_after_seconds=clear_after_seconds)

    def error(self, message, clear_after_seconds=10): 
        self._log(message, "ERROR", clear_after_seconds=clear_after_seconds)

    def debug(self, message):
        if self.debug_mode: 
            self._log(message, "DEBUG", clear_after_seconds=5) 

    def clear(self): 
        if self.status_bar:
            try:
                if AppMainWindow_instance and hasattr(AppMainWindow_instance, 'root') and \
                   AppMainWindow_instance.root is not None and AppMainWindow_instance.root.isVisible():
                    self.status_bar.showMessage("状态就绪", 0) 
                    if self._status_clear_timer and self._status_clear_timer.isActive():
                        self._status_clear_timer.stop()
                        try:
                            self._status_clear_timer.timeout.disconnect(self._restore_default_status)
                        except TypeError: pass 
                        self._status_clear_timer = None
            except RuntimeError:
                pass 
            except Exception as e:
                if constants.DEBUG: # 使用导入的 constants
                    print(f"[LOGGER_ERROR] Unexpected error clearing QStatusBar: {e}")
        elif constants.DEBUG: # 使用导入的 constants
             print("[LOGGER_DEBUG] Attempted to clear status_bar but it's not set or app seems destroyed.")

logger = SimpleLogger()