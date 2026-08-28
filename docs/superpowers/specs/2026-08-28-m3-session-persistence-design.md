# M3 设计：会话持久化

> 目标：关掉终端再打开，能接着上次继续聊。
> 验收标准：一轮对话后退出，`--continue` 重进能引用之前的上下文继续作答。

前置：[M1 回溯规格](2026-08-28-m1-agent-core-design.md)、
[M2 工具与权限](2026-08-28-m2-tools-and-permissions-design.md)。
当前 110 个测试全绿，已用 Kimi k3 完成真实读写验收。

---

## 1. 范围

| 做 | 不做（YAGNI） |
|---|---|
| 会话落盘（每条 message 即时追加） | 会话搜索 / 全文检索 |
| `--continue` 恢复当前目录最近一次 | 会话分支、多会话合并 |
| `--resume <id>` 恢复指定会话 | 自动清理 / 容量上限 |
| `--sessions` 列出当前目录的会话 | 会话导出为其他格式 |
| 崩溃残缺的尾部修复 | 加密存储 |

---

## 2. 关键决策：存 history，不存事件流

`DESIGN.md` 原定「一行一个事件」，本设计**推翻该决定**。

**理由**：恢复会话需要的是 `history`（messages 数组），而事件流无法无损
重建它。内核中 `history.append` 存的是 provider 返回的**原始 blocks**，
事件只是它的**投影**：

- `TextDelta` 是碎片，能拼回文本，但拼不回 assistant 消息的 block 结构
- `tool_use` block 的完整字段、thinking block、各 provider 的特有字段，
  在事件里没有完整体现

从投影反推原件必然有损。而这种损伤**不会在恢复时报错**——它会在恢复之后
的第一次请求才炸成 400，届时排查成本极高。M1 的 `usage` 藏在
`model_extra` 里那次已经证明：「看起来一样的结构其实不同」是这个项目
最容易踩的一类坑。

**代价**：JSONL 里是 block 结构，不如事件流可读。接受——会话文件的用途
是恢复，不是给人读。真要审计日志，那是独立需求，届时另加。

---

## 3. 存储格式

```
~/.workpilot/sessions/<session_id>.jsonl        权限 0600
```

每行一条 JSON，两类记录：

```jsonc
// 第 1 行：会话元信息
{"type": "meta", "session_id": "20260828-204930-a3f9",
 "workspace": "/abs/path/to/project", "model": "k3",
 "created_at": "2026-08-28T20:49:30+08:00"}

// 后续每行：一条 message，data 为 history 中的原样内容
{"type": "message", "data": {"role": "user", "content": "..."}}
{"type": "message", "data": {"role": "assistant",
                             "content": [{"type": "tool_use", ...}]}}
```

`data` 是 `history` 里的 message **逐字节原样**，不做任何转换。
恢复即「过滤 `type == "message"` 后取 `data`」，零转换、无损。

### session_id 格式

`<YYYYMMDD>-<HHMMSS>-<4 位随机>`，例如 `20260828-204930-a3f9`。

字典序即时间序，`--continue` 找最近一次只需按文件名排序，不必读内容。
随机后缀避免同秒启动两个实例时冲突。

### 为什么选 JSONL 而非整体 JSON

- 追加写，崩溃/断电不丢已有内容，无需重写整个文件
- 单条 message 写完即刻落盘可读
- 一行损坏不影响其余行

---

## 4. 落盘时机：drive() 中同步增量

事件循环每转一圈，比对 `loop.history` 的长度，把新增的 message 追加落盘。

```python
def drive(loop, user_input, renderer, approve, store=None):
    gen = loop.run_turn(user_input)
    decision, synced = None, len(loop.history)
    while True:
        try:
            event = gen.send(decision)
        except StopIteration:
            break
        ...
        if store:
            synced = store.sync(loop.history, synced)   # 追加 [synced:]
```

**为什么不让内核回调**：`AgentLoop` 每 append 一条就回调 `on_message`，
落盘时机确实更精确，但那等于把「存储」这个概念塞回内核。M1 拒绝
回调式权限确认正是同一理由，此处没有反悔的道理。

**为什么不在 TurnEnd 后整轮落盘**：一轮可能包含多次工具调用、耗时数分钟，
中途崩溃就整轮全丢。而 M3 的意义恰恰是「崩了也不丢」。

代价是 `drive()` 需要读 `loop.history`，耦合略增。这是三个方案里唯一
同时满足「内核干净」与「崩溃不丢」的。

---

## 5. 尾部修复：M3 特有的配对断裂

**问题**：崩溃点若恰在工具执行中途，history 末尾是一条带 `tool_use` 的
assistant 消息，而对应的 `tool_result` 尚未写入。直接把这样的 history
送给 API —— 400。

这是「`tool_use` / `tool_result` 配对不能断裂」在 M3 的新形式：
不是丢弃结果造成的，而是持久化的天然截断造成的。

**处理**：加载后做一次尾部修复。

```
从尾部检查：若最后一条 assistant 消息中存在 tool_use，
且其后没有携带对应 tool_use_id 的 tool_result 消息，
则整条丢弃该 assistant 消息。
```

丢掉一个未完成的回合，好过恢复出一个必然 400 的会话。
丢弃时向用户明确提示「上次有一轮未完成，已丢弃」。

修复只处理尾部。历史中间不可能出现断裂——中间的断裂意味着当时就已经
发送成功过，那本身就是配对完整的。

---

## 6. workspace 隔离

`--continue` **只恢复当前工作目录下的会话**。

**理由**：若恢复全局最近一次，用户从 project-a 切到 project-b 再
`--continue`，history 里全是 A 的文件内容，而 system prompt 中的工作
目录已是 B。模型会拿着 A 的记忆在 B 里操作 —— 这能真正造成误改文件。

**实现**：匹配靠 meta 行的绝对路径。按文件名从新到旧遍历，**只读第一行**
判断 workspace 是否匹配，命中即停。不必解析整个文件。

当前目录无历史会话时，提示「该目录没有历史会话」并开新会话，不报错。

---

## 7. CLI 接口

```
workpilot                  新会话
workpilot --continue       恢复当前目录最近一次
workpilot --resume <id>    恢复指定会话
workpilot --sessions       列出当前目录的会话（id / 时间 / 首条提问摘要）
```

`--sessions` 是必需的：没有它，`--resume` 要求用户凭空知道 id，不可用。
它同样按工作目录过滤，只列出当前目录的会话。

注意与第 6 节的区别：`--continue` 为了快，**只读第一行**判断 workspace；
`--sessions` 要展示首条提问摘要，因此需要多读一行拿到第一条 user
message。两者读取深度不同，不矛盾。

`--continue` 与 `--resume` 互斥，由 argparse 保证。

---

## 8. 边界情况

| 情况 | 处理 |
|---|---|
| `--resume <id>` 不存在 | 报错退出，并列出当前目录可用的会话 id |
| 最后一行残缺（写到一半断电） | 跳过该行，不使整个会话不可用 |
| `--continue` 在新目录找不到匹配 | 提示后开新会话，不报错 |
| 会话文件为空或仅有 meta 行 | 视为空会话，正常开始 |
| 同时给出 `--continue` 与 `--resume` | argparse 报参数互斥错误 |
| `~/.workpilot/sessions/` 不存在 | 首次写入时自动创建 |

---

## 9. 隐私说明

会话文件是**明文**，其中包含：

- 用户输入的全部内容
- 模型读取过的文件内容（`read_file` 的返回值）
- `bash` 命令及其完整输出
- 因此可能包含密钥、令牌、生产数据

**措施**：文件权限设为 `0600`（仅属主可读写），目录同样 `0700`。
本节内容需在 README 中同样写明，让用户知道 `~/.workpilot/sessions/`
里存了什么，可以自行删除。

不做加密——本地明文文件加密需要密钥管理，那是另一个量级的工程，
且在用户自己的机器上收益有限。

---

## 10. 测试策略

沿用既有原则：**用真实临时目录读写文件，不 mock 文件系统**。

必须覆盖：

- 写入即时性：追加一条 message 后，立刻读文件能看到它
- 恢复无损：写入含 `tool_use` blocks 的 history，读回后与原对象相等
- 尾部修复：末尾 `tool_use` 无配对 → 丢弃该条；配对完整 → 不动
- 尾部修复不误伤：连续多轮工具调用且全部配对完整时，一条都不丢
- workspace 隔离：两个目录各自的会话互不可见
- 残行跳过：手工写入半行 JSON，其余行仍能正确加载
- `session_id` 排序：字典序等于时间序
- `--continue` / `--resume` / `--sessions` 的参数解析与互斥
- 文件权限确实是 0600

---

## 11. 验收

单元测试全绿之外，需完成真实模型验证：

用 Kimi k3 进行一轮涉及读文件的对话后退出进程，
再以 `--continue` 重新进入，提问一个**必须依赖上一轮上下文**
才能回答的问题（例如「你刚才读的那个函数叫什么名字」），
确认模型能正确作答。

不能用假 Provider 替代——M1 的经验是，真实调用会暴露假 Provider
覆盖不到的结构差异，而本里程碑恰恰以「结构无损」为核心命题。
