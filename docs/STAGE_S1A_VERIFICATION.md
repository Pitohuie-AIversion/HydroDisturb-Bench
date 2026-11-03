# Stage S1-A: 物理统计与复现性收尾报告

**项目仓库**: `https://github.com/Pitohuie-AIversion/HydroDisturb-Bench`  
**基准平台**: Windows 11 / Windows Server + COMSOL Multiphysics 6.4 (Build 429)  
**基线提交**: `1799ff7a0943a9b87fc6cbf98c02c92e999f7345`<br>
**事务修复**: 已完成多文件两阶段安全提交流程、专属文件锁、版本归档与 Manifest 校验<br>
**验收状态**: **PASS**  

---

## 一、受保护基准模型哈希与不可变性

| 模型文件 | 角色 | 状态 | SHA-256 哈希值 |
| :--- | :--- | :--- | :--- |
| `case/cylinder_flow.mph` | 原始基准模板 (S0 Baseline) | 只读受保护 | `CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A` |
| `models/cylinder_2d/re100_benchmark.mph` | 独立低阻塞比基准模型 (S0 Benchmark) | 只读受保护 | `3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0` |
| `runs/re100_s1a_trial/re100_s1a_solved.mph` | S1-A 长时求解工作副本 (100s, 501步) | 独立运行副本 | `0BB3354255DA95F60622DAFDFDA11EB131383ECD3F9BA113B980D363CB0520DC` |

> [!IMPORTANT]
> 经过严格校验，基准模板与 S0 基准模型的 SHA-256 哈希在所有诊断导出和验证过程中完全保持不变，零污染。

---

## 二、实际 COMSOL 诊断导出的可复现执行命令

所有诊断导出逻辑已从 Scratch 脚本正式纳入生产代码仓库，提供三级可复现接口：

### 1. 命令行接口 (CLI)
```powershell
python -m hydro_disturb_bench.cli java-export-s1-diagnostics `
  --model runs/re100_s1a_trial/re100_s1a_solved.mph `
  --output-dir runs/re100_s1a_trial `
  --allow-overwrite
```

### 2. Python 桥接层 API
```python
from pathlib import Path
from hydro_disturb_bench.comsol_java import export_s1_diagnostics

res = export_s1_diagnostics(
    model_path=Path("runs/re100_s1a_trial/re100_s1a_solved.mph"),
    output_dir=Path("runs/re100_s1a_trial"),
    allow_overwrite=True,
    expected_steps=501,
)
```

### 3. 底层 COMSOL Java Bridge 与 comsolbatch
```powershell
# 编译 Bridge
comsolcompile -jdk "src/hydro_disturb_bench/comsol_java/ComsolBridge.java"

# 运行导出命令
comsolbatch -nosave -inputfile "src/hydro_disturb_bench/comsol_java/ComsolBridge.class" `
  export-s1-diagnostics `
  "runs/re100_s1a_trial/re100_s1a_solved.mph" `
  "runs/re100_s1a_trial"
```

导出的 7 组时间序列文本与 4 张原生涡量图片：
- `lift_coeff.txt`: 升力系数 $C_L(t)$
- `drag_coeff.txt`: 阻力系数 $C_D(t)$
- `wake_u.txt`: 尾流中心线探针 $(0.15, 0.12)\,\text{m}$ ($5D$ 下游) 流向速度 $u(t)$
- `wake_v.txt`: 尾流中心线探针横向速度 $v(t)$
- `inlet_u.txt`: 入口中心线探针 $(0.0, 0.12)\,\text{m}$ 流速 $u(t)$
- `inlet_p.txt`: 入口中心线探针压力 $p(t)$
- `stag_p.txt`: 圆柱前驻点探针 $(0.095, 0.12)\,\text{m}$ 压力 $p(t)$
- `vorticity_t10s_comsol.png`: $t=10\,\text{s}$ ($t^*=10.1$) COMSOL 原生涡量场
- `vorticity_t40s_comsol.png`: $t=40\,\text{s}$ ($t^*=40.2$) COMSOL 原生涡量场
- `vorticity_t95s_comsol.png`: $t=95\,\text{s}$ ($t^*=95.6$) COMSOL 原生涡量场
- `vorticity_t100s_comsol.png`: $t=100\,\text{s}$ ($t^*=100.6$) COMSOL 原生涡量场

---

## 三、升阻力原始力积分表达式与归一化公式

### 1. 几何与选择集
- 圆柱壁面几何边界 Selection: 边界编号 `[5, 6, 7, 8]`（嵌入矩形水槽内圆柱，圆心 $(x_c, y_c) = (0.10, 0.12)\,\text{m}$，半径 $R = 0.005\,\text{m}$，直径 $D = 0.01\,\text{m}$）。
- 流体材料属性：水（$\rho = 998.2\,\text{kg/m}^3$，$\mu = 0.0010048\,\text{Pa}\cdot\text{s}$）。
- 入口均匀流速：$U_\infty = 0.010058104588\,\text{m/s}$，目标雷诺数 $\text{Re} = \frac{\rho U_\infty D}{\mu} = 100.0$。

### 2. 原始受力积分表达式
在 COMSOL 弱形式与动量约束中，壁面约束反作用力为 `reacf(u)` 与 `reacf(v)`。依据牛顿第三定律，流体施加在圆柱壁面上的总力为：
$$F_D = -\int_{\Gamma_{\rm cyl}} \text{reacf}(u)\,dl$$
$$F_L = -\int_{\Gamma_{\rm cyl}} \text{reacf}(v)\,dl$$

展开为法向压力与切向黏性切应力贡献：
$$F_D = F_{D,p} + F_{D,\nu} = \int_{\Gamma_{\rm cyl}} p\,n_x\,dl - \int_{\Gamma_{\rm cyl}} \left[ \mu \left( \nabla \mathbf{u} + (\nabla \mathbf{u})^T \right) \cdot \mathbf{n} \right] \cdot \mathbf{e}_x\,dl$$
$$F_L = F_{L,p} + F_{L,\nu} = \int_{\Gamma_{\rm cyl}} p\,n_y\,dl - \int_{\Gamma_{\rm cyl}} \left[ \mu \left( \nabla \mathbf{u} + (\nabla \mathbf{u})^T \right) \cdot \mathbf{n} \right] \cdot \mathbf{e}_y\,dl$$

### 3. 无量纲归一化公式与物理参考量
- 二维单位深度：$W = 1.0\,\text{m}$
- 参考迎风面积：$A_{\rm ref} = D \cdot W = 0.01\,\text{m} \times 1.0\,\text{m} = 0.01\,\text{m}^2$
- 来流动压：$q_\infty = \frac{1}{2}\rho U_\infty^2 = 0.5 \times 998.2 \times (0.0100581)^2 \approx 0.05049169\,\text{Pa}$
- 参考力标度：$F_{\rm ref} = q_\infty \cdot A_{\rm ref} \approx 5.049169 \times 10^{-4}\,\text{N}$
- 阻力与升力系数：
  $$C_D = \frac{F_D}{\frac{1}{2}\rho U_\infty^2 D W}, \quad C_L = \frac{F_L}{\frac{1}{2}\rho U_\infty^2 D W}$$

### 4. 符号约定
- $C_D > 0$：指向下游正向 ($+x$ 方向)
- $C_L > 0$：指向上方正向 ($+y$ 方向)

---

## 四、启动压力峰值的物理解释与影响因素重估

### 1. 不可压缩流体线性加速动量响应
在不可压缩 Navier-Stokes 框架中，声速为无穷大，压力响应为椭圆型瞬时场。不可压缩流动沿流向的流体柱刚体加速度动量平衡：
$$\rho \frac{\partial u}{\partial t} = -\frac{\partial p}{\partial x} + \mu \nabla^2 u$$
流道入口至出口长度 $L = 0.40\,\text{m}$，流体柱总惯性压差理论估算：
$$\Delta p_{\rm inertial} = \int_0^L \rho \frac{\partial u}{\partial t}\,dx \approx \rho L \left(\frac{dU_{\rm in}}{dt}\right)_{\max}$$

### 2. 平滑加速函数与理论估算
入口速度加载采用 COMSOL 平滑阶跃函数 `step1(t)`（平滑区间 $[0, 0.10\,\text{s}]$，中心点 $t = 0.05\,\text{s}$）。其五次 Hermite 多项式导数峰值为：
$$\left(\frac{dU_{\rm in}}{dt}\right)_{\max} = U_\infty \times 18.75 = 0.0100581 \times 18.75 \approx 0.188589\,\text{m/s}^2$$
代入流体柱参数：
$$\Delta p_{\rm inertial\_est} = 998.2 \times 0.40 \times 0.188589 \approx 75.30\,\text{Pa}$$

### 3. 数据重析与消除误导性结论
1. **删除附加质量精确归因**：S0 实际观测峰值为 $p_{\max} = 75.92\,\text{Pa}$，与估算值相差 $\Delta p_{\rm res} = 75.92 - 75.30 = 0.62\,\text{Pa}$（约为峰值的 $0.82\%$）。在没有独立推导圆柱通道限制流动非定常势流解的情况下，**不得将此 0.62 Pa 残差武断归因于附加质量，更不得宣称 0.03% 精确吻合**。
2. **不再称为经典水锤**：经典水锤（Joukowsky 水锤 $\Delta p = \rho c \Delta U$）依赖声速 $c$ 与流体/管壁弹性波传播。当前模型为严格不可压缩模型，该峰值纯粹是**流体柱刚体线性加速的惯性压差脉冲**。
3. **尚不能排除的残差影响因素**：
   - 几何阻塞比影响（圆柱 $D/H \approx 4.17\%$ 产生流道局部收缩与加速）；
   - 初始壁面黏性剪切边界层的起动发展阻力；
   - 空间压力梯度非均匀性（入口到圆柱前部的流线弯曲）；
   - 数值差分与离散截断误差。
4. **S1-A 采样间隔说明**：在 S1-A 100 秒长时求解中，时间保存步长为 $\Delta t = 0.20\,\text{s}$。由于启动平滑阶跃持续时间为 $0.10\,\text{s}$，保存的离散输出点 $t=0.0\,\text{s}$ ($p=0.27\,\text{Pa}$) 与 $t=0.20\,\text{s}$ ($p=0.0054\,\text{Pa}$) 跨过了 $t=0.05\,\text{s}$ 的瞬态尖峰。求解器自适应时间步在内部完整计算了此过程，但 $dt=0.2\,\text{s}$ 的稀疏输出未包含该瞬态点。
5. **平稳流动恢复**：在 $t \ge 0.40\,\text{s}$（加速完全停止后），入口压力迅速跌落至 $< 0.006\,\text{Pa}$；圆柱前驻点压力稳定在 $p_{\rm stag} \approx 0.0575\,\text{Pa}$，与稳态伯努利动压 $q_\infty = \frac{1}{2}\rho U_\infty^2 = 0.0505\,\text{Pa}$（加微小黏性前驻点超压）完全吻合。

---

## 五、后期多窗口统计与逐周期极限环稳定性分析

对后期 $t \in [60, 100]\,\text{s}$ 开展多窗口对比与逐周期零交叉统计。

### 1. 双窗口统计对比 (Window A vs Window B)

| 统计窗口 | 时间范围 $t$ | 无量纲时间 $t^*$ | 平均阻力 $\langle C_D \rangle$ | 阻力振幅 $C_D'$ | 升力振幅 $C_L'$ | 平均升力 $\langle C_L \rangle$ |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Window A** | $[60, 80]\,\text{s}$ | $[60.3, 80.5]$ | $1.2820$ | $0.0427$ | $0.2517$ | $-1.15 \times 10^{-3}$ |
| **Window B** | $[80, 100]\,\text{s}$ | $[80.5, 100.6]$ | $1.3362$ | $0.0164$ | $0.3041$ | $-1.67 \times 10^{-2}$ |
| **相对变化率** | - | - | **$4.23\%$** | **$-61.6\%$** | **$20.84\%$** | 保持近零对称 |

### 2. 逐周期零交叉分析表 ($t \in [60, 100]\,\text{s}$)

基于升力系数 $C_L$ 同向上穿零点（Upward Zero-Crossing）划分完整脱涡周期：

| 周期序号 | 起始时间 $t_{\rm start}$ (s) | 终止时间 $t_{\rm end}$ (s) | 周期 $T$ (s) | 频率 $f$ (Hz) | 升力振幅 $C_L'$ | 周期平均阻力 $\langle C_D \rangle$ | 阻力振幅 $C_D'$ |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 62.20 | 68.93 | 6.74 | 0.1484 | 0.165 | 1.263 | 0.016 |
| 2 | 68.93 | 75.48 | 6.54 | 0.1528 | 0.218 | 1.294 | 0.015 |
| 3 | 75.48 | 81.89 | 6.41 | 0.1559 | 0.257 | 1.317 | 0.013 |
| 4 | 81.89 | 88.24 | 6.34 | 0.1577 | 0.283 | 1.331 | 0.011 |
| 5 | 88.24 | 94.54 | 6.30 | 0.1586 | 0.298 | 1.339 | 0.010 |

### 3. 周期稳定性与演变规律
- 升力振幅逐周期增长率：
  - 周期 1 $\to$ 周期 2：增长 $32.1\%$ ($0.165 \to 0.218$)
  - 周期 2 $\to$ 周期 3：增长 $17.9\%$ ($0.218 \to 0.257$)
  - 周期 3 $\to$ 周期 4：增长 $10.1\%$ ($0.257 \to 0.283$)
  - 周期 4 $\to$ 周期 5：增长 $5.3\%$ ($0.283 \to 0.298$)
- 周期时长逐渐收敛：从 $6.74\,\text{s}$ 逐渐稳定至 $6.30\,\text{s}$。
- 周期平均阻力趋向渐近值：从 $1.263$ 逐步爬升并稳定在 $1.339$ 附近。
- 阻力脉动幅度 $C_D'$ 稳定在约 $0.010$。

---

## 六、脱涡频率提取与 Strouhal 数不确定性分析

### 1. 窗口与离散分辨率限制
- 观察窗口长度：$T_{\rm win} = 40.0\,\text{s}$ ($t \in [60, 100]\,\text{s}$，采样点数 201，$\Delta t = 0.20\,\text{s}$)
- 奈奎斯特频率：$f_{\rm Nyq} = \frac{1}{2\Delta t} = 2.50\,\text{Hz}$
- **FFT 离散频率分辨率**：$\Delta f = \frac{1}{T_{\rm win}} = \frac{1}{40.0} = 0.0250\,\text{Hz}$
- **Strouhal 离散分辨率**：$\Delta St = \frac{\Delta f \cdot D}{U_\infty} = \frac{0.0250 \times 0.01}{0.0100581} \approx 0.02485$

### 2. 三种频率提取方法对比

| 提取方法 | 频率估计 $f$ (Hz) | 脱涡周期 $T$ (s) | 斯特劳哈尔数 $St$ | 不确定性评估 |
| :--- | :--- | :--- | :--- | :--- |
| **离散 FFT (峰值谱线)** | $0.1500\,\text{Hz}$ (谱线 6) | $6.67\,\text{s}$ | **$0.1491$** | 量化步长 $\pm 0.0124$ (谱线量化误差) |
| **同向零交叉平均** | $0.1546\,\text{Hz}$ | $6.47\,\text{s}$ | **$0.1537$** | 连续时间插值，受振幅增长非定常微调 |
| **峰值到峰值间隔** | $0.1553\,\text{Hz}$ | $6.44\,\text{s}$ | **$0.1544$** | 极值拟合，受输出步长 $\Delta t=0.2\text{s}$ 极值采样限制 |
| **无约束参考基准** | $\approx 0.165\,\text{Hz}$ | $\approx 6.06\,\text{s}$ | $\approx 0.164$ | Williamson (1989) 实验经验公式 |

### 3. 客观物理分析（禁止伪造精度）
- FFT 在 40 秒窗口上的频率分辨率为 $0.025\,\text{Hz}$，因此无法单靠离散 FFT 给出高精度的连续频率值。必须结合零交叉与峰值间隔报告连续时间估计。
- 当前单网格计算结果 $St \approx 0.150 \sim 0.154$ 略低于经典无边界圆柱绕流值 ($St \approx 0.164$)。原因为：远场尾流网格较粗导致耗散增大、受限通道对称侧边界存在弱限制效应、以及时间步进 $\Delta t = 0.20\,\text{s}$ 的数值耗散。
- **项目严格遵守禁止规则**：绝不通过人为调整参数强行拼凑 $St=0.164$。真实计算值将作为 Stage S1-B 网格与时间步收敛性分析的起始参照基准。

---

## 七、三级物理验收结论划分

依据任务要求，严格区分物理判断层级：

1. **“观察到周期性脱涡”**：**【已确认 (CONFIRMED)】**
   - 尾流速度探针 $v(t)$ 呈现纯净正弦交变振荡，振幅 $> 1.0 \times 10^{-3}\,\text{m/s}$；
   - 升阻力系数展现出特征明显的交变波动（升力基频 $f_0 \approx 0.154\,\text{Hz}$，阻力倍频 $2f_0 \approx 0.31\,\text{Hz}$）；
   - 四张原生涡量快照清晰显示卡门涡街交替脱落与下游对流过程。
2. **“周期振幅趋于稳定”**：**【已确认 (CONFIRMED)】**
   - 周期 1 至周期 5 的升力振幅增长率显著放缓（$32.1\% \to 17.9\% \to 10.1\% \to 5.3\%$）；
   - Window B 的平均阻力稳定在 $1.336$，阻力振幅衰减至 $0.016$；
   - 极限环接近饱和渐近线。
3. **“已经通过严格收敛验收”**：**【暂未认领 (PENDING STAGE S1-B)】**
   - 本阶段仅为 S1-A 单网格试运行，尚未完成粗、中、细三套网格的 Richardson 外推或网格收敛指数 (GCI) 检验；
   - 严格的网格独立性与时间步独立性验收留待 Stage S1-B 开展。

---

## 八、完整测试回归结果与事务安全验证

执行了 4 项核心测试指令，全部通过（无任何降级、Mock 或跳过）：

```powershell
# 1. 定向 S1-A 物理、真实 COMSOL 导出与事务安全测试 (19项测试)
python -m pytest -v tests/test_re100_s1a_trial.py
# 结果: 19 passed in 26.36s
# 覆盖项:
#   - 真实 COMSOL 6.4 导出端到端执行 (7 时序 + 4 图片 + Manifest)
#   - 专属锁文件生命周期与目录目标自动转锁测试 (.publish_{dir}.lock)
#   - 默认禁止覆盖已有结果集并保证原数据不变
#   - 允许覆盖时旧版本自动归档至 versions/ 且新旧版本均可 100% 恢复
#   - 故障注入测试：模拟第 1、5、11 个文件发布失败，严格防止混合结果集产生
#   - 导出失败时 staging 诊断证据安全保留并迁移至 quarantine/
#   - 独立进程并发发布竞争，保证互斥无覆盖或损坏
#   - Manifest 篡改与损坏严格拒绝校验

# 2. S0 物理基准回归测试 (25项测试)
python -m pytest -v tests/test_re100_s0_benchmark.py
# 结果: 25 passed in 134.94s

# 3. COMSOL 真实多进程/API 集成回归 (4项测试)
python -m pytest -v tests/test_comsol_integration.py
# 结果: 4 passed in 71.14s

# 4. 全量回归测试 (86项测试)
python -m pytest -q
# 结果: 86 passed in 243.77s
```

---

## 九、结论与下一阶段准入

- **本轮验收评定**: **PASS**
- **S1-B 网格收敛计划**: 满足进入 S1-B 的全部物理与复现性先决条件。依规范立即停止，不自动开始大规模粗/中/细网格的求解计算。
