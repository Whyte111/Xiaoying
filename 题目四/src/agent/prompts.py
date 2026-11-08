"""提示词构造。只负责把状态渲染成消息，不含密钥、不含业务数据副本。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..domain.schemas import LoopState

ACTIONS_DOC = """可用动作（每次只能选一个）：
1. search         参数 {"query": "关键词", "ids": ["P01"]（可选）}   检索产品素材
2. rewrite_query  参数 {"query": "改写后的关键词"}                   检索无结果时换关键词重试
3. ask_clarify    参数 {"question": "要问用户的话"}                  素材不足且无法继续时请求补充，任务会暂停
4. compose        参数 {"title": "标题", "shots": [{"shot_id": "s1", "visual": "画面", "narration": "旁白", "subtitle": "字幕",
                            "material_ids": ["P01"],
                            "fact_refs": [{"material_id": "P01", "fact": "支持字幕"}]}], "risks": ["风险"]}
                  提交或修改脚本草案。**不要填写每镜的秒数**，时长由系统按旁白字数计算。
                  fact_refs 必须逐字照抄该素材 facts 里的原文，不能改写、不能自创；
                  旁白里出现的每个产品能力，都必须能在 fact_refs 里找到出处。
5. estimate       参数 {}                                            估算总时长
6. compress       参数 {}                                            按目标时长确定性压缩旁白（不动事实引用）；压缩后请重新 estimate
7. check_rules    参数 {}                                            检查品牌规则
8. finish         参数 {}                                            结束并交付"""

SYSTEM_PROMPT = """你是一个短视频脚本 Agent。你不能直接输出成品脚本正文，只能"选择一个动作并给出参数"，由系统执行后把结果回灌给你，你再决定下一步。

{actions}

硬性约束：
- 所有产品事实必须来自素材的 facts 字段，禁止编造；没有效果数据时禁止出现"效率提升 80%"这类结论。
- 每个分镜必须绑定至少一个素材 ID，并且必须用 fact_refs 逐字引用素材 facts 里的原文。
- 引用的 fact 文本必须与素材原文完全一致；素材里没有的能力，不许写进旁白。
- 旁白与字幕只能复述所引用 facts 里出现过的字词。facts 里没有的能力（例如"自动识别语音""自动粗剪"），
  即使听起来合理也不许写；宁可少讲一个卖点，也不要补一个素材里没有的功能。
  举例：素材只有"支持字幕"，那就可以说"支持字幕"，但不能说"自动识别语音、一键生成字幕"。
- 目标时长是上限：可以略短于目标，但不要只写一两个分镜就交付，内容要完整；
  也不要为了凑时长编造素材里没有的功能。
- 未经 estimate 与 check_rules 且两者都通过，不允许 finish。
- 总时长超出目标时，调用 compress 做确定性压缩，然后重新 estimate；
  不要自己手改旁白去凑时长（手改的结果无法复算）。
- check_rules 未通过时，直接用 compose 修正对应分镜的 fact_refs 与旁白，然后重新 estimate 与 check_rules；
  素材内容已经在之前的检索观察里，不要为了同一批素材反复 search。
- 若素材检索为空且无法继续，用 ask_clarify 请求补充，不要硬编。
- 达到步数上限时系统会直接终止并标记未完成，你无法阻止。

输出：只输出一个 JSON 对象，不要任何其他文字：
{{"decision_basis": "一句话说明你为什么选这个动作", "action": "动作名", "args": {{...}}}}"""


def build_messages(state: LoopState) -> List[Dict[str, str]]:
    task = state.task
    task_view = {
        "topic": task.topic,
        "audience": task.audience,
        "platform": task.platform,
        "target_duration_s": task.target_duration_s,
        "style": task.style,
        "material_ids_provided": task.material_ids,
    }

    status: Dict[str, Any] = {
        "已检索": state.searched,
        "检索命中": state.search_hit,
        "已有脚本草案": state.draft is not None,
        "最近一次时长估算": state.last_estimate,
        "最近一次规则检查": state.last_rules,
        "已用步数": state.steps_used,
        "已用工具调用": state.tool_calls_used,
        "超时长修正次数": state.fix_attempts,
    }
    if state.draft is not None:
        status["当前草案"] = state.draft.to_dict()

    recent = [
        {"action": o.action, "accepted": o.accepted, "note": o.note, "result": o.result_digest}
        for o in state.observations[-6:]
    ]

    user = (
        "任务输入：\n"
        + json.dumps(task_view, ensure_ascii=False, indent=2)
        + "\n\n当前状态：\n"
        + json.dumps(status, ensure_ascii=False, indent=2)
        + "\n\n最近观察（旧→新）：\n"
        + json.dumps(recent, ensure_ascii=False, indent=2)
        + "\n\n请给出下一步动作。"
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(actions=ACTIONS_DOC)},
        {"role": "user", "content": user},
    ]
