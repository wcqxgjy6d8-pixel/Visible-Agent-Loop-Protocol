# VALP 中文注解

根目录中文入口见 [../../README.zh-CN.md](../../README.zh-CN.md)。

本页是中文注解，不是协议规范原文。若本页与 `SPEC.md`、`schemas/` 或
`valp audit` 行为冲突，以英文规范和机器可验证规则为准。

## VALP 是什么

VALP（Visible Agent Loop Protocol）是一个面向多 Agent 工作流的可见证据协议。

它解决的不是“怎么让模型更聪明”，而是一个更朴素的问题：

```text
Agent 或 Runtime 说 done，用户凭什么相信？
```

VALP 要求任务过程留下可审计的证据：

- 谁被分配了任务；
- dispatch 有没有真的提交；
- 预期证据是什么；
- 证据文件是否存在；
- 验证、审查、审批是否通过；
- 最终结论引用了哪些证据。

所以 VALP 更像一个 acceptance system，而不是聊天提示词集合。

## v0.3 提案：从任务验收到安装级控制平面

当前发布版本仍是 `0.2.0`。
[RFC 0001](../rfcs/0001-v0.3-installation-control-plane.md) 整体仍未完成，
也没有达到稳定状态。它的 deterministic-wake 子集已在本地实现，并由仓库
测试覆盖；其余 installation control-plane 仍是提案，不改变当前 Runtime
支持范围或发布状态。

可以把变化理解成：`0.2.0` 主要检查“这个任务凭什么算 Done”，v0.3 提案
进一步检查“管理所有任务的安装级控制平面凭什么可信”。

| 层面 | 当前 `0.2.0` | v0.3 RFC 提案 |
|---|---|---|
| 控制主体 | 每个任务根据当前能力证据选择 coordinator | 用户明确选择 Installation Leader；确定性 core 和 epoch 负责约束与 fencing |
| 能力真值 | 当前 scan、routing、provider matrix 和 task evidence | 持久 registry 分开记录 `official_claim`、`local_presence`、`live_callable`、`task_verified` |
| 执行契约 | task receipts、expected evidence、review、approval、audit | 严格 message、event-sourced state、claim-evidence、deterministic failure、exact-artifact review |
| Provider 边界 | Runtime adapter 导出同等 receipts 和 evidence | Provider plugin 使用 manifest、最小权限和隔离边界，不能直接改协议 core state |
| 稳定证明 | 仓库测试与 bundled examples；live E2E 仍有公开缺口 | 必须补齐实现、迁移、负面/恢复 conformance，以及真实非 HERDR Full Mode E2E |

这里最重要的不是多几个名词，而是 proof bar：用户选择 Leader 不等于信任
Leader；发现 CLI、Skill 或 MCP 不等于已经能调用；Runtime completed 仍不等于
VALP Done；写完 RFC 更不等于功能已经发布。

稳定 `0.3.0` 只有在 RFC 被接受并写入 `SPEC.md`、相关 schemas/reference
behavior 已实现、重启和迁移等 conformance tests 通过、且真实非 HERDR
adapter 完成公开脱敏 E2E 后才能成立。请同时查看
[当前项目状态](../project-status.md)，不要把 proposed target 当成当前证明。

## 它不是什么

VALP 不是：

- 一个托管平台；
- 一个模型集成方法；
- 一个固定绑定 HERDR 的私有工作流；
- 一个能自动证明所有 Agent 可靠的魔法层；
- 一个替代测试、代码审查、审批流程的工具。

HERDR 只是当前参考 Runtime。其他 Runtime 只要能导出同等的 receipts、
evidence、state mapping 和 audit 数据，也可以实现 VALP。

## 最小体验路径

如果你只是想理解协议，不需要先安装 Runtime：

```bash
git clone https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol.git
cd Visible-Agent-Loop-Protocol
python -m pip install -r requirements-dev.txt
bin/valp audit examples/minimal-task
```

通过时你会看到：

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0
```

再看这个演示：

- [Minimal audit demo](../minimal-audit-demo.md)

它会让你看到：删掉 expected evidence 后，即使 receipt 说结果已提交，
`valp audit` 也会失败。

## 核心概念

| 英文术语 | 中文注解 |
|---|---|
| Runtime | 实际执行和记录任务的运行层，例如 pane controller、queue、hosted platform 或人工手动流程 |
| Dispatch | 发给某个 Agent 的可见任务分配 |
| Receipt | 机器可读的状态收据，例如 `dispatch_submitted`、`dispatch_completed` |
| Evidence | 能证明工作发生过的文件、日志、截图、命令输出、审查记录 |
| Expected evidence | 这个任务完成前必须出现的证据路径 |
| Audit | 用 `valp audit` 检查任务证据是否满足 Done Criteria |
| Correction cycle | 退件、重试、blocked、invalid、superseded 后的修复闭环记录 |
| Final synthesis | 最终总结，必须指出结果、决策、分歧和证据缺口 |

完整术语表见 [glossary.md](glossary.md)。

## 面向中文用户的理解方式

可以把 VALP 想成“多 Agent 工作流的验收单”：

```text
不要只听 Agent 说 done
要看 dispatch、receipt、evidence、review、approval、final synthesis
最后跑 audit
```

这对中文社区尤其重要，因为很多 Agent 演示容易停留在“看起来完成了”。
VALP 关心的是：这个完成状态能不能被另一个人、另一个 Agent、或者 CI
重新检查。

## 推荐阅读顺序

1. 先读本页。
2. 跑 `bin/valp audit examples/minimal-task`。
3. 读 [glossary.md](glossary.md)。
4. 读 [../minimal-audit-demo.md](../minimal-audit-demo.md)。
5. 读 [../when-agent-done-is-not-done.md](../when-agent-done-is-not-done.md)。
6. 读 [../failure-gallery.md](../failure-gallery.md)。
7. 对照读 [v0.3 RFC](../rfcs/0001-v0.3-installation-control-plane.md) 和
   [当前项目状态](../project-status.md)。
8. 再读英文 [SPEC.md](../../SPEC.md)。

如果要实现 Runtime adapter，直接读英文：

- [Adapter checklist](../adapter-checklist.md)
- [Runtime adapters](../runtime-adapters.md)
- [Dispatch receipts](../dispatch-receipts.md)
- [Correction cycle evidence](../correction-cycle.md)
