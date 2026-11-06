# CI 修复与第一轮 Python 解耦（2026-11-06）

本轮从 `4fc01caa2b5e4b0430a9a060274718bd594bca6b` 开始，修复实际 CI 阻塞和 Python 分支执行的耦合问题。**本地完整离线回归为 546 passed、13 deselected；未选择真实 COMSOL 集成测试，没有新增 CFD 求解。** 历史数值结论仍以对应实验提交为准。

## 已确认根因与修改

原 [CI 运行](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/actions/runs/42194712699) 在 Ruff 步骤报 `Failed to spawn: ruff`，pytest 被跳过。Ruff 位于可选 `dev` extra，而该步骤未启用 extra。仓库有 `uv.lock`，但安装了 uv 不等于项目的测试工具已安装。

当前工作流固定 uv 0.13.7，先执行 `uv sync --locked --python 3.11 --extra dev --extra analysis`，后续均使用 `uv run --no-sync`。`analysis` 提供图片测试所需的 Pillow 依赖。NumPy 声明从 ≥1.24 修正为 ≥2.0，与实际使用的 `np.trapezoid` 一致；37 个锁定包的版本保持不变，仅更新锁文件中的对应要求。

测试现在分为三类：

| 类别 | 执行位置与含义 |
| --- | --- |
| 普通离线单元/契约测试 | GitHub Windows/Linux runner；不要求 COMSOL 或历史模型/结果 |
| `artifact` | 本机已有模型、历史 TXT/图片/元数据验收；显式选择后缺数据仍失败 |
| `integration` | 实际 COMSOL 安装、模型读取或求解；本轮未运行 |

22 项旧历史产物验收及 S0 两项产物检查标为 `artifact`。7 个模拟事务实例改用临时假模型，继续运行；S0 原模块级 integration 标记拆到真实操作上，恢复 15 项纯离线覆盖。CI 使用 `--strict-markers -m "not integration and not artifact"`，并上传 JUnit。这不是把失败测试全部隐藏为 skip；模拟测试保留，真实产物检查继续有独立入口。

原 Ruff 的 49 项诊断中，29 项位于冻结历史证据脚本。当前仅将 `docs/evidence` 排除于样式扫描；这些文件仍受既有 Git 字节/清单审计。当前维护代码中的导入排序和未使用导入已清理，不改写历史证据以消除 lint。静态仓库检查也改为检查当前入口，移除“尚无验证结果”的过时提示。

## 解耦范围

新增 `hydro_disturb_bench.experiments`，按职责拆分：

- `branch_execution.py`：执行、完成记录校验、采样和实施源码身份。
- `process_recording.py`：每次调用独立的进程记录器，保留原始 stdout/stderr、返回码、部分 payload 与向量。
- `solver_steps.py`：既有内部步进解析与判定，保持算法。
- `evidence_io.py`：JSON、哈希、向量字节校验与不可覆盖写入。

原分支 CLI 保留参数、冻结策略及阶段编排；幅值 CLI 通过公共服务复用执行逻辑，不再动态加载旧 CLI 或调用其八个私有助手。旧完成记录保持只读兼容，不要求向历史执行记录补写新字段。

桥接新增公开 `run_command(..., process_runner=...)`；执行器沿编译、batch、standalone 回退及采样每批显式传递。生产代码不再全局替换 `subprocess.run`。新执行记录绑定实际入口、服务模块、bridge 及两份 Java 源码，采样另绑定 field_sampling/field_sequence。新增测试覆盖重叠执行与无关子进程隔离、类型化失败、超时部分输出、损坏 payload、部分 U/UDot 留存，以及正式 TXT→NPZ→loader 采样链路。

本轮没有拆 Java 类、没有整体迁移 bridge 的文件事务层、没有更改数值公式、单位/时间来源、掩码或冻结物理计划。大型序列的流式读取和剩余跨模块私有引用也未在本轮重构。这里完成的是第一轮执行服务拆分，不宣称所有大模块已经消除。

## 验证与证据

| 检查 | 本地结果 |
| --- | --- |
| 完整离线回归，含本地产物验收 | **546 passed, 13 deselected in 79.49s**，Python 3.10.21，退出码 0 |
| 桥接执行器、采样与序列定向 | 143 passed（包含在后续完整回归中） |
| 两个分支入口及服务定向 | 100 passed（包含在后续完整回归中） |
| Ruff | `ruff check .` 通过，0 项诊断 |
| 锁文件 | `uv lock --check --offline` 通过 |
| 仓库入口与 diff | `scripts/check_repo.py`、`git diff --check` 通过 |

原始 [stdout](../evidence/code_health_ci_20261106/offline_stdout.txt)、[stderr](../evidence/code_health_ci_20261106/offline_stderr.txt)、[JUnit](../evidence/code_health_ci_20261106/offline_regression.xml) 与 [执行记录](../evidence/code_health_ci_20261106/offline_execution.json) 保存实际命令、解释器和返回码。[源码身份](../evidence/code_health_ci_20261106/source_identity.json) 是回归前工作目录字节的 SHA-256；测试前后相同，明确区别于 Git 可能进行 LF 规范化后的 blob 字节。

暂存检查另发现两个新增模块末尾有多余空白行；测试后仅移除这些行，Python AST 相同，实际前后哈希见 [格式变化记录](../evidence/code_health_ci_20261106/post_test_formatting.json)。远端 CI 检查最终提交字节。

原 514/30 与当前 546/13 不能直接理解为同一套选择增加 32 项：总集合从 544 增至 559（新增或参数化覆盖 15 项），同时有 17 项 S0 测试移出原模块级 integration 排除范围。实际数值实验没有因此增加。

本地新建 uv 环境的两次尝试均遇到 Windows `os error 6`：一次发生在下载解释器后的目录重命名，一次发生在解释器缓存临时文件持久化。本轮没有把它写成修复成功，也未修改系统安装；上述回归使用已有 Anaconda Python 环境。干净安装由本次提交的 [GitHub Actions Windows/Linux 作业](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/actions/workflows/tests.yml) 另行验证，其日志与 JUnit 是远端执行依据。

执行器隔离测试不代表共享编译目录或 COMSOL 已支持并发求解；新的进程传参路径经过离线替身和真实 Python 子进程核验，未新增一次真实 COMSOL 回归。后续拆 Java 类之前仍须完善额外 `.class` 的编译、复制和身份清单，并验证实际加载。

## 使用方式

```powershell
uv sync --locked --extra dev --extra analysis
uv run --no-sync ruff check .
uv run --no-sync pytest -q --strict-markers -m "not integration and not artifact"
uv run --no-sync python scripts/check_repo.py
```

持有历史数据时，再运行 `python -m pytest -q --strict-markers -m "not integration"`；只核验本地产物可选 `-m artifact`。不要求为普通代码检查上传大型 MPH/张量，不重新计算已验收的 A/B/C/D 或 M0–M3。
