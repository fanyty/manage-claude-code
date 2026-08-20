# Manage Claude Code

让 Codex 成为管理入口，让本机 Claude Code 成为编码执行者。

这个 Skill 面向希望通过自然语言管理工作的非技术管理者。用户可以在 Codex 中描述目标，由 Codex启动本机 Claude Code 后台任务、读取进度、续接工作、协助验收，并将结果转换成业务语言。用户也可以随时进入 Claude Code 会话亲自操作。

## 能力

- 从 Codex 启动本机 Claude Code 后台任务
- 为每个任务保存稳定的任务编号和项目目录
- 区分管理编号、Claude 后台编号和可恢复会话编号
- 查询任务状态并读取最近日志
- 恢复已停止或已完成的会话
- 输出可供用户进入 Claude Code 的连接命令
- 启动后明确提示 Claude Code 正在后台运行，并同时给出状态、日志和进入现场的命令
- 要求 Codex在汇报完成前独立检查结果
- 支持无部署、测试环境和明确授权的正式环境三种范围
- 检测可能覆盖 Claude 登录的环境凭据，并可执行最小连通性探测

## 工作方式

```text
老板向 Codex 描述目标
        ↓
Codex整理结果和验收条件
        ↓
管理脚本启动本机 Claude Code
        ↓
Claude Code 在后台开发和验证
        ↓
Codex读取状态、日志和代码结果
        ↓
老板可随时进入 Claude Code 操作
        ↓
Codex验收并用业务语言汇报
```

## 前置条件

- Codex 或 ChatGPT desktop app
- Python 3.10+
- 已安装并登录的 Claude Code

## 安装

在 Codex 中使用 Skill Installer：

```text
使用 $skill-installer 安装：
https://github.com/fanyty/manage-claude-code/tree/main/skills/manage-claude-code
```

或者将 `skills/manage-claude-code` 复制到个人 Skills 目录：

```text
$HOME/.agents/skills/manage-claude-code
```

## 使用

在包含目标代码仓库的 Codex 项目中说：

```text
使用 $manage-claude-code，把这个需求交给本机 Claude Code 完成并持续追踪：
为内部系统增加 CSV 导出。现有功能不能受到影响；相关测试必须通过；
先发布到测试环境，不要发布到正式环境。
```

查询或接管任务：

```text
使用 $manage-claude-code 汇报当前任务进度。
使用 $manage-claude-code 让我进入正在运行的 Claude Code 任务。
使用 $manage-claude-code 让 Claude Code 修复验收中发现的问题。
```

Claude Code 默认以后台任务运行，因此不会自动弹出一个新窗口。每次启动后，Skill 会返回管理任务编号、Claude 后台编号，以及三条可直接使用的命令：查看状态、查看日志、进入 Claude Code 现场。想亲自操作时，让 Codex 执行 `attach`，或在交互式终端运行它给出的连接命令。

首次启动任务前，Skill 会执行一次最小真实请求来验证后台凭证。原因是 `claude auth status` 可能显示已经登录，但后台进程仍可能无法刷新登录信息。验证失败时，Skill 会停止启动并提示用户处理登录，不会把一个无法工作的进程误报为正常开发中。

## 状态与安全

任务台账默认保存在 `~/.codex/manage-claude-code/tasks.json`，不会写入目标项目。Skill 不使用跳过全部权限的模式，也不将“启动任务”解释为部署、发布或其他外部操作授权。

Codex 只有在活跃运行或用户再次询问时才能重新查询 Claude Code 状态；这个 Skill 本身不是常驻通知服务。

## License

[MIT](LICENSE)
