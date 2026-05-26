#!/usr/bin/env python3
"""
IdeaScout — Research Agent Pipeline
Orchestrates multi-agent research workflow.

Usage:
    python3 research_agent.py "小米 SU7 vs Model 3 对比分析"
    python3 research_agent.py --interactive

Output:
    - Agent execution log (step by step)
    - Final research report with confidence score
"""

import sys
import json
import re
from datetime import datetime
from collections import defaultdict


# ============================================================
# Confidence Scoring System
# ============================================================

SOURCE_TRUST = {
    "official":   100,  # 官方数据、一手资料
    "professional": 80, # 专业媒体
    "community":    60, # 用户实测、社区
    "blog":         40, # 自媒体
    "unknown":      20, # 匿名/未标注
}

def score_confidence(sources, validated_facts):
    """
    Confidence = source_trust*30% + timeliness*20% + cross_validation*30% + consistency*20%
    """
    n = len(validated_facts)
    if n == 0:
        return 0

    # Source trust
    trust_scores = [SOURCE_TRUST.get(f.get("source_type", "unknown"), 20)
                    for f in validated_facts]
    trust = sum(trust_scores) / n

    # Timeliness (all fresh = 100, all old = 0)
    now = datetime.now()
    freshness = []
    for f in validated_facts:
        days = f.get("age_days", 180)
        freshness.append(max(0, 100 - days * 0.5))
    timeliness = sum(freshness) / n

    # Cross-validation (how many facts have 2+ sources?)
    cross = sum(1 for f in validated_facts if f.get("source_count", 1) >= 2)
    cross_rate = (cross / n) * 100

    # Consistency (non-contradicted facts)
    consistent = sum(1 for f in validated_facts if not f.get("contradicted", False))
    consistency = (consistent / n) * 100

    total = (trust * 0.3) + (timeliness * 0.2) + (cross_rate * 0.3) + (consistency * 0.2)
    return round(total, 1)


# ============================================================
# Agent Pipeline Simulation
# ============================================================

def orchestrator(topic):
    print(f"\n{'='*60}")
    print(f"🎯 Orchestrator: 收到研究请求")
    print(f"   话题: {topic}")
    print(f"{'='*60}")


def planner_agent(topic):
    print(f"\n📋 Planner Agent: 分解研究问题...")
    # Simulated sub-questions
    subs = [
        "价格与配置对比",
        "性能参数对比",
        "智能驾驶对比",
        "用户口碑对比",
        "售后服务对比",
    ]
    print(f"\n  📋 研究计划:")
    for i, s in enumerate(subs, 1):
        print(f"     {i}. {s}")
    return subs


def scout_agent(sub_questions):
    print(f"\n🔍 Scout Agent: 并行搜索 {len(sub_questions)} 个子问题...")
    # Simulated sources per question
    all_sources = []
    for q in sub_questions:
        sources = [
            {"title": f"{q}_来源A", "type": "professional", "snippet": f"关于{q}的数据..."},
            {"title": f"{q}_来源B", "type": "community", "snippet": f"用户实测{q}..."},
        ]
        all_sources.extend(sources)
        print(f"     🔍 [{q}] → 找到 2 个来源")
    print(f"\n  共找到 {len(all_sources)} 个来源")
    return all_sources


def extractor_agent(sources):
    print(f"\n🧬 Extractor Agent: 从 {len(sources)} 个来源提取事实...")
    facts = []
    for i, src in enumerate(sources[:6], 1):
        fact = {
            "id": f"F-{i:03d}",
            "topic": src["title"].split("_来源")[0],
            "content": f"提取自 {src['title']} 的关键数据",
            "source": src["title"],
            "source_type": src["type"],
            "source_count": 1,
            "age_days": 30,
            "contradicted": False,
        }
        facts.append(fact)
        print(f"     🧬 事实卡片 #{i:03d}: {src['title']} → ✓")
    return facts


def validator_agent(facts):
    print(f"\n✅ Validator Agent: 交叉验证 {len(facts)} 条事实...")
    contradictions = []
    for i in range(len(facts) - 1):
        # Simulate occasional contradictions
        if i == 2:
            facts[i]["contradicted"] = True
            contradictions.append({
                "fact_a": facts[i]["id"],
                "fact_b": facts[i + 1]["id"],
                "desc": f"价格数据不一致: A说 X, B说 Y",
                "severity": "medium",
            })
            print(f"     ⚠️  发现矛盾: {facts[i]['id']} vs {facts[i+1]['id']}")
    if not contradictions:
        print(f"     ✅ 未发现矛盾，全部一致")
    return facts, contradictions


def analyst_agent(facts, contradictions):
    print(f"\n🔬 Analyst Agent: 深度分析...")
    insights = [
        "价格维度: 选项 A 入门门槛更低",
        "智驾维度: 选项 B 技术更成熟",
        "趋势: 市场差距正在缩小",
    ]
    gaps = []
    if len(facts) < 8:
        gaps.append("缺少保值率数据")
        gaps.append("缺少充电网络对比数据")
    print(f"     🔬 发现 {len(insights)} 个洞察")
    if gaps:
        print(f"     ⚠️  发现 {len(gaps)} 个信息缺口")
    return insights, gaps


def synthesizer_agent(insights, contradictions):
    print(f"\n📊 Synthesizer Agent: 生成研究报告...")
    print(f"     📊 包含: 执行摘要 | 关键发现 | 矛盾分析 | 数据对比 | 建议")
    print(f"     📊 报告生成完成")


def quality_evaluator(facts, contradictions, gaps):
    score = score_confidence(facts, facts)
    # Adjust for gaps
    gap_penalty = len(gaps) * 5
    score = max(0, score - gap_penalty)

    print(f"\n🎯 Quality Evaluator: 质量评估")
    print(f"     📊 整体置信度: {score}/100")
    print(f"     ✅ 数据完整性: {80 + len(gaps) * 5}%")
    print(f"     ✅ 来源可信度: 80%")
    print(f"     {'✅' if not contradictions else '⚠️'} 矛盾处理: {'100%' if not contradictions else '部分未解决'}")
    print(f"     {'✅' if score >= 80 else '❌'} 时效性: {'95%' if score >= 80 else '70%'}")

    if gaps:
        print(f"     ⚠️  信息缺口: {', '.join(gaps)}")
    return score


# ============================================================
# Main Pipeline
# ============================================================

def run_pipeline(topic, max_rounds=3):
    orchestrator(topic)

    for round_num in range(1, max_rounds + 1):
        if round_num > 1:
            print(f"\n{'='*60}")
            print(f"🔄 Deep Dive 第 {round_num} 轮")
            print(f"{'='*60}")

        sub_questions = planner_agent(topic)
        sources = scout_agent(sub_questions)
        facts = extractor_agent(sources)
        facts, contradictions = validator_agent(facts)
        insights, gaps = analyst_agent(facts, contradictions)
        synthesizer_agent(insights, contradictions)
        score = quality_evaluator(facts, contradictions, gaps)

        if score >= 80:
            print(f"\n{'='*60}")
            print(f"✅ 研究完成! 置信度 {score}/100 (达标)")
            print(f"{'='*60}")
            break
        else:
            print(f"\n🔄 置信度 {score}/100 < 80，触发 Deep Dive 第 {round_num + 1} 轮")
    else:
        print(f"\n⚠️  已达最大迭代次数 ({max_rounds} 轮)，输出当前最佳版本")

    print(f"\n📄 最终报告已生成")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 research_agent.py \"小米 SU7 vs Model 3 对比分析\"")
        sys.exit(1)

    topic = sys.argv[1]
    run_pipeline(topic)


if __name__ == "__main__":
    main()
