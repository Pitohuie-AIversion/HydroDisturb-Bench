# 水动力学扰动反演与基准数据集打包规范 (Disturbance Inversion & Benchmark Bundles)

本文档规范 **HydroDisturb-Bench** 的水动力学扰动反演与隐式参数识别评测基准（Disturbance Inversion Benchmark）、基线反演算法（Inverse Estimators）以及自包含防篡改基准数据集打包验证体系（Benchmark Dataset Bundles）。

---

## 一、扰动反演任务定义 (The Inverse Problem)

在前向世界模型任务中，输入历史流场预测未来演化；而在**水动力学扰动反演任务**中，算法的目标是根据下游稀疏测点时序 $\mathbf{y}(t) = \mathcal{H}(\mathbf{q}(t))$ 或重构流场，反向估计产生扰动的未观测隐式物理参数：

$$\hat{\boldsymbol{\theta}} = \mathcal{F}_{\text{inv}}\left(\{\mathbf{y}(t)\}_{t \le t_{\text{obs}}}\right)$$

其中待反演参数集 $\boldsymbol{\theta}$ 包含：
- **事件发生判定**：$\text{active} \in \{\text{True}, \text{False}\}$ 及检测报警时刻 $t_{\text{detect}}$；
- **时序参数**：施加起始时刻 $t_{on}$ 与结束时刻 $t_{off}$；
- **空间位置**：扰动中心物理坐标 $\mathbf{c} = (x_c, y_c)$ 或 $(x_c, y_c, z_c)$；
- **动力学参数**：加速度峰值幅值 $A$（单位 $\text{m/s}^2$）与力的方向矢量 $\mathbf{n}$；
- **移动轨迹参数**：对于动态扰动源，还包含平移速度 $\mathbf{v} = (v_x, v_y, v_z)$。

---

## 二、标准化评测指标体系 (Inversion Metrics)

通过 `hydro_disturb_bench.disturbance_inversion` 执行标准化打分：

| 评估维度 | 指标名称 | 数学定义与物理意义 |
|---|---|---|
| **事件检测** | `precision`, `recall`, `f1_score` | 准确率、召回率与综合 F1 分数 |
| **检测时延** | `detection_delay_sec` | $\Delta t_{\text{delay}} = t_{\text{detect}} - t_{on}$（单位 $\text{s}$） |
| **空间定位误差** | `localization_error_m`<br>`localization_error_D` | 欧氏距离 $\|\hat{\mathbf{c}} - \mathbf{c}^*\|_2$（单位 $\text{m}$）与圆柱直径无量纲比 $\|\hat{\mathbf{c}} - \mathbf{c}^*\|_2 / D$ |
| **时序误差** | `t_on_error_sec`<br>`duration_error_sec` | 发生时刻绝对误差 $|\hat{t}_{on} - t_{on}^*|$ 与持续时间误差 $|\Delta\hat{t} - \Delta t^*|$ |
| **幅值相对误差** | `amplitude_error_rel_pct` | 相对百分比误差 $\frac{|\hat{A} - A^*|}{A^*} \times 100\%$ |
| **方向角误差** | `direction_angle_error_deg` | 力方向向量夹角 $\theta = \arccos\left(\frac{\hat{\mathbf{n}} \cdot \mathbf{n}^*}{\|\hat{\mathbf{n}}\| \|\mathbf{n}^*\|}\right)$（单位 $^\circ$） |
| **轨迹追踪误差** | `trajectory_tracking_error_m` | 移动扰动源全时程均方根位置偏差 $\frac{1}{K}\sum_k \|\hat{\mathbf{c}}(t_k) - \mathbf{c}^*(t_k)\|$ |
| **综合反演得分** | `composite_inversion_score` | 归一化综合评分 $\in [0.0, 1.0]$，惩罚定位、幅值、时序与方向角漂移 |

---

## 三、基线反演估计器 (Baseline Heuristic Inverters)

系统提供了两套直接基于流体动力学物理原理的无训练基线反演器：

1. **测点能量异常检测器 (`AnomalyEnergyDetector`)**：
   - 统计未扰动流场的测点基线均值与标准差；
   - 实时计算归一化能量偏离度 $z(t) = \frac{1}{K}\sum_k \left(\frac{y_k(t) - \bar{y}_k}{\sigma_k}\right)^2$；
   - 当 $z(t) \ge \text{threshold}$ 时触发检测报警 $t_{\text{detect}}$。

2. **尾流对流渡越时间反投影器 (`ConvectiveTimeOfFlightEstimator`)**：
   - 圆柱尾流相干涡对以对流速度 $U_c \approx 0.85 U_0$ 向下游平流演化；
   - 根据下游测线 $x_{\text{sensor}}$ 处的扰动首达时间 $t_{\text{detect}}$，反演推导上游扰动发生流向位置：
     $$\hat{x} = x_{\text{sensor}} - U_c \cdot (t_{\text{detect}} - \hat{t}_{on})$$

---

## 四、自包含加密基准数据集打包 (Benchmark Bundle Protocol)

为了确保科研评测的**绝对可复现性（Bit-for-Bit Reproducibility）**与**防篡改验证**，`hydro_disturb_bench.benchmark_bundle` 将所有要素统一封装至 `.npz` 容器中：

### 1. 打包内容规范
- `times`：严格单调递增的一维物理时间戳数组；
- `coord_x`, `coord_y`, `coord_z`：高精度空间物理坐标轴；
- `values`：连续物理流场张量（严格保证流体计算域内无 NaN/Inf）；
- `solid_mask`：固体障碍物几何掩码；
- `sdf`：几何有符号距离场；
- `observations`：稀疏传感器实际采样观测时序；
- `bundle_manifest`：JSON 格式的数字凭证清单，包含每一个内部数组的维度、数据类型与 **SHA-256 哈希校验和**。

### 2. 严苛防篡改验证 (`verify_benchmark_bundle`)
任何对打包文件中数据的篡改、文件损坏或时间反序，均会被验证器捕获并抛出 `ModelValidationError`。

---

## 五、CLI 命令行工作流

```bash
# 1. 将仿真导出的流场打包为标准自包含评测 Bundle
hydrobench bundle-create --dataset runs/c04_s1_2d_slice_sequence.npz --out benchmarks/c04_standard_bundle.npz --name "C04-2D-Slice-v1"

# 2. 验证 Bundle 文件的 SHA-256 完整性与物理格式
hydrobench bundle-verify --bundle benchmarks/c04_standard_bundle.npz

# 3. 评估 AI 反演模型输出与真实扰动参数的误差指标
hydrobench inversion-eval --truth configs/disturbance_truth.json --pred runs/inverted_params.json --diameter 0.30 --out runs/inversion_report.json
```
