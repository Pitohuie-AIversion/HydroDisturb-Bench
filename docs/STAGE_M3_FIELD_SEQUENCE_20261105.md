# 已有 M3 解的 151 帧连续时间基线

本轮连续序列采样通过：从已有 M3 保存解取得 **170.0–200.0 s、间隔 0.2 s、151 帧**，空间采样为 **161×97**，完整读回张量为 **`(151,3,97,161)`**，通道 `[u,v,p]`，float64。仅进行已有解的求值、采样与存储；未重新求解 CFD、改变求解网格、注入扰动、加入被动标量或训练模型。这个采样规格是连续数据流程的试运行规格，未被提升为最终训练空间分辨率。

代码及已验收五帧小样基线：`c34636166dc37fbee9c5ff68db289e9d8d006a54`。

## 结果与复核入口

| 检查 | 真实结果 |
|---|---|
| 请求时间范围与数量 | 170–200 s，151 帧，0.2 s 间隔 |
| 实际保存解编号 | 851–1001，逐项连续且无重复 |
| x/y 范围 | `[0,0.4] × [0,0.24] m`，递增并包含端点 |
| 规则采样间距 | 0.0025 m，两轴相同 |
| 每帧坐标行数 | 15,617 |
| 全部原始坐标行数 | 2,358,167 |
| 张量条目 | 7,074,501 = 7,068,612 有效流体值 + 5,889 掩码 NaN |
| 每帧几何/最终掩码点数 | 13，包含圆柱边界，跨帧及跨批一致 |
| 每帧原始无效点数 | 9，全部位于圆柱内部，跨帧一致 |
| 直接求值抽查 | 453 个时空点、1,359 个通道值，最大绝对差 `[0,0,0]` |
| 与上轮重叠 | 180/185/190/195/200 s，234,255 个条目一致，最大绝对差 `[0,0,0]` |
| 完整序列读回 | 通过，包含全部五个批次 |
| 源模型及历史文件 | M3 哈希一致，712 个历史文件 0 改动 |

正式运行耗时 **191.45 s**，包括模型检查、五次已有解加载/导出、逐帧校验、NPZ 写入与完整读回；退出码为 0。这不是新的 CFD 求解耗时。

- [序列机器报告](evidence/m3_field_sequence_20261105/sequence_report.json)、[序列清单](evidence/m3_field_sequence_20261105/sequence_manifest.json)。
- [全部证据入口与文件说明](evidence/m3_field_sequence_20261105/README.md)、[原生 ZIP 清单](evidence/m3_field_sequence_20261105/native_archive_manifest.json)。
- [第一批真实回读](evidence/m3_field_sequence_20261105/batch_0000/bridge_response.json)、[最后一批真实回读](evidence/m3_field_sequence_20261105/batch_0004/bridge_response.json)，中间三批在同级目录。
- [独立原始数据审计](evidence/m3_field_sequence_20261105/sequence_artifact_audit.json)、[归档解压审计](evidence/m3_field_sequence_20261105/archive_sequence_audit_final.json)、[历史文件最终哈希核对](evidence/m3_field_sequence_20261105/history_audit_final.json)。
- [91 项新旧采样测试](evidence/m3_field_sequence_20261105/sampling_regression.log)、[340 项完整离线回归](evidence/m3_field_sequence_20261105/offline_regression.log)，同目录 XML 和 `_execution.json` 保留实际命令、退出码及执行时间。

## 连续序列怎样组织

新入口将连续时间段拆成五个有序批次，保存每批 NPZ、元数据、真实回读 JSON 和原始 TXT；统一清单保存全时间轴、保存解编号、全张量形状以及各批文件哈希。没有重复保存另一份 52 MB 的总 NPZ。`load_full_field_sequence()` 检查所有批次后，在内存中拼回整个张量。

| 批目录 | 全序列零基索引，右端不含 | 帧数 | 时间范围（s） |
|---|---|---:|---|
| `batch_0000` | `[0,32)` | 32 | 170.0–176.2 |
| `batch_0001` | `[32,64)` | 32 | 176.4–182.6 |
| `batch_0002` | `[64,96)` | 32 | 182.8–189.0 |
| `batch_0003` | `[96,128)` | 32 | 189.2–195.4 |
| `batch_0004` | `[128,151)` | 23 | 195.6–200.0 |

`values[t,c,iy,ix]` 对应 `times[t]、y[iy]、x[ix]`。每批仍保留已经验收的 `values/mask/cylinder_mask/invalid_mask/x/y/times` 七个 NPZ 数组。完整读取结果沿用相同键；`metadata` 为序列清单。

批次覆盖必须从索引 0 连续到 151，不能重叠、遗漏或附加未登记批目录。除各批自身通过旧数据契约外，还比较模型标识与哈希、Dataset–Solution、几何、单位、坐标轴及最终掩码。保存解编号要求逐帧增加 1，避免仅检查等间隔请求列表却漏掉模型实际保存帧。完整读回再次验证每批 NPZ、metadata、原始 response 的 SHA-256，并检查实际时间轴与全张量形状。

请求时刻与实际保存时刻分别保留；本次最大差为 **`2.842170943040401e-14 s`**，属于浮点表示差异，低于既定 `1e-12 s` 绝对容差。没有最近时刻代替，也没有请求时间间的解插值。

上轮五个参考时刻对应本序列零基索引 **50、75、100、125、150**。比较覆盖这些帧全部三个通道及掩码，而不只比较探针。参考 NPZ 的已验收 SHA-256 为：

```text
d9646e1bf03687ea9a6e10a0275f64d750cc4e78ca02a89c2f5942593cf77570
```

## 最小实现变化与真实来源

- Java 新增 `export-full-field-sequence-batch`，每批允许 1–32 帧，复用已有导出和直接求值处理段。旧 `export-full-field-sample` 继续限制 3–5 帧；旧 Python 小样入口继续要求两种分辨率。
- `field_sampling.py` 将已有单网格解析、来源校验、掩码、NPZ 写入与读回提取为共享函数，原有 59 项小样测试全部通过。
- `field_sequence.py` 增加序列分批、跨批连续性校验、重叠比较和完整读取。
- `scripts/sample_re100_m3_sequence.py` 固定本轮源模型、参考数据哈希及试运行规格，执行前调用真实模型检查。
- `tests/test_field_sequence.py` 新增 32 项序列测试，覆盖缺帧/重复、跨批来源与坐标/掩码变化、错误解编号、重叠不一致、失败留证、清单篡改和哈希不符。

每批真实 COMSOL 回读仍给出 `Data → dset1 (Solution) → sol1`，执行导出后读取实际表达式 `[u,v,p]`、单位 `[m/s,m/s,Pa]`、解编号、坐标数组。原始 TXT 的明确单位和时间表头同时参与检查。新增 `batch_mode:true` 区分新入口；`operation=export_full_field_sample` 表示两入口复用同一采样原语，不表示重新运行了旧的双分辨率工作流。

每个时刻独立 EvalPoint 读取 `[u,v,p,x,y,t]`，实际单位为 `[m/s,m/s,Pa,m,m,s]`；坐标为 `(0.05,0.12)`、`(0.15,0.12)`、`(0.30,0.18) m`。直接求值、全域重叠和独立原始数据重建的零差值，证明与同一保存离散解的一致性，不构成真实 CFD 点值误差为零的结论。

源模型仍为 `runs/re100_s1b2_m3_20261105_verified/re100_s1b2_m3_solved.mph`，SHA-256：

```text
15e841fc4240d93db877932c1b371646426f1229bdb40d0335b0ed7609d145b4
```

## 原生归档与读取

完整原生运行目录含 **170 个文件**，分为六个独立 ZIP：一个根目录索引包和五个批数据包。每包小于 29 MB；ZIP 和每个内部文件均有 SHA-256。独立审计将六包合并解压到新目录后，重新核对全部原始 TXT 与 NPZ，并调用正式 loader 完整读回。

必须把 **六包都解压到同一个新目录**。仓库单独展示的 JSON/日志用于浏览并规范化为 LF；序列清单中 metadata/response 的字节哈希指向 ZIP 内原生文件，不能混用展示副本与原生批文件。`.gitattributes` 及之前的归档规则没有修改。

另附 `execution_logs.zip` 保存执行日志、JUnit 和命令记录的原始字节，不参与六个序列数据包的合并。定向测试执行记录的 `stdout_sha256` 对应包内原始 CRLF 日志，`display_stdout_sha256` 对应 LF 展示日志，避免将二者的字节哈希混用。

可使用归档附带的工具核对、解压及重新审计：

```powershell
python docs/evidence/m3_field_sequence_20261105/archive_sequence_audit.py `
  --evidence-dir docs/evidence/m3_field_sequence_20261105 `
  --output-dir runs/m3_sequence_review_extract `
  --reference-dir runs/re100_m3_field_sample_20261105_verified/grid_161x97 `
  --output scratch/m3_sequence_review_audit.json
```

参考目录可来自上轮 `native_sample.zip` 解压结果。新审计输出路径和解压目标均应尚不存在。异地没有源 `.mph` 时，独立审计会记录 `physical_model_bytes_checked=false`；本机本轮审计为 true。

在已安装本项目（或将 src 加入 Python 导入路径）的环境中，读取整个序列不启动 COMSOL：

```python
from hydro_disturb_bench.field_sequence import load_full_field_sequence
data = load_full_field_sequence("runs/m3_sequence_review_extract")
uvp = data["values"]            # float64 (151, 3, 97, 161)
t = data["times"]               # 实际保存时刻，151 项
x, y = data["x"], data["y"]
invalid = data["mask"]           # True 为无效；对应 uvp 为 NaN
```

重新从已有模型采样时使用新的运行目录：

```powershell
python scripts/sample_re100_m3_sequence.py --output runs/m3_sequence_new_review
python -m pytest -v tests/test_field_sequence.py tests/test_full_field_sampling.py
python -m pytest -q -m "not integration"
```

## 测试与可靠性边界

新旧定向测试：**91 passed in 10.30s**，退出码 0。完整离线回归：**340 passed, 30 deselected in 33.18s**，退出码 0。30 项集成测试本轮未运行，避免重跑包含 CFD 求解的旧测试。真实已有解导出、453 个直接求值点和独立全数据审计提供本轮集成证据；不将上述数字表述为“所有测试都执行”。

独立历史审计检查了 **712 个文件**：原有 646 项，以及上轮小样运行文件、其已提交证据和报告；全部哈希不变。独立数据审计不导入生产解析函数，完整重建 151 个 TXT 的全部张量条目。本轮没有采样或发布重命名失败；上一轮 Windows `WinError 5` 仍是尚未定位根因的可靠性记录，不因本轮成功而宣布修复。失败时依然保留 staging/原始返回和错误记录，不覆盖目标目录。

首次归档核验在独立数据检查后因本地包尚未安装而未能导入生产 loader；仅为审计脚本补充仓库 src 路径发现，使用全新目录解压复核后通过。初次退出码 1 与最终退出码 0 的日志均保留，未重新调用 COMSOL 或修改数据。

源码通过 Ruff 与 Git diff 检查。归档保留 COMSOL 日志中的原始行尾空白；全量 diff 的对应格式提示不代表源码错误，未为消除提示修改原始执行日志。本轮没有未解决的连续性、来源或数值读回阻断项。后续扩大空间规格、时间范围或建立训练切分，需要另行明确数据规格；不由本轮试运行提前决定。
