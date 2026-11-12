# 稀疏观测算子与世界模型场重构评测规范 (Observation Operators & Field Reconstruction)

本文档规范 **HydroDisturb-Bench** 的稀疏传感器观测算子（Observation Operators）、传感器拓扑布局（Sensor Layouts）、动态移动扰动源（Moving Disturbance Sources）以及流场全域重构评估指标体系（Field Reconstruction Metrics）。

---

## 一、物理背景与数学模型

在真实水下流体扰动反演与世界模型评估中，算法无法直接获取三维流场全域（$1.18 \times 10^6$ 点）或二维中心切片（$18,432$ 点）的稠密状态，而必须依赖水下稀疏观测网络。

观测算子 $\mathcal{H}$ 将流场连续稠密状态 $\mathbf{q}(t) \in \mathbb{R}^{C \times N_z \times N_y \times N_x}$ 映射到有限传感器观测集合 $\mathbf{y}(t) \in \mathbb{R}^{K \times C}$：

$$\mathbf{y}_k(t) = \mathcal{H}_k(\mathbf{q}(t)) + \boldsymbol{\epsilon}_k(t), \quad k = 1, \dots, K$$

其中：
- $K$ 为传感器测点数量；
- $C$ 为测量物理通道（二维为 $[u, v, p]$，三维为 $[u, v, w, p]$）；
- $\boldsymbol{\epsilon}_k(t) \sim \mathcal{N}(\mathbf{b}_k, \boldsymbol{\Sigma}_k)$ 为传感器测量噪声与漂移偏差。

---

## 二、标准传感器布局协议 (Standard Sensor Layouts)

系统在 `hydro_disturb_bench.observation_operator` 中预置了四大类标准化水动力学观测拓扑：

| 布局类型 | 生成函数 | 物理拓扑与应用场景 |
|---|---|---|
| **二维下游耙式测线**<br>(Downstream Rake 2D) | `make_downstream_rake_2d(x_streamwise, y_span, num_probes)` | 在圆柱后方不同流向截面（如 $x = 2D, 4D, 6D$）布置垂向线阵，监测卡门涡街涡对脱落与尾流耗散。 |
| **三维立体空间阵列**<br>(Downstream Rake 3D) | `make_downstream_rake_3d(x_streamwise, y_span, z_span, ny, nz)` | 在流向尾流区布置 $N_y \times N_z$ 网格状三维耙式探针阵列，捕捉发卡涡与展向二次涡结构。 |
| **圆柱表面壁面测环**<br>(Surface Ring Probes) | `make_cylinder_surface_probes_2d(D, num_probes, standoff)` | 紧贴圆柱外表面（$r = D/2 + \delta$）布置周向环形压力与壁面剪切传感器，模拟潜艇/柱体表面附着水声基阵。 |
| **移动巡航 AUV 轨迹**<br>(Mobile AUV Patrol) | `make_auv_patrol_path(start_pos, velocity, times)` | 模拟移动自主水下航行器（AUV）沿规划巡航路径穿越尾流场采样。 |

### 布局序列化契约 (JSON)
传感器拓扑以 JSON 格式规范持久化，包含所有测点的全局坐标及元数据：
```json
{
  "name": "downstream_rake_x3D",
  "dim": 2,
  "probes": [
    {"probe_id": "rake_000", "x": 0.90, "y": -0.60, "z": 0.0, "channels": ["u", "v", "p"]},
    {"probe_id": "rake_001", "x": 0.90, "y": -0.45, "z": 0.0, "channels": ["u", "v", "p"]}
  ],
  "metadata": {"D": 0.30, "x_D": 3.0}
}
```

---

## 三、高阶空间插值与测量噪声模型

### 1. 规则网格插值
观测算子 `ObservationOperator` 内部基于 `scipy.interpolate.RegularGridInterpolator`，严格遵循物理网格的单元中心坐标：
- **二维场**：执行双线性插值（Bilinear Interpolation）；
- **三维空间场**：执行三线性插值（Trilinear Interpolation）；
- **边界外推防护**：对于落在流体计算域之外的测点坐标，算子自动安全返回 `NaN` 并提供告警拦截，杜绝非物理外推伪影。

### 2. 测量噪声与漂移注入
算子支持三层复合现实传感器退化效应：
- **信噪比高斯白噪声 (SNR in dB)**：
  $$\sigma_{noise} = \sigma_{signal} \cdot 10^{-\text{SNR} / 20}$$
- **相对测量误差 ($\sigma_{rel}$)**：
  $$\boldsymbol{\epsilon}_{rel} \sim \mathcal{N}\left(0, (\sigma_{rel} \cdot |\mathbf{y}|)^2\right)$$
- **零漂与传感器系统偏差 ($b$)**：
  $$\mathbf{y}_{obs} = \mathbf{y} + b + \boldsymbol{\epsilon}$$

---

## 四、动态移动扰动源协议 (Moving Disturbance Sources)

在 `hydro_disturb_bench.c04_disturbance` 中，水动力学局部扰动源协议已由静态高斯扩展为**时空动态移动扰动体**：

### 1. 轨迹与时空演化模型
扰动源中心随时间匀速平移：
$$\mathbf{c}(t) = \mathbf{c}_0 + \mathbf{v} \cdot (t - t_{on}), \quad t \in [t_{on}, t_{off}]$$

定向外加速度分布：
$$\mathbf{a}(\mathbf{x}, t) = A \cdot \exp\left(-\frac{\|\mathbf{x} - \mathbf{c}(t)\|^2}{2\sigma^2}\right) \cdot \mathbf{n}_{dir}$$

### 2. 严苛几何准入约束 (Geometric Admission)
移动扰动源在仿真执行前须通过时空包络几何校验：
1. **全时间域计算域包容**：轨迹起点 $\mathbf{c}(t_{on})$ 与终点 $\mathbf{c}(t_{off})$ 必须严格落在流场采样边界内，并留有至少 $2\sigma$ 安全余量；
2. **移动避障校验**：在整个动作区间 $[t_{on}, t_{off}]$ 内，移动中心与圆柱表面的最小距离必须满足：
   $$\min_{t \in [t_{on}, t_{off}]} d(\mathbf{c}(t), \text{cylinder}) > \frac{D}{2} + 2\sigma$$
   严禁扰动源穿透或进入固体壁面内部。

### 3. COMSOL 动态解析表达式生成
系统自动编译生成原生 COMSOL `VolumeForce` 表达式：
```text
Fx = rho0 * 0.005 * exp(-((x - (0.90 + 0.05*(t-181.0)))^2 + (y - 0.0)^2 + (z - 1.5)^2)/(2*0.04^2)) * (t>=181.0)*(t<=185.0)
```

---

## 五、全域场重构与概率预报评估指标

通过 `hydro_disturb_bench.probabilistic_metrics`，评测体系支持两类核心世界模型评测：

### 1. 固体受限流场全域重构质量 (Field Reconstruction Metrics)
所有指标严格在有效流体网格点集 $\Omega_{fluid} = \{\mathbf{x} \mid \text{solid\_mask}(\mathbf{x}) = 0\}$ 上计算：
- **相对流体 $L_2$ 误差 (Rel-$L_2$)**：
  $$\text{Rel-}L_2 = \frac{\|\hat{\mathbf{q}} - \mathbf{q}\|_{2, \Omega_{fluid}}}{\|\mathbf{q}\|_{2, \Omega_{fluid}}}$$
- **流场峰值信噪比 (PSNR in dB)**：
  $$\text{PSNR} = 10 \cdot \log_{10}\left(\frac{\max(\mathbf{q})^2}{\text{MSE}_{\Omega_{fluid}}}\right)$$
- **皮尔逊空间场相关系数 (Pearson Correlation $R$)**：
  $$R = \frac{\sum_{i \in \Omega} (\hat{q}_i - \bar{\hat{q}})(q_i - \bar{q})}{\sqrt{\sum (q_i - \bar{q})^2 \sum (\hat{q}_i - \bar{\hat{q}})^2}}$$

### 2. 集合概率预报评测 (Probabilistic Forecast Metrics)
- **连续分级概率评分 (Continuous Ranked Probability Score, CRPS)**：
  对 $M$ 个集合预报成员 $\{\hat{\mathbf{q}}^{(m)}\}_{m=1}^M$ 与真实观测 $\mathbf{q}$：
  $$\text{CRPS}(\hat{\mathbf{q}}, \mathbf{q}) = \frac{1}{M}\sum_{m=1}^M \|\hat{\mathbf{q}}^{(m)} - \mathbf{q}\|_1 - \frac{1}{2 M^2}\sum_{m=1}^M\sum_{m'=1}^M \|\hat{\mathbf{q}}^{(m)} - \hat{\mathbf{q}}^{(m')}\|_1$$
  当 $M=1$ 时退化为确定性 MAE；当 $M>1$ 时惩罚集合离散度不足或过度离散。
- **离散度-技巧比 (Spread-Skill Ratio, SSR)**：
  $$\text{SSR} = \frac{\mathbb{E}[\text{Ensemble Spread}]}{\text{RMSE}(\bar{\mathbf{q}}_{ens}, \mathbf{q})}$$
  理想标定模型满足 $\text{SSR} \approx 1.0$；$\text{SSR} < 1.0$ 表明集合过于自信（Under-dispersive），$\text{SSR} > 1.0$ 表明集合过于发散（Over-dispersive）。

---

## 六、CLI 命令行工具与工作流

```bash
# 1. 导出下游耙式测点布局方案
hydrobench observation-plan --layout-type rake_2d --x-streamwise 0.90 --num-probes 20 --out configs/rake_x3D.json

# 2. 从流场 NPZ 中提取稀疏测点时序观测（注入 30dB 噪声）
hydrobench observation-sample --field-npz runs/c04_s1_2d_slice_sequence.npz --layout configs/rake_x3D.json --snr-db 30 --out-csv runs/observed_probes.csv

# 3. 对比模型重构流场与真实 CFD 场
hydrobench reconstruction-eval --pred runs/reconstructed_flow.npz --true runs/c04_s1_2d_slice_sequence.npz --solid-mask runs/solid_mask.npy
```
