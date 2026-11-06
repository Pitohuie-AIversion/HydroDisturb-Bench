# COMSOL 仿真安全防护规则（不可绕过）

1. **基线模型严格只读 (Immutability)**：
   - 严禁原地修改或覆写 `models/` 或 `case/` 目录下的 baseline `.mph` 文件。
   - 所有变参和仿真计算必须输出到 `runs/<task_id>/` 隔离沙箱中。
   - 操作前与操作后强制校验 baseline 文件的 SHA-256 哈希值，若哈希值发生任何改变，必须立即中止并报警。

2. **严禁臆测节点标签 (Introspection First)**：
   - 严禁在未经查询的情况下假设 `comp1`, `spf`, `std1`, `mesh1` 等节点存在。
   - 在触发参数更新或求解前，必须先调用 `inspect_model` 读取物理场真实 Tag 及参数单位。

3. **上下文轻量契约 (Heavy Data Downsampling)**：
   - 严禁将超过 50KB 的原始网格点阵、全局单元应力或全时步场量数组直接返回给上下文。
   - 必须通过 `export_diagnostics` 仅获取标量 KPI 指标、1D 探针 CSV 路径及 2D 切片图路径。

4. **进程与浮动 License 保护 (Concurrency Control)**：
   - 在触发求解时，必须遵循排队互斥锁，等待浮动 License 令牌释放。
   - 任何超时未响应的子进程必须在规定窗口（默认 1800 秒）内强制熔断退出，避免幽灵进程占用宿主机核心与内存。
