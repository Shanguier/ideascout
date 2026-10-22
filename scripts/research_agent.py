#!/usr/bin/env python3
"""IdeaScout reference pipeline.

Offline, deterministic demo of the planner → scout → extractor →
validator → analyst → synthesizer loop and the confidence formula in
SKILL.md. Source cards are simulated. This script does not search the
web and does not call a model.

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

__version__ = "0.2.0"

SOURCE_TRUST = {
    "official": 100,      # 官方数据、一手资料
    "professional": 80,   # 专业媒体
    "community": 60,      # 用户实测、社区
    "blog": 40,           # 自媒体
    "unknown": 20,        # 匿名/未标注
}

# Documented formula. Terms are rounded to one decimal before weighting
# so a reader can recompute the printed score from the printed terms.
SCORE_WEIGHTS = {
    "source_trust": 0.3,
    "timeliness": 0.2,
    "cross_validation": 0.3,
    "consistency": 0.2,
}

CONFIDENCE_THRESHOLD = 80
GAP_TRIGGER = 2
STALE_DAYS = 90
DEFAULT_MAX_ROUNDS = 3

# Plant one disagreement on the third sub-question. Round 2 adds an
# official card for the first three sub-questions, which resolves it.
# Later sub-questions stay single-source until round 3, so the coverage
# gate (two or more missing second sources) is what keeps round 3 alive.
DEMO_CONFLICT_INDEX = 2
OFFICIAL_EARLY_LIMIT = 3

_COMPARISON = re.compile(r"(?i)\bvs\.?\b|\bversus\b|\bcompared\b|对比|相比|比较")
_CJK = re.compile(r"[\u4e00-\u9fff]")

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


def score_confidence(validated_facts: list[dict]) -> tuple[float, dict]:
    """Return (score, components) for the documented weighted formula.

    Cross-validation counts a fact only when two or more sources agree.
    An empty fact list scores 0. Unknown source types use the unknown trust.
    """
    empty = {
        "source_trust": 0.0,
        "timeliness": 0.0,
        "cross_validation": 0.0,
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
    cross_hits = sum(
        1
        for fact in validated_facts
        if fact.get("source_count", 1) >= 2 and not fact.get("contradicted", False)
    )
    consistent_hits = sum(
        1 for fact in validated_facts if not fact.get("contradicted", False)
    )

    components = {
        "source_trust": round(sum(trust_values) / count, 1),
        "timeliness": round(sum(fresh_values) / count, 1),
        "cross_validation": round(cross_hits / count * 100, 1),
        "consistency": round(consistent_hits / count * 100, 1),
    }
    score = round(
        components["source_trust"] * SCORE_WEIGHTS["source_trust"]
        + components["timeliness"] * SCORE_WEIGHTS["timeliness"]
        + components["cross_validation"] * SCORE_WEIGHTS["cross_validation"]
        + components["consistency"] * SCORE_WEIGHTS["consistency"],
        1,
    )
    return score, components


def gate_failures(
    score: float,
    facts: list[dict],
    gaps: list[str],
    contradictions: list[dict],
) -> list[str]:
    """Reasons the quality gate is still closed. Empty means stop."""
    reasons = []
    if score < CONFIDENCE_THRESHOLD:
        reasons.append(f"置信度 {score:.1f}/100 < {CONFIDENCE_THRESHOLD}")
    unresolved = [item for item in contradictions if not item.get("resolved", False)]
    if unresolved:
        reasons.append(f"未解决矛盾 {len(unresolved)} 个")
    if len(gaps) >= GAP_TRIGGER:
        reasons.append(f"缺少第二来源的维度 {len(gaps)} 个")
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


def _describe(types: list[str], disagreement: bool, conflict: bool) -> str:
    listed = ", ".join(types)
    if conflict:
        return f"模拟卡片互相冲突（{listed}）。这些不是检索结果。"
    if disagreement:
        return f"冲突已按官方类型卡片对齐（{listed}）。这些不是检索结果。"
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
        agrees = {card.get("agrees", True) for card in group}
        types = [card["source_type"] for card in group]
        disagreement = len(agrees) > 1
        has_official = any(card["source_type"] == "official" for card in group)
        conflict = disagreement and not has_official
        fact = {
            "id": f"F-{index:03d}",
            "topic": topic,
            "content": _describe(types, disagreement, conflict),
            "source": ", ".join(card["source"] for card in group),
            "source_type": _best_type(types),
            "source_count": len(group),
            "age_days": min(card["age_days"] for card in group),
            "contradicted": conflict,
        }
        facts.append(fact)
        if disagreement:
            contradictions.append(
                {
                    "fact_id": fact["id"],
                    "topic": topic,
                    "severity": "medium",
                    "resolved": not conflict,
                    "source_types": types,
                    "desc": (
                        "同一子问题下的模拟卡片结论不一致。"
                        if conflict
                        else "模拟卡片曾不一致，官方类型卡片已作为对齐依据。"
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
        else:
            state = f"由 {fact['source_count']} 个来源支持"
        insights.append(f"{fact['topic']}：{state}（最高可信类型 {fact['source_type']}）")
        if fact["source_count"] < 2:
            gaps.append(fact["topic"])
    return insights, gaps


def _cell(value: object) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def _score_lines(score: float, components: dict) -> list[str]:
    trust = components["source_trust"]
    timing = components["timeliness"]
    cross = components["cross_validation"]
    consistency = components["consistency"]
    return [
        f"整体置信度: {score:.1f}/100",
        f"来源可信度: {trust:.1f}（权重 30%）",
        f"时效性: {timing:.1f}（权重 20%）",
        f"交叉验证通过率: {cross:.1f}（权重 30%）",
        f"一致性: {consistency:.1f}（权重 20%）",
        (
            "计算: "
            f"{trust:.1f}×0.3 + {timing:.1f}×0.2 + {cross:.1f}×0.3 + "
            f"{consistency:.1f}×0.2 = {score:.1f}"
        ),
    ]


def render_report(topic: str, rounds: list[dict], stopped_reason: str) -> str:
    final = rounds[-1]
    score = final["score"]
    components = final["components"]
    facts = final["facts"]
    gaps = final["gaps"]
    contradictions = final["contradictions"]
    reason = (
        "质量门通过（置信度、覆盖、矛盾和时效都满足）"
        if stopped_reason == "quality_gate"
        else f"已达到最大轮次 {len(rounds)}，这是当前最好的一版"
    )
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
            status = "已对齐" if item["resolved"] else "未解决"
            types = ", ".join(item["source_types"])
            lines.append(
                f"| {_cell(item['topic'])} | {status} | {_cell(types)} | {_cell(item['desc'])} |"
            )

    lines.extend(
        [
            "",
            "## 数据对比",
            "",
            "| 子问题 | 来源数 | 最高可信类型 | 卡龄（天） | 矛盾 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for fact in facts:
        flag = "未解决" if fact["contradicted"] else "无"
        lines.append(
            "| {topic} | {count} | {kind} | {age} | {flag} |".format(
                topic=_cell(fact["topic"]),
                count=fact["source_count"],
                kind=_cell(fact["source_type"]),
                age=fact["age_days"],
                flag=flag,
            )
        )

    lines.extend(["", "## 研究置信度", ""])
    lines.extend(f"- {line}" for line in _score_lines(score, components))
    lines.append(
        f"- 维度覆盖: {covered}/{len(facts)} 有第二来源（不计入加权分）"
    )
    lines.extend(["", "## 下一步建议", ""])
    if gaps:
        lines.append("- 以下维度还没有第二来源，真实调研时应补搜：")
        for gap in gaps:
            lines.append(f"  - {gap}")
    lines.append("- 本报告的分数只描述模拟卡片，不描述该话题的真实资料质量。")
    lines.append(f"- IdeaScout reference demo v{__version__}")
    lines.append("")
    return "\n".join(lines)


def _emit(out, text: str = "") -> None:
    print(text, file=out)


def run_round(subquestions: list[str], round_num: int, out) -> dict:
    if round_num == 1:
        _emit(out, "\n📋 Planner Agent: 分解研究问题...")
        _emit(out, "\n  📋 研究计划:")
        for index, question in enumerate(subquestions, 1):
            _emit(out, f"     {index}. {question}")
    else:
        _emit(out, "\n📋 Planner Agent: 继续沿用第 1 轮的研究计划，本轮只补充来源。")

    sources = collect_sources(subquestions, round_num)
    _emit(out, f"\n🔍 Scout Agent: 并行搜索 {len(subquestions)} 个子问题（模拟来源，无网络请求）...")
    per_question: dict[str, int] = {question: 0 for question in subquestions}
    for source in sources:
        per_question[source["question"]] += 1
    for question in subquestions:
        _emit(out, f"     🔍 [{question}] → 找到 {per_question[question]} 个来源")
    _emit(out, f"\n  共找到 {len(sources)} 个来源")

    cards = extract_cards(sources)
    _emit(out, f"\n🧬 Extractor Agent: 从 {len(sources)} 个来源提取事实...")
    for card in cards:
        _emit(out, f"     🧬 事实卡片 {card['id']}: {card['source']} → ✓")

    facts, contradictions = validate_cards(cards)
    _emit(out, f"\n✅ Validator Agent: 交叉验证 {len(cards)} 张卡片，合并为 {len(facts)} 条事实...")
    if not contradictions:
        _emit(out, "     ✅ 未发现矛盾")
    for item in contradictions:
        mark = "✅ 已对齐" if item["resolved"] else "⚠️  未解决"
        _emit(out, f"     {mark}: {item['fact_id']} {item['desc']}")

    insights, gaps = analyze(facts)
    _emit(out, "\n🔬 Analyst Agent: 深度分析...")
    _emit(out, f"     🔬 发现 {len(insights)} 个观察（由事实卡片得出）")
    if gaps:
        _emit(out, f"     ⚠️  {len(gaps)} 个维度还没有第二来源")
    else:
        _emit(out, "     ✅ 每个维度都有第二来源")

    _emit(out, "\n📊 Synthesizer Agent: 本轮材料已汇总（完整报告在轮次结束后输出）")

    score, components = score_confidence(facts)
    covered = len(facts) - len(gaps)
    _emit(out, "\n🎯 Quality Evaluator: 质量评估")
    for line in _score_lines(score, components):
        _emit(out, f"     {line}")
    _emit(out, f"     维度覆盖: {covered}/{len(facts)} 有第二来源（不计入加权分）")
    for gap in gaps:
        _emit(out, f"     未交叉验证: {gap}")

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
    subquestions = plan_subquestions(topic)
    out = sys.stdout if out is None else out

    _emit(out, "=" * 60)
    _emit(out, "🎯 Orchestrator: 收到研究请求")
    _emit(out, f"   话题: {topic.strip()}")
    _emit(out, "   模式: 本地演示（模拟来源，不访问网络，不调用模型）")
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
        snapshot = run_round(subquestions, round_num, out)
        rounds.append(snapshot)
        failures = gate_failures(
            snapshot["score"],
            snapshot["facts"],
            snapshot["gaps"],
            snapshot["contradictions"],
        )
        if not failures:
            stopped_reason = "quality_gate"
            _emit(out, "")
            _emit(out, "=" * 60)
            _emit(
                out,
                f"✅ 研究完成! 模拟卡片置信度 {snapshot['score']:.1f}/100，质量门通过",
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

    report = render_report(topic.strip(), rounds, stopped_reason)
    _emit(out, "")
    _emit(out, report.rstrip("\n"))
    final = rounds[-1]
    return {
        "topic": topic.strip(),
        "version": __version__,
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
            "IdeaScout local demo of the multi-agent research loop. "
            "Offline and deterministic: no network and no model calls."
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
