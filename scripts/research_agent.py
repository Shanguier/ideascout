#!/usr/bin/env python3
"""IdeaScout reference pipeline.

Offline, deterministic demo of one process walking the eight roles in
SKILL.md. Source cards are simulated. This script does not search the
web and does not call a model. The printed score is a reference metric.

Usage:
    python3 scripts/research_agent.py "小米 SU7 vs Model 3 对比分析"
    python3 scripts/research_agent.py --interactive
    python3 scripts/research_agent.py --json --max-rounds 1 "topic"
"""

from __future__ import annotations

import argparse
import json
import re
import sys

__version__ = "0.3.0"

# One process plays these roles in order. They are not separate agents.
ROLES = (
    "orchestrator",
    "planner",
    "scout",
    "extractor",
    "validator",
    "analyst",
    "synthesizer",
    "quality_evaluator",
)

SOURCE_TRUST = {
    "official": 100,      # 官方数据、一手资料
    "professional": 80,   # 专业媒体
    "community": 60,      # 用户实测、社区
    "blog": 40,           # 自媒体
    "unknown": 20,        # 匿名/未标注
}

# Reference score, not a conclusion. Terms are rounded to one decimal
# before weighting so a reader can recompute the printed total. The
# decimal is for that check, not a claim of measurement precision.
# multi_source and consistency are split on purpose: a contradiction
# lowers consistency only, even when the fact has several sources.
SCORE_WEIGHTS = {
    "source_trust": 0.3,
    "timeliness": 0.2,
    "multi_source": 0.3,
    "consistency": 0.2,
}

COMPONENT_LABELS = {
    "source_trust": "来源可信度",
    "timeliness": "时效性",
    "multi_source": "多源覆盖",
    "consistency": "一致性",
}

CONFIDENCE_THRESHOLD = 80
GAP_TRIGGER = 2
STALE_DAYS = 90
DEFAULT_MAX_ROUNDS = 3

# Plant one disagreement on the third sub-question. Round 2 adds an
# agreeing official card beside the professional card, so that side has
# two cards and a higher best trust. A lone official card would not close
# it. Later sub-questions stay single-source until round 3, so the
# coverage gate (two or more missing second sources) keeps round 3 alive.
DEMO_CONFLICT_INDEX = 2
OFFICIAL_EARLY_LIMIT = 3

_COMPARISON = re.compile(r"(?i)\bvs\.?\b|\bversus\b|\bcompared\b|对比|相比|比较")
_CJK = re.compile(r"[\u4e00-\u9fff]")
# History and principle topics keep old primary sources. Age still feeds
# the printed timeliness term, but it does not force another Deep Dive.
_DURABLE_TOPIC = re.compile(
    r"(?i)\b(?:histories|history|historical|principles|principle|"
    r"theorems|theorem|definitions|definition|origins|origin|"
    r"theories|theory|theoretical|philosophy|philosophical)\b"
    r"|历史|原理|原则|定理|定义|起源|由来|理论|哲学"
)

_DIMS = {
    "zh": {
        "comparison": [
            "价格与获取成本",
            "能力与性能",
            "体验与口碑",
            "风险与限制",
            "支持与后续成本",
        ],
        "generic": [
            "背景与定义",
            "关键事实",
            "主要方案",
            "风险与限制",
            "近期变化",
        ],
    },
    "en": {
        "comparison": [
            "Price and access",
            "Capability and performance",
            "Experience and reputation",
            "Risks and limits",
            "Support and ongoing cost",
        ],
        "generic": [
            "Background",
            "Key facts",
            "Main approaches",
            "Risks and limits",
            "Recent changes",
        ],
    },
}


def configure_stdio() -> None:
    """Prefer UTF-8 so the Chinese demo text survives a non-UTF-8 console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def freshness(age_days: float) -> float:
    """0.5 points lost per day of age, clamped to 0–100."""
    return max(0.0, min(100.0, 100.0 - float(age_days) * 0.5))


def freshness_policy(topic: str) -> str:
    """Return ``durable`` for history/principle topics, else ``current``."""
    return "durable" if _DURABLE_TOPIC.search(topic) else "current"


def score_confidence(validated_facts: list[dict]) -> tuple[float, dict]:
    """Return (reference score, components) for the documented weights.

    Multi-source coverage is the share of facts with two or more sources.
    Consistency is the share with no open contradiction. An empty fact
    list scores 0. Unknown source types use the unknown trust.
    """
    empty = {
        "source_trust": 0.0,
        "timeliness": 0.0,
        "multi_source": 0.0,
        "consistency": 0.0,
    }
    count = len(validated_facts)
    if count == 0:
        return 0.0, empty

    trust_values = [
        SOURCE_TRUST.get(fact.get("source_type", "unknown"), SOURCE_TRUST["unknown"])
        for fact in validated_facts
    ]
    fresh_values = [freshness(fact.get("age_days", 180)) for fact in validated_facts]
    multi_hits = sum(1 for fact in validated_facts if fact.get("source_count", 1) >= 2)
    consistent_hits = sum(
        1 for fact in validated_facts if not fact.get("contradicted", False)
    )

    components = {
        "source_trust": round(sum(trust_values) / count, 1),
        "timeliness": round(sum(fresh_values) / count, 1),
        "multi_source": round(multi_hits / count * 100, 1),
        "consistency": round(consistent_hits / count * 100, 1),
    }
    score = round(
        sum(components[key] * SCORE_WEIGHTS[key] for key in SCORE_WEIGHTS),
        1,
    )
    return score, components


def loop_score(components: dict, policy: str) -> float:
    """Score the 80-point loop check uses.

    History and principle topics drop timeliness from this check and
    rescale the other three weights. The printed reference score still
    includes timeliness.
    """
    if policy != "durable":
        raise ValueError("loop_score is only defined for the durable policy")
    weight = 1.0 - SCORE_WEIGHTS["timeliness"]
    rest = sum(
        components[key] * SCORE_WEIGHTS[key]
        for key in SCORE_WEIGHTS
        if key != "timeliness"
    )
    return round(rest / weight, 1)


def gate_failures(
    score: float,
    facts: list[dict],
    gaps: list[str],
    contradictions: list[dict],
    *,
    policy: str = "current",
    components: dict | None = None,
) -> list[str]:
    """Reasons the quality gate is still closed. Empty means stop."""
    reasons = []
    if policy == "durable":
        if components is None:
            raise ValueError("durable freshness policy requires score components")
        checked = loop_score(components, policy)
        if checked < CONFIDENCE_THRESHOLD:
            reasons.append(
                f"循环检查分 {checked:.1f}/100 < {CONFIDENCE_THRESHOLD}"
                "（历史/原理类话题不把时效性算进这道门槛）"
            )
    elif score < CONFIDENCE_THRESHOLD:
        reasons.append(f"参考分 {score:.1f}/100 < {CONFIDENCE_THRESHOLD}")
    unresolved = [item for item in contradictions if not item.get("resolved", False)]
    if unresolved:
        reasons.append(f"未解决矛盾 {len(unresolved)} 个")
    if len(gaps) >= GAP_TRIGGER:
        reasons.append(f"缺少第二来源的维度 {len(gaps)} 个")
    if policy != "durable":
        stale = [fact for fact in facts if fact.get("age_days", 180) > STALE_DAYS]
        if stale:
            reasons.append(f"超过 {STALE_DAYS} 天的事实 {len(stale)} 条")
    return reasons


def plan_subquestions(topic: str) -> list[str]:
    """Split a topic into five research questions that name the topic."""
    topic = topic.strip()
    if not topic:
        raise ValueError("topic is empty")
    language = "zh" if _CJK.search(topic) else "en"
    kind = "comparison" if _COMPARISON.search(topic) else "generic"
    return [f"{topic} · {dim}" for dim in _DIMS[language][kind]]


def _source(question: str, source_type: str, age_days: int, agrees: bool) -> dict:
    return {
        "title": f"{question} [{source_type}]",
        "type": source_type,
        "question": question,
        "age_days": age_days,
        "agrees": agrees,
    }


def collect_sources(subquestions: list[str], round_num: int) -> list[dict]:
    """Simulated catalog. Later rounds add cards; they do not repeat round 1."""
    if round_num < 1:
        raise ValueError("round_num must be >= 1")
    if not subquestions:
        return []
    conflict_at = min(DEMO_CONFLICT_INDEX, len(subquestions) - 1)
    sources = []
    for index, question in enumerate(subquestions):
        sources.append(_source(question, "professional", 45, agrees=True))
        if index == conflict_at:
            sources.append(_source(question, "community", 20, agrees=False))
        if round_num >= 2 and index < OFFICIAL_EARLY_LIMIT:
            sources.append(_source(question, "official", 12, agrees=True))
        if round_num >= 3 and index >= OFFICIAL_EARLY_LIMIT:
            sources.append(_source(question, "official", 8, agrees=True))
    return sources


def extract_cards(sources: list[dict]) -> list[dict]:
    """One card per source. Every gathered source is kept."""
    cards = []
    for index, source in enumerate(sources, 1):
        cards.append(
            {
                "id": f"C-{index:03d}",
                "topic": source["question"],
                "content": (
                    f"模拟卡片（{source['type']}，"
                    f"{'一致' if source['agrees'] else '冲突'}）。不是检索结果。"
                ),
                "source": source["title"],
                "source_type": source["type"],
                "source_count": 1,
                "age_days": source["age_days"],
                "contradicted": False,
                "agrees": source["agrees"],
            }
        )
    return cards


def _best_type(types: list[str]) -> str:
    return max(types, key=lambda name: SOURCE_TRUST.get(name, SOURCE_TRUST["unknown"]))


def _score_type(types: list[str], contradicted: bool) -> str:
    """Open conflicts score the weakest source, not the official card."""

    def rank(name: str) -> int:
        return SOURCE_TRUST.get(name, SOURCE_TRUST["unknown"])

    return min(types, key=rank) if contradicted else _best_type(types)


def _best_trust(cards: list[dict]) -> int:
    return max(
        SOURCE_TRUST.get(card.get("source_type", "unknown"), SOURCE_TRUST["unknown"])
        for card in cards
    )


def _side_closes(side: list[dict], other: list[dict]) -> bool:
    """A side closes a disagreement only with corroboration and higher trust.

    One official card is not enough. Two weaker cards do not outvote a
    single higher-trust card either.
    """
    if len(side) < 2 or not other:
        return False
    return _best_trust(side) > _best_trust(other)


def _disagreement_closed(group: list[dict]) -> bool:
    sides: dict[bool, list[dict]] = {True: [], False: []}
    for card in group:
        sides[bool(card.get("agrees", True))].append(card)
    return _side_closes(sides[True], sides[False]) or _side_closes(sides[False], sides[True])


def _describe(types: list[str], disagreement: bool, conflict: bool) -> str:
    listed = ", ".join(types)
    if conflict:
        return (
            f"模拟卡片互相冲突（{listed}）。"
            "一张官方卡片不会单独收束。这些不是检索结果。"
        )
    if disagreement:
        return (
            f"模拟卡片仍有分歧，已按「至少两张卡片且更高可信类型」暂定收束（{listed}）。"
            "这不是官方即正确。这些不是检索结果。"
        )
    return f"模拟卡片一致（{listed}）。这些不是检索结果。"


def validate_cards(cards: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge cards by sub-question and flag unresolved disagreements."""
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for card in cards:
        topic = card["topic"]
        if topic not in groups:
            order.append(topic)
            groups[topic] = []
        groups[topic].append(card)

    facts = []
    contradictions = []
    for index, topic in enumerate(order, 1):
        group = groups[topic]
        agrees = {bool(card.get("agrees", True)) for card in group}
        types = [card["source_type"] for card in group]
        disagreement = len(agrees) > 1
        closed = _disagreement_closed(group) if disagreement else False
        conflict = disagreement and not closed
        fact = {
            "id": f"F-{index:03d}",
            "topic": topic,
            "content": _describe(types, disagreement, conflict),
            "source": ", ".join(card["source"] for card in group),
            "source_type": _score_type(types, conflict),
            "source_count": len(group),
            "age_days": min(card["age_days"] for card in group),
            "contradicted": conflict,
            "disagreement": disagreement,
        }
        facts.append(fact)
        if disagreement:
            contradictions.append(
                {
                    "fact_id": fact["id"],
                    "topic": topic,
                    "severity": "medium",
                    "resolved": not conflict,
                    "rule": "corroborated_side" if not conflict else "open",
                    "source_types": types,
                    "desc": (
                        "同一子问题下的模拟卡片结论不一致，"
                        "没有任何一侧同时满足「至少两张卡片」和「更高来源类型可信度」。"
                        if conflict
                        else "分歧还在表里。暂定收束是因为同一侧至少有两张卡片，"
                        "且该侧最高来源类型可信度严格高于另一侧。"
                        "一张官方卡片不能单独收束矛盾。"
                    ),
                }
            )
    return facts, contradictions


def analyze(facts: list[dict]) -> tuple[list[str], list[str]]:
    """Insights and coverage gaps derived from the fact cards."""
    insights = []
    gaps = []
    for fact in facts:
        if fact["contradicted"]:
            state = "存在未解决矛盾"
        elif fact.get("disagreement"):
            state = f"分歧已暂定收束，计 {fact['source_count']} 个来源"
        else:
            state = f"由 {fact['source_count']} 个来源支持"
        insights.append(f"{fact['topic']}：{state}（计分来源类型 {fact['source_type']}）")
        if fact["source_count"] < 2:
            gaps.append(fact["topic"])
    return insights, gaps


def _cell(value: object) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def _score_lines(score: float, components: dict) -> list[str]:
    order = tuple(SCORE_WEIGHTS)
    lines = [
        f"参考分: {score:.1f}/100（决定要不要再搜一轮，不是结论，也不是校准过的概率）",
    ]
    for key in order:
        lines.append(
            f"{COMPONENT_LABELS[key]}: {components[key]:.1f}"
            f"（权重 {SCORE_WEIGHTS[key]:.0%}）"
        )
    formula = " + ".join(
        f"{components[key]:.1f}×{SCORE_WEIGHTS[key]:g}" for key in order
    )
    lines.append(f"计算: {formula} = {score:.1f}")
    lines.append("小数只为把加权算式核对清楚，不是测量精度。")
    return lines


def render_report(topic: str, rounds: list[dict], stopped_reason: str, policy: str) -> str:
    final = rounds[-1]
    score = final["score"]
    components = final["components"]
    facts = final["facts"]
    gaps = final["gaps"]
    contradictions = final["contradictions"]
    if stopped_reason == "quality_gate" and policy == "durable":
        reason = "质量门通过（历史/原理类话题不因来源年龄续轮）"
    elif stopped_reason == "quality_gate":
        reason = "质量门通过（参考分、第二来源、矛盾和 90 天时效都满足）"
    else:
        reason = f"已达到最大轮次 {len(rounds)}，这是当前最好的一版"
    trail = " → ".join(
        f"第{item['round']}轮 {item['score']:.1f}" for item in rounds
    )
    covered = len(facts) - len(gaps)

    lines = [
        f"# {topic} 研究报告",
        "",
        "> 本地演示输出。来源卡片由脚本按轮次生成，没有联网检索，也没有调用模型。",
        "> Local demo. Source cards are simulated. No web search and no model call.",
        "",
        "## 执行摘要",
        "",
        f"这是一轮本地模拟，不是对「{topic}」的真实检索。完成 {len(rounds)} 轮流水线。停止原因：{reason}。",
        f"分数轨迹（只反映模拟卡片）：{trail}。",
        "",
        "## 关键发现",
        "",
    ]
    for insight in final["insights"]:
        lines.append(f"- {insight}")

    lines.extend(["", "## 矛盾与争议", ""])
    if not contradictions:
        lines.append("未发现矛盾。")
    else:
        lines.append("| 子问题 | 状态 | 来源类型 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for item in contradictions:
            status = "暂定收束" if item["resolved"] else "未解决"
            types = ", ".join(item["source_types"])
            lines.append(
                f"| {_cell(item['topic'])} | {status} | {_cell(types)} | {_cell(item['desc'])} |"
            )

    lines.extend(
        [
            "",
            "## 数据对比",
            "",
            "| 子问题 | 来源数 | 计分来源类型 | 卡龄（天） | 矛盾 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for fact in facts:
        if fact["contradicted"]:
            flag = "未解决"
        elif fact.get("disagreement"):
            flag = "暂定收束"
        else:
            flag = "无"
        lines.append(
            "| {topic} | {count} | {kind} | {age} | {flag} |".format(
                topic=_cell(fact["topic"]),
                count=fact["source_count"],
                kind=_cell(fact["source_type"]),
                age=fact["age_days"],
                flag=flag,
            )
        )

    lines.extend(["", "## 参考分（不是结论）", ""])
    lines.append("这个数字只描述模拟卡片够不够再搜一轮，不证明上面的发现为真。")
    lines.append("")
    lines.extend(f"- {line}" for line in _score_lines(score, components))
    if policy == "durable":
        checked = loop_score(components, policy)
        lines.append(
            f"- 循环检查分: {checked:.1f}/100（80 分门槛看这个数；参考分里的时效性不参与）"
        )
    lines.append(
        f"- 第二来源: {covered}/{len(facts)}"
        "（占比就是多源覆盖；缺口达到 2 个还会单独挡住质量门）"
    )
    lines.extend(["", "## 下一步建议", ""])
    if gaps:
        lines.append("- 以下维度还没有第二来源，真实调研时应补搜：")
        for gap in gaps:
            lines.append(f"  - {gap}")
    lines.append("- 本报告的参考分只描述模拟卡片，不描述该话题的真实资料质量，也不是结论。")
    lines.append(f"- IdeaScout reference demo v{__version__}")
    lines.append("")
    return "\n".join(lines)


def _emit(out, text: str = "") -> None:
    print(text, file=out)


def run_round(subquestions: list[str], round_num: int, out, policy: str) -> dict:
    if round_num == 1:
        _emit(out, "\n📋 Planner 角色: 分解研究问题...")
        _emit(out, "\n  📋 研究计划:")
        for index, question in enumerate(subquestions, 1):
            _emit(out, f"     {index}. {question}")
    else:
        _emit(out, "\n📋 Planner 角色: 继续沿用第 1 轮的研究计划，本轮只补充来源。")

    sources = collect_sources(subquestions, round_num)
    _emit(out, f"\n🔍 Scout 角色: 为 {len(subquestions)} 个子问题列出模拟来源（无网络请求）...")
    per_question: dict[str, int] = {question: 0 for question in subquestions}
    for source in sources:
        per_question[source["question"]] += 1
    for question in subquestions:
        _emit(out, f"     🔍 [{question}] → 找到 {per_question[question]} 个来源")
    _emit(out, f"\n  共找到 {len(sources)} 个来源")

    cards = extract_cards(sources)
    _emit(out, f"\n🧬 Extractor 角色: 从 {len(sources)} 个来源提取事实...")
    for card in cards:
        _emit(out, f"     🧬 事实卡片 {card['id']}: {card['source']} → ✓")

    facts, contradictions = validate_cards(cards)
    _emit(out, f"\n✅ Validator 角色: 交叉核对 {len(cards)} 张卡片，合并为 {len(facts)} 条事实...")
    if not contradictions:
        _emit(out, "     ✅ 未发现矛盾")
    for item in contradictions:
        mark = "✅ 暂定收束" if item["resolved"] else "⚠️  未解决"
        _emit(out, f"     {mark}: {item['fact_id']} {item['desc']}")

    insights, gaps = analyze(facts)
    _emit(out, "\n🔬 Analyst 角色: 整理观察...")
    _emit(out, f"     🔬 发现 {len(insights)} 个观察（由事实卡片得出）")
    if gaps:
        _emit(out, f"     ⚠️  {len(gaps)} 个维度还没有第二来源")
    else:
        _emit(out, "     ✅ 每个维度都有第二来源")

    _emit(out, "\n📊 Synthesizer 角色: 本轮材料已汇总（完整报告在轮次结束后输出）")

    score, components = score_confidence(facts)
    covered = len(facts) - len(gaps)
    _emit(out, "\n🎯 Quality Evaluator 角色: 参考分（不是结论）")
    for line in _score_lines(score, components):
        _emit(out, f"     {line}")
    if policy == "durable":
        checked = loop_score(components, policy)
        _emit(
            out,
            f"     循环检查分: {checked:.1f}/100（80 分门槛看这个数，不看时效性）",
        )
        stale_count = sum(1 for fact in facts if fact.get("age_days", 180) > STALE_DAYS)
        if stale_count:
            _emit(
                out,
                f"     超过 {STALE_DAYS} 天的事实 {stale_count} 条，已记入时效性，不因此续轮",
            )
    _emit(
        out,
        f"     第二来源: {covered}/{len(facts)}（占比即多源覆盖；缺口 ≥ {GAP_TRIGGER} 会单独挡住质量门）",
    )
    for gap in gaps:
        _emit(out, f"     还没有第二来源: {gap}")

    return {
        "round": round_num,
        "plan": list(subquestions),
        "source_count": len(sources),
        "card_count": len(cards),
        "fact_count": len(facts),
        "facts": facts,
        "contradictions": contradictions,
        "insights": insights,
        "gaps": gaps,
        "score": score,
        "components": components,
    }


def run_pipeline(topic: str, max_rounds: int = DEFAULT_MAX_ROUNDS, out=None) -> dict:
    """Run the demo loop. Prints the human log to ``out`` (stdout by default)."""
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    topic = topic.strip()
    subquestions = plan_subquestions(topic)
    policy = freshness_policy(topic)
    out = sys.stdout if out is None else out

    _emit(out, "=" * 60)
    _emit(out, "🎯 Orchestrator 角色: 收到研究请求")
    _emit(out, f"   话题: {topic}")
    _emit(out, "   模式: 本地演示（模拟来源，不访问网络，不调用模型）")
    _emit(
        out,
        f"   执行: 一个进程按 {len(ROLES)} 个角色串行演示，不是多个独立 Agent",
    )
    if policy == "durable":
        _emit(
            out,
            "   时效策略: 历史/原理类。来源年龄记入参考分，但不单独触发 Deep Dive，也不计入 80 分门槛",
        )
    else:
        _emit(
            out,
            f"   时效策略: 当前类。超过 {STALE_DAYS} 天的事实会让质量门保持关闭",
        )
    _emit(out, f"   最大轮次: {max_rounds}")
    _emit(out, "=" * 60)

    rounds = []
    stopped_reason = "max_rounds"
    for round_num in range(1, max_rounds + 1):
        if round_num > 1:
            _emit(out, "")
            _emit(out, "=" * 60)
            _emit(out, f"🔄 Deep Dive 第 {round_num} 轮")
            _emit(out, "=" * 60)
        snapshot = run_round(subquestions, round_num, out, policy)
        rounds.append(snapshot)
        failures = gate_failures(
            snapshot["score"],
            snapshot["facts"],
            snapshot["gaps"],
            snapshot["contradictions"],
            policy=policy,
            components=snapshot["components"],
        )
        if not failures:
            stopped_reason = "quality_gate"
            _emit(out, "")
            _emit(out, "=" * 60)
            _emit(
                out,
                f"✅ 演示结束。参考分 {snapshot['score']:.1f}/100，质量门通过。这个分数不是结论。",
            )
            _emit(out, "=" * 60)
            break
        detail = "；".join(failures)
        if round_num == max_rounds:
            _emit(out, "")
            _emit(
                out,
                f"⚠️  未通过质量门（{detail}），已达最大迭代次数 ({max_rounds} 轮)，输出当前结果",
            )
        else:
            _emit(out, "")
            _emit(
                out,
                f"🔄 未通过质量门（{detail}），进入 Deep Dive 第 {round_num + 1} 轮",
            )

    report = render_report(topic, rounds, stopped_reason, policy)
    _emit(out, "")
    _emit(out, report.rstrip("\n"))
    final = rounds[-1]
    return {
        "topic": topic,
        "version": __version__,
        "freshness_policy": policy,
        "rounds": rounds,
        "score": final["score"],
        "components": final["components"],
        "gaps": final["gaps"],
        "contradictions": final["contradictions"],
        "stopped_reason": stopped_reason,
        "report": report,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_agent.py",
        description=(
            "IdeaScout local demo of eight roles played in series by one process. "
            "Offline and deterministic: no network and no model calls. "
            "The printed score is a reference for the loop, not a conclusion."
        ),
        epilog='Example: python3 scripts/research_agent.py "小米 SU7 vs Model 3 对比分析"',
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help="Research topic. Required unless --interactive is set.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Read one topic from the prompt, or from stdin when stdin is not a TTY.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=f"Deep Dive cap (default: {DEFAULT_MAX_ROUNDS}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the pipeline result as JSON on stdout. The human log goes to stderr.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def resolve_topic(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    if args.topic and args.topic.strip():
        return args.topic.strip()
    if args.interactive:
        if sys.stdin.isatty():
            try:
                entered = input("研究话题 / Research topic: ")
            except EOFError:
                entered = ""
        else:
            entered = sys.stdin.readline()
        if entered.strip():
            return entered.strip()
    parser.error('missing topic. Pass one, or use --interactive.')
    return ""


def main(argv=None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_rounds < 1:
        parser.error("--max-rounds must be >= 1")
    topic = resolve_topic(args, parser)
    log = sys.stderr if args.json else sys.stdout
    try:
        result = run_pipeline(topic, max_rounds=args.max_rounds, out=log)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
