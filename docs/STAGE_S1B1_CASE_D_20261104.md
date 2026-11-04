# Case D：C/D 内部步长上限减半对照（2026-11-04）

**状态：Case D 已完成，C/D 对照 PASS，完整回归 166 项通过。** 两组各自的最后五个完整周期均稳定，四指标变化均小于 0.1%，通过既定 1% 门槛。当前网格下的时间积分敏感性验证阶段收尾；完整回归后复核的 169 个历史文件 SHA-256 全部保持一致。基线为已验收提交 `b8477d7342615ae0cac5d7f996c9c445cc8f6fb0` 的 Case C。此次只开展 Case D，不重算 A/B/C，不开展网格加密。

## 执行范围与入口

新增 [Case D 入口](../scripts/run_re100_s1b1_case_d.py) 和 [定向测试](../tests/test_re100_s1b1_case_d.py)。沿用已验收的 COMSOL 桥接、真实单位来源前置检查、整周期积分、稳定性检查及四指标比较函数，没有改动上述基础模块。

实际运行命令：

```powershell
& "D:/Program Files/Anaconda/envs/seagent/python.exe" -u scripts/run_re100_s1b1_case_d.py --output-dir runs/re100_s1b1_case_d_20261104_verified
```

入口先读取 C 的实际模型树，发现瞬态 study，再读取 C 的已有解并独立重导出探针以核验单位来源。C 的正式前置检查和 `rtol=1e-4 / const / 0.05 s` 回读全部通过后，才调用 `run_study`。调用只指定 `maxstep_override="0.025"`；不传入 rtol 或输出时间表覆盖值。D 使用新目录，已有目录会被拒绝。实际内部步长可以自适应减小，0.025 s 是上限；COMSOL 官方定义 `maxstepbdf` 在 `maxstepconstraintbdf=const` 时作为 BDF 最大步长生效。见 [COMSOL 6.4 Time API 文档](https://doc.comsol.com/6.4/doc/com.comsol.help.comsol/comsol_api_solver.51.51.html)。

本次命令退出 0；真实发现并执行的 study 为 `std1`，解为 `sol1`，沿用 `standalone_java_api` 启动路径。D 的求解调用耗时 423.916 s（包括启动和桥接；COMSOL 返回的内部耗时为 400.919 s）。C 的历史同口径调用耗时为 220.306 s；D 的内部步数为 C 的 1.97 倍，调用耗时为 1.92 倍。

## 实际设置、单位来源与步进

以下为 C/D 保存模型的实际读取结果，详见 [完整报告](evidence/case_d_20261104/case_d_report.json)、[D 求解器回读](evidence/case_d_20261104/case_d_solver_metadata.json)、[C 模型树](evidence/case_d_20261104/case_c_model_inspection.json) 和 [D 模型树](evidence/case_d_20261104/case_d_model_inspection.json)。

| 实际读取项 | Case C | Case D |
|---|---|---|
| rtol | 1e-4 | 1e-4 |
| maxstepconstraintbdf | const | const |
| maxstepbdf | 0.05 s | 0.025 s |
| timemethod / order | bdf / 1–2 | bdf / 1–2 |
| tstepsbdf | intermediate | intermediate |
| 输出时间 | range(0, 0.2, 200) | range(0, 0.2, 200) |
| 网格单元 / 顶点 | 7493 / 3944 | 7493 / 3944 |
| 实际日志步数 | 4091 | 8067 |
| 实际 dt 范围 | 0.0017176–0.05 s | 0.0017176–0.025 s |
| t≥160 s 实际 dt | 0.05 s | 0.025 s |

所有非目标求解器回读字段相同，包括 `atolfactor="comp1_p, 1, comp1_u, 0.1"`、`atolglobalfactor="0.05"`、`atolglobalmethod="scaled"` 与初始步设置，完整绝对容差字段保存在 JSON。参数、网格信息、可读取物理字段及组件树比较一致。

[D 实际步进日志](evidence/case_d_20261104/case_d_solver_run.txt) 的接受步号连续覆盖 1…8067，无重复，记录时间严格递增；每条实际步长均为正且不超过 0.025 s，最终内部时间为 200 s。求解响应确认 1001 个输出时刻、输出范围 0–200 s、19,326 个自由度、无 NaN/Inf。步进检查拒绝未到达 200 s 的日志尾部，不把仅接近终点视为完整运行。

C/D 均通过正式探针前置核验。各自从保存解向独立目录重导出，两通道全部 1001 个样本与待验证时序在 `1e-12` 内一致；180、185、190、195、200 s 的独立 EvalPoint 对照差值均为 0，且实际时间与目标时间相同。来源证据绑定实际模型与文件 SHA-256。原始返回：[C 单位来源](evidence/case_d_20261104/c_probe_readback.json)、[D 单位来源](evidence/case_d_20261104/d_probe_readback.json)。实际 D 的 u 通道片段为：

```json
{
  "model_tag": "Model",
  "export_tag": "exp_unit_u",
  "plotgroup_tag": "pg_unit_u",
  "feature_tag": "point",
  "expression": "u",
  "unit": "m/s",
  "dataset_tag": "cpt_unit_evidence",
  "dataset_type": "CutPoint2D",
  "point_x": "0.15",
  "point_y": "0.12",
  "solution_dataset": "dset1",
  "solution_type": "Solution",
  "solution_tag": "sol1"
}
```

v 通道回读为 `exp_unit_v → pg_unit_v → point`、表达式 `v`、单位 `m/s`，Dataset—Solution 关联相同；来源为 COMSOL 实际节点读取，沿用已验收实现，不设置或补写速度单位。

读取范围沿用已验收边界：检查可读取的非目标求解器字段、参数、网格信息与组件树；组件树含边界选择和材料 tag，未逐项枚举所有材料和边界属性。其他设置的保留还依赖直接加载 C、仅修改指定最大步长这一执行路径，不能表述为全部属性均逐项独立核验。无法读取的字段保留 `UNOBTAINED`。

## C/D 稳定周期与四指标

从 C/D 原始时序分别提取最后五个完整周期，沿用原有稳定性门槛。四指标差异按 `(D-C)/C × 100%` 计算，每项阈值保持 1%，没有调整阈值。

| 周期稳定性检查 | Case C | Case D | 原有门槛 |
|---|---:|---:|---:|
| 完整周期数 | 5 | 5 | 至少 5 |
| 窗口起点 / s | 168.152139267 | 168.040590636 | 各自最后五周期 |
| 窗口终点 / s | 199.319744778 | 199.191521392 | 各自最后五周期 |
| 周期跨度 / 均值 | 0.013873% | 0.006620% | <1% |
| 升力半振幅跨度 / 均值 | 0.113822% | 0.133201% | <2% |
| 周期平均 Cd 跨度 / 均值 | 0.001110% | 0.002085% | <1% |

两组均为 `STABLE`。稳定性表中的跨度是五周期最大值减最小值后除以均值；JSON 的 `st_drift_pct` 按周期长度计算。

| 验收指标 | Case C | Case D | D 相对 C | 判定 |
|---|---:|---:|---:|---|
| St（上穿零点） | 0.159496229 | 0.159581605 | +0.053529% | PASS |
| 平均 Cd | 1.346962504 | 1.346933379 | −0.002162% | PASS |
| 升力半峰峰振幅 | 0.312976165 | 0.313211693 | +0.075254% | PASS |
| 去均值升力 RMS | 0.221526759 | 0.221733439 | +0.093298% | PASS |

四项均通过 1% 门槛，最大绝对变化为 RMS 的 0.093298%。峰值法 St 仅作为交叉诊断：C 为 0.159501313、D 为 0.159584118，相对变化 +0.051915%；不替代既定四指标中的上穿零点 St。

时序复核入口：[已验收 C 升力系数](evidence/unit_source_verified_20261104/case_c_lift_coeff.txt)、[已验收 C 阻力系数](evidence/unit_source_verified_20261104/case_c_drag_coeff.txt)、[D 升力系数](evidence/case_d_20261104/case_d_lift_coeff.txt)、[D 阻力系数](evidence/case_d_20261104/case_d_drag_coeff.txt)。C 的历史归档未改动。完整统计和每周期数据保存在 [比较报告](evidence/case_d_20261104/case_d_report.json)。

## 测试与历史保全

本机已实际执行：D 定向测试 **32 passed in 0.95s**，原有 S1-B1 模块测试 **25 passed in 1.46s**，真实 COMSOL 接口回归 **4 passed in 96.72s**，完整回归 **166 passed in 314.40s (0:05:14)**，无失败或跳过。

保存产物的只读复核已通过：重新解析实际步进日志，独立检查全部 8067 步及 200 s 终点；核对 C/D 模型和导出文件哈希、七组时序的有限性及 0.2 s 输出间隔；从原始 C/D 升阻力时序再次调用既有统计函数，结果与运行报告在 `1e-12` 内一致，并独立重算四指标相对变化。详见 [产物核对](evidence/case_d_20261104/artifact_audit.json)。该步骤没有重新求解 CFD。

D 入口前后核验的原始模型、S0、S1-A、A、B、C 六个模型哈希均保持一致。C 来源模型和本轮 D 模型的 SHA-256：

```text
C  D29F8A55695B42B2FC2B351512FBE85FBE1042BDF380700F44FAB402B695C5C2
D  75183E3B4BE7AAAF1A2EEC9F09079F5AE0497ABA5B868268DE0EBA782525EDAB
```

D 模型独立保存在 `runs/re100_s1b1_case_d_20261104_verified/re100_s1b1_case_d_solved.mph`；A/B/C 模型和原始结果继续保留。

完整回归后再次核对 D 启动前记录的 **169 个历史文件**，SHA-256 全部一致；范围包含原始模型、S0、S1-A、A、B、既有步长实验、已验收 C 产物及其证据归档。新归档包含 26 份证据文件及 1 份清单，共 27 个文件。

- [D 定向测试](evidence/case_d_20261104/case_d_tests.txt)
- [原有时间收敛模块测试](evidence/case_d_20261104/time_convergence_tests.txt)
- [COMSOL 接口回归](evidence/case_d_20261104/comsol_integration_tests.txt)
- [完整回归](evidence/case_d_20261104/full_regression.txt)
- [历史文件 SHA-256 保全记录](evidence/case_d_20261104/history_immutability.json)
- [产物核对](evidence/case_d_20261104/artifact_audit.json)
- [归档证据清单](evidence/case_d_20261104/evidence_manifest.json)

归档文件使用 UTF-8 无 BOM 和 LF 行尾；仅 TXT 展示副本去除行尾空白。清单分别记录原文件与归档副本的 SHA-256。原始 runs/scratch 产物保持不变，JSON 内容保持一致。本文的模型哈希和真实回读中的文件哈希指向原始产物，归档副本哈希用于核验仓库中的展示文件。

## 阶段结论

实际结论为 `INSENSITIVE_WITHIN_TESTED_RANGE`，`acceptance_status=PASS`，`phase_can_close=true`。C/D 均通过五周期稳定性检查，四指标绝对相对变化均不超过 1%，因此关闭当前网格下的时间积分敏感性验证阶段。后续统一使用 C 的 `rtol=1e-4、maxstepconstraintbdf=const、maxstepbdf=0.05 s` 及现有绝对容差策略；该设置在本次对照下通过门槛且计算量较小。

这只说明当前网格及固定容差策略下，两档内部步长上限对所选指标的影响小；不能解释成相对真实解的误差小于 0.1%，也不构成网格或计算域独立性结论。下一阶段应转向空间离散验证，本轮没有开展网格加密，也没有更改初始条件、入口函数或控制方程。

本轮不 push；实际提交哈希随交付消息提供。
