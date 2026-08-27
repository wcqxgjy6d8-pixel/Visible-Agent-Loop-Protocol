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

## 完整工作流

```text
Doctor 观察当前能力真值
  -> 用户明确选择 Installation Leader
  -> 用户发布任务
  -> Leader 声明 WorkItem 与角色到 Agent 的分工
  -> VALP 验证能力、上下文、Skill、权限与证据契约
  -> Runtime adapter 预检、绑定 Worker session 与可见派送
  -> 提交回执 -> Worker 执行 -> 完成回执 + 预期证据
  -> 验证 -> 独立审查 -> 有界修复/复审
  -> 处理 Agent 建议 -> 审批 gate -> 最终总结 + feedback
  -> valp audit -> PASS / WARN / FAIL
  -> 独立受 gate 约束的任务状态 -> Done / Blocked / Failed
  -> 可选的确定性单任务 Task Graph 投影
```

![VALP 0.3 Doctor 到 Audit 工作流](docs/assets/valp-v03-open-core-overview.gif)

这张动图是解释图，不是 Runtime 或 release 证明。逐步语义见
[完整可见流程](docs/visual-flow.md)，可复核的真实 publish/dispatch 片段见
[公开过程证据](docs/case-studies/visible-dispatch-process-proof.md)。

Task Graph 只读显示单个任务已有的 receipts、evidence 与 audit 摘要，不能生成
证据或改变审计结果。Neo4j 不在本候选版中，也不是依赖；下一版可以把它作为
可选 ontology 投影，但它只能读取既有 task ledger，永远不能成为路由、证据或
审计权威。

## 谁选 Agent

这个权力边界是固定的：

```text
Doctor 扫描每个 Agent surface/session 的能力护照
  -> 用户明确选择 Leader
  -> Leader 拆解任务并声明分工
  -> VALP 核对能力、模型、权限、上下文和证据边界
  -> 通过后才能 dispatch
```

Doctor 把 `official_claim`、`local_presence`、`live_callable` 和
`task_verified` 四层证据分开，并记录当前 Agent 实际接入的
model、provider、reasoning mode、session identity、Skills、MCP、权限、
上下文与限制。信息缺失就标记 `unknown`，不从 Agent 名称
猜模型。

VALP 可以拒绝一份不符合当前证据的 Leader 分工，但不能自己
选 Agent，也不能偷偷换人。`selected_agents` 仅是旧格式兼容字段，
表示“Leader 已声明的 Agents”，不表示“VALP 选出的 Agents”。

通用 VALP 任务在严格审计后把结果交回用户就结束。代码托管、
branch、push、pull request 或 merge 是用户与其 Agent 自己的后续工作，
不是通用协议的内建步骤。

## 版本入口与兼容性

当前候选协议版本线是 `v0.3.0`，reference CLI package 为
`0.3.0`，可以从候选分支和精确 SHA 做明确评估。RFC 要求的真实
non-HERDR E2E、分 profile conformance、required checks、external review、merge、
不可变 tag、GitHub release metadata 和 post-release smoke 尚未全部完成；
在此之前它不是 stable release。`v0.2.0` 只保留为
旧运行复现与迁移来源；旧 tag 和 release 会保留为不可变的历史记录。升级与兼容规则见
[版本与兼容性](docs/versioning-and-compatibility.md)。

## v0.3.0 候选协议与参考 CLI

协议 wire/version target 是 `0.3.0`，当前 reference CLI package 是
`0.3.0`。[RFC 0001](docs/rfcs/0001-v0.3-installation-control-plane.md)
语义已纳入 `SPEC.md`、schemas 与 reference CLI，但 stable-version Done Criteria
尚未闭合。请看 [v0.3 implementation guide](docs/v0.3-implementation.md)。

如果把 Prompt、Tools、Agents 看成 Software 3.0 的执行层，VALP 更像外面的
控制与验收层：它不负责让模型突然更聪明，而是让控制决策和 done claim
可以被检查。`0.2.0` 建立了单个任务的证据纪律；`0.3.0` 将它扩展到整个
安装级控制平面，使重启、故障、Provider 变化和协议升级仍然可追溯。

`0.3.0` core 包括：

- 由用户明确选择的 **Installation Leader**，并由确定性 core 和 leader
  epoch 约束，而不是把某个 Agent 永久写死为总协调者；
- 持久能力注册表，把 `official_claim`、`local_presence`、`live_callable`
  和 `task_verified` 四层证据分开保存；
- 严格的 message、可执行 state、claim-evidence、确定性 failure 和针对
  精确 artifact 的独立 review 契约；
- Provider-neutral plugin manifest 检查、显式 migration，以及包含负面与恢复
  场景的 conformance tests。

当前是协议与 reference CLI 候选版，不表示任何 Runtime、Provider、
平台、生产部署或 stable release 已得到证明。

对外声明按证据包分层：协议与 reference CLI 由 schemas、测试和 bundled audits
证明；自动 Full Mode 由具体 runtime adapter 的 dispatch/session/evidence/review
链证明；跨重启自动 continuation 需要完整到 `resume_consumed` 的
provider-consumed ledger，以及在 provider 已消费、VALP 未落盘 receipt 之间
注入崩溃后的重启对账与去重证据；生产托管与平台支持必须有独立运行环境证明。没有这些
证据时，不把它写成默认能力。

## 开源核心与商业交付边界

这个仓库公开的是 MIT 许可的开源核心：协议、reference CLI、schemas、adapter
契约、示例和测试。企业安装迁移、私有系统集成、托管运行、监控、合规审查、
培训和支持服务属于独立的商业交付层，不随本仓库打包，也不把客户数据、凭证、
本机控制根目录或部署密钥带进来。详细边界见
[Open core and commercial boundary](docs/open-source-commercial-boundary.md)。

这里真正可推广的不是“VALP 内置 223 个 skill”。Skill 来自企业已经安装或接入的
Agent / Runtime 环境：Doctor 先扫描每个 Agent 实际可达的 skill；用户选择的
Leader 再拆解任务并分配 Worker；VALP 按工作项推荐 skill、过滤成每个 Worker
自己的 skill slice；Worker 先加载 control contract，再调用或明确跳过可达的
skill，并返回证据。完整流程见
[Skill 的发现、路由与 Worker 调用](docs/skill-recommendation.md)。

对企业可以这样说：**VALP 不要求你重新买一套 Agent 或 skill；它先盘点你已经
有的能力，再由 Leader 把任务分给合适的 Worker，让 Worker 调用自己确实可达的
skill，并把 dispatch、验证、审查和最终证据串起来。**

请把 [完整 RFC](docs/rfcs/0001-v0.3-installation-control-plane.md) 和
[当前证据矩阵](docs/project-status.md) 对照阅读：前者写已接受的协议契约，
后者写今天已经证明的实现与 Runtime 范围。

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
Summary: pass=14 warn=0 fail=0
```

再看 [Minimal audit demo](docs/minimal-audit-demo.md)，它会展示：
删掉 expected evidence 后，即使 receipt 说结果已提交，`valp audit` 也会失败。

## 它不是什么

VALP 不是：

- 一个托管平台；
- 一个模型集成方法；
- 一个固定绑定 HERDR 的私有工作流；
- 一个能自动证明所有 Agent 可靠的魔法层；
- 一个替用户选 Leader，或替 Leader 选 Agent 的调度器；
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
9. [开源核心与商业交付边界](docs/open-source-commercial-boundary.md)

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
