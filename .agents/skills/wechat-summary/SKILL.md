---
name: wechat-summary
description: >-
  微信关键群聊（如北京移动群）增量消息提取与 AI 智能总结技能。
  自动比对增量时间戳，读取未读/新增群发言、链接与图片路径，生成高保真群聊总结与待办事项。
  当用户询问“微信群总结”、“看看群里说了什么”、“总结北京移动群”、“@wechat-summary”时使用。
---

# 微信群聊增量 AI 总结技能

本技能打通本地微信桌面端数据库，针对关键目标群（如包含“北京移动”的群聊）执行无侵入增量抓取，并结合大模型进行智能深度分析。

## 何时使用
- 用户要求：“总结一下北京移动群”、“微信群里有什么新消息”、“今天群里聊了什么”、“@wechat-summary”；
- 需要排查某个群内的重要业务通知、套餐变动、链接与图片分享时。

## 运行与数据源
1. **底层引擎路径**：`D:\code\st\wechat-cli-plus-main\auto_summary.py`
2. **虚拟环境解释器**：`D:\code\st\wechat-cli-plus-main\.venv\Scripts\python.exe`
3. **状态文件**：`D:\code\st\wechat-cli-plus-main\last_sync.json`
4. **归档文档**：`D:\code\st\wechat-cli-plus-main\summaries\YYYY-MM-DD.md` (按天独立成册)

## 执行流程与规范
1. **触发增量采集**：
   通过命令执行增量检测：
   ```powershell
   & "D:\code\st\wechat-cli-plus-main\.venv\Scripts\python.exe" D:\code\st\wechat-cli-plus-main\auto_summary.py
   ```
2. **分析输出结果**：
   - 若返回 `暂无新发言`，直接告知用户当前所有目标群均无新增内容；
   - 若有增量发言，结合提取到的发言人、文本、文件链接与图片，在对话框生成结构化深度报告。
