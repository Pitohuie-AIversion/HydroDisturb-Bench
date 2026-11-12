# Changelog

本文件按 Git 提交日期记录已合入 `main` 的开发里程碑；实验目录日期可能早于提交日期。项目包版本仍为 `0.1.0`，这里不另行虚构版本发布。当前状态、入口与验证边界见 [开发文档](docs/DEVELOPMENT.md)。历史测试数字来自对应归档，不表示本次文档更新重跑了这些测试；定向测试不与回归总数相加。

## 2026-11-09

### 局部体力方向与持续时间对照

- 登记独立冻结的 `reverse`（−90°／2 s）、`short`（+90°／1.2 s）、`long`（+90°／3.2 s）配置，复用原默认 +90°／2 s 和零分支。共同中心 `(0.15,0.12) m`、`A=0.005 m/s²`、`sigma=0.01 m`、`on=181 s`；保留同一 180 s 原生 checkpoint、网格、时间策略与 `(101,3,97,161)` 采样。
- 三个新增案例均完成真实 `inspect/solve/sample/analyze` 并逐案通过；各有 457 个接受的内部步、实际最大步长 0.05 s 和 101 帧输出，824 个历史文件哈希保持一致。
- 新增只读比较入口，绑定冻结配置、run plan、旧引用文件、实际回读／步进和模型／场数据来源。各案原窗口门槛保持；另以共同 ROI `[0.12,0.18,0.09,0.15] m`、窗口 `(181,188] s` 比较四案。历史幅值旧 ROI／窗口的峰值不直接参与本次横向比较，不预设流场反号或响应与冲量线性关系。
- 新配置精确使用 `text eol=lf`，通过真实 Git 暂存、blob 与 `core.autocrlf=true` 检出测试保护冻结字节身份。
- 保留反向采样的 `WinError 5` 原失败现场；核验 staging 的 117 个文件和七个数组后仅重试发布成功，未重新求解或采样，不宣称 Windows 偶发发布失败根因已修复。
- 不生成新训练索引，新增分支仍属于同一祖先族；保留历史报告与证据，继续轻量交付。真实执行状态、比较结果与本轮测试范围见 [阶段报告](docs/reports/M3_PULSE_DIRECTION_DURATION_20261109.md)。

## 2026-11-06

### 最小预测基线与评估

- 新增末帧保持、末两帧线性外推和逐提前量/通道 MAE/RMSE；预测函数只见历史与已知时间表，未来数值仅用于评分。
- 基于已有固定索引完成 5 条序列/105 窗口的 train 管线评估，68 个访问数据文件哈希不变；仅一个祖先族，不声称独立留出或自由滚动。
- 独立读取原始 NPZ 并用 `math.fsum` 复算全部分轨迹/总体指标；保留长提前量下压力线性外推更差的结果，没有准确率通过门槛。
- 新增 108 项测试包含于 **768 passed, 13 deselected** 的本地完整离线回归；不启动 COMSOL、不拟合模型、不上传模型或完整张量。[使用说明](docs/FORECAST_BASELINES.md)、[实测报告与日志](docs/reports/FORECAST_BASELINE_20261106.md)。

### 局部体力参数入口与真实续算

- 新增严格的局部高斯体力配置：全局米制位置、方向角、加速度峰值、Gaussian σ、对齐保存表的起止时间；三分量 VolumeForce、源积分和 Dataset/Solution 实际回读。
- 从同一 M3 原生 checkpoint 完成全参数变化案例 `(0.17,0.13) m / 45° / 0.0075 m/s² / σ=0.008 m / 182–184 s`，101 帧场采样与重启/响应门槛通过；不覆盖历史模型和张量。
- 冷构建复现 `csutil.dll` 依赖失败后，窄匹配回退到同安装 bundled Java 编译；非相关编译失败仍拒绝。参数、验证、Java parser 和 runner 定向测试 **253 passed**，完整离线回归 **1021 passed, 13 deselected**。[参数说明](docs/LOCAL_PULSE_PARAMETERS.md) · [真实报告](docs/reports/LOCAL_PULSE_PARAMETERS_20261106.md)

### 局部体力矩阵预注册

- 新增轻量 `m3_local_pulse_sweep.json` 与 `pulse_sweep` 校验入口，登记默认、对角线和反向三个局部体力案例；基础配置哈希、六个物理参数、重复配置和同一 M3 checkpoint 族策略均在发布前检查。
- 该步骤只生成带哈希的计划 manifest，不启动 COMSOL、不提交 `.mph`、原生状态向量或完整张量；实际续算必须按案例使用独立输出目录并保存真实回读证据。

### 连续数据使用层

- 新增 `FieldSequenceReader` 按批遍历、跨批窗口及无拼接全批核验；保留旧全量 loader 与导出校验兼容。
- 新增显式轨迹族划分、半开时间窗口索引及独占保存/读回；共享祖先或数据身份不能通过改名跨分区，学习器字段与扰动/来源元数据分离。
- 真实 M3 入口先读取固定来源链，再为已有五条序列生成轻量索引；它们同属一个祖先族，全部 train，validation/test 为空。5+5 帧、步幅 5 仅为管线规格。
- 不新增求解或完整张量副本；[数据使用说明](docs/DATA_USAGE.md) 列出验证范围、路径与内存边界。
- 已有 555 帧/21 批/105 窗口与固定旧 reader 对照一致，28 个窗口跨批；45 个输入文件哈希保持。完整离线回归 **660 passed, 13 deselected**，保留夹具发布失败与旧/新版本对照，未宣称修复历史 WinError 5。[本轮报告与轻量索引](docs/reports/DATA_USAGE_20261106.md)。

### 文件事务层拆分

- 将 bridge 的 staging 租约、发布锁、文件发布、失败隔离和孤立文件恢复提取到 `comsol_java.file_transactions`；错误契约独立到 `errors`，原公开导入路径保留。
- 分支及采样留证纳入新模块的实际源码哈希；已有模型、冻结配置和历史执行记录不迁移、不改写。
- 故障注入定位到实现所属模块，旧 bridge 与 package 入口继续参与兼容性测试。这是行为保持的结构拆分，不宣称修复历史 Windows `WinError 5`。[报告与回归记录](docs/reports/FILE_TRANSACTION_REFACTOR_20261106.md)。
- 固定基线 AST 对照中 46 个函数/类、8 个错误常量一致；新增 7 项兼容、真实进程锁与失败恢复测试。本地完整离线回归 **553 passed, 13 deselected**，未运行真实 COMSOL 集成测试。

### CI 修复与 Python 执行服务抽取

- 修复 uv 已安装但 Ruff extra 未安装造成的真实 Actions 失败；固定 uv 0.13.7，显式同步锁定的 dev/analysis 依赖，后续命令不重复同步，上传 JUnit。
- 根据首次修复后的远端收集失败，显式配置 pytest 的源码与仓库根导入路径，统一 console pytest 与 `python -m pytest` 对脚本模块的导入。
- 分离 `artifact` 与真实 `integration` 测试；7 个模拟事务实例使用临时模型，恢复 S0 的 15 项离线覆盖；历史产物缺失仍明确失败。
- 修正 NumPy 最低版本为 2.0，清理当前维护代码的 lint；冻结证据退出样式扫描，原字节与清单核验保留。
- 抽取公共分支执行、进程留证、步进与证据 IO 服务；幅值入口不再加载旧 CLI，生产代码移除全局 subprocess patch。显式执行器覆盖编译、启动回退及逐批采样，保留来源、失败现场和旧记录读取。
- 本地完整离线回归 **546 passed, 13 deselected**；修正导入路径后，console pytest 的 CI 子集为 **522 passed, 37 deselected**。未启动 COMSOL；Java、物理参数、统计公式和历史证据未修改。[修复报告与验证范围](docs/reports/CODE_HEALTH_CI_20261106.md)。

### 开发文档同步

- 新增开发状态、分支状态、阶段证据和正式运行入口索引；同步 README、验证清单、研究计划、Java API、模型契约及发布指南。
- 区分已合入实验、未合入 MCP/插件原型、未来研究；修正过时 CLI 参数、旧 GitHub 发布目标与纯脚手架状态描述。
- 明确干净克隆的测试夹具限制、原生重启初始化变化、轻量数据交付及固定提交审计方式。未修改求解器、模型、统计实现或历史证据。

### M3 脉冲幅值系列 — [`195d613`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/195d613decee0f960b86f2dd588592f60fc01856)

- 在同一原生 `sol1 / solnum 901 / t=180 s` 起点新增 0.0025 和 0.010 m/s² 脉冲分支；复用已有零分支与 0.005 m/s² 结果。
- 冻结空间/时间包络、网格、时间积分及重启协议；新增计划绑定、旧文件身份核验、分阶段入口与大小写无关但严格格式校验的 SHA-256 比较。
- 两条新增分支完成真实求解、101 帧采样、节点回读、源积分及步进核验。三档 `(181,186] s` 窗口内响应峰值/U 为约 2.922887%、5.847157%、11.688384%；仅作此窗口内描述，不认定一般线性规律。
- 归档入口测试 53 项通过；同步远端代码后离线回归 **514 passed, 30 deselected**。同步后的正式 loader/验证函数重读结果与原归档一致，没有重算 CFD。
- 轻量入库，完整 MPH、原生向量和全量 TXT/NPZ 留在数据目录，以身份清单定位。
- [报告](docs/reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md) · [指标](docs/evidence/m3_pulse_amplitude_series_20261106/series_metrics.json) · [回归原始日志](docs/evidence/m3_pulse_amplitude_series_20261106/logs/offline_regression_after_remote_sync/stdout.txt)

### 性能改动合入 — `a2271a4` / `f043239`

- PR #2 合入 NumPy 向量化 COMSOL 时序解析（`3971eb1` 及其修正）。
- PR #3 合入圆柱掩码广播计算，减少 `meshgrid` 中间数组（`1d5395e`）。
- 最新幅值交付保留这两项改动，并完成同步后离线回归和已有新样本读回验证。不据此虚构 CFD 求解加速倍数。

### 共同起点的零扰动 / 单脉冲 — [`1f4d74e`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/1f4d74e68b05c29d2ab62d3bb7486f54b22136dd)

- 从 M3 保存解复制原生 U、UDot，构造零扰动与 0.005 m/s² 局部 Gaussian/sin² 脉冲两条未来。
- 采用披露的 `native-restart-consistent-off-v2` 协议；分别评估 Z−B 重启差异和 P−Z 扰动响应，不声称恢复完整 BDF 历史。
- 引入原生状态、实际体力单位/积分、时间步和连续场比较门槛；采样复用既有数据契约。
- 转为代码、必要日志、紧凑回读和清单入 Git 的轻量交付；完整大数据保留本地。
- 定向复测 212 项通过，离线 **461 passed, 30 deselected**；首次 `WinError 5` 失败记录保留，未宣布根因修复。
- [报告](docs/STAGE_M3_BRANCH_PULSE_20261105.md) · [证据目录](docs/evidence/m3_branch_pulse_20261105)

## 2026-11-05

### 连续全域场数据 — [`301088d`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/301088dd8b7bd7a6c4bc88f827693cbcfbfd2c4b)

- 在已有 M3 解上导出 170–200 s、0.2 s 间隔的 151 帧，不求解新 CFD；161×97 网格，张量 `(151,3,97,161)`。
- 复用单批采样，以 32+32+32+32+23 帧组织；正式 loader 拒绝缺帧、重叠、额外批次及来源/坐标/掩码变化。
- 验证 7,074,501 项张量条目，包含 7,068,612 个流体值与 5,889 个掩码 NaN；保留 453 次点×时刻直接核对和五帧重叠比较。
- 采样定向 91 项通过，离线 **340 passed, 30 deselected**。
- [报告](docs/STAGE_M3_FIELD_SEQUENCE_20261105.md) · [结果](docs/evidence/m3_field_sequence_20261105/sequence_report.json)

### 五时刻、双分辨率采样 — [`c346361`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/c34636166dc37fbee9c5ff68db289e9d8d006a54)

- 从保存解按实际解编号选择 180/185/190/195/200 s，在 81×49 与 161×97 规则坐标导出 `[u,v,p]`。
- 实现 `(T,3,Ny,Nx)` float64、递增坐标、实际单位/来源、独立掩码、NPZ 保存和完整读回；流体区域非有限值拒绝发布。
- 293,790 项总条目核验、共同流体坐标比较及 30 次点×时刻核对；这是同一离散解的数据一致性，不是零 CFD 误差。
- 定向复测 59 项通过，离线 **308 passed, 30 deselected**。保留首次发布目录 `WinError 5`，不以复测通过认定根因消失。
- [报告](docs/STAGE_M3_FIELD_SAMPLING_20261105.md) · [审计](docs/evidence/m3_field_sampling_20261105/sample_artifact_audit.json)

### 归档证据修复 — `bcb611a` → `37d442e` → [`19aea85`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/19aea85a6f51d5c36ee2d71cfbbcb0e0136e5205)

- 恢复实际 pytest stdout、退出码、JUnit 和求解步进日志，按 Git blob 字节同步证据清单。
- 最终规则改为 `docs/evidence/** -text`，只对明确的文本后缀启用 `text eol=lf`，防止 PNG/未知二进制被换行转换。
- 增加真实 `git add` 暂存字节检查及已提交文件清单检查；本地归档记录 82 项文件、其中 13 PNG 无不一致。
- [规则](.gitattributes) · [检查入口](check_git_attributes.py) · [测试](tests/test_git_attributes.py)

### M3 时间步抽查 — `f74eea4`

- 在相同 M3 原生网格上，将自适应内部步长上限从 const 0.05 s 减至 0.025 s，保持 `rtol=1e-4`。
- 两组先通过周期稳定性，再比较最后五个完整周期；四指标变化均 <0.07%，满足 ≤1% 门槛。保留 0.05 s 作为当前数据生成设置。
- 完整回归归档 275 项通过；后续 `bcb611a` 补入真实执行与重新捕获的日志。
- [报告](docs/STAGE_S1B3_M3_TIME_20261105.md) · [比较 JSON](docs/evidence/s1b3_m3_time_20261105/m3_time_report.json)

### 原生网格系列 — `0dd7df2` / `3743c19` / `ac3971c` / `652404e`

- 从受保护的 Case C / M0 按固定尺度规则构造 M1、M2、M3 原生求解网格；不以增加后处理采样点代替 CFD 网格细化。
- 记录实际尺寸、单元/自由度、质量、圆柱近壁与尾流分辨率，保持域和物理配置。
- M1/M2 阶段仍有四指标中的超限项；最终 M2→M3 四指标变化约为 +0.789899%、+0.335012%、+0.685247%、+0.703916%，全部进入 ≤1% 工程门槛。
- `652404e` 修正 M3 报告元数据与绝对变化判定说明。没有确立严格渐近阶次或真解误差上界。
- M1/M2/M3 各次完整回归记录分别为 220、239、261 项通过。
- [M1 报告](docs/STAGE_S1B2_M1_20261105.md) · [M2 报告](docs/STAGE_S1B2_M2_20261105.md) · [M3 报告](docs/STAGE_S1B2_M3_20261105.md)

## 2026-11-04

### 时间敏感性、积分与真实单位来源

- `7532af8` 增加 A/B 容差策略比较；`22b2eb3` 修复整周期端点插值积分；`6acb01e` 纳入升力振幅/RMS 和真实时间三方核对。
- `21c82f4` 补充时间/表头单位、`actual_t` 缺失及非有限值拒绝，但当时入口仍补写速度单位，**不将该提交单独记为真实来源验收完成**。
- `6cc9458` 绑定实际导出证据并消除入口绕过；[`b8477d7`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/b8477d7342615ae0cac5d7f996c9c445cc8f6fb0) 修复 COMSOL 原生启动依赖并完成已有解真实回读与 Case C。表达式、单位、Dataset、模型/文件哈希关联后才可标记回读成功。
- Case C 使用 `rtol=1e-4 / const / 0.05 s`，实际 4,091 内部步；B/C 四指标变化均 <0.06%；完整回归 134 项通过。
- [`91e06b5`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/91e06b5085b3f9df17f1104d99ed64e91a20d774) 完成 D（cap=0.025 s），实际 8,067 内部步；C/D 最大变化约 0.0933%；完整回归 166 项通过。A/B/C 原始结果继续保留。
- [A/B 报告](docs/STAGE_S1B1_VERIFICATION.md) · [真实单位与 C 最终报告](docs/UNIT_SOURCE_CASE_C_VERIFIED_20261104.md) · [D 报告](docs/STAGE_S1B1_CASE_D_20261104.md)

### 可视化与解析

- `54af287` / `7eb802a` 完善规则网格检查、相位标签、三面板比较与圆柱边界容差带/尾流裁剪说明。
- NumPy 时序解析优化在功能提交中开发，2026-11-06 经 PR #2 合入；合入日期与原开发日期分别保留。

## 2026-11-03

- `a78bd39` 建立独立的低阻塞 Re=100 外流基线；`d7215b1` 声明分析依赖。
- `1959522` / `cf70958` 完善导出 staging、动态表头、发布前数据契约及实际几何查询，失败时拒绝发布。
- `7f7cb4e` / `1799ff7` 完成 S1-A 长时试运行、坐标/单位校验、压力参考、升阻力/尾流诊断及多窗口周期分析；`35bdcff` 增加诊断发布事务和版本记录。
- `545bce3` 扩展到 200 s，检查周期饱和与稳定统计。S1-A/S1-A2 报告分别记录完整回归 86/103 项通过。
- `3b923a5` / `962cc01` 增加红白蓝涡量配色、色条、20D 尾流局部图与卡门涡街可视化。
- [S1-A 报告](docs/STAGE_S1A_VERIFICATION.md) · [S1-A2 报告](docs/STAGE_S1A2_VERIFICATION.md)

## 2026-11-02

- `b211e44` 初始化参数规划、配置校验、可复现随机抽样、manifest 与可选 MPh 单模型接口。生成的是待求解计划。
- `ca29ce1` 接入官方 COMSOL 6.4 Java API、模型检查/参数副本/求解/导出入口、技能与集成测试。
- `5ad7224` 至 `8497dd8` 逐步补齐子进程返回码及 schema 拒绝、独立 staging、数值解检查、隔离失败目录、进程间发布锁、源文件复核、lease/恢复与 POSIX 发布竞态处理。
- `e50ba91` / `bb1ea1c` 记录接口契约及 Windows 优先支持范围；Linux solver 集成未获验收。
- [接口与平台说明](docs/COMSOL_API_INTEGRATION.md)

## 已推送但未合入 main 的开发

以下记录用于检索分支工作，不计入上述主线已交付能力或测试总数。截至本次同步，远端功能分支为 [`d1ba0af`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/d1ba0af0041f029db5124414d8f72bf2f8f45206)。

| 提交 | 内容 | 验证状态 |
| --- | --- | --- |
| `08c0a58` | 26 工具的 COMSOL MCP 服务、动态 Java 执行器及辅助模块 | 源码已存在；未找到该分支提交的真实回归日志 |
| `5dcb715` | 同步主线 IO/掩码优化到功能分支 | 不代表 MCP 已合入主线 |
| `689cd4a` | 通用 CAE 插件初版、打包与汇报资料 | 开发产物，不是其他软件后端验收 |
| `d1ba0af` | Universal Software MCP Builder 通用规范、模板和 COMSOL 案例 | 插件配置、工具参数/审批匹配与客户端安装仍需验证 |

详细源码入口和具体待核验项见 [未合入的开发](docs/DEVELOPMENT.md#未合入的开发)。本轮文档同步不合并该分支，不将异步/HPC/OAuth/全面 API 覆盖等目标描述为现成功能。
