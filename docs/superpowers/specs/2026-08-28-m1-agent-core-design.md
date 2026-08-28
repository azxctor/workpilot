# M1 设计：事件驱动内核与工具调用闭环

> **本文是回溯规格（as-built）**：M1 已交付，这里记录实际实现的设计、
> 落地过程中偏离原计划的地方，以及真实调用中才暴露的问题。
> 事前的整体架构见仓库根目录 `DESIGN.md`。

交付状态：已完成并通过真实模型验证。40 个测试全绿，663 行实现。

---

## 1. 目标与验收

**目标**：跑通最小闭环——用户提问，模型自主调用工具读取文件，基于真实
内容作答。

**验收标准**：能问「这个项目是干嘛的」并让它自己读文件回答。

**实际验收结果**（Kimi k3，`api.kimi.com/coding/v1`）：

```
> main.py 里的 fizzbuzz 函数，输入 15 会返回什么？
  Need to read main.py first.
⏺ read_file(path='main.py')
  ✓ 1   def fizzbuzz(n): (+7 行)
返回 "FizzBuzz"。因为 15 % 15 == 0，命中第一个分支。
tokens: in=445 out=34 cached=256
```

---

## 2. 核心决策：内核只产事件

Agent Loop 是一个生成器，只 `yield` 事件；终端渲染、权限确认、
日志全是这条事件流的消费者。

**三条铁律**（已用 grep 验证）：

1. `agent/` 不 import `ui/` —— 实际依赖仅 `typing`、`dataclasses`、
   `enum` 和自身的 `events`
2. `agent/loop.py` 看不到任何厂商 SDK
3. `loop.py` 中无任何 `print`

**收益兑现情况**：内核的 5 个测试全部零 mock 终端——喂一个假 Provider，
断言事件序列即可。这是选择事件驱动而非分层单体的直接回报。

### 事件类型

```
TextDelta / ThinkingDelta      模型输出的流式增量
ToolCallRequest                唯一需要消费者回灌决定的事件
ToolResult                     工具执行结果（含失败）
ContextCompacted               预留给 M4，M1 未产生
TurnEnd                        本轮结束，携带 token 用量
```

### 双向生成器：权限确认作为控制流

```python
decision = yield ToolCallRequest(...)   # 暂停
# 消费者：gen.send("allow" | "deny")
```

**为什么不用回调函数**：回调需要把 UI 的引用注入内核，直接违反铁律 1。
`send()` 让「暂停等人回答」变成纯粹的控制流，内核对终端一无所知。

装配点是 `cli.drive()`，15 行：内核吐事件 → 渲染 → 遇到请求就问 →
把答案送回内核。

---

## 3. Provider 抽象层

统一接口只有两个方法：`stream()` 产出归一化 `Chunk`，`count_tokens()`。

**内部消息格式采用 Anthropic 的 content-block 结构**，OpenAI 侧做双向
翻译。理由：block 结构信息量更大，反向选型会丢 thinking 块。

### 偏离原计划：OpenAI Provider 提前到 M1

原路线图把 OpenAI 兼容实现放在 M5。实际提前实现了，原因是 M1 验收需要
真实模型，而当时手上唯一可用的凭据是 Kimi。

这个提前带来了额外收益：抽象层在**两个真实后端**上都跑通过，而不是只在
Anthropic 一家上验证过——避免了「抽象层其实只是 Anthropic 的马甲」
这种自欺。

---

## 4. 真实调用暴露的问题

以下三项**全部只在真实 API 调用中暴露**，假 Provider 测试无法发现。
这是 M1 最重要的经验。

### 4.1 endpoint 错误伪装成 key 无效

最初给的 endpoint 返回 401 `Invalid Authentication`，与无效 key 的响应
完全一致。四个模型名全试过、对照了一个随手编造的错误 key——响应逐字相同。
换用正确 endpoint 后同一个 key 立即可用。

**教训**：401 不足以断定 key 无效，必须先确认 endpoint。

### 4.2 思考流走 `delta.reasoning_content`

Kimi 把思考内容放在 `reasoning_content` 而非 `content`。原实现只读
`content`，模型的思考整段丢失且无任何报错。

### 4.3 usage 嵌在 choice 里，且到手是 dict

这个坑套了两层：

- 标准 OpenAI 把 `usage` 放顶层，Kimi 放在 `choices[0].usage`
- 又因为它是非标准字段，SDK 将其收进 pydantic 的 `model_extra`，
  取出来是 **dict 而非对象**

原实现用 `getattr(raw, "prompt_tokens", 0)` 读取，对 dict 静默返回 0，
线上表现为 `tokens: in=0 out=0 cached=0`——**没有任何异常，只是数字不对**。
现已兼容顶层/choice 内、对象/dict 四种组合。

---

## 5. 工具层

M1 只交付 `read_file`（SAFE）。关键约束：

- 带行号返回，2000 行截断并**明确告知模型已截断及总行数**——
  不告知会让模型以为文件就这么长
- `safe_path()` 在 `resolve()` 之后校验是否落在 workspace 内
- `Registry.schemas()` 按名排序——工具顺序抖动会让 prompt cache 整片失效

### 路径逃逸防护的验证方式

真实 agent 测试中，让模型读 workspace 外的 `.env`，密钥没有泄漏——
**但那次是模型自己拒绝的，`safe_path` 根本没被触发**。

模型的自觉不能替代防护层。因此绕过模型直接调用工具层验证，
四种逃逸形式全部拦下：`../`、`../../`、绝对路径 `/etc/passwd`、
`./../../`。

---

## 6. 实测验证的设计假设

DESIGN.md 中标注的头号坑是「`tool_use` / `tool_result` 配对不能断裂」。
这条在真实运行中自己撞上并通过了：

模型并行调用两个工具，其中 `package.json` 不存在，抛出
`FileNotFoundError`。该异常被转为 `is_error: true` 的 `tool_result`
回灌历史，模型正常续跑并给出正确答案。配对完整，无 400。

---

## 7. 交付物

| 模块 | 行数 | 职责 |
|---|---|---|
| `agent/events.py` | 57 | 事件协议 |
| `agent/loop.py` | 90 | Agent Loop 内核 |
| `providers/base.py` | 33 | Chunk / ToolCall / LLMClient 协议 |
| `providers/anthropic_provider.py` | 61 | 默认实现 |
| `providers/openai_provider.py` | 174 | OpenAI 兼容（含 Kimi 适配） |
| `tools/base.py` | 26 | Tool 协议 + Registry |
| `tools/fs.py` | 54 | read_file + safe_path |
| `ui/renderer.py` | 48 | 事件流唯一输出端 |
| `cli.py` | 75 | drive() 装配点 + REPL |
| `config.py` | 45 | system prompt + Provider 工厂 |

测试 40 个，分布：内核 5、Anthropic Provider 6、OpenAI Provider 9、
Provider 工厂 4、渲染 4、工具 7、配置 2、装配 2、端到端 1。

---

## 8. 已知缺口（留给后续里程碑）

| 缺口 | 处理 |
|---|---|
| 权限确认是占位实现（非只读工具一律拒绝） | M2 |
| 只有 `read_file` 一个工具 | M2 |
| 凭据缺失要等到首次提问才暴露，不在启动时检查 | 待定，见下 |
| `ContextManager` 只有接口，无实现 | M4 |
| 会话不持久化 | M3 |

**关于凭据检查**：SDK 是懒加载的，没有 key 时 CLI 能正常启动，
直到第一次提问才报认证错误。体验不佳但不影响正确性，未擅自扩大 M1
范围去修——记录在此，待决定是否单独处理。
