# 03 测试与评估

## 怎么跑

```bash
python -m unittest discover -s tests -v   # 41 条，约 1 秒
python examples/run_cases.py              # 重建 6 组离线用例
```

## 测试分布

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `tests/test_closed_loop.py` | 18 | 主链路、预算边界、失败、续跑、幂等、触顶、费用口径 |
| `tests/test_hardening.py` | 16 | 输入校验、数据 schema、状态损坏、超时、兜底计数 |
| `tests/test_llm_policy.py` | 7 | 模型输出解析、未知工具、网络异常、密钥不外泄 |

## 已验证（有测试断言）

| 能力 | 证据 |
|---|---|
| 正常四步完成、费用 = 方案 + 工具费 | `test_normal_with_replan`、`test_cost_identity` |
| 超预算改选方案（A→B） | trace 连续两条 `estimate_cost`，第二条注明改选 |
| 预算边界：8.61 完成、8.59 失败 | `test_budget_boundary_after_save` |
| 缺主题/预算/截止时间 → 0 次调用阻断 | `test_missing_*` |
| 非法预算（nan/inf/负/0）、超长主题 | `test_budget_must_be_finite_positive`、`test_long_topic_*` |
| 工具失败一次 → 降级恢复；永久失败 → 步骤保持 failed | `test_injected_failure_recovers`、`test_permanent_failure_*` |
| 保存超时 → `OUTCOME_UNKNOWN`，不重试 | `test_side_effect_timeout_is_outcome_unknown` |
| 重复执行 → 0 调用、同产物 | `test_repeat_execution_is_idempotent` |
| 失败续跑 → 只补失败步骤 | `test_resume_skips_succeeded_steps` |
| 触顶 → `stopped_by_limit` 且有原因 | `test_tool_call_limit`、`test_max_steps_has_reason` |
| 状态记录损坏 → 自愈，不崩 | `test_corrupt_state_entry_is_ignored` |
| 场景数据改一个字段 → 不复用旧结果 | `test_scenario_change_invalidates_cached_result` |
| 模型空口 finish → 拒绝 | `test_finish_without_save_is_rejected` |
| 模型全部降级 → 有计数 | `test_llm_fallback_is_counted` |

## 没有验证（明示）

| 项 | 原因 |
|---|---|
| 真实模型在引擎内的自动化测试 | 需要 Key 与网络，只保留两次人工运行记录（`examples/case6-llm`、`case7-llm-budget-gap`），模型输出不保证逐字复现 |
| 覆盖率数字 | 环境未安装 coverage，只有用例清单，没有百分比 |
| 并发执行同一任务 | `state.json` 读改写无锁，未做并发测试 |
| 磁盘只读、进程中断 | 未模拟 |
| 时间可行性 | 功能未实现（缺少“可用工时”输入），只测了格式校验 |
| 键盘中断后的状态一致性 | 未测 |

## 示例用例

| 用例 | 场景 | 结果 |
|---|---|---|
| `case1-normal` | 预算 10，A 超限改选 B | completed，8.6 |
| `case2-budget-gap` | 预算 5，两方案都超限 | incomplete，缺口 3.60 |
| `case3-tool-failure` | 注入脚本失败一次 | completed，降级恢复 |
| `case4-repeat` | 同输入跑两次 | 第二次 0 调用复用 |
| `case5-missing-deadline` | 缺截止时间 | incomplete，0 调用 |
| `case8-resume` | 保存永久失败后重跑 | 第二次只补保存 |
| `case6/7-llm` | 真实模型两次运行 | 模型自主改选 / 自主停止 |

细节见 `examples/README.md`。
