# 单位来源入口修复与 Case C 执行记录（2026-11-04）

**验收状态：FAIL / 未关闭。** 代码入口已去除固定单位赋值，但真实 COMSOL 核验在原生库加载阶段失败；尚无成功的真实单位回读片段。按任务门槛，**本轮未启动 Case C**。不开展 Case D 或网格加密。

基线提交：`21c82f4a6d5ecb2e9b7db6caa341576f1a3da399`。本轮不 push；修复提交哈希由交付消息和 Git 日志提供。

## 修改文件与证据链

- `scripts/verify_s1b1_prechecks.py`：移除两行固定赋值以及未关联的 `res.probe_units` 回退。优先使用文件表头单位，明确的 `cm/s`、`ft/s` 等被保留并拒绝。表头缺单位时请求独立目录重新导出已有解，核对所有时间与数值样本，并以模型/文件 SHA-256、实际节点、表达式、坐标和 Dataset 链绑定证据。每次报告使用新目录，读取失败返回 FAIL、unit=unknown、unit_source=none。
- `src/hydro_disturb_bench/comsol_java/ComsolBridge.java`：导出后沿 `export.plotgroup → plot.feature → plot.data → CutPoint.data → Solution.solution` 回读；额外回读 PointGraph 的 data，拒绝不同的 Dataset 覆盖。没有设置速度单位。保存实际节点 tag、表达式、unit、文件名、模型 tag 和 Dataset。JSON 转义非 ASCII，保持路径可比较；缺失实际时间输出 null。
- `src/hydro_disturb_bench/comsol_java/bridge.py`：新增可选 `probe_output_dir`，只允许新目录，附加实际模型和文件哈希；发生异常也检查源模型哈希。
- `tests/test_re100_s1b1_time_convergence.py`：9 个新增入口级场景经过 run_prechecks、真实 TXT 读取和关联校验；仅外部 COMSOL 读取接口为故障注入。场景包括缺失、cm/s、正确 m/s、读取失败、错误文件、错误 Dataset、ft/s 表头、仅来源标签和 PointGraph Dataset 覆盖。
- `scripts/run_re100_s1b1_case_c.py`：新增有前置门槛的 Case C 脚本；从 B 加载，rtol=1e-4，const/0.05 s，独立目录。计划核对实际步长日志、非目标设置、历史哈希和最后五个稳定完整周期；当前未执行，不能视为已验证的 Case C 运行结果。
- `docs/STAGE_S1B1_VERIFICATION.md`：撤回旧单位来源 PASS 声明，保留 A/B 物理比较记录。

## 真实前置核验

实际命令（本机默认 python 没有 pytest，使用项目原有 seagent 环境）：

```powershell
& "D:/Program Files/Anaconda/envs/seagent/python.exe" scripts/verify_s1b1_prechecks.py
```

退出码 1。[正式入口原始失败报告](evidence/unit_source_20261104/real_precheck_failure.json) 的原始字段为：

```json
{"status": "FAIL", "unit": "unknown", "unit_source": "none", "comparison_table": []}
```

COMSOL 原生日志：

```text
java.lang.UnsatisfiedLinkError: D:\Program Files\COMSOL64\Multiphysics\lib\win64\csutil.dll: Can't find dependent libraries
```

[完整原生日志](evidence/unit_source_20261104/native_startup_failure.txt) 与 [诊断记录](evidence/unit_source_20261104/runtime_diagnosis.json) 已保存。进程级 DLL 路径和临时 Java agent 尝试未使执行检查器的子进程正常运行；未修改 COMSOL 安装。模型未成功加载，**没有实际读取成功的速度单位数据，不以测试字典代替集成证据**。

## 验证结果

- 探针定向测试：14 passed（含 9 个新增入口场景）。
- S1-B1 模块：25 passed。
- Java 编译成功，最终 bytecode 68,044 bytes；这只证明编译成功。
- 完整回归：**116 passed, 12 failed in 75.86s**；12 项失败均在真实 COMSOL 启动环节。见 [完整回归日志](evidence/unit_source_20261104/full_regression.txt)、[定向测试日志](evidence/unit_source_20261104/probe_tests.txt)、[模块日志](evidence/unit_source_20261104/module_tests.txt)。
- `git diff --check`：通过。
- 原始、S0、S1-A、A、B 五个模型的哈希均与历史预期一致：[哈希记录](evidence/unit_source_20261104/protected_model_hashes.json)。

A/B 复算继续为 INSENSITIVE_WITHIN_TESTED_RANGE，各取最后五个稳定完整周期；[完整比较结果](evidence/unit_source_20261104/ab_comparison.json)：

| 指标 | A | B | 相对差异 |
|---|---:|---:|---:|
| St | 0.158489 | 0.159429 | +0.5929% |
| 平均 Cd | 1.346769 | 1.346992 | +0.0166% |
| 升力半振幅 | 0.311061 | 0.312789 | +0.5556% |
| 去均值升力 RMS | 0.220254 | 0.221414 | +0.5265% |

复算调用原有 main，仅将报告路径改为新的 scratch/unit_source_ab_comparison.json，避免覆盖历史报告。未修改积分、四指标门槛或可视化模块。

## 未解决项与恢复入口

必须先恢复本机 COMSOL 原生启动，完成新的 Java 导出节点真实读取及单位/文件关联验收。当前同次导出 getter 路径只有编译和注入测试证据，不能宣布实际来源已核验。真实前置通过后才能执行并验收 Case C：

```powershell
& "D:/Program Files/Anaconda/envs/seagent/python.exe" scripts/run_re100_s1b1_case_c.py
```

该脚本会先重新核验 B，失败时停止，成功才启动 C；当前不存在本轮 B/C 参数及物理指标实测对照。
