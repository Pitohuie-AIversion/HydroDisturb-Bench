# COMSOL 原生启动恢复、真实单位来源与 Case C 验收（2026-11-04）

**状态：真实单位来源核验 PASS；Case C 已完成并通过。** 本记录接续 `6cc9458f0d127bda98e79343c0571ed559c7b6bc`，解决其真实 COMSOL 启动阻塞。此前失败记录保留，未把历史伪来源声明改称真实读取。

## 运行环境修复

`csutil.dll` 及其依赖都存在。临时将安装中原有 tbbmalloc 放入 batch 可执行文件目录后，csutil 加载成功而下一个 csjni 依赖继续失败，定位为依赖目录查找问题；唯一新增副本已按哈希校验后移除，安装恢复原状。

生产桥接层现在仅在 batch 同时报 `Failed to start COMSOL`、`FlNativeException` 且没有任何桥接 JSON 时，使用官方支持的单进程 Java API。新 `ComsolStandalone.java` 在进程内注册 COMSOL bin/lib/JRE bin DLL 目录，调用 initStandalone，再执行同一个 ComsolBridge。绘图导出启用 plotting support；进度以 String-null 重载写 stdout。模型和输出参数通过 UTF-8/base64 传递，避免原始中文/emoji 路径丢失。安装与全局环境未被更改；结构化模型/求解错误不重试。

修改文件：`src/hydro_disturb_bench/comsol_java/bridge.py`、新增 `ComsolStandalone.java`、`tests/test_comsol_api_contract.py`、`docs/COMSOL_API_INTEGRATION.md`。本轮沿用上一提交的严格前置入口和 Case C 脚本，没有改动积分、四指标规则或可视化模块。

该修复恢复了项目的正常 Python 命令执行。原生 batch 本身在这台机器上仍可能报错，响应中的 `launch_mode=standalone_java_api` 与 `batch_startup_failure` 透明保留启动路径；不声称修改了系统级 COMSOL 启动器。临时类目录和安装路径须为 ASCII，模型与数据路径允许 Unicode。

## 真实速度单位来源

A、B、C 均通过正式 run_prechecks。各次从已保存解重新导出到新目录，不重新求解 A/B。回读与文件、模型哈希绑定，1001 个时间点和两个速度通道全部与待验收文件一致，五个时刻的独立 EvalPoint 比较差值为 0。

实际 Java 返回的 u 通道字段（A/B/C 均为以下节点关联，模型及文件哈希分别记录）：

```json
{
  "export_tag": "exp_unit_u",
  "plotgroup_tag": "pg_unit_u",
  "feature_tag": "point",
  "expression": "u",
  "unit": "m/s",
  "feature_data": "parent",
  "dataset_tag": "cpt_unit_evidence",
  "dataset_type": "CutPoint2D",
  "point_x": "0.15",
  "point_y": "0.12",
  "solution_dataset": "dset1",
  "solution_type": "Solution",
  "solution_tag": "sol1"
}
```

v 通道真实回读为 `exp_unit_v / pg_unit_v / point / expression=v / unit=m/s`。没有设置或补写预期单位。完整原始返回包含模型路径、模型 tag、文件路径和 SHA-256：[A](evidence/unit_source_verified_20261104/a_probe_readback.json)、[B](evidence/unit_source_verified_20261104/b_probe_readback.json)、[C](evidence/unit_source_verified_20261104/c_probe_readback.json)。[A 正式核验报告](evidence/unit_source_verified_20261104/a_prechecks.json)；B/C 核验包含在 [Case C 完整报告](evidence/unit_source_verified_20261104/case_c_report.json) 中。

## Case C 设置与实际步进

实际命令：

```powershell
& "D:/Program Files/Anaconda/envs/seagent/python.exe" scripts/verify_s1b1_prechecks.py
& "D:/Program Files/Anaconda/envs/seagent/python.exe" -u scripts/run_re100_s1b1_case_c.py --output-dir runs/re100_s1b1_case_c_20261104_verified
```

执行顺序：A 正式前置通过 → B 模型检查和正式前置通过 → 从 B 加载并求解 C → 导出诊断 → C 正式前置通过 → B/C 设置与五周期物理指标比较。两条命令均退出 0。输出目录是本轮新目录，已有目录会被拒绝，历史产物未覆盖。

| 实际读取项 | B | C |
|---|---|---|
| rtol | 1e-4 | 1e-4 |
| maxstepconstraintbdf | auto | const |
| maxstepbdf | 0.1（auto 下非硬上限） | 0.05 s |
| timemethod / order | bdf / 1–2 | bdf / 1–2 |
| tstepsbdf | intermediate | intermediate |
| 输出时间 | range(0, 0.2, 200) | range(0, 0.2, 200) |
| 网格单元 / 顶点 | 7493 / 3944 | 7493 / 3944 |
| 实际日志步数 | 2688 | 4091 |
| 实际 dt 范围 | 0.0017176–0.31064 s | 0.0017176–0.05 s |
| t≥160 s 实际 dt | 0.062762 s | 0.05 s |

C 日志步号完整覆盖 1…4091，无重复、时间严格递增；每条记录均满足 `0 < dt ≤ 0.05`。最终内部时间为 200.03 s，输出仍为 0–200 s 的 1001 点。[完整 C 求解日志](evidence/unit_source_verified_20261104/case_c_solver_run.txt) 和 [求解器元数据](evidence/unit_source_verified_20261104/case_c_solver_metadata.json) 可复核。

所有可读非目标求解器字段（包括绝对容差的耦合策略、初始步设置）、参数、网格和组件树均相同。保留核验边界：组件树包含边界选择和材料 tag，未逐项枚举所有材料/边界属性；这些设置的保留还依据直接加载 B 且仅调用 rtol/maxstep setter，不能扩大为全部属性均逐项回读。原始 UNOBTAINED 字段仍如实保留。

## B/C 四指标

两组各取最后五个稳定完整周期：B 窗口 [162.635035037, 193.815773936] s，C 窗口 [168.152139267, 199.319744778] s，均通过原有稳定性门槛。

| 指标 | B | C | C 相对 B |
|---|---:|---:|---:|
| St（上穿零点） | 0.159429049 | 0.159496229 | +0.042138% |
| 平均 Cd | 1.346992250 | 1.346962504 | -0.002208% |
| 升力半振幅 | 0.312789185 | 0.312976165 | +0.059778% |
| 去均值升力 RMS | 0.221413885 | 0.221526759 | +0.050979% |

四项相对差异均小于既定 1% 门槛，结论为 `INSENSITIVE_WITHIN_TESTED_RANGE`。这对应固定 rtol 下本次内部步长约束比较，不推广为网格收敛或所有参数范围结论。原始新数据：[C 升力](evidence/unit_source_verified_20261104/case_c_lift_coeff.txt)、[C 阻力](evidence/unit_source_verified_20261104/case_c_drag_coeff.txt)。

## 回归与保全

新增 6 项启动回退/Unicode 契约测试通过；S1-B1 模块 25 项通过（含 9 个入口级来源反例/正常场景）。完整回归 **134 passed in 347.37s**，包含真实 COMSOL 检查、参数修改、短求解、导出等集成测试，无失败/跳过。非集成测试单独运行亦为 104 passed、30 deselected。见 [完整回归日志](evidence/unit_source_verified_20261104/full_regression.txt) 和 [模块日志](evidence/unit_source_verified_20261104/module_tests.txt)。

全库回归后再次核对原始 case、models、S1-A、A、B 及既有步长实验目录：**122 个历史文件 SHA-256 全部一致**，见 [逐文件记录](evidence/unit_source_verified_20261104/history_immutability.json)。`git diff --check` 通过。证据中的 TXT 展示副本仅规范化行尾空白，原日志和数据完整保留于 runs/scratch；[展示副本哈希](evidence/unit_source_verified_20261104/evidence_hashes.json) 可复核。本轮 C 模型 SHA-256：`D29F8A55695B42B2FC2B351512FBE85FBE1042BDF380700F44FAB402B695C5C2`。已知运行限制仅为上面的启动路径和属性回读边界；本轮不开展 Case D 或网格加密。

本轮提交包含上述修复、报告和可审计证据；真实 commit hash 见交付消息与 Git 日志。未 push。
