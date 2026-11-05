# -*- coding: utf-8 -*-
"""
微信 AI 总结助手 - CustomTkinter 桌面端界面
支持：增量总结、密钥提取、群聊筛选、AI模型配置、历史归档查看、三级隐私清理。
双模支持：既可通过 Python/Bat 直接运行，也可通过 Nuitka 编译为机器码 .exe 发布。
"""

import os
import sys

# 彻底兼容 pythonw 无黑框运行：防止 stdout/stderr 为 None 导致崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r", encoding="utf-8")

import json
import shutil
import queue
import threading
import datetime
import subprocess

# 彻底修复 Windows 编码与任务栏独立图标绑定
if os.name == 'nt':
    os.system('chcp 65001 >nul 2>&1')
    try:
        import ctypes
        # 显式注册独立 AppUserModelID，使任务栏显示专属图标而非 Python 默认图标
        my_app_id = 'canghe.wechat.ai.summary.assistant.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(my_app_id)
    except Exception:
        pass
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')

# 引入核心业务逻辑、配置路径与辅助工具
from auto_summary import (
    BASE_DIR,
    CONFIG_FILE,
    OUTPUT_DIR as SUMMARIES_DIR,
    STATE_FILE as LAST_SYNC_FILE,
    run_incremental_summary,
    load_user_config,
    load_full_config,
    save_full_config,
    update_full_config,
    get_today_doc_path,
    get_agy_path,
    get_antigravity_exe,
    get_ai_engine_desc,
    run_cli_command,
    load_state,
    save_state,
    get_subprocess_flags,
    get_stored_keys
)

try:
    import customtkinter as ctk
    from tkinter import messagebox, filedialog
except ImportError:
    print("[错误] 缺少 customtkinter 库，正在自动安装...")
    subprocess.run([sys.executable, "-m", "pip", "install", "customtkinter", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    import customtkinter as ctk
    from tkinter import messagebox, filedialog

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# 字体栈定义：优先微软正黑/微软雅黑UI，字形优雅清晰饱满；技术日志采用 Consolas
FONT_FAMILY_MAIN = "Microsoft YaHei UI"
FONT_FAMILY_MONO = "Consolas"


def safe_textbox_append(textbox: ctk.CTkTextbox, text: str):
    """向成果文本框追加文本并严格锁定视口，保留用户当前阅读位置"""
    raw = textbox._textbox
    try:
        top_index = raw.index("@0,0")
    except Exception:
        top_index = "1.0"

    textbox.insert("end", text)

    try:
        raw.see(top_index)
    except Exception:
        pass


def log_textbox_append(textbox: ctk.CTkTextbox, text: str):
    """向日志文本框追加内容并实时自动滚动滑到最新最底部"""
    textbox.insert("end", text)
    try:
        textbox._textbox.see("end")
    except Exception:
        pass


class WeChatAssistantApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("微信 AI 增量总结助手")
        self.geometry("1040x700")
        self.minsize(940, 620)

        # 线程通信队列与全局状态
        self.log_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.is_running = False
        self.key_count = 0
        self.keys_drawer_visible = False
        self.configured_groups = []
        self.cached_wechat_groups = []

        # 设置专属窗口与任务栏图标
        self.icon_path = os.path.join(BASE_DIR, "app.ico")
        self._set_app_icon()

        # 网格布局：左侧导航栏 + 右侧主内容区
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._build_views()

        # 默认选中第一个视图并开启队列消费者
        self.select_view("summary")
        self.after(100, self._process_queues)

    def _set_app_icon(self):
        """设置专属窗口与任务栏图标"""
        def _apply():
            try:
                if hasattr(self, 'icon_path') and os.path.exists(self.icon_path):
                    self.iconbitmap(self.icon_path)
            except Exception:
                pass
        _apply()
        self.after(150, _apply)

    def _build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=190, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        # 品牌 LOGO
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="⚡ 微信 AI 总结",
            font=ctk.CTkFont(size=19, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 15))

        self.nav_buttons = {}
        views = [
            ("summary", "⚡ 增量总结"),
            ("keys", "🔑 密钥管理"),
            ("groups", "🎯 监控群聊"),
            ("model", "🤖 AI 模型"),
            ("history", "📄 历史归档"),
            ("cleanup", "🧹 隐私清理"),
        ]

        for idx, (view_id, title) in enumerate(views, start=1):
            btn = ctk.CTkButton(
                self.sidebar_frame,
                text=title,
                height=38,
                corner_radius=8,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                anchor="w",
                command=lambda v=view_id: self.select_view(v)
            )
            btn.grid(row=idx, column=0, padx=12, pady=4, sticky="ew")
            self.nav_buttons[view_id] = btn

        # 底部外观模式切换
        self.appearance_label = ctk.CTkLabel(self.sidebar_frame, text="主题模式:", font=ctk.CTkFont(size=12))
        self.appearance_label.grid(row=8, column=0, padx=20, pady=(10, 0), sticky="w")
        self.appearance_mode_menu = ctk.CTkOptionMenu(
            self.sidebar_frame,
            values=["Dark", "Light", "System"],
            command=ctk.set_appearance_mode
        )
        self.appearance_mode_menu.grid(row=9, column=0, padx=20, pady=(5, 20), sticky="ew")

    def _build_views(self):
        self.views = {}

        # 1. 增量总结视图
        self.views["summary"] = self._create_summary_view()
        # 2. 密钥管理视图
        self.views["keys"] = self._create_keys_view()
        # 3. 监控群聊视图
        self.views["groups"] = self._create_groups_view()
        # 4. AI 模型视图
        self.views["model"] = self._create_model_view()
        # 5. 历史归档视图
        self.views["history"] = self._create_history_view()
        # 6. 隐私清理视图
        self.views["cleanup"] = self._create_cleanup_view()

    def select_view(self, name: str):
        # 切换高亮按钮
        for v_id, btn in self.nav_buttons.items():
            if v_id == name:
                btn.configure(fg_color=("gray75", "gray30"))
            else:
                btn.configure(fg_color="transparent")

        # 切换主区域视图
        for v_id, frame in self.views.items():
            if v_id == name:
                frame.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
                if hasattr(frame, "on_show"):
                    frame.on_show()
            else:
                frame.grid_forget()

    # =========================================================================
    # 视图 1：增量总结 (主视图)
    # =========================================================================
    def _create_summary_view(self):
        """构建增量总结主视图：包含状态栏、密钥抽屉、操作栏、动态进度及成果/日志双标签页"""
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)

        # 1. 顶部状态卡片 (row 0)
        self._build_top_status_card(frame)

        # 2. 密钥展开抽屉 (row 1, 初始隐藏)
        self._build_keys_drawer(frame)

        # 3. 操作工具栏 (row 2)
        self._build_action_bar(frame)

        # 4. 实时动态状态条 (row 3)
        self.lbl_live_status = ctk.CTkLabel(
            frame,
            text="💡 提示：点击「立即执行增量总结」提炼群聊要点",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
            text_color=("gray40", "gray70"),
            anchor="w"
        )
        self.lbl_live_status.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))

        # 5. 分离的选项卡输出区：AI 总结成果 + 运行过程日志 (row 4)
        self._build_output_tabs(frame)

        def on_show():
            self._refresh_summary_status()
        frame.on_show = on_show
        return frame

    def _build_top_status_card(self, parent):
        """顶部状态摘要卡片"""
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=5)
        card.grid_columnconfigure((0, 1, 2), weight=1)

        self.lbl_monitored_groups = ctk.CTkLabel(
            card, text="当前监控: 加载中...", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13, weight="bold")
        )
        self.lbl_monitored_groups.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        self.lbl_ai_engine = ctk.CTkLabel(
            card, text="AI 引擎: 加载中...", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13)
        )
        self.lbl_ai_engine.grid(row=0, column=1, padx=15, pady=10, sticky="w")

        self.btn_key_status = ctk.CTkButton(
            card,
            text="🔑 密钥就绪 (16个) ▾",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13, weight="bold"),
            fg_color="transparent",
            hover_color=("gray80", "gray25"),
            text_color=("green", "lightgreen"),
            width=140,
            height=28,
            cursor="hand2",
            command=self._toggle_keys_drawer
        )
        self.btn_key_status.grid(row=0, column=2, padx=15, pady=8, sticky="e")

    def _build_keys_drawer(self, parent):
        """一键展开/收起的 16 个微信 SQLite 数据库密钥抽屉面板"""
        self.keys_drawer = ctk.CTkFrame(parent, corner_radius=10, fg_color=("gray90", "#1E222A"))
        self.keys_drawer_visible = False

        top_row = ctk.CTkFrame(self.keys_drawer, fg_color="transparent")
        top_row.pack(fill="x", padx=15, pady=(10, 5))

        title = ctk.CTkLabel(
            top_row,
            text="🔑 微信 SQLite 数据库密钥列表 (已提取就绪，全只读模式)",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13, weight="bold")
        )
        title.pack(side="left")

        btn_close = ctk.CTkButton(
            top_row,
            text="✕ 收起",
            width=65,
            height=24,
            fg_color=("gray75", "gray30"),
            command=self._toggle_keys_drawer
        )
        btn_close.pack(side="right")

        self.scroll_keys_list = ctk.CTkScrollableFrame(self.keys_drawer, height=170, corner_radius=8)
        self.scroll_keys_list.pack(fill="both", expand=True, padx=10, pady=(5, 10))

    def _toggle_keys_drawer(self):
        """切换密钥面板的展开与收起状态"""
        self.keys_drawer_visible = not self.keys_drawer_visible
        if self.keys_drawer_visible:
            self.keys_drawer.grid(row=1, column=0, sticky="ew", pady=(0, 10), padx=5)
            self._render_keys_drawer_items()
        else:
            self.keys_drawer.grid_forget()
        arrow = '▴' if self.keys_drawer_visible else '▾'
        self.btn_key_status.configure(text=f"🔑 密钥就绪 ({self.key_count}个) {arrow}")

    def _render_keys_drawer_items(self):
        """渲染抽屉内的 16 个密钥详情项"""
        for w in self.scroll_keys_list.winfo_children():
            w.destroy()

        data = get_stored_keys()
        if not data:
            ctk.CTkLabel(self.scroll_keys_list, text="⚠️ 未找到密钥文件，请在左侧「密钥管理」中点击提取。").pack(pady=15)
            return

        for db_rel, info in data.items():
            self._build_key_item_row(db_rel, info)

    def _build_key_item_row(self, db_rel: str, info: dict):
        """构建单个数据库密钥条目行"""
        row = ctk.CTkFrame(self.scroll_keys_list, fg_color="transparent")
        row.pack(fill="x", pady=2, padx=5)

        size_mb = info.get("size_mb", 0.0)
        enc_key = info.get("enc_key", "")
        masked = f"{enc_key[:8]}...{enc_key[-8:]}" if len(enc_key) > 16 else enc_key

        ctk.CTkLabel(
            row,
            text=f"📄 {db_rel} ({size_mb} MB)",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12, weight="bold"),
            width=240,
            anchor="w"
        ).pack(side="left")

        ctk.CTkLabel(
            row,
            text=masked,
            font=ctk.CTkFont(family=FONT_FAMILY_MONO, size=12),
            text_color="gray",
            width=210,
            anchor="w"
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            row,
            text="📋 复制Key",
            width=75,
            height=24,
            command=lambda k=enc_key, n=db_rel: self._copy_key_to_clipboard(k, n)
        ).pack(side="right", padx=5)

    def _copy_to_clipboard(self, text: str, tip_title: str, tip_msg: str):
        """通用剪贴板复制辅助函数"""
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo(tip_title, tip_msg)

    def _copy_key_to_clipboard(self, key_str: str, db_name: str):
        """复制指定数据库的密钥到剪贴板"""
        self._copy_to_clipboard(key_str, "已复制", f"已成功复制【{db_name}】的 64 位数据库密钥！")

    def _build_action_bar(self, parent):
        """顶部操作工具栏：增量总结触发、默认不自动写入文档开关、打开文档"""
        action_bar = ctk.CTkFrame(parent, fg_color="transparent")
        action_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8), padx=5)

        self.btn_run_summary = ctk.CTkButton(
            action_bar,
            text="🚀 立即执行增量总结",
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=14, weight="bold"),
            command=self._start_summary_task
        )
        self.btn_run_summary.pack(side="left", padx=(0, 12))

        # 读取配置，默认不自动写入文档（False）
        cfg = load_full_config()
        self.auto_save_var = ctk.BooleanVar(value=cfg.get("auto_save_to_doc", False))
        self.switch_auto_save = ctk.CTkSwitch(
            action_bar,
            text="自动写入今日归档文档",
            variable=self.auto_save_var,
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
            command=self._on_auto_save_toggled
        )
        self.switch_auto_save.pack(side="left", padx=10)

        self.btn_open_today = ctk.CTkButton(
            action_bar,
            text="📄 打开今日文档",
            width=120,
            height=36,
            fg_color=("gray70", "gray30"),
            command=self._open_today_doc
        )
        self.btn_open_today.pack(side="right", padx=(10, 0))

    def _on_auto_save_toggled(self):
        """持久化记忆自动写入今日文档的偏好设置"""
        update_full_config(auto_save_to_doc=self.auto_save_var.get())

    def _build_output_tabs(self, parent):
        """构建分离的选项卡：Tab 1 总结成果，Tab 2 运行过程日志"""
        self.output_tabview = ctk.CTkTabview(parent, corner_radius=10, command=self._on_output_tab_changed)
        self.output_tabview.grid(row=4, column=0, sticky="nsew", padx=5, pady=(0, 5))

        tab_results = self.output_tabview.add("✨ AI 总结成果")
        tab_logs = self.output_tabview.add("📋 运行过程日志")
        self.output_tabview.set("✨ AI 总结成果")

        self._build_results_tab(tab_results)
        self._build_logs_tab(tab_logs)

    def _on_output_tab_changed(self):
        """切换选项卡时：如果切换到日志标签页，自动滚动至最新位置"""
        if self.output_tabview.get() == "📋 运行过程日志":
            try:
                self.txt_summary_log._textbox.see("end")
            except Exception:
                pass

    def _build_results_tab(self, tab):
        """构建 AI 总结成果展示选项卡 (排版舒适、大号微软雅黑)"""
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        ctk.CTkLabel(
            bar, text="📑 提炼成果展示 (已剔除杂音与底层日志，专心阅读重点)",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12), text_color="gray"
        ).pack(side="left")

        ctk.CTkButton(
            bar, text="📋 复制全部总结", width=110, height=28,
            fg_color=("gray75", "gray30"), command=self._copy_summary_results
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            bar, text="🧹 清空成果", width=85, height=28,
            fg_color=("gray75", "gray30"), command=self._clear_summary_results
        ).pack(side="right")

        self.txt_summary_result = ctk.CTkTextbox(
            tab, corner_radius=8, font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=14),
            wrap="word", text_color=("gray10", "#F1F5F9")
        )
        self.txt_summary_result.grid(row=1, column=0, sticky="nsew")

    def _build_logs_tab(self, tab):
        """构建运行过程日志选项卡 (等宽 Consolas 技术日志)"""
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        ctk.CTkLabel(
            bar, text="🔍 技术运行日志 (包含消息抓取、跳过原因、时间戳对齐与报错)",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12), text_color="gray"
        ).pack(side="left")

        ctk.CTkButton(
            bar, text="🧹 清空日志", width=85, height=28,
            fg_color=("gray75", "gray30"), command=self._clear_summary_log
        ).pack(side="right")

        self.txt_summary_log = ctk.CTkTextbox(
            tab, corner_radius=8, font=ctk.CTkFont(family=FONT_FAMILY_MONO, size=12),
            wrap="word"
        )
        self.txt_summary_log.grid(row=1, column=0, sticky="nsew")

    def _copy_summary_results(self):
        """一键复制 AI 总结全部成果到剪贴板"""
        content = self.txt_summary_result.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("提示", "当前暂无总结成果可复制。")
            return
        self._copy_to_clipboard(content, "成功", "已将全部 AI 总结成果复制到剪贴板！")

    def _clear_summary_results(self):
        """清空成果面板并同步清理已解码的图片临时缓存"""
        self.txt_summary_result.delete("1.0", "end")
        try:
            from wechat_cli.core.image_decoder import clear_image_cache
            count = clear_image_cache()
            if count > 0:
                self._log_summary(f"🧹 已清空成果面板，并同步删除 {count} 张临时缓存图片。")
        except Exception:
            pass

    def _clear_summary_log(self):
        """清空日志面板"""
        self.txt_summary_log.delete("1.0", "end")

    def _refresh_summary_status(self):
        """刷新增量总结主界面的顶部状态提示"""
        cfg = load_full_config()
        groups = cfg.get("monitored_groups", [])
        enabled_count = sum(1 for g in groups if isinstance(g, dict) and g.get("enabled"))

        if groups:
            self.lbl_monitored_groups.configure(
                text=f"🎯 监控: 已启用 {enabled_count} 个群 (共保留 {len(groups)} 个)"
            )
        else:
            patterns = cfg.get("group_patterns", ["*北京移动*"])
            self.lbl_monitored_groups.configure(text=f"🎯 监控规则: {', '.join(patterns)}")

        self.lbl_ai_engine.configure(text=f"🤖 AI 引擎: {get_ai_engine_desc(cfg)}")

        keys_data = get_stored_keys()
        self.key_count = len(keys_data)
        if self.key_count > 0:
            arrow = '▴' if self.keys_drawer_visible else '▾'
            self.btn_key_status.configure(
                text=f"🔑 密钥就绪 ({self.key_count}个) {arrow}",
                text_color=("green", "lightgreen")
            )
        else:
            self.btn_key_status.configure(text="⚠️ 密钥未就绪", text_color=("red", "orange"))

    def _log_summary(self, text: str):
        """由后台线程安全将日志推送至日志队列"""
        self.log_queue.put(text)

    def _on_summary_result(self, item: dict):
        """由后台线程安全将 AI 总结成果推送至成果队列"""
        self.result_queue.put(item)

    def _process_queues(self):
        """定时消费日志与成果队列：日志自动滚到最新底部，AI总结成果严格锁定阅读视口"""
        # 1. 处理技术日志 (实时追加并自动滑到最新最底部)
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            log_textbox_append(self.txt_summary_log, msg + "\n")
            # 实时更新简明进度条
            if "正在拉取" in msg or "正在全量分析" in msg or "重点总结已提炼完成" in msg:
                clean_msg = msg.strip().replace("\n", " ")
                if len(clean_msg) > 60:
                    clean_msg = clean_msg[:60] + "..."
                self.lbl_live_status.configure(text=clean_msg, text_color=("blue", "#38BDF8"))

        # 2. 处理 AI 提炼成果 (锁定视口，用户看到哪就停留在哪，不强制滚到底部)
        while not self.result_queue.empty():
            item = self.result_queue.get_nowait()
            body = item['summary'].strip()
            sep = "=" * 55
            card = (
                f"\n{sep}\n"
                f"📌 [{item['index']}/{item['total']}] 【{item['chat_name']}】重点总结 (新增 {item['count']} 条发言)\n"
                f"{sep}\n\n"
                f"{body}\n\n"
            )
            safe_textbox_append(self.txt_summary_result, card)

        self.after(100, self._process_queues)

    def _start_summary_task(self):
        """启动增量总结后台工作线程"""
        if self.is_running:
            return

        self.is_running = True
        self.btn_run_summary.configure(state="disabled", text="⏳ 正在检测与总结中...")
        self.lbl_live_status.configure(
            text="⏳ 增量总结检测启动中，正在连接本地数据库...",
            text_color=("blue", "#38BDF8")
        )
        auto_save = self.auto_save_var.get()

        def worker():
            try:
                self._log_summary(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] 启动微信增量消息总结...")
                run_incremental_summary(
                    log_fn=self._log_summary,
                    ask_save_fn=None,
                    auto_save=auto_save,
                    result_fn=self._on_summary_result
                )
            except Exception as e:
                self._log_summary(f"\n❌ 执行出错: {str(e)}")
            finally:
                self.after(0, self._on_summary_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _on_summary_finished(self):
        """增量总结完成回调"""
        self.is_running = False
        self.btn_run_summary.configure(state="normal", text="🚀 立即执行增量总结")
        self.lbl_live_status.configure(
            text="✅ 增量总结执行完毕！最新成果已呈现在「✨ AI 总结成果」中",
            text_color=("green", "lightgreen")
        )
        self._refresh_summary_status()

    def _open_today_doc(self):
        """打开今日总结归档 Markdown 文档"""
        doc = get_today_doc_path()
        if os.path.exists(doc):
            os.system(f'start "" "{doc}"')
        else:
            messagebox.showinfo("提示", f"今日文档尚未生成（今天暂无增量记录或未开启写入）：\n{doc}")

    # =========================================================================
    # 视图 2：密钥管理
    # =========================================================================
    def _create_keys_view(self):
        """构建密钥提取与扫码工具视图"""
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(frame, corner_radius=10)
        card.pack(fill="x", padx=10, pady=10)

        title = ctk.CTkLabel(card, text="🔑 微信解密密钥一键提取 / 更换工具", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=16, weight="bold"))
        title.pack(anchor="w", padx=20, pady=(15, 5))

        desc = ctk.CTkLabel(
            card,
            text="从 Windows 微信 (Weixin.exe) 内存特征码中快速提取 64 位数据库密钥，全只读安全扫描，无账号风险。\n"
                 "每次切换微信账号、重新扫码登录微信、或初次使用时，请点击下方按钮提取密钥。",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
            text_color="gray",
            justify="left"
        )
        desc.pack(anchor="w", padx=20, pady=(0, 15))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 15))

        self.btn_extract_keys = ctk.CTkButton(
            btn_row,
            text="⚡ 一键扫描微信内存并提取密钥",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=14, weight="bold"),
            command=self._extract_keys_task
        )
        self.btn_extract_keys.pack(side="left")

        self.lbl_keys_info = ctk.CTkLabel(btn_row, text="", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13))
        self.lbl_keys_info.pack(side="left", padx=15)

        log_card = ctk.CTkFrame(frame, corner_radius=10)
        log_card.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        lbl_log = ctk.CTkLabel(log_card, text="扫描与提取日志:", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=14, weight="bold"))
        lbl_log.pack(anchor="w", padx=15, pady=(10, 5))

        self.txt_keys_log = ctk.CTkTextbox(log_card, font=ctk.CTkFont(family=FONT_FAMILY_MONO, size=13))
        self.txt_keys_log.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        def on_show():
            self._update_keys_info()
        frame.on_show = on_show
        return frame

    def _update_keys_info(self):
        """检查并更新密钥库状态"""
        keys_data = get_stored_keys()
        if keys_data:
            self.lbl_keys_info.configure(text=f"✅ 已配置 {len(keys_data)} 个微信数据库密钥", text_color=("green", "lightgreen"))
        else:
            self.lbl_keys_info.configure(text="⚠️ 尚未提取密钥，请先点击提取", text_color=("red", "orange"))

    def _extract_keys_task(self):
        """执行后台密钥扫描提取（静默无黑框）"""
        self.btn_extract_keys.configure(state="disabled", text="⏳ 正在扫描微信内存...")
        self.txt_keys_log.delete("1.0", "end")

        def worker():
            def log(m):
                self.after(0, lambda: log_textbox_append(self.txt_keys_log, m + "\n"))

            log("正在检查微信客户端运行状态...")
            kwargs = get_subprocess_flags()
            check = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/NH"],
                                   capture_output=True, text=True, errors="replace", **kwargs)
            if "Weixin.exe" not in check.stdout:
                log("⚠️ 未检测到正在运行的 Weixin.exe！请先登录电脑端微信后再试。")
                self.after(0, lambda: self.btn_extract_keys.configure(state="normal", text="⚡ 一键扫描微信内存并提取密钥"))
                return

            log("检测到 Weixin.exe 运行中，开始提取密钥...")
            try:
                from click.testing import CliRunner
                from wechat_cli.main import cli as wechat_cli_app
                runner = CliRunner()
                res = runner.invoke(wechat_cli_app, ["init"])
                log(res.output if res.output else "[无输出]")
                if res.exit_code == 0:
                    log("✅ 微信解密密钥提取完成！")
                else:
                    log("❌ 提取失败，请检查微信是否扫码登录。")
            except Exception as e:
                log(f"执行异常: {str(e)}")

            self.after(0, lambda: (self.btn_extract_keys.configure(state="normal", text="⚡ 一键扫描微信内存并提取密钥"), self._update_keys_info()))

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # 视图 3：监控群聊 (精细清单勾选 + 微信活跃群直接呈现点选)
    # =========================================================================
    def _create_groups_view(self):
        """构建监控群聊管理视图：上下双卡片架构"""
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure((0, 1), weight=1)

        self._build_configured_groups_card(frame)
        self._build_discovered_groups_card(frame)

        frame.on_show = self._on_groups_view_shown
        return frame

    def _build_configured_groups_card(self, parent):
        """Card 1: 已定制的监控群聊清单卡片 (精准勾选、增删、保存)"""
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.grid(row=0, column=0, sticky="nsew", padx=10, pady=(5, 6))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)

        self.lbl_configured_title = ctk.CTkLabel(
            card,
            text="🎯 我的监控群聊清单 (勾选以启用总结，未勾选仅保留在列表中跳过总结)",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=15, weight="bold")
        )
        self.lbl_configured_title.grid(row=0, column=0, sticky="w", padx=15, pady=(10, 6))

        row_add = ctk.CTkFrame(card, fg_color="transparent")
        row_add.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 8))

        self.ent_add_group = ctk.CTkEntry(
            row_add,
            placeholder_text="输入群聊精确全名，例如：北京移动7群",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
            width=360
        )
        self.ent_add_group.pack(side="left", fill="x", expand=True, padx=(0, 10))

        buttons = [
            ("➕ 手动添加", 95, "#2563EB", self._add_group_from_entry),
            ("☑️ 全选", 70, ("gray75", "gray30"), lambda: self._toggle_all_groups(True)),
            ("⬜ 全不选", 75, ("gray75", "gray30"), lambda: self._toggle_all_groups(False)),
            ("💾 保存清单", 90, "#2563EB", lambda: self._save_groups(notify=True)),
        ]
        for txt, w, fg, cmd in buttons:
            kw = {"fg_color": fg} if fg else {}
            ctk.CTkButton(row_add, text=txt, width=w, command=cmd, **kw).pack(side="left", padx=(0, 8))

        self.scroll_configured_groups = ctk.CTkScrollableFrame(card, corner_radius=8)
        self.scroll_configured_groups.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _build_discovered_groups_card(self, parent):
        """Card 2: 微信本地活跃群聊发现卡片 (直接呈现全部现有群聊、实时搜索、一键点选加入)"""
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        disc_header = ctk.CTkFrame(card, fg_color="transparent")
        disc_header.grid(row=0, column=0, sticky="ew", padx=15, pady=(8, 6))

        self.lbl_disc_title = ctk.CTkLabel(
            disc_header,
            text="🔍 微信本地活跃群聊 (点击「选择加入监控」按钮快速加入上方清单)",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=14, weight="bold")
        )
        self.lbl_disc_title.pack(side="left")

        self.ent_search_discovered = ctk.CTkEntry(
            disc_header,
            placeholder_text="实时搜索群名或发言...",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12),
            width=220
        )
        self.ent_search_discovered.pack(side="left", padx=12)
        self.ent_search_discovered.bind("<KeyRelease>", lambda e: self._filter_and_render_discovered())

        btn_fetch_all = ctk.CTkButton(
            disc_header,
            text="🔄 刷新全部活跃群",
            width=135,
            height=28,
            fg_color=("gray75", "gray30"),
            command=lambda: self._fetch_all_wechat_groups(force=True)
        )
        btn_fetch_all.pack(side="left")

        self.scroll_discovered_groups = ctk.CTkScrollableFrame(card, corner_radius=8)
        self.scroll_discovered_groups.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _on_groups_view_shown(self):
        """切换到群聊视图时的自动刷新与呈现逻辑"""
        self._load_and_render_groups()
        if not self.cached_wechat_groups:
            self._fetch_all_wechat_groups(force=False)
        else:
            self._filter_and_render_discovered()

    def _load_and_render_groups(self):
        """加载配置并渲染群聊列表"""
        cfg = load_full_config()
        self.configured_groups = cfg.get("monitored_groups", [])
        self._render_configured_groups()

    def _render_configured_groups(self):
        """渲染已配置群聊的每一项及勾选框"""
        for w in self.scroll_configured_groups.winfo_children():
            w.destroy()

        enabled_count = sum(1 for g in self.configured_groups if g.get("enabled"))
        total_count = len(self.configured_groups)
        self.lbl_configured_title.configure(
            text=f"🎯 我的监控群聊清单 (已添加 {total_count} 个，已勾选启用 {enabled_count} 个)"
        )

        if not self.configured_groups:
            ctk.CTkLabel(
                self.scroll_configured_groups,
                text="当前监控清单为空。可在下方微信活跃群聊中点击「➕ 选择加入监控」添加。",
                font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
                text_color="gray"
            ).pack(pady=25)
            return

        for item in self.configured_groups:
            self._build_configured_group_row(item)

    def _build_configured_group_row(self, item: dict):
        """构建单个监控群聊条目组件（含复选框、状态指示、移除按钮）"""
        name = item.get("name", "").strip()
        enabled = bool(item.get("enabled", True))

        row = ctk.CTkFrame(self.scroll_configured_groups, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=3)

        var = ctk.BooleanVar(value=enabled)
        ctk.CTkCheckBox(
            row,
            text=name,
            variable=var,
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
            command=lambda n=name, v=var: self._on_group_check_changed(n, v.get())
        ).pack(side="left", padx=5)

        status_text = " [已启用总结]" if enabled else " [已暂停总结-仅保留]"
        status_color = ("green", "#4ADE80") if enabled else "gray"
        ctk.CTkLabel(
            row,
            text=status_text,
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12),
            text_color=status_color
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            row,
            text="🗑️ 移除",
            width=65,
            height=26,
            fg_color=("gray75", "gray30"),
            command=lambda n=name: self._remove_group(n)
        ).pack(side="right", padx=5)

    def _save_groups(self, notify: bool = False):
        """持久化保存群聊清单配置"""
        update_full_config(monitored_groups=self.configured_groups)
        if notify:
            messagebox.showinfo("成功", "监控群聊清单已成功保存！增量总结将精准生效。")

    def _sync_groups_state(self):
        """统一持久化群聊配置并同步更新所有相关视图"""
        self._save_groups(notify=False)
        self._render_configured_groups()
        self._refresh_summary_status()
        self._filter_and_render_discovered()

    def _on_group_check_changed(self, group_name: str, enabled: bool):
        """当用户在群聊清单中勾选或取消勾选时触发"""
        for g in self.configured_groups:
            if g.get("name") == group_name:
                g["enabled"] = enabled
                break
        self._sync_groups_state()

    def _add_group_from_entry(self):
        """从输入框精确添加群聊"""
        name = self.ent_add_group.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入要添加的群聊名称！")
            return
        self._add_group_direct(name, show_popup=True)
        self.ent_add_group.delete(0, "end")

    def _add_group_direct(self, name: str, show_popup: bool = True):
        """将群聊加入监控清单"""
        if any(g.get("name") == name for g in self.configured_groups):
            if show_popup:
                messagebox.showinfo("提示", f"【{name}】已在监控清单中！")
            return
        self.configured_groups.append({"name": name, "enabled": True})
        self._sync_groups_state()
        if show_popup:
            messagebox.showinfo("成功", f"已成功将【{name}】添加并启用总结！")

    def _remove_group(self, name: str):
        """从监控清单中移除群聊"""
        self.configured_groups = [g for g in self.configured_groups if g.get("name") != name]
        self._sync_groups_state()

    def _toggle_all_groups(self, enabled: bool):
        """批量全选/全不选群聊"""
        for g in self.configured_groups:
            g["enabled"] = enabled
        self._sync_groups_state()

    def _fetch_all_wechat_groups(self, force: bool = False):
        """从微信本地数据库读取活跃群聊并直接列出"""
        for w in self.scroll_discovered_groups.winfo_children():
            w.destroy()
        loading = ctk.CTkLabel(
            self.scroll_discovered_groups,
            text="⏳ 正在直接读取微信本地活跃群聊，请稍候...",
            font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13)
        )
        loading.pack(pady=20)

        def worker():
            raw = run_cli_command(["sessions", "--limit", "300", "--format", "json"])
            group_items = []
            if raw:
                try:
                    data = json.loads(raw)
                    seen = set()
                    for s in data:
                        if s.get("is_group"):
                            chat = s.get("chat", "").strip()
                            if chat and chat not in seen:
                                seen.add(chat)
                                group_items.append(s)
                except Exception:
                    pass
            self.cached_wechat_groups = group_items
            try:
                self.after(0, self._filter_and_render_discovered)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _filter_and_render_discovered(self):
        """按搜索词过滤并渲染下方的微信活跃群列表"""
        kw = self.ent_search_discovered.get().strip().lower()
        if kw:
            filtered = [
                g for g in self.cached_wechat_groups
                if kw in g.get("chat", "").lower()
                or kw in g.get("last_message", "").lower()
                or kw in g.get("sender", "").lower()
            ]
        else:
            filtered = self.cached_wechat_groups

        self._display_discovered_groups(filtered)

    def _display_discovered_groups(self, groups: list):
        """渲染从微信读取到的群聊列表，支持上下滑动与一键选择加入"""
        for w in self.scroll_discovered_groups.winfo_children():
            w.destroy()

        self.lbl_disc_title.configure(
            text=f"🔍 微信本地活跃群聊 (已列出 {len(groups)}/{len(self.cached_wechat_groups)} 个，点击按钮加入监控)"
        )

        if not groups:
            ctk.CTkLabel(
                self.scroll_discovered_groups,
                text="未找到匹配群聊。可点击「🔄 刷新全部活跃群」重新读取。",
                font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13),
                text_color="gray"
            ).pack(pady=20)
            return

        configured_names = {g["name"] for g in self.configured_groups}
        for item in groups:
            chat_name = item.get("chat", "").strip()
            self._build_discovered_group_row(item, chat_name in configured_names)

    def _build_discovered_group_row(self, item: dict, in_list: bool):
        """构建单个微信活跃群的展示行组件"""
        chat_name = item.get("chat", "").strip()
        unread = item.get("unread", 0)
        msg_time = item.get("time", "")
        last_msg = item.get("last_message", "")
        sender = item.get("sender", "")

        row = ctk.CTkFrame(self.scroll_discovered_groups, fg_color=("gray95", "#1E222A"), corner_radius=6)
        row.pack(fill="x", padx=6, pady=3)

        info_frame = ctk.CTkFrame(row, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=8, pady=4)

        top_line = ctk.CTkFrame(info_frame, fg_color="transparent")
        top_line.pack(fill="x", anchor="w")

        ctk.CTkLabel(
            top_line, text=f"👥 {chat_name}", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=13, weight="bold")
        ).pack(side="left")

        if unread > 0:
            ctk.CTkLabel(
                top_line, text=f" {unread}条未读", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=11), text_color="#F59E0B"
            ).pack(side="left", padx=6)

        if msg_time or last_msg:
            sub = f"🕒 {msg_time}" + (f" · {sender}" if sender else "")
            if last_msg:
                clean = last_msg.replace("\n", " ").strip()
                sub += f": {clean[:35]}..." if len(clean) > 35 else f": {clean}"
            ctk.CTkLabel(
                info_frame, text=sub, font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=11),
                text_color="gray", anchor="w"
            ).pack(fill="x", anchor="w", pady=(1, 0))

        if in_list:
            ctk.CTkLabel(
                row, text="✅ 已在监控中", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12, weight="bold"),
                text_color=("green", "#4ADE80"), width=115
            ).pack(side="right", padx=10)
        else:
            ctk.CTkButton(
                row, text="➕ 选择加入监控", font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12, weight="bold"),
                width=115, height=28, fg_color="#2563EB", hover_color="#1D4ED8", cursor="hand2",
                command=lambda name=chat_name: self._add_group_direct(name, show_popup=False)
            ).pack(side="right", padx=8, pady=4)

    # =========================================================================
    # 视图 4：AI 模型配置
    # =========================================================================
    def _create_model_view(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(frame, corner_radius=10)
        card.pack(fill="x", padx=10, pady=10)

        title = ctk.CTkLabel(card, text="🤖 AI 大模型总结引擎配置", font=ctk.CTkFont(size=16, weight="bold"))
        title.pack(anchor="w", padx=20, pady=(15, 5))

        desc = ctk.CTkLabel(
            card,
            text="微信增量消息优先使用本地已安装的官方 agy CLI (Gemini Flash，免 Key 极速提炼)；\n"
                 "如果想切换为云端 DeepSeek、Gemini API 或本地 Ollama 模型，可在下方设置。",
            font=ctk.CTkFont(size=13),
            text_color="gray",
            justify="left"
        )
        desc.pack(anchor="w", padx=20, pady=(0, 15))

        # 预设快速选择
        preset_frame = ctk.CTkFrame(card, fg_color="transparent")
        preset_frame.pack(fill="x", padx=20, pady=(0, 15))

        ctk.CTkLabel(preset_frame, text="快速预设:", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(0, 10))

        presets = [
            ("🌟 Google Gemini", "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent", 125, None),
            ("🚀 DeepSeek", "https://api.deepseek.com/chat/completions", 110, None),
            ("🛠️ 本地 Ollama", "http://localhost:11434/v1/chat/completions", 110, ("gray70", "gray30")),
        ]
        for text, url, width, fg in presets:
            kw = {"fg_color": fg} if fg else {}
            ctk.CTkButton(
                preset_frame, text=text, width=width,
                command=lambda u=url: self._set_api_url(u), **kw
            ).pack(side="left", padx=(0, 8))

        btn_clear_key = ctk.CTkButton(preset_frame, text="❌ 清空密钥", width=90, fg_color=("gray70", "gray30"), command=self._clear_ai_key)
        btn_clear_key.pack(side="left")

        # 表单
        form = ctk.CTkFrame(frame, corner_radius=10)
        form.pack(fill="x", padx=10, pady=(0, 10))

        lbl_url = ctk.CTkLabel(form, text="API 接口完整地址 (URL):", font=ctk.CTkFont(size=13, weight="bold"))
        lbl_url.pack(anchor="w", padx=20, pady=(15, 5))

        self.ent_api_url = ctk.CTkEntry(form, placeholder_text="例如: https://api.deepseek.com/chat/completions", width=600)
        self.ent_api_url.pack(fill="x", padx=20, pady=(0, 10))

        lbl_key = ctk.CTkLabel(form, text="API Key (密钥):", font=ctk.CTkFont(size=13, weight="bold"))
        lbl_key.pack(anchor="w", padx=20, pady=(5, 5))

        key_row = ctk.CTkFrame(form, fg_color="transparent")
        key_row.pack(fill="x", padx=20, pady=(0, 15))

        self.ent_api_key = ctk.CTkEntry(key_row, placeholder_text="输入你的 API Key (如 sk-xxxx)", show="*", width=500)
        self.ent_api_key.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_toggle_key = ctk.CTkButton(key_row, text="👁️ 显示", width=70, fg_color=("gray70", "gray30"), command=self._toggle_key_visibility)
        self.btn_toggle_key.pack(side="left")

        btn_save_model = ctk.CTkButton(form, text="💾 保存并应用 AI 模型配置", height=38, font=ctk.CTkFont(size=14, weight="bold"), command=self._save_ai_config)
        btn_save_model.pack(anchor="w", padx=20, pady=(0, 20))

        def on_show():
            self._load_ai_config_display()
        frame.on_show = on_show
        return frame

    def _toggle_key_visibility(self):
        if self.ent_api_key.cget("show") == "*":
            self.ent_api_key.configure(show="")
            self.btn_toggle_key.configure(text="🔒 隐藏")
        else:
            self.ent_api_key.configure(show="*")
            self.btn_toggle_key.configure(text="👁️ 显示")

    def _set_api_url(self, url: str):
        self.ent_api_url.delete(0, "end")
        self.ent_api_url.insert(0, url)

    def _clear_ai_key(self):
        self.ent_api_key.delete(0, "end")
        self._save_ai_config()

    def _load_ai_config_display(self):
        _, url, key = load_user_config()
        self._set_api_url(url)
        self.ent_api_key.delete(0, "end")
        self.ent_api_key.insert(0, key)

    def _save_ai_config(self):
        update_full_config(
            ai_api_url=self.ent_api_url.get().strip(),
            ai_api_key=self.ent_api_key.get().strip()
        )
        self._refresh_summary_status()
        messagebox.showinfo("成功", "AI 模型配置已更新成功！")

    # =========================================================================
    # 视图 5：历史归档
    # =========================================================================
    def _create_history_view(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # 顶部操作栏
        bar = ctk.CTkFrame(frame, corner_radius=10)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 10))

        lbl = ctk.CTkLabel(bar, text="📄 今日与历史总结归档文件", font=ctk.CTkFont(size=15, weight="bold"))
        lbl.pack(side="left", padx=15, pady=10)

        btn_open_folder = ctk.CTkButton(bar, text="📂 打开 summaries 文件夹", fg_color=("gray70", "gray30"), command=self._open_summaries_folder)
        btn_open_folder.pack(side="right", padx=15, pady=10)

        btn_refresh_files = ctk.CTkButton(bar, text="🔄 刷新列表", width=90, command=self._refresh_history_files)
        btn_refresh_files.pack(side="right", padx=(0, 5), pady=10)

        # 主体左右分栏：左侧文件列表，右侧预览
        split_frame = ctk.CTkFrame(frame, fg_color="transparent")
        split_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        split_frame.grid_columnconfigure(1, weight=1)
        split_frame.grid_rowconfigure(0, weight=1)

        self.scroll_files = ctk.CTkScrollableFrame(split_frame, width=220, corner_radius=10)
        self.scroll_files.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.txt_history_preview = ctk.CTkTextbox(split_frame, corner_radius=10, font=ctk.CTkFont(family=FONT_FAMILY_MONO, size=13), wrap="word")
        self.txt_history_preview.grid(row=0, column=1, sticky="nsew")

        def on_show():
            self._refresh_history_files()
        frame.on_show = on_show
        return frame

    def _open_summaries_folder(self):
        os.makedirs(SUMMARIES_DIR, exist_ok=True)
        os.system(f'explorer "{SUMMARIES_DIR}"')

    def _refresh_history_files(self):
        for widget in self.scroll_files.winfo_children():
            widget.destroy()

        os.makedirs(SUMMARIES_DIR, exist_ok=True)
        files = [f for f in os.listdir(SUMMARIES_DIR) if f.endswith(".md")]
        files.sort(reverse=True)

        if not files:
            lbl = ctk.CTkLabel(self.scroll_files, text="暂无归档文档", font=ctk.CTkFont(size=12), text_color="gray")
            lbl.pack(pady=20)
            self.txt_history_preview.delete("1.0", "end")
            self.txt_history_preview.insert("1.0", "暂无历史文档。执行增量总结后将自动生成按天归档的 Markdown 文档。")
            return

        for f in files:
            btn = ctk.CTkButton(
                self.scroll_files,
                text=f"📑 {f}",
                anchor="w",
                height=32,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                command=lambda fname=f: self._preview_file(fname)
            )
            btn.pack(fill="x", pady=2)

        # 默认预览第一个
        self._preview_file(files[0])

    def _preview_file(self, fname: str):
        full_path = os.path.join(SUMMARIES_DIR, fname)
        self.txt_history_preview.delete("1.0", "end")
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    self.txt_history_preview.insert("1.0", f.read())
            except Exception as e:
                self.txt_history_preview.insert("1.0", f"读取失败: {str(e)}")

    # =========================================================================
    # 视图 6：隐私清理
    # =========================================================================
    def _create_cleanup_view(self):
        """构建隐私清理卡片视图"""
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        cards_data = [
            (
                "🟢 等级 1：轻度重置（仅清理临时缓存 + 重置同步时间戳）",
                "• 动作：删除 last_sync.json 及解密图片缓存\n• 保留：保留微信解密密钥、保留每日总结文档、保留虚拟环境\n• 适合：想重新完整抓取消息，但不想重新提取微信密钥",
                "执行等级 1 重置", ("#2FA572", "#248A5E"), self._do_cleanup_level_1
            ),
            (
                "🟡 等级 2：隐私脱敏（粉碎全部微信密钥 + 删除历史总结文档）",
                "• 动作：粉碎 ~/.wechat-cli (密钥全部抹除) + 彻底删除 summaries 文档\n• 保留：保留 Python 虚拟环境与工具本身\n• 适合：借电脑给他人，确保任何微信密钥与聊天内容彻底无残留",
                "执行等级 2 脱敏", ("#D97706", "#B45309"), self._do_cleanup_level_2
            ),
            (
                "🔴 等级 3：终极销毁（退电脑/归还电脑模式，粉碎一切）",
                "• 动作：粉碎全部密钥 + 彻底删除总结文档 + 注销定时任务\n• 适合：彻底不再使用这台电脑，电脑不留任何运行轨迹与聊天隐私",
                "执行等级 3 销毁", ("#DC2626", "#B91C1C"), self._do_cleanup_level_3
            )
        ]

        for title, desc, btn_txt, colors, cmd in cards_data:
            self._build_cleanup_card(frame, title, desc, btn_txt, colors, cmd)

        return frame

    def _build_cleanup_card(self, parent, title: str, desc: str, btn_text: str, colors: tuple, command):
        """构建隐私清理等级卡片组件"""
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.pack(fill="x", padx=10, pady=8)

        ctk.CTkLabel(
            card, text=title, font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=14, weight="bold")
        ).pack(anchor="w", padx=15, pady=(12, 4))

        ctk.CTkLabel(
            card, text=desc, font=ctk.CTkFont(family=FONT_FAMILY_MAIN, size=12),
            text_color="gray", justify="left"
        ).pack(anchor="w", padx=15, pady=(0, 8))

        ctk.CTkButton(
            card, text=btn_text, width=140, fg_color=colors[0], hover_color=colors[1],
            command=command
        ).pack(anchor="w", padx=15, pady=(0, 12))

    def _purge_privacy_data(self, include_config: bool = False):
        """统一执行隐私数据物理粉碎（密钥、时间戳、历史总结、Windows 定时任务）"""
        for p in [os.path.expanduser("~/.wechat-cli"), SUMMARIES_DIR]:
            shutil.rmtree(p, ignore_errors=True)
        if os.path.exists(LAST_SYNC_FILE):
            os.remove(LAST_SYNC_FILE)
        if include_config and os.path.exists(CONFIG_FILE):
            try:
                os.remove(CONFIG_FILE)
            except Exception:
                pass
        subprocess.run(["schtasks", "/delete", "/tn", "微信群定时增量总结", "/f"], capture_output=True, **get_subprocess_flags())
        self._refresh_summary_status()

    def _do_cleanup_level_1(self):
        if not messagebox.askyesno("确认", "确定执行【等级 1：轻度重置】吗？\n将删除时间戳并清空临时解密图片缓存。"):
            return
        if os.path.exists(LAST_SYNC_FILE):
            os.remove(LAST_SYNC_FILE)
        user_dir = os.path.expanduser("~/.wechat-cli")
        for sub in ["decrypted", "decoded_images", "cache"]:
            shutil.rmtree(os.path.join(user_dir, sub), ignore_errors=True)
        try:
            from wechat_cli.core.image_decoder import clear_image_cache
            clear_image_cache()
        except Exception:
            pass
        messagebox.showinfo("完成", "等级 1 清理完成！微信解密密钥仍保留，下次运行将重新抓取。")
        self._refresh_summary_status()

    def _do_cleanup_level_2(self):
        if messagebox.askyesno("警告", "确定执行【等级 2：隐私脱敏】吗？\n将粉碎 ~/.wechat-cli 微信密钥并删除所有总结文档！"):
            self._purge_privacy_data(include_config=False)
            messagebox.showinfo("完成", "等级 2 脱敏完成！微信密钥与历史聊天总结已彻底抹除。")

    def _do_cleanup_level_3(self):
        if messagebox.askyesno("危险确认", "确定执行【等级 3：终极销毁】吗？\n电脑上所有微信密钥、纪要文档及相关配置将被永久销毁！"):
            self._purge_privacy_data(include_config=True)
            messagebox.showinfo("完成", "等级 3 终极销毁完成！电脑未残留任何隐私痕迹。")


def main():
    app = WeChatAssistantApp()
    app.mainloop()


if __name__ == "__main__":
    main()
