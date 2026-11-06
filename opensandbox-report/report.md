## 怎么读这份导读

这份导读写给想部署、贡献或定制 OpenSandbox 的工程师。读完你应该能说清：控制面和数据面为什么分开；一次创建和一次命令执行分别经过哪些组件；Docker、Kubernetes、Fast Sandbox 三种后端有什么差别；新运行时、新 SDK 能力该改哪里。

全文顺序：

1. **全景**：问题、目标、限制，六个面。
2. **协议**：`specs/` 是公共合约。
3. **控制面**：生命周期服务器。
4. **运行时后端**：Docker、Kubernetes、Fast Sandbox。
5. **数据面**：execd。
6. **网络面**：ingress、egress、凭据保险库。
7. **Kubernetes 控制器**：CRD 和资源池。
8. **SDK 与工具**。
9. **端到端**：从 `Sandbox.create` 到一条命令。
10. **许可证**、**贡献与定制**、**源码索引**。

::: callout key 固定版本
`opensandbox-group/OpenSandbox` @ `0da12cf`（2026-11-06）。我没有部署或运行过，内容来自阅读代码和仓库文档。性能数字都来自项目自己的文档，我没有复测。
:::

## 全景：问题、目标、限制

### 它解决什么

AI 应用要执行模型生成的代码、跑命令、操作浏览器。这些事不能在应用进程里做。

| 环节 | 问题 | 解决办法 |
|---|---|---|
| **隔离** | 模型生成的代码不可信 | 容器、gVisor/Kata/Firecracker，加网络控制 |
| **环境可移植** | 本地用 Docker，生产用 K8s，代码不想改两遍 | 统一的生命周期 API |
| **启动延迟** | 每轮 agent 都等容器启动太慢 | 资源池、Fast Sandbox 模板 |
| **凭据泄露** | agent 要调外部 API，但不能拿到密钥 | 出站代理注入凭据 |
| **评估规模** | RL 和 agent 评估要大批量沙箱 | 批量创建、资源池 |

### 要求 / 目标 / 限制

| 项目 | 内容 |
|---|---|
| **核心目标** | 通用沙箱平台，运行在用户自己的基础设施上 |
| **功能要求** | 生命周期、命令、文件、代码、PTY、端点、快照、暂停恢复、出站策略 |
| **规模压力** | README 给出 Firecracker 创建 64ms P50（串行）、125.4ms P99（10 并发）（[[os:README.md#L36|Features]]） |
| **外部限制** | 多语言 SDK 要一致；Docker 和 K8s 能力不同；Fast Sandbox 平台在别的仓库 |
| **关键手段** | OpenAPI 合约；统一 `SandboxService`；数据面直连；fail-closed 出站 |
| **许可证** | Apache-2.0，LF Projects 技术章程，AAIF 项目 |

### 六个面

[[os:docs/architecture/index.md#L24|架构总览]] 把项目分成六个面，这是读仓库最好的地图：

::: flow 控制面和数据面分开走
cfg cw=205 rh=100 nw=165
n sdk 0 1 "SDK · CLI · MCP" "五种语言" k=acc
n srv 1 0 "生命周期服务器" "FastAPI" k=teal
n rt 2 0 "运行时后端" "Docker · K8s" k=teal
n box 3 1 "沙箱" "用户负载" k=dark
n ex 2 1 "execd" "沙箱内守护进程" k=pass
n ing 1 1 "ingress" "K8s 入口网关" k=vio
n eg 3 2 "egress" "出站 sidecar" k=warn
n net 4 2 "外部服务" "API · 模型" k=dark
n spec 0 2 "specs/" "OpenAPI 合约" k=vio
e sdk:t -> srv:l "生命周期"
e srv -> rt
e rt:b -> ex:t dash
e sdk -> ing
e ing -> ex
e ex -> box
e box:b -> eg:t
e eg -> net
e spec:t -> sdk:b dash
cap: 三条路径：生命周期走服务器；执行和服务访问**绕过服务器**直连沙箱；出站经 egress 过滤。这个分离来自 [[os:docs/architecture/index.md#L16|架构文档]]。Docker 模式没有 ingress，客户端直连映射端口。
:::

| 面 | 目录 |
|---|---|
| 客户端 | [[os:sdks/|sdks/]]、[[os:cli/|cli/]] |
| 协议 | [[os:specs/|specs/]] |
| 控制面 | [[os:server/|server/]] |
| 运行时 | [[os:server/opensandbox_server/services/|services/]]、[[os:kubernetes/|kubernetes/]] |
| 数据面 | [[os:components/execd/|components/execd]] |
| 网络与安全 | [[os:components/ingress/|ingress]]、[[os:components/egress/|egress]] |

## 协议：specs 是公共合约

[[os:specs/|specs/]] 下有四份 OpenAPI：[[os:specs/sandbox-lifecycle.yml|生命周期]]、[[os:specs/execd-api.yaml|execd]]、[[os:specs/egress-api.yaml|egress]]、[[os:specs/diagnostic-api.yml|诊断]]。

根 [[os:AGENTS.md#L45|AGENTS.md]] 的规则是：把 `specs/*` 当公共合约；用户可见的行为变化时，spec、实现、SDK、文档、示例要一起对齐，**先改文档**。扩展优先做向后兼容的增量。

这种设计的好处是可替换：任何实现了 execd API 的镜像都能配合 SDK 工作。[[os:docs/architecture/data-plane/execd.md#L8|execd 文档]] 称它是“参考”守护进程。

::: callout analogy 插座标准和电器
- **插座标准** = `specs/` 的 OpenAPI。
- **电器** = 五种语言的 SDK。
- **电网** = Docker、K8s、Fast Sandbox 后端。

电器不知道电从哪来，换电网不用换电器。所以有一条硬规则：不能只改生成物来修问题，要改标准本身。
:::

## 控制面：生命周期服务器

### 职责

一个 FastAPI 应用，是所有 SDK 共用的认证入口，只管生命周期。入口在 [[os:server/opensandbox_server/main.py|main.py]]，路由在 [[os:server/opensandbox_server/api/lifecycle.py|lifecycle.py]]。

### 两条分层规则

[[os:docs/architecture/control-plane/server.md#L25|server.md]] 写得很明确：

1. **API 层不直接接触运行时。** 一切经过服务接口。
2. **沙箱不是服务器数据库里的行。** Docker 容器、K8s 资源或 Fast Sandbox CR 才是事实来源。服务器只持久化自己管理的记录，比如快照元数据和模板目录。

第二条很关键：服务器本身几乎无状态，容器的 label 就是元数据。比如 `createdAt` 存在 `opensandbox.io/created-at` 这个 label 里。

| 层 | 目录 | 职责 |
|---|---|---|
| API | [[os:server/opensandbox_server/api/|api/]] | 薄路由 |
| 中间件 | [[os:server/opensandbox_server/middleware/|middleware/]] | 认证、请求 ID、指标 |
| 服务 | [[os:server/opensandbox_server/services/|services/]] | 状态机和运行时集成 |
| 仓储 | [[os:server/opensandbox_server/repositories/|repositories/]] | 快照元数据，SQLite 或 PostgreSQL |
| 租户 | [[os:server/opensandbox_server/tenants/|tenants/]] | 多租户身份，只用于 K8s |
| 集成 | [[os:server/opensandbox_server/integrations/|integrations/]] | 空闲暂停、访问续期、OTel |

### 生命周期是异步的

创建立即返回 `Pending`，客户端轮询或用 SDK 的就绪辅助函数。状态有 `Pending`、`Running`、`Pausing`、`Paused`、`Resuming`、`Stopping`、`Terminated`、`Failed`（[[os:docs/architecture/control-plane/server.md#L36|Asynchronous lifecycle]]）。

::: flow 沙箱状态
cfg cw=205 rh=96 nw=160
n p 0 0 "Pending" "创建已接受" k=warn
n r 1 0 "Running" "可执行" k=pass
n ps 2 0 "Pausing" "" k=teal
n pd 3 0 "Paused" "" k=vio
n f 0 1 "Failed" "非 0 或失败" k=todo
n st 1 1 "Stopping" "到期或删除" k=warn
n rs 3 1 "Resuming" "" k=teal
n t 1 2 "Terminated" "退出码 0" k=dark
e p -> r
e r -> ps
e ps -> pd
e pd -> rs
e rs:l -> r:r
e r -> st
e st -> t
e p -> f dash
cap: 每个沙箱有绝对过期时间，通过 `renew` 延长。K8s 的删除是异步的，`DELETE` 返回 204 后要轮询到它消失。
:::

### 启动时就拒绝不安全的配置

服务器遵循 fail-fast：非交互环境没有 API key 是致命错误，除非显式确认不安全（[[os:server/opensandbox_server/startup_guard.py|startup_guard.py]]）。

### 服务接口

[[os:server/opensandbox_server/services/sandbox_service.py@SandboxService|SandboxService]] 是抽象基类，[[os:server/opensandbox_server/services/factory.py@create_sandbox_service|create_sandbox_service]] 按配置选实现。

## 运行时后端

| 后端 | 实现 | 选用 | 特点 |
|---|---|---|---|
| Docker | [[os:server/opensandbox_server/services/docker/docker_service.py@DockerSandboxService|DockerSandboxService]] | `runtime.type = "docker"` | 本地和单机 |
| Kubernetes | [[os:server/opensandbox_server/services/k8s/kubernetes_service.py|KubernetesSandboxService]] | `"kubernetes"` | BatchSandbox 或 agent-sandbox |
| Fast Sandbox | [[os:server/opensandbox_server/services/fast_sandbox/|FastSandboxService]] | 同样是 `kubernetes`，请求带 `templateId` | 模板化 Firecracker |

Fast Sandbox **不是**单独的 `runtime.type`。[[os:server/opensandbox_server/services/composite_service.py|CompositeSandboxService]] 在 K8s 下按请求路由：带 `templateId` 的走 Fast Sandbox，ID 前缀 `fsb-` 决定后续操作归谁（[[os:docs/architecture/fast-sandbox/index.md#L12|How it fits]]）。不用新增公共 API，SDK 不用改。

### Docker 怎么把 execd 放进任意镜像

用户给的是任意镜像，比如 `alpine`，里面没有 execd。Docker 后端的做法是：

1. 从 execd 镜像取出二进制归档，按平台缓存在内存（[[os:server/opensandbox_server/services/docker/runtime.py|runtime.py]]）。
2. 创建容器，入口改成 bootstrap 脚本，原命令作为它的参数（[[os:server/opensandbox_server/services/docker/container_ops.py#L485|container_ops.py]]）。
3. 把归档复制进容器，再启动（[[os:server/opensandbox_server/services/docker/container_ops.py#L535|_prepare_sandbox_runtime]]）。

::: flow 把 execd 注入任意镜像
cfg cw=215 rh=92 nw=175
n i 0 0 "execd 镜像" "二进制归档" k=dark
n c 1 0 "内存缓存" "按平台" k=teal
n k 2 0 "创建容器" "入口换成 bootstrap" k=teal
n p 3 0 "复制归档" "容器启动前" k=warn
n s 3 1 "bootstrap" "先起 execd" k=pass
n u 2 1 "用户命令" "原 entrypoint" k=pass
e i -> c
e c -> k
e k -> p
e p -> s
e s -> u
cap: 这样 `alpine` 这种没有准备过的镜像也能用同一套 execd API。`execd_run_as_init` 配置可以让 execd 作为 init 运行（[[os:server/opensandbox_server/services/docker/container_ops.py#L317|代码]]）。
:::

Kubernetes 用 init container 做同样的事（[[os:server/opensandbox_server/services/k8s/batchsandbox_provider.py#L192|batchsandbox_provider.py]]）。Fast Sandbox 把 execd 烤进模板。

### 隔离档位

[[os:server/opensandbox_server/config.py#L1269|SecureRuntimeConfig]] 支持 `gvisor`、`kata`、`firecracker`。Firecracker 通过 `kata-fc` 只能用于 Kubernetes。默认是普通容器，也就是和宿主共享内核。**处理不可信代码时，一定要配置 secure runtime。**

### Fast Sandbox

::: flow Fast Sandbox 的关键组件
cfg cw=215 rh=98 nw=175
n s 0 0 "server / ingress" "gRPC 调用" k=acc
n fp 1 0 "FastPath" "内存放置 · 常数时间" k=teal
n cr 2 0 "sandbox.fast.io CRD" "持久意图" k=vio
n rc 3 0 "Reconciler" "收敛到现实" k=vio
n fl 2 1 "Fastlet" "预热 pod · 多沙箱" k=pass
n ag 3 1 "runtime agent" "管 Firecracker VM" k=pass
n dt 1 1 "DART" "P2P 块分发" k=dark
n tb 0 1 "模板构建" "OCI → 黄金镜像" k=dark
e s -> fp
e fp -> cr
e cr -> rc
e rc:b -> ag:t
e ag:l -> fl:r
e tb -> dt
e dt -> fl
cap: 组件表见 [[os:docs/architecture/fast-sandbox/index.md#L22|Core components]]。**FastPath、Fastlet 和 CRD 属于外部的 fast-sandbox 平台**，这个仓库只有适配器。
:::

快的关键是：工作负载在请求之前就固定了。模板把镜像预构建成黄金镜像，创建只是表达意图，不是配置。Fastlet 预置好网络槽位，放置在内存中完成，不走 K8s 调度器。

## 数据面：execd

### 职责

[[os:components/execd/|execd]] 是沙箱内的 Go 守护进程，提供命令、代码、PTY、文件、指标。SDK 的命令和文件操作都用这个 API。

| 区域 | 端点 | 说明 |
|---|---|---|
| 健康 | `GET /ping`、`/ready` | 不需要 token |
| 命令 | `/command` | 前台流式，或后台轮询 |
| 会话 | `/session` | 有状态 shell |
| 代码 | `/code`、`/code/contexts` | 基于 Jupyter |
| 终端 | `/pty` | WebSocket |
| 文件 | `/files`、`/directories` | 完整文件系统操作 |
| 隔离会话 | `/v1/isolated/*` | 私有命名空间 |
| 指标 | `/metrics` | 快照和流 |
| 代理 | `/proxy/{port}` | 通过 execd 访问任意端口 |

表格整理自 [[os:docs/architecture/data-plane/execd.md#L29|execd.md]]。配置了 token 时，请求要带 `X-EXECD-ACCESS-TOKEN`。

代码入口：路由到 [[os:components/execd/pkg/web/controller/command.go|command.go]] 的 `RunCommand`，执行在 [[os:components/execd/pkg/runtime/|runtime/]]。

`/proxy/{port}` 解决了一个单机问题：一个映射的宿主端口就能到达沙箱内所有端口（[[os:docs/architecture/network/single-host-network.md|Single-Host Network]]）。

::: callout warn 容易误解的地方
- **execd 和你的代码在同一个信任域**，隔离在沙箱这一层，不在 execd 这一层。
- **执行不经过服务器**。所以服务器日志里看不到命令，审计要在别处做。
- **`renew` 不会自动发生**。默认过期就没了，可以用 renew-on-access 集成。
:::

## 网络面

### 入站

Kubernetes 上，[[os:components/ingress/|ingress]] 是所有沙箱共用的反向代理，不需要逐沙箱配置。路由键是“沙箱 ID + 端口”：

| 模式 | 写法 |
|---|---|
| 头部（默认） | `OpenSandbox-Ingress-To: <id>-<port>`，或主机名 `id-8080.example.com` |
| URI | `/<id>/<port>/<path>` |
| 签名令牌 | 带过期和 MAC，不用查表就能验证 |

错误码是合约的一部分：`404` 是沙箱不存在，`503` 是存在但还没就绪（[[os:docs/architecture/network/ingress.md#L23|Error semantics]]）。客户端可以据此分支。

客户端不猜地址，`GET /sandboxes/{id}/endpoints/{port}` 返回可达地址（[[os:server/opensandbox_server/api/lifecycle.py@get_sandbox_endpoint|get_sandbox_endpoint]]）。

### 出站

[[os:components/egress/|egress]] 作为 sidecar 共享沙箱网络命名空间，所有出站包都经过它（[[os:docs/architecture/network/egress.md#L6|egress.md]]）。

::: flow 出站的两层
cfg cw=215 rh=98 nw=175
n a 0 0 "沙箱进程" "发起连接" k=dark
n d 1 0 "DNS 代理" "第 1 层" k=teal
n n 2 0 "nftables" "第 2 层 · dns+nft" k=teal
n m 3 0 "L7 代理" "可选 TLS 拦截" k=warn
n o 4 0 "外部" "允许的目标" k=dark
n v 3 1 "凭据保险库" "注入 header" k=pass
n x 1 1 "NXDOMAIN" "拒绝的域名" k=todo
e a -> d
e d -> n
e n -> m dash
e m -> o
e d:b -> x:t
e v:t -> m:b
cap: 规则优先级：平台 `deny.always` → 平台 `allow.always` → 用户策略，第一条匹配生效。启动失败就退出，不会以空操作运行。
:::

| 模式 | 强度 |
|---|---|
| `dns`（默认） | 只过滤解析。直接连 IP 可以绕过 |
| `dns+nft` | 包级默认拒绝，地址由 DNS 结果带租约喂入 |

默认的 DNS 模式挡不住直连 IP，这一点要清楚。需要强保证就用 `dns+nft`。端口规则目前只支持 TCP（[[os:docs/architecture/network/egress.md#L6|egress.md]]）。

### 凭据保险库

[[os:docs/guides/credential-vault.md|Credential Vault]] 让 agent 调外部服务而看不到真实凭据：真实凭据写进 egress sidecar，出站时注入。它要求 `dns+nft` 模式，因为 DNS 模式下直连 IP 能绕过策略。

::: callout analogy 公司前台的门卡
- **员工** = 沙箱里的 agent，不持有大楼门卡。
- **前台** = egress sidecar，门卡放在前台抽屉。
- **出门办事** = 出站请求：经过前台时，前台代为刷卡。
- **只能去名单上的地方** = 出站策略。

员工可以把工作证（假 token）给任何人看，但真门卡从未到过他手上。
:::

## Kubernetes 控制器

[[os:kubernetes/|kubernetes/]] 是一个独立的 Go operator，有三个 CRD：

| CRD | 作用 | 定义 |
|---|---|---|
| `BatchSandbox` | 一批沙箱 | [[os:kubernetes/apis/sandbox/v1alpha1/batchsandbox_types.go|batchsandbox_types.go]] |
| `Pool` | 预热资源池 | [[os:kubernetes/apis/sandbox/v1alpha1/pool_types.go|pool_types.go]] |
| `SandboxSnapshot` | 暂停和快照 | [[os:kubernetes/apis/sandbox/v1alpha1/sandboxsnapshot_types.go|sandboxsnapshot_types.go]] |

控制器在 [[os:kubernetes/internal/controller/|internal/controller]]：`batchsandbox_controller.go`、`pool_controller.go`、[[os:kubernetes/internal/controller/allocator.go|allocator.go]] 负责把池里的 pod 分给 BatchSandbox。

::: flow 资源池分配
cfg cw=225 rh=96 nw=180
n b 0 0 "BatchSandbox" "请求 N 个沙箱" k=acc
n a 1 0 "allocator" "分配 pod" k=teal
n e 2 0 "就绪端点" "写回 annotation" k=pass
n s 0 1 "server" "创建 CR · informer" k=acc
n p 1 1 "Pool" "维持预热 pod" k=vio
n t 2 1 "task-executor" "pod 内执行任务" k=vio
e b -> a
e a -> e
e s -> b
e p -> a
e a:r -> t:l dash
cap: server 用 informer 读状态（[[os:server/opensandbox_server/services/k8s/informer.py|informer.py]]）。CRD 是公共接口，改 `apis/` 要跑 `make manifests` 和 `make generate`（[[os:kubernetes/AGENTS.md#L29|kubernetes/AGENTS.md]]）。
:::

另一个可选后端是 `kubernetes-sigs/agent-sandbox`，实现在 [[os:server/opensandbox_server/services/k8s/agent_sandbox_provider.py|agent_sandbox_provider.py]]。

## SDK 与工具

| 工具 | 位置 |
|---|---|
| Python、JS/TS、Kotlin/Java、C#、Go SDK | [[os:sdks/sandbox/|sdks/sandbox]] |
| Code Interpreter SDK | [[os:sdks/code-interpreter/|sdks/code-interpreter]] |
| MCP | [[os:sdks/mcp/|sdks/mcp]] |
| `osb` CLI | [[os:cli/|cli/]] |
| 控制台 | [[os:console/|console/]] |
| Helm | [[os:manifests/charts/|manifests/charts]] |

生成的 OpenAPI 客户端和手写适配层并存：生成代码处理普通请求，手写层处理流式、传输、错误（[[os:docs/architecture/index.md#L46|Client surface]]）。**永远不要只改生成物**。

代码解释器镜像已经搬到 `opensandbox-group/sandbox-images`，不在这里。

## 端到端：从 create 到一条命令

```python
sandbox = await Sandbox.create("alpine")
execution = await sandbox.commands.run("echo 'Hello OpenSandbox!'")
await sandbox.destroy()
```

简化自 [[os:README.md#L80|README]]。

::: steps Docker 模式下的创建和执行
::: flow
cfg cw=190 rh=96 nw=152
n sd 0 0 "Sandbox.create" "Python SDK" k=acc
n ap 1 0 "POST /sandboxes" "lifecycle.py" k=teal
n sv 2 0 "DockerService" "create_sandbox" k=teal
n ct 3 0 "容器" "bootstrap + execd" k=pass
n w 4 0 "就绪检查" "ping execd" k=warn
n ep 4 1 "get_endpoint" "解析端口" k=vio
n run 3 1 "POST /command" "直连 execd" k=pass
n sh 2 1 "shell 进程" "流式输出" k=dark
n del 1 1 "destroy" "DELETE" k=teal
n gone 0 1 "容器移除" "" k=dark
e sd -> ap
e ap -> sv
e sv -> ct
e ct -> w
e w -> ep
e ep -> run
e run -> sh
e sh -> del
e del -> gone
:::
--- step SDK 发起 {hl=sd}
[[os:sdks/sandbox/python/src/opensandbox/sandbox.py@Sandbox|Sandbox.create]] 通过生成的客户端调用生命周期 API，默认 `ready_timeout` 是 30 秒。
--- step 路由 {hl=sd,ap}
[[os:server/opensandbox_server/api/lifecycle.py@create_sandbox|create_sandbox]] 校验扩展字段和网络策略，然后交给 `sandbox_service`。路由本身很薄。
--- step 服务 {hl=ap,sv}
[[os:server/opensandbox_server/services/docker/docker_service.py@DockerSandboxService.create_sandbox|create_sandbox]] 准备资源限制和 ID，调用 `_provision_sandbox`。
--- step 建容器 {hl=sv,ct}
`_create_and_start_container` 替换入口，复制 execd 归档，启动。容器的 label 记录元数据。
--- step 等就绪 {hl=ct,w}
SDK 解析端点并做健康检查，直到 execd 响应或超时。
--- step 取端点 {hl=w,ep}
[[os:sdks/sandbox/python/src/opensandbox/sandbox.py@Sandbox.get_endpoint|get_endpoint]] 通过生命周期 API 得到可达地址。
--- step 执行 {hl=ep,run}
`commands.run` 直接请求 execd 的 `/command`，这一步**不经过服务器**。
--- step 流式 {hl=run,sh}
execd 启动 shell，把输出流式返回。
--- step 销毁 {hl=sh,del}
`destroy` 调 `DELETE /sandboxes/{id}`。
--- step 清理 {hl=del,gone}
容器移除。没有显式删除时，到期也会回收。
:::

K8s 模式的差别：创建变成 CR，init container 放 execd，端点经 ingress。

## 开源许可证

| 部分 | 许可证 | 证据 |
|---|---|---|
| 全部 | Apache-2.0 | [[os:LICENSE|LICENSE]] |
| 项目治理 | LF Projects 技术章程 | [[os:CHARTER.md|CHARTER.md]]、[[os:GOVERNANCE.md|GOVERNANCE.md]] |
| 依赖 | 按各生态的清单 | 我没有逐个审计 |

| 场景 | 允许吗 | 要做什么 |
|---|---|---|
| 自用 | 允许 | 无 |
| 提 PR | 允许 | **需要 DCO**：`git commit -s`（[[os:CONTRIBUTING.md#L682|CONTRIBUTING]]） |
| 公开 fork | 允许 | 保留 LICENSE，商标按章程 |
| 发安装包 | 允许 | 保留声明 |
| 对外提供服务 | 允许 | Apache-2.0 无网络条款 |

历史：GitHub API 显示 `LICENSE` 有两条记录：2025-11-13 和 2025-12-17，第一条标题就是添加 Apache 2.0。包名里还保留 `alibaba`（[[os:README.md#L167|README]]），Go 模块路径也是，说明项目最初来自阿里巴巴，这是根据命名的推断。

::: callout info 不是法律意见
这是工程师读代码时的理解。
:::

## 贡献与定制

### 开发环境

```bash
cd server
uv sync --all-groups
cp opensandbox_server/examples/example.config.toml ~/.sandbox.toml
uv run python -m opensandbox_server.main
uv run pytest tests/test_docker_service.py
```

来自 [[os:CONTRIBUTING.md#L71|Quick Setup]] 和 [[os:server/AGENTS.md#L28|server/AGENTS.md]]。

### 项目规则

- 根 AGENTS.md 是路由器，先读最近目录的 `AGENTS.md`。
- 公共 API、SDK、CLI、生命周期语义、运行时隔离、网络的改动，走 [[os:oseps/|OSEP]] 提案（[[os:GOVERNANCE.md#L18|Governance]]）。
- 路由保持薄，逻辑放服务层。
- Docker 能跑不代表 K8s 能跑。
- 不要只改生成物。
- 破坏性变更要先问。
- 提交带 DCO。

### 适合入手的方向

- 文档和示例：[[os:docs/examples/|docs/examples]]。
- SDK 能力对齐：五种语言容易有差异。
- 诊断 API 和错误信息。
- [[os:ROADMAP.md|ROADMAP]] 上的条目。

需要避开：没有 OSEP 就改公共合约。

### 定制路线

::: flow 想让它适配我的环境
cfg cw=240 rh=90 nw=195
n q 0 1 "要改什么？" "从配置开始" k=dark
n a 1 0 "配置" "secure runtime · egress" k=pass
n b 1 1 "自定义镜像" "实现 execd 或用参考版" k=teal
n c 1 2 "新运行时" "实现 SandboxService" k=vio
n d 2 2 "写 OSEP" "公共合约变更" k=warn
e q:r -> a:l
e q -> b
e q:r -> c:l
e c -> d dash
cap: 配置示例在 [[os:server/opensandbox_server/examples/example.config.toml|example.config.toml]]。
:::

### 仓库里没有的东西

- **fast-sandbox 平台**：FastPath、Fastlet、CRD 在 [fast-sandbox](https://github.com/opensandbox-group/fast-sandbox)。
- **官方沙箱镜像**：在 `sandbox-images`。
- **DART**：外部的块分发项目。
- **Firecracker、gVisor、Kata**：要自己准备。

## 附录：源码索引

| 主题 | 链接 |
|---|---|
| 路由表 | [[os:AGENTS.md|AGENTS.md]] |
| 架构总览 | [[os:docs/architecture/index.md|index.md]] |
| 合约 | [[os:specs/README.md|specs/README.md]] |
| 服务器入口 | [[os:server/opensandbox_server/main.py|main.py]] |
| 配置 | [[os:server/opensandbox_server/config.py|config.py]] |
| 生命周期 API | [[os:server/opensandbox_server/api/lifecycle.py|lifecycle.py]] |
| Docker | [[os:server/opensandbox_server/services/docker/docker_service.py|docker_service.py]] |
| K8s | [[os:server/opensandbox_server/services/k8s/kubernetes_service.py|kubernetes_service.py]] |
| execd | [[os:components/execd/main.go|main.go]] |
| egress | [[os:components/egress/main.go|main.go]] |
| ingress | [[os:components/ingress/main.go|main.go]] |
| 控制器 | [[os:kubernetes/internal/controller/batchsandbox_controller.go|batchsandbox_controller.go]] |
| Python SDK | [[os:sdks/sandbox/python/src/opensandbox/sandbox.py|sandbox.py]] |
| Fast Sandbox | [[os:docs/architecture/fast-sandbox/index.md|index.md]] |
| 治理 | [[os:GOVERNANCE.md|GOVERNANCE.md]]、[[os:CHARTER.md|CHARTER.md]] |
