# OpenClaw Memory Manager (OMM)

把 OpenClaw 的日常聊天，自动变成长期记忆。

每天自动：整理会话 → 提炼摘要 → 进化你的 `USER.md` / `MEMORY.md` / `AGENTS.md`。

---

## 🚀 一键安装

**1. 双击运行 `install.bat`**

自动完成：
- 安装到 `~/.openclaw/openclaw-memory-manager`
- 桌面创建「OMM控制台」快捷方式
- 自动安装控制台依赖（需要 Node.js）

**2. 打开桌面「OMM控制台」**

**3. 选择服务商 → 填 API Key → 点保存**

搞定 ✅ 之后每天 23:30 自动整理（安装时可选注册定时任务）。

---

## 前置要求

| 软件 | 用途 |
|------|------|
| OpenClaw | 主程序（必须有 `~/.openclaw` 目录） |
| Python 3.10+ | 记忆整理脚本 |
| Node.js 18+ | 控制台（仅手动配置时需要） |
| LLM API Key | 任意 OpenAI 兼容服务商 |

---

## 控制台能做什么

- **LLM 配置**：一键切换全局模型（摘要 + 进化全部生效）
- **手动执行**：每日 / 每周 / 每月任务按钮 + Dry Run 试运行
- **编辑三文档**：直接改 USER.md / MEMORY.md / AGENTS.md（自动备份）
- **查看日志**：任务执行记录一目了然

---

## 手动运行（可选）

```bat
python scripts\run_all.py --dry-run   :: 试运行
python scripts\daily\main.py           :: 只跑每日
```

环境变量说明见 [docs](scripts/common/utils.py)（全部有默认值，一般不用配）。

## License

MIT