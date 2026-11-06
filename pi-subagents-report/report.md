## 怎么读这份导读

这份导读写给想读懂、贡献或基于 pi-subagents 做定制的工程师。读完你应该能说清：一次 `Agent` 工具调用怎么变成一个独立会话；完成通知为什么不会打断父 agent；工作流脚本为什么禁用 `Date.now()`；新功能该放在哪个文件。

全文顺序：

1. **全景**：问题、目标、限制、总图。
2. **agent 注册表**：内置、自定义、包内 agent 怎么合并。
3. **生命周期管理**：两个并发池、排队、状态。
4. **运行器**：怎么构造一个隔离的子会话。
5. **完成通知**：outbox 和 join。
6. **隔离与嵌套**：worktree、嵌套 agent、作用域。
7. **工作流**：脚本沙箱、确定性、journal 重放、gate。
8. **端到端**：一次后台 `Agent` 调用。
9. **许可证**、**贡献与定制**、**源码索引**。

::: callout key 固定版本
`tintinweb/pi-subagents` @ `50460b5`（2026-11-04，版本 [[ps:package.json#L3|0.23.1]]）。需要 pi 1.1.0 以上。我没有运行过这个扩展，也没有跑过测试，内容来自阅读源码和文档。
:::

## 全景：问题、目标、限制

### 它解决什么

[pi](https://pi.dev) 是一个可扩展的编码 agent，但核心不带子 agent。这个扩展补上这一层，并且刻意和 Claude Code 保持一样的工具名、调用约定和 UI（[[ps:CONTRIBUTING.md#L13|项目哲学]]）。

| 环节 | 问题 | 解决办法 |
|---|---|---|
| **上下文污染** | 主 agent 自己探索代码，上下文很快耗尽 | 子 agent 在独立会话里跑，只把结果带回来 |
| **串行等待** | 模型一次只能发一批工具调用 | 后台 agent + 完成通知 |
| **动态扇出** | 工具调用批次不能循环，也不能根据运行时发现的列表分支 | `SubagentWorkflow` 脚本 |
| **并行写冲突** | 多个 agent 改同一棵树会互相覆盖 | git worktree 隔离 |
| **验证靠嘴** | “修好了”只是模型说的 | 工作流的 `gate`：跑命令，失败就算 agent 失败 |

### 要求 / 目标 / 限制

| 项目 | 内容 |
|---|---|
| **核心目标** | 让子 agent 在 pi 里感觉是原生的，和 Claude Code 行为一致 |
| **功能要求** | 后台/前台、恢复、steering、自定义 agent、工作流、调度、跨扩展 RPC |
| **规模压力** | 工作流单次最多 1000 个 agent（[[ps:src/workflow/runtime.ts@WORKFLOW_AGENT_CAP|WORKFLOW_AGENT_CAP]]）；TUI 不能被卡住 |
| **外部限制** | 运行在 pi 进程内；依赖 pi 的会话、事件和工具 API；peer 依赖 `>=1.1.0` |
| **安全边界** | 和运行 pi 的用户同一信任边界，**不是沙箱**（[[ps:SECURITY.md#L6|SECURITY.md]]） |
| **关键手段** | 独立 `AgentSession`；两个并发池；outbox；vm + worker；journal 重放 |
| **许可证** | MIT |

### 总图

::: flow 扩展在 pi 里的位置
cfg cw=205 rh=100 nw=165
n pi 0 0 "pi 主会话" "模型 · 工具循环" k=dark
n idx 1 0 "index.ts" "工具 · 命令 · UI" k=acc
n mgr 2 0 "AgentManager" "生命周期 · 队列" k=teal
n run 3 0 "agent-runner" "构造子会话" k=teal
n sess 4 0 "子 AgentSession" "pi SDK" k=dark
n ui 0 1 "UI" "widget · FleetView" k=vio
n reg 1 1 "agent 注册表" "默认 + 自定义 + 包" k=vio
n wf 2 1 "工作流运行时" "worker + vm" k=warn
n box 3 1 "CompletionOutbox" "边界投递" k=pass
e pi -> idx
e idx -> mgr
e mgr -> run
e run -> sess
e idx:b -> reg:t dash
e wf:t -> mgr:b
e sess:b -> box:r
e box:b -> pi:b "通知" dash k=pass
e ui:t -> pi:b dash
cap: 深色来自 pi，不在这个仓库里。所有东西都在**同一个 Node 进程**内运行，只有工作流脚本在 worker thread 里。文件地图见 [[ps:README.md#L1063|README 的 Architecture]]。
:::

[[ps:src/index.ts|index.ts]] 有 4370 行，是最大的文件，负责注册工具、命令、渲染器和 `/agents` 菜单，默认导出的函数就是扩展入口（[[ps:src/index.ts#L309|入口]]）。注册的 LLM 工具有 `Agent`、`get_subagent_result`、`steer_subagent` 和可选的 `SubagentWorkflow`。

## agent 注册表

### 职责

回答“`subagent_type: "Explore"` 到底是什么配置”。

### 来源和优先级

| 来源 | 位置 | 代码 |
|---|---|---|
| 内置 | `general-purpose`、`Explore`、`Plan` | [[ps:src/default-agents.ts|default-agents.ts]] |
| 项目 | `.pi/agents/`、`.agents/agents/` | [[ps:src/custom-agents.ts|custom-agents.ts]] |
| 全局 | 用户目录 | [[ps:src/custom-agents.ts|custom-agents.ts]] |
| pi 包 | `package.json` 的 `pi.subagents` | [[ps:src/package-resources.ts|package-resources.ts]] |

统一入口是 [[ps:src/agent-types.ts|agent-types.ts]]。agent 文件是带 YAML frontmatter 的 Markdown，字段见 [[ps:README.md#L361|Frontmatter Fields]]。

一个重要约定：**frontmatter 是权威的**。文件里写了 `model`、`thinking`、`max_turns`、`isolated` 等，调用方传的值就不生效（[[ps:README.md#L391|README]]）。例外也写明了：`isolation` 在工作流路径上由脚本决定，这是为了和 Claude Code 一致。

类型名大小写不敏感，但如果解析不到恰好一个启用的 agent，会回退或报错，不会悄悄选一个。

包的声明有一个刻意的差异：pi 其他资源会扫描约定目录，子 agent **必须按名字声明**（[[ps:docs/packages.md#L13|packages.md]]）。这样装一个包不会意外多出一堆 agent。

::: callout warn 容易误解的地方
`isolated` 和 `isolation` 是两个字段。`isolated: true` 是“密封模式”：去掉扩展和技能。`isolation: worktree` 是文件系统隔离。名字相近，含义完全不同。
:::

## 生命周期管理

### 职责

[[ps:src/agent-manager.ts@AgentManager|AgentManager]] 跟踪每个 agent 的记录，管理并发、排队、恢复和清理。[[ps:src/types.ts@AgentRecord|AgentRecord]] 是核心状态。

### 难点：两个并发池

文件头的注释解释了设计理由，这段值得读（[[ps:src/agent-manager.ts#L1|agent-manager.ts]]）：

| 池 | 默认 | 管谁 |
|---|---|---|
| 后台 | `maxConcurrent = 10` | detached 的 agent |
| 前台 | `maxConcurrentForeground = 0`（不限） | 调用方阻塞等待的 agent |

为什么不是一个池？前台 agent 反正会阻塞父 agent，父 agent 本来也能自己干这个活。如果它占后台名额，满载的后台池就会饿死主会话。嵌套子 agent 和工作流 agent 都不占池：嵌套子 agent 排在等它的父 agent 后面会死锁，工作流有自己的上限（[[ps:README.md#L675|Concurrency]]）。

### 一个精巧的细节

[[ps:src/agent-manager.ts@AgentManager.startAgent|startAgent]] 在**第一个 await 之前**就把状态设成 running 并占用名额。原因：创建 worktree 是异步的，而 `drainQueue` 是同步循环读计数器。如果 await 之后才加一，它会把队列里的 agent 全部启动。

池也只解析一次并记在 `record.chargedPool`：用户可能在运行中改设置，settle 时重新计算会减错池，要么计数下溢，要么名额泄漏。这类注释是这个项目的特点，读它们比读代码本身更省时间。

::: flow 一个 agent 的状态变化
cfg cw=205 rh=96 nw=165
n q 0 0 "queued" "池满时排队" k=warn
n r 1 0 "running" "占用名额" k=acc
n s 2 0 "结束" "完成/出错/中止" k=teal
n w 3 0 "worktree 收尾" "提交到分支" k=vio
n n 4 0 "通知 / 结果" "outbox" k=pass
n rs 2 1 "resume" "重新打开会话" k=teal
n st 1 1 "steer" "运行中插话" k=acc
n bg 0 1 "转后台" "ctrl+b · 单向" k=warn
e q -> r
e r -> s
e s -> w
e w -> n
e n:b -> rs:r dash
e rs:t -> s:b dash
e st:t -> r:b dash
e bg:r -> st:l dash
cap: 对应 [[ps:src/agent-manager.ts@AgentManager.spawnAndWait|spawnAndWait]]、[[ps:src/agent-manager.ts@AgentManager.sendToBackground|sendToBackground]]、[[ps:src/agent-manager.ts@AgentManager.resume|resume]]、[[ps:src/agent-manager.ts@AgentManager.steer|steer]]。转后台是单向的（[[ps:README.md#L113|README]]）。
:::

被驱逐的 agent 还能恢复：结束时写一条 `subagents:record` 会话条目，`get_subagent_result` 和 resume 都能回退到它（[[ps:src/agent-record-entry.ts|agent-record-entry.ts]]）。

## 运行器：构造子会话

### 职责

[[ps:src/agent-runner.ts@runAgent|runAgent]] 把“类型 + prompt + 选项”变成一个跑起来的 `AgentSession`，返回结果。

### 难点

子会话要和主会话共用 pi 的机制，但配置必须独立。而且默认的 pi 行为会破坏这种独立：`AGENTS.md` 会被自动加到系统提示后面，这样 `prompt_mode: replace` 就失效了。

### 原理

`runAgent` 的主要步骤：

1. 解析工作目录：worktree 覆盖 > 父 cwd。文件操作在 `effectiveCwd`，配置发现在 `configCwd`。
2. `isolated` 在这里解析，不靠调用方，因为 mention 和 RPC 不传这个值。
3. 预加载技能、处理 memory：有写工具就给读写记忆，否则给只读记忆。
4. [[ps:src/prompts.ts|prompts.ts]] 构建系统提示。
5. 构造 `DefaultResourceLoader`，设 `noContextFiles: true`，手动转发 pi 内置扩展。
6. 继承父项目的信任决定，否则被拒绝信任的项目里，子 agent 会加载 `.pi` 扩展和 MCP 服务。
7. 运行，观察 turn，处理 max turns。

第 6 点是一个安全相关的细节：`SettingsManager` 默认信任，所以必须显式传 `projectTrusted`（[[ps:src/agent-runner.ts#L942|代码]]）。

下面这张图是一个子会话的组成。每一块的来源都不同，这也是 `runAgent` 这么长的原因。

::: flow 一个子会话由什么组成
cfg cw=215 rh=92 nw=175
n a 0 0 "agent 配置" "frontmatter" k=acc
n m 1 0 "记忆块" "读写或只读" k=teal
n sk 2 0 "预加载技能" "写进提示" k=teal
n sp 1 1 "系统提示" "replace · append" k=vio
n ld 2 1 "ResourceLoader" "扩展过滤" k=vio
n set 3 1 "SettingsManager" "继承项目信任" k=warn
n se 2 2 "AgentSession" "pi SDK" k=dark
e a:b -> sp:t
e m -> sp
e sk:b -> sp:t
e sp:r -> ld:l
e set -> ld
e ld -> se
cap: 系统提示由 [[ps:src/prompts.ts|prompts.ts]] 构建。`replace` 模式下不继承 `AGENTS.md`，`append` 模式才把父提示嵌进来。
:::

### 工具和扩展的作用域

这里有三个独立的旋钮，容易混淆：

| 字段 | 控制什么 | 位置 |
|---|---|---|
| `extensions` | **加载**哪些扩展 | [[ps:src/agent-runner.ts@parseExtensionsSpec|parseExtensionsSpec]] |
| `tools` 里的 `ext:` | 扩展工具里**暴露**哪些给模型 | [[ps:src/agent-runner.ts@parseExtSelectors|parseExtSelectors]] |
| `disallowed_tools` | 无论如何都**拒绝** | [[ps:README.md#L1050|Tool Denylist]] |

源码注释特别提醒：排除的扩展工厂仍会执行一次，排除只是阻止绑定处理器和注册工具，**它不是沙箱**。

### 优雅的 turn 上限

硬中止会留下半截输出。[[ps:src/agent-runner.ts@observeRun|observeRun]] 在接近上限时发一条“收尾”提示，再给 `graceTurns` 轮宽限（[[ps:README.md#L658|Graceful Max Turns]]）。这样得到的是完整的部分结果。

### 入口不止一个

派生 agent 有四条路：`Agent` 工具、`@handle` mention、工作流、跨扩展 RPC。`Agent` 工具和工作流直接调用 manager；mention 和 RPC 的参数不完全可信，要先过一道过滤。

::: flow 四条派生路径汇到同一处
cfg cw=225 rh=88 nw=180
n a 0 0 "Agent 工具" "模型调用" k=acc
n b 0 1 "@handle mention" "用户输入" k=acc
n c 0 2 "跨扩展 RPC" "pi.events" k=warn
n d 0 3 "工作流 agent()" "脚本调用" k=vio
n top 1 1.5 "spawnTopLevel" "剥掉内部字段" k=teal
n res 2 1.5 "spawnResolved" "解析后派生" k=teal
n mgr 3 1.5 "AgentManager" "统一生命周期" k=pass
e a:r -> mgr:t
e b:r -> top:l
e c:r -> top:l
e d:r -> mgr:b
e top -> res
e res -> mgr
cap: `Agent` 工具在 [[ps:src/index.ts#L2319|index.ts]] 调 `manager.spawn`，工作流在 [[ps:src/workflow/host.ts|host.ts]] 调 `spawnAndWait`。[[ps:src/index.ts#L737|spawnTopLevel]] 会删除 `parentAgentId`、`workflowId`、`resumeSessionFile`、`rootSessionId`、`blocking` 等内部字段。
:::

删字段是有安全考虑的：`rootSessionId` 决定转录目录，伪造就是路径穿越；`resumeSessionFile` 决定打开哪个文件当对话重放。注释把每个字段的风险都写清了。唯一的例外是恢复 mention：它走 `spawnResolved`，因为那些值来自扩展自己写的记录（[[ps:src/index.ts#L1038|代码]]）。

## 完成通知

### 难点

后台 agent 完成时，父 agent 可能正在跑工具、空闲，或刚被 Esc 中止。pi 的 steering 队列在默认的 one-at-a-time 模式下会把多条消息拆成多次模型调用，很贵。

### 原理

[[ps:src/completion-outbox.ts@CompletionOutbox|CompletionOutbox]] 把通知攒起来，在边界上一次发出：

| 父 agent 状态 | 处理 |
|---|---|
| 忙 | 留到 `turn_end`，这时工具批次已结束，下一次模型调用还没开始 |
| 空闲 | 短暂等 [[ps:src/completion-outbox.ts@NOTIFICATION_HOLD_MS|200ms]]，让接近同时完成的合并到一轮 |
| 被中止或失败 | 继续持有，等运行结束再发，因为 pi 在 Esc 时会清空自己的队列 |

另外两个细节：读取结果会取消对应通知（[[ps:src/completion-outbox.ts@CompletionOutbox.cancel|cancel]]），不会既读到又被通知；同一 agent 的新完成替换旧通知。

::: callout analogy 前台的信件格
- **信件** = 子 agent 的完成通知。
- **你** = 父 agent，开会时不被打断。
- **信件格** = outbox：会议结束（`turn_end`）一起拿给你。
- **你已经主动去取了** = `get_subagent_result`：这封信就从格子里撤掉。

你不在开会时，前台也会等 200ms，看看有没有紧跟着的第二封，好一起送上来。
:::

join 模式在这之上：`smart` 逐个通知；`group` 把同一轮派生的 agent 合成一条，第一个完成后 30 秒超时（[[ps:src/group-join.ts@GroupJoinManager|GroupJoinManager]]）。

## 隔离与嵌套

### Worktree

[[ps:src/worktree.ts|worktree.ts]] 创建临时 git worktree。没有改动就清理，有改动就提交到分支，把分支名返回。所有 git 调用都走异步的 `pi.exec`，否则并发隔离的 agent 会卡住 TUI 事件循环。

失败时**不回退**：不是 git 仓库或没有提交时直接报错，绝不悄悄在主树上跑（[[ps:src/agent-manager.ts#L760|startAgent]]）。

::: callout warn 容易误解的地方
worktree agent 结束后改动在分支上，不在主树。所以串行的“修复再验证”不要用隔离，否则验证的是没修过的树。[[ps:examples/workflows/gated-fix.js|gated-fix.js]] 的文件头专门讲了这个坑。
:::

### 嵌套

默认关闭。agent 设置 `allowed_subagents` 才会得到作用域内的 `Agent` 等工具（[[ps:src/nested-tools.ts|nested-tools.ts]]）。深度上限 [[ps:src/nested-tools.ts@maxSubagentDepth|maxSubagentDepth]] 默认 2：主会话是 0，子 agent 是 1，再下一层是 2。

## 工作流

### 职责

`SubagentWorkflow` 让模型写一段 JavaScript 脚本来编排 agent。**你不写脚本，模型写**（[[ps:docs/workflows.md#L5|workflows.md]]）。脚本可用 `agent()`、`parallel()`、`pipeline()`、`phase()`、`log()`。

```js
export const meta = { name: 'gated-fix', description: '…', phases: [{ title: 'Fix' }] }

phase('Fix')
let fixed = await agent(task, { label: 'fix', gate: testCommand })
if (fixed === null) {
  fixed = await agent(`${testCommand} is still failing…`, { label: 'fix', resume: 'fix' })
}
```

简化自 [[ps:examples/workflows/gated-fix.js|gated-fix.js]]。

### 两层边界

这是全项目最有意思的设计，[[ps:src/workflow/worker-source.ts|worker-source.ts]] 的文件头讲得很透。

::: flow 两层边界，目的不同
cfg cw=260 rh=98 nw=215
n h 0 0 "host thread" "AgentManager · 上限" k=acc
n w 1 0 "worker thread" "可被 terminate" k=teal
n s 2 0 "vm context" "workflow 脚本" k=warn
n a 0 1 "spawn 真实 agent" "唯一的 agent 通道" k=pass
e s -> w
e w -> h
e h -> a
cap: worker 那层是为了**能杀掉**：`vm` 超时对已经进入 `await` 的脚本无效，`worker.terminate()` 可以。vm 那层是为了**确定性和防手滑**，不是安全边界。[[ps:src/workflow/runtime.ts|runtime.ts]] 说脚本唯一的路径就是 `call` 消息，所以上限才绕不过去。
:::

### 为什么不算安全边界

文件头直说：注入的全局函数是 host 闭包，`agent.constructor` 还是 host 的 `Function`。真正承重的防线是 `codeGeneration: { strings: false }`，它让 `Function("…")` 和 `eval` 抛错。作者明确写“这是确定性边界，不是针对恶意脚本的安全边界”。

### 确定性和 journal

工作流可以恢复。恢复靠 [[ps:src/workflow/journal.ts|journal]] **按前缀重放**：已完成的调用直接返回记录的结果，不重新派生 agent。如果脚本读了时钟或随机数，第二次运行的前缀就对不上，重放会悄悄偏离。

所以 [[ps:src/workflow/worker-source.ts@DETERMINISM_PRELUDE|DETERMINISM_PRELUDE]] 把 `Date.now`、`Math.random` 改成抛错，并用词法遮蔽替换 `Date`。错误信息直接告诉作者：时间戳通过 `args` 传入。

prelude 必须写成**一行**：脚本从第 2 行开始编译，host 传 `lineOffset: -1`，多一个换行所有栈帧行号都会错。

::: steps 中断后恢复一个工作流
::: flow
cfg cw=200 rh=92 nw=165
n a 0 0 "脚本重新执行" "从头开始" k=acc
n b 1 0 "第 N 次 agent()" "算 journal key" k=teal
n c 2 0 "命中 journal" "返回旧结果" k=pass
n d 3 0 "未命中" "真的派生" k=warn
n e 4 0 "写入 journal" "可再次恢复" k=vio
e a -> b
e b -> c
e c -> d dash
e d -> e
:::
--- step 重新运行 {hl=a}
带 `resumeFromRunId` 再调用工具，脚本从第一行重新执行。
--- step 计算 key {hl=a,b}
每次 `agent()` 由 [[ps:src/workflow/journal.ts@journalKey|journalKey]] 算出 key，所以调用顺序和参数决定匹配。
--- step 重放 {hl=b,c}
前缀里的调用立即返回记录的结果，不花 token。
--- step 接着跑 {hl=c,d}
第一个没命中的调用开始真正派生 agent。
--- step 记录 {hl=d,e}
每个调用完成时追加写入，所以这次运行也能再次恢复。
:::

::: callout analogy 游戏的存档点
- **存档** = journal 里记录的每次 `agent()` 结果。
- **读档** = `resumeFromRunId`：从头跑脚本，但已存档的关卡直接跳过。
- **关卡必须和上次一样** = 确定性：如果地图是随机生成的（`Math.random`），存档就对不上。

禁用时钟和随机数不是洁癖，是读档的前提。
:::

### gate

`gate` 是一条 shell 命令，agent 结束后运行，非零退出就算失败，`agent()` 返回 `null`（[[ps:src/workflow/runtime.ts#L483|applyGate 注释]]）。这是“别问模型，跑命令”的落地。worktree agent 的 gate 要在它改过的树上跑，所以交给 host 在 settle 里执行。`gate` 不能和 `resume` 同时用。

### meta

`export const meta` 在 `vm` 里是语法错误，而 UI 又要在执行前知道阶段。[[ps:src/workflow/meta.ts|meta.ts]] 不引入 acorn，而是自己扫描到匹配的括号，在空 vm 里求值这个片段。纯字面量没有东西可调用，所以是安全的。扫描器要识别字符串、注释和正则，因为 `detail` 文本里经常有花括号。

## 端到端：一次后台 Agent 调用

假设模型调用 `Agent({ subagent_type: "Explore", prompt: "找认证代码", run_in_background: true })`。

::: steps 后台 Agent 调用
::: flow
cfg cw=190 rh=96 nw=152
n t 0 0 "Agent 工具" "index.ts" k=acc
n r 1 0 "解析配置" "类型 · 模型 · 范围" k=acc
n m 2 0 "manager.spawn" "入池或排队" k=teal
n s 3 0 "startAgent" "占名额 · worktree" k=teal
n ra 4 0 "runAgent" "构造会话" k=teal
n se 4 1 "子会话运行" "observeRun" k=dark
n fin 3 1 "收尾" "settle · 记录" k=vio
n ob 2 1 "outbox" "等边界" k=pass
n par 1 1 "父 agent" "steering 消息" k=dark
n get 0 1 "读取结果" "get_subagent_result" k=acc
e t -> r
e r -> m
e m -> s
e s -> ra
e ra -> se
e se -> fin
e fin -> ob
e ob -> par
e par -> get dash
:::
--- step 工具调用 {hl=t}
`Agent` 工具在 [[ps:src/index.ts#L2585|index.ts]] 注册。
--- step 解析 {hl=t,r}
[[ps:src/invocation-config.ts@resolveAgentInvocationConfig|resolveAgentInvocationConfig]] 合并参数和 frontmatter；[[ps:src/model-resolver.ts|model-resolver.ts]] 解析模型；开了 model scope 就检查白名单。
--- step 派生 {hl=r,m}
[[ps:src/index.ts#L2319|manager.spawn]] 登记记录，池满就排队，工具立即返回 ID。
--- step 启动 {hl=m,s}
`startAgent` 同步占用名额，再创建 worktree（如果需要）。
--- step 构造会话 {hl=s,ra}
`runAgent` 构建提示、loader、设置，创建 `AgentSession`。
--- step 运行 {hl=ra,se}
子会话跑完，转录同时写到输出文件（[[ps:src/output-file.ts|output-file.ts]]）。
--- step 收尾 {hl=se,fin}
释放名额、收尾 worktree、写记录条目，再 `drainQueue`。
--- step 投递 {hl=fin,ob,par}
通知进 outbox，在父 agent 的下一个边界作为一条 steering 消息送达，带结果预览。
--- step 取全文 {hl=par,get}
需要完整输出时调用 `get_subagent_result`，这同时取消尚未送达的通知。
:::

## 开源许可证

| 部分 | 许可证 | 证据 |
|---|---|---|
| 全部代码 | MIT，Copyright 2026 tintinweb | [[ps:LICENSE|LICENSE]]、[[ps:package.json#L6|package.json]] |
| 运行时依赖 | `croner`、`nanoid` | [[ps:package.json#L32|dependencies]]，各自的许可证请自行核对 |
| pi 本体 | peer 依赖，不随包发布 | [[ps:package.json#L26|peerDependencies]] |

MIT 的要求只有一条：分发时保留版权和许可声明。

| 场景 | 允许吗 | 要做什么 |
|---|---|---|
| 自用 | 允许 | 无 |
| 提 PR | 允许 | 先开 issue；没有看到 CLA 要求 |
| 公开 fork | 允许 | 保留 LICENSE |
| 发 npm 包 | 允许 | 保留 LICENSE，换包名 |
| 对外提供服务 | 允许 | MIT 无网络条款 |
| 闭源复用 | 允许 | 保留声明 |

历史：GitHub API 显示 `LICENSE` 只有 2026-03-05 的初始提交一次，一直是 MIT。`media/` 里的 logo 没有单独声明，商用前可以问作者（这是我的提醒，仓库没有写）。

::: callout info 不是法律意见
这是工程师读代码时的理解。
:::

## 贡献与定制

### 开发环境

```bash
git clone https://github.com/tintinweb/pi-subagents
cd pi-subagents
npm install
npm run check          # lint + typecheck + test
pi -e ./src/index.ts   # 加载本地扩展
```

命令来自 [[ps:AGENTS.md#L56|AGENTS.md]] 和 [[ps:README.md#L85|README]]。

### 项目规则

- **唯一规则：你必须理解自己的代码**，否则 PR 会被关闭（[[ps:CONTRIBUTING.md#L19|The One Rule]]）。
- 超过小修的改动先开 issue。
- 不要编辑 `CHANGELOG.md`，由维护者写。
- 改用户可见的行为要更新 README。
- 引用代码用符号，不用行号（[[ps:AGENTS.md#L54|文档规则]]）。
- 不用内联 import，不用 `any`，top-level imports only。
- PR 标题用 conventional commit，描述分 Summary、What changed、Performance、Testing 等节（[[ps:AGENTS.md#L86|Filing PRs]]）。
- 维护者对外部 PR 的审查规则很严：先当作可能恶意来检查。

### 适合入手的方向

- 补 `test/` 里的边界用例，这个项目有 110 多个测试文件。
- [[ps:examples/workflows/|examples/workflows]] 加新配方，它们被 [[ps:test/workflow-examples.test.ts|测试]] 执行。
- 改善文档。

需要避开：和 Claude Code 不一致的工具名和参数；范围之外的新功能。

### 定制路线

::: flow 想让它按我的方式工作
cfg cw=240 rh=90 nw=195
n q 0 1 "要改什么？" "从最轻的开始" k=dark
n a 1 0 "写 agent 文件" ".pi/agents/*.md" k=pass
n b 1 1 "写工作流脚本" "保存为命名工作流" k=teal
n c 1 2 "写 pi 扩展" "RPC + 事件" k=vio
n d 2 2 "fork" "改 src" k=warn
e q:r -> a:l
e q -> b
e q:r -> c:l
e c -> d dash
cap: 其他扩展通过进程内事件总线调用，见 [[ps:docs/rpc.md|rpc.md]] 和 [[ps:src/cross-extension-rpc.ts|cross-extension-rpc.ts]]。发包见 [[ps:docs/packages.md|packages.md]]。
:::

| 路线 | 代价 | 适合 |
|---|---|---|
| agent 文件 | 很低 | 专用角色、模型、工具集 |
| 工作流 | 低 | 可复用的编排流程 |
| 发 pi 包 | 低 | 团队共享 agent 和工作流 |
| 写扩展调 RPC | 中 | 和其他功能集成 |
| fork | 高 | 改核心行为，要跟随 pi 的 API 变化 |

### 仓库里没有的东西

- **pi 本体**：`@earendil-works/pi-coding-agent`、`pi-ai`、`pi-tui` 都是外部包。`AgentSession` 的内部行为要看它们的源码。
- **沙箱**：子 agent 拥有用户权限。需要隔离就放进容器或 VM，SECURITY.md 说得很明确。
- **防提示注入**：SECURITY.md 明说 `AGENTS.md`、agent 文件都可以注入，且无法防御。只在可信仓库里使用。

## 附录：源码索引

| 主题 | 链接 |
|---|---|
| 入口 | [[ps:src/index.ts|index.ts]] |
| 类型 | [[ps:src/types.ts|types.ts]] |
| 生命周期 | [[ps:src/agent-manager.ts|agent-manager.ts]] |
| 运行器 | [[ps:src/agent-runner.ts|agent-runner.ts]] |
| 通知 | [[ps:src/completion-outbox.ts|completion-outbox.ts]]、[[ps:src/group-join.ts|group-join.ts]] |
| 注册表 | [[ps:src/agent-types.ts|agent-types.ts]]、[[ps:src/custom-agents.ts|custom-agents.ts]] |
| 隔离 | [[ps:src/worktree.ts|worktree.ts]]、[[ps:src/nested-tools.ts|nested-tools.ts]] |
| 调度 | [[ps:src/schedule.ts|schedule.ts]]、[[ps:src/schedule-store.ts|schedule-store.ts]] |
| 记忆 | [[ps:src/memory.ts|memory.ts]] |
| 工作流 | [[ps:src/workflow/runtime.ts|runtime.ts]]、[[ps:src/workflow/worker-source.ts|worker-source.ts]]、[[ps:src/workflow/meta.ts|meta.ts]]、[[ps:src/workflow/journal.ts|journal.ts]] |
| UI | [[ps:src/ui/agent-widget.ts|agent-widget.ts]]、[[ps:src/ui/fleet-list.ts|fleet-list.ts]]、[[ps:src/ui/conversation-viewer.ts|conversation-viewer.ts]] |
| 设置 | [[ps:src/settings.ts|settings.ts]] |
| 文档 | [[ps:docs/workflows.md|workflows.md]]、[[ps:docs/rpc.md|rpc.md]]、[[ps:docs/packages.md|packages.md]] |
| 规则 | [[ps:AGENTS.md|AGENTS.md]]、[[ps:CONTRIBUTING.md|CONTRIBUTING.md]]、[[ps:SECURITY.md|SECURITY.md]] |
