# 已有 M3 窗口的最小预测基线

日期：2026-11-06。结论：**执行与独立算术核验 PASS**。本轮在已有固定索引上完成末帧保持、末两帧线性外推和逐提前量/通道评估；没有拟合参数或预测精度通过门槛。数据仍只有一个祖先族，结果属于 train `pipeline_smoke`，不是独立留出泛化、自由滚动或 CFD 精度结论。

## 范围与正式入口

开发基线为 `ae59c6b1ad3e39a91249094332e34310d6289136`。新增独立的 `forecast_baselines.py`、`forecast_evaluation.py`，以及注册运行器、独立审计器和四组测试；复用既有 reader/window API，未修改求解、采样、族划分或历史指标实现。接口说明见 [FORECAST_BASELINES.md](../FORECAST_BASELINES.md)。

输入固定为 [既有 index.json](../evidence/data_usage_20261106/index.json)，文件 SHA-256：

```text
63004756d70b381a51a63106fd81035dbd6f5594ae3865d66ef1daf9543575b5
```

[注册配置](../../configs/m3_forecast_baseline.json) 在评估前确定两个方法、窗口规格与物理单位评分方式。入口同时核对加载时、开始评估前和结束后的索引身份，防止文件在读取与哈希快照间发生变化却仍报告旧身份。已有输出和位于输入目录内的新输出均拒绝；执行失败不留下成功报告。

```powershell
python -X utf8 scripts/evaluate_m3_baselines.py --output scratch/m3_forecast_review_01
python -X utf8 scripts/audit_forecast_baselines.py --index docs/evidence/data_usage_20261106/index.json --index-sha256 63004756d70b381a51a63106fd81035dbd6f5594ae3865d66ef1daf9543575b5 --report scratch/m3_forecast_review_01/report.json --output scratch/m3_forecast_audit_01.json
```

命令需要索引指向的原本地 NPZ/JSON；远程轻量副本可以复核代码、日志与结构化结果，不能独立重算未上传的完整数据。无需加载 MPH 或调用 COMSOL。

## 数据与预测协议

| 项目 | 实际执行 |
| --- | --- |
| 序列 | baseline、zero、a0025、a0050、a0100，共 555 帧/21 批 |
| 族与分区 | 共同 M3 祖先族全部 train；validation/test 为空 |
| 窗口 | 5 帧历史 + 5 帧未来，stride 5；0.2 s 保存间隔 |
| 时间解释 | 历史跨度 0.8 s，窗口起点间距 1 s；预测距末历史帧 0.2/0.4/0.6/0.8/1.0 s |
| 真实窗口数 | baseline 29，其余各 19，总计 105 |
| 目标计数 | 525 个预测帧实例，525 个唯一 `(trajectory_id,frame_index)`，重复目标 0 |
| 空间采样 | 161×97，每帧 13 个掩码点、15,604 个有效流体节点 |
| 各提前量/通道总体 count | 1,638,420 = 105×15,604 |

每条轨迹末尾 200 s 帧未组成完整窗口，不补帧、不移动窗口。最后一个窗口历史为 198.0–198.8 s，目标为 199.0–199.8 s。唯一目标计数不代表独立样本：相邻窗口和同源轨迹仍相关。

预测器只接收历史值、历史掩码、历史时间和已知预测时间表。末帧保持复制最后观测；线性外推用最后两帧及其实际时间差。未来值仅进入评分器，扰动幅值、来源路径、族标签不进入预测。每窗重新使用观测历史，没有将预测反馈为下一窗历史。

指标为未加权采样节点上的原始单位误差，逐提前量/通道累计 SAE、SSE、count 后求 MAE/RMSE。没有面积加权、速度/压力合并、归一化或压力去均值。baseline 因窗口更多而占更大权重；[完整结果](../evidence/forecast_baseline_20261106/report.json) 同时提供五条轨迹的独立指标。

## 实测结果

下表为总体 RMSE，速度单位 m/s、压力 Pa：

| 提前量 | 方法 | u | v | p |
| --- | --- | ---: | ---: | ---: |
| 0.2 s | 末帧保持 | 6.832153e-5 | 1.429281e-4 | 2.988471e-4 |
| 0.2 s | 末两帧线性外推 | 1.728192e-5 | 3.499673e-5 | 2.534682e-4 |
| 1.0 s | 末帧保持 | 3.211376e-4 | 6.744857e-4 | 1.808463e-3 |
| 1.0 s | 末两帧线性外推 | 2.321678e-4 | 4.729350e-4 | 2.983600e-3 |

1.0 s 的总体 MAE：

| 方法 | u（m/s） | v（m/s） | p（Pa） |
| --- | ---: | ---: | ---: |
| 末帧保持 | 1.196757e-4 | 2.372263e-4 | 6.360099e-4 |
| 末两帧线性外推 | 7.753885e-5 | 1.652384e-4 | 7.813133e-4 |

本数据上的线性外推在五个提前量均降低速度 MAE/RMSE；压力 RMSE 从 0.4 s 起高于末帧保持，压力 MAE 从 0.6 s 起更高。保留这一结果，没有调整方法或通过门槛来选出统一赢家。这不是训练效果，也不证明未知扰动泛化。

## 独立复算与身份核验

[独立审计](../evidence/forecast_baseline_20261106/independent_audit.json) 直接打开原始 NPZ，自行按窗口组装并实现两种公式，使用 `math.fsum` 累加，不调用正式 reader、预测器、累计器或 evaluator。逐项比较 5 条轨迹及总体的 5 个提前量 × 3 个通道 × 2 个方法 × 5 类指标，共 900 个指标/计数条目。

计数精确一致；数值固定使用 `atol=1e-14, rtol=1e-11`，提前量 `atol=1e-12 s`，没有根据结果修改容差。所有非零参考的最大相对残差为 **8.936978508853608e-16**。MAE/RMSE 各项的最大数值残差均为 **8.673617379884035e-19**（各自保留通道单位）；独立复算并非逐位相同，差别符合累加顺序舍入。

正式评估与独立审计分别核验 **68 个输入文件**：5 份序列清单和 21 批各自的 NPZ、metadata、bridge_response。实际访问输入前后哈希一致，代码/配置/索引前后身份也记录于 [执行文件](../evidence/forecast_baseline_20261106/evaluation_execution.json)。本次不重新读取大型模型、原生向量或再次证明其物理来源；既有来源链继续引用上一阶段固定证据。

## 测试与交付

最终 Windows/Python 3.10.21 命令：

```powershell
python -m pytest -q --strict-markers -m "not integration"
```

实际为 **768 passed, 13 deselected in 125.57s**，退出码 0，测试期间所记录源码哈希不变。其中包括本轮 108 项新增测试，不能与 768 相加。覆盖非有限值/掩码/时刻错误、解析信号、未来输入隔离、空留出拒绝、错误聚合、跨窗释放、来源变化及原始指标篡改。13 项真实 COMSOL 集成测试未运行。

- [完整回归 stdout](../evidence/forecast_baseline_20261106/regression_logs/stdout.txt)、[JUnit](../evidence/forecast_baseline_20261106/regression_logs/junit.xml)、[执行命令/退出码/源码身份](../evidence/forecast_baseline_20261106/regression_logs/execution.json)
- [真实评估命令与执行日志](../evidence/forecast_baseline_20261106/evaluation_logs/execution.json)、[独立审计命令与日志](../evidence/forecast_baseline_20261106/audit_logs/execution.json)
- [原始证据复制清单](../evidence/forecast_baseline_20261106/local_copy_manifest.json)

Ruff、仓库入口检查、`uv lock --check --offline` 和 Git diff 检查通过。远端使用现有锁定 uv 的 Windows/Linux Python 3.11 CI，排除 integration 和 artifact；最终运行状态以交付提交的 Actions 为准，不把本地 768 项记录写成远端执行数。

交付只含代码、配置、文档、小型指标和测试日志；未新增模型、完整张量或预测张量文件，未修改历史证据或复制开发库。Windows 目录发布的历史 WinError 5 仍未定位，本轮只读评估没有声称修复该问题。真实独立留出族、拟合模型及自由滚动评估仍未完成。
