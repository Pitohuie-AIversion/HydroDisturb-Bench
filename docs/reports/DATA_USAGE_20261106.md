# 已有连续数据的使用层（2026-11-06）

代码基线为 `4b7f74c658739908532b15277bb46a6495b02aad`。本轮消费已有 M3 数据，实现按批读取、跨批时间窗口与按轨迹族划分；未读取 MPH/原生向量二进制、未启动 COMSOL、未生成或提交完整场张量副本。

## 真实数据结果

| 序列 | 帧数 | 批数 | 5 历史 + 5 未来、stride=5 的窗口数 | 分区 |
| --- | ---: | ---: | ---: | --- |
| baseline，170–200 s | 151 | 5 | 29 | train |
| zero，180–200 s | 101 | 4 | 19 | train |
| a0025，180–200 s | 101 | 4 | 19 | train |
| a0050，180–200 s | 101 | 4 | 19 | train |
| a0100，180–200 s | 101 | 4 | 19 | train |
| 合计 | **555** | **21** | **105** | **一个祖先族** |

这些序列均来自同一个 M3 原始模型及共同 checkpoint。入口先读取固定的计划、prepare、各分支 execution 和 U/UDot 身份 JSON，再将它们关联为同族。没有把不同输出 MPH 的哈希误当作独立起点；validation/test 均为空，不能据此声称已完成独立训练/验证评估。

真实执行核验了 **23 个固定 JSON、21 个 NPZ 及本轮配置，共 45 个输入文件**，前后哈希一致。来源核验使用已接受记录中的模型/原生状态身份，没有重新读取模型或向量来证明物理结果。配置、执行源码的实际字节哈希也分别记录，审计期间一致。

[实际结果](../evidence/data_usage_20261106/report.json) · [105 窗口索引](../evidence/data_usage_20261106/index.json) · [来源链与输入哈希](../evidence/data_usage_20261106/lineage_audit.json) · [实际命令、解释器、返回码及源码身份](../evidence/data_usage_20261106/real_execution/execution.json) · [stdout](../evidence/data_usage_20261106/real_execution/stdout.txt)

## 数值读回对照

使用固定基线提交中的旧 `field_sequence.py` 在内存加载参考；其共享 `field_sampling.py` 本轮未修改。新接口的 21 批、105 个窗口和五条轨迹的末三帧均与参考逐项相同，包含 NaN 位置；窗口中有 **28 个跨批窗口**。每条轨迹最后一个完整窗口之后剩 1 帧，按预定 stride 不补窗，该末帧仍通过全批与末尾直接读取校验。

对照执行了 **1,022 次数组比较**、110,541,310 次元素比较，其中包含重复窗口、掩码、坐标和时间，不能解释为同等数量的独立流场数值或样本。有限数值的实际最大绝对差为 **0**；它证明读取和切片与同一保存数据一致，不是 CFD 真解误差为零。

完整索引文件 SHA-256：`63004756d70b381a51a63106fd81035dbd6f5594ae3865d66ef1daf9543575b5`。索引保存后按该身份重新加载，结构完全一致。当前索引使用实际绝对数据路径；远程复核可读轻量结构，完整数值重验仍需本地 NPZ。

## 实现与防泄漏边界

- `FieldSequenceReader` 在构造时校验清单覆盖/时间/解编号/目录；访问时检查清单及相关批次哈希，保留来源、坐标、掩码、单位与时间契约。`iter_batches()` 和 `verify_all()` 不拼接字段，`read_frames()` 预分配所需窗口；弱引用测试检查历史批数组被释放。旧全量 loader 保持原接口，旧导出仍有完整读回步骤。
- `data_windows` 先分族，再生成半开窗口；显式分配必须完整，未来紧接历史，窗口不跨轨迹。共享模型/祖先/checkpoint/原生状态、清单或同字节 NPZ 的记录不能被改名放进不同族。拒绝改写窗口边界、重复 ID、错误源清单及重算摘要后仍不符合规格的划分。
- 通用库的祖先字段由调用方提供，不自动推断血缘；真实 M3 入口另行验证固定来源链。重复数据门使用 NPZ 字节哈希，不宣称识别任意重新压缩后的数值相同数组。索引摘要防意外改写，不是签名认证。
- 学习器只收到八个数组：history/future、对应 mask/times、x/y。幅值、模型路径、族和来源记录保留在管理索引中，不进入学习器返回值。不自动填 NaN、做全数据归一化或训练模型。
- 局部读取只验证所访问批次，不能提前宣称其余批次正常；创建索引前会完成 `verify_all()`。通常读取所需场内存随批大小及请求窗口增长；对照审计另行按轨迹保留一个完整旧参考，不把审计路径宣称为同样的内存界限。

使用入口与重现命令见 [DATA_USAGE](../DATA_USAGE.md)。输出路径在创建前检查，禁止放进输入序列或来源证据目录；已有输出不覆盖。

## Windows 发布失败记录与测试边界

最初窗口测试通过正式 exporter 生成夹具，三次分别出现 **36 passed/1 failed、36 passed/1 failed、29 passed/8 failed**；失败均在生成输入时最后的 Windows `os.rename`，尚未进入窗口断言。保留 [首次工具输出转录](../evidence/data_usage_20261106/tests/window_tests_first/tool_output.txt)、[第二次原始日志](../evidence/data_usage_20261106/tests/window_tests_repeat/stdout.txt) 和 [D 盘复测原始日志](../evidence/data_usage_20261106/tests/window_tests_d_temp/stdout.txt)。首次没有原始字节重定向，已明确标为工具返回转录，其余执行记录和 stdout/stderr 原样留存。

另对固定旧版本和当前版本交替运行各 10 次小型解析信号导出，不做重试：两者均 **8 成功、2 失败**，四次均是最终 rename 的 WinError 5，试次前后句柄数均 240→240。[逐次结果与 traceback](../evidence/data_usage_20261106/rename_comparison/results.json)、[实际执行](../evidence/data_usage_20261106/rename_comparison/process_execution.json)、[句柄与关闭路径检查](../evidence/data_usage_20261106/rename_comparison/handle_review.json) 保留。这个小样不支持“新 Reader 独有引入”，也不足以证明长期失败率相同；实际占用者仍未定位。

只读窗口及入口测试随后改为直接建立小型解析信号 NPZ/metadata/manifest，保留全部业务断言并通过正式 reader/loader 校验。窗口 **37 passed**、入口 **16 passed**；旧 TXT→NPZ、导出发布与采样回归未删除或改为跳过。此调整分离只读功能与无关的输入发布前置，不宣称修复 WinError 5。成功定向 [窗口日志](../evidence/data_usage_20261106/tests/window_tests_saved_inputs/stdout.txt) 与 [入口日志](../evidence/data_usage_20261106/tests/entrypoint/stdout.txt) 单列，不能与完整回归相加。

完整离线回归为 **660 passed, 13 deselected in 83.42s**，Python 3.10.21，退出码 0；包含历史产物验收，未选择真实 COMSOL 集成测试。测试期间 73 个源码/配置文件哈希一致，Ruff、`uv lock --check --offline`、仓库入口检查通过。[stdout](../evidence/data_usage_20261106/regression/stdout.txt) · [stderr](../evidence/data_usage_20261106/regression/stderr.txt) · [JUnit](../evidence/data_usage_20261106/regression/junit.xml) · [命令/返回码](../evidence/data_usage_20261106/regression/execution.json) · [源码身份](../evidence/data_usage_20261106/regression/source_identity.json)。

暂存的三个失败日志中有 pytest traceback 自带的 10 处行末空格，原样保留；完整 `git diff --cached --check` 会报告这些位置。仅排除这三个日志后的 diff 检查通过，未修改归档规则来隐藏诊断，也未修剪原始失败输出。索引和配置的 Git 暂存 blob 哈希已与真实审计记录核对一致。

远端使用同一 uv 工作流在 Windows/Linux 选择 `not integration and not artifact`，以固定提交对应的 [Actions 日志与 JUnit](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/actions/workflows/tests.yml) 为执行依据。定向、完整回归与真实数据审计的检查量分别记录，不叠加为独立测试数量。

## 交付范围

Git 保存代码、注册配置、包含 105 条窗口记录的轻量索引、来源摘要与测试/诊断日志。完整模型、场张量及失败导出样例目录继续留在本地，不纳入提交。历史冻结计划、原始采样产物和之前的证据目录不改写。
