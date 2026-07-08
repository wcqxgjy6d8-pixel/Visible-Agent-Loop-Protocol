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
Summary: pass=13 warn=0 fail=0 skip=7
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
3. [中文注解](docs/zh-CN/README.md)
4. [失败案例图鉴](docs/failure-gallery.md)
5. [Runtime adapter checklist](docs/adapter-checklist.md)
6. [社区参与说明](docs/community.md)

如果你要实现 Runtime adapter，优先读：

- [Runtime adapters](docs/runtime-adapters.md)
- [Adapter checklist](docs/adapter-checklist.md)
- [Dispatch receipts](docs/dispatch-receipts.md)
- [Correction cycle evidence](docs/correction-cycle.md)
