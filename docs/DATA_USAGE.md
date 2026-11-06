# 连续场数据的读取、窗口与划分

本层消费已验收的 NPZ 批次，不启动 COMSOL。数据仍是 `float64 (T,3,Ny,Nx)`，通道 `[u,v,p]`、单位 `[m/s,m/s,Pa]`；掩码内 NaN 保留，不自动填零、归一化或计算全数据统计量。

## 按批和按窗口读取

```python
from hydro_disturb_bench.field_sequence import FieldSequenceReader

reader = FieldSequenceReader(sequence_directory)
summary = reader.verify_all()  # 遍历全部批次，返回小型校验结果，不拼接张量
for batch in reader.iter_batches():
    consume(batch["values"], batch["mask"], batch["times"])
    del batch
window = reader.read_frames(25, 35)  # 半开区间 [25,35)，允许跨批
```

构造 reader 时检查清单的帧覆盖、时间、解编号、形状及目录。访问时固定清单字节哈希，对相关批次的 NPZ、metadata 和 bridge_response 再核对哈希，并复用既有单批解析校验。跨批来源、坐标、掩码与保存时间契约继续生效。

reader 不缓存历史场数组，只保留静态坐标、掩码及来源。窗口直接分配所需输出、逐批拷贝；所需场内存随单批与请求窗口大小增长，不随整条序列增长。清单和窗口索引仍是内存中的小型元数据；NPZ 内部解压和校验会分配临时数组。调用者若自行收集所有 yielded 批次，会自行占用整序列内存。

`read_frames()` 只证明访问批次已通过；`iter_batches()` 未遍历完也不代表整条序列通过。只有 `verify_all()` 成功返回才完成全批校验。旧 `load_full_field_sequence()` 保留全量返回行为，并复用相同校验；旧导出流程未整体改为流式。

## 先划分轨迹族，再构造窗口

`hydro_disturb_bench.data_windows` 提供：

| 接口 | 契约 |
| --- | --- |
| `TrajectoryRecord` | 轨迹 ID、族 ID、现存序列目录、清单哈希、祖先模型及可选 checkpoint/原生状态哈希 |
| `WindowSpec(history_frames, forecast_frames, stride=1)` | 三项均为正整数；不接受布尔值 |
| `split_trajectory_families(records, assignments)` | 显式指定每族的 `train` / `validation` / `test`，拒绝缺项和多余分配 |
| `build_window_index(records, assignments, spec)` | 先确认族身份及全批有效，再生成轻量索引，不复制场张量 |
| `save_window_index(index, path)` | 重新校验结构，以独占方式写入 JSON，已有文件不覆盖 |
| `load_window_index(path, expected_sha256=...)` | 检查文件身份、payload 摘要及按规格重建的窗口/划分 |
| `read_window(index, window_id)` | 绑定实际清单、验证范围，只读取目标窗口的批次 |

族划分采用保守规则：重复目录或轨迹 ID 拒绝；共享清单、模型、祖先、checkpoint、原生状态或 NPZ 身份的记录不能标成不同族。模型哈希跨角色比较，避免把一条轨迹的直接来源当作另一条轨迹的“独立祖先”。同一族不能跨分区，窗口不能跨轨迹；历史和未来相邻，端点均为半开索引。

祖先信息不能由场张量自动推断。通用库检查调用者提供的来源是否相互冲突；真实 M3 入口额外读取固定计划、prepare、分支 execution 与 U/UDot 身份 JSON，证明共同来源后才构造记录。索引摘要不是数字签名；复核固定产物时应使用独立记录的文件 SHA-256。

`read_window()` 仅返回八个学习器字段：`history`、`future`、`history_mask`、`future_mask`、`x`、`y`、`history_times`、`future_times`。幅值、族 ID、原生状态、模型路径及索引本身不作为预测输入。索引属于管理和审计元数据，不能直接整体喂给学习器。

## 已有 M3 数据的入口

```powershell
python -X utf8 scripts/prepare_m3_data_usage.py --output scratch/m3_data_usage_review_01 --audit-reference-ref 4b7f74c658739908532b15277bb46a6495b02aad
```

命令默认使用 [注册配置](../configs/m3_data_usage.json)，数据根目录来自 `--data-root`、`HYDRO_DATA_ROOT` 或仓库根目录。历史证据中的模型关联路径也必须匹配；该选项不自动改写旧记录中的绝对路径。输出必须是新目录，且不能位于任一输入序列内。

产物为 `index.json`、`lineage_audit.json`、`report.json`。进入生成阶段后的失败留下 `failure.json`；前置路径拒绝不会创建输出。带审计参数时，使用固定提交中的旧全量 reader 在内存逐条读取参考，再与每批、每个窗口及末尾帧比较。该参考核验会加载一条完整轨迹，是审计路径的成本；普通读取接口不需要它。

当前配置是 **5 帧历史 + 5 帧未来，stride=5**，沿用 0.2 s 保存间隔。这是数据管线样例，不是最终训练规格。只生成完整窗口，不补帧或移动最后一个窗口来凑整；末尾未入窗帧在报告中明确记录。

现有 baseline、zero、a0025、a0050、a0100 全部属于同一个 M3 祖先族。它们统一进入 train，validation/test 留空。多族划分由独立合成夹具验证，不能把这些测试夹具称作新增真实工况。要开展独立泛化评估，仍须增加独立起点/轨迹族。

索引记录当前绝对序列目录并绑定原清单字节；数据搬迁后须依据可验证的来源重新生成索引，不能仅改路径和重算摘要。Git 仅交付代码、注册配置、轻量索引和审计日志；MPH、原生向量及完整 TXT/NPZ 留在原数据存储中。
