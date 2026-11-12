# 涡动力学、涡核识别准则与不可压缩连续性诊断规范 (Vorticity Dynamics & Incompressibility)

本文档规范 **HydroDisturb-Bench** 的微分运动学诊断体系，涵盖涡量场（Vorticity）、$Q$ 准则涡核识别（$Q$-Criterion）、拟涡能（Enstrophy）以及用于评估 AI 流场世界模型物理守恒性的不可压缩质量守恒连续性残差指标（Mass Conservation Residual）。

---

## 一、物理与数学模型

在潜航器/水下目标尾流受扰动流场分析中，速度场 $\mathbf{u} = (u, v, w)$ 的高阶空间导数直接反映了相干涡结构的脱落、拉伸与耗散机制。

### 1. 涡量场 (Vorticity Vector)
涡量定义为速度场的旋度 $\boldsymbol{\omega} = \nabla \times \mathbf{u}$：
- **二维切片流场**（垂直于展向）：
  $$\omega_z = \frac{\partial v}{\partial x} - \frac{\partial u}{\partial y}$$
- **三维空间体场**：
  $$\boldsymbol{\omega} = (\omega_x, \omega_y, \omega_z) = \left(\frac{\partial w}{\partial y} - \frac{\partial v}{\partial z},\; \frac{\partial u}{\partial z} - \frac{\partial w}{\partial x},\; \frac{\partial v}{\partial x} - \frac{\partial u}{\partial y}\right)$$

### 2. $Q$ 准则涡核识别 (Hunt et al., 1988)
速度梯度张量 $\mathbf{J} = \nabla \mathbf{u}$ 可严格分解为对称应变率张量 $\mathbf{S}$ 与反对称旋转率张量 $\boldsymbol{\Omega}$：
$$\mathbf{S} = \frac{1}{2}(\nabla \mathbf{u} + (\nabla \mathbf{u})^T), \quad \boldsymbol{\Omega} = \frac{1}{2}(\nabla \mathbf{u} - (\nabla \mathbf{u})^T)$$

$Q$ 准则标量定义为：
$$Q = \frac{1}{2}\left(\|\boldsymbol{\Omega}\|^2 - \|\mathbf{S}\|^2\right)$$
- **$Q > 0$**：旋转率主导应变率，标识局部相干涡核（Vortex Core）；
- **$Q < 0$**：剪切/拉伸应变主导，常见于壁面剪切层与鞍点分离区。

### 3. 拟涡能 (Enstrophy Density)
拟涡能反映湍流尺度级联与黏性耗散强度：
$$\mathcal{E} = \frac{1}{2}\|\boldsymbol{\omega}\|^2$$

### 4. 不可压缩连续性残差 (Mass Conservation Divergence Residual)
对于不可压缩单相水流，物理连续性方程严格要求散度为零：
$$\nabla \cdot \mathbf{u} = \frac{\partial u}{\partial x} + \frac{\partial y}{\partial y} + \frac{\partial w}{\partial z} = 0$$

在世界模型与流场生成评测中，非物理伪影常表现为发散不为零。系统定义无量纲连续性违背指标：
$$\epsilon_{div} = \frac{\|\nabla \cdot \mathbf{u}\|_{\text{RMS}, \Omega_{fluid}} \cdot D}{U_0}$$
其中 $U_0$ 为特征来流速度（如 $0.50\,\text{m/s}$），$D$ 为圆柱直径（如 $0.30\,\text{m}$）。

---

## 二、代码接口与使用规范

模块位于 `hydro_disturb_bench.vorticity_dynamics`：

```python
from hydro_disturb_bench.vorticity_dynamics import (
    compute_vorticity_2d,
    compute_q_criterion_2d,
    compute_divergence_2d,
    compute_enstrophy,
    evaluate_incompressibility_metrics,
)

# 1. 计算二维涡量与 Q 准则
omega_z = compute_vorticity_2d(u, v, x, y, solid_mask=solid_mask)
q_field = compute_q_criterion_2d(u, v, x, y, solid_mask=solid_mask)

# 2. 评估不可压缩物理守恒性残差
div_u = compute_divergence_2d(u, v, x, y, solid_mask=solid_mask)
metrics = evaluate_incompressibility_metrics(
    div_u, solid_mask=solid_mask, characteristic_velocity=0.50, characteristic_length=0.30
)
print(f"最大绝对散度: {metrics['max_abs_div']:.4e} 1/s")
print(f"无量纲 RMS 散度违背: {metrics['normalized_rms_div']:.4e}")
```

---

## 三、命令行工具 (CLI)

```bash
# 对流场 NPZ 执行全域涡动力学与连续性指标评估
hydrobench vorticity-eval --dataset runs/c04_s1_2d_slice_sequence.npz --out runs/vorticity_report.json
```

输出示例 JSON：
```json
{
  "dataset": "runs/c04_s1_2d_slice_sequence.npz",
  "dim": 2,
  "incompressibility": {
    "max_abs_div": 0.00312,
    "rms_div": 0.00042,
    "normalized_rms_div": 0.00025,
    "fluid_cell_count": 18200
  },
  "enstrophy_mean": 1.452,
  "enstrophy_max": 48.31,
  "q_criterion_mean": 0.021,
  "q_criterion_max": 24.15
}
```
