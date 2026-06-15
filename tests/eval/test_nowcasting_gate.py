"""W24 线上实例回归：require_any 标题匹配 + 收紧词表 → 只留领域相关。

线上 W24 周报混进 4 篇"领域、技术全不搭"的论文（自动驾驶雷达/城市热岛/海浪/通用生成ODE）。
根因：require_any 匹配全文 + 含裸 radar，被摘要名提 + LiDAR-radar 放行。
修复：require_any 只匹配标题、去裸 radar。本测试用真实标题钉死。
"""

from __future__ import annotations

from pathlib import Path

from isbe.triage import load_contract, triage
from isbe.triage.models import Item

CONTRACT = Path(__file__).parent / "nowcasting" / "contract.yaml"

# 真实 W24 标题（gem + 4 篇 LLM 自己标"无关"的）
_GEM = "Temporal Context Conditioning for Seasonality-Aware Precipitation Nowcasting of High-Intensity Rainfall"  # noqa: E501
_JUNK = {
    "ode": "Learning to Solve Generative ODEs Beyond the Linear Span",
    "atn3d": "ATN3D: Density-Aware LiDAR-Radar Early 3D Object Detection Under Extreme Sparsity",  # noqa: E501
    "heat": "Urban Heat MiniCubes: An AI-Ready dataset for urban heat research",
    "wave": "Physics-Guided Spatiotemporal Learning for Coastal Wave Peak Period Estimation from Video",  # noqa: E501
}


def test_w24_keeps_only_domain_relevant():
    c = load_contract(CONTRACT)
    items = [Item(id="gem", source="s", headline=_GEM)]
    # ODE 摘要里"名提"precipitation nowcasting —— 标题门必须不被摘要误放行
    items.append(Item(id="ode", source="s", headline=_JUNK["ode"],
                      summary="A solver applicable to precipitation nowcasting among other tasks."))
    items += [Item(id=k, source="s", headline=t) for k, t in _JUNK.items() if k != "ode"]

    res = triage(items, c)
    assert res.kept_ids == {"gem"}
    assert {it.id for it, _ in res.dropped} == set(_JUNK)
