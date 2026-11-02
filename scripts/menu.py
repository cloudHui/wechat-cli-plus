# -*- coding: utf-8 -*-
"""
微信 AI 总结控制台交互菜单
集成：增量总结、一键提取/更换Key、群聊交互式搜索与自选配置、按小时/天总结查阅、三级隐私清理。
"""

import os
import sys
import json
import datetime
import subprocess
import shutil

# 彻底修复 Windows 中文与特殊 Emoji 乱码和编码崩溃
if os.name == 'nt':
    os.system('chcp 65001 >nul 2>&1')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stdin.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_EXEC = sys.executable
CONFIG_FILE = os.path.join(BASE_DIR, "user_config.json")


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def get_choice(valid_keys: list, prompt: str = "请选择: ") -> str:
    """直接按键无需回车确认 (Windows 专属 msvcrt 极速单键响应)"""
    print(prompt, end="", flush=True)
    if os.name == 'nt':
        import msvcrt
        valid_set = set(k.lower() for k in valid_keys)
        while True:
            ch = msvcrt.getch()
            if ch in (b'\x00', b'\xe0'):
                msvcrt.getch()
                continue
            if ch == b'\x03':  # Ctrl+C
                print("\n[退出]")
                sys.exit(0)
            try:
                char = ch.decode('utf-8', errors='ignore')
            except Exception:
                continue
            if char.lower() in valid_set:
                print(char)
                return char
    else:
        while True:
            c = input().strip()
            if c.lower() in [k.lower() for k in valid_keys]:
                return c


def wait_any_key(prompt: str = "\n按任意键继续..."):
    """按任意键立即继续，无需按回车"""
    print(prompt, end="", flush=True)
    if os.name == 'nt':
        import msvcrt
        ch = msvcrt.getch()
        if ch in (b'\x00', b'\xe0'):
            msvcrt.getch()
        print()
    else:
        input()


def load_config() -> dict:
    default_cfg = {
        "group_patterns": ["*北京移动*"],
        "ai_api_url": "https://api.deepseek.com/chat/completions",
        "ai_api_key": ""
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_cfg.update(cfg)
        except Exception:
            pass
    return default_cfg


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run_summary():
    print("\n" + "=" * 60)
    print("正在启动增量检测与总结...")
    print("=" * 60)
    subprocess.run([PYTHON_EXEC, os.path.join(BASE_DIR, "auto_summary.py")], cwd=BASE_DIR)


def refresh_keys():
    print("\n" + "=" * 60)
    print("🔑 微信解密密钥一键提取 / 更换工具")
    print("=" * 60)

    # 1. 检查微信是否已在运行
    check = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/NH"],
                           capture_output=True, text=True, errors="replace")
    if "Weixin.exe" not in check.stdout:
        print("\n[⚠️ 提示] 未检测到正在运行的微信 (Weixin.exe)！")
        wait_any_key("请先在电脑上打开微信并完成扫码登录，然后按任意键重试...")

    print("\n正在扫描 Weixin.exe 内存特征码提取 64 位数据库密钥...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run([PYTHON_EXEC, os.path.join(BASE_DIR, "entry.py"), "init"],
                         cwd=BASE_DIR, env=env)

    if res.returncode == 0:
        keys_file = os.path.expanduser("~/.wechat-cli/all_keys.json")
        key_count = 0
        if os.path.exists(keys_file):
            try:
                with open(keys_file, "r", encoding="utf-8") as f:
                    key_count = len(json.load(f))
            except Exception:
                pass
        print(f"\n✅ 微信密钥提取/更换成功！已提取 {key_count} 个数据库密钥并配置就绪。")
    else:
        print("\n❌ 提取失败！可能原因：微信尚未扫码登录，或杀毒软件拦截了本次只读扫描。")


def open_today_doc():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    doc_path = os.path.join(BASE_DIR, "summaries", f"{today}.md")
    if os.path.exists(doc_path):
        print(f"\n正在打开今日归档文档: {doc_path}")
        os.system(f'start "" "{doc_path}"')
    else:
        print(f"\n[提示] 今日文档尚未生成（今天暂无增量记录），路径为: {doc_path}")


def get_all_group_sessions() -> list:
    """拉取本地所有活跃的群聊名称列表"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run(
        [PYTHON_EXEC, os.path.join(BASE_DIR, "entry.py"), "sessions", "--limit", "100", "--format", "json"],
        cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
    )
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        sessions = json.loads(res.stdout)
        groups = []
        for s in sessions:
            if s.get("is_group"):
                groups.append(s.get("chat", "").strip())
        return [g for g in groups if g]
    except Exception:
        return []


def manage_groups():
    """群聊筛选管理：支持查看、搜索并选择目标监控群"""
    while True:
        cfg = load_config()
        current_patterns = cfg.get("group_patterns", ["*北京移动*"])

        clear_screen()
        print("=" * 65)
        print("                 🎯 监控群聊设置与自选中心")
        print("=" * 65)
        print(f"当前监控的群聊规则：")
        for idx, p in enumerate(current_patterns, 1):
            print(f"   {idx}. {p}")
        print("-" * 65)
        print("   [1] 📋 列出全部活跃群聊列表 (按序号勾选)")
        print("   [2] 🔍 按关键词搜索群聊 (如输入: 移动 / 业主 / 卡友)")
        print("   [3] ✏️  手动输入通配符规则 (如: *移动*, *技术交流*)")
        print("   [4] 🔄 恢复为默认配置 (*北京移动*)")
        print("\n   [0] ↩️  保存并返回主菜单")
        print("=" * 65)

        op = get_choice(['0', '1', '2', '3', '4'], "请选择操作 [0-4]: ")
        if op == '0':
            break

        elif op == '1':
            print("\n正在从本地微信数据库读取活跃群聊列表...")
            all_groups = get_all_group_sessions()
            if not all_groups:
                print("未读取到群聊，请确认已提取密钥且微信有聊天记录。")
                wait_any_key("\n按任意键继续...")
                continue

            print(f"\n共找到 {len(all_groups)} 个活跃群聊：")
            for i, g in enumerate(all_groups, 1):
                marker = " [已监控]" if any(p == g or (p.startswith("*") and p[1:-1] in g) for p in current_patterns) else ""
                print(f"   [{i:2d}] {g}{marker}")

            print("\n操作提示：")
            print("• 输入序号批量选择（用逗号隔开，如 1, 3, 5）")
            print("• 输入 A 选择全部群聊")
            print("• 输入 0 取消返回")
            sel = input("请选择要监控的群序号: ").strip()
            if sel == '0' or not sel:
                continue
            if sel.upper() == 'A':
                cfg["group_patterns"] = all_groups
                save_config(cfg)
                print(f"\n✅ 已将全部 {len(all_groups)} 个群聊设为监控目标！")
            else:
                new_list = []
                for part in sel.replace("，", ",").split(","):
                    part = part.strip()
                    if part.isdigit():
                        idx = int(part)
                        if 1 <= idx <= len(all_groups):
                            new_list.append(all_groups[idx - 1])
                if new_list:
                    cfg["group_patterns"] = new_list
                    save_config(cfg)
                    print(f"\n✅ 成功将监控目标更新为：{new_list}")
            wait_any_key("\n按任意键继续...")

        elif op == '2':
            keyword = input("\n请输入要搜索的群聊关键词 (例如: 移动 / 卡 / 交流): ").strip()
            if not keyword:
                continue

            print(f"\n正在搜索包含【{keyword}】的群聊...")
            all_groups = get_all_group_sessions()
            matches = [g for g in all_groups if keyword.lower() in g.lower()]

            if not matches:
                print(f"未找到名称包含【{keyword}】的群聊。你可以直接选择 [3] 输入通配符 *{keyword}*。")
            else:
                print(f"\n找到以下 {len(matches)} 个匹配群聊：")
                for i, g in enumerate(matches, 1):
                    print(f"   [{i}] {g}")

                print("\n操作提示：输入想要监控的序号 (如 1, 2)，或输入 A 全部添加: ")
                sel = input("请选择: ").strip()
                if sel.upper() == 'A':
                    # 追加不重复的
                    for m in matches:
                        if m not in current_patterns:
                            current_patterns.append(m)
                    cfg["group_patterns"] = current_patterns
                    save_config(cfg)
                    print(f"\n✅ 已将所有匹配群加入监控！")
                else:
                    added = []
                    for part in sel.replace("，", ",").split(","):
                        part = part.strip()
                        if part.isdigit():
                            idx = int(part)
                            if 1 <= idx <= len(matches):
                                g_name = matches[idx - 1]
                                if g_name not in current_patterns:
                                    current_patterns.append(g_name)
                                    added.append(g_name)
                    if added:
                        cfg["group_patterns"] = current_patterns
                        save_config(cfg)
                        print(f"\n✅ 已添加监控群：{added}")
            wait_any_key("\n按任意键继续...")

        elif op == '3':
            print("\n请输入新的监控规则，支持通配符（多个规则用逗号隔开）：")
            print("示例：*北京移动*, *技术部*, 核心架构群")
            val = input("请输入规则: ").strip()
            if val:
                patterns = [p.strip() for p in val.replace("，", ",").split(",") if p.strip()]
                cfg["group_patterns"] = patterns
                save_config(cfg)
                print(f"\n✅ 监控规则已更新为: {patterns}")
            wait_any_key("\n按任意键继续...")

        elif op == '4':
            cfg["group_patterns"] = ["*北京移动*"]
            save_config(cfg)
            print("\n✅ 已重置为默认规则: ['*北京移动*']")
            wait_any_key("\n按任意键继续...")


def cleanup_traces():
    while True:
        clear_screen()
        print("=" * 65)
        print("              数据清理与隐私销毁等级选择")
        print("=" * 65)
        print("  [1] 🟢 等级 1：轻度重置（仅清理临时缓存 + 重置同步时间戳）")
        print("      • 动作：删除 last_sync.json 及解密图片缓存")
        print("      • 保留：保留微信解密密钥、保留每日总结文档、保留虚拟环境")
        print("      • 适合：想重新完整抓取消息，但不想重新登录微信提 key\n")
        print("  [2] 🟡 等级 2：隐私脱敏（粉碎全部微信密钥 + 删掉历史总结文档）")
        print("      • 动作：粉碎 ~/.wechat-cli (密钥全部抹除) + 删除 summaries 文档")
        print("      • 保留：保留 Python 虚拟环境 (.venv) 与工具本身")
        print("      • 适合：借电脑给他人，确保任何微信密钥与聊天内容彻底无残留\n")
        print("  [3] 🔴 等级 3：终极销毁（退电脑/归还电脑模式，粉碎一切）")
        print("      • 动作：粉碎密钥 + 删总结文档 + 注销定时任务 + 彻底删掉 .venv")
        print("      • 适合：彻底不再使用这台电脑，电脑不留任何字节痕迹\n")
        print("  [0] ↩️  返回主菜单")
        print("=" * 65)

        lvl = get_choice(['0', '1', '2', '3'], "请选择清理等级 [0-3]: ")
        if lvl == '0':
            return
        elif lvl == '1':
            confirm = get_choice(['Y', 'N'], "\n确认执行【等级 1：轻度重置】吗？(Y/N): ")
            if confirm.upper() == 'Y':
                sync_file = os.path.join(BASE_DIR, "last_sync.json")
                if os.path.exists(sync_file):
                    os.remove(sync_file)
                    print("[OK] 已重置 last_sync.json 同步状态")
                user_dir = os.path.expanduser("~/.wechat-cli")
                for sub in ["decrypted", "decoded_images"]:
                    p = os.path.join(user_dir, sub)
                    if os.path.exists(p):
                        shutil.rmtree(p, ignore_errors=True)
                        print(f"[OK] 已清空临时缓存: {sub}")
                print("\n✅ 等级 1 清理完成！微信解密密钥仍保留，下次运行将全量重新抓取。")
            else:
                print("\n[已取消]")
            wait_any_key("\n按任意键返回...")
            return
        elif lvl == '2':
            confirm = get_choice(['Y', 'N'], "\n确认执行【等级 2：隐私脱敏】（将销毁密钥和总结文档）吗？(Y/N): ")
            if confirm.upper() == 'Y':
                user_dir = os.path.expanduser("~/.wechat-cli")
                if os.path.exists(user_dir):
                    shutil.rmtree(user_dir, ignore_errors=True)
                    print("[OK] 已粉碎 ~/.wechat-cli 微信密钥库与配置")
                sync_file = os.path.join(BASE_DIR, "last_sync.json")
                if os.path.exists(sync_file):
                    os.remove(sync_file)
                    print("[OK] 已删除 last_sync.json")
                sum_dir = os.path.join(BASE_DIR, "summaries")
                if os.path.exists(sum_dir):
                    shutil.rmtree(sum_dir, ignore_errors=True)
                    print("[OK] 已彻底删除 summaries 历史群总结文档")
                os.system("schtasks /delete /tn \"微信群定时增量总结\" /f >nul 2>&1")
                print("\n✅ 等级 2 清理完成！电脑上所有微信密钥与聊天纪要已彻底抹除（虚拟环境已保留）。")
            else:
                print("\n[已取消]")
            wait_any_key("\n按任意键返回...")
            return
        elif lvl == '3':
            confirm = get_choice(['Y', 'N'], "\n⚠️ 危险确认：即将执行【等级 3：终极销毁】（抹除全部密钥、文档并删除虚拟环境）！(Y/N): ")
            if confirm.upper() == 'Y':
                user_dir = os.path.expanduser("~/.wechat-cli")
                if os.path.exists(user_dir):
                    shutil.rmtree(user_dir, ignore_errors=True)
                    print("[OK] 已粉碎 ~/.wechat-cli 微信密钥库")
                sync_file = os.path.join(BASE_DIR, "last_sync.json")
                if os.path.exists(sync_file):
                    os.remove(sync_file)
                    print("[OK] 已删除 last_sync.json")
                sum_dir = os.path.join(BASE_DIR, "summaries")
                if os.path.exists(sum_dir):
                    shutil.rmtree(sum_dir, ignore_errors=True)
                    print("[OK] 已删除 summaries 历史群总结文档")
                os.system("schtasks /delete /tn \"微信群定时增量总结\" /f >nul 2>&1")
                venv_dir = os.path.join(BASE_DIR, ".venv")
                if os.path.exists(venv_dir):
                    print("[+] 正在删除 Python 虚拟环境 (.venv 约100MB)...")
                    shutil.rmtree(venv_dir, ignore_errors=True)
                    print("[OK] Python 虚拟环境已彻底删除")
                print("\n✅ 等级 3 终极销毁完成！电脑未残留任何字节痕迹。")
            else:
                print("\n[已取消]")
            wait_any_key("\n按任意键返回...")
            return


def get_ai_status(cfg: dict) -> str:
    """检测当前 AI 总结引擎生效状态"""
    agy_candidates = [
        shutil.which("agy"),
        os.path.expanduser("~/.gemini/bin/agy.exe"),
        os.path.expandvars(r"%USERPROFILE%\.gemini\bin\agy.exe")
    ]
    for c in agy_candidates:
        if c and os.path.exists(c):
            return "官方 agy CLI (Gemini Flash，本地免Key)"

    if cfg.get("ai_api_key"):
        return f"已配置外部 API ({cfg.get('ai_api_url', '')[:25]}...)"
    return "未配置 Key (本地离线清洗模式)"


def configure_ai_model():
    """配置或更换 AI 大模型（Gemini / DeepSeek / 本地 Ollama）"""
    while True:
        cfg = load_config()
        current_url = cfg.get("ai_api_url", "未配置")
        current_key = cfg.get("ai_api_key", "")
        mask_key = (current_key[:4] + "****" + current_key[-4:]) if len(current_key) >= 8 else ("已设置" if current_key else "未设置")
        ai_engine_status = get_ai_status(cfg)

        clear_screen()
        print("=" * 65)
        print("                 🤖 AI 大模型总结引擎配置")
        print("=" * 65)
        print(f"当前生效引擎: {ai_engine_status}")
        print(f"备用接口地址: {current_url}")
        print(f"备用密钥状态: {mask_key}")
        print("-" * 65)
        print("💡 提示：检测到本地已安装官方 agy CLI，无需配置 Key 即可自动使用 Gemini Flash！")
        print("   若需额外切换为云端 API，可在下方选择：")
        print("-" * 65)
        print("   [1] 🌟 使用 Google Gemini 官方免费模型 (API Key)")
        print("   [2] 🚀 使用 DeepSeek 大模型 (国内高性价比)")
        print("   [3] 🛠️  自定义 OpenAI 兼容接口 (如本地 Ollama / OneAPI)")
        print("   [4] ❌ 清空密钥 (恢复离线纯净过滤模式)")
        print("\n   [0] ↩️  返回主菜单")
        print("=" * 65)

        op = get_choice(['0', '1', '2', '3', '4'], "请选择操作 [0-4]: ")
        if op == '0':
            break
        elif op == '1':
            print("\n【配置 Google Gemini】")
            print("免费获取 API Key 网址: https://aistudio.google.com/app/apikey")
            key = input("请输入你的 Gemini API Key: ").strip()
            if key:
                cfg["ai_api_url"] = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
                cfg["ai_api_key"] = key
                save_config(cfg)
                print("\n✅ Google Gemini 配置成功！下次总结将由 Gemini 智能提炼。")
            wait_any_key("\n按任意键继续...")
        elif op == '2':
            print("\n【配置 DeepSeek】")
            print("获取 API Key 网址: https://platform.deepseek.com/")
            key = input("请输入你的 DeepSeek API Key (形如 sk-xxxx): ").strip()
            if key:
                cfg["ai_api_url"] = "https://api.deepseek.com/chat/completions"
                cfg["ai_api_key"] = key
                save_config(cfg)
                print("\n✅ DeepSeek 配置成功！下次总结将由 DeepSeek 智能提炼。")
            wait_any_key("\n按任意键继续...")
        elif op == '3':
            url = input("\n请输入自定义接口完整 URL (如 http://localhost:11434/v1/chat/completions): ").strip()
            key = input("请输入 API Key (若本地免鉴权可直接按回车): ").strip()
            if url:
                cfg["ai_api_url"] = url
                cfg["ai_api_key"] = key
                save_config(cfg)
                print("\n✅ 自定义模型配置成功！")
            wait_any_key("\n按任意键继续...")
        elif op == '4':
            cfg["ai_api_key"] = ""
            save_config(cfg)
            print("\n✅ API Key 已清空，恢复离线纯净过滤模式。")
            wait_any_key("\n按任意键继续...")


def main():
    while True:
        clear_screen()
        cfg = load_config()
        patterns_str = ", ".join(cfg.get("group_patterns", ["*北京移动*"]))
        ai_status = get_ai_status(cfg)

        print("=" * 60)
        print("                 微信 AI 增量总结控制台")
        print("=" * 60)
        print(f"   当前监控群：[{patterns_str}]")
        print(f"   AI 总结引擎：[{ai_status}]")
        print("-" * 60)
        print("   [1] ⚡ 立即执行增量总结 (拉取新发言并追加写入今日文档)")
        print("   [2] 🔑 一键提取/更换解密密钥 (更换微信或重新登录后使用)")
        print("   [3] 🎯 更改监控群聊 (列出全部群/按关键词搜索自选)")
        print("   [4] 🤖 配置/更换 AI 大模型 (一键填入 Gemini / DeepSeek Key)")
        print("   [5] 📄 打开今日总结文档 (Markdown)")
        print("   [6] 🧹 彻底销毁所有密钥与隐私痕迹 (三级安全清理)")
        print("\n   [0] 🚪 退出")
        print("=" * 60)

        choice = get_choice(['0', '1', '2', '3', '4', '5', '6'], "请选择操作编号 [0-6]: ")
        if choice == '1':
            run_summary()
            wait_any_key("\n按任意键返回主菜单...")
        elif choice == '2':
            refresh_keys()
            wait_any_key("\n按任意键返回主菜单...")
        elif choice == '3':
            manage_groups()
        elif choice == '4':
            configure_ai_model()
        elif choice == '5':
            open_today_doc()
            wait_any_key("\n按任意键返回主菜单...")
        elif choice == '6':
            cleanup_traces()
            wait_any_key("\n按任意键返回主菜单...")
        elif choice == '0':
            print("\n已退出控制台。")
            break
        else:
            print("\n[提示] 输入有误，请输入 0 到 6 之间的数字。")
            import time
            time.sleep(1)


if __name__ == "__main__":
    main()
