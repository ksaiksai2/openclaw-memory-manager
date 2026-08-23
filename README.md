# OpenClaw Memory Manager

> 让 OpenClaw 越来越懂你——主动沉淀记忆，而不是等你提醒它"记住XXX"。

每天自动读取 TencentDB Agent Memory 的会话记录，用 LLM 提炼关键信息，主动写入 `USER.md` / `MEMORY.md` / `AGENTS.md`。agent 日常提及旧项目时直接读几行就够，不需要费时费 token 去检索。

支持多 Agent 独立记忆分区（如 main、echo 各自维护独立记忆），支持 DeepSeek、OpenAI、Moonshot、通义千问、智谱、SiliconFlow 等主流 Provider。

详细说明见 [介绍.md](介绍.md)。

## 安装

### 前置条件

- Windows 10/11
- Python 3.10+（需在 PATH 中）
- OpenClaw 已安装，TencentDB Agent Memory 插件已启用
- LLM API Key（支持 [DeepSeek](https://platform.deepseek.com/) / [OpenAI](https://platform.openai.com/) / [Moonshot](https://platform.moonshot.cn/) / [通义千问](https://dashscope.console.aliyun.com/) / [智谱](https://open.bigmodel.cn/) / [SiliconFlow](https://cloud.siliconflow.cn/) 等）

### 方式一：安装器（推荐）

```bash
cd %USERPROFILE%\.openclaw
git clone https://github.com/你的用户名/openclaw-memory-manager.git
cd openclaw-memory-manager
python setup.py
```

浏览器自动打开，选择 Provider、填写 API Key、确认路径，一键完成安装。

### 方式二：手动安装

**1. 克隆项目**

```bash
cd %USERPROFILE%\.openclaw
git clone https://github.com/你的用户名/openclaw-memory-manager.git
```

**2. 设置 API Key**

```powershell
# 以 DeepSeek 为例，其他 Provider 换对应的 key
[System.Environment]::SetEnvironmentVariable("MM_LLM_API_KEY", "sk-你的key", "User")
[System.Environment]::SetEnvironmentVariable("MM_LLM_PROVIDER", "deepseek", "User")

# 计划任务可能读不到环境变量，同时写注册表
Set-ItemProperty -Path "HKCU:\Environment" -Name "MM_LLM_API_KEY" -Value "sk-你的key"
Set-ItemProperty -Path "HKCU:\Environment" -Name "MM_LLM_PROVIDER" -Value "deepseek"
```

注销重新登录使环境变量生效。

**3. 注册计划任务**

```bash
cd openclaw-memory-manager\scripts
install_tasks.bat
```

**4. 配置通知（可选）**

```bash
pip install winrt-windows.ui.notifications winrt-windows.data.xml.dom
```

创建快捷方式到 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`，目标为 `pythonw.exe scripts\notify_agent.py`。

**5. 验证**

```bash
cd scripts
python run_all.py --dry-run    # 不调用模型，测试流程
```

## 其他 Provider

在环境变量或安装器中切换：

| Provider | `MM_LLM_PROVIDER` | 默认模型 |
|----------|-------------------|---------|
| DeepSeek | `deepseek` | deepseek-v4-flash |
| OpenAI | `openai` | gpt-4o-mini |
| Moonshot | `moonshot` | moonshot-v1-8k |
| 通义千问 | `qwen` | qwen-plus |
| 智谱 GLM | `zhipu` | glm-4-flash |
| SiliconFlow | `siliconflow` | deepseek-ai/DeepSeek-V3 |
| 自定义 | `custom` | 需设置 `MM_LLM_API_URL` 和 `MM_LLM_MODEL` |

## 许可证

[MIT](LICENSE)
