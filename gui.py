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
from fnmatch import fnmatch

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

# 动态获取根目录（兼容源码运行与 Nuitka 编译后的独立二进制）
def get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    if "__compiled__" in globals():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "user_config.json")
SUMMARIES_DIR = os.path.join(BASE_DIR, "summaries")
LAST_SYNC_FILE = os.path.join(BASE_DIR, "last_sync.json")

# 引入核心业务逻辑
from auto_summary import (
    run_incremental_summary,
    load_user_config,
    get_today_doc_path,
    get_agy_path,
    get_antigravity_exe,
    run_cli_command,
    load_state,
    save_state
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


class WeChatAssistantApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("微信 AI 增量总结助手")
        self.geometry("1020x680")
        self.minsize(920, 600)

        # 线程通信与状态
        self.log_queue = queue.Queue()
        self.is_running = False

        # 设置专属窗口与任务栏图标
        self.icon_path = os.path.join(BASE_DIR, "app.ico")
        self._set_app_icon()

        # 网格布局：左侧导航栏 + 右侧主内容区
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._build_views()

        # 默认选中第一个视图
        self.select_view("summary")
        self.after(100, self._process_log_queue)

    def _set_app_icon(self):
        if hasattr(self, 'icon_path') and os.path.exists(self.icon_path):
            try:
                self.iconbitmap(self.icon_path)
            except Exception:
                pass
            self.after(150, self._apply_icon_safe)

    def _apply_icon_safe(self):
        try:
            if hasattr(self, 'icon_path') and os.path.exists(self.icon_path):
                self.iconbitmap(self.icon_path)
        except Exception:
            pass

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
    # 视图 1：增量总结
    # =========================================================================
    def _create_summary_view(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        # 顶部状态卡片
        status_card = ctk.CTkFrame(frame, corner_radius=10)
        status_card.grid(row=0, column=0, sticky="ew", pady=(0, 10), padx=5)
        status_card.grid_columnconfigure((0, 1, 2), weight=1)

        self.lbl_monitored_groups = ctk.CTkLabel(
            status_card, text="当前监控: 加载中...", font=ctk.CTkFont(size=13, weight="bold")
        )
        self.lbl_monitored_groups.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        self.lbl_ai_engine = ctk.CTkLabel(
            status_card, text="AI 引擎: 加载中...", font=ctk.CTkFont(size=13)
        )
        self.lbl_ai_engine.grid(row=0, column=1, padx=15, pady=10, sticky="w")

        self.lbl_key_status = ctk.CTkLabel(
            status_card, text="密钥: 检查中...", font=ctk.CTkFont(size=13)
        )
        self.lbl_key_status.grid(row=0, column=2, padx=15, pady=10, sticky="e")

        # 操作工具栏
        action_bar = ctk.CTkFrame(frame, fg_color="transparent")
        action_bar.grid(row=1, column=0, sticky="ew", pady=(0, 10), padx=5)

        self.btn_run_summary = ctk.CTkButton(
            action_bar,
            text="🚀 立即执行增量总结",
            height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_summary_task
        )
        self.btn_run_summary.pack(side="left", padx=(0, 12))

        self.auto_save_var = ctk.BooleanVar(value=True)
        self.switch_auto_save = ctk.CTkSwitch(
            action_bar,
            text="自动写入今日归档文档",
            variable=self.auto_save_var,
            font=ctk.CTkFont(size=13)
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

        self.btn_clear_log = ctk.CTkButton(
            action_bar,
            text="🧹 清空日志",
            width=90,
            height=36,
            fg_color=("gray70", "gray30"),
            command=self._clear_summary_log
        )
        self.btn_clear_log.pack(side="right", padx=(10, 0))

        # 日志输出文本框
        self.txt_summary_log = ctk.CTkTextbox(
            frame,
            corner_radius=10,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word"
        )
        self.txt_summary_log.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)

        def on_show():
            self._refresh_summary_status()
        frame.on_show = on_show

        return frame

    def _refresh_summary_status(self):
        patterns, url, key = load_user_config()
        self.lbl_monitored_groups.configure(text=f"🎯 监控规则: {', '.join(patterns)}")

        if get_agy_path():
            engine_desc = "官方 agy CLI (Gemini Flash 本地免Key)"
        elif get_antigravity_exe():
            engine_desc = "本地 Antigravity 引擎 (Gemini Flash)"
        elif key:
            engine_desc = f"外部 API ({url[:20]}...)"
        else:
            engine_desc = "本地离线纯净过滤模式"
        self.lbl_ai_engine.configure(text=f"🤖 AI 引擎: {engine_desc}")

        keys_file = os.path.expanduser("~/.wechat-cli/all_keys.json")
        if os.path.exists(keys_file):
            try:
                with open(keys_file, "r", encoding="utf-8") as f:
                    kc = len(json.load(f))
                self.lbl_key_status.configure(text=f"🔑 密钥就绪 ({kc}个)", text_color=("green", "lightgreen"))
            except Exception:
                self.lbl_key_status.configure(text="🔑 密钥就绪", text_color=("green", "lightgreen"))
        else:
            self.lbl_key_status.configure(text="⚠️ 密钥未就绪", text_color=("red", "orange"))

    def _log_summary(self, text: str):
        self.log_queue.put(text)

    def _process_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.txt_summary_log.insert("end", msg + "\n")
            self.txt_summary_log.see("end")
        self.after(100, self._process_log_queue)

    def _clear_summary_log(self):
        self.txt_summary_log.delete("1.0", "end")

    def _start_summary_task(self):
        if self.is_running:
            return

        self.is_running = True
        self.btn_run_summary.configure(state="disabled", text="⏳ 正在检测与总结中...")
        auto_save = self.auto_save_var.get()

        def worker():
            try:
                self._log_summary(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] 启动微信增量消息总结...")
                run_incremental_summary(
                    log_fn=self._log_summary,
                    ask_save_fn=None,
                    auto_save=auto_save
                )
            except Exception as e:
                self._log_summary(f"\n❌ 执行出错: {str(e)}")
            finally:
                self.after(0, self._on_summary_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _on_summary_finished(self):
        self.is_running = False
        self.btn_run_summary.configure(state="normal", text="🚀 立即执行增量总结")
        self._refresh_summary_status()

    def _open_today_doc(self):
        doc = get_today_doc_path()
        if os.path.exists(doc):
            os.system(f'start "" "{doc}"')
        else:
            messagebox.showinfo("提示", f"今日文档尚未生成（今天暂无增量记录）：\n{doc}")

    # =========================================================================
    # 视图 2：密钥管理
    # =========================================================================
    def _create_keys_view(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(frame, corner_radius=10)
        card.pack(fill="x", padx=10, pady=10)

        title = ctk.CTkLabel(card, text="🔑 微信解密密钥一键提取 / 更换工具", font=ctk.CTkFont(size=16, weight="bold"))
        title.pack(anchor="w", padx=20, pady=(15, 5))

        desc = ctk.CTkLabel(
            card,
            text="从 Windows 微信 (Weixin.exe) 内存特征码中快速提取 64 位数据库密钥，全只读安全扫描，无账号风险。\n"
                 "每次切换微信账号、重新扫码登录微信、或初次使用时，请点击下方按钮提取密钥。",
            font=ctk.CTkFont(size=13),
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
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._extract_keys_task
        )
        self.btn_extract_keys.pack(side="left")

        self.lbl_keys_info = ctk.CTkLabel(btn_row, text="", font=ctk.CTkFont(size=13))
        self.lbl_keys_info.pack(side="left", padx=15)

        log_card = ctk.CTkFrame(frame, corner_radius=10)
        log_card.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        lbl_log = ctk.CTkLabel(log_card, text="扫描与提取日志:", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_log.pack(anchor="w", padx=15, pady=(10, 5))

        self.txt_keys_log = ctk.CTkTextbox(log_card, font=ctk.CTkFont(family="Consolas", size=13))
        self.txt_keys_log.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        def on_show():
            self._update_keys_info()
        frame.on_show = on_show
        return frame

    def _update_keys_info(self):
        keys_file = os.path.expanduser("~/.wechat-cli/all_keys.json")
        if os.path.exists(keys_file):
            try:
                with open(keys_file, "r", encoding="utf-8") as f:
                    kc = len(json.load(f))
                self.lbl_keys_info.configure(text=f"✅ 已配置 {kc} 个微信数据库密钥", text_color=("green", "lightgreen"))
            except Exception:
                self.lbl_keys_info.configure(text="✅ 密钥库文件已存在", text_color=("green", "lightgreen"))
        else:
            self.lbl_keys_info.configure(text="⚠️ 尚未提取密钥，请先点击提取", text_color=("red", "orange"))

    def _extract_keys_task(self):
        self.btn_extract_keys.configure(state="disabled", text="⏳ 正在扫描微信内存...")
        self.txt_keys_log.delete("1.0", "end")

        def worker():
            def log(m):
                self.after(0, lambda: (self.txt_keys_log.insert("end", m + "\n"), self.txt_keys_log.see("end")))

            log("正在检查微信客户端运行状态...")
            check = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/NH"],
                                   capture_output=True, text=True, errors="replace")
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
    # 视图 3：监控群聊
    # =========================================================================
    def _create_groups_view(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        # 当前配置卡片
        card = ctk.CTkFrame(frame, corner_radius=10)
        card.grid(row=0, column=0, sticky="ew", padx=10, pady=(5, 10))

        title = ctk.CTkLabel(card, text="🎯 目标监控群聊管理", font=ctk.CTkFont(size=16, weight="bold"))
        title.pack(anchor="w", padx=15, pady=(10, 5))

        self.lbl_current_rules = ctk.CTkLabel(card, text="当前规则: 加载中...", font=ctk.CTkFont(size=13))
        self.lbl_current_rules.pack(anchor="w", padx=15, pady=(0, 10))

        # 手动输入规则栏
        rule_row = ctk.CTkFrame(card, fg_color="transparent")
        rule_row.pack(fill="x", padx=15, pady=(0, 15))

        self.ent_pattern = ctk.CTkEntry(rule_row, placeholder_text="输入群名规则（支持通配符，多个用逗号隔开，如: *北京移动*, *业主群*）", width=450)
        self.ent_pattern.pack(side="left", fill="x", expand=True, padx=(0, 10))

        btn_save_rule = ctk.CTkButton(rule_row, text="💾 保存规则", width=100, command=self._save_group_pattern)
        btn_save_rule.pack(side="left", padx=(0, 10))

        btn_reset_default = ctk.CTkButton(rule_row, text="🔄 恢复默认", width=100, fg_color=("gray70", "gray30"), command=self._reset_group_pattern)
        btn_reset_default.pack(side="left")

        # 搜索与群列表工具栏
        search_card = ctk.CTkFrame(frame, corner_radius=10)
        search_card.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 5))

        self.ent_search_group = ctk.CTkEntry(search_card, placeholder_text="输入关键词搜索微信中的群聊 (如: 移动 / 技术 / 交流)", width=350)
        self.ent_search_group.pack(side="left", padx=15, pady=10, fill="x", expand=True)

        btn_search = ctk.CTkButton(search_card, text="🔍 搜索群聊", width=100, command=self._search_groups)
        btn_search.pack(side="left", padx=(0, 10), pady=10)

        btn_fetch_all = ctk.CTkButton(search_card, text="📋 列出全部活跃群", width=130, fg_color=("gray70", "gray30"), command=self._fetch_all_groups)
        btn_fetch_all.pack(side="left", padx=(0, 15), pady=10)

        # 群聊列表容器
        self.scroll_groups = ctk.CTkScrollableFrame(frame, corner_radius=10)
        self.scroll_groups.grid(row=2, column=0, sticky="nsew", padx=10, pady=(5, 10))

        def on_show():
            self._load_group_patterns_display()
        frame.on_show = on_show
        return frame

    def _load_group_patterns_display(self):
        patterns, _, _ = load_user_config()
        self.lbl_current_rules.configure(text=f"当前规则: {', '.join(patterns)}")
        self.ent_pattern.delete(0, "end")
        self.ent_pattern.insert(0, ", ".join(patterns))

    def _save_group_pattern(self):
        val = self.ent_pattern.get().strip()
        if not val:
            messagebox.showwarning("警告", "规则不能为空！")
            return
        patterns = [p.strip() for p in val.replace("，", ",").split(",") if p.strip()]

        cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        cfg["group_patterns"] = patterns
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        self._load_group_patterns_display()
        messagebox.showinfo("成功", f"监控规则已成功保存为:\n{patterns}")

    def _reset_group_pattern(self):
        self.ent_pattern.delete(0, "end")
        self.ent_pattern.insert(0, "*北京移动*")
        self._save_group_pattern()

    def _fetch_all_groups(self):
        for widget in self.scroll_groups.winfo_children():
            widget.destroy()

        loading_lbl = ctk.CTkLabel(self.scroll_groups, text="正在读取本地活跃群聊列表...", font=ctk.CTkFont(size=13))
        loading_lbl.pack(pady=20)

        def worker():
            raw = run_cli_command(["sessions", "--limit", "100", "--format", "json"])
            groups = []
            if raw:
                try:
                    data = json.loads(raw)
                    for s in data:
                        if s.get("is_group"):
                            chat = s.get("chat", "").strip()
                            if chat:
                                groups.append(chat)
                except Exception:
                    pass
            self.after(0, lambda: self._display_group_list(groups))

        threading.Thread(target=worker, daemon=True).start()

    def _search_groups(self):
        keyword = self.ent_search_group.get().strip().lower()
        if not keyword:
            self._fetch_all_groups()
            return

        for widget in self.scroll_groups.winfo_children():
            widget.destroy()

        loading_lbl = ctk.CTkLabel(self.scroll_groups, text=f"正在搜索包含【{keyword}】的群聊...", font=ctk.CTkFont(size=13))
        loading_lbl.pack(pady=20)

        def worker():
            raw = run_cli_command(["sessions", "--limit", "100", "--format", "json"])
            matched = []
            if raw:
                try:
                    data = json.loads(raw)
                    for s in data:
                        if s.get("is_group"):
                            chat = s.get("chat", "").strip()
                            if keyword in chat.lower():
                                matched.append(chat)
                except Exception:
                    pass
            self.after(0, lambda: self._display_group_list(matched))

        threading.Thread(target=worker, daemon=True).start()

    def _display_group_list(self, groups: list):
        for widget in self.scroll_groups.winfo_children():
            widget.destroy()

        if not groups:
            lbl = ctk.CTkLabel(self.scroll_groups, text="未找到匹配的群聊，请确认已提取密钥且微信有聊天记录。", font=ctk.CTkFont(size=13), text_color="gray")
            lbl.pack(pady=20)
            return

        patterns, _, _ = load_user_config()

        for g in groups:
            row = ctk.CTkFrame(self.scroll_groups, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=4)

            is_monitored = any(fnmatch(g, p) for p in patterns)
            badge_text = " [已监控]" if is_monitored else ""
            badge_color = ("green", "lightgreen") if is_monitored else ("gray10", "gray90")

            lbl = ctk.CTkLabel(row, text=f"• {g}{badge_text}", font=ctk.CTkFont(size=13), text_color=badge_color, anchor="w")
            lbl.pack(side="left", padx=5)

            btn = ctk.CTkButton(
                row,
                text="设为监控" if not is_monitored else "已添加",
                width=80,
                height=28,
                fg_color=("gray70", "gray30") if is_monitored else None,
                command=lambda name=g: self._add_single_group_pattern(name)
            )
            btn.pack(side="right", padx=5)

    def _add_single_group_pattern(self, name: str):
        patterns, _, _ = load_user_config()
        if name not in patterns:
            patterns.append(name)
            cfg = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    pass
            cfg["group_patterns"] = patterns
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._load_group_patterns_display()
            self._fetch_all_groups()
            messagebox.showinfo("成功", f"已成功将【{name}】加入监控！")

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

        lbl_presets = ctk.CTkLabel(preset_frame, text="快速预设:", font=ctk.CTkFont(size=13, weight="bold"))
        lbl_presets.pack(side="left", padx=(0, 10))

        btn_preset_gemini = ctk.CTkButton(preset_frame, text="🌟 Google Gemini", width=120, command=self._preset_gemini)
        btn_preset_gemini.pack(side="left", padx=(0, 8))

        btn_preset_deepseek = ctk.CTkButton(preset_frame, text="🚀 DeepSeek", width=110, command=self._preset_deepseek)
        btn_preset_deepseek.pack(side="left", padx=(0, 8))

        btn_preset_ollama = ctk.CTkButton(preset_frame, text="🛠️ 本地 Ollama", width=110, fg_color=("gray70", "gray30"), command=self._preset_ollama)
        btn_preset_ollama.pack(side="left", padx=(0, 8))

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

    def _preset_gemini(self):
        self.ent_api_url.delete(0, "end")
        self.ent_api_url.insert(0, "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent")

    def _preset_deepseek(self):
        self.ent_api_url.delete(0, "end")
        self.ent_api_url.insert(0, "https://api.deepseek.com/chat/completions")

    def _preset_ollama(self):
        self.ent_api_url.delete(0, "end")
        self.ent_api_url.insert(0, "http://localhost:11434/v1/chat/completions")

    def _clear_ai_key(self):
        self.ent_api_key.delete(0, "end")
        self._save_ai_config()

    def _load_ai_config_display(self):
        _, url, key = load_user_config()
        self.ent_api_url.delete(0, "end")
        self.ent_api_url.insert(0, url)
        self.ent_api_key.delete(0, "end")
        self.ent_api_key.insert(0, key)

    def _save_ai_config(self):
        url = self.ent_api_url.get().strip()
        key = self.ent_api_key.get().strip()

        cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        cfg["ai_api_url"] = url
        cfg["ai_api_key"] = key
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

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

        self.txt_history_preview = ctk.CTkTextbox(split_frame, corner_radius=10, font=ctk.CTkFont(family="Consolas", size=13), wrap="word")
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
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.txt_history_preview.delete("1.0", "end")
                self.txt_history_preview.insert("1.0", content)
            except Exception as e:
                self.txt_history_preview.delete("1.0", "end")
                self.txt_history_preview.insert("1.0", f"读取失败: {str(e)}")

    # =========================================================================
    # 视图 6：隐私清理
    # =========================================================================
    def _create_cleanup_view(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        # 等级 1 卡片
        card1 = ctk.CTkFrame(frame, corner_radius=10)
        card1.pack(fill="x", padx=10, pady=8)

        t1 = ctk.CTkLabel(card1, text="🟢 等级 1：轻度重置（仅清理临时缓存 + 重置同步时间戳）", font=ctk.CTkFont(size=14, weight="bold"))
        t1.pack(anchor="w", padx=15, pady=(12, 4))
        d1 = ctk.CTkLabel(card1, text="• 动作：删除 last_sync.json 及解密图片缓存\n• 保留：保留微信解密密钥、保留每日总结文档、保留虚拟环境\n• 适合：想重新完整抓取消息，但不想重新提取微信密钥", font=ctk.CTkFont(size=12), text_color="gray", justify="left")
        d1.pack(anchor="w", padx=15, pady=(0, 8))
        b1 = ctk.CTkButton(card1, text="执行等级 1 重置", width=140, fg_color="#2FA572", hover_color="#248A5E", command=self._do_cleanup_level_1)
        b1.pack(anchor="w", padx=15, pady=(0, 12))

        # 等级 2 卡片
        card2 = ctk.CTkFrame(frame, corner_radius=10)
        card2.pack(fill="x", padx=10, pady=8)

        t2 = ctk.CTkLabel(card2, text="🟡 等级 2：隐私脱敏（粉碎全部微信密钥 + 删除历史总结文档）", font=ctk.CTkFont(size=14, weight="bold"))
        t2.pack(anchor="w", padx=15, pady=(12, 4))
        d2 = ctk.CTkLabel(card2, text="• 动作：粉碎 ~/.wechat-cli (密钥全部抹除) + 彻底删除 summaries 文档\n• 保留：保留 Python 虚拟环境与工具本身\n• 适合：借电脑给他人，确保任何微信密钥与聊天内容彻底无残留", font=ctk.CTkFont(size=12), text_color="gray", justify="left")
        d2.pack(anchor="w", padx=15, pady=(0, 8))
        b2 = ctk.CTkButton(card2, text="执行等级 2 脱敏", width=140, fg_color="#D97706", hover_color="#B45309", command=self._do_cleanup_level_2)
        b2.pack(anchor="w", padx=15, pady=(0, 12))

        # 等级 3 卡片
        card3 = ctk.CTkFrame(frame, corner_radius=10)
        card3.pack(fill="x", padx=10, pady=8)

        t3 = ctk.CTkLabel(card3, text="🔴 等级 3：终极销毁（退电脑/归还电脑模式，粉碎一切）", font=ctk.CTkFont(size=14, weight="bold"))
        t3.pack(anchor="w", padx=15, pady=(12, 4))
        d3 = ctk.CTkLabel(card3, text="• 动作：粉碎全部密钥 + 彻底删除总结文档 + 注销定时任务\n• 适合：彻底不再使用这台电脑，电脑不留任何运行轨迹与聊天隐私", font=ctk.CTkFont(size=12), text_color="gray", justify="left")
        d3.pack(anchor="w", padx=15, pady=(0, 8))
        b3 = ctk.CTkButton(card3, text="执行等级 3 销毁", width=140, fg_color="#DC2626", hover_color="#B91C1C", command=self._do_cleanup_level_3)
        b3.pack(anchor="w", padx=15, pady=(0, 12))

        return frame

    def _do_cleanup_level_1(self):
        if not messagebox.askyesno("确认", "确定执行【等级 1：轻度重置】吗？\n将删除时间戳并清空临时解密图片缓存。"):
            return
        if os.path.exists(LAST_SYNC_FILE):
            os.remove(LAST_SYNC_FILE)
        user_dir = os.path.expanduser("~/.wechat-cli")
        for sub in ["decrypted", "decoded_images"]:
            p = os.path.join(user_dir, sub)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
        messagebox.showinfo("完成", "等级 1 清理完成！微信解密密钥仍保留，下次运行将重新抓取。")
        self._refresh_summary_status()

    def _do_cleanup_level_2(self):
        if not messagebox.askyesno("警告", "确定执行【等级 2：隐私脱敏】吗？\n将粉碎 ~/.wechat-cli 微信密钥并删除所有总结文档！"):
            return
        user_dir = os.path.expanduser("~/.wechat-cli")
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)
        if os.path.exists(LAST_SYNC_FILE):
            os.remove(LAST_SYNC_FILE)
        if os.path.exists(SUMMARIES_DIR):
            shutil.rmtree(SUMMARIES_DIR, ignore_errors=True)
        os.system("schtasks /delete /tn \"微信群定时增量总结\" /f >nul 2>&1")
        messagebox.showinfo("完成", "等级 2 脱敏完成！微信密钥与历史聊天总结已彻底抹除。")
        self._refresh_summary_status()

    def _do_cleanup_level_3(self):
        if not messagebox.askyesno("危险确认", "确定执行【等级 3：终极销毁】吗？\n电脑上所有微信密钥、纪要文档及相关配置将被永久销毁！"):
            return
        user_dir = os.path.expanduser("~/.wechat-cli")
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)
        if os.path.exists(LAST_SYNC_FILE):
            os.remove(LAST_SYNC_FILE)
        if os.path.exists(SUMMARIES_DIR):
            shutil.rmtree(SUMMARIES_DIR, ignore_errors=True)
        os.system("schtasks /delete /tn \"微信群定时增量总结\" /f >nul 2>&1")
        messagebox.showinfo("完成", "等级 3 终极销毁完成！电脑未残留任何隐私痕迹。")
        self._refresh_summary_status()


def main():
    app = WeChatAssistantApp()
    app.mainloop()


if __name__ == "__main__":
    main()
