## 怎么读这份导读

这份导读写给有网络或系统背景、想读懂或定制 Tailscale 客户端的工程师。读完你应该能说清：`tailscaled` 由哪些子系统组成；节点怎么拿到网络图；两台机器在 NAT 后面怎么连上；一个包怎么走完全程；新功能该放哪。

全文顺序：

1. **全景**：问题、目标、限制，开源和闭源的边界。
2. **启动与结构**：`tailscaled`、`tsd.System`、LocalBackend。
3. **控制面**：登录、Noise、MapResponse。
4. **数据面**：`wgengine`、TUN、过滤器。
5. **magicsock**：打洞、路径选择。
6. **DERP**：最后的退路。
7. **周边**：DNS、netstack、tsnet、Tailnet Lock。
8. **功能模块化**：`feature/` 的方向。
9. **端到端**：一个 ping 的旅程。
10. **许可证**、**贡献与定制**、**源码索引**。

::: callout key 固定版本
`tailscale/tailscale` @ `e906e69`（2026-11-05，版本 [[ts:VERSION.txt|1.105.0]]）。我没有运行过这份代码，内容来自阅读源码和注释。
:::

## 全景：问题、目标、限制

### 它解决什么

WireGuard 本身是一个优秀的数据面，但用它组网要自己解决：密钥分发、地址分配、NAT 穿透、访问控制。

| 环节 | 手动 WireGuard 的问题 | Tailscale 的回答 |
|---|---|---|
| **密钥** | 每对节点手工交换公钥 | 协调服务器分发，私钥不离开设备 |
| **地址** | 手工规划 | 控制面分配 tailnet IP |
| **NAT** | 两端都在 NAT 后就连不上 | magicsock 打洞 + DERP 中继 |
| **身份** | 只有公钥 | 接入身份提供商 |
| **访问控制** | 自己写防火墙 | 控制面下发策略，每个节点本地过滤 |

核心结构是**控制面和数据面分离**：协调服务器只交换元数据，用户流量点对点走，WireGuard 端到端加密。

### 要求 / 目标 / 限制

| 项目 | 内容 |
|---|---|
| **核心目标** | 让私有网络“装上就能用” |
| **功能要求** | 任意 NAT 下连通；路由、出口节点、MagicDNS、SSH、Serve/Funnel |
| **规模压力** | 大 tailnet 的 netmap 很大；小设备内存紧 |
| **外部限制** | 跨 Linux、macOS、Windows、BSD、iOS、Android；中间网络可能屏蔽 UDP |
| **关键手段** | 用户态 WireGuard、magicsock、DERP、增量 netmap、模块化构建 |
| **许可证** | BSD-3-Clause + 专利授权 |

### 总图

::: flow 客户端和周边
cfg cw=205 rh=100 nw=165
n cli 0 0 "tailscale CLI" "LocalAPI 客户端" k=acc
n d 1 0 "tailscaled" "LocalBackend" k=teal
n ctl 2 0 "协调服务器" "闭源 · SaaS" k=dark
n idp 3 0 "身份提供商" "OIDC 等" k=dark
n eng 1 1 "wgengine" "WireGuard + 路由" k=pass
n ms 2 1 "magicsock" "选路" k=pass
n peer 4 1 "对端节点" "直连 UDP" k=vio
n derp 3 2 "DERP 中继" "TCP/HTTPS" k=warn
n tun 0 1 "TUN / netstack" "操作系统" k=dark
e cli -> d
e d -> ctl
e ctl -> idp dash
e d -> eng
e tun -> eng
e eng -> ms
e ms -> peer
e ms:b -> derp:l dash
e derp:r -> peer:b dash
cap: 深色不在这个仓库。协调服务器闭源；开源替代 Headscale 是第三方项目。DERP 服务器代码是开源的（[[ts:cmd/derper/|cmd/derper]]）。
:::

### 开源的边界

[[ts:README.md#L9|README]] 说得很清楚：

| 部分 | 状态 |
|---|---|
| `tailscaled`、`tailscale` CLI、DERP | 在这个仓库，开源 |
| iOS、Android 使用的核心逻辑 | 在这个仓库 |
| Android 应用 | 单独的开源仓库 |
| macOS、iOS、Windows 的 GUI 外壳 | 闭源 |
| 协调服务器 | 闭源 |
| WireGuard 实现 | 用 Tailscale 的 fork：[[ts:go.mod#L129|wireguard-go]] |

## 启动与结构

### tailscaled

入口是 [[ts:cmd/tailscaled/tailscaled.go@main|main]]，主要流程在 [[ts:cmd/tailscaled/tailscaled.go@run|run]] 和 [[ts:cmd/tailscaled/tailscaled.go@getLocalBackend|getLocalBackend]]。

[[ts:cmd/tailscaled/tailscaled.go@tryEngine|tryEngine]] 决定数据面的形态：

| 模式 | 条件 | 行为 |
|---|---|---|
| 内核 TUN | 默认 | 创建 tun、router、OS DNS 配置 |
| `userspace-networking` | `--tun=userspace-networking` | 不需要特权，用 netstack |
| TAP | `tap:` 前缀 | 二层设备 |

### tsd.System

子系统靠 [[ts:tsd/tsd.go|tsd.System]] 组装。包注释说明目标是统一 tailscaled、Windows 服务、macOS GUI、tsnet、wasm 的初始化，并提醒：这个包依赖几乎所有东西，所以不要把它传给不想依赖全世界的包。

### LocalBackend

[[ts:ipn/ipnlocal/local.go|ipn/ipnlocal/local.go]] 有 9500 行，是客户端的大脑：连接控制客户端和 wgengine，维护状态机和用户偏好。所有东西都经过它，这也是 `feature/` 要解决的问题。

::: flow CLI 怎么控制 daemon
cfg cw=215 rh=92 nw=175
n c 0 0 "tailscale up" "cmd/tailscale" k=acc
n l 1 0 "client/local" "LocalAPI 客户端" k=teal
n s 2 0 "ipnserver" "safesocket" k=teal
n h 3 0 "localapi" "HTTP 处理器" k=teal
n b 3 1 "LocalBackend" "状态机" k=pass
n cc 2 1 "控制客户端" "登录 · 轮询" k=vio
n e 1 1 "wgengine" "Reconfig" k=vio
e c -> l
e l -> s
e s -> h
e h -> b
e b -> cc
e cc -> e dash
cap: 本地 API 走 unix socket 或 Windows 命名管道（[[ts:safesocket/|safesocket]]），入口在 [[ts:ipn/ipnserver/server.go@Server.serveHTTP|serveHTTP]]。客户端库见 [[ts:client/local/local.go|local.go]]。
:::

### 状态机

[[ts:ipn/ipnlocal/local.go@LocalBackend.nextStateLocked|nextStateLocked]] 和 [[ts:ipn/ipnlocal/local.go@LocalBackend.enterStateLocked|enterStateLocked]] 管理 `NoState`、`InUseOtherUser`、`NeedsLogin`、`NeedsMachineAuth`、`Stopped`、`Starting`、`Running`。状态定义在 [[ts:ipn/backend.go|ipn/backend.go]]。

收到新 netmap 时走 [[ts:ipn/ipnlocal/local.go@LocalBackend.setNetMapLocked|setNetMapLocked]]，然后 [[ts:ipn/ipnlocal/local.go@LocalBackend.authReconfig|authReconfig]] 把它变成 wgengine 的配置。

::: callout warn 容易误解的地方
- 代码里的 `ipn` 没有明确的全称，[[ts:ipn/doc.go|包注释]]自己都在开玩笑：“Identified Private Network? IP Network?”
- 大量 `*View` 类型是生成的只读视图（[[ts:cmd/viewer/|cmd/viewer]]），`tailcfg_view.go` 不要手改。
:::

## 控制面

### 职责

[[ts:control/controlclient/direct.go|direct.go]] 实现和协调服务器的通信，[[ts:control/controlclient/auto.go|auto.go]] 负责自动重试和状态。

### Noise

控制连接不依赖 TLS 的信任链。[[ts:control/ts2021/|ts2021]] 是基于 Noise 的协议，建立在 [[ts:control/controlbase/|controlbase]] 之上，拨号在 [[ts:control/controlhttp/|controlhttp]]，并且能穿过 HTTP 代理和只开 443 的环境。

### 两个核心请求

| 请求 | 类型 | 作用 |
|---|---|---|
| 登录 | [[ts:tailcfg/tailcfg.go@RegisterRequest|RegisterRequest]] | [[ts:control/controlclient/direct.go@Direct.TryLogin|TryLogin]] 注册节点公钥 |
| 取网络图 | [[ts:tailcfg/tailcfg.go@MapRequest|MapRequest]] / [[ts:tailcfg/tailcfg.go@MapResponse|MapResponse]] | [[ts:control/controlclient/direct.go@Direct.PollNetMap|PollNetMap]] 长轮询 |

[[ts:tailcfg/tailcfg.go|tailcfg]] 定义了线上协议。[[ts:tailcfg/tailcfg.go@CapabilityVersion|CapabilityVersion]] 表示客户端能力，当前是 [[ts:tailcfg/tailcfg.go@CurrentCapabilityVersion|150]]。新旧客户端靠它协商，所以改协议时要加版本。

::: steps 节点上线
::: flow
cfg cw=200 rh=92 nw=165
n a 0 0 "生成密钥" "机器 + 节点 + disco" k=acc
n b 1 0 "Noise 连接" "ts2021" k=teal
n c 2 0 "RegisterRequest" "可能要 auth URL" k=teal
n d 3 0 "MapRequest" "streaming" k=teal
n e 4 0 "MapResponse" "peers · DERP · 策略" k=pass
n f 4 1 "LocalBackend" "setNetMap" k=vio
n g 3 1 "wgengine" "Reconfig" k=vio
e a -> b
e b -> c
e c -> d
e d -> e
e e -> f
e f -> g
:::
--- step 密钥 {hl=a}
节点有几把钥匙：机器密钥、节点（WireGuard）密钥、disco 密钥。私钥不离开设备，由 [[ts:types/key/|types/key]] 表示。
--- step 连接 {hl=a,b}
用 Noise 连到协调服务器，用它自己的公钥验证。
--- step 登录 {hl=b,c}
未授权时服务器返回 URL，用户在浏览器走身份提供商。
--- step 轮询 {hl=c,d}
[[ts:control/controlclient/direct.go@Direct.sendMapRequest|sendMapRequest]] 发起 streaming 请求，带上自己的 endpoint 和 Hostinfo。
--- step 接收 {hl=d,e}
`MapResponse` 带来 peer 列表、DERP 地图、包过滤规则、DNS 配置。后续是增量更新，由 [[ts:control/controlclient/map.go|map.go]] 合并。
--- step 进入后端 {hl=e,f}
netmap 交给 `LocalBackend`。
--- step 配置数据面 {hl=f,g}
`authReconfig` 生成配置调用 `Reconfig`。
:::

### 协调服务器不看你的流量

::: callout analogy 电话簿，不是电话局
- **协调服务器** = 电话簿：记着每个人的公钥、地址、能打给谁。
- **MapResponse** = 定期寄来的新版电话簿。
- **WireGuard 隧道** = 两个人之间直接通话，电话簿公司不在线路上。
- **策略** = 电话簿上写着“不接 B 的电话”，而挂断动作发生在你自己家里（本地过滤器）。

所以协调服务器短暂宕机时，已有的连接继续工作，受影响的是新变化的分发。这一点是从架构推断的。
:::

它只有公钥、地址、策略，没有私钥。Tailnet Lock 更进一步：节点要有本地信任签名才被接受，防止控制面被攻破时塞入恶意节点，实现在 [[ts:tka/tka.go|tka]]。

## 数据面：wgengine

### 职责

[[ts:wgengine/userspace.go@NewUserspaceEngine|NewUserspaceEngine]] 把这些东西组装到一起：TUN 包装、WireGuard 设备、magicsock、路由、DNS、过滤器。

| 组件 | 作用 | 位置 |
|---|---|---|
| `tstun.Wrapper` | 包装 TUN，在这里过滤和拦截 | [[ts:net/tstun/wrap.go|wrap.go]] |
| WireGuard | 加解密 | `wireguard-go` fork |
| `magicsock.Conn` | WireGuard 的 `conn.Bind`，负责选路 | [[ts:wgengine/magicsock/magicsock.go@Conn|Conn]] |
| `router` | 配置系统路由 | [[ts:wgengine/router/router.go|router.go]] |
| `dns.Manager` | 配置系统 DNS | [[ts:net/dns/manager.go|manager.go]] |
| `filter.Filter` | 有状态包过滤 | [[ts:wgengine/filter/filter.go@Filter|Filter]] |

[[ts:wgengine/userspace.go@userspaceEngine.Reconfig|Reconfig]] 的特点是比较差异：`engineChanged`、`routerChanged`、`dnsChanged`、`listenPortChanged`，没变化就提前返回。网络图频繁变化时这很重要。

关键巧思在 WireGuard 和 magicsock 的接口：**WireGuard 以为自己在往一个地址发包，实际地址是公钥**。magicsock 实现 `conn.Bind`，决定这个包这次走哪条路。WireGuard 完全不知道换路发生过，会话也不用重建。

[[ts:wgengine/filter/filter.go|过滤器]]在每个节点本地执行控制面下发的规则，**入站方向**决定是否接受。

## magicsock

这是项目最有特色的部分。包注释只有一句：“一个能在使用中改变通信路径的 socket，主动寻找最好的通信方式”（[[ts:wgengine/magicsock/magicsock.go#L4|magicsock.go]]）。

### 候选地址

[[ts:wgengine/magicsock/magicsock.go@Conn.determineEndpoints|determineEndpoints]] 收集自己的候选：本地接口地址、STUN 发现的公网地址、端口映射（UPnP、NAT-PMP、PCP，[[ts:net/portmapper/|portmapper]]）。[[ts:net/netcheck/netcheck.go|netcheck]] 探测网络情况，包括 UDP 是否可用、NAT 类型、各 DERP 区延迟。

这些候选通过 [[ts:wgengine/magicsock/magicsock.go@Conn.updateEndpoints|updateEndpoints]] 报给控制面，再分发给 peer。

### disco：路径探测

[[ts:disco/disco.go|disco]] 是独立于 WireGuard 的小协议：头部是 magic `TS💬`、发送方 disco 公钥、nonce，内容用 nacl box 加密。消息类型有 Ping、Pong、CallMeMaybe。

为什么不直接用 WireGuard 探测？disco 用单独的密钥，可以在不建立 WireGuard 会话时探路。

::: steps 两台 NAT 后的机器打洞
::: flow
cfg cw=210 rh=96 nw=170
n a 0 0 "节点 A" "有包要发" k=acc
n d 1 0 "DERP" "已有的连接" k=warn
n b 2 0 "节点 B" "公网候选已知" k=acc
n p 1 1 "UDP 打洞" "同时互发 Ping" k=teal
n k 1 2 "bestAddr" "最优直连路径" k=pass
e a -> d
e d -> b
e a:b -> p:l
e b:b -> p:r
e p -> k
:::
--- step 先走中继 {hl=a,d,b}
A 的第一个包可以经 DERP 发给 B，所以连接立即可用。
--- step 探测 {hl=a,p}
同时，[[ts:wgengine/magicsock/endpoint.go@endpoint.sendDiscoPingsLocked|sendDiscoPingsLocked]] 向 B 的所有候选地址发 disco Ping。A 自己的 NAT 因此打开映射。
--- step CallMeMaybe {hl=d,b}
A 经 DERP 发 CallMeMaybe 给 B，内容是 A 的候选地址。B 收到后反过来向 A 发 Ping，打开自己的 NAT。这是打洞的关键：**两边要差不多同时发**。
--- step 收到 Pong {hl=p,b}
一条路通了，[[ts:wgengine/magicsock/endpoint.go@endpoint.handlePongConnLocked|handlePongConnLocked]] 记录延迟。
--- step 换路 {hl=p,k}
最优路径写入 `bestAddr`。之后的包直接走 UDP，WireGuard 无感知。
:::

### 发送时选路

[[ts:wgengine/magicsock/endpoint.go@endpoint.addrForSendLocked|addrForSendLocked]] 很短，逻辑却是全部精华：

| 情况 | 行为 |
|---|---|
| `bestAddr` 有效且未过期 | 只走 UDP |
| wireguard-only 节点 | 只有 UDP，没有 DERP |
| 过期或没有 | UDP 和 DERP **同时发** |

[[ts:wgengine/magicsock/endpoint.go@endpoint.send|send]] 在没有直连或信任过期时触发新一轮探测。过期时双发，保证切换期间不断流。[[ts:wgengine/magicsock/endpoint.go@endpoint.heartbeat|heartbeat]] 在活跃时周期性刷新路径。

最后还有一招：如果不知道对方的 UDP 地址和 DERP home，就用对方上次联系我们的 DERP 区（`fallbackDERPRegionForPeer`）。

::: callout analogy 同时打电话和发快递
- **WireGuard** = 寄件人，只写收件人姓名（公钥），不管地址。
- **magicsock** = 物流调度，决定走哪条路。
- **DERP** = 公司总部的中转站：慢但一定到。
- **disco Ping** = 派人试几条小路通不通。
- **CallMeMaybe** = 经总部捎个话：“我也在试，咱俩同时走，门就开了。”
- **路径过期时双发** = 同时走小路和中转站，避免换路时丢件。

寄件人从头到尾不知道包裹换过路。
:::

### 换网络

切换 Wi-Fi 时，[[ts:wgengine/userspace.go@userspaceEngine.linkChange|linkChange]] 响应 [[ts:net/netmon/|netmon]] 的事件，[[ts:wgengine/magicsock/magicsock.go@Conn.ReSTUN|ReSTUN]] 重新探测。WireGuard 会话以公钥为身份，不绑定 IP，所以 TCP 连接能活过网络切换。

较新的 peer relay 用另一个节点做 UDP 中继，介于直连和 DERP 之间，见 [[ts:wgengine/magicsock/relaymanager.go|relaymanager.go]] 和 [[ts:feature/relayserver/|feature/relayserver]]。

## DERP

### 职责

DERP 按 **curve25519 公钥寻址**，不是 IP。它中继两种包：disco 消息，以及 NAT 穿透失败时的 WireGuard 包（[[ts:derp/README.md|derp/README.md]]）。它是最后的退路，也是打洞的旁路信令。

DERP 走 TCP/HTTPS，所以 UDP 被屏蔽的网络里也能工作。

它看不到明文：中继的 WireGuard 包是端到端加密的。

### 区域和家

::: flow DERP 区域
cfg cw=225 rh=96 nw=180
n a 0 0 "节点 A" "home: 区域 1" k=acc
n r1 1 0 "区域 1 节点 a" "mesh" k=warn
n r2 1 1 "区域 1 节点 b" "mesh" k=warn
n o 1 2 "区域 2" "按需连接" k=warn
n b 2 2 "节点 B" "home: 区域 2" k=acc
e a -> r1
e r1 -> r2 dash
e a:b -> o:l dash
e o -> b
cap: 每个节点选延迟最低的区作为 home，由协调服务器分享给所有 peer。发给 B 就连到 B 的 home。只有 home 连接要长期保持（[[ts:derp/README.md#L19|DERP Map]]）。
:::

设计取舍来自 README：

- 客户端自己按延迟选 home，所以不需要云负载均衡或 anycast，也不需要区域间路由。
- 同区节点 mesh，包只转发一跳。**区域之间没有路由**。
- 每区至少三个节点，不是为了选举，而是防止级联故障：两台各 51% 负载时，挂一台另一台也撑不住。

服务器在 [[ts:cmd/derper/|cmd/derper]] 和 [[ts:derp/derpserver/derpserver.go@Server|derpserver]]，还有 [[ts:derp/xdp/|xdp]] 目录下的 XDP 实现。[[ts:cmd/stund/|stund]] 提供 STUN。

## 周边

| 功能 | 作用 | 位置 |
|---|---|---|
| MagicDNS | tailnet 内域名，配置系统 DNS | [[ts:net/dns/|net/dns]] |
| netstack | 用户态 TCP/IP，基于 gVisor | [[ts:wgengine/netstack/|wgengine/netstack]] |
| tsnet | 把 Tailscale 节点嵌进 Go 程序 | [[ts:tsnet/tsnet.go|tsnet]] |
| Tailscale SSH | 用 tailnet 身份认证 SSH | [[ts:ssh/|ssh]]、[[ts:feature/ssh/|feature/ssh]] |
| Tailnet Lock | 本地节点签名 | [[ts:tka/|tka]] |
| K8s operator | 集群集成 | [[ts:k8s-operator/|k8s-operator]]、[[ts:cmd/k8s-operator/|cmd]] |
| Taildrive | 文件共享 | [[ts:drive/|drive]] |

DNS 的难点是平台差异：[[ts:net/dns/|net/dns]] 有 resolvconf、systemd-resolved、NetworkManager、Windows NRPT 等一堆实现，这里的文件大多是在和操作系统打交道。

**tsnet** 是最值得定制者关注的：一个 Go 程序不需要 TUN，不需要 daemon，自己就是 tailnet 上的节点。

::: flow 三种接入方式
cfg cw=240 rh=92 nw=195
n a 0 0 "tailscaled + TUN" "全机接入" k=pass
n b 0 1 "userspace-networking" "无特权 · SOCKS" k=teal
n c 0 2 "tsnet" "嵌入单个程序" k=vio
n n 1 1 "同一套核心" "LocalBackend · magicsock" k=acc
e a:r -> n:l
e b -> n
e c:r -> n:l
cap: 三种方式共用核心逻辑，差别在包从哪进出。
:::

## 功能模块化

[[ts:feature/README.md|feature/README.md]] 讲了项目当前的重要方向：

客户端变大了，不是每个用户都要全部功能。一台几美元芯片的 IoT 设备不需要 Taildrop、WebDAV、ACME 或 SSH。所以**新功能优先放 `feature/<name>`**，通过 hook 接入，不要塞进 `LocalBackend`。

| 机制 | 说明 |
|---|---|
| `feature/` 包 | 从自己的 `init` 注册 |
| `ts_omit_*` 构建标签 | 去掉功能 |
| [[ts:feature/buildfeatures/|buildfeatures]] | 编译期常量，如 `HasDebug`、`HasNetstack` |
| [[ts:cmd/featuretags/|cmd/featuretags]] | 查看功能依赖 |
| [[ts:cmd/omitsize/|cmd/omitsize]] | 算每个功能的体积 |

所以 `tailscaled.go` 里能看到 `hookNewNetstack.GetOk()` 这样的钩子。README 也承认大部分代码处于**半迁移状态**。贡献时要先读这份文档。

## 端到端：一个 ping

机器 A 上执行 `ping 100.64.0.2`，目标是 B。

::: steps 一个 ICMP 包从 A 到 B
::: flow
cfg cw=190 rh=96 nw=152
n a 0 0 "A 的内核" "路由到 tailscale0" k=dark
n t 1 0 "tstun.Wrapper" "读 TUN" k=teal
n f 2 0 "出站过滤" "本地处理" k=warn
n w 3 0 "WireGuard" "按目的 IP 找 peer" k=pass
n m 4 0 "magicsock" "endpoint.send" k=pass
n n 4 1 "网络" "UDP 或 DERP" k=dark
n r 3 1 "receiveIP" "B 的 magicsock" k=pass
n dw 2 1 "WireGuard" "解密" k=pass
n fb 1 1 "入站过滤" "策略规则" k=warn
n b 0 1 "B 的内核" "ICMP 回复" k=dark
e a -> t
e t -> f
e f -> w
e w -> m
e m -> n
e n -> r
e r -> dw
e dw -> fb
e fb -> b
:::
--- step 进入 TUN {hl=a,t}
系统路由把 `100.64.0.0/10` 指向 `tailscale0`，路由由 `router` 配置。`tstun.Wrapper` 读到这个包。
--- step 出站处理 {hl=t,f}
[[ts:wgengine/userspace.go@userspaceEngine.handleLocalPackets|handleLocalPackets]] 等钩子可以在这里处理或拦截。
--- step 加密 {hl=f,w}
WireGuard 按目的 IP 查 peer，加密。如果还没握手就先握手。
--- step 选路 {hl=w,m}
[[ts:wgengine/magicsock/magicsock.go@Conn.Send|Conn.Send]] 调用 `endpoint.send`，`addrForSendLocked` 决定路径。
--- step 传输 {hl=m,n}
UDP 直发，或经 DERP，或同时。
--- step 接收 {hl=n,r}
B 的 [[ts:wgengine/magicsock/magicsock.go@Conn.receiveIP|receiveIP]] 区分 disco 和 WireGuard 包。
--- step 解密 {hl=r,dw}
WireGuard 解密并验证公钥和 AllowedIPs。
--- step 入站过滤 {hl=dw,fb}
过滤器用控制面下发的规则判断，不允许就丢弃。
--- step 投递 {hl=fb,b}
写回 TUN，B 的内核回 ICMP，反向走一遍。
:::

## 开源许可证

| 部分 | 许可证 | 证据 |
|---|---|---|
| 仓库代码 | BSD-3-Clause，Copyright 2020 Tailscale Inc | [[ts:LICENSE|LICENSE]] |
| 专利 | 额外的专利授权，类似 Go | [[ts:PATENTS|PATENTS]] |
| 依赖 | 各自许可证 | [[ts:licenses/|licenses/]] |
| `tempfork/` | 临时 fork 的第三方代码，各有许可 | [[ts:tempfork/|tempfork]] |
| WireGuard 商标 | Jason A. Donenfeld 的注册商标 | [[ts:README.md#L85|README]] |

| 场景 | 允许吗 | 要做什么 |
|---|---|---|
| 自用 | 允许 | 无 |
| 提 PR | 允许 | **需要 DCO**：`Signed-off-by` |
| 公开 fork | 允许 | 保留版权；不要用 Tailscale 名义 |
| 发安装包 | 允许 | 用 `build_dist.sh` 带上版本信息 |
| 闭源嵌入 | 允许 | 保留声明，注意专利条款的终止条件 |
| 对外提供服务 | 允许 | BSD 无网络条款，但协调服务器闭源，要用第三方实现或自己写 |

历史：GitHub API 显示 `LICENSE` 自 2020-02-05 进入这个仓库，之后三次修改的标题都是格式或版权声明调整，我没有逐条 diff。

::: callout info 不是法律意见
这是工程师读代码时的理解。商标使用请看 Tailscale 自己的政策。
:::

## 贡献与定制

### 开发环境

```bash
git clone https://github.com/tailscale/tailscale
cd tailscale
go install tailscale.com/cmd/tailscale{,d}
go test ./wgengine/magicsock/
make check
```

要求最新的 Go（[[ts:README.md#L40|README]]）。

### 项目规则

- 提交要有 DCO `Signed-off-by`，并引用 bug。
- 提交信息用 Go 和 Linux 风格：`wgengine/magicsock: fix foo`，**不用** Conventional Commits（[[ts:docs/commit-messages.md|commit-messages.md]]）。
- 新功能优先放 `feature/`。
- 协议变更要考虑 `CapabilityVersion`。
- 生成文件用 [[ts:cmd/viewer/|viewer]]、[[ts:cmd/cloner/|cloner]]。
- 改依赖关系要过 `depaware` 检查（[[ts:Makefile#L73|make check]]）。

### 适合入手的方向

- 平台相关的 bug：DNS、路由。
- 把 `LocalBackend` 里的功能拆到 `feature/`。
- 测试：[[ts:tstest/|tstest]] 里有虚拟网络 [[ts:tstest/natlab/|natlab]]，可以模拟各种 NAT。
- 文档和诊断。

### 定制路线

::: flow 想基于 Tailscale 做点什么
cfg cw=240 rh=90 nw=195
n q 0 1.5 "要什么？" "从最轻的开始" k=dark
n a 1 0 "CLI + 策略" "用 SaaS" k=pass
n b 1 1 "LocalAPI" "控制本地 daemon" k=teal
n c 1 2 "tsnet" "程序就是节点" k=vio
n d 1 3 "自建 DERP" "cmd/derper" k=warn
n e 2 3 "第三方控制面" "Headscale 等" k=warn
e q:r -> a:l
e q:r -> b:l
e q:r -> c:l
e q:r -> d:l
e d -> e dash
cap: 协调服务器闭源，自建完整网络要用第三方实现。仓库里的 [[ts:cmd/testcontrol/|testcontrol]] 只用于测试，不是生产控制面。
:::

### 仓库里没有的东西

- **协调服务器**：闭源 SaaS。协议只能从 `tailcfg` 和客户端代码推断。
- **macOS、iOS、Windows GUI**：闭源外壳。
- **ACL 策略求值**：策略编译在服务器，客户端只收到编译后的 `FilterRule`。这一点是从客户端只收到 `FilterRule` 推断的。
- **Go fork**：发布版用 `tailscale/go`，但不是必需。

## 附录：源码索引

| 主题 | 链接 |
|---|---|
| daemon 入口 | [[ts:cmd/tailscaled/tailscaled.go|tailscaled.go]] |
| CLI | [[ts:cmd/tailscale/|cmd/tailscale]] |
| 大脑 | [[ts:ipn/ipnlocal/local.go|local.go]] |
| 本地 API | [[ts:ipn/localapi/|localapi]]、[[ts:client/local/local.go|client/local]] |
| 控制协议 | [[ts:tailcfg/tailcfg.go|tailcfg.go]]、[[ts:control/controlclient/direct.go|direct.go]] |
| 引擎 | [[ts:wgengine/userspace.go|userspace.go]] |
| 路径 | [[ts:wgengine/magicsock/magicsock.go|magicsock.go]]、[[ts:wgengine/magicsock/endpoint.go|endpoint.go]] |
| disco | [[ts:disco/disco.go|disco.go]] |
| DERP | [[ts:derp/README.md|README]]、[[ts:cmd/derper/|cmd/derper]] |
| TUN | [[ts:net/tstun/wrap.go|wrap.go]] |
| 过滤 | [[ts:wgengine/filter/filter.go|filter.go]] |
| 网络探测 | [[ts:net/netcheck/netcheck.go|netcheck.go]] |
| 功能模块 | [[ts:feature/README.md|feature/README.md]] |
| 嵌入 | [[ts:tsnet/tsnet.go|tsnet.go]] |
| Tailnet Lock | [[ts:tka/tka.go|tka.go]] |
