# 开发状态与证据索引

更新：**2026-11-06**。本页汇总 `main` 的开发历史、功能分支、正式 Python/Java 入口、测试、阶段报告和归档日志。数值实验与后续软件维护分别列示；软件回归不增加一次 CFD 求解或科学精度验证。

数值成果以 [`195d613decee0f960b86f2dd588592f60fc01856`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/195d613decee0f960b86f2dd588592f60fc01856) 为最新实验交付。后续软件维护不改变该固定证据身份。开发变更按日期见 [CHANGELOG](../CHANGELOG.md)，科学验收边界见 [VALIDATION](VALIDATION.md)。

## 当前状态

项目已从参数规划脚手架推进到 **M3 原生状态下的一组受控未来分支**：保留零扰动对照，在同一 checkpoint 上得到 0.0025、0.005、0.010 m/s² 三档局部脉冲响应。连续基线、原生重启、单位来源、场采样与读回均有真实 COMSOL 执行证据。当前尚不是多起点、未知机制泛化数据集，也没有训练模型结果。

| 开发线 | 本次核实的提交 | 处理状态 |
| --- | --- | --- |
| `main` | 实验基线 `195d613`；CI 修复基线 `c28aded6090fecdf287fee7f5b6cbc27f3ff651d` | 数值结果保留；后续软件维护记录见下文 |
| `feat/comsol-mcp` | `d1ba0af0041f029db5124414d8f72bf2f8f45206` | 已推送、尚未合入；MCP/插件原型单列如下 |
| `codex/m3-pulse-amplitudes` | 本次核查时与实验提交 `195d613` 相同 | 已完成工作的工作分支，不是另一套实验结果 |
| `codex/m3-pulse-full-local` | `6ab889cc160d74cf779dee85fcfee442bdb2f5d7` | 保留的历史大型归档方案，不属于 `main` 当前交付 |

本次直接复用原仓库，不新建副本或 worktree。后续以当前主工作目录开发，代码与大型数据位置分别记录；历史分支或仍被会话占用的工作区不等于需要重新求解或再次交付。

软件维护已完成 uv 锁定安装、测试分层和分支执行服务抽取；`c28aded` 的 [Windows/Linux CI](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/actions/runs/42202843334) 各有 522 passed、37 deselected。随后将 bridge 的文件锁、staging、发布与恢复抽到独立模块，保留公开入口和失败留存语义，见 [第二轮拆分报告](reports/FILE_TRANSACTION_REFACTOR_20261106.md)。各次执行的测试范围和数量分别记录，不累计为同一次验证。

数据使用层现提供按批读取、跨批时间窗口和显式轨迹族划分。已有五条序列的 555 帧、21 批次被用于生成 105 个窗口，统一归入一个 train 族，validation/test 为空；未新增物理工况。见 [使用接口](DATA_USAGE.md) 与 [真实数据审计](reports/DATA_USAGE_20261106.md)。

## 已合入的阶段与复核入口

下表描述最终阶段结论，不把早期失败或待修复报告当作最终证据。历史报告保留原样；其中旧措辞与当前状态不一致时，以最终结构化结果、修复提交和本页限定范围为准。

| 阶段 | 最终开发成果与状态 | 报告 / 结构化证据 |
| --- | --- | --- |
| 基础接口与 S0 | 官方 Java API、独立输出与失败保留；建立 0.40×0.24 m 的低阻塞 Re=100 模型，区别于原始通道流夹具 | [API 文档](COMSOL_API_INTEGRATION.md)、[模型说明](../models/cylinder_2d/README.md) |
| S1-A / S1-A2 | 100 s 试运行后扩展到 200 s；建立周期稳定性、升阻力与尾流诊断、涡量可视化 | [S1-A](STAGE_S1A_VERIFICATION.md)、[S1-A2](STAGE_S1A2_VERIFICATION.md) |
| S1-B1 A/B | 容差策略比较；修复整周期端点插值积分，四指标共同判定 | [A/B 报告](STAGE_S1B1_VERIFICATION.md) |
| 真实单位与 Case C | 移除入口固定补写单位；同次导出节点实际回读表达式、单位、Dataset，绑定模型与文件；修复 `csutil.dll` 启动依赖；C 采用 `1e-4 / const / 0.05 s` | [最终报告](UNIT_SOURCE_CASE_C_VERIFIED_20261104.md)、[C 回读](evidence/unit_source_verified_20261104/c_probe_readback.json)、[B/C 指标](evidence/unit_source_verified_20261104/case_c_report.json) |
| Case D | 同网格 cap=0.025 s；C/D 四指标均变化 <0.1%，采用 C 作为后续起始设置 | [报告](STAGE_S1B1_CASE_D_20261104.md)、[比较 JSON](evidence/case_d_20261104/case_d_report.json)、[D 回读](evidence/case_d_20261104/d_probe_readback.json) |
| S1-B2 M0–M3 | 原生网格细化；M1/M2 阶段仍有指标超 1%，最终 M2/M3 四项进入 ≤1% 门槛；选择 M3 | [M1](STAGE_S1B2_M1_20261105.md)、[M2](STAGE_S1B2_M2_20261105.md)、[M3](STAGE_S1B2_M3_20261105.md)、[四网格 JSON](evidence/s1b2_m3_20261105/mesh_report.json) |
| S1-B3 | 同 M3 网格 cap 0.05→0.025 s，四指标变化均 <0.07%；保留 0.05 s | [报告](STAGE_S1B3_M3_TIME_20261105.md)、[比较 JSON](evidence/s1b3_m3_time_20261105/m3_time_report.json) |
| Git 证据归档 | 默认保持证据二进制字节，仅明确文本后缀规范化 LF；实际暂存和 Git blob 清单核验 | [规则](../.gitattributes)、[检查器](../scripts/check_git_attributes.py)、[测试](../tests/test_git_attributes.py) |
| 五帧全域采样 | 已有 M3 解的 180/185/190/195/200 s，81×49 与 161×97；不重新求解 | [报告](STAGE_M3_FIELD_SAMPLING_20261105.md)、[审计](evidence/m3_field_sampling_20261105/sample_artifact_audit.json) |
| 151 帧连续基线 | 170–200 s、0.2 s 间隔、161×97；32+32+32+32+23 批次；严格检查跨批时间、坐标、掩码与来源 | [报告](STAGE_M3_FIELD_SEQUENCE_20261105.md)、[序列结果](evidence/m3_field_sequence_20261105/sequence_report.json) |
| 零分支 / 单脉冲 | 同原生 `sol1 / solnum 901 / t=180 s`；分别比较 Z−B 与 P−Z，区分重启差异和响应 | [报告](STAGE_M3_BRANCH_PULSE_20261105.md)、[零分支回读](evidence/m3_branch_pulse_20261105/zero_readback_summary.json)、[脉冲回读](evidence/m3_branch_pulse_20261105/pulse_readback_summary.json) |
| 多幅值组 | 复用零分支及 0.005；新增 0.0025、0.010；只改变预注册幅值及其源积分参考值 | [阶段报告](reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md)、[系列结果](evidence/m3_pulse_amplitude_series_20261106/analysis/report.json)、[完整精度指标](evidence/m3_pulse_amplitude_series_20261106/series_metrics.json) |

时间与网格结论仅适用于已测试配置下的 St、平均 Cd、升力半峰峰振幅及去均值 RMS。它们不是相对真解的误差上界，不证明计算域独立性或全场逐点收敛。未建立形式化 Richardson/GCI 外推结论。

## 当前实验配置与数据契约

| 项目 | 当前冻结配置 |
| --- | --- |
| 原生模型 | M3，34,752 个二维单元、88,483 个报告自由度；非 161×97 采样网格 |
| 基线求解 | `rtol=1e-4`、`maxstepconstraintbdf=const`、`maxstepbdf=0.05 s`；输出每 0.2 s |
| 共同起点 | 已保存 M3 原生解 `sol1 / solnum 901 / 180 s`，复制 U、UDot；不由规则张量反插值 |
| 分支重启 | `native-restart-consistent-off-v2`；`consistent=off`；不声称恢复原 BDF 内部历史 |
| 脉冲 | 中心 (0.15,0.12) m，σ=0.01 m，+y 方向，181–183 s 紧支撑 sin² 时间包络 |
| 源项单位 | A 是 m/s²；乘实际密度形成 N/m³ 体力，记录实际源项积分 |
| 分支输出 | 180–200 s 共 101 帧，每档 `(101,3,97,161)` |
| 连续基线 | 170–200 s 共 151 帧，`(151,3,97,161)` |
| 坐标与通道 | `float64`、`[u,v,p]`、递增 x/y，包含全域端点；匹配保存时刻/解编号 |
| 掩码 | 几何掩码、原始无效点与最终掩码明确区分；掩码内 NaN，流体区域非有限值拒绝发布 |
| 来源 | 表头与实际节点单位、Dataset/Solution、模型/文件身份、批次时间及读回检查 |

151 帧只有一条轨迹，不是 151 个独立工况。当前三个非零幅值也只有一个共同起点。采样分辨率验证的是数据接口，不提前认定为最终训练规格。

三档的响应窗口 `(181,186] s` 内 ROI 速度 RMS 峰值/U 分别为 **2.922887%、5.847157%、11.688384%**，约为同 ROI 重启差异峰值的 **55.5、111.0、222.0 倍**。三个峰值均出现在窗口右端 186 s；这不代表完整轨迹的全局峰值或一般线性定律。

## 最新交付的直接入口

| 内容 | 入口 |
| --- | --- |
| 报告与冻结计划 | [报告](reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md)、[计划](evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json)、[冻结记录](evidence/m3_pulse_amplitude_series_20261106/plan_freeze_record.json) |
| 两支真实回读 | [a0025](evidence/m3_pulse_amplitude_series_20261106/a0025_readback_summary.json)、[a0100](evidence/m3_pulse_amplitude_series_20261106/a0100_readback_summary.json) |
| 两支完整步进 stdout | [a0025](evidence/m3_pulse_amplitude_series_20261106/a0025_solver_stdout.txt)、[a0100](evidence/m3_pulse_amplitude_series_20261106/a0100_solver_stdout.txt) |
| 原生来源及实际源积分 | [a0025](evidence/m3_pulse_amplitude_series_20261106/analysis/a0025/native_validation.json)、[a0100](evidence/m3_pulse_amplitude_series_20261106/analysis/a0100/native_validation.json) |
| 完整离线回归 | [同步后 stdout：514 passed / 30 deselected](evidence/m3_pulse_amplitude_series_20261106/logs/offline_regression_after_remote_sync/stdout.txt) |
| 同步后正式 loader 重验 | [post_sync_validation.json](evidence/m3_pulse_amplitude_series_20261106/post_sync_validation.json) |
| Git 与本地数据身份 | [轻量交付清单](evidence/m3_pulse_amplitude_series_20261106/lightweight_export_manifest.json)、[本地文件清单](evidence/m3_pulse_amplitude_series_20261106/local_artifact_inventory.json) |

纯远程材料复核使用固定的实验提交，输出路径必须尚不存在：

```powershell
python -X utf8 docs/evidence/m3_pulse_amplitude_series_20261106/audit_delivery.py --git-ref 195d613decee0f960b86f2dd588592f60fc01856 --output amplitude_delivery_review_01.json
```

审计读取实际 Git blob、清单、冻结引用、摘要披露和日志结构；不启动 COMSOL，不要求大型本地数据，不重新证明全部原生向量/全场数值。审计器绑定交付父提交，因此后续文档提交不能用 `HEAD` 代替上述实验哈希。

## 正式入口与运行影响

从仓库根目录调用。Python 环境安装 `.[dev]`；绘图增加 `.[analysis]`；Java 路径需要本机 COMSOL，MPh adapter 才需要 `.[comsol]`。源码入口比历史报告中的临时执行命令优先。

| 入口 | 作用及前提 |
| --- | --- |
| `hydrobench plan` / `inspect` | 通用参数规划与查看，不求解；`configs/base_case.json` 不是冻结 M3 计划 |
| `hydrobench java-inspect-api` / `java-inspect --model ...` | 读取本机安装或实际模型，使用 Java bridge；完整语法见 [API 文档](COMSOL_API_INTEGRATION.md) |
| [sample_re100_m3_fields.py](../scripts/sample_re100_m3_fields.py) `--output <新目录>` | 从已保存 M3 解导出五帧、两档网格，不调用求解 |
| [sample_re100_m3_sequence.py](../scripts/sample_re100_m3_sequence.py) `--output <新目录>` | 151 帧连续导出；需已保存 M3 模型及旧五帧重叠参考，不调用求解 |
| [run_m3_branch_pulse.py](../scripts/run_m3_branch_pulse.py) `--output <新目录> --stage all` | 准备原生起点并实际求解零/0.005 两分支；不是旧结果只读复核命令 |
| [run_m3_pulse_amplitude_series.py](../scripts/run_m3_pulse_amplitude_series.py) `--output <新目录> --stage precheck` | 检查冻结计划、旧模型及数据身份，不进行新的 Time 求解 |
| 同上 `--stage all` | 实际求解两个新增幅值并采样；不能用于重复确认已验收成果 |
| [prepare_m3_data_usage.py](../scripts/prepare_m3_data_usage.py) `--output <新目录>` | 只读核验已有数据的来源族，创建小型窗口索引；不加载 MPH 或启动 COMSOL；可选固定旧 reader 对照 |
| [check_git_attributes.py](../check_git_attributes.py) `--attributes .gitattributes` | Git 属性、真实暂存行为及清单检查；仅在归档规则或相应证据改变时按需运行 |

旧五帧/151 帧脚本按源码定位仓库内的固定历史 `runs/` 路径。幅值系列入口支持 `HYDRO_DATA_ROOT`，其冻结相对路径必须指向持有旧模型、checkpoint 和样本的数据根目录；该环境变量不是所有旧入口统一支持的重定向参数。不要以改哈希、删除检查或重算历史模型来绕过缺数据。

既有 `run_re100_*` 脚本保留用于复现旧阶段，部分旧脚本使用固定输出路径并允许覆写。调用前查看实现及报告；不把整套旧求解作为常规开发检查。新的代码工作与新的输出各自明确定位，避免将临时 worktree 当作数据源。

## 测试与已知限制

| 用途 | 命令 / 前提 |
| --- | --- |
| 干净克隆的规划示例 | `python -m pytest -q tests/test_planning.py`，不要求 COMSOL 或历史模型 |
| 采样、序列与分支修改 | 按变更选择 `tests/test_full_field_sampling.py`、`tests/test_field_sequence.py`、`tests/test_branch_pulse.py`、`tests/test_branch_pulse_runner.py`、`tests/test_pulse_amplitude_series.py` |
| 干净环境 CI | `uv sync --locked --extra dev --extra analysis`，随后 `uv run --no-sync pytest -q --strict-markers -m "not integration and not artifact"`；无需 COMSOL 或本地历史数据 |
| 配有历史夹具的离线回归 | `python -m pytest -q -m "not integration"`，包含 `artifact` 历史验收；缺文件时仍失败 |
| 真实接口验证 | `tests/test_comsol_integration.py` 及对应阶段运行证据；需要真实环境，部分测试会求解，不与离线通过数混算 |

定向测试是相关回归的子集，不能相加为独立总数。514 项记录是实验交付时的 Windows 离线执行；新的 CI/测试分层及 Python 服务抽取记录见 [代码健康修复报告](reports/CODE_HEALTH_CI_20261106.md)。测试数随新增覆盖和标记校正变化，不将旧数量作为永久验收目标。真实两条新分支另有回读、步进和场验证记录。

当前可见限制：

- **测试分层**：历史产物检查现单列 `artifact`，真实求解/安装检查标 `integration`，模拟事务测试使用临时假模型保留在普通 CI。首次幅值回归缺夹具的 29 项历史失败保留原记录，不改写成当时通过。CI 显式安装锁定依赖并排除这两类外部环境检查；实际运行结果以对应提交的 Actions 为准。
- **Windows 发布可靠性 P2**：曾出现目录 rename 的 `WinError 5`。失败现场保留、重测通过不等于根因已解决；将事务代码抽成独立模块也不等于修复该故障。
- **初始化边界**：分支协议把 `consistent` 从连续基线的 `bweuler` 改成 `off`，用 Z−B 测量影响，不能称所有求解设置未变。
- **科学边界**：未完成计算域扫描、独立求解器验证、完整动量收支、全域真解误差估计或 ML 泛化评估；施加源项积分不等于流体净动量变化。
- **存储边界**：Git 保存必要证据，完整 MPH/向量/TXT/NPZ 在本地；清单中独立备份为 `not_configured`，不宣称已备份，也不作为本轮数值通过的门禁。见 [交付规则](PUBLISHING.md)。

## 未合入的开发

本次远端核查：`feat/comsol-mcp` 指向 [`d1ba0af`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/d1ba0af0041f029db5124414d8f72bf2f8f45206)，与最新实验提交的共同祖先为 `f043239`。功能分支独有 4 个提交，实验分支独有 `195d613`；功能分支合入旧 `main` 不等于其功能已进入当前 `main`。

| 提交 | 已存在的开发内容 |
| --- | --- |
| `08c0a58` | [COMSOL MCP 服务](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/tree/d1ba0af0041f029db5124414d8f72bf2f8f45206/src/hydro_disturb_bench/mcp)：源码注册 26 个工具、2 个资源、1 个提示词，stdio 入口、动态 Java 执行与辅助模块 |
| `5dcb715` | 将已合入的 IO/掩码优化同步到功能分支 |
| `689cd4a` | 通用 CAE 插件初版、ZIP 与 HTML/PPT 汇报产物 |
| `d1ba0af` | [Universal Software MCP Builder](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/tree/d1ba0af0041f029db5124414d8f72bf2f8f45206/.agents/plugins/universal-software-mcp-builder)：通用开发规范、模板与 COMSOL 案例 |

分支中的 [MCP 指南](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/blob/d1ba0af0041f029db5124414d8f72bf2f8f45206/docs/COMSOL_MCP_SERVER_GUIDE.md) 和 [测试](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/blob/d1ba0af0041f029db5124414d8f72bf2f8f45206/tests/test_mcp_server.py) 可供实现审查，但本次未找到该分支提交的原始测试日志/退出码，不能借用 `main` 的 514 项通过记录。26 是注册数，不是 26 项真实 COMSOL 验收。

该原型仍有具体待核验项：服务工具为同步函数；`configure_solver.maxstep` 与 `solve_study.timeout_sec` 未在相应实现中使用；插件审批配置有与当前工具名不匹配的条目；CI 未显式安装该分支新增的 `mcp` extra。插件配置实际仍指向 COMSOL 服务，不代表已提供其他软件后端或完成客户端安装验收。异步/HPC/OAuth/全面 API 覆盖属于规划或模板，不能记为已验证能力。本次仅如实记录，不合并或修复该分支。

## 后续开发约定

按批读取与窗口索引已用现有数据贯通；5 帧历史、5 帧未来、步幅 5 是本轮管线样例，不是最终训练规格。共享 checkpoint 的兄弟分支与重叠窗口仍不能随机分散到训练/验证两侧。下一次模型实验前须补充独立轨迹族并明确任务与留出策略，或明确只做当前族内的管线试运行，不包装为独立泛化评估。

保持已验收基线、配置和历史证据不变；下一次实验先明确独立起点/轨迹族、隐藏信息、时间窗口、数据划分和数据预算，再扩展受控分支。参见 [研究计划](RESEARCH_PLAN.md)。当前不新增网格、时间步、扰动机制或训练任务。

文档随实际开发更新：将新能力记入 CHANGELOG，并提供固定提交、报告、真实回读、测试范围与数据位置。发布继续采用轻量方式，避免上传完整模型和张量，也避免用“全部验证”代替具体的验收范围。
