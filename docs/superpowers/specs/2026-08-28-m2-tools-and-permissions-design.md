# M2 设计：工具补全与权限确认

> 目标：把 M1 的只读骨架扩展成能真正改代码的助手。
> 验收标准：用真实模型让它改一个文件，确认后落盘，改动正确。

前置：M1 已交付事件驱动内核、`read_file`、Anthropic/OpenAI 两个 Provider，
40 个测试全绿，并已用 Kimi k3 跑通真实 agent 链路。

---

## 1. 范围

### 新增工具

| 工具 | 等级 | 职责 |
|---|---|---|
| `list_files` | SAFE | 列目录树 |
| `grep` | SAFE | 正则搜索文件内容 |
| `write_file` | CONFIRM | 新建或整体重写文件 |
| `edit_file` | CONFIRM | 精确字符串替换 |
| `bash` | DESTRUCTIVE | 执行 shell 命令 |

### 替换占位实现

`cli.approve_safe_only`（M1 的占位：非只读一律拒绝）
→ `ui/confirm.Approver`（三态交互确认）

### 不做（YAGNI）

- 命令白名单自动放行（可被组合绕过，制造假安全感）
- 写操作的自动备份 / 回滚（git 已经是回滚机制）
- 权限决策持久化到磁盘（会话内记忆足够，落盘会让"我上次允许过什么"变得不可见）

---

## 2. 编辑文件：两个工具而非一个

`write_file` 管新建和整体重写，`edit_file` 管精确替换。

**为什么不只用 `write_file`**：让模型改一行就得重写全文，token 浪费是次要的，
主要问题是长文件重写时模型容易丢内容——它得凭记忆复述整个文件。

**`edit_file` 的安全核心是「必须唯一匹配」**：

```
old_string 在文件中出现 0 次  → 报错。模型记错了内容，它必须先读准确
old_string 出现 2 次及以上    → 报错，并告知出现次数。可能改错位置
old_string 恰好出现 1 次      → 执行替换
```

两种失败都**拒绝执行并把实情返回给模型**，绝不猜一个。这条约束顺带
强制了正确的工作流：模型必须先 `read_file` 拿到真实内容，才可能构造出
唯一匹配的 `old_string`。

---

## 3. 安全边界

### 文件工具：复用已验证的 safe_path

`write_file` / `edit_file` / `list_files` / `grep` 全部走 M1 已验证的
`safe_path()`——`resolve()` 之后校验是否落在 workspace 内。该函数在 M1
已实测拦下 `../`、`../../`、绝对路径、`./../../` 四种逃逸形式。

### bash：这是一条弱边界，如实说明

`bash` 只能通过 `cwd=workspace` 约束工作目录。命令里写绝对路径照样能
读写工作区之外的任何位置——**这不是一个真正的沙箱**。这正是它被定为
`DESTRUCTIVE`、每次都必须人工确认、且永不适用「始终允许」的原因。

硬性限制：
- `timeout=120s`，超时则终止并返回超时信息
- 输出截断到 30KB（一条 `cat` 大文件就能吃掉几十万 token）
- 用 `shell=True` 执行。模型给的本来就是一条 shell 命令（含管道、
  重定向、`&&`），假装能把它安全地拆成 argv 反而更危险——那只会
  在解析层制造一个新的、更隐蔽的绕过面

### 危险模式高亮：提示，不是防护

确认界面对以下模式额外标红警告，但**不阻断**：

```
rm -rf / rm -fr        sudo             chmod 777
curl ... | sh          wget ... | sh    > 重定向到已有文件
git push --force       dd if=           mkfs
```

作用是降低「随手敲 y」的概率。必须避免误伤：`git rm --cached` 不是
`rm -rf`，`echo x >> log` 是追加不是覆盖。这条要有测试锁定。

---

## 4. 权限确认层

### 位置与边界

`Approver` 住在 `ui/confirm.py`，是事件流的消费者。内核只负责
`yield ToolCallRequest` 然后接收一个字符串，**不知道确认是怎么发生的**——
这是 M1 三条铁律的延续。

```python
class Approver:
    def __init__(self, console, yolo: bool = False):
        self.console = console
        self.yolo = yolo
        self.always: set[str] = set()      # 会话内记忆，不落盘

    def ask(self, req: ToolCallRequest) -> str:   # "allow" | "deny"
        ...
```

### 判定顺序

```
yolo 开启                         → allow
danger 是 SAFE                    → allow
danger 是 CONFIRM 且在 always 里  → allow
stdin 不是 TTY                    → deny   ← 见下
否则                              → 渲染预览，交互询问
```

**非交互环境一律 deny**：管道运行（`echo ... | workpilot`）时静默放行
写操作是不可接受的。宁可让自动化场景显式加 `--yolo`，也不要默认放行。

### always 的粒度：按工具，且 DESTRUCTIVE 永不适用

对 `edit_file` 点一次 `a`，本次会话内它改任何文件都不再问；
`bash` 敲 `a` 只当作单次 allow，并明确告知它不支持始终允许。

取舍理由：编码任务的自然粒度就是「这一轮我信任它改文件」。按文件粒度
会让跨文件重构退化成无脑连敲 y，反而降低每次确认的注意力。真正不可逆的
破坏面在 `bash`，守死那一处即可。

### 预览形态因工具而异

确认环节的价值全在预览质量上：

| 工具 | 预览内容 |
|---|---|
| `write_file`（新建） | 目标路径 + `+N 行` + 内容头部若干行 |
| `write_file`（覆盖） | `difflib` 算出的 unified diff，绿加红减 |
| `edit_file` | 只显示被替换的那一小段前后对照，附命中行号 |
| `bash` | 完整命令 + 工作目录（+ 命中危险模式时的红色警告行） |

交互提示：`[y]es / [n]o / [a]lways`。

---

## 5. 文件组织

```
tools/fs.py        read_file, list_files, write_file, edit_file   (~180 行)
tools/search.py    grep
tools/shell.py     bash
ui/confirm.py      Approver + 各工具的预览渲染
```

`fs.py` 涨到约 180 行仍在可控范围；若继续增长则按「读」与「写」拆开。
预览渲染放在 `ui/` 而非工具里——工具不该知道自己会被怎么展示。

---

## 6. 测试策略

沿用 M1 的原则：**工具用真实临时目录，不 mock 文件系统**。mock 掉就
测不出路径逃逸这类真问题——M1 写 `safe_path` 测试时，正是先看到未防护
版本真的读到了 workspace 外的文件。

必须覆盖的行为：

- `edit_file` 匹配 0 次 → 抛错且**文件未被修改**
- `edit_file` 匹配 2 次 → 抛错、告知次数、且文件未被修改
- `edit_file` 匹配 1 次 → 正确替换，其余内容逐字节不变
- `write_file` / `edit_file` 拒绝写到 workspace 外
- `bash` 超时确实中断
- `bash` 输出确实截断到 30KB
- `list_files` 跳过 `.git` / `node_modules` / `.venv`
- `grep` 在有/无 ripgrep 两条路径下，返回**相同的命中集合**
  （文件路径 + 行号 + 行内容）；输出的排版格式由 grep 工具自己统一，
  不依赖 ripgrep 的原生格式
- `Approver` 在非 TTY 下返回 deny
- `always` 对 CONFIRM 生效、对 DESTRUCTIVE 不生效
- 危险模式命中 `rm -rf`，但**不误伤** `git rm --cached`

---

## 7. 验收

单元测试全绿之外，必须完成一次真实模型验证：

用 Kimi k3 让它修改一个真实文件（例如给函数加参数校验），
观察：模型先读文件 → 发起 `edit_file` → 确认界面展示正确的 diff →
批准后落盘 → 文件内容确实按预期改变。

这一步不能用假 Provider 替代——M1 的经验是，两个非标准行为
（`reasoning_content`、`usage` 嵌在 choice 里且是 dict）都只有在
真实调用中才暴露出来。
