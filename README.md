# WorkPilot

终端里的 AI 编码助手：对话式地读写代码、执行命令。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 配置模型

默认使用 Anthropic：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

也可接任何 OpenAI 兼容接口（Moonshot / DeepSeek / Qwen / Ollama）：

```bash
export WORKPILOT_PROVIDER=openai
export WORKPILOT_BASE_URL=https://api.kimi.com/coding/v1
export WORKPILOT_MODEL=k3
export WORKPILOT_API_KEY=<你的 key>
```

API key 只从环境变量读取，不接受命令行参数 —— 避免留在 shell history 里。

## 用法

```bash
workpilot                  # 新会话
workpilot --continue       # 恢复当前目录最近一次会话
workpilot --resume <id>    # 恢复指定会话
workpilot --sessions       # 列出当前目录的历史会话
workpilot --yolo           # 跳过所有确认（危险）
```

会话中输入 `/exit` 或 `/quit` 退出。

## 工具与权限

| 工具 | 是否需要确认 |
|---|---|
| `read_file` / `list_files` / `grep` | 否 |
| `write_file` / `edit_file` | 是，可选「始终允许」 |
| `bash` | 每次都要确认，不支持「始终允许」 |

确认时会展示改动的 diff、或命令原文。对 `rm -rf`、`sudo`、`curl \| sh`
等模式会额外标红提示 —— 这是**提示，不是防护**。

文件工具受工作目录约束：路径解析后若落在工作目录之外会被拒绝
（`../`、绝对路径、符号链接均已覆盖）。

但 `bash` **不是沙箱**。它只能约束命令的工作目录，命令里写绝对路径
照样能读写工作区之外的任何位置。这正是它每次都要确认、且不支持
「始终允许」的原因。

## 会话数据与隐私

会话保存在 `~/.workpilot/sessions/<id>.jsonl`，**明文存储**，其中包含：

- 你输入的全部内容
- 模型读取过的文件内容
- `bash` 命令及其完整输出

因此**可能包含密钥、令牌或生产数据**。文件权限为 `0600`、目录为 `0700`
（仅属主可读写），但**未加密**。不需要时可直接删除：

```bash
rm -rf ~/.workpilot/sessions
```

会话按工作目录隔离：`--continue` 只恢复当前目录的会话，不会把 A 项目的
上下文带到 B 项目。未发言就退出的会话不会产生任何文件。

## 开发

```bash
.venv/bin/python -m pytest
```

设计文档在 `docs/superpowers/specs/`，实现计划在 `docs/superpowers/plans/`。
