# Visible Agent Loop Protocol 中文入口

本页是中文入口，不是协议规范原文。若本页与 `SPEC.md`、`schemas/` 或
`valp audit` 行为冲突，以英文规范和机器可验证规则为准。

VALP 是一个面向多 Agent 工作流的可见证据协议。它解决的问题很窄：

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

所以 VALP 更像一个多 Agent 工作流的验收单，而不是聊天提示词集合。

## v0.3 Draft Implementation

当前稳定发布版本仍是 `0.2.0`。[RFC 0001](docs/rfcs/0001-v0.3-installation-control-plane.md)
作为稳定 `0.3.0` 仍未完成，但可执行的 installation control plane core 已经
落地在 reference CLI 中。请看 [v0.3 implementation guide](docs/v0.3-implementation.md)。

如果把 Prompt、Tools、Agents 看成 Software 3.0 的执行层，VALP 更像外面的
控制与验收层：它不负责让模型突然更聪明，而是让控制决策和 done claim
可以被检查。`0.2.0` 主要验证单个任务；v0.3 RFC 进一步追问，整个安装级
控制平面能不能在重启、故障、Provider 变化和协议升级后仍然可追溯。

已落地的 draft core 包括：

- 由用户明确选择的 **Installation Leader**，并由确定性 core 和 leader
  epoch 约束，而不是把某个 Agent 永久写死为总协调者；
- 持久能力注册表，把 `official_claim`、`local_presence`、`live_callable`
  和 `task_verified` 四层证据分开保存；
- 严格的 message、可执行 state、claim-evidence、确定性 failure 和针对
  精确 artifact 的独立 review 契约；
- Provider-neutral plugin manifest 检查、显式 migration，以及包含负面与恢复
  场景的 conformance tests。

稳定 `0.3.0` 的门槛仍在未来：RFC 需要被接受并写入 `SPEC.md`，schemas 和
reference behavior 需要实现，重启、迁移、故障与 conformance tests 需要
通过，还必须有一个真实的非 HERDR Full Mode adapter 完成公开、脱敏的
端到端验证。当前仓库交付的是 draft core，不是稳定平台能力。

请把 [完整 RFC](docs/rfcs/0001-v0.3-installation-control-plane.md) 和
[当前证据矩阵](docs/project-status.md) 对照阅读：前者写 proposed target，
后者写今天已经证明的范围。

## 五分钟体验

不需要先安装 Runtime：

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

再看 [Minimal audit demo](docs/minimal-audit-demo.md)，它会展示：
删掉 expected evidence 后，即使 receipt 说结果已提交，`valp audit` 也会失败。

## 它不是什么

VALP 不是：

- 一个托管平台；
- 一个模型集成方法；
- 一个固定绑定 HERDR 的私有工作流；
- 一个能自动证明所有 Agent 可靠的魔法层；
- 一个替代测试、代码审查、审批流程的工具。

HERDR 只是当前参考 Runtime。其他 Runtime 只要能导出同等的 receipts、
evidence、state mapping 和 audit 数据，也可以实现 VALP。

## 推荐阅读

1. [英文 README](README.md)
2. [协议规范 SPEC.md](SPEC.md)
3. [v0.3 Installation Control Plane RFC](docs/rfcs/0001-v0.3-installation-control-plane.md)
4. [中文注解](docs/zh-CN/README.md)
5. [When Agent "Done" Is Not Done](docs/when-agent-done-is-not-done.md)
6. [失败案例图鉴](docs/failure-gallery.md)
7. [Runtime adapter checklist](docs/adapter-checklist.md)
8. [社区参与说明](docs/community.md)

当前讨论入口：

- [RFC: Phase 0 public evaluation](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/8)
- [Runtime adapter checklist feedback](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/9)

适合新贡献者的任务：

- [Run the adapter checklist against one runtime](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/10)
- [Add one false-done case to the failure gallery](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/11)
- [Improve the Pages demo for Agent done is not done](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/12)

如果你要实现 Runtime adapter，优先读：

- [Runtime adapters](docs/runtime-adapters.md)
- [Adapter checklist](docs/adapter-checklist.md)
- [Dispatch receipts](docs/dispatch-receipts.md)
- [Correction cycle evidence](docs/correction-cycle.md)
