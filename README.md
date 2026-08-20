# Manage Claude Code

让 Codex 成为管理入口，让本机 Claude Code 成为编码执行者。

这个 Skill 面向希望通过自然语言管理工作的非技术管理者。用户可以在 Codex 中描述目标，由 Codex启动本机 Claude Code 后台任务、读取进度、续接工作、协助验收，并将结果转换成业务语言。用户也可以随时进入 Claude Code 会话亲自操作。

## 能力

- 从 Codex 启动本机 Claude Code 后台任务
- 为每个任务保存稳定的任务编号和项目目录
- 查询任务状态并读取最近日志
- 恢复已停止或已完成的会话
- 输出可供用户进入 Claude Code 的连接命令
- 要求 Codex在汇报完成前独立检查结果
- 支持无部署、测试环境和明确授权的正式环境三种范围

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

## 状态与安全

任务台账默认保存在 `~/.codex/manage-claude-code/tasks.json`，不会写入目标项目。Skill 不使用跳过全部权限的模式，也不将“启动任务”解释为部署、发布或其他外部操作授权。

Codex 只有在活跃运行或用户再次询问时才能重新查询 Claude Code 状态；这个 Skill 本身不是常驻通知服务。

## License

[MIT](LICENSE)
