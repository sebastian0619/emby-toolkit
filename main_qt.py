# main_qt.py
import sys
import traceback # 用于格式化追溯信息
import os
from datetime import datetime # 用于给日志文件添加时间戳

# --- 应用程序基础路径 ---
# (这个函数可以从 utils.py 复制过来，或者确保 utils.py 在这里能被导入)
# 为了独立性，我们在这里重新定义一个简化版，或者您可以确保 utils.py 的导入
def get_app_base_path():
    if getattr(sys, 'frozen', False): # 是否是打包后的 .exe
        application_path = os.path.dirname(sys.executable)
    else: # 是否是直接运行 .py 文件
        try:
            application_path = os.path.dirname(os.path.abspath(__file__))
        except NameError: # 如果在交互式解释器中
            application_path = os.getcwd()
    return application_path

# --- 全局异常处理函数 ---
def handle_global_exception(exc_type, exc_value, exc_traceback):
    """
    捕获未处理的全局异常，并将其记录到日志文件中。
    """
    # 定义日志文件名和路径
    log_dir = os.path.join(get_app_base_path(), "logs") # 将日志放在 logs 子目录中
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except Exception as e_mkdir:
            # 如果创建目录失败，就直接放在程序根目录
            print(f"创建日志目录失败: {e_mkdir}, 日志将保存在程序根目录。") # 打包后这个print看不到
            log_dir = get_app_base_path()

    # 用时间戳命名日志文件，避免覆盖
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    error_log_file = os.path.join(log_dir, f"crash_report_{timestamp}.log")

    # 格式化异常信息
    error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    
    # 尝试写入日志文件
    try:
        with open(error_log_file, "w", encoding="utf-8") as f:
            f.write("抱歉，程序遇到意外错误并即将关闭。\n")
            f.write("错误发生时间: {}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            f.write("="*50 + "\n")
            f.write("错误详情：\n")
            f.write(error_message)
            f.write("="*50 + "\n")
            f.write("请将此文件提供给开发者以帮助解决问题。\n")
        
        # (可选) 如果是打包的EXE，这里可以尝试弹出一个简单的非Qt提示框告知用户日志已生成
        # 但这会增加复杂性，并且如果Qt本身也崩溃了，可能弹不出来
        # import ctypes
        # ctypes.windll.user32.MessageBoxW(0, f"程序发生严重错误，错误报告已保存到:\n{error_log_file}", "程序崩溃", 0x10 | 0x0) # MB_ICONERROR | MB_OK

    except Exception as e_log:
        # 如果写入日志文件也失败了，尝试在标准错误流打印（打包后可能看不到）
        sys.stderr.write("紧急错误：无法写入崩溃日志！\n")
        sys.stderr.write(str(e_log) + "\n")
        sys.stderr.write(error_message + "\n")

    # 调用原始的 sys.excepthook（通常是打印到 stderr 并退出）
    # 或者直接 sys.exit(1)
    sys.__excepthook__(exc_type, exc_value, exc_traceback) # 调用默认的钩子，它会终止程序
    # 或者更简单粗暴地退出：
    # print("程序因未捕获的异常而终止。错误报告已尝试保存。") # 打包后看不到
    # sys.exit(1)


# --- 在程序启动的早期设置这个钩子 ---
sys.excepthook = handle_global_exception


# --- 后面的代码不变 ---
from PyQt6.QtWidgets import QApplication
# Import constants and logger setup first
import constants
import logger_setup
# Import the main application window class
from app_window_qt import JSONEditorApp 

if __name__ == "__main__":
    # ... (您的 main 函数内容)    # QApplication expects sys.argv
    app = QApplication(sys.argv)

    # Set logger debug status based on constants
    # This should be done after QApplication is created if logger interacts with Qt timers early,
    # but for now, setting it here is fine.
    logger_setup.logger.set_debug(constants.DEBUG)

    main_window = JSONEditorApp()
    # The JSONEditorApp __init__ should set logger_setup.AppMainWindow_instance = self
    # And also call logger_setup.logger.set_status_bar(self.statusBar())

    main_window.show()

    exit_code = app.exec()

    # Cleanly close resources if the app logic has a method for it
    # This is especially important if the DoubanApi session needs explicit closing
    if hasattr(main_window, 'close_app_resources') and callable(main_window.close_app_resources):
        main_window.close_app_resources()
    
    sys.exit(exit_code)