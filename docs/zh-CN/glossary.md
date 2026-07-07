# VALP 术语中文注解

本页是中文解释层，不改变协议含义。字段名、receipt 状态、schema 名称和
审计规则以英文 `SPEC.md`、`schemas/` 和 `valp audit` 为准。

## 核心对象

| Term | 中文注解 | 说明 |
|---|---|---|
| VALP task | VALP 任务 | 用户发布的一次工作单元，包含路由、证据、收据、审查、审批和最终总结 |
| Runtime work item | Runtime 工作项 | Runtime 内部的执行单元，例如 queue item、pane submission、hosted run |
| Runtime | 运行层 | 负责提交任务、读取输出、记录状态和导出证据的系统 |
| Adapter | 适配层 | 把具体 Runtime 的状态转换成 VALP receipts 和 evidence |
| Agent session | Agent 会话 | Agent 接收任务并产出结果的地方，可以是终端 pane、queue job、hosted thread 或人工流程 |

## 证据和收据

| Term | 中文注解 | 说明 |
|---|---|---|
| Dispatch | 任务分配 | 发给 Agent 的可见指令 |
| Receipt | 收据 | 机器可读的 dispatch 状态记录 |
| Evidence | 证据 | 文件、日志、截图、命令输出、review、findings、final synthesis |
| Expected evidence | 预期证据 | 完成任务前必须出现的 task-relative 路径 |
| Evidence status | 证据状态 | `valid`、`superseded`、`invalid`、`rejected`、`blocked` |
| Final synthesis | 最终综合 | 记录结果、决策、分歧、证据缺口和引用证据 |

## Receipt 状态

| Receipt state | 中文注解 | 是否足够完成 |
|---|---|---|
| `dispatch_written` | dispatch 文件已写出并可见 | 不够 |
| `dispatch_inserted` | 文本进入输入框 | 不够，不等于提交 |
| `dispatch_submitted` | Runtime 证明已提交 | 不够，仍需 expected evidence |
| `dispatch_completed` | expected evidence 已出现 | Full/Remote Mode 还需要 prior submission proof |
| `dispatch_blocked` | 提交或完成无法证明 | 不够，需要修复或升级 |
| `manual_result_attested` | 人工模式下人工证明结果证据存在 | 只适用于 Manual Mode |

## 关键门槛

| Term | 中文注解 | 说明 |
|---|---|---|
| Done Criteria | 完成标准 | `SPEC.md` 中定义的任务完成条件 |
| Audit | 审计 | `valp audit` 将 Done Criteria 变成可执行检查 |
| Review findings | 审查发现 | critical/high 未解决时不能 done |
| Approval gate | 审批门槛 | release、deploy、auth、secrets、destructive reset 等高风险动作需要审批证据 |
| Correction cycle | 修复闭环 | 记录 rejected、retried、blocked、invalid、superseded 后如何修复 |
| Routing feedback | 路由反馈 | 任务结束后的经验记录，是未来路由的 prior，不是当前任务的证明 |

## Runtime 模式

| Mode | 中文注解 | 说明 |
|---|---|---|
| Full Mode | 完整自动模式 | Runtime 能导出提交证明、状态、收据、证据和审计数据 |
| Remote Mode | 远程模式 | Runtime 在远端主机执行，需要远程 proof caveats |
| Manual Mode | 人工模式 | 人复制 dispatch 和结果，只能作为人工证据流，不能冒充 Full Mode |
| Auto Visible Mode | 自动可见入口 | Runtime 或策略自动发布任务，但必须可见，不能静默执行高风险动作 |

## 常见误解

| 误解 | 正确理解 |
|---|---|
| Runtime 显示 completed 就是 VALP done | 不对，还要 receipts、expected evidence、review、approval、final synthesis 和 audit |
| 文字粘进输入框就是 dispatch 成功 | 不对，只能算 `dispatch_inserted` |
| 有 review 文件就一定完成 | 不对，review 只是证据之一 |
| Manual Mode 可以冒充 Full Mode | 不对，人工 attestation 不能变成 runtime submission proof |
| routing feedback 可以替代新任务扫描 | 不对，它只是 prior，每个新任务还要 fresh scan |
| 中文注解可以覆盖英文协议 | 不对，中文只是解释层 |

