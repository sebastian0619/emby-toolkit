# app_window_qt.py
import json
import os
import glob
import configparser  # 用于读写 .ini 配置文件
import time
import sys
import webbrowser
import csv
import io
import uuid  # For unique task IDs
import traceback  # For detailed error logging
import re
import constants
import logger_setup
from utils import (
    get_base_path_for_files,
    create_media_item_key,
    is_role_name_valid,
    contains_chinese,
    translate_text_with_translators,
    # --- <<< 新增或确保这两行存在 >>> ---
    get_tmdb_person_details,
    format_tmdb_person_to_cast_entry
    # --- <<< 新增或确保这两行存在 >>> ---
)
from failure_window_qt import FailureProcessingWindowQt
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar, QCheckBox,
    QFileDialog, QMessageBox, QFrame, QScrollArea, QStatusBar, QSizePolicy,
    QSpacerItem
)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSlot, QCoreApplication, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPalette, QColor, QTextCursor, QFont
from typing import Optional, List, Dict

# 导入我们修改过的 constants.py
import constants
import logger_setup
from utils import (
    get_base_path_for_files,
    create_media_item_key,
    is_role_name_valid,
    contains_chinese,
    translate_text_with_translators
)
from failure_window_qt import FailureProcessingWindowQt
# <<<--- 新增导入：导入我们刚刚创建的配置对话框 --- >>>
from config_dialog import ConfigDialog
import emby_handler

try:
    from douban import DoubanApi, clean_character_name_static
    DOUBAN_API_AVAILABLE = True
except ImportError:
    logger_setup.logger.error(
        "错误: douban.py 文件未找到或 DoubanApi/clean_character_name_static 类/函数无法导入。")
    DOUBAN_API_AVAILABLE = False

    class DoubanApi:
        _translation_cache = {}
        def __init__(self, *args, **kwargs): pass
        def get_acting(
            self, *args, **kwargs): return {"error": "DoubanApi not available", "cast": []}

        def close(self): pass

        @classmethod
        def _load_translation_cache(cls): cls._translation_cache = {
                                    "voice": "配音"}

        @classmethod
        def _save_translation_cache(cls): pass

    def clean_character_name_static(text): return str(text) if text else ""


class TranslationThread(QThread):  # 这个类保持不变
    translation_started = pyqtSignal(str, str, str, int)
    translation_finished = pyqtSignal(str, str, str, str, int)
    log_message_from_thread = pyqtSignal(str, str)

    def __init__(self, task_id: str, text_to_translate: str, original_text_for_signal: str, field_type: str, actor_index: int, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.text_to_translate = text_to_translate
        self.original_text_for_signal = original_text_for_signal
        self.field_type = field_type
        self.actor_index = actor_index
        self.app_parent = parent

    def run(self):
        self.translation_started.emit(
            self.task_id, self.original_text_for_signal, self.field_type, self.actor_index)
        original_emit_done = False
        final_translated_text_to_emit = None
        try:
            if not self.text_to_translate or not self.text_to_translate.strip():
                final_translated_text_to_emit = None
                original_emit_done = True
            if not original_emit_done:
                text_to_process_stripped = self.text_to_translate.strip()
                final_translated_text_to_emit = text_to_process_stripped
                skip_translation_due_to_shortness_thread = False
                if len(text_to_process_stripped) == 1 and 'A' <= text_to_process_stripped <= 'Z':
                    skip_translation_due_to_shortness_thread = True
                elif len(text_to_process_stripped) == 2 and text_to_process_stripped.isupper() and text_to_process_stripped.isalpha():
                    skip_translation_due_to_shortness_thread = True
                if skip_translation_due_to_shortness_thread:
                    self.log_message_from_thread.emit(
                        f"任务 {self.task_id}: 文本 '{text_to_process_stripped}' 是超短名/模式，跳过翻译，使用原文。", "DEBUG"
                    )
                else:
                    current_key_for_cache_thread = text_to_process_stripped
                    suffix_to_add_after_translation_thread = ""
                    if self.field_type == 'character':
                        voice_patterns = [
                            re.compile(r'\s*\(voice\)\s*$', re.IGNORECASE),
                            re.compile(r'\s*voice\s*$', re.IGNORECASE),
                        ]
                        for pattern in voice_patterns:
                            match = pattern.search(
                                current_key_for_cache_thread)
                            if match:
                                current_key_for_cache_thread = current_key_for_cache_thread[:match.start(
                                )].strip()
                                suffix_to_add_after_translation_thread = " (配音)"
                                break
                    translated_value_from_cache_thread = None
                    cache_lookup_status_thread = "MISS"
                    if not current_key_for_cache_thread.strip() and suffix_to_add_after_translation_thread:
                        final_translated_text_to_emit = "配音"
                        cache_lookup_status_thread = "SPECIAL_VOICE_ONLY"
                        self.log_message_from_thread.emit(
                            f"任务 {self.task_id}: 原文 '{self.original_text_for_signal}' 仅为(voice)模式，直接设为: '{final_translated_text_to_emit}'", "INFO"
                        )
                    elif current_key_for_cache_thread in DoubanApi._translation_cache:
                        cached_content_thread = DoubanApi._translation_cache[current_key_for_cache_thread]
                        if cached_content_thread is not None:
                            translated_value_from_cache_thread = cached_content_thread
                            final_translated_text_to_emit = translated_value_from_cache_thread + \
                                suffix_to_add_after_translation_thread
                            cache_lookup_status_thread = "HIT_TRANSLATION"
                            self.log_message_from_thread.emit(
                                f"任务 {self.task_id}: 缓存命中: '{current_key_for_cache_thread}' -> '{cached_content_thread}'. 最终使用: '{final_translated_text_to_emit}'", "SUCCESS"
                            )
                        else:
                            final_translated_text_to_emit = current_key_for_cache_thread + \
                                suffix_to_add_after_translation_thread
                            cache_lookup_status_thread = "HIT_NONE"
                            self.log_message_from_thread.emit(
                                f"任务 {self.task_id}: 缓存命中 (之前翻译失败): '{current_key_for_cache_thread}'. 使用原文+后缀: '{final_translated_text_to_emit}'", "INFO"
                            )
                                # --- 在调用 translate_text_with_translators 之前，获取引擎顺序 ---
                    engine_order_to_use = None
                    if self.app_parent and hasattr(self.app_parent, 'cfg_translator_engines_order'): # 确认老板有这个记忆
                        engine_order_to_use = self.app_parent.cfg_translator_engines_order
                        print(f"调试[批量线程]: 使用配置的引擎顺序: {engine_order_to_use}") # <--- 加个打印确认
                    else:
                        print(f"调试[批量线程]: 未能从父窗口获取引擎顺序，将使用 translate_text_with_translators 的默认后备。") # <--- 加个打印确认
                    # --- 获取引擎顺序结束 ---
                    if cache_lookup_status_thread == "MISS" and current_key_for_cache_thread.strip():
                            self.log_message_from_thread.emit(
                                f"翻译线程 {self.task_id} ({self.field_type}): 缓存里没有，准备在线翻译 '{current_key_for_cache_thread}'", "DEBUG"
                            )
                            print(f"调试演员名翻译[批量-线程]: 准备在线翻译演员名 '{current_key_for_cache_thread}' (原始字段类型: {self.field_type})") # <--- 新增
                            online_translation_result_thread = translate_text_with_translators( # <--- 修改这一行
                                current_key_for_cache_thread, 
                                "zh",
                                engine_order=engine_order_to_use # <--- 传递引擎顺序
                            )
                            print(f"调试演员名翻译[批量-线程]: 演员名 '{current_key_for_cache_thread}' 翻译结果: '{online_translation_result_thread}'") # <--- 新增
                            self.log_message_from_thread.emit(
                                        f"翻译线程 {self.task_id} ({self.field_type}): 在线翻译 '{current_key_for_cache_thread}' 的结果是: '{online_translation_result_thread}'", "DEBUG"
                                    )
                            DoubanApi._translation_cache[current_key_for_cache_thread] = online_translation_result_thread
                            if online_translation_result_thread and online_translation_result_thread.strip():
                                final_translated_text_to_emit = online_translation_result_thread.strip() + \
                                                                                                       suffix_to_add_after_translation_thread
                            else:
                                final_translated_text_to_emit = current_key_for_cache_thread + \
                                    suffix_to_add_after_translation_thread
                    elif cache_lookup_status_thread == "MISS" and not current_key_for_cache_thread.strip() and not suffix_to_add_after_translation_thread :
                        final_translated_text_to_emit = text_to_process_stripped
            original_emit_done = True
        except Exception as e:
            print(f"[线程错误] Task {self.task_id}: 翻译 '{self.text_to_translate}' 时发生异常: {e}\n{traceback.format_exc()}")
            final_translated_text_to_emit = None
            original_emit_done = True
        finally:
            if not original_emit_done:
                print(f"[线程警告] Task {self.task_id}: 线程 run 方法的 try 块结束前未标记发射，强制设为None并发射。")
                final_translated_text_to_emit = None
            self.log_message_from_thread.emit(
                f"翻译线程 {self.task_id} ({self.field_type}): 翻译任务完成，准备通知主程序。原文='{self.original_text_for_signal}', 最终翻译结果='{final_translated_text_to_emit}'", "DEBUG"
            )
            self.translation_finished.emit(self.task_id, self.original_text_for_signal, final_translated_text_to_emit, self.field_type, self.actor_index)


class JSONEditorApp(QMainWindow):
    MAX_CONCURRENT_TRANSLATION_THREADS = 3
    MAX_TRANSLATION_TASK_DURATION = 60
    WATCHDOG_TIMER_INTERVAL = 7000

    def __init__(self):
        super().__init__()
        logger_setup.AppMainWindow_instance = self
        self.root = self
        
        # +++ 给大总管一个记忆引擎顺序的地方 +++
        self.cfg_translator_engines_order = list(constants.DEFAULT_TRANSLATOR_ENGINES_ORDER) # 先用默认的填上
        # +++ 给大总管一个记忆国产片是否用在线API的地方 +++
        self.cfg_domestic_source_mode = constants.DEFAULT_DOMESTIC_SOURCE_MODE
        # <<<--- 修改：窗口标题可以稍微更新一下版本号，表示开发中 --- >>>
        self.setWindowTitle(f"神TMD小工具 v7.4 (By Aqr-K)")
        self.setGeometry(100, 100, 1050, 650) # 窗口大小和位置


        # --- 新增/修改：用于存储从 config.ini 加载的配置值的属性 ---
        self.cfg_main_cache_path = os.path.expanduser(constants.FALLBACK_DEFAULT_MAIN_CACHE_PATH) # 主缓存目录路径
        self.cfg_override_cache_path = ""  # 覆盖缓存目录路径，默认为空
        self.cfg_emby_server_url = ""      # Emby 服务器 URL
        self.cfg_emby_api_key = ""         # Emby API Key
        self.cfg_enable_emby_item_refresh = constants.DEFAULT_ENABLE_EMBY_ITEM_REFRESH
        self.cfg_tmdb_api_key = constants.FALLBACK_TMDB_API_KEY # 使用默认回退值初始化
        # 豆瓣 API 冷却时间相关的配置属性 (这些之前可能就有，但确保它们存在)
        self.cfg_default_api_cooldown_seconds = constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK
        self.cfg_max_api_cooldown_seconds = constants.MAX_API_COOLDOWN_SECONDS_FALLBACK
        self.cfg_cooldown_increment_seconds = constants.COOLDOWN_INCREMENT_SECONDS_FALLBACK
        # --- 配置属性结束 ---

        # 当前API冷却时间 (毫秒)，会根据配置初始化
        self.current_api_cooldown_ms = self.cfg_default_api_cooldown_seconds * 1000

        # 其他属性保持不变
        self.mtype = None
        self.batch_media_dirs_to_process = []
        self.current_batch_index = 0
        self.batch_processing_active = False
        self.batch_stats = {
            "total_media_processed_successfully": 0, "total_media_skipped_due_to_cache": 0,
            "total_media_failed_to_extract_info": 0, "total_media_api_call_failed_early": 0,
            "total_media_role_name_issue": 0, "total_media_skipped_due_to_precheck": 0,
            "total_media_skipped_due_to_no_local_cast": 0, "total_media_directly_translated": 0,
            "total_json_files_processed_and_saved": 0, "total_json_files_with_role_updates": 0,
            "total_json_files_failed_to_process": 0
        }
        self.batch_start_time = None
        self.failed_media_items = []
        self.last_batch_failed_items_log_path = None
        self.failure_window_qt_instance = None

        self.log_buffer = []
        self.log_update_timer = QTimer(self)
        self.log_update_timer.timeout.connect(self._flush_log_buffer_to_gui)
        self.log_update_timer.start(100)
        self.max_log_lines = 3000

        self.current_translation_threads = []
        self.current_item_translation_buffer = {}
        self.current_item_pending_translations = 0
        self.current_item_context_for_async_processing = {}
        self.launched_translation_tasks_info = set()
        self.translation_task_queue = []

        self.translation_watchdog_timer = QTimer(self)
        self.translation_watchdog_timer.timeout.connect(self._check_stalled_translation_tasks)
        self.active_translation_task_details = {}

        self._init_ui()  # 初始化UI元素
        self.api_type_label.setText("---")
        self._connect_signals() # 连接信号和槽

        if hasattr(self, 'statusBar') and callable(self.statusBar):
            logger_setup.logger.set_status_bar(self.statusBar())

        # <<<--- 修改：现在 load_app_config 会加载所有配置，包括新的路径和Emby设置 --- >>>
        self.load_app_config()
        self.processed_media_set = self.load_processed_log()

        # Douban API 初始化 (保持不变)
        self._douban_api_instance = None
        if DOUBAN_API_AVAILABLE:
            try: self._douban_api_instance = DoubanApi()
            except Exception as e:
                self.log_to_progress_text_qt(f"初始化 DoubanApi 失败: {e}", "ERROR")
                self._douban_api_instance = DoubanApi()
        else: self._douban_api_instance = DoubanApi()

        is_real_api_instance = DOUBAN_API_AVAILABLE and \
                               self._douban_api_instance is not None and \
                               isinstance(self._douban_api_instance, DoubanApi) and \
                               (type(self._douban_api_instance).__module__ == 'douban' if DOUBAN_API_AVAILABLE else True)

        if not is_real_api_instance:
            if hasattr(self, 'process_button'): self.process_button.setEnabled(False)
            self.log_to_progress_text_qt("DoubanAPI 未能有效初始化，处理功能禁用。", "ERROR")
        else:
            if hasattr(self, 'process_button'): self.process_button.setEnabled(True)
            self.log_to_progress_text_qt("DoubanAPI 初始化成功。", "INFO")

        self._toggle_file_type_options() # 根据复选框状态更新UI
        self.log_to_progress_text_qt(f"神TMD小工具 (v{self.windowTitle().split('v')[-1].split(' (')[0]}) 已就绪。", "INFO")
        self.load_app_config() # <--- 这行会在后面读取真正的配置来更新上面的记忆


    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- <<< 修改：移除旧的目录选择框和按钮 >>> ---
        # 原来的 dir_frame, dir_label, self.dir_entry, self.dir_browse_button 都被删除了
        # 因为目录配置将通过新的配置对话框进行
        # --- 移除结束 ---

        # API 参数显示区域 (保持不变)
        api_params_groupbox = QFrame()
        api_params_groupbox.setFrameShape(QFrame.Shape.StyledPanel)
        api_params_layout = QGridLayout(api_params_groupbox)
        api_params_layout.addWidget(QLabel("接口:"), 0, 0)
        self.api_type_label = QLabel("---")
        api_params_layout.addWidget(self.api_type_label, 0, 1)
        api_params_layout.addWidget(QLabel("类型:"), 0, 2)
        self.auto_media_type_label = QLabel("---")
        api_params_layout.addWidget(self.auto_media_type_label, 0, 3)
        api_params_layout.addWidget(QLabel("名称:"), 1, 0)
        self.auto_name_display = QLineEdit("---")
        self.auto_name_display.setReadOnly(True)
        api_params_layout.addWidget(self.auto_name_display, 1, 1, 1, 3)
        api_params_layout.setColumnStretch(1, 1)
        main_layout.addWidget(api_params_groupbox)

        # 选项区域 (大部分保持不变，但会调整按钮布局)
        options_groupbox = QFrame()
        options_groupbox.setFrameShape(QFrame.Shape.StyledPanel)
        options_main_layout = QVBoxLayout(options_groupbox)
        options_main_layout.setContentsMargins(5,5,5,5)

        # <<<--- 新增：处理选项的总标签 --- >>>
        processing_type_label = QLabel("<b>处理选项:</b>")
        options_main_layout.addWidget(processing_type_label)

        file_types_outer_layout = QVBoxLayout() # 用于组织多行文件类型和图片选项

        # 电影行 (后面增加 "下载图片" 复选框)
        movie_row_layout = QHBoxLayout()
        self.cb_process_movie = QCheckBox("电影(all.json)")
        self.cb_process_movie.setChecked(True)
        movie_row_layout.addWidget(self.cb_process_movie)
        self.cb_download_movie_images = QCheckBox("下载图片") # 新增
        self.cb_download_movie_images.setChecked(True)    # 默认不勾选
        movie_row_layout.addWidget(self.cb_download_movie_images)
        movie_row_layout.addStretch(1) # 添加弹性空间，将后续内容推到右边 (如果还有的话)
        file_types_outer_layout.addLayout(movie_row_layout)

        # 剧集行
        series_row_layout = QHBoxLayout()
        self.cb_process_series = QCheckBox("剧集(series.json)")
        self.cb_process_series.setChecked(True)
        series_row_layout.addWidget(self.cb_process_series)
        self.cb_download_series_images = QCheckBox("下载图片") # 新增
        self.cb_download_series_images.setChecked(True)
        series_row_layout.addWidget(self.cb_download_series_images)
        series_row_layout.addStretch(1)
        file_types_outer_layout.addLayout(series_row_layout)

        # 季行
        season_row_layout = QHBoxLayout()
        self.cb_process_season = QCheckBox("季(season-*.json)")
        self.cb_process_season.setChecked(True)
        season_row_layout.addWidget(self.cb_process_season)
        self.cb_download_season_images = QCheckBox("下载图片") # 新增
        self.cb_download_season_images.setChecked(True)
        season_row_layout.addWidget(self.cb_download_season_images)
        season_row_layout.addStretch(1)
        file_types_outer_layout.addLayout(season_row_layout)

        # 集行
        episode_row_layout = QHBoxLayout()
        self.cb_process_episode = QCheckBox("集(episode.json)") # 注意：你的示例是 season-X-episode-Y.json
        self.cb_process_episode.setChecked(False)
        episode_row_layout.addWidget(self.cb_process_episode)
        self.cb_download_episode_images = QCheckBox("下载图片") # 新增
        self.cb_download_episode_images.setChecked(False)
        episode_row_layout.addWidget(self.cb_download_episode_images)
        episode_row_layout.addStretch(1)
        file_types_outer_layout.addLayout(episode_row_layout)

        options_main_layout.addLayout(file_types_outer_layout) # 将文件类型组添加到选项主布局

        # 添加一个小的垂直间隔
        options_main_layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))

        # 第二行选项：处理范围 和 强制重处理
        second_row_options_layout = QHBoxLayout()
        second_row_options_layout.addWidget(QLabel("处理范围:"))
        self.cb_process_domestic_only = QCheckBox("仅国产影视")
        self.cb_process_domestic_only.setChecked(True)
        second_row_options_layout.addWidget(self.cb_process_domestic_only)
        self.cb_process_foreign_only = QCheckBox("仅外语影视")
        self.cb_process_foreign_only.setChecked(False)
        second_row_options_layout.addWidget(self.cb_process_foreign_only)
        second_row_options_layout.addStretch(1) # 弹性空间
        self.cb_force_reprocess = QCheckBox("强制重处理")
        self.cb_force_reprocess.setChecked(False)
        second_row_options_layout.addWidget(self.cb_force_reprocess)
        options_main_layout.addLayout(second_row_options_layout) # 将第二行选项添加到选项主布局

        main_layout.addWidget(options_groupbox) # 将整个选项组添加到主窗口布局

        # 处理日志区域 (保持不变)
        progress_text_label = QLabel("处理日志:")
        main_layout.addWidget(progress_text_label)
        self.progress_text_edit = QTextEdit()
        self.progress_text_edit.setReadOnly(True)
        self.progress_text_edit.setFont(QFont("Consolas", 9))
        main_layout.addWidget(self.progress_text_edit, 1) # 占据剩余垂直空间

        # 底部控制按钮区域
        bottom_controls_frame = QFrame()
        bottom_layout = QHBoxLayout(bottom_controls_frame)
        bottom_layout.setContentsMargins(0,5,0,0) # 上边距5，其他0
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        bottom_layout.addWidget(self.progress_bar, 1) # 进度条占据更多横向空间
        self.progress_label = QLabel("0/0")
        self.progress_label.setMinimumWidth(60) # 给进度标签一个最小宽度
        bottom_layout.addWidget(self.progress_label)

        # <<<--- 新增：程序设置按钮 --- >>>
        self.settings_button = QPushButton("基础设置")
        bottom_layout.addWidget(self.settings_button)

        # <<<--- 修改：“开始处理”按钮的文本 --- >>>
        self.process_button = QPushButton("开始处理") # 之前是 "开始处理神医缓存目录"
        self.process_button.setStyleSheet("font-weight: bold;")
        bottom_layout.addWidget(self.process_button)
        self.stop_button = QPushButton("停止处理")
        self.stop_button.setEnabled(False)
        bottom_layout.addWidget(self.stop_button)
        self.failure_process_button = QPushButton("手动处理")
        bottom_layout.addWidget(self.failure_process_button)
        self.clear_log_button = QPushButton("清除日志")
        bottom_layout.addWidget(self.clear_log_button)
        main_layout.addWidget(bottom_controls_frame)
        self.setStatusBar(QStatusBar(self)) # 设置状态栏

    def _connect_signals(self):
        # <<<--- 移除旧的目录浏览按钮连接 --- >>>
        # self.dir_browse_button.clicked.connect(self.browse_directory_qt)

        # <<<--- 新增：连接设置按钮的点击信号到打开设置对话框的方法 --- >>>
        self.settings_button.clicked.connect(self.open_settings_dialog)

        self.process_button.clicked.connect(self.start_batch_processing)
        self.stop_button.clicked.connect(self.stop_batch_processing)
        self.failure_process_button.clicked.connect(self.open_failure_processing_window_qt)
        self.clear_log_button.clicked.connect(self.clear_progress_and_log)
        self.cb_force_reprocess.stateChanged.connect(self._toggle_file_type_options)
        self.cb_process_domestic_only.stateChanged.connect(self._on_processing_scope_changed)
        self.cb_process_foreign_only.stateChanged.connect(self._on_processing_scope_changed)

        # 连接各文件类型复选框的状态变化到 _toggle_file_type_options，以便更新对应图片下载复选框的可用性
        self.cb_process_movie.stateChanged.connect(self._toggle_file_type_options)
        self.cb_process_series.stateChanged.connect(self._toggle_file_type_options)
        self.cb_process_season.stateChanged.connect(self._toggle_file_type_options)
        self.cb_process_episode.stateChanged.connect(self._toggle_file_type_options)
    # <<<--- 新增：打开配置对话框的方法 --- >>>
    @pyqtSlot()
    def open_settings_dialog(self):
        """打开配置对话框，并在保存后重新加载配置"""
        dialog = ConfigDialog(self) # 创建 ConfigDialog 实例，父窗口是当前主窗口
        # dialog.exec() 会阻塞，直到对话框关闭
        # 如果用户点击了 "Save" (或等效按钮)，返回 QDialog.Accepted (通常是1)
        # 如果用户点击了 "Cancel" (或关闭窗口)，返回 QDialog.Rejected (通常是0)
        if dialog.exec():
            self.log_to_progress_text_qt("配置已保存。", "INFO")
            self.load_app_config() # 重新加载配置，以使更改在主程序中生效
        else:
            self.log_to_progress_text_qt("配置更改已取消。", "INFO")

    @pyqtSlot(str, str)
    def _log_message_from_worker_thread(self, message: str, level: str): # 保持不变
        self.log_to_progress_text_qt(f"[线程日志] {message}", level)

    @pyqtSlot(int)
    def _on_processing_scope_changed(self, state): # 保持不变
        sender_checkbox = self.sender()
        self.cb_process_domestic_only.stateChanged.disconnect(self._on_processing_scope_changed)
        self.cb_process_foreign_only.stateChanged.disconnect(self._on_processing_scope_changed)
        if sender_checkbox == self.cb_process_domestic_only:
            if self.cb_process_domestic_only.isChecked():
                self.cb_process_foreign_only.setChecked(False)
            elif not self.cb_process_foreign_only.isChecked():
                self.cb_process_domestic_only.setChecked(True)
        elif sender_checkbox == self.cb_process_foreign_only:
            if self.cb_process_foreign_only.isChecked():
                self.cb_process_domestic_only.setChecked(False)
            elif not self.cb_process_domestic_only.isChecked():
                self.cb_process_foreign_only.setChecked(True)
        self.cb_process_domestic_only.stateChanged.connect(self._on_processing_scope_changed)
        self.cb_process_foreign_only.stateChanged.connect(self._on_processing_scope_changed)

    # <<<--- 移除 browse_directory_qt 方法，因为它不再被主UI使用 --- >>>
    # def browse_directory_qt(self): ...

    def log_to_progress_text_qt(self, message, level="INFO"): # 保持不变
        level_upper = level.upper()
        if level_upper == "DEBUG" and not constants.DEBUG:
            return
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        color_map = {"INFO": "black", "SUCCESS": "green", "WARN": "orange", "ERROR": "red", "DEBUG": "purple"}
        text_color_name = color_map.get(level_upper, "black")
        if not isinstance(message, str):
            try: message = str(message)
            except Exception: message = "日志消息无法转换为字符串"
        if (level_upper == "DEBUG" and constants.DEBUG) or level_upper in ["ERROR", "CRITICAL"]:
             print(f"[GUI_LOG_FALLBACK] [{timestamp} {level_upper}] {message}")
        formatted_message = f"[{timestamp} {level_upper}] {message}\n"
        self.log_buffer.append((formatted_message, text_color_name))

    @pyqtSlot()
    def _flush_log_buffer_to_gui(self): # 保持不变
        if not self.log_buffer or not hasattr(self, 'progress_text_edit') or not self.progress_text_edit.isVisible():
            if not (hasattr(self, 'progress_text_edit') and self.progress_text_edit.isVisible()):
                self.log_buffer.clear()
            return
        doc = self.progress_text_edit.document()
        cursor = QTextCursor(doc)
        scrollbar = self.progress_text_edit.verticalScrollBar()
        scroll_at_bottom = (scrollbar.value() >= scrollbar.maximum() - 4)
        current_block_count = doc.blockCount()
        num_new_lines = len(self.log_buffer)
        if current_block_count + num_new_lines > self.max_log_lines:
            blocks_to_remove = min((current_block_count + num_new_lines) - self.max_log_lines, current_block_count)
            if blocks_to_remove > 0:
                delete_cursor = QTextCursor(doc)
                delete_cursor.movePosition(QTextCursor.MoveOperation.Start)
                delete_cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, blocks_to_remove)
                delete_cursor.removeSelectedText()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for message_text, color_name in self.log_buffer:
            char_format = cursor.charFormat()
            default_text_color = QApplication.palette().color(QPalette.ColorRole.Text)
            try:
                new_color = QColor(color_name)
                char_format.setForeground(new_color if new_color.isValid() else default_text_color)
            except Exception:
                char_format.setForeground(default_text_color)
            cursor.setCharFormat(char_format)
            cursor.insertText(message_text)
        self.log_buffer.clear()
        if scroll_at_bottom:
            self.progress_text_edit.ensureCursorVisible()

    def _reset_progress_bar_qt(self): # 保持不变
        if hasattr(self, 'progress_bar') and self.progress_bar:
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(False)
        if hasattr(self, 'progress_label') and self.progress_label:
            self.progress_label.setText("0/0")

    def clear_progress_and_log(self): # 保持不变
        if hasattr(self, 'progress_text_edit') and self.progress_text_edit:
            self.progress_text_edit.clear()
        self.log_buffer.clear()
        self.log_to_progress_text_qt("界面日志已清除。", "INFO")
        if hasattr(logger_setup, 'logger') and hasattr(logger_setup.logger, 'clear'):
            logger_setup.logger.clear()

    # <<<--- 修改：load_app_config 方法，以读取新的配置项 --- >>>
    def load_app_config(self):
        """加载应用程序配置，包括新的路径和Emby设置"""
        config = configparser.ConfigParser()
        base_path = get_base_path_for_files()
        config_file_path = os.path.join(base_path, constants.CONFIG_FILE)

        # 先设置所有配置属性的默认值 (从 constants.py 或直接定义)
        self.cfg_main_cache_path = os.path.expanduser(constants.FALLBACK_DEFAULT_MAIN_CACHE_PATH)
        self.cfg_override_cache_path = ""  # 默认为空字符串
        self.cfg_emby_server_url = ""
        self.cfg_emby_api_key = ""
        self.cfg_tmdb_api_key = constants.FALLBACK_TMDB_API_KEY
        self.cfg_default_api_cooldown_seconds = constants.DEFAULT_API_COOLDOWN_SECONDS_FALLBACK
        self.cfg_max_api_cooldown_seconds = constants.MAX_API_COOLDOWN_SECONDS_FALLBACK
        self.cfg_cooldown_increment_seconds = constants.COOLDOWN_INCREMENT_SECONDS_FALLBACK

        if os.path.exists(config_file_path):
            try:
                config.read(config_file_path, encoding='utf-8')

                # 读取 [Paths] 配置段
                if config.has_section(constants.CONFIG_SECTION_PATHS):
                    path_from_config = config.get(
                        constants.CONFIG_SECTION_PATHS,
                        constants.CONFIG_OPTION_MAIN_CACHE_PATH,
                        fallback=self.cfg_main_cache_path # 如果选项不存在，使用已设定的默认值
                    )
                    if path_from_config and path_from_config.strip():
                        # 扩展用户路径 (例如 ~), 但不立即检查目录是否存在
                        self.cfg_main_cache_path = os.path.expanduser(path_from_config.strip())

                    self.cfg_override_cache_path = config.get(
                        constants.CONFIG_SECTION_PATHS,
                        constants.CONFIG_OPTION_OVERRIDE_CACHE_PATH,
                        fallback="" # 默认为空
                    ).strip()

                # 读取 [Emby] 配置段
                if config.has_section(constants.CONFIG_SECTION_EMBY):
                    self.cfg_emby_server_url = config.get(
                        constants.CONFIG_SECTION_EMBY,
                        constants.CONFIG_OPTION_EMBY_SERVER_URL,
                        fallback=""
                    ).strip()
                    self.cfg_emby_api_key = config.get(
                        constants.CONFIG_SECTION_EMBY,
                        constants.CONFIG_OPTION_EMBY_API_KEY,
                        fallback=""
                    ).strip()
                # 读取新的Emby刷新选项
                    enable_item_refresh_str = config.get(
                        constants.CONFIG_SECTION_EMBY,
                        constants.CONFIG_OPTION_ENABLE_EMBY_ITEM_REFRESH,
                        fallback=str(constants.DEFAULT_ENABLE_EMBY_ITEM_REFRESH)
                    )
                    self.cfg_enable_emby_item_refresh = (enable_item_refresh_str.lower() == "true")
                else: # 如果[Emby]段不存在，确保所有相关配置使用默认值
                    self.cfg_emby_server_url = ""
                    self.cfg_emby_api_key = ""
                    self.cfg_enable_emby_item_refresh = constants.DEFAULT_ENABLE_EMBY_ITEM_REFRESH
                # --- <<< 新增：读取 [TMDB] 配置段 >>> ---
                if config.has_section(constants.CONFIG_SECTION_TMDB):
                    self.cfg_tmdb_api_key = config.get(
                        constants.CONFIG_SECTION_TMDB,
                        constants.CONFIG_OPTION_TMDB_API_KEY,
                        fallback=self.cfg_tmdb_api_key # 如果选项不存在，使用已设定的默认值
                    ).strip()
                # --- TMDB 配置段读取结束 ---

                # 读取 [API_Douban] 配置段 (豆瓣API冷却)
                # 确保使用在 constants.py 中为豆瓣API段确定的常量名
                if config.has_section(constants.CONFIG_SECTION_API_DOUBAN):
                    self.cfg_default_api_cooldown_seconds = config.getint(
                        constants.CONFIG_SECTION_API_DOUBAN,
                        constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN,
                        fallback=self.cfg_default_api_cooldown_seconds
                    )
                    self.cfg_max_api_cooldown_seconds = config.getint(
                        constants.CONFIG_SECTION_API_DOUBAN,
                        constants.CONFIG_OPTION_DOUBAN_MAX_COOLDOWN,
                        fallback=self.cfg_max_api_cooldown_seconds
                    )
                    self.cfg_cooldown_increment_seconds = config.getint(
                        constants.CONFIG_SECTION_API_DOUBAN,
                        constants.CONFIG_OPTION_DOUBAN_INCREMENT_COOLDOWN,
                        fallback=self.cfg_cooldown_increment_seconds
                    )
                                        # --- 读取翻译引擎顺序的配置 ---
                    # self.cfg_translator_engines_order 已经在 __init__ 中用默认值初始化了
                    if config.has_section(constants.CONFIG_SECTION_TRANSLATION):
                        engines_str = config.get(
                            constants.CONFIG_SECTION_TRANSLATION,
                            constants.CONFIG_OPTION_TRANSLATOR_ENGINES,
                            fallback="" # 如果配置文件里没有这一项，就返回空字符串
                        )
                        if engines_str.strip(): # 如果从配置文件读到了内容 (不是空的)
                            # 按逗号分割，并去掉每个引擎名两边的空格，同时确保引擎是有效的
                            valid_engines_from_config = [
                                e.strip() for e in engines_str.split(',') 
                                if e.strip() and e.strip() in constants.AVAILABLE_TRANSLATOR_ENGINES
                            ]
                            if valid_engines_from_config: # 如果解析后得到了有效的引擎列表
                                self.cfg_translator_engines_order = valid_engines_from_config
                    # --- 读取国产影视数据源配置 ---
                    if config.has_section(constants.CONFIG_SECTION_DOMESTIC_SOURCE):
                        self.cfg_domestic_source_mode = config.get(
                            constants.CONFIG_SECTION_DOMESTIC_SOURCE,
                            constants.CONFIG_OPTION_DOMESTIC_SOURCE_MODE,
                            fallback=constants.DEFAULT_DOMESTIC_SOURCE_MODE
                        )
                    else: # 如果配置段或选项不存在，确保使用默认值
                        self.cfg_domestic_source_mode = constants.DEFAULT_DOMESTIC_SOURCE_MODE
                    self.log_to_progress_text_qt(f"  国产影视豆瓣数据源策略: {self.cfg_domestic_source_mode}", "DEBUG")
                    # --- 国产影视数据源配置读取结束 ---

            except Exception as e:
                self.log_to_progress_text_qt(f"读取配置文件 '{config_file_path}' 失败: {e}", "ERROR")

        # 更新当前API冷却时间 (毫秒)
        self.current_api_cooldown_ms = self.cfg_default_api_cooldown_seconds * 1000

        # 日志记录加载的配置
        self.log_to_progress_text_qt(f"配置已加载。", "DEBUG")
        self.log_to_progress_text_qt(f"  主缓存目录: '{self.cfg_main_cache_path}'", "DEBUG")
        self.log_to_progress_text_qt(f"  翻译引擎顺序: {', '.join(self.cfg_translator_engines_order)}", "DEBUG") # <--- 确保这条日志能打印出顺序
        if self.cfg_override_cache_path:
            self.log_to_progress_text_qt(f"  覆盖缓存目录: '{self.cfg_override_cache_path}'", "DEBUG")
        else:
            self.log_to_progress_text_qt(f"  覆盖缓存目录: 未配置 (将保存回主缓存)", "DEBUG")
        if self.cfg_emby_server_url and self.cfg_emby_api_key:
            self.log_to_progress_text_qt(f"  Emby 服务器: '{self.cfg_emby_server_url}' (API Key 已配置)", "DEBUG")
            self.log_to_progress_text_qt(f"  处理后自动刷新单个Emby项目: {self.cfg_enable_emby_item_refresh}", "DEBUG")
        elif self.cfg_emby_server_url:
            self.log_to_progress_text_qt(f"  Emby 服务器: '{self.cfg_emby_server_url}' (API Key 未配置)", "DEBUG")
        else:
            self.log_to_progress_text_qt(f"  Emby: 未配置", "DEBUG")
        # --- <<< 新增：记录TMDB API Key配置状态 >>> ---
        if self.cfg_tmdb_api_key:
            self.log_to_progress_text_qt(f"  TMDB API Key: 已配置", "DEBUG")
        else:
            self.log_to_progress_text_qt(f"  TMDB API Key: 未配置", "DEBUG")
      # --- TMDB API Key 日志记录结束 ---
        self.log_to_progress_text_qt(f"  豆瓣API冷却: 默认={self.cfg_default_api_cooldown_seconds}s, 最大={self.cfg_max_api_cooldown_seconds}s, 增量={self.cfg_cooldown_increment_seconds}s", "DEBUG")


    def load_processed_log(self): # 保持不变
        log_set = set()
        log_file_path = os.path.join(get_base_path_for_files(), constants.PROCESSED_MEDIA_LOG_FILE)
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped_line = line.strip()
                        if stripped_line: log_set.add(stripped_line)
                self.log_to_progress_text_qt(f"已加载 {len(log_set)} 个已处理媒体记录。", "INFO")
            except Exception as e:
                self.log_to_progress_text_qt(f"读取已处理记录文件 '{log_file_path}' 失败: {e}", "ERROR")
        return log_set

    def save_to_processed_log(self, media_type, tmdb_id_str): # 保持不变
        item_key = create_media_item_key(media_type, tmdb_id_str)
        if item_key is None:
            self.log_to_progress_text_qt(f"无法保存已处理记录：媒体类型或TMDB ID无效 (类型: {media_type}, ID: {tmdb_id_str})。", "ERROR")
            return
        log_file_path = os.path.join(get_base_path_for_files(), constants.PROCESSED_MEDIA_LOG_FILE)
        try:
            with open(log_file_path, 'a', encoding='utf-8') as f: f.write(f"{item_key}\n")
            self.processed_media_set.add(item_key)
        except Exception as e:
            self.log_to_progress_text_qt(f"保存已处理记录到 '{log_file_path}' 失败: {e}", "ERROR")

    def _get_cast_from_json_data_helper(self, data): # 保持不变
        if "credits" in data and isinstance(data.get("credits"), dict) and \
           "cast" in data["credits"] and isinstance(data["credits"].get("cast"), list):
            return data["credits"]["cast"]
        elif "casts" in data and isinstance(data.get("casts"), dict) and \
             "cast" in data["casts"] and isinstance(data["casts"].get("cast"), list):
            return data["casts"]["cast"]
        elif "guest_stars" in data and isinstance(data.get("guest_stars"), list):
            return data["guest_stars"]
        elif "cast" in data and isinstance(data.get("cast"), list):
            return data["cast"]
        return None

    def _extract_full_info_from_local_json(self, json_filepath): # 保持不变
        if not json_filepath or not os.path.exists(json_filepath):
            return None, None, None, None, None
        try:
            with open(json_filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            title = data.get("title", data.get("name", ""))
            original_title = data.get("original_title", data.get("original_name", title))
            original_language = data.get("original_language", None)
            if not original_language and "languages" in data and isinstance(data.get("languages"), list) and data["languages"]:
                original_language = data["languages"][0]
            cast_list = self._get_cast_from_json_data_helper(data)
            media_type_from_json = None
            # 尝试从JSON内容判断媒体类型 (更可靠)
            tmdb_id_from_json = data.get("id", None) # TMDb ID
            # 电影通常有 "release_date", 电视剧有 "first_air_date"
            if "name" in data and ("first_air_date" in data or "seasons" in data): media_type_from_json = "tv"
            elif "title" in data and "release_date" in data: media_type_from_json = "movie"

            return title, original_title, original_language, cast_list, media_type_from_json
        except Exception as e:
            self.log_to_progress_text_qt(f"从本地JSON '{os.path.basename(json_filepath)}' 提取完整信息失败: {e}", "ERROR")
            return None, None, None, None, None

    def _check_cast_roles_in_json_file(self, json_filepath, cast_list_to_check=None): # 保持不变
        cast_list = cast_list_to_check
        file_basename_for_log = os.path.basename(json_filepath) if json_filepath else "提供的演员列表"
        if cast_list is None:
            if not json_filepath or not os.path.exists(json_filepath):
                self.log_to_progress_text_qt(f"预检JSON文件路径无效或文件不存在: {json_filepath}", "WARN"); return False, False, 0, 0
            try:
                with open(json_filepath, 'r', encoding='utf-8') as f: data = json.load(f)
                cast_list = self._get_cast_from_json_data_helper(data)
            except FileNotFoundError:
                self.log_to_progress_text_qt(f"预检JSON文件未找到: {json_filepath}", "WARN"); return False, False, 0, 0
            except json.JSONDecodeError:
                self.log_to_progress_text_qt(f"预检JSON文件解析失败: {json_filepath}", "WARN"); return False, False, 0, 0
            except Exception as e:
                self.log_to_progress_text_qt(f"预检JSON '{file_basename_for_log}' 时发生未知错误: {e}", "ERROR")
                self.log_to_progress_text_qt(f"Traceback for precheck error: {traceback.format_exc()}", "ERROR")
                return False, False, 0, 0
        if not cast_list:
            return True, False, 0, 0 # 无演员列表，算作“跳过API”，但不是因为角色名OK
        valid_chinese_roles = 0; non_empty_roles = 0
        for actor in cast_list:
            if isinstance(actor, dict):
                character_name = actor.get("character")
                actor_name = actor.get("name", "未知演员")
                if character_name is not None: # 角色名可以是空字符串
                    if str(character_name).strip(): non_empty_roles +=1
                    if is_role_name_valid(character_name, actor_name): valid_chinese_roles += 1
        if non_empty_roles == 0: # 如果所有角色名都是空或空白
            return False, False, valid_chinese_roles, non_empty_roles # 不跳过API，因为没有有效角色名
        should_skip_api_due_to_names_ok = (valid_chinese_roles == non_empty_roles)
        return False, should_skip_api_due_to_names_ok, valid_chinese_roles, non_empty_roles

    def _is_media_id_directory(self, dir_path): # 保持不变
        if not os.path.isdir(dir_path): return False
        dir_name = os.path.basename(dir_path)
        # 假设TMDb ID命名的文件夹是纯数字 (或者你可以用更复杂的正则匹配，比如 "123" 或 "tv-123")
        if dir_name.isdigit(): # 或者你的刮削器可能生成如 "movie-12345" "tv-67890" 这样的文件夹名
            # 检查关键json文件是否存在
            if os.path.exists(os.path.join(dir_path, "all.json")) or \
               os.path.exists(os.path.join(dir_path, "series.json")):
                return True
        return False

    def extract_info_from_media_directory(self, media_dir_path): # 保持不变
        if not media_dir_path or not os.path.isdir(media_dir_path):
            if hasattr(self, 'auto_name_display'): self.auto_name_display.setText(f"{os.path.basename(media_dir_path)}: 无效")
            if hasattr(self, 'auto_media_type_label'): self.auto_media_type_label.setText("未知"); self.mtype = None
            return None, None, None # 返回三个None
        extracted_title, extracted_year, detected_mtype, extracted_imdb_id = "", "", None, None
        main_json_data = None # 用于存储从主JSON文件读取的数据
        series_json_path = os.path.join(media_dir_path, "series.json")
        if os.path.exists(series_json_path):
            try:
                with open(series_json_path, 'r', encoding='utf-8') as f: main_json_data = json.load(f)
                extracted_title = main_json_data.get("name", main_json_data.get("original_name", ""))
                date_str = main_json_data.get("first_air_date", main_json_data.get("air_date", ""))
                extracted_year = date_str[:4] if date_str and len(date_str) >= 4 else ""
                if extracted_title: detected_mtype = "tv" # 如果有标题，则认为是电视剧
            except Exception as e:
                self.log_to_progress_text_qt(f"读取或解析 {os.path.basename(series_json_path)} 失败: {e}", "ERROR")
        if not detected_mtype: # 如果不是电视剧，再检查是否为电影
            all_json_path = os.path.join(media_dir_path, "all.json")
            if os.path.exists(all_json_path):
                try:
                    with open(all_json_path, 'r', encoding='utf-8') as f: main_json_data = json.load(f)
                    extracted_title = main_json_data.get("title", main_json_data.get("original_title", ""))
                    date_str = main_json_data.get("release_date", "")
                    extracted_year = date_str[:4] if date_str and len(date_str) >= 4 else ""
                    if extracted_title: detected_mtype = "movie" # 如果有标题，则认为是电影
                except Exception as e:
                    self.log_to_progress_text_qt(f"读取或解析 {os.path.basename(all_json_path)} 失败: {e}", "ERROR")
        if main_json_data: # 如果成功读取了主JSON数据
            # 尝试提取IMDB ID
            extracted_imdb_id = main_json_data.get("imdb_id", "").strip() or \
                                (main_json_data.get("external_ids", {}).get("imdb_id", "")).strip() or None
            if extracted_imdb_id and not extracted_imdb_id.startswith("tt"): # 确保IMDB ID以 "tt" 开头
                extracted_imdb_id = None # 如果格式不对，则设为None

        display_name = extracted_title if extracted_title else "未知标题"
        display_year = extracted_year if extracted_year else "未知年份"
        if hasattr(self, 'auto_name_display'): self.auto_name_display.setText(f"{display_name} ({display_year})")
        if detected_mtype == "movie":
            if hasattr(self, 'auto_media_type_label'): self.auto_media_type_label.setText("电影")
            self.mtype = "movie"
        elif detected_mtype == "tv":
            if hasattr(self, 'auto_media_type_label'): self.auto_media_type_label.setText("电视剧")
            self.mtype = "tv"
        else:
            if hasattr(self, 'auto_media_type_label'): self.auto_media_type_label.setText("未知类型")
            self.mtype = None
            return None, None, None # 如果无法确定类型，返回三个None
        return extracted_title, extracted_year, extracted_imdb_id

    # 在 app_window_qt.py 中
    def _get_json_files_for_media_item(self, media_dir_path, media_type, force_get_all_relevant_types=False): # 新增参数
        refined_json_files = []
        
        # --- 调试日志 (保持或按需移除) ---
        self.log_to_progress_text_qt(
            f"[DEBUG_GET_JSON] Called for: {os.path.basename(media_dir_path)}, Type: {media_type}, ForceAll: {force_get_all_relevant_types}", "DEBUG"
        )
        # ... (原有的获取UI复选框状态的日志可以保留，用于对比)

        # 获取UI复选框的状态，但如果 force_get_all_relevant_types 为 True，则覆盖它们
        process_movie_json = self.cb_process_movie.isChecked() if not force_get_all_relevant_types else True
        process_series_json = self.cb_process_series.isChecked() if not force_get_all_relevant_types else True
        process_season_json = self.cb_process_season.isChecked() if not force_get_all_relevant_types else True
        process_episode_json = self.cb_process_episode.isChecked() if not force_get_all_relevant_types else True

        if force_get_all_relevant_types:
            self.log_to_progress_text_qt(f"[DEBUG_GET_JSON] 强制获取所有相关类型文件，忽略UI勾选。", "DEBUG")

        # ... 后续的 if media_type == "movie": 和 elif media_type == "tv": 逻辑保持不变 ...
        # 它们现在会使用上面可能被 force_get_all_relevant_types 覆盖的 process_xxx_json 变量

        # 例如:
        if media_type == "movie":
            if process_movie_json: # 如果 force_get_all_relevant_types 为 True, 这个 process_movie_json 也为 True
                f = os.path.join(media_dir_path, "all.json")
                if os.path.exists(f):
                    refined_json_files.append(f)
                    log_suffix = "(基于UI勾选)" if not force_get_all_relevant_types else "(强制获取)"
                    self.log_to_progress_text_qt(f"文件收集：添加电影JSON '{os.path.basename(f)}' {log_suffix}。", "DEBUG")
        elif media_type == "tv":
            if process_series_json:
                f_series = os.path.join(media_dir_path, "series.json")
                if os.path.exists(f_series):
                    refined_json_files.append(f_series)
                    log_suffix = "(基于UI勾选)" if not force_get_all_relevant_types else "(强制获取)"
                    self.log_to_progress_text_qt(f"文件收集：添加剧集JSON '{os.path.basename(f_series)}' {log_suffix}。", "DEBUG")

            if process_season_json:
                season_files_found = []
                potential_season_files = glob.glob(os.path.join(media_dir_path, "season-*.json"))
                for season_file_path in potential_season_files:
                    if os.path.isfile(season_file_path) and "episode" not in os.path.basename(season_file_path).lower():
                        season_files_found.append(season_file_path)
                if season_files_found:
                    refined_json_files.extend(season_files_found)
                    log_suffix = "(基于UI勾选)" if not force_get_all_relevant_types else "(强制获取)"
                    self.log_to_progress_text_qt(f"文件收集：添加 {len(season_files_found)} 个季JSON文件 {log_suffix}。", "DEBUG")
            
            if process_episode_json: # 注意：这里的逻辑是收集所有类型的集文件
                episode_files_found = []
                for item_name in os.listdir(media_dir_path):
                    item_path = os.path.join(media_dir_path, item_name)
                    if os.path.isdir(item_path) and item_name.lower().startswith("season"):
                        episode_json_path_in_subdir = os.path.join(item_path, "episode.json")
                        if os.path.exists(episode_json_path_in_subdir):
                            episode_files_found.append(episode_json_path_in_subdir)
                    elif os.path.isfile(item_path) and \
                         item_name.lower().startswith("season-") and \
                         "episode-" in item_name.lower() and \
                         item_name.lower().endswith(".json"):
                        episode_files_found.append(item_path)
                if episode_files_found:
                    refined_json_files.extend(episode_files_found)
                    log_suffix = "(基于UI勾选)" if not force_get_all_relevant_types else "(强制获取)"
                    self.log_to_progress_text_qt(f"文件收集：添加 {len(episode_files_found)} 个集JSON文件 {log_suffix}。", "DEBUG")
        
        if not refined_json_files:
            log_suffix = "(基于UI勾选)" if not force_get_all_relevant_types else "(强制获取模式)"
            self.log_to_progress_text_qt(f"文件收集：对于媒体 '{os.path.basename(media_dir_path)}' (类型: {media_type})，未收集到任何JSON文件 {log_suffix}。", "DEBUG")
            
        return list(set(refined_json_files))    
    # app_window_qt.py

    def _update_characters_in_this_json(self, json_data, cast_list_to_use):
        self.log_to_progress_text_qt(
            f"[DEBUG_UPDATE_JSON] Method _update_characters_in_this_json called.", "DEBUG"
        )
        if not cast_list_to_use: # 如果期望的演员列表为空
            self.log_to_progress_text_qt("[DEBUG_UPDATE_JSON] cast_list_to_use is empty. Clearing existing cast in JSON if any.", "DEBUG")
            # 根据需求，如果 cast_list_to_use 为空，可以选择清空JSON中的演员列表
            # 或者保持不变。这里我们假设如果提供了空列表，意味着要清空。
            if "credits" in json_data and "cast" in json_data.get("credits", {}):
                json_data["credits"]["cast"] = []
                return json_data, 0, 0 # 返回0更新，因为是清空
            elif "casts" in json_data and "cast" in json_data.get("casts", {}):
                 json_data["casts"]["cast"] = []
                 return json_data, 0, 0
            elif "cast" in json_data and isinstance(json_data.get("cast"), list):
                 json_data["cast"] = []
                 return json_data, 0, 0
            elif "guest_stars" in json_data and isinstance(json_data.get("guest_stars"), list):
                 json_data["guest_stars"] = []
                 return json_data, 0, 0
            return json_data, 0, 0 # 没有可清空的演员列表

        # 确保 cast_list_to_use 中的每个演员条目都有必要的字段，并且格式正确
        # (这一步在 format_tmdb_person_to_cast_entry 和 failure_window_qt 的编辑逻辑中应已保证)
        
        # 直接用 cast_list_to_use 替换 JSON 中的演员列表
        # 需要找到正确的键 (credits.cast, casts.cast, cast, guest_stars)
        
        original_cast_len = 0
        updated_cast_len = len(cast_list_to_use)
        structure_updated = False

        if "credits" in json_data and isinstance(json_data.get("credits"), dict):
            if "cast" in json_data["credits"]:
                original_cast_len = len(json_data["credits"]["cast"])
            json_data["credits"]["cast"] = cast_list_to_use
            structure_updated = True
            self.log_to_progress_text_qt(f"[DEBUG_UPDATE_JSON] Replaced json_data['credits']['cast'] with {updated_cast_len} new actors.", "DEBUG")
        elif "casts" in json_data and isinstance(json_data.get("casts"), dict):
            if "cast" in json_data["casts"]:
                original_cast_len = len(json_data["casts"]["cast"])
            json_data["casts"]["cast"] = cast_list_to_use
            structure_updated = True
            self.log_to_progress_text_qt(f"[DEBUG_UPDATE_JSON] Replaced json_data['casts']['cast'] with {updated_cast_len} new actors.", "DEBUG")
        elif "cast" in json_data and isinstance(json_data.get("cast"), list):
            original_cast_len = len(json_data["cast"])
            json_data["cast"] = cast_list_to_use
            structure_updated = True
            self.log_to_progress_text_qt(f"[DEBUG_UPDATE_JSON] Replaced json_data['cast'] with {updated_cast_len} new actors.", "DEBUG")
        elif "guest_stars" in json_data and isinstance(json_data.get("guest_stars"), list): # 通常用于剧集
            original_cast_len = len(json_data["guest_stars"])
            json_data["guest_stars"] = cast_list_to_use
            structure_updated = True
            self.log_to_progress_text_qt(f"[DEBUG_UPDATE_JSON] Replaced json_data['guest_stars'] with {updated_cast_len} new actors.", "DEBUG")
        else:
            self.log_to_progress_text_qt("[DEBUG_UPDATE_JSON] No known cast structure (credits.cast, casts.cast, cast, guest_stars) found in json_data. Cannot update.", "WARN")
            return json_data, 0, 0 # 无法更新

        # 简单计算更新数量：这里我们认为整个列表被替换就算一次“角色更新”
        # 更精确的计数需要对比前后列表的内容差异
        names_updated_count = updated_cast_len # 简化计数
        chars_updated_count = updated_cast_len # 简化计数
        
        if original_cast_len == updated_cast_len:
            # 如果长度没变，可以进一步比较内容来确定实际更新了多少。
            # 但对于直接替换策略，只要列表不同就视为更新。
            # 为简化，我们这里不深入比较每个字段。
            pass

        self.log_to_progress_text_qt(f"[DEBUG_UPDATE_JSON] JSON cast updated. Original length: {original_cast_len}, New length: {updated_cast_len}", "DEBUG")
        return json_data, names_updated_count, chars_updated_count
    def _toggle_file_type_options(self): # 保持不变，但现在也控制图片下载复选框的可用性
        is_forced = self.cb_force_reprocess.isChecked()
        log_message = "强制重新处理已启用。" if is_forced else "强制重新处理已禁用。"
        self.log_to_progress_text_qt(log_message, "INFO")

        # 根据主文件类型复选框的状态，启用/禁用对应的图片下载复选框
        self.cb_download_movie_images.setEnabled(self.cb_process_movie.isChecked())
        self.cb_download_series_images.setEnabled(self.cb_process_series.isChecked())
        self.cb_download_season_images.setEnabled(self.cb_process_season.isChecked())
        self.cb_download_episode_images.setEnabled(self.cb_process_episode.isChecked())


    @pyqtSlot()
    def open_failure_processing_window_qt(self):
        if hasattr(self, 'failure_window_qt_instance') and \
           self.failure_window_qt_instance and \
           self.failure_window_qt_instance.isVisible():
            self.failure_window_qt_instance.raise_()
            self.failure_window_qt_instance.activateWindow()
            self.log_to_progress_text_qt("失败处理窗口已打开。", "INFO")
            return

        failure_log_path_to_load = None # 初始化为 None
        fixed_log_path = os.path.join(get_base_path_for_files(), constants.FIXED_FAILURE_LOG_FILENAME)

        # 1. 尝试自动加载固定的失败日志文件
        if os.path.exists(fixed_log_path) and os.path.getsize(fixed_log_path) > 0:
            self.log_to_progress_text_qt(f"自动加载默认失败日志: {fixed_log_path}", "INFO")
            failure_log_path_to_load = fixed_log_path
            self.last_batch_failed_items_log_path = failure_log_path_to_load # 更新记录，即使是自动加载
        else:
            # 文件不存在或为空，failure_log_path_to_load 保持为 None
            if os.path.exists(fixed_log_path):
                 self.log_to_progress_text_qt(f"默认失败日志 '{fixed_log_path}' 为空。将打开空的手动处理窗口。", "INFO")
            else:
                 self.log_to_progress_text_qt(f"未找到默认失败日志 '{fixed_log_path}'。将打开空的手动处理窗口。", "INFO")
        
        # 2. (已移除) 不再提示用户手动选择文件。

        # 3. 如果 failure_log_path_to_load 有值 (即找到了有效的日志文件)，则进行额外检查。
        #    如果 failure_log_path_to_load 是 None，则跳过此块。
        if failure_log_path_to_load:
            # 这个检查理论上是多余的，因为上面的逻辑已经保证了这一点，但作为双重保险
            if not os.path.exists(failure_log_path_to_load):
                # 这种情况非常罕见，除非文件在检查和使用之间被删除
                QMessageBox.critical(self, "内部错误", f"尝试加载的日志文件突然消失了。\n路径: {failure_log_path_to_load}")
                self.log_to_progress_text_qt(f"内部错误：日志文件 '{failure_log_path_to_load}' 在加载前消失。", "ERROR")
                return
            if os.path.getsize(failure_log_path_to_load) == 0:
                # 这种情况也非常罕见，因为上面已经检查过 getsize() > 0
                QMessageBox.information(self, "提示", f"日志文件突然变为空：\n{failure_log_path_to_load}\n将打开空窗口。")
                self.log_to_progress_text_qt(f"日志文件 '{failure_log_path_to_load}' 突然变为空，将打开空窗口。", "INFO")
                failure_log_path_to_load = None # 如果文件变空了，也当作没有文件加载

        # 4. 打开失败处理窗口
        # 此时 failure_log_path_to_load 要么是有效的、有内容的日志文件路径，要么是 None
        if failure_log_path_to_load:
            self.log_to_progress_text_qt(f"准备打开失败处理窗口，加载文件: {failure_log_path_to_load}", "INFO")
        else:
            self.log_to_progress_text_qt(f"准备打开空的手动处理窗口 (无失败日志加载)。", "INFO")

        self.failure_window_qt_instance = FailureProcessingWindowQt(self, self, failure_log_path_to_load)
        self.failure_window_qt_instance.show()
    @pyqtSlot()
    def stop_batch_processing(self): # 保持不变
        if self.batch_processing_active:
            self.log_to_progress_text_qt("用户请求停止批量处理...", "WARN")
            self.batch_processing_active = False
            if hasattr(self, 'batch_timer') and self.batch_timer.isActive():
                self.batch_timer.stop()
                self.log_to_progress_text_qt("批处理定时器已停止。", "DEBUG")
            if hasattr(self, 'translation_watchdog_timer') and self.translation_watchdog_timer.isActive():
                self.translation_watchdog_timer.stop()
                self.log_to_progress_text_qt("翻译看门狗定时器已停止。", "DEBUG")
            self.translation_task_queue.clear()
            self.log_to_progress_text_qt("翻译任务队列已清空。", "DEBUG")
            if self.current_translation_threads:
                self.log_to_progress_text_qt(f"尝试停止 {len(self.current_translation_threads)} 个正在运行的翻译线程...", "DEBUG")
                threads_to_attempt_stop = list(self.current_translation_threads)
                for thread_instance in threads_to_attempt_stop:
                    if thread_instance.isRunning():
                        thread_instance.quit()
                        if not thread_instance.wait(300):
                            thread_instance.terminate()
                            task_id_str = thread_instance.task_id if hasattr(thread_instance, 'task_id') else "未知任务"
                            self.log_to_progress_text_qt(f"翻译线程 {task_id_str} 被强制终止。", "WARN")
                    if thread_instance in self.current_translation_threads:
                        self.current_translation_threads.remove(thread_instance)
                if self.current_translation_threads:
                    self.log_to_progress_text_qt(f"停止后，仍有 {len(self.current_translation_threads)} 个线程在列表中，执行最终清空。", "DEBUG")
                    self.current_translation_threads.clear()
                self.log_to_progress_text_qt("翻译线程停止尝试完成。", "DEBUG")
            self.active_translation_task_details.clear()
            self.current_item_pending_translations = 0
            self.current_item_translation_buffer.clear()
            self.current_item_context_for_async_processing.clear()
            if hasattr(self, 'process_button'):
                self.process_button.setEnabled(True)
                self.process_button.setText("开始处理") # 修改按钮文本
            if hasattr(self, 'stop_button'):
                self.stop_button.setEnabled(False)
            self._finalize_batch_processing(interrupted=True)
            QCoreApplication.processEvents()
        else:
            if hasattr(self, 'stop_button'):
                self.stop_button.setEnabled(False)
            if hasattr(self, 'process_button') and not self.process_button.isEnabled():
                 self.process_button.setEnabled(True)
                 self.process_button.setText("开始处理") # 修改按钮文本

    @pyqtSlot()
    def _check_stalled_translation_tasks(self): # 保持不变
        if not self.batch_processing_active or not self.active_translation_task_details:
            if self.translation_watchdog_timer.isActive():
                self.translation_watchdog_timer.stop()
            return
        now = time.time()
        media_display_name_for_log = "当前媒体项"
        media_id_for_log = "N/A"
        if self.current_item_context_for_async_processing:
            media_display_name_for_log = self.current_item_context_for_async_processing.get('api_name',
                                           self.current_item_context_for_async_processing.get('display_path_for_gui', '当前媒体项'))
            if not media_display_name_for_log or media_display_name_for_log == "---":
                media_display_name_for_log = self.current_item_context_for_async_processing.get('display_path_for_gui', '当前媒体项')
            media_id_for_log = self.current_item_context_for_async_processing.get('tmdb_id_str', 'N/A')

        for task_id, task_details in list(self.active_translation_task_details.items()):
            if now - task_details['start_time'] > self.MAX_TRANSLATION_TASK_DURATION:
                thread_to_kill = task_details['thread']
                actor_idx = task_details['actor_index']
                field_type = task_details['field_type']
                original_text = task_details['original_text']
                print(f"[看门狗] 检测到翻译任务超时: {task_id} (文本: '{original_text}', 媒体: '{media_display_name_for_log}')")
                self.log_to_progress_text_qt(f"媒体 '{media_display_name_for_log}' 的翻译任务 '{original_text}' (ID: {task_id}) 超时，尝试终止。", "WARN")
                if thread_to_kill.isRunning():
                    thread_to_kill.quit()
                    if not thread_to_kill.wait(150):
                        thread_to_kill.terminate()
                        print(f"[看门狗] 翻译线程 {task_id} 被强制终止。")
                del self.active_translation_task_details[task_id]
                if actor_idx in self.current_item_translation_buffer:
                    if field_type == 'character':
                        self.current_item_translation_buffer[actor_idx]['char_translated'] = "TRANSLATION_TIMED_OUT"
                    elif field_type == 'name':
                        self.current_item_translation_buffer[actor_idx]['name_translated'] = "TRANSLATION_TIMED_OUT"
                    self.current_item_translation_buffer[actor_idx][f"{field_type}_watchdog_processed"] = True
                self._handle_translation_finished(task_id, original_text, None, field_type, actor_idx)
        if self.active_translation_task_details or self.translation_task_queue:
            if not self.translation_watchdog_timer.isActive():
                self.translation_watchdog_timer.start(self.WATCHDOG_TIMER_INTERVAL)
        elif self.translation_watchdog_timer.isActive():
            self.translation_watchdog_timer.stop()

    # <<<--- 修改：start_batch_processing 方法，使用配置中的主缓存目录 --- >>>
    @pyqtSlot()
    def start_batch_processing(self):
        is_api_ok = DOUBAN_API_AVAILABLE and self._douban_api_instance and \
                      isinstance(self._douban_api_instance, DoubanApi) and \
                      (type(self._douban_api_instance).__module__ == 'douban' if DOUBAN_API_AVAILABLE else True)
        if not is_api_ok:
            QMessageBox.critical(self, "错误", "豆瓣API模块未初始化或不可用，无法处理。"); return
        if self.batch_processing_active:
            self.log_to_progress_text_qt("批量处理已在进行中！", "WARN")
            QMessageBox.warning(self, "提示", "批量处理已在进行中！"); return

        process_domestic = self.cb_process_domestic_only.isChecked()
        process_foreign = self.cb_process_foreign_only.isChecked()
        if not process_domestic and not process_foreign:
            QMessageBox.warning(self, "选择处理类型", "请至少选择一种处理类型（“仅国产影视”或“仅外语影视”）。")
            return

        # <<<--- 修改：从 self.cfg_main_cache_path 获取主缓存目录 --- >>>
        selected_root_path = self.cfg_main_cache_path
        if not selected_root_path or not os.path.isdir(selected_root_path):
            # <<<--- 修改：提示用户去设置中配置主缓存目录 --- >>>
            QMessageBox.critical(self, "错误", "主缓存目录未配置或无效。\n请通过“程序设置”按钮配置主缓存目录。")
            self.log_to_progress_text_qt("错误：主缓存目录未配置或无效。请打开设置进行配置。", "ERROR")
            return

        self.batch_start_time = time.time()
        self.failed_media_items = []
        self.log_to_progress_text_qt(f"开始分析并准备批量处理目录: {selected_root_path}", "INFO")
        if hasattr(self, 'process_button'): self.process_button.setEnabled(False); self.process_button.setText("正在分析目录...")
        if hasattr(self, 'stop_button'): self.stop_button.setEnabled(True)

        self.current_translation_threads.clear()
        self.current_item_translation_buffer.clear()
        self.current_item_pending_translations = 0
        self.current_item_context_for_async_processing.clear()
        self.launched_translation_tasks_info.clear()
        self.active_translation_task_details.clear()
        self.translation_task_queue.clear()
        if self.translation_watchdog_timer.isActive():
            self.translation_watchdog_timer.stop()
        QCoreApplication.processEvents()

        self.force_reprocess = self.cb_force_reprocess.isChecked()
        if self.force_reprocess:
            self.log_to_progress_text_qt("用户选择强制重新处理所有媒体目录。", "INFO")
            self.processed_media_set.clear()
            try:
                log_file_path = os.path.join(get_base_path_for_files(), constants.PROCESSED_MEDIA_LOG_FILE)
                if os.path.exists(log_file_path): os.remove(log_file_path)
            except Exception as e:
                self.log_to_progress_text_qt(f"清除已处理记录文件时发生错误: {e}", "ERROR")

        self.batch_media_dirs_to_process = []
        # <<<--- 修改：目录扫描逻辑，现在是实时扫描主缓存目录 --- >>>
        # --- 根据UI选项确定要扫描的媒体子目录 ---
        media_type_subdirs_to_scan = []
        process_movies_selected = self.cb_process_movie.isChecked()
        process_tv_any_selected = (self.cb_process_series.isChecked() or
                                   self.cb_process_season.isChecked() or
                                   self.cb_process_episode.isChecked())

        if process_movies_selected:
            media_type_subdirs_to_scan.append("tmdb-movies2")
            self.log_to_progress_text_qt("扫描选项：将扫描电影目录 (tmdb-movies2)。", "DEBUG")
        if process_tv_any_selected:
            media_type_subdirs_to_scan.append("tmdb-tv")
            self.log_to_progress_text_qt("扫描选项：将扫描电视剧目录 (tmdb-tv)。", "DEBUG")

        if not media_type_subdirs_to_scan:
            QMessageBox.information(self, "提示", "没有选择任何要处理的媒体类型（电影、剧集、季或集）。")
            self.log_to_progress_text_qt("没有选择任何媒体类型进行扫描，批处理中止。", "WARN")
            self._finalize_batch_processing(interrupted=True) # 使用中断标志来结束
            return
        
        self.log_to_progress_text_qt(f"将扫描以下子目录: {', '.join(media_type_subdirs_to_scan)}", "INFO")
        # --- 扫描逻辑开始 ---
        self.batch_media_dirs_to_process = [] # 确保在扫描前清空

        for subdir_name in media_type_subdirs_to_scan: # 使用新的列表
            current_scan_path = os.path.join(selected_root_path, subdir_name)
            if not os.path.isdir(current_scan_path):
                self.log_to_progress_text_qt(f"注意：在主缓存中未找到子目录 '{current_scan_path}'，跳过扫描此部分。", "WARN")
                continue

            try:
                items_in_subdir = os.listdir(current_scan_path)
                for i, item_name in enumerate(items_in_subdir):
                    item_path = os.path.join(current_scan_path, item_name)
                    if i % 200 == 0 and i > 0 : QCoreApplication.processEvents()
                    if os.path.isdir(item_path):
                        if item_name.lower() == "images": continue
                        if self._is_media_id_directory(item_path):
                            self.batch_media_dirs_to_process.append(item_path)
            except FileNotFoundError:
                self.log_to_progress_text_qt(f"扫描主缓存子目录 '{current_scan_path}' 时出错：目录未找到。", "ERROR")
            except Exception as e:
                self.log_to_progress_text_qt(f"无法列出主缓存子目录 '{current_scan_path}' 的内容: {e}", "ERROR")
                # 考虑是否要因为一个子目录扫描失败而停止整个批处理
                # self._finalize_batch_processing(interrupted=True); return

        self.batch_media_dirs_to_process = sorted(list(set(self.batch_media_dirs_to_process)))

        if not self.batch_media_dirs_to_process:
            self.log_to_progress_text_qt(f"在主缓存目录 '{selected_root_path}' 中未找到任何可处理的媒体目录。", "WARN")
            QMessageBox.warning(self, "提示", f"在主缓存目录 '{selected_root_path}' 中未找到任何可处理的媒体目录。\n请确保该目录下直接包含TMDB ID命名的文件夹。")
            self._finalize_batch_processing(interrupted=True); return

        self.log_to_progress_text_qt(f"目录扫描完成。共找到 {len(self.batch_media_dirs_to_process)} 个媒体目录待处理。", "INFO")
        self.batch_stats = {k: 0 for k in self.batch_stats}
        self.current_batch_index = 0
        self.batch_processing_active = True
        self.current_api_cooldown_ms = self.cfg_default_api_cooldown_seconds * 1000

        if hasattr(self, 'progress_bar'):
            self.progress_bar.setMaximum(len(self.batch_media_dirs_to_process))
            self.progress_bar.setValue(0)
        if hasattr(self, 'progress_label'): self.progress_label.setText(f"0/{len(self.batch_media_dirs_to_process)}")
        if hasattr(self, 'process_button'): self.process_button.setText(f"处理中... (0/{len(self.batch_media_dirs_to_process)})")
        QCoreApplication.processEvents()

        if not hasattr(self, 'batch_timer'):
            self.batch_timer = QTimer(self)
            self.batch_timer.setSingleShot(True)
            self.batch_timer.timeout.connect(self._process_one_media_dir_in_batch_qt)
        if not self.batch_timer.isActive():
            self.batch_timer.start(10) # 立即开始第一个

    # --- 后续方法 (_handle_translation_started, _handle_translation_finished, _process_one_media_dir_in_batch_qt, 等) ---
    # --- 暂时保持不变，但它们内部关于路径的处理 (特别是 display_path_for_gui) 可能需要微调 ---
    # --- _finalize_item_processing 中保存JSON的路径逻辑需要修改为使用覆盖缓存目录 ---
    # --- 这些将在下一阶段详细处理 ---

    @pyqtSlot(str, str, str, int)
    def _handle_translation_started(self, task_id: str, original_text: str, field_type: str, actor_index: int): # 保持不变
        if not self.batch_processing_active:
            return
        if constants.DEBUG:
            media_display_name = self.current_item_context_for_async_processing.get('display_path_for_gui', '未知媒体项')
            actor_name_for_log = "未知演员"
            if actor_index in self.current_item_translation_buffer and \
               'original_actor_data' in self.current_item_translation_buffer[actor_index]:
                actor_name_for_log = self.current_item_translation_buffer[actor_index]['original_actor_data'].get('name', '未知演员')
            field_type_chinese = "角色名" if field_type == 'character' else "演员名"
            log_message = f"媒体'{media_display_name}' (演员 {actor_index+1}: {actor_name_for_log}): 开始翻译 {field_type_chinese} '{original_text}' (任务ID: {task_id})"
            self.log_to_progress_text_qt(log_message, "DEBUG")

    @pyqtSlot(str, str, str, str, int)
    def _handle_translation_finished(self, task_id: str, original_text: str, translated_text: str, field_type: str, actor_index: int):
        self.log_to_progress_text_qt(
            f"主程序收到翻译结果 - 任务ID: {task_id}, 原文: '{original_text}', 翻译后文本: '{translated_text}', 翻译字段类型: {field_type}, 演员索引: {actor_index}", "DEBUG"
        )
        if not self.batch_processing_active:
            if task_id in self.active_translation_task_details:
                del self.active_translation_task_details[task_id]
            self.current_translation_threads = [t for t in self.current_translation_threads if not (hasattr(t, 'task_id') and t.task_id == task_id)]
            self._launch_next_translation_tasks_if_needed()
            return

        media_display_name_for_log = "未知媒体项"
        media_id_for_log = "N/A"
        actor_name_for_log_context = "未知演员"

        if self.current_item_context_for_async_processing:
            media_display_name_for_log = self.current_item_context_for_async_processing.get('api_name',
                                           self.current_item_context_for_async_processing.get('display_path_for_gui', '未知媒体项'))
            if not media_display_name_for_log or media_display_name_for_log == "---":
                media_display_name_for_log = self.current_item_context_for_async_processing.get('display_path_for_gui', '未知媒体项')
            media_id_for_log = self.current_item_context_for_async_processing.get('tmdb_id_str', 'N/A')

        if actor_index in self.current_item_translation_buffer and \
           'original_actor_data' in self.current_item_translation_buffer[actor_index]:
            actor_name_for_log_context = self.current_item_translation_buffer[actor_index]['original_actor_data'].get('name', '未知演员')

        task_details_from_watchdog = self.active_translation_task_details.pop(task_id, None)
        thread_obj_to_remove = None
        if task_details_from_watchdog:
            thread_obj_to_remove = task_details_from_watchdog.get('thread')
        else:
            for t in self.current_translation_threads:
                if hasattr(t, 'task_id') and t.task_id == task_id:
                    thread_obj_to_remove = t; break
        if thread_obj_to_remove and thread_obj_to_remove in self.current_translation_threads:
            self.current_translation_threads.remove(thread_obj_to_remove)

        current_processing_path_check = ""
        if self.current_batch_index < len(self.batch_media_dirs_to_process):
             current_processing_path_check = self.batch_media_dirs_to_process[self.current_batch_index]

        if not self.current_item_context_for_async_processing or \
           self.current_item_context_for_async_processing.get('media_dir_path') != current_processing_path_check:
            self._launch_next_translation_tasks_if_needed()
            return

        if actor_index not in self.current_item_translation_buffer:
            self._launch_next_translation_tasks_if_needed()
            return

        actor_entry = self.current_item_translation_buffer[actor_index]
        can_process_signal = False
        field_type_chinese = "角色名" if field_type == 'character' else "演员名"

        if field_type == 'character' and actor_entry.get('char_translated') == "TRANSLATION_PENDING":
            actor_entry['char_translated'] = translated_text
            can_process_signal = True
        elif field_type == 'name' and actor_entry.get('name_translated') == "TRANSLATION_PENDING":
            actor_entry['name_translated'] = translated_text
            can_process_signal = True

        if not can_process_signal:
            self._launch_next_translation_tasks_if_needed()
            return

        log_level_ui = "SUCCESS"
        log_message_ui = ""
        if translated_text and translated_text.strip() and translated_text != "TRANSLATION_TIMED_OUT":
            log_message_ui = f"媒体'{media_display_name_for_log}' (演员 {actor_index+1}: {actor_name_for_log_context}): {field_type_chinese} '{original_text}' -> 成功翻译为 '{translated_text.strip()}'"
        elif translated_text == "TRANSLATION_TIMED_OUT":
            log_level_ui = "WARN"
            log_message_ui = f"媒体'{media_display_name_for_log}' (演员 {actor_index+1}: {actor_name_for_log_context}): {field_type_chinese} '{original_text}' -> 翻译超时"
        else:
            log_level_ui = "WARN"
            log_message_ui = f"媒体'{media_display_name_for_log}' (演员 {actor_index+1}: {actor_name_for_log_context}): {field_type_chinese} '{original_text}' -> 翻译失败或返回空"
        self.log_to_progress_text_qt(log_message_ui, log_level_ui)

        if hasattr(self, 'launched_translation_tasks_info'):
            task_to_remove_debug = None
            for launched_task_tuple in self.launched_translation_tasks_info:
                if launched_task_tuple[0] == task_id:
                    task_to_remove_debug = launched_task_tuple; break
            if task_to_remove_debug: self.launched_translation_tasks_info.remove(task_to_remove_debug)

        self.current_item_pending_translations -= 1
        if self.current_item_pending_translations == 0:
            if self.translation_watchdog_timer.isActive(): self.translation_watchdog_timer.stop()
            if hasattr(self, 'launched_translation_tasks_info') and self.launched_translation_tasks_info:
                 self.launched_translation_tasks_info.clear()
            context = self.current_item_context_for_async_processing
            if context:
                self._process_foreign_film_after_translation(
                    context['media_dir_path'], context['tmdb_id_str'], context['display_path_for_gui'],
                    context['item_key'], context['current_media_type'], context['api_name'],
                    context['local_cast_raw']
                )
            else: self._schedule_next_item_processing(10)
        elif self.current_item_pending_translations < 0:
            if self.translation_watchdog_timer.isActive(): self.translation_watchdog_timer.stop()
            self._schedule_next_item_processing(10)
        else:
            self._launch_next_translation_tasks_if_needed()

# app_window_qt.py
# class JSONEditorApp:
    # ...

    def _process_one_media_dir_in_batch_qt(self):
        # --- 步骤1: 严格的前置条件检查和退出 ---
        if not self.batch_processing_active:
            self.log_to_progress_text_qt("DEBUG_BATCH: Batch processing not active, stopping timer and returning.", "DEBUG")
            if hasattr(self, 'batch_timer') and self.batch_timer.isActive():
                self.batch_timer.stop()
            return

        if self.current_batch_index >= len(self.batch_media_dirs_to_process):
            self.log_to_progress_text_qt("DEBUG_BATCH: All items processed, finalizing batch.", "DEBUG")
            self._finalize_batch_processing(interrupted=False) # 正常完成
            if hasattr(self, 'batch_timer') and self.batch_timer.isActive():
                self.batch_timer.stop()
            return

        # --- 步骤2: 清理上一轮的状态 (如果需要) 和更新UI进度 ---
        # 这些清理应该在确认要处理当前项目后，或者在每个项目处理开始时
        self.current_translation_threads.clear()
        self.current_item_translation_buffer.clear()
        self.current_item_pending_translations = 0
        self.current_item_context_for_async_processing.clear()
        self.launched_translation_tasks_info.clear()
        self.active_translation_task_details.clear()
        self.translation_task_queue.clear()
        if self.translation_watchdog_timer.isActive():
            self.translation_watchdog_timer.stop()

        if hasattr(self, 'progress_bar'): self.progress_bar.setValue(self.current_batch_index + 1)
        if hasattr(self, 'progress_label'): self.progress_label.setText(f"{self.current_batch_index + 1}/{len(self.batch_media_dirs_to_process)}")
        if hasattr(self, 'process_button'): self.process_button.setText(f"处理中... ({self.current_batch_index + 1}/{len(self.batch_media_dirs_to_process)})")
        QCoreApplication.processEvents() # 确保UI更新

        # --- 步骤3: 获取当前要处理的媒体项信息 ---
        media_dir_path = self.batch_media_dirs_to_process[self.current_batch_index]
        tmdb_id_str = os.path.basename(media_dir_path)
        
        self.log_to_progress_text_qt(f"DEBUG_BATCH: Processing index {self.current_batch_index}, path: {media_dir_path}", "DEBUG")

        # 提取 api_name 用于日志，如果提取失败则回退
        api_name_for_log, _, _ = self.extract_info_from_media_directory(media_dir_path) # 只需要名字
        log_display_title = api_name_for_log if api_name_for_log and api_name_for_log != "---" else tmdb_id_str

        self.log_to_progress_text_qt(f"--- 开始处理媒体: '{log_display_title}' (ID: {tmdb_id_str}) ---", "INFO")
        self.api_type_label.setText("分析中...") # 重置API类型显示

        # 重新获取完整的 api_name, api_year, api_imdb_id 和 current_media_type，因为 extract_info_from_media_directory 会设置 self.mtype
        api_name, api_year, api_imdb_id = self.extract_info_from_media_directory(media_dir_path)
        current_media_type = self.mtype # 获取最新的 self.mtype

        # --- 步骤4: 再次检查批处理状态 (以防在耗时操作如extract_info_from_media_directory时被停止) ---
        if not self.batch_processing_active:
            self.log_to_progress_text_qt("DEBUG_BATCH: Batch processing stopped during item analysis.", "DEBUG")
            # 注意：这里不直接调用 _finalize_batch_processing，因为可能只是单个项目处理被中断
            # _schedule_next_item_processing 会在下次被调用时检查 batch_processing_active
            # 或者，如果希望停止时立即结束整个批处理，则调用 _finalize_batch_processing(interrupted=True)
            # 但通常 stop_batch_processing 已经处理了 finalize
            return # 直接返回，等待下一次定时器（如果还在运行）或手动启动

        # --- 步骤5: 执行核心处理逻辑 (与您提供的代码基本一致) ---
        # 根据UI勾选的媒体类型进行过滤
        should_process_this_type = False
        if current_media_type == "movie" and self.cb_process_movie.isChecked():
            should_process_this_type = True
        elif current_media_type == "tv" and (self.cb_process_series.isChecked() or self.cb_process_season.isChecked() or self.cb_process_episode.isChecked()):
            should_process_this_type = True
        
        if not should_process_this_type:
            self.log_to_progress_text_qt(f"媒体 '{log_display_title}' (类型: {current_media_type or '未知'}): 根据UI选项，跳过处理此类型。", "INFO")
            self._schedule_next_item_processing(10); return

        early_fail_reason = ""
        if not current_media_type: early_fail_reason = "无法确定媒体类型"
        elif not api_name: early_fail_reason = "提取到的标题为空" # api_name 是从 extract_info_from_media_directory 获取的

        item_key = create_media_item_key(current_media_type, tmdb_id_str) if not early_fail_reason else None
        if not item_key and not early_fail_reason: early_fail_reason = "创建媒体键失败"

        if early_fail_reason:
            self.log_to_progress_text_qt(f"媒体 '{log_display_title}' (ID: {tmdb_id_str}): {early_fail_reason}，标记失败。", "ERROR")
            self.batch_stats["total_media_failed_to_extract_info"] += 1
            self.failed_media_items.append({"path": media_dir_path, "tmdb_id": tmdb_id_str, "type": current_media_type or "unknown", "reason": early_fail_reason, "display_title_for_list": log_display_title})
            self._schedule_next_item_processing(10); return

        if not self.cb_force_reprocess.isChecked() and item_key in self.processed_media_set:
            self.log_to_progress_text_qt(f"媒体项 '{log_display_title}' (ID: {tmdb_id_str}) 已处理过，跳过。", "INFO")
            self.batch_stats["total_media_skipped_due_to_cache"] += 1
            self._schedule_next_item_processing(10); return

        main_json_path = os.path.join(media_dir_path, "all.json" if current_media_type == "movie" else "series.json")
        if not os.path.exists(main_json_path):
             self.log_to_progress_text_qt(f"媒体 '{log_display_title}' (ID: {tmdb_id_str}): 主JSON文件 '{os.path.basename(main_json_path)}' 未找到。", "ERROR")
             self.batch_stats["total_media_failed_to_extract_info"] += 1
             self.failed_media_items.append({"path": media_dir_path, "tmdb_id": tmdb_id_str, "type": current_media_type, "reason": f"主JSON '{os.path.basename(main_json_path)}' 未找到", "display_title_for_list": log_display_title})
             self._schedule_next_item_processing(10); return

        _, local_orig_title, local_orig_lang, local_cast_raw, _ = self._extract_full_info_from_local_json(main_json_path)

        if not local_cast_raw:
            self.log_to_progress_text_qt(f"媒体 '{log_display_title}' (ID: {tmdb_id_str}): 本地JSON '{os.path.basename(main_json_path)}' 中无演员信息，完全跳过。", "INFO")
            self.batch_stats["total_media_skipped_due_to_no_local_cast"] += 1
            self._schedule_next_item_processing(10); return

        is_foreign_film_decision = False
        # ... (判断 is_foreign_film_decision 的逻辑不变) ...
        has_chinese_in_title = contains_chinese(local_orig_title) if local_orig_title else False
        lang_is_non_chinese = local_orig_lang and str(local_orig_lang).lower().strip() not in constants.CHINESE_LANG_CODES
        if lang_is_non_chinese and not has_chinese_in_title: is_foreign_film_decision = True
        elif not local_orig_lang and not has_chinese_in_title and local_orig_title and local_orig_title.strip(): is_foreign_film_decision = True


        process_this_domestic_option_selected = self.cb_process_domestic_only.isChecked()
        process_foreign_selected = self.cb_process_foreign_only.isChecked()
        
        # 使用 log_display_title 进行跳过日志的打印
        if not is_foreign_film_decision: # 国产片
            if not process_this_domestic_option_selected:
                self.log_to_progress_text_qt(f"媒体 '{log_display_title}' (ID: {tmdb_id_str}): 根据选项（当前仅处理外语影视）跳过国产/API类影视。", "INFO")
                self.batch_stats["total_media_skipped_due_to_precheck"] += 1
                self._schedule_next_item_processing(10); return
        else: # 外语片
            if not process_foreign_selected:
                self.log_to_progress_text_qt(f"媒体 '{log_display_title}' (ID: {tmdb_id_str}): 根据选项（当前仅处理国产影视）跳过外语影视。", "INFO")
                self.batch_stats["total_media_skipped_due_to_precheck"] += 1
                self._schedule_next_item_processing(10); return

        self.current_item_context_for_async_processing = {
            'media_dir_path': media_dir_path, 'tmdb_id_str': tmdb_id_str,
            'display_path_for_gui': log_display_title, # 使用 log_display_title
            'item_key': item_key, 'current_media_type': current_media_type,
            'api_name': api_name, 'api_year': api_year, 'api_imdb_id': api_imdb_id,
            'local_cast_raw': local_cast_raw, 'main_json_path': main_json_path
        }
        
        # title_for_log_in_branch 的获取也基于 log_display_title 或 api_name
        title_for_log_in_branch_decision = api_name if api_name and api_name != "---" else log_display_title

        if is_foreign_film_decision:
            self.log_to_progress_text_qt(f"媒体 '{title_for_log_in_branch_decision}' (ID: {tmdb_id_str}): 判断为外语片，尝试本地异步翻译。", "INFO")
            self.api_type_label.setText("在线翻译中...")
            self._start_foreign_film_translation_tasks(local_cast_raw) # 这个方法会负责后续的调度
        else: # 判断为国产片
            self.log_to_progress_text_qt(f"媒体 '{title_for_log_in_branch_decision}' (ID: {tmdb_id_str}): 判断为国产片，进入豆瓣数据处理流程 (策略: {self.cfg_domestic_source_mode})...", "INFO")
            if self.cfg_domestic_source_mode == constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY: self.api_type_label.setText("豆瓣API(在线)")
            elif self.cfg_domestic_source_mode == constants.DOMESTIC_SOURCE_MODE_LOCAL_ONLY: self.api_type_label.setText("豆瓣数据(本地)")
            else: self.api_type_label.setText("豆瓣(本地/在线)")
            
            self._process_domestic_film( # _process_domestic_film 会负责后续的调度
                media_dir_path, tmdb_id_str, 
                log_display_title, # 传递 log_display_title
                item_key, current_media_type, 
                api_name, api_year, api_imdb_id, 
                main_json_path, local_cast_raw
            )
    def _start_foreign_film_translation_tasks(self, local_cast_raw): # 保持不变
        self.launched_translation_tasks_info.clear()
        self.active_translation_task_details.clear()
        self.translation_task_queue.clear()
        media_display_name_for_log = "未知外语片"
        media_id_for_log = "N/A"
        if self.current_item_context_for_async_processing:
            media_display_name_for_log = self.current_item_context_for_async_processing.get('api_name',
                                           self.current_item_context_for_async_processing.get('display_path_for_gui', '未知外语片'))
            if not media_display_name_for_log or media_display_name_for_log == "---":
                media_display_name_for_log = self.current_item_context_for_async_processing.get('display_path_for_gui', '未知外语片')
            media_id_for_log = self.current_item_context_for_async_processing.get('tmdb_id_str', 'N/A')

        if not local_cast_raw:
            self.log_to_progress_text_qt(f"外语片处理：'{media_display_name_for_log}' (ID: {media_id_for_log}) 无本地演员信息以供翻译。", "WARN")
            self._finalize_item_processing(None,
                                           self.current_item_context_for_async_processing['media_dir_path'],
                                           self.current_item_context_for_async_processing['tmdb_id_str'],
                                           self.current_item_context_for_async_processing['display_path_for_gui'],
                                           self.current_item_context_for_async_processing['item_key'],
                                           self.current_item_context_for_async_processing['current_media_type'],
                                           use_api_source_for_cooldown=False
                                          )
            return

        for actor_index, actor_raw_item in enumerate(local_cast_raw):
            if not isinstance(actor_raw_item, dict): continue
            self.current_item_translation_buffer[actor_index] = {
                'original_actor_data': actor_raw_item.copy(),
                'name': actor_raw_item.get('name', ""),
                'character': actor_raw_item.get('character', ""),
                'char_translated': "NO_TRANSLATION_NEEDED",
                'name_translated': "NO_TRANSLATION_NEEDED"
            }
            actor_name_orig = actor_raw_item.get('name', "")
            char_name_orig = actor_raw_item.get('character', "")
            clean_char_orig = clean_character_name_static(char_name_orig)
            char_needs_translation = clean_char_orig and not contains_chinese(clean_char_orig)
            name_needs_translation = actor_name_orig and not contains_chinese(actor_name_orig)

            if char_needs_translation:
                task_id_char = f"char_{uuid.uuid4().hex}"
                self.translation_task_queue.append({
                    'task_id': task_id_char, 'text_to_translate': clean_char_orig,
                    'original_text_for_signal': clean_char_orig, 'field_type': 'character',
                    'actor_index': actor_index
                })
                self.current_item_translation_buffer[actor_index]['char_translated'] = "QUEUED_FOR_TRANSLATION"

            if name_needs_translation:
                print(f"调试演员名翻译[批量]: 演员 '{actor_name_orig}' 需要翻译，准备加入队列。") # <--- 新增的打印
                task_id_name = f"name_{uuid.uuid4().hex}"
                self.translation_task_queue.append({
                    'task_id': task_id_name, 'text_to_translate': actor_name_orig,
                    'original_text_for_signal': actor_name_orig, 'field_type': 'name',
                    'actor_index': actor_index
                })
                self.current_item_translation_buffer[actor_index]['name_translated'] = "QUEUED_FOR_TRANSLATION"

        self.current_item_pending_translations = len(self.translation_task_queue)

        if self.current_item_pending_translations == 0:
            self.log_to_progress_text_qt(f"外语片 '{media_display_name_for_log}' (ID: {media_id_for_log}): 无需翻译的字段。", "DEBUG")
            self._process_foreign_film_after_translation(
                self.current_item_context_for_async_processing['media_dir_path'],
                self.current_item_context_for_async_processing['tmdb_id_str'],
                self.current_item_context_for_async_processing['display_path_for_gui'],
                self.current_item_context_for_async_processing['item_key'],
                self.current_item_context_for_async_processing['current_media_type'],
                self.current_item_context_for_async_processing['api_name'],
                local_cast_raw
            )
        else:
            self.log_to_progress_text_qt(f"外语片 '{media_display_name_for_log}' (ID: {media_id_for_log}): 共 {self.current_item_pending_translations} 个翻译任务进入队列。开始调度...", "INFO")
            self._launch_next_translation_tasks_if_needed()
            if not self.translation_watchdog_timer.isActive() and (self.active_translation_task_details or self.translation_task_queue):
                self.translation_watchdog_timer.start(self.WATCHDOG_TIMER_INTERVAL)

    def _launch_next_translation_tasks_if_needed(self): # 保持不变
        if not self.batch_processing_active:
            return
        threads_to_remove = []
        for t in self.current_translation_threads:
            if not t.isRunning():
                threads_to_remove.append(t)
        for t_rem in threads_to_remove:
            self.current_translation_threads.remove(t_rem)

        while len(self.current_translation_threads) < self.MAX_CONCURRENT_TRANSLATION_THREADS and self.translation_task_queue:
            if not self.batch_processing_active:
                break
            task_data = self.translation_task_queue.pop(0)
            actor_idx_for_buffer = task_data['actor_index']
            field_type_for_buffer = task_data['field_type']
            if actor_idx_for_buffer in self.current_item_translation_buffer:
                if field_type_for_buffer == 'character':
                    self.current_item_translation_buffer[actor_idx_for_buffer]['char_translated'] = "TRANSLATION_PENDING"
                elif field_type_for_buffer == 'name':
                    self.current_item_translation_buffer[actor_idx_for_buffer]['name_translated'] = "TRANSLATION_PENDING"
            self.launched_translation_tasks_info.add((task_data['task_id'], task_data['actor_index'], task_data['field_type'], task_data['text_to_translate']))
            thread = TranslationThread(
                task_id=task_data['task_id'],
                text_to_translate=task_data['text_to_translate'],
                original_text_for_signal=task_data['original_text_for_signal'],
                field_type=task_data['field_type'],
                actor_index=task_data['actor_index'],
                parent=self
            )
            thread.translation_started.connect(self._handle_translation_started)
            thread.translation_finished.connect(self._handle_translation_finished)
            thread.log_message_from_thread.connect(self._log_message_from_worker_thread)
            self.current_translation_threads.append(thread)
            self.active_translation_task_details[task_data['task_id']] = {
                'thread': thread,
                'start_time': time.time(),
                'actor_index': task_data['actor_index'],
                'field_type': task_data['field_type'],
                'original_text': task_data['original_text_for_signal']
            }
            thread.start()
        if self.active_translation_task_details or self.translation_task_queue:
            if not self.translation_watchdog_timer.isActive():
                self.translation_watchdog_timer.start(self.WATCHDOG_TIMER_INTERVAL)
        elif self.translation_watchdog_timer.isActive():
            self.translation_watchdog_timer.stop()

    def _process_foreign_film_after_translation(self, media_dir_path, tmdb_id_str, display_path_for_gui, item_key, current_media_type, api_name, local_cast_raw_original): # 保持不变
        final_cast_list_for_update = []
        for actor_idx, original_actor_item_data in enumerate(local_cast_raw_original):
            if not isinstance(original_actor_item_data, dict): continue
            processed_actor = original_actor_item_data.copy()
            buffered_results = self.current_item_translation_buffer.get(actor_idx, {})
            original_char_name = processed_actor.get("character", "")
            clean_original_char = clean_character_name_static(original_char_name)
            char_translation_result = buffered_results.get('char_translated', "NO_TRANSLATION_ATTEMPTED")
            if char_translation_result == "TRANSLATION_TIMED_OUT" or char_translation_result == "TRANSLATION_PENDING":
                processed_actor["character"] = clean_original_char
            elif char_translation_result != "NO_TRANSLATION_NEEDED":
                if char_translation_result and char_translation_result.strip() and contains_chinese(char_translation_result):
                    processed_actor["character"] = char_translation_result.strip()
                else: processed_actor["character"] = clean_original_char
            else: processed_actor["character"] = clean_original_char
            original_actor_name = processed_actor.get("name", "")
            name_translation_result = buffered_results.get('name_translated', "NO_TRANSLATION_ATTEMPTED")
            print(f"调试演员名翻译[批量-应用]: 演员 '{original_actor_name}' 的翻译结果是: '{name_translation_result}'") # <--- 新增
            self.log_to_progress_text_qt(
                f"应用翻译结果前 - 演员 '{original_actor_name}' (索引 {actor_idx}): 缓存拿到的翻译结果是 '{name_translation_result}', 这个结果是否包含中文: {contains_chinese(name_translation_result if name_translation_result else '')}", "DEBUG"
            )
            if name_translation_result == "TRANSLATION_TIMED_OUT" or name_translation_result == "TRANSLATION_PENDING":
                print(f"调试演员名翻译[批量-应用]: 演员 '{original_actor_name}' 翻译超时或待处理，使用原文。") # <--- 新增
                pass
            elif name_translation_result != "NO_TRANSLATION_NEEDED":
                print(f"调试演员名翻译[批量-应用]: 演员 '{original_actor_name}' 翻译结果非'无需翻译'，准备检查是否为中文。") # <--- 新增
                if name_translation_result and name_translation_result.strip() and contains_chinese(name_translation_result):
                    processed_actor["name"] = name_translation_result.strip()
                    print(f"调试演员名翻译[批量-应用]: 演员 '{original_actor_name}' 已更新为中文名: '{processed_actor['name']}'") # <--- 新增
                else:
                    print(f"调试演员名翻译[批量-应用]: 演员 '{original_actor_name}' 翻译结果 '{name_translation_result}' 非中文或为空，使用原文。") # <--- 新增
            else:
                print(f"调试演员名翻译[批量-应用]: 演员 '{original_actor_name}' 无需翻译。") # <--- 新增
            processed_actor.setdefault("original_name", original_actor_item_data.get("name"))
            final_cast_list_for_update.append(processed_actor)
        self.batch_stats["total_media_directly_translated"] += 1
        self._finalize_item_processing(
            final_cast_list_for_update, media_dir_path, tmdb_id_str, display_path_for_gui,
            item_key, current_media_type, use_api_source_for_cooldown=False
        )

    def _try_get_cast_from_local_douban(self, imdb_id_to_match: str, media_type_for_subdir: str, display_path_for_log: str) -> Optional[List[Dict]]:
        """辅助函数：尝试从本地豆瓣缓存获取演员列表。成功返回列表，失败或未找到返回None。"""
        self.log_to_progress_text_qt(f"尝试本地豆瓣数据源 (IMDB: {imdb_id_to_match} for '{display_path_for_log}')...", "INFO")
        douban_source_base = self.cfg_main_cache_path
        douban_subdir_name = constants.DOUBAN_LOCAL_MOVIES_SUBDIR if media_type_for_subdir == "movie" else constants.DOUBAN_LOCAL_TV_SUBDIR
        potential_douban_parent_dir = os.path.join(douban_source_base, douban_subdir_name)
        found_douban_media_dir = None
        is_placeholder_dir = False

        if os.path.isdir(potential_douban_parent_dir):
            for item_name_in_douban_cache in os.listdir(potential_douban_parent_dir):
                item_path_in_douban_cache = os.path.join(potential_douban_parent_dir, item_name_in_douban_cache)
                if os.path.isdir(item_path_in_douban_cache):
                    info_json_path = os.path.join(item_path_in_douban_cache, "info.json")
                    if os.path.exists(info_json_path):
                        try:
                            with open(info_json_path, 'r', encoding='utf-8') as f_info:
                                info_data = json.load(f_info)
                            if info_data.get("imdb_id") == imdb_id_to_match:
                                if str(info_data.get("id", "")).strip() == "0":
                                    is_placeholder_dir = True
                                    self.log_to_progress_text_qt(f"  本地豆瓣目录 '{item_path_in_douban_cache}' 是占位符 (豆瓣ID为0)。", "DEBUG")
                                found_douban_media_dir = item_path_in_douban_cache
                                break
                        except Exception as e_info_read:
                            self.log_to_progress_text_qt(f"  读取或解析本地豆瓣info.json '{info_json_path}' 失败: {e_info_read}", "WARN")
        
        if is_placeholder_dir: # 如果找到的是占位符，也视为本地获取失败
            self.log_to_progress_text_qt(f"本地豆瓣数据为占位符 (IMDB: {imdb_id_to_match})，不使用此数据。", "WARN")
            return None 

        if found_douban_media_dir:
            self.log_to_progress_text_qt(f"  找到本地豆瓣目录 '{os.path.basename(found_douban_media_dir)}'，尝试提取演员...", "DEBUG")
            douban_main_json_path = os.path.join(found_douban_media_dir, "all.json" if media_type_for_subdir == "movie" else "series.json")
            if not os.path.exists(douban_main_json_path):
                self.log_to_progress_text_qt(f"  本地豆瓣主JSON文件 '{douban_main_json_path}' 未找到。", "WARN")
                return None
            try:
                with open(douban_main_json_path, 'r', encoding='utf-8') as f_douban:
                    douban_json_data = json.load(f_douban)
                douban_cast_raw_list_from_file = douban_json_data.get("actors") # 假设本地豆瓣JSON的演员键是 "actors"
                if douban_cast_raw_list_from_file and isinstance(douban_cast_raw_list_from_file, list):
                    temp_processed_douban_cast = []
                    for idx, actor_entry_from_douban in enumerate(douban_cast_raw_list_from_file):
                        actor_chinese_name = actor_entry_from_douban.get("name")
                        if not actor_chinese_name: continue
                        raw_character_name = actor_entry_from_douban.get("character")
                        cleaned_char_name = clean_character_name_static(raw_character_name)
                        actor_tmdb_id_from_douban_data = actor_entry_from_douban.get("tmdb_person_id")
                        profile_path_from_douban = actor_entry_from_douban.get("avatar", {}).get("large")
                        processed_actor = {
                            "name": actor_chinese_name, "character": cleaned_char_name,
                            "id": actor_tmdb_id_from_douban_data,
                            "original_name": actor_entry_from_douban.get("latin_name", actor_chinese_name),
                            "profile_path": profile_path_from_douban,
                            "adult": False, "gender": 0, "known_for_department": "Acting",
                            "popularity": 0.0, "cast_id": None,
                            "credit_id": f"douban_local_{actor_entry_from_douban.get('id')}_{idx}",
                            "order": idx, "_source": "douban_local",
                            "_douban_person_id": actor_entry_from_douban.get("id")
                        }
                        temp_processed_douban_cast.append(processed_actor)
                    if temp_processed_douban_cast:
                        self.log_to_progress_text_qt(f"成功从本地豆瓣提取 {len(temp_processed_douban_cast)} 位演员。", "SUCCESS")
                        return temp_processed_douban_cast
                    else:
                        self.log_to_progress_text_qt(f"本地豆瓣演员列表为空或未能有效解析。", "WARN")
                else:
                    self.log_to_progress_text_qt(f"本地豆瓣数据 '{douban_main_json_path}' 未找到 'actors' 列表或列表为空。", "WARN")
            except Exception as e:
                self.log_to_progress_text_qt(f"处理本地豆瓣数据 '{douban_main_json_path}' 时发生错误: {e}", "ERROR")
        else:
            self.log_to_progress_text_qt(f"未能找到IMDB ID '{imdb_id_to_match}' 对应的本地豆瓣数据目录。", "INFO")
        return None

    def _try_get_cast_from_online_douban(self, api_name_search: str, imdb_id_match: str, media_type_search: str, year_search: Optional[str]) -> Optional[List[Dict]]:
        """辅助函数：尝试从在线豆瓣API获取演员列表。成功返回列表，失败或API限流等问题返回None或特定错误标记。"""
        self.log_to_progress_text_qt(f"尝试在线豆瓣API (IMDB: {imdb_id_match}, Name: {api_name_search})...", "INFO")
        
        time.sleep(self.current_api_cooldown_ms / 1000.0)
        douban_match_result = self._douban_api_instance.match_info(name=api_name_search, imdbid=imdb_id_match, mtype=media_type_search, year=year_search)
        douban_online_id = None
        douban_online_type = None

        if douban_match_result and douban_match_result.get("id") and not douban_match_result.get("search_candidates") and not douban_match_result.get("error"):
            douban_online_id = str(douban_match_result.get("id"))
            douban_online_type = douban_match_result.get("type", media_type_search)
        elif douban_match_result and douban_match_result.get("error") == "rate_limit":
            self.log_to_progress_text_qt(f"在线匹配豆瓣条目时触发速率限制 (IMDB: {imdb_id_match})。", "WARN")
            return {"error": "rate_limit_match", "is_api_call": True} # 标记为API调用相关的错误
        else:
            self.log_to_progress_text_qt(f"无法通过IMDB ID '{imdb_id_match}' 在线精确匹配到唯一豆瓣条目。", "WARN")
            return None

        if douban_online_id and douban_online_type:
            time.sleep(self.current_api_cooldown_ms / 1000.0)
            api_cast_response = self._douban_api_instance.get_acting(name=api_name_search, mtype=douban_online_type, year=year_search, douban_id_override=douban_online_id)
            if api_cast_response and api_cast_response.get("cast") is not None: # 确保 cast 键存在且其值不是 None
                if api_cast_response.get("error") == "rate_limit":
                    self.log_to_progress_text_qt(f"获取豆瓣演员表时触发速率限制 (豆瓣ID: {douban_online_id})。", "WARN")
                    return {"error": "rate_limit_cast", "is_api_call": True} # 标记为API调用相关的错误
                
                # 即使 cast 键存在，其值也可能是一个空列表
                if not api_cast_response.get("cast"): # 如果 cast 是空列表
                    self.log_to_progress_text_qt(f"在线豆瓣API返回了空的演员列表 (豆瓣ID: {douban_online_id})。", "INFO")
                    return [] # 返回空列表表示成功调用但无数据

                processed_douban_cast_online = []
                for idx, actor_entry_from_douban_api in enumerate(api_cast_response.get("cast")):
                    actor_tmdb_id_from_api = actor_entry_from_douban_api.get("id")
                    processed_actor = {
                        "name": actor_entry_from_douban_api.get("name"),
                        "character": clean_character_name_static(actor_entry_from_douban_api.get("character")),
                        "id": actor_tmdb_id_from_api,
                        "original_name": actor_entry_from_douban_api.get("original_name", actor_entry_from_douban_api.get("name")),
                        "profile_path": actor_entry_from_douban_api.get("profile_path"),
                        "adult": actor_entry_from_douban_api.get("adult", False),
                        "gender": actor_entry_from_douban_api.get("gender", 0),
                        "known_for_department": actor_entry_from_douban_api.get("known_for_department", "Acting"),
                        "popularity": actor_entry_from_douban_api.get("popularity", 0.0),
                        "cast_id": actor_entry_from_douban_api.get("cast_id"),
                        "credit_id": actor_entry_from_douban_api.get("credit_id", f"douban_api_{douban_online_id}_{idx}"),
                        "order": actor_entry_from_douban_api.get("order", idx),
                        "_source": "douban_api",
                        "_douban_person_id": None # 在线API通常不直接返回豆瓣影人ID，除非douban.py特殊处理
                    }
                    processed_douban_cast_online.append(processed_actor)
                self.log_to_progress_text_qt(f"成功从在线豆瓣API获取到 {len(processed_douban_cast_online)} 位演员信息。", "SUCCESS")
                return processed_douban_cast_online
            else: # api_cast_response 为 None 或者没有 "cast" 键，或者 "cast" 的值是 None
                msg = f"在线豆瓣API未能返回ID '{douban_online_id}' 的有效演员信息结构。"
                if api_cast_response and api_cast_response.get("message"): msg += f" (API消息: {api_cast_response.get('message')})"
                self.log_to_progress_text_qt(msg, "WARN")
        return None # 获取演员失败或之前步骤失败


    def _process_domestic_film(self,
                                    tmdb_media_dir_path,
                                    tmdb_id_str,
                                    display_path_for_gui,
                                    item_key,
                                    current_media_type,
                                    api_name, # 影视名称
                                    api_year, # 影视年份
                                    api_imdb_id, # IMDB ID
                                    main_tmdb_json_path, # 主TMDB JSON文件路径
                                    local_tmdb_cast_raw # 从主TMDB JSON中提取的原始演员列表
                                    ):
                self.log_to_progress_text_qt(f"国产片 '{display_path_for_gui}' (IMDB: {api_imdb_id}): 开始处理。策略: {self.cfg_domestic_source_mode}", "INFO")

                if not api_imdb_id: # IMDB ID 是匹配豆瓣的关键
                    self.log_to_progress_text_qt(f"国产片 '{display_path_for_gui}': 缺少IMDB ID，无法精确匹配豆瓣数据，加入失败列表。", "ERROR")
                    self.failed_media_items.append({
                        "path": tmdb_media_dir_path, "tmdb_id": tmdb_id_str, "type": current_media_type,
                        "reason": "缺少IMDB ID，无法精确匹配豆瓣数据"
                    })
                    self.batch_stats["total_media_failed_to_extract_info"] += 1
                    self._schedule_next_item_processing(10)
                    return

                douban_cast_list_source = None
                source_type_log = "未知豆瓣源"
                used_online_api_call = False # 标记是否实际调用了在线API

                # 根据配置的策略决定数据获取顺序
                if self.cfg_domestic_source_mode == constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY:
                    douban_cast_list_source = self._try_get_cast_from_online_douban(api_name, api_imdb_id, current_media_type, api_year)
                    source_type_log = "仅在线豆瓣API"
                    if isinstance(douban_cast_list_source, dict) and douban_cast_list_source.get("is_api_call"):
                        used_online_api_call = True # 标记发生了API调用（即使是失败的）
                        # 如果API调用出错（如速率限制），需要特殊处理并返回
                        self.failed_media_items.append({"path": tmdb_media_dir_path, "tmdb_id": tmdb_id_str, "type": current_media_type, "reason": f"豆瓣API错误: {douban_cast_list_source.get('error')}"})
                        self.batch_stats["total_media_api_call_failed_early"] += 1
                        self._schedule_next_item_processing(self.current_api_cooldown_ms if "rate_limit" in douban_cast_list_source.get("error", "") else 10)
                        return
                    elif douban_cast_list_source is None: # 在线未找到，但策略是仅在线
                        self.log_to_progress_text_qt(f"策略为“仅在线豆瓣”，但未能获取数据。", "INFO")


                elif self.cfg_domestic_source_mode == constants.DOMESTIC_SOURCE_MODE_LOCAL_ONLY:
                    douban_cast_list_source = self._try_get_cast_from_local_douban(api_imdb_id, current_media_type, display_path_for_gui)
                    source_type_log = "仅本地豆瓣"
                    if douban_cast_list_source is None:
                        self.log_to_progress_text_qt(f"策略为“仅本地豆瓣”，但未能获取数据。", "INFO")

                elif self.cfg_domestic_source_mode == constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE:
                    source_type_log = "本地豆瓣(优先)"
                    douban_cast_list_source = self._try_get_cast_from_local_douban(api_imdb_id, current_media_type, display_path_for_gui)
                    if douban_cast_list_source is None: # 本地未获取到，尝试在线
                        self.log_to_progress_text_qt(f"本地豆瓣数据未找到或无效，尝试在线豆瓣API作为备选...", "INFO")
                        source_type_log = "在线豆瓣API(本地失败后备选)"
                        douban_cast_list_source = self._try_get_cast_from_online_douban(api_name, api_imdb_id, current_media_type, api_year)
                        if isinstance(douban_cast_list_source, dict) and douban_cast_list_source.get("is_api_call"):
                            used_online_api_call = True
                            self.failed_media_items.append({"path": tmdb_media_dir_path, "tmdb_id": tmdb_id_str, "type": current_media_type, "reason": f"豆瓣API错误(备选): {douban_cast_list_source.get('error')}"})
                            self.batch_stats["total_media_api_call_failed_early"] += 1
                            self._schedule_next_item_processing(self.current_api_cooldown_ms if "rate_limit" in douban_cast_list_source.get("error", "") else 10)
                            return
                        elif douban_cast_list_source is None:
                            self.log_to_progress_text_qt(f"在线豆瓣API(备选)也未能获取数据。", "INFO")
                else: # 未知的策略值
                    self.log_to_progress_text_qt(f"警告：未知的国产影视豆瓣数据源策略 '{self.cfg_domestic_source_mode}'。将默认尝试本地优先。", "WARN")
                    # 可以选择一个默认行为，例如本地优先
                    source_type_log = "本地豆瓣(默认)"
                    douban_cast_list_source = self._try_get_cast_from_local_douban(api_imdb_id, current_media_type, display_path_for_gui)
                    if douban_cast_list_source is None:
                        source_type_log = "在线豆瓣API(默认备选)"
                        douban_cast_list_source = self._try_get_cast_from_online_douban(api_name, api_imdb_id, current_media_type, api_year)
                        # ... (处理API错误) ...


                # --- 后续处理获取到的 douban_cast_list_source ---
                final_cast_to_write_to_json = local_tmdb_cast_raw.copy() if local_tmdb_cast_raw else []
                actors_updated_count = 0

                if douban_cast_list_source and isinstance(douban_cast_list_source, list): # 确保是列表且非空
                    self.log_to_progress_text_qt(f"准备使用来自“{source_type_log}”的 {len(douban_cast_list_source)} 位演员数据更新角色名...", "DEBUG")
                    
                    douban_actors_map_by_name = {}
                    douban_actors_map_by_latin_name = {}
                    douban_actors_map_by_tmdb_id = {}
                    for db_actor in douban_cast_list_source:
                        if db_actor.get("name"): douban_actors_map_by_name[db_actor.get("name").strip().lower()] = db_actor
                        if db_actor.get("original_name"): douban_actors_map_by_latin_name[db_actor.get("original_name").strip().lower()] = db_actor
                        if db_actor.get("id") and isinstance(db_actor.get("id"), int):
                            douban_actors_map_by_tmdb_id[db_actor.get("id")] = db_actor
                    
                    temp_updated_tmdb_cast = []
                    processed_tmdb_actor_indices = set() # 用于跟踪哪些原始TMDB演员已被豆瓣数据匹配和处理

                    # 优先用豆瓣数据构建基础，然后尝试用TMDB原始数据补充或验证
                    for idx_db, db_actor_info in enumerate(douban_cast_list_source):
                        final_actor_entry = db_actor_info.copy() # 以豆瓣信息为基础
                        final_actor_entry["order"] = idx_db # 先按豆瓣顺序
                        final_actor_entry["_source"] = final_actor_entry.get("_source", source_type_log) # 确保来源

                        # 尝试在原始TMDB cast中找到匹配的演员，以获取原始TMDB的order或其他可能需要保留的字段
                        # (例如，如果豆瓣没有TMDB Person ID，但TMDB JSON里有，我们可能想保留TMDB的ID)
                        matched_original_tmdb_actor = None
                        original_tmdb_actor_idx = -1

                        if local_tmdb_cast_raw:
                            for i, tmdb_orig_actor in enumerate(local_tmdb_cast_raw):
                                if i in processed_tmdb_actor_indices: continue # 这个原始TMDB演员已被用过

                                # 尝试多种匹配方式：TMDB ID (如果豆瓣有), 名称, 原始名称
                                if final_actor_entry.get("id") and tmdb_orig_actor.get("id") == final_actor_entry.get("id"):
                                    matched_original_tmdb_actor = tmdb_orig_actor; original_tmdb_actor_idx = i; break
                                if tmdb_orig_actor.get("name","").strip().lower() == final_actor_entry.get("name","").strip().lower():
                                    matched_original_tmdb_actor = tmdb_orig_actor; original_tmdb_actor_idx = i; break
                                if final_actor_entry.get("original_name") and tmdb_orig_actor.get("original_name","").strip().lower() == final_actor_entry.get("original_name","").strip().lower():
                                    matched_original_tmdb_actor = tmdb_orig_actor; original_tmdb_actor_idx = i; break
                        
                        if matched_original_tmdb_actor:
                            processed_tmdb_actor_indices.add(original_tmdb_actor_idx)
                            # 如果豆瓣条目缺少某些关键TMDB字段，从原始TMDB条目中补充
                            if final_actor_entry.get("id") is None and matched_original_tmdb_actor.get("id"):
                                final_actor_entry["id"] = matched_original_tmdb_actor.get("id")
                            if not final_actor_entry.get("profile_path") and matched_original_tmdb_actor.get("profile_path"):
                                final_actor_entry["profile_path"] = matched_original_tmdb_actor.get("profile_path")
                            # 保留原始TMDB的order，如果豆瓣没有提供或者我们更信任TMDB的顺序
                            final_actor_entry["order"] = matched_original_tmdb_actor.get("order", idx_db)
                            
                            # 角色名以豆瓣为准，如果豆瓣的角色名与TMDB的不同，则算作更新
                            db_char = clean_character_name_static(db_actor_info.get("character"))
                            tmdb_char = clean_character_name_static(matched_original_tmdb_actor.get("character"))
                            if db_char != tmdb_char:
                                actors_updated_count +=1
                            final_actor_entry["character"] = db_char # 确保使用豆瓣的角色名
                        
                        temp_updated_tmdb_cast.append(final_actor_entry)

                    # 添加那些在豆瓣列表中没有对应匹配的原始TMDB演员
                    if local_tmdb_cast_raw:
                        for i, tmdb_orig_actor in enumerate(local_tmdb_cast_raw):
                            if i not in processed_tmdb_actor_indices:
                                entry_to_add = tmdb_orig_actor.copy()
                                entry_to_add["_source"] = "tmdb_original_unmatched_by_douban"
                                temp_updated_tmdb_cast.append(entry_to_add)
                    
                    # 按 order 排序
                    temp_updated_tmdb_cast.sort(key=lambda x: x.get("order", 9999))
                    # 重新分配 order 确保连续性
                    for i_order, actor_entry_sorted in enumerate(temp_updated_tmdb_cast):
                        actor_entry_sorted["order"] = i_order

                    final_cast_to_write_to_json = temp_updated_tmdb_cast

                elif douban_cast_list_source is None: # 豆瓣数据获取完全失败 (在线和本地都失败，或策略导致不尝试)
                    if local_tmdb_cast_raw:
                        self.log_to_progress_text_qt(f"信息：媒体 '{display_path_for_gui}'，未能从任何豆瓣源获取到演员信息。将使用原始TMDB演员列表。", "INFO")
                    else:
                        self.log_to_progress_text_qt(f"警告：媒体 '{display_path_for_gui}'，豆瓣源和原始TMDB JSON均无演员信息。", "WARN")
                        final_cast_to_write_to_json = [] # 确保是空列表

                if actors_updated_count > 0:
                    self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': {actors_updated_count} 位演员的角色名已根据来自“{source_type_log}”的信息更新。", "INFO")
                elif douban_cast_list_source is not None: # 有豆瓣数据，但没有更新任何角色名
                    self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': 未找到可匹配更新的演员角色名（基于“{source_type_log}”数据）。", "INFO")
                # 如果 douban_cast_list_source is None，则不打印这条“未找到更新”的日志，因为前面已经打印过获取失败了

                self._finalize_item_processing(
                    final_cast_to_write_to_json,
                    tmdb_media_dir_path, tmdb_id_str, display_path_for_gui, item_key,
                    current_media_type,
                    use_api_source_for_cooldown=used_online_api_call # 只有当实际调用了在线API时才考虑API冷却
                )
                
    def _finalize_item_processing(self, final_cast_list, media_dir_path_in_main_cache, tmdb_id_str, display_path_for_gui,
                                  item_key, current_media_type,
                                  was_slow_process: bool = False, 
                                  use_api_source_for_cooldown=False):
        
        all_roles_valid_for_success_log = True
        first_invalid_reason_text = ""
        processed_item_successfully = False # 最终是否认为整个媒体项处理成功（用于记录到processed_log和统计）
        any_json_update_failed = False # 标记是否有任何一个JSON文件更新失败

        # 1. 基本有效性检查 (final_cast_list)
        if final_cast_list is None: # 通常不应发生，除非前面的逻辑有严重错误
            self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': 最终演员列表为空(None)，处理中止。", "ERROR")
            if not any(item["path"] == media_dir_path_in_main_cache for item in self.failed_media_items):
                 self.failed_media_items.append({"path": media_dir_path_in_main_cache, "tmdb_id": tmdb_id_str, "type": current_media_type, "reason": "处理后最终演员列表为None"})
            self._schedule_next_item_processing(10); return

        # 2. 检查角色名有效性 (仅当 final_cast_list 非空时才有意义)
        if final_cast_list: # 如果列表是空的（例如，电影确实没有演员，或用户清空了），则不进行角色名检查
            for actor_idx, actor_data in enumerate(final_cast_list):
                char_to_validate = actor_data.get("character", "")
                actor_name_for_log = actor_data.get("name", "未知演员")
                if not is_role_name_valid(char_to_validate, actor_name_for_log):
                    all_roles_valid_for_success_log = False
                    if not first_invalid_reason_text:
                        first_invalid_reason_text = f"角色名 '{char_to_validate}' (演员: {actor_name_for_log}) 不符规范 (在最终列表第{actor_idx+1}项)"
                    # break # 如果只想记录第一个错误，可以提前跳出检查
        # 如果 final_cast_list 为空，all_roles_valid_for_success_log 保持 True (因为没有无效角色名)
        
        # 3. 确定并创建权威的目标媒体项基础目录 (用于JSON和图片)
        authoritative_target_media_item_base_dir = os.path.normpath(media_dir_path_in_main_cache)
        if self.cfg_override_cache_path and \
           os.path.isdir(self.cfg_override_cache_path) and \
           os.path.normpath(self.cfg_override_cache_path).lower() != os.path.normpath(self.cfg_main_cache_path).lower():
            media_id_folder_name = os.path.basename(media_dir_path_in_main_cache) 
            path_above_media_id_folder = os.path.dirname(media_dir_path_in_main_cache) 
            type_subdir_name = os.path.basename(path_above_media_id_folder) 
            if type_subdir_name in ["tmdb-movies2", "tmdb-tv"]:
                authoritative_target_media_item_base_dir = os.path.normpath(os.path.join(self.cfg_override_cache_path, type_subdir_name, media_id_folder_name))
        try:
            if not os.path.exists(authoritative_target_media_item_base_dir):
                os.makedirs(authoritative_target_media_item_base_dir, exist_ok=True)
        except Exception as e_mkdir_authoritative:
            self.log_to_progress_text_qt(f"致命错误：创建权威目标目录 '{authoritative_target_media_item_base_dir}' 失败: {e_mkdir_authoritative}。将中止对此媒体项的文件操作。", "ERROR")
            self.failed_media_items.append({"path": media_dir_path_in_main_cache, "tmdb_id": tmdb_id_str, "type": current_media_type, "reason": f"创建目标目录失败: {e_mkdir_authoritative}"})
            self._schedule_next_item_processing(10); return 
        
        # 4. 获取并处理JSON文件
        json_files_to_process_from_main_cache = self._get_json_files_for_media_item(media_dir_path_in_main_cache, current_media_type)
        json_processing_successful_this_item = True # 标记当前媒体项的所有JSON文件是否都处理成功

        if not json_files_to_process_from_main_cache:
            if not all_roles_valid_for_success_log and final_cast_list: # 只有当有演员列表且其中有角色名问题时
                json_processing_successful_this_item = False # 即使没有JSON文件，但角色名有问题，也算处理不完全成功
                self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': 未找到JSON文件但角色名存在问题: {first_invalid_reason_text}。", "WARN")
            else:
                 self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': 未找到相关JSON文件更新。", "INFO")
                 # 如果没有JSON文件，并且(没有演员列表 或者 演员列表为空 或者 演员列表角色名都有效)，则认为此项处理成功
                 if not final_cast_list or all_roles_valid_for_success_log:
                      processed_item_successfully = True
        else: # 有JSON文件需要处理
            for f_path_in_main_cache in json_files_to_process_from_main_cache:
                if not self.batch_processing_active: any_json_update_failed = True; break # 如果中途停止
                
                target_json_save_path = ""
                try:
                    with open(f_path_in_main_cache, 'r', encoding='utf-8') as f_read: json_content = json.load(f_read)
                    updated_content, names_upd, chars_upd = self._update_characters_in_this_json(json_content, final_cast_list)
                    json_filename = os.path.basename(f_path_in_main_cache)
                    target_json_save_path = os.path.join(authoritative_target_media_item_base_dir, json_filename)
                    
                    has_changes = (names_upd > 0 or chars_upd > 0)
                    # 即使没变化，如果目标路径与源路径不同（例如从主缓存读，写到覆盖缓存），也需要写入
                    needs_write = has_changes or (os.path.normpath(target_json_save_path).lower() != os.path.normpath(f_path_in_main_cache).lower())

                    if needs_write:
                        with open(target_json_save_path, 'w', encoding='utf-8') as f_write: json.dump(updated_content, f_write, indent=4, ensure_ascii=False)
                        if has_changes: self.batch_stats['total_json_files_with_role_updates'] += 1
                        try: os.utime(target_json_save_path, None) # 触摸文件时间戳
                        except Exception as e_utime: self.log_to_progress_text_qt(f"  更新文件 '{json_filename}' 时间戳失败: {e_utime}", "WARN")
                    
                    self.batch_stats['total_json_files_processed_and_saved'] += 1 # 无论是否写入，只要处理了就计数
                except Exception as e_json_loop_general:
                    self.log_to_progress_text_qt(f"  处理/写入文件 '{os.path.basename(f_path_in_main_cache)}' 到 '{target_json_save_path}' 时发生错误: {e_json_loop_general}", "ERROR")
                    self.batch_stats['total_json_files_failed_to_process'] += 1; any_json_update_failed = True
                    json_processing_successful_this_item = False; break # 一个JSON失败则此媒体项的JSON处理失败
            
            if any_json_update_failed:
                self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': 部分JSON文件更新/保存失败。", "ERROR")
            
            # 如果JSON处理成功了，但角色名仍有问题 (且有演员列表)
            if json_processing_successful_this_item and not all_roles_valid_for_success_log and final_cast_list:
                json_processing_successful_this_item = False # 标记为不完全成功
                self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}': JSON已更新/复制，但因角色名问题: {first_invalid_reason_text}，标记为未完全成功。", "WARN")

        # 5. 图片下载逻辑 (这部分不影响 processed_item_successfully 的判断)
        # ... (should_download_images, emby_handler.download_emby_image 等逻辑保持不变) ...

        # 6. 最终判断此媒体项是否处理成功
        if json_processing_successful_this_item and (all_roles_valid_for_success_log or not final_cast_list):
            # 如果JSON处理成功，并且 (所有角色名都有效 或者 根本没有演员列表需要检查角色名)
            processed_item_successfully = True
        
        if processed_item_successfully:
            self.save_to_processed_log(current_media_type, tmdb_id_str)
            self.batch_stats["total_media_processed_successfully"] += 1
            self.log_to_progress_text_qt(f"媒体 '{display_path_for_gui}' 本地元数据处理成功。", "SUCCESS")

            # --- <<< 在此处根据配置决定是否立即触发单个Emby项目刷新 >>> ---
            if self.cfg_enable_emby_item_refresh: # <--- 新增的配置检查
                if self.cfg_emby_server_url and self.cfg_emby_api_key:
                    self.log_to_progress_text_qt(f"  自动处理：尝试为 '{display_path_for_gui}' (TMDb ID: {tmdb_id_str}) 发送Emby刷新请求...", "INFO")
                    
                    media_name_for_emby_search = display_path_for_gui
                    if self.current_item_context_for_async_processing:
                        name_from_context = self.current_item_context_for_async_processing.get('api_name')
                        if name_from_context and name_from_context != "---":
                            media_name_for_emby_search = name_from_context
                    
                    emby_item_id = emby_handler.get_emby_item_id_from_tmdb(
                        tmdb_id_str, media_name_for_emby_search, current_media_type,
                        self.cfg_emby_server_url, self.cfg_emby_api_key
                    )
                    if emby_item_id:
                        self.log_to_progress_text_qt(f"    自动处理：获取到Emby Item ID: {emby_item_id}。发送替换式刷新请求...", "DEBUG")
                        is_recursive = True if current_media_type == "tv" else False
                        refresh_success = emby_handler.refresh_emby_item_metadata(
                            emby_item_id, self.cfg_emby_server_url, self.cfg_emby_api_key,
                            recursive=is_recursive,
                            metadata_refresh_mode="FullRefresh",
                            image_refresh_mode="Default",      # 不强制刷新图片
                            replace_all_metadata_param=True,   # 强制替换元数据
                            replace_all_images_param=False     # 不强制替换图片
                        )
                        if refresh_success: self.log_to_progress_text_qt(f"    自动处理：Emby刷新请求已为 '{display_path_for_gui}' 发送。", "SUCCESS")
                        else: self.log_to_progress_text_qt(f"    自动处理：为 '{display_path_for_gui}' 发送Emby刷新请求失败。", "WARN")
                    else:
                        self.log_to_progress_text_qt(f"    自动处理：未能为 '{display_path_for_gui}' 找到对应的Emby Item ID，跳过Emby刷新。", "WARN")
                else:
                    self.log_to_progress_text_qt(f"  自动处理：Emby服务器未配置，无法对 '{display_path_for_gui}' 进行刷新。", "WARN")
            else: # 如果配置为不自动刷新
                self.log_to_progress_text_qt(f"  自动处理：根据配置，未对 '{display_path_for_gui}' 发送Emby刷新通知。", "INFO")
            # --- <<< 单个Emby项目刷新结束 >>> ---

        else: # 如果 processed_item_successfully 为 False
            reason_for_failure = first_invalid_reason_text # 优先使用角色名问题
            if not reason_for_failure and any_json_update_failed: # 如果角色名没问题，但JSON处理失败了
                reason_for_failure = "JSON文件处理/保存失败"
            elif not reason_for_failure: # 其他未知原因（理论上不应到这里）
                reason_for_failure = "未知处理错误导致未成功"
            
            # 避免重复添加相同的失败项
            if not any(item["path"] == media_dir_path_in_main_cache and item.get("reason","").startswith(reason_for_failure.split("(")[0].strip()) for item in self.failed_media_items):
                self.failed_media_items.append({
                    "path": media_dir_path_in_main_cache, "tmdb_id": tmdb_id_str,
                    "type": current_media_type, "reason": reason_for_failure
                })
                # 更新统计：只有当JSON本身没问题，纯粹是角色名问题时，才计入total_media_role_name_issue
                if "角色名" in reason_for_failure and not any_json_update_failed :
                    self.batch_stats["total_media_role_name_issue"] += 1
        
        # 7. 调度下一个项目处理
        cooldown_val = self.current_api_cooldown_ms if use_api_source_for_cooldown and not processed_item_successfully else 10
        self._schedule_next_item_processing(cooldown_val)


    def _schedule_next_item_processing(self, delay_ms): # 保持不变
        self.current_batch_index += 1
        if hasattr(self, 'batch_timer') and self.batch_timer:
            self.batch_timer.start(int(delay_ms))
        else:
            self.log_to_progress_text_qt("错误: Batch timer 未找到，无法调度下一项。", "ERROR")
            if self.batch_processing_active and self.current_batch_index < len(self.batch_media_dirs_to_process):
                 QTimer.singleShot(int(delay_ms), self._process_one_media_dir_in_batch_qt)
            else: self._finalize_batch_processing(interrupted=True)

    def _finalize_batch_processing(self, interrupted=False): # 保持不变
        if self.translation_watchdog_timer.isActive():
            self.translation_watchdog_timer.stop()
        self.batch_processing_active = False
        if hasattr(self, 'process_button'): self.process_button.setEnabled(True); self.process_button.setText("开始处理") # 修改按钮文本
        if hasattr(self, 'stop_button'): self.stop_button.setEnabled(False)
        duration_str = "00:00:00"
        if self.batch_start_time:
            end_time = time.time(); total_duration_seconds = round(end_time - self.batch_start_time)
            self.batch_start_time = None
            hours, rem = divmod(total_duration_seconds, 3600); mins, secs = divmod(rem, 60)
            duration_str = f"{int(hours):02d}:{int(mins):02d}:{int(secs):02d}"
        fixed_log_path = os.path.join(get_base_path_for_files(), constants.FIXED_FAILURE_LOG_FILENAME)
        self.last_batch_failed_items_log_path = fixed_log_path
        log_written_successfully, new_unique_failures_count = False, 0
        try:
            existing_failure_keys = set(); file_existed_before_write = os.path.exists(fixed_log_path)
            is_empty_file = file_existed_before_write and os.path.getsize(fixed_log_path) == 0
            if file_existed_before_write and not is_empty_file:
                with open(fixed_log_path, 'r', encoding='utf-8', newline='') as fr:
                    csv_reader = csv.reader(fr)
                    for row in csv_reader:
                        if row and len(row) == 4 and not row[0].strip().startswith(("#", "=")):
                            existing_failure_keys.add((row[0].strip().lower(), row[1].strip(), row[2].strip()))
            unique_new_failed_items_to_append = [ item for item in self.failed_media_items if (item.get('type','N/A').lower(), item.get('tmdb_id','N/A'), item.get('path','N/A')) not in existing_failure_keys ]
            new_unique_failures_count = len(unique_new_failed_items_to_append)
            if new_unique_failures_count > 0 or not file_existed_before_write or is_empty_file:
                with open(fixed_log_path, 'a' if file_existed_before_write and not is_empty_file else 'w', encoding='utf-8', newline='') as fa:
                    writer = csv.writer(fa); current_timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime())
                    if not file_existed_before_write or is_empty_file:
                        fa.write(f"# 处理失败的媒体列表 (创建于 {current_timestamp_str})\n# 文件名: {constants.FIXED_FAILURE_LOG_FILENAME}\n# 格式: 媒体类型,TMDB_ID,\"目录路径\",\"失败原因\"\n" + "="*50 + "\n")
                    elif new_unique_failures_count > 0: fa.write(f"\n# === 追加失败项 (批处理于 {current_timestamp_str}) ===\n")
                    if new_unique_failures_count > 0:
                        for item_to_log in unique_new_failed_items_to_append: writer.writerow([ item_to_log.get(k,'N/A') for k in ['type','tmdb_id','path','reason'] ])
            log_written_successfully = True
        except Exception as e_log: self.log_to_progress_text_qt(f"写入/追加失败列表 '{constants.FIXED_FAILURE_LOG_FILENAME}' 失败: {e_log}", "ERROR")
        title = "批量处理完成" if not interrupted else "批量处理已停止"
        processed_count_display = len(self.batch_media_dirs_to_process) if not interrupted else self.current_batch_index
        fail_log_message = ""; bs = self.batch_stats
        if new_unique_failures_count > 0: fail_log_message = f"\n本轮 {new_unique_failures_count} 个新失败项已追加到: {constants.FIXED_FAILURE_LOG_FILENAME}" if log_written_successfully else f"\n注意：本轮有 {new_unique_failures_count} 个新失败项，但写入日志 '{constants.FIXED_FAILURE_LOG_FILENAME}' 出错。"
        elif self.failed_media_items and new_unique_failures_count == 0: fail_log_message = f"\n本轮识别到 {len(self.failed_media_items)} 个失败项，但已存在于日志中。"
        else: fail_log_message = f"\n本轮无新失败项。日志 '{constants.FIXED_FAILURE_LOG_FILENAME}'" + (" 可能已更新时间戳。" if log_written_successfully else " 更新时出错。")
        summary_message = (
            f"{title}！\n\n总耗时: {duration_str}\n尝试处理目录数: {processed_count_display}\n"
            f" - 成功更新文件: {bs.get('total_media_processed_successfully',0)}\n"
            f" - 因角色名问题记录到失败列表: {bs.get('total_media_role_name_issue',0)}\n"
            f" - 因缓存跳过: {bs.get('total_media_skipped_due_to_cache',0)}\n"
            f" - 信息提取失败跳过: {bs.get('total_media_failed_to_extract_info',0)}\n"
            f" - 国产片本地角色名符合要求跳过API: {bs.get('total_media_skipped_due_to_precheck',0)}\n"
            f" - 因本地JSON无演员信息跳过处理: {bs.get('total_media_skipped_due_to_no_local_cast',0)}\n"
            f" - 外语片尝试本地翻译: {bs.get('total_media_directly_translated',0)}\n"
            f" - API调用失败或无结果 (国产片): {bs.get('total_media_api_call_failed_early',0)}\n\n"
            f"JSON文件扫描并保存总数: {bs.get('total_json_files_processed_and_saved',0)}\n"
            f" - 其中角色信息被更新的文件数: {bs.get('total_json_files_with_role_updates',0)}\n"
            f"JSON文件处理/写入失败数: {bs.get('total_json_files_failed_to_process',0)}"
            f"{fail_log_message}"
        )
        log_level_summary = "SUCCESS" if not interrupted and new_unique_failures_count == 0 and log_written_successfully else "WARN"
        self.log_to_progress_text_qt(summary_message, log_level_summary)
        QMessageBox.information(self, title, summary_message)
        self.api_type_label.setText("---") # 重置接口显示
        if hasattr(self, 'auto_name_display'): self.auto_name_display.setText("---")
        if hasattr(self, 'auto_media_type_label'): self.auto_media_type_label.setText("---")
        if not interrupted and hasattr(self, 'progress_bar'): QTimer.singleShot(3000, self._reset_progress_bar_qt)

    def close_app_resources(self): # 保持不变
        self.log_to_progress_text_qt("开始关闭应用程序资源...", "DEBUG")
        if hasattr(self, 'log_update_timer') and self.log_update_timer.isActive():
            self.log_update_timer.stop()
            self.log_to_progress_text_qt("日志更新定时器已停止。", "DEBUG")
        if hasattr(self, 'batch_timer') and self.batch_timer.isActive():
            self.batch_timer.stop()
            self.log_to_progress_text_qt("批处理定时器已停止。", "DEBUG")
        if hasattr(self, 'translation_watchdog_timer') and self.translation_watchdog_timer.isActive():
            self.translation_watchdog_timer.stop()
            self.log_to_progress_text_qt("翻译看门狗定时器已停止。", "DEBUG")
        active_threads_names_on_close = []
        if hasattr(self, 'current_translation_threads') and self.current_translation_threads:
            self.log_to_progress_text_qt(f"关闭时检测到 {len(self.current_translation_threads)} 个翻译线程，尝试停止...", "DEBUG")
            threads_to_close_copy = list(self.current_translation_threads)
            for thread_instance in threads_to_close_copy:
                if thread_instance.isRunning():
                    task_id_str = thread_instance.task_id if hasattr(thread_instance, 'task_id') else "Unknown Task"
                    active_threads_names_on_close.append(task_id_str)
                    thread_instance.quit()
                    if not thread_instance.wait(300):
                        thread_instance.terminate()
                        self.log_to_progress_text_qt(f"翻译线程 {task_id_str} 在关闭时被强制终止。", "WARN")
                    else:
                        self.log_to_progress_text_qt(f"翻译线程 {task_id_str} 在关闭时正常结束。", "DEBUG")
                if thread_instance in self.current_translation_threads:
                    self.current_translation_threads.remove(thread_instance)
        if active_threads_names_on_close:
             self.log_to_progress_text_qt(f"关闭时明确停止或终止了以下翻译线程: {', '.join(active_threads_names_on_close)}", "INFO")
        if hasattr(self, 'current_translation_threads'):
            self.current_translation_threads.clear()
            self.log_to_progress_text_qt("翻译线程列表已清空。", "DEBUG")
        if hasattr(self, '_douban_api_instance') and self._douban_api_instance and \
           hasattr(self._douban_api_instance, 'close') and callable(self._douban_api_instance.close):
            self.log_to_progress_text_qt("正在调用 DoubanApi 实例的 close 方法...", "DEBUG")
            self._douban_api_instance.close()
            self.log_to_progress_text_qt("DoubanApi 实例的 close 方法调用完成。", "DEBUG")
        self.log_to_progress_text_qt("应用程序资源关闭完成。", "DEBUG")

    def closeEvent(self, event): # 保持不变
        self.batch_processing_active = False
        self.close_app_resources()
        super().closeEvent(event)

# End of JSONEditorApp class