---
name: comsol-workflow
description: Standard operational procedure (SOP) for parameter exploration and simulation solving in COMSOL Multiphysics.
---

# COMSOL 标准操作闭环（Golden Loop）

当在智能体中执行 COMSOL 仿真任务时，按以下严格顺序调用工具：

1. **环境与 License 嗅探**：
   调用 `inspect_environment`，确认 COMSOL 6.4 安装状态与可用 License Token。
2. **Schema 结构探测**：
   调用 `inspect_model(model_path="...")`，解析组件名、可用参数及 Study 标签，确保标签 100% 真实。
3. **写时复制变参 (Copy-on-Write)**：
   调用 `update_parameter(source_model="...", output_model="runs/.../case.mph", param_name="...", param_value="...")`。
4. **异步求解**：
   调用 `solve_study(working_model="runs/.../case.mph", study_tag="std1")`。
5. **降采样评估**：
   从返回的 `kpis`（收敛性、最大流速、阻力系数）评估计算质量，仅在需要时通过文件路径查阅切片图。
