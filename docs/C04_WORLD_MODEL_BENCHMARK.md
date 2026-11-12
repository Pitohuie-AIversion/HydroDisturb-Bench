# C04 三维流场世界模型基准与评估规范

本文档定义 **HydroDisturb-Bench** 的 C04 高雷诺数（$Re \approx 1.26 \times 10^5$）单圆柱绕流三维时空世界模型（Spatial-Temporal World Model）的数据契约、滑动窗口协议与基线评测体系。

---

## 一、物理与网格数据契约

### 1. 物理参数标准
- **圆柱直径**：$D = 0.30\,\text{m}$
- **来流速度**：$U_0 = 0.50\,\text{m/s}$（来流偏角 $\alpha = 90^\circ$）
- **流体物性（ITTC 2024 标准海水）**：密度 $\rho_0 = 1025.8944\,\text{kg/m}^3$，运动黏度 $\nu_0 = 1.1889 \times 10^{-6}\,\text{m}^2/\text{s}$，动力黏度 $\mu_0 = 0.00121969\,\text{Pa}\cdot\text{s}$。
- **雷诺数**：$Re_D = \frac{U_0 D}{\nu_0} \approx 126,167$。

### 2. 空间采样空间与坐标映射
CFD 仿真全域为 $30D \times 20D \times 10D$（$9.0\,\text{m} \times 6.0\,\text{m} \times 3.0\,\text{m}$）。世界模型训练截取包含近壁与活跃卡门涡街的局部核心区：

| 采样几何 | 采样点分辨率 | 空间范围 | 坐标对齐与映射约定 |
|---|---|---|---|
| **三维空间体场** | **$192 \times 96 \times 64$**<br>(1,179,648 点) | $x \in [-2D, 10D]$ ($3.6\,\text{m}$)<br>$y \in [-3D, 3D]$ ($1.8\,\text{m}$)<br>$z \in [3D, 7D]$ ($1.2\,\text{m}$) | $X_{proj} = x_{comsol}$ (流向 192 点)<br>$Y_{proj} = z_{comsol}$ (横向 96 点)<br>$Z_{proj} = y_{comsol} + 3.0\,\text{m}$ (展向 64 点，$+10D$ 平移) |
| **二维中心截面** | **$192 \times 96$**<br>(18,432 点) | $x \in [-2D, 10D]$<br>$y \in [-3D, 3D]$<br>$z_{fixed} = 5D = 1.5\,\text{m}$ | $X_{proj} = x_{comsol}$<br>$Y_{proj} = y_{comsol}$ |

- **单元中心定位**：$x_i = x_{\min} + (i + 0.5) \cdot \Delta$，均匀步长 $\Delta x = \Delta y = \Delta z = D/16 = 0.01875\,\text{m}$。
- **通道定义与单位**：四物理通道 `[u, v, w, p]`，速度单位 $\text{m/s}$，压力单位 $\text{Pa}$。
- **几何掩码**：`solid_mask` 标识圆柱固体内部（$r \le D/2$），`sdf` 为有符号几何距离场（壁面 $0$，内部 $<0$）。

---

## 二、时空滑动窗口索引机制（Window Indexing）

通过 `hydro_disturb_bench.c04_windows` 提供标准化滑动窗口提取：

```python
from hydro_disturb_bench.c04_windows import C04WindowSpec, C04WindowIndex

# 1. 定义窗口规范：例如历史 2 帧，预测未来 1 帧，滑动步长 1 帧
spec = C04WindowSpec(history_frames=2, forecast_frames=1, stride=1)

# 2. 建立索引目录
index = C04WindowIndex(spec)
index.add_dataset("runs/c04_s1_volume_sequence.npz")

print(f"总计提取滑动窗口数: {len(index)}")

# 3. 按需读取 Learner View
window = index[0]
history = window["history"]        # (2, 4, 64, 96, 192)
future = window["future"]          # (1, 4, 64, 96, 192)
solid_mask = window["solid_mask"]  # (64, 96, 192)
```

### 质量约束与校验标准
1. **严格时间单调性**：序列中所有帧的源物理时间必须严格递增，若存在时序反序或重复直接抛出 `ModelValidationError`。
2. **多轴逐帧校验**：所有提取帧在导出与读取阶段均经过全部三轴几何校验。
3. **有限性检查**：流体计算域内严禁出现 NaN 或 Inf，异常值即时报错并拦截。

---

## 三、历史驱动直接基线预测器（History-only Baselines）

基线预测器严格遵循**纯历史驱动（History-only）**原则，绝不接收任何未来流场、未来扰动标签或外部元数据。

| 基准方法 | 数学形式 | 物理适用性 |
|---|---|---|
| **持续性基线 (`persistence`)** | $\hat{\mathbf{q}}(t) = \mathbf{q}(t_{\text{last}})$ | 评测慢演化流体或小步长预测的基准下限 |
| **双帧线性外推 (`last_two_linear`)** | $\hat{\mathbf{q}}(t) = \mathbf{q}(t_{\text{last}}) + \frac{\mathbf{q}(t_{\text{last}}) - \mathbf{q}(t_{\text{prev}})}{t_{\text{last}} - t_{\text{prev}}} \cdot (t - t_{\text{last}})$ | 评测一阶时间导数线性外推能力的物理基线 |

---

## 四、评测体系与物理通道独立打分

流场评测通过 `hydro_disturb_bench.c04_forecasting` 执行，严格遵守以下科学规范：

1. **固体障碍物滤除**：自动应用 `solid_mask` 排除圆柱内部不可动点，打分分母严格为有效流体节点数 $N_{\text{fluid}}$。
2. **保留物理单位，禁止非物理混合**：
   - 速度通道 $u, v, w$ 计算 $\text{MAE}$ 与 $\text{RMSE}$，单位保持 $\text{m/s}$；
   - 压力通道 $p$ 计算 $\text{MAE}$ 与 $\text{RMSE}$，单位保持 $\text{Pa}$；
   - **绝不将速度和压力混合计算单一指标**。
3. **相对 $L_2$ 误差**：提供无量纲相对误差 $\text{Rel-}L_2 = \frac{\|\hat{\mathbf{q}}_c - \mathbf{q}_c\|_2}{\|\mathbf{q}_c\|_2}$。

---

## 五、真实 4 帧试算解基线实测数据

在 $t \in [0.0, 0.18]\,\text{s}$（$\Delta t = 0.06\,\text{s}$）的 4 帧 C04 非定常数据上运行基线评估（$L=2, H=1$）：

### 1. 二维截面序列基线（`runs/c04_s1_slice_sequence.npz`）
- **Persistence 基线**：
  - $u$ 通道：MAE $= 0.000831\,\text{m/s}$，RMSE $= 0.001673\,\text{m/s}$
  - $v$ 通道：MAE $= 0.000360\,\text{m/s}$，RMSE $= 0.000693\,\text{m/s}$
  - $w$ 通道：MAE $= 0.000189\,\text{m/s}$，RMSE $= 0.000325\,\text{m/s}$
  - $p$ 通道：MAE $= 2.114\,\text{Pa}$，RMSE $= 3.078\,\text{Pa}$
- **Last-two Linear 基线**：
  - $u$ 通道：MAE $= 0.001092\,\text{m/s}$，RMSE $= 0.002897\,\text{m/s}$
  - $p$ 通道：MAE $= 60.35\,\text{Pa}$，RMSE $= 148.25\,\text{Pa}$

### 2. 三维体场序列基线（`runs/c04_s1_volume_sequence.npz`，全场 $1.179 \times 10^6$ 点）
- **Persistence 基线**：
  - $u$ 通道：MAE $= 0.000864\,\text{m/s}$，RMSE $= 0.001688\,\text{m/s}$
  - $v$ 通道：MAE $= 0.000390\,\text{m/s}$，RMSE $= 0.000725\,\text{m/s}$
  - $w$ 通道：MAE $= 0.000192\,\text{m/s}$，RMSE $= 0.000338\,\text{m/s}$
  - $p$ 通道：MAE $= 2.113\,\text{Pa}$，RMSE $= 3.076\,\text{Pa}$
- **计算性能**：纯 NumPy 矢量化计算，三维体场全域两窗口打分仅耗时 **1.8 秒**。

---

## 六、CLI 命令行工具参考

```bash
# 1. 检查数据集中可用的滑动窗口清单
python -m hydro_disturb_bench.cli c04-inspect-windows \
    --dataset runs/c04_s1_slice_sequence.npz \
    --history 2 --forecast 1 --stride 1

# 2. 评估持续性基线 (Persistence)
python -m hydro_disturb_bench.cli c04-evaluate-baselines \
    --dataset runs/c04_s1_volume_sequence.npz \
    --history 2 --forecast 1 --stride 1 \
    --method persistence

# 3. 评估双帧线性外推基线 (Last-two Linear)
python -m hydro_disturb_bench.cli c04-evaluate-baselines \
    --dataset runs/c04_s1_slice_sequence.npz \
    --history 2 --forecast 1 --stride 1 \
    --method last_two_linear
```
