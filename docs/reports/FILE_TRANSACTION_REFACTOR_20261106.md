# 文件事务层拆分（2026-11-06）

本轮基线为 `c28aded6090fecdf287fee7f5b6cbc27f3ff651d`。在已通过 Windows/Linux CI 的执行服务拆分之后，将文件事务从 COMSOL 启动与模型操作代码中分离，以便独立检查锁、发布与失败恢复。没有新增 CFD 求解、网格、扰动工况或数据集。

## 修改与兼容性

| 模块 | 本轮职责 |
| --- | --- |
| `comsol_java/bridge.py` | 保留原生启动、模型操作和公开兼容入口；1,925 → 1,240 行 |
| `comsol_java/file_transactions.py` | 14 个既有函数：进程/租约、发布锁、文件发布、隔离与恢复；656 行 |
| `comsol_java/errors.py` | 8 个错误码、9 个异常类及映射函数；80 行 |

事务模块仅依赖标准库及错误契约，不回导 bridge。原 `comsol_java` 和 `comsol_java.bridge` 的公开函数、异常导入仍指向同一实现对象，签名和错误消息保持。旧调用方使用的私有别名也保留；两个故障注入测试改为替换真正所属模块的 helper，不通过动态回调或全局模块代理维持旧的私有 patch 位置。

新分支及采样执行记录增加 `errors.py`、`file_transactions.py` 的实际源码哈希，已有执行记录继续只读兼容。Java 源码、数值公式、单位/时间/掩码契约和历史物理计划未修改。

[AST 提取审计](../evidence/file_transactions_20261106/extraction_audit.json) 对照固定基线，逐项记录全部 **46 个函数/类及 8 个错误常量**：AST 一致，包含签名、装饰器、语句和文档字符串，排除文件位置。它证明本轮移动的函数体没有被顺便修改；跨模块运行行为仍需由回归测试验证。

## 验证与执行记录

新增 7 项测试覆盖公开别名/异常身份、空产物隔离、恢复失败后 stage 与 lease 留存并可重试、目录隔离失败保留字节，以及真实 Python 子进程中的发布锁超时、活跃租约保护和释放后的恢复。旧 API/子进程测试继续从 bridge 导入，故障注入仍运行正式发布与恢复函数。

定向原始 [stdout](../evidence/file_transactions_20261106/targeted/stdout.txt)、[stderr](../evidence/file_transactions_20261106/targeted/stderr.txt)、[执行记录](../evidence/file_transactions_20261106/targeted/execution.json) 包含 **49 passed in 5.09s**，pytest 返回 0。外围留证脚本在文件写完后的最后一条目录打印遇到 Windows GBK 无法编码 emoji，外层返回 1，单列于 [wrapper_status.json](../evidence/file_transactions_20261106/targeted/wrapper_status.json)；没有把外层失败改写成成功。

| 检查 | 实际本地结果 |
| --- | --- |
| 完整离线回归，含历史产物 | **553 passed, 13 deselected in 83.32s**，Python 3.10.21，退出码 0 |
| 测试期间源码与配置 | 66 个文件的测试前后哈希一致 |
| Ruff / 锁文件 | `ruff check .` 与 `uv lock --check --offline` 通过 |
| 仓库入口 / diff | `python scripts/check_repo.py` 与 `git diff --check` 通过 |

完整回归的 [stdout](../evidence/file_transactions_20261106/offline_stdout.txt)、[stderr](../evidence/file_transactions_20261106/offline_stderr.txt)、[JUnit](../evidence/file_transactions_20261106/offline_regression.xml)、[执行记录](../evidence/file_transactions_20261106/offline_execution.json) 与 [源码身份](../evidence/file_transactions_20261106/source_identity.json) 分别保存。49 项定向包含在 553 项中；13 项真实 COMSOL 集成测试未选择。历史产物只读检查计入本地回归，但不要求普通 GitHub runner 持有这些大文件。

远端继续使用已有 Windows/Linux uv 工作流，选择 `not integration and not artifact`；实际结果以本轮固定提交对应的 [Actions 原始日志与 JUnit](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/actions/workflows/tests.yml) 为准。

## 边界与后续

这次只拆分实现位置，保留原事务策略。历史 Windows 目录发布 `WinError 5` 的根因仍未定位；函数体移动和测试通过不代表该故障已修复。也未改变诊断目录发布策略、POSIX 底层调用或共享编译目录，不宣称支持并发 COMSOL 求解。

桥接仍保留原生启动、模型操作与诊断编排；Java 大类、序列全量拼接和采样跨模块私有助手尚未整体拆分。不继续仅以文件行数为目标拆代码。

下一块功能工作优先使用已有连续基线和分支，增加有界内存读取与窗口索引；先固定历史/预测窗口及轨迹族划分，再构造样本。当前所有兄弟分支只有一个共同 checkpoint，不能随机拆成独立训练/验证集，也不据此启动泛化结论。该数据接口尚未在本轮实现。

交付继续轻量化：源码、报告和小型日志入 Git；没有上传模型/完整张量，也没有改写已验收历史证据。
