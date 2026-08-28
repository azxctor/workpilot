# WorkPilot —— 终端 AI 编码助手实现方案（简易版）

> 一个类似 Claude Code 的 CLI 工具：在终端里对话，AI 通过工具调用读写你的文件、执行命令，完成编码任务。
>
> 技术栈：Python 3.10+ · 架构：事件驱动内核 · 模型接入：Provider 抽象层（默认 Anthropic）

---

## 1. 目标与范围

### 做什么

| 能力 | 说明 |
|---|---|
| Agent Loop | 模型自主决定调用工具 → 拿到结果 → 继续推理，直到任务完成 |
| 工具调用 | 读文件 / 列目录 / 搜索 / 写文件 / 执行 shell |
| 流式输出 | 逐 token 渲染，含 thinking 摘要 |
| 权限确认 | 危险操作执行前人工确认 |
| 会话持久化 | `--continue` / `--resume <id>` 断点续聊 |
| 上下文管理 | 逼近上下文上限时自动压缩历史 |
| 项目记忆 | 自动加载 `WORKPILOT.md` 进系统提示 |
| 自定义命令 | `.workpilot/commands/*.md` 定义 `/slash` 命令 |

### 明确不做（YAGNI）

- 多 Agent / 子 Agent 编排
- MCP 协议支持
- 插件热加载机制
- 图形化 diff 编辑器（写文件只做「展示 diff + 确认」）

---

## 2. 架构总览

### 2.1 核心思想：内核只产事件

Agent Loop 是一个**生成器**，它不打印、不落盘、不弹确认框——它只 `yield` 事件。
终端渲染、权限确认、会话落盘、日志，全是这条事件流的**消费者**。

这样做的收益：

- **可测试**：喂一个假 Provider，断言事件序列即可，零 mock 终端
- **可替换 UI**：换 Web/HTTP 前端不改内核一行
- **权限确认变成纯控制流**：`yield` 出请求，消费者 `send()` 回决定，而不是往内核注入回调

### 2.2 目录结构

```
workpilot/
├── cli.py                       # 入口：参数解析、REPL、Ctrl-C 处理
├── config.py                    # 配置加载 + WORKPILOT.md 项目记忆
│
├── agent/
│   ├── events.py                # ★ 事件协议（全系统的中枢契约）
│   ├── loop.py                  # ★ Agent Loop 内核（生成器，无 I/O）
│   └── context.py               # token 计数 + 自动压缩
│
├── providers/
│   ├── base.py                  # LLMClient 协议 + 归一化数据结构
│   ├── anthropic_provider.py    # 默认实现
│   └── openai_provider.py       # OpenAI 兼容实现（DeepSeek/Qwen/Ollama）
│
├── tools/
│   ├── base.py                  # Tool 协议 + 注册表 + 危险等级
│   ├── fs.py                    # read_file / list_files / write_file
│   ├── search.py                # grep
│   └── shell.py                 # bash
│
├── ui/
│   ├── renderer.py              # 唯一 print 的地方：消费事件流
│   └── confirm.py               # 权限确认交互
│
├── session/
│   └── store.py                 # JSONL 会话落盘 / continue / resume
│
└── commands/
    └── loader.py                # /slash 自定义命令
```

### 2.3 三条铁律

1. **`agent/` 不 import `ui/`**，内核里不出现任何 `print`。
2. **`agent/loop.py` 看不到 `anthropic` 这个包**——各家 API 差异由 `providers/` 磨平。
3. **工具自己声明 schema 和危险等级**，Loop 不认识具体工具，只认识注册表。

### 2.4 数据流

```
用户输入
   │
   ▼
ContextManager ──(超限则压缩)──► Provider.stream() ──► 归一化 Chunk 流
                                                            │
                                                            ▼
                                                     Agent Loop 事件流
                                                            │
                       ┌────────────────┬───────────────────┤
                       ▼                ▼                   ▼
                   Renderer        SessionStore       ToolCallRequest
                  (终端渲染)        (JSONL 落盘)             │
                                                     (危险操作) ▼
                                                        Confirm 交互
                                                            │
                                                    send() 回灌决定
                                                            │
                                                            └──► 继续执行
```

---

## 3. 核心契约：事件协议

```python
# agent/events.py
from dataclasses import dataclass, field
from enum import Enum


class Danger(Enum):
    SAFE = 0          # 只读，直接放行
    CONFIRM = 1       # 有副作用，问一次
    DESTRUCTIVE = 2   # 不可逆，每次都问


@dataclass
class TextDelta:
    """模型正文的流式增量。"""
    text: str


@dataclass
class ThinkingDelta:
    """思考摘要的流式增量（可选渲染为灰色）。"""
    text: str


@dataclass
class ToolCallRequest:
    """★ 唯一需要消费者回灌决定的事件。send('allow'|'deny'|'always')"""
    id: str
    name: str
    args: dict
    danger: Danger


@dataclass
class ToolResult:
    id: str
    name: str
    output: str
    is_error: bool = False


@dataclass
class ContextCompacted:
    freed_tokens: int
    kept_turns: int


@dataclass
class TurnEnd:
    stop_reason: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0


Event = (TextDelta | ThinkingDelta | ToolCallRequest
         | ToolResult | ContextCompacted | TurnEnd)
```

**为什么事件要放在最前面定义**：它是内核与所有消费者之间的唯一接口。
一旦事件类型钉死，`loop.py`、`renderer.py`、`store.py` 就可以并行开发、独立测试。

---

## 4. Agent Loop 内核

```python
# agent/loop.py
from typing import Iterator
from .events import *


class AgentLoop:
    def __init__(self, provider, registry, ctx_manager, system_prompt, workspace):
        self.provider = provider
        self.registry = registry
        self.ctx = ctx_manager
        self.system = system_prompt
        self.workspace = workspace          # 供 CLI 展开 /slash 命令时使用
        self.history: list[dict] = []

    def run_turn(self, user_input: str) -> Iterator[Event]:
        """跑完一整轮对话（可能包含多次工具调用）。

        这是一个双向生成器：遇到 ToolCallRequest 时会暂停，
        等消费者用 gen.send("allow"/"deny"/"always") 回灌决定后继续。
        """
        self.history.append({"role": "user", "content": user_input})

        while True:
            # ---- 1. 上下文压缩检查 ----
            if self.ctx.should_compact(self.history):
                self.history, freed, kept = self.ctx.compact(self.history)
                yield ContextCompacted(freed_tokens=freed, kept_turns=kept)

            # ---- 2. 流式请求模型 ----
            blocks: list[dict] = []
            tool_calls: list[ToolCall] = []
            usage, stop_reason = None, ""

            for chunk in self.provider.stream(
                system=self.system,
                messages=self.history,
                tools=self.registry.schemas(),
            ):
                if chunk.kind == "text":
                    yield TextDelta(chunk.text)
                elif chunk.kind == "thinking":
                    yield ThinkingDelta(chunk.text)
                elif chunk.kind == "tool_call":
                    tool_calls.append(chunk.tool_call)
                elif chunk.kind == "done":
                    blocks = chunk.blocks       # 原样保留，用于回填 history
                    usage, stop_reason = chunk.usage, chunk.stop_reason

            self.history.append({"role": "assistant", "content": blocks})

            # ---- 3. 没有工具调用 → 本轮结束 ----
            if not tool_calls:
                yield TurnEnd(
                    stop_reason=stop_reason,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_input_tokens,
                )
                return

            # ---- 4. 执行工具（危险操作先请示） ----
            results = []
            for call in tool_calls:
                tool = self.registry.get(call.name)

                decision = yield ToolCallRequest(
                    id=call.id, name=call.name,
                    args=call.args, danger=tool.danger,
                )

                if decision == "deny":
                    output, is_error = "User declined this operation.", True
                else:
                    try:
                        output, is_error = tool.run(**call.args), False
                    except Exception as e:
                        output, is_error = f"{type(e).__name__}: {e}", True

                yield ToolResult(call.id, call.name, output, is_error)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": output,
                    "is_error": is_error,
                })

            # ★ 所有 tool_result 必须打包进【同一条】 user 消息
            self.history.append({"role": "user", "content": results})
```

### 关键设计说明

**为什么用 `yield` 回灌而不是回调函数**
回调需要把 UI 的引用注入内核，直接破坏铁律 1。`send()` 让「暂停等人回答」变成纯粹的控制流，内核依然对终端一无所知。

**为什么 `tool_result` 必须打包进同一条 user 消息**
API 要求并行的多个 `tool_use` 对应的结果放在单条 user 消息里。拆成多条会静默地让模型以后不再发起并行调用。

**失败的工具也要返回结果**
异常必须转成 `is_error: true` 的 `tool_result` 返回，**不能丢弃**——丢掉会导致 `tool_use` / `tool_result` 配对断裂，API 直接 400。

---

## 5. Provider 抽象层

### 5.1 统一接口

```python
# providers/base.py
from typing import Protocol, Iterator
from dataclasses import dataclass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Chunk:
    """归一化的流式片段。kind: text | thinking | tool_call | done"""
    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    blocks: list | None = None      # kind=done 时携带完整 content blocks
    usage: object | None = None
    stop_reason: str = ""


class LLMClient(Protocol):
    def stream(self, system, messages, tools) -> Iterator[Chunk]: ...
    def count_tokens(self, system, messages, tools) -> int: ...
    @property
    def context_limit(self) -> int: ...
```

### 5.2 Anthropic 实现（默认）

```python
# providers/anthropic_provider.py
import anthropic
from .base import Chunk, ToolCall

MODEL = "claude-opus-5"          # 1M 上下文


class AnthropicProvider:
    context_limit = 1_000_000

    def __init__(self, api_key: str | None = None):
        # 不传 api_key 时 SDK 自动解析 ANTHROPIC_API_KEY / ant auth login 的 profile
        self.client = anthropic.Anthropic(api_key=api_key) if api_key \
                      else anthropic.Anthropic()

    def stream(self, system, messages, tools):
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=64000,
            # 自适应思考：模型自行决定思考深度。display 必须显式开启，
            # 否则默认 "omitted"，思考块文本为空，UI 上表现为长时间无输出。
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "xhigh"},   # 编码/Agent 场景的推荐档位
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},   # 缓存系统提示
            }],
            tools=tools,
            messages=messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield Chunk("text", text=event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        yield Chunk("thinking", text=event.delta.thinking)

            final = stream.get_final_message()
            for block in final.content:
                if block.type == "tool_use":
                    yield Chunk("tool_call", tool_call=ToolCall(
                        id=block.id, name=block.name, args=block.input,
                    ))
            yield Chunk(
                "done",
                blocks=final.content,
                usage=final.usage,
                stop_reason=final.stop_reason,
            )

    def count_tokens(self, system, messages, tools) -> int:
        return self.client.messages.count_tokens(
            model=MODEL, system=system, tools=tools, messages=messages,
        ).input_tokens
```

> **API 要点（易踩坑）**
> - 模型 ID 直接写 `claude-opus-5`，**不要加日期后缀**。
> - **不要用 `budget_tokens`**——在 Opus 5 上会返回 400，已被 `thinking: {"type": "adaptive"}` + `output_config.effort` 取代。
> - **不要用 assistant prefill**——当前模型family 一律 400。
> - 大 `max_tokens` 必须走 `.stream()`，否则容易 HTTP 超时。
> - `block.input` 用 SDK 解析好的对象，**不要对序列化后的 JSON 做字符串匹配**。

### 5.3 OpenAI 兼容实现

内部消息格式**统一采用 Anthropic 的 content-block 结构**，OpenAI 侧做双向翻译。
理由：block 结构信息量更大（能承载 thinking 块），反向选型会丢信息。

翻译要点：

| 环节 | 转换 |
|---|---|
| 工具 schema | 外面包一层 `{"type": "function", "function": {...}}` |
| 工具调用 | `tool_calls[].function.arguments` 是**分片的字符串**，需拼接后 `json.loads` |
| 工具结果 | Anthropic 的单条 user 多 block → OpenAI 的多条 `role: "tool"` 消息 |
| token 计数 | 无 count_tokens 接口，退化为 `len(text) / 3.5` 估算 |

---

## 6. 工具层与权限

### 6.1 工具协议

```python
# tools/base.py
from typing import Protocol
from agent.events import Danger

_REGISTRY: dict[str, "Tool"] = {}


def register(cls):
    _REGISTRY[cls.name] = cls()
    return cls


class Tool(Protocol):
    name: str
    danger: Danger
    schema: dict
    def run(self, **kwargs) -> str: ...


class Registry:
    def get(self, name): return _REGISTRY[name]
    def schemas(self):
        # ★ 必须排序：工具顺序变动会让 prompt cache 全部失效
        return [t.schema for _, t in sorted(_REGISTRY.items())]
```

### 6.2 MVP 五件套

| 工具 | 危险等级 | 说明 |
|---|---|---|
| `read_file` | SAFE | 读文件，带行号，默认上限 2000 行 |
| `list_files` | SAFE | 列目录，自动跳过 `.git` / `node_modules` |
| `grep` | SAFE | 正则搜索，复用 `ripgrep`，无则退回 Python 实现 |
| `write_file` | CONFIRM | 写文件，确认时展示 diff |
| `bash` | DESTRUCTIVE | 执行命令，强制超时 |

### 6.3 安全边界

```python
# tools/fs.py
from pathlib import Path

def safe_path(workspace: Path, raw: str) -> Path:
    p = (workspace / raw).resolve()
    if not p.is_relative_to(workspace.resolve()):
        raise ValueError(f"Path escapes workspace: {raw}")
    return p
```

- **所有路径 `resolve()` 后校验必须落在 workspace 根内**，挡住 `../` 逃逸
- `bash` 强制 `timeout=120s`，输出截断到 30KB（超长输出会瞬间吃掉上下文）
- 读文件超过上限时截断并明确告知模型「已截断，共 N 行」

### 6.4 权限三态

```python
# ui/confirm.py
class Approver:
    def __init__(self, yolo: bool = False):
        self.yolo = yolo
        self.always: set[str] = set()      # 本次会话内始终允许的工具名

    def ask(self, req: ToolCallRequest) -> str:
        if self.yolo or req.danger is Danger.SAFE:
            return "allow"
        if req.danger is Danger.CONFIRM and req.name in self.always:
            return "allow"

        render_preview(req)      # write_file 显示 diff，bash 显示完整命令
        choice = input("允许? [y]es / [n]o / [a]lways: ").strip().lower()
        if choice == "a" and req.danger is Danger.CONFIRM:
            self.always.add(req.name)       # DESTRUCTIVE 不允许 always
            return "allow"
        return "allow" if choice == "y" else "deny"
```

决策缓存放在 UI 层，**不进内核**——内核不该记住「用户之前说过 yes」这种交互状态。

---

## 7. 上下文管理（自动压缩）

```python
# agent/context.py
COMPACT_RATIO = 0.75      # 达到上下文上限的 75% 时触发
KEEP_RECENT_TURNS = 6     # 保留最近 6 个完整轮次的原文


class ContextManager:
    def __init__(self, provider, system, tools):
        self.provider, self.system, self.tools = provider, system, tools

    def should_compact(self, history) -> bool:
        used = self.provider.count_tokens(self.system, history, self.tools)
        return used > self.provider.context_limit * COMPACT_RATIO

    def compact(self, history) -> tuple[list, int, int]:
        split = self._safe_split_point(history)
        old, recent = history[:split], history[split:]

        summary = self._summarize(old)      # 一次独立的、无工具的 LLM 调用
        new_history = [
            {"role": "user",
             "content": f"[早期对话摘要]\n{summary}"},
            *recent,
        ]
        freed = self.provider.count_tokens(self.system, old, self.tools)
        return new_history, freed, len(recent)

    def _safe_split_point(self, history) -> int:
        """★ 压缩边界必须落在完整轮次上。

        绝不能把 tool_use / tool_result 配对切断——
        history 里若出现没有对应 tool_result 的 tool_use，API 直接 400。
        """
        target = len(history) - KEEP_RECENT_TURNS * 2
        for i in range(max(target, 0), len(history)):
            msg = history[i]
            if msg["role"] == "user" and not self._has_tool_result(msg):
                return i        # 找到一个「纯用户输入」的干净切点
        return 0                # 找不到安全切点就不压缩
```

**最容易出错的地方就是切点**。历史里 `tool_use` 和 `tool_result` 必须成对存在，
所以切点只能落在「不含 tool_result 的 user 消息」处——那才是一个真正的轮次起点。

---

## 8. 会话持久化

存储：`~/.workpilot/sessions/<session_id>.jsonl`，一行一个事件。

```python
# session/store.py
import json, time
from pathlib import Path


class SessionStore:
    def __init__(self, session_id: str):
        self.path = Path.home() / ".workpilot/sessions" / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, payload: dict):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"t": time.time(), "kind": kind, **payload},
                ensure_ascii=False,
            ) + "\n")

    def load_history(self) -> list[dict]:
        """只回放 history 相关的记录，重建 messages 数组。"""
        ...

    @staticmethod
    def latest() -> str | None:
        d = Path.home() / ".workpilot/sessions"
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        return files[-1].stem if files else None
```

**为什么选 JSONL 而不是整体 JSON**：

- 崩溃/断电不丢已有历史（追加写，无需重写整个文件）
- `--continue` 只需按 mtime 取最后一个文件
- 天然就是一份可读的审计日志

---

## 9. 配置与项目记忆

```python
# config.py
from pathlib import Path

def load_project_memory(start: Path) -> str:
    """向上查找 WORKPILOT.md，逐层拼接（越靠近项目根优先级越低）。"""
    parts = []
    for d in [start, *start.parents]:
        f = d / "WORKPILOT.md"
        if f.exists():
            parts.append(f.read_text(encoding="utf-8"))
        if (d / ".git").exists():
            break
    return "\n\n".join(reversed(parts))


def build_system_prompt(workspace: Path) -> str:
    memory = load_project_memory(workspace)
    return f"""你是 WorkPilot，一个运行在终端里的编码助手。
工作目录：{workspace}

工作方式：
- 修改代码前先读相关文件，不要凭猜测编辑
- 一次只做一件事，做完再进行下一步
- 执行命令前简述意图

{f"## 项目约定{chr(10)}{memory}" if memory else ""}"""
```

### Prompt Caching 的硬性要求

system 段打了 `cache_control` 就必须保证它**逐字节稳定**：

- ❌ 不要往 system 里塞时间戳、随机 ID、当前时间
- ❌ 不要让工具列表顺序随字典迭代变化（所以 `Registry.schemas()` 要排序）
- ✅ 用 `usage.cache_read_input_tokens` 验证——若长期为 0，说明有东西在悄悄破坏缓存

---

## 10. 自定义命令

`.workpilot/commands/` 下每个 `.md` 文件即一条命令，文件名就是命令名。

```markdown
<!-- .workpilot/commands/review.md -->
请审查以下改动，重点关注错误处理和边界条件：

$ARGUMENTS
```

```python
# commands/loader.py
def expand(raw: str, workspace: Path) -> str:
    """把 '/review src/main.py' 展开为模板内容。非命令则原样返回。"""
    if not raw.startswith("/"):
        return raw
    name, _, args = raw[1:].partition(" ")
    f = workspace / ".workpilot/commands" / f"{name}.md"
    if not f.exists():
        return raw
    return f.read_text(encoding="utf-8").replace("$ARGUMENTS", args.strip())
```

零解析逻辑——展开后就是一条普通的用户输入，直接送进 loop。

---

## 11. CLI 入口：把所有部件接起来

```python
# cli.py
def repl(loop, renderer, approver, store):
    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if raw in ("/exit", "/quit"):
            break

        user_input = expand(raw, loop.workspace)
        gen = loop.run_turn(user_input)
        decision = None      # 首次 send 必须是 None

        while True:
            try:
                event = gen.send(decision)
            except StopIteration:
                break

            decision = None
            renderer.handle(event)
            store.append(type(event).__name__, asdict(event))

            if isinstance(event, ToolCallRequest):
                decision = approver.ask(event)     # ★ 回灌决定
```

这段 20 行的胶水就是整个系统的装配点：
**内核吐事件 → 渲染 + 落盘 → 遇到请求就问人 → 把答案送回内核。**

CLI 参数：

```
workpilot                      # 新会话
workpilot -p "修复这个 bug"     # 单次执行（非交互）
workpilot --continue           # 继续最近一次会话
workpilot --resume <id>        # 恢复指定会话
workpilot --provider openai    # 切换 provider
workpilot --yolo               # 跳过所有确认
```

---

## 12. 测试策略

**内核测试**：用 `FakeProvider` 按脚本吐 chunk，断言事件序列。零 mock 终端。

```python
def test_tool_call_denied_returns_error_result():
    provider = FakeProvider(script=[
        [Chunk("tool_call", tool_call=ToolCall("t1", "bash", {"cmd": "rm -rf /"})),
         Chunk("done", blocks=[...], usage=..., stop_reason="tool_use")],
        [Chunk("text", text="好的，我不执行。"),
         Chunk("done", blocks=[...], usage=..., stop_reason="end_turn")],
    ])
    loop = AgentLoop(provider, registry, ctx, "sys", tmp_path)

    gen = loop.run_turn("删掉所有文件")
    req = gen.send(None)
    assert isinstance(req, ToolCallRequest)
    assert req.danger is Danger.DESTRUCTIVE

    result = gen.send("deny")           # 拒绝
    assert result.is_error
    assert "declined" in result.output
```

**工具测试**：用真实临时目录，**不 mock 文件系统**——mock 掉就测不出路径逃逸这类真问题。

**压缩测试**：构造含 tool_use/tool_result 配对的历史，断言切点从不落在配对中间。

---

## 13. 分阶段实施路线

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| **M1** 骨架跑通 | events + loop + AnthropicProvider + read_file + 最简 renderer | 能问「这个项目是干嘛的」并让它自己读文件回答 |
| **M2** 工具补全 | 五件套工具 + 权限确认 + 路径安全边界 | 能让它改一个文件并在确认后落盘 |
| **M3** 会话能力 | SessionStore + `--continue` / `--resume` | 关掉终端再开能接着聊 |
| **M4** 长任务支撑 | ContextManager 自动压缩 + prompt caching | 长对话不崩，`cache_read_input_tokens` > 0 |
| **M5** 工程化 | WORKPILOT.md + /slash 命令 + OpenAI Provider | 换 provider 不改内核 |

每个阶段都应保持「可运行」——M1 结束时就该是个能用的（虽然功能少）工具。

---

## 14. 依赖

```toml
# pyproject.toml
[project]
name = "workpilot"
requires-python = ">=3.10"
dependencies = [
    "anthropic>=0.40",   # 官方 SDK
    "rich>=13",          # 终端渲染：Markdown / diff / 语法高亮
    "typer>=0.12",       # CLI 参数解析
]

[project.optional-dependencies]
openai = ["openai>=1.0"]      # OpenAI 兼容 provider 才需要

[project.scripts]
workpilot = "workpilot.cli:main"
```

依赖刻意压到最少。`prompt_toolkit`（历史补全、多行输入）等到 M5 觉得 `input()` 不够用时再加。

---

## 附：三个最容易踩的坑

1. **`tool_use` / `tool_result` 必须严格配对**——工具抛异常也要返回 `is_error: true` 的结果，压缩历史时也不能切断配对。破坏配对 = API 400。
2. **prompt cache 静默失效**——system 里一个时间戳、工具顺序的一次抖动，就让缓存永远不命中，而且**不会报错**。用 `cache_read_input_tokens` 盯着。
3. **bash 输出不截断**——一条 `cat` 大文件就能吃掉几十万 token。所有工具输出都要有硬上限。
