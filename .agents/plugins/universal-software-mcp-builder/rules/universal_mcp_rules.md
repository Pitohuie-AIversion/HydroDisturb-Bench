# 通用工业软件 MCP 构建核心准则（不可违背的工程红线）

当使用本插件指导、生成或审查任何桌面软件（如 COMSOL, ANSYS, Abaqus, MATLAB, SolidWorks）的 MCP 服务时，必须强制遵循以下 6 大非妥协契约：

1. **子进程物理隔离 (Crash & Lifecycle Isolation)**：
   - 严禁在 MCP Server 主进程内直接加载原生仿真计算引擎或不可信动态库。
   - 必须通过独立子进程（Subprocess）、RPC 守护进程（Daemon）或批处理客户端运行，并强制配置超时看门狗（Watchdog），防止底层崩溃（OOM / Segfault / 奇异矩阵）导致 MCP 宿主崩溃。

2. **Base64 零转义通信边界 (Zero-Escape Boundary)**：
   - 工业软件参数普遍带有方括号、物理单位和科学计数法（如 `1.15[m/s]`, `range(0,0.05,0.1)`）。
   - 进程间所有 CLI 参数和标准 I/O 交互必须强制采用 Base64 编码封包传输，彻底消除 Windows 路径反斜杠转义与命令行注入漏洞。

3. **写时复制与只读基线 (Copy-on-Write & Baseline Immutability)**：
   - 用户原始模型文件（如 `.mph`, `.cas`, `.inp`, `.slx`）为神圣不可侵犯的只读资产。
   - 任何涉及变参、网格剖分、几何构建的操作，必须预先生成独立沙箱副本并在副本上执行；强制执行前置/后置 SHA-256 哈希校验。

4. **反幻觉自省先行 (Introspection-First Principle)**：
   - LLM 极易臆测工程软件内部树节点（如臆测物理场 Tag、网格序列号、边界编号）。
   - 必须提供且优先调用 `inspect_*` 工具反射出真实的组件树、参数字典和单位制，严禁盲目猜写。

5. **重数据降采样与上下文保护 (Heavy Data Downsampling Contract)**：
   - 工业仿真产生的 GB 级网格与全时步瞬态场量严禁直接塞入 LLM 上下文。
   - MCP 工具只返回轻量标量 KPI 指标、收敛残差状态，以及 2D 剖面切片图或 1D 测线探针 CSV 的磁盘路径。

6. **商业浮动 License 令牌排队 (Floating License Governance)**：
   - 商业软件 Token 昂贵且并发受限。在触发计算或签出 License 前，必须通过跨进程文件互斥锁（FileLock）进行排队治理，避免多并发智能体打爆许可证服务器。
