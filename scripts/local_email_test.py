"""Local end-to-end email send test for ISBE notify.

Renders the REAL weekly (research) and daily (finance) Jinja templates with
faithful synthetic data, then sends both through the production
`send_digest_notification` path against a throwaway in-process SMTP server.
Captures the wire-level .eml plus the extracted plaintext + HTML parts so the
actual rendered report format can be inspected without a real mail account.

Run: uv run --with aiosmtpd python scripts/local_email_test.py
"""
from __future__ import annotations

import email
import email.policy
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aiosmtpd.controller import Controller
from jinja2 import Environment, FileSystemLoader

from isbe.topics._shared.digester_utils import PaperBlock, SotaClaim

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tmp" / "email_test"
OUT.mkdir(parents=True, exist_ok=True)

SRC = REPO / "src" / "isbe" / "topics"


def _env(template_dir: Path) -> Environment:
    return Environment(loader=FileSystemLoader(str(template_dir)), autoescape=False)


def render_weekly() -> str:
    tpl = _env(SRC / "_shared" / "templates").get_template("weekly.j2")
    papers = [
        SimpleNamespace(
            arxiv_id="2506.01234",
            title="DiffCast-XL: Latent Diffusion for 0–3h Convective Precipitation Nowcasting",
            authors=["L. Chen", "A. Smith", "DeepMind Weather"],
            primary_category="physics.ao-ph",
            submitted_at=datetime(2026, 6, 14),
            source_url="https://arxiv.org/abs/2506.01234",
            pdf_uri="minio://papers/nowcasting/2026-W24/2506.01234.pdf",
            abstract=(
                "We present DiffCast-XL, a latent diffusion model conditioned on "
                "multi-radar mosaics that produces calibrated 0–3 hour precipitation "
                "forecasts at 1km/5min resolution. On the SEVIR and MeteoNet benchmarks "
                "it improves CSI@8mm/h by 14% over DGMR and MetNet-3 while remaining "
                "real-time on a single A100. Code and weights are released."
            ),
        ),
        SimpleNamespace(
            arxiv_id="2506.05678",
            title="Probabilistic Nowcasting with Graph Neural Operators",
            authors=["Y. Wang", "K. Müller"],
            primary_category="cs.LG",
            submitted_at=datetime(2026, 6, 12),
            source_url="https://arxiv.org/abs/2506.05678",
            pdf_uri=None,
            abstract=(
                "A graph neural operator that ingests sparse rain-gauge and radar "
                "observations to emit ensemble nowcasts with sharp, well-calibrated "
                "uncertainty. We show reliability-diagram gains over pySTEPS baselines."
            ),
        ),
    ]
    repos = [
        SimpleNamespace(
            title="openclimatefix/skillful_nowcasting",
            github_url="https://github.com/openclimatefix/skillful_nowcasting",
            stars=1820,
            last_commit_at=datetime(2026, 6, 13),
        ),
        SimpleNamespace(
            title="NVIDIA/modulus",
            github_url="https://github.com/NVIDIA/modulus",
            stars=4120,
            last_commit_at=datetime(2026, 6, 11),
        ),
    ]
    comparison = SimpleNamespace(
        compare_label="方向汇总",
        prior_period="2026-W23",
        buckets=[
            SimpleNamespace(
                label="扩散模型 nowcasting", current_n=3, prior_n=1, delta_n=2,
                new=["DiffCast-XL"], carried=[], dropped=[],
            ),
            SimpleNamespace(
                label="图神经算子", current_n=1, prior_n=2, delta_n=-1,
                new=[], carried=["GNO-Rain"], dropped=["OldGraphNet"],
            ),
        ],
        active_repos=[
            SimpleNamespace(
                title="openclimatefix/skillful_nowcasting",
                github_url="https://github.com/openclimatefix/skillful_nowcasting",
                last_commit_at=datetime(2026, 6, 13),
            )
        ],
    )
    return tpl.render(
        topic_label="nowcasting",
        topic_id="nowcasting",
        period_label="2026-W24",
        tldr=(
            "本周 nowcasting 方向最值得读的是 DeepMind 的 DiffCast-XL（潜空间扩散，0–3h，"
            "CSI@8mm/h 较 DGMR/MetNet-3 +14%，已开源）。另有一篇图神经算子做集合预报、"
            "强调不确定度校准。OpenClimateFix 与 NVIDIA Modulus 本周均有活跃提交。"
        ),
        comparison=comparison,
        papers=papers,
        paper_blocks={
            "2506.01234": PaperBlock(
                arxiv_id="2506.01234",
                verdict=(
                    "开源 + 公开数据集 + 方法清晰，可复现性强，本期值得细读；"
                    "但「单卡实时」未给端到端延迟分布，待核。"
                ),
                plain=(
                    "用扩散模型做未来 0–3 小时的降水预报，比上一代方法更准，"
                    "且代码、权重都公开可复现。"
                ),
                provenance=(
                    "第一作者 L. Chen（DeepMind Weather 团队）；"
                    "本文自述延续 DGMR 的生成式临近预报路线。"
                ),
                method=(
                    "潜空间条件扩散模型，以多雷达拼图为条件；"
                    "建立在 DGMR（GAN 路线）与 latent diffusion 之上。"
                ),
                data="SEVIR（公开，雷达—卫星）+ MeteoNet（公开，法国气象局）；均可下载复现。",
                code="https://github.com/example/diffcast-xl",
                sota=(
                    SotaClaim("CSI@8mm/h", "SEVIR", "0.41", "0.47",
                              "CSI@8mm/h@SEVIR vs DGMR: 0.41→0.47", "DGMR", "+14.6%"),
                    SotaClaim("CSI", "MeteoNet", "0.38", "0.44",
                              "CSI@MeteoNet vs MetNet-3: 0.38→0.44", "MetNet-3", "+15.8%"),
                ),
                repro={"开源": "是", "权重": "是", "算力": "训练 8×A100", "代码完整度": "中"},
            ),
            "2506.05678": PaperBlock(
                arxiv_id="2506.05678",
                verdict=(
                    "方法稳健，但仅在 pySTEPS 上对照，缺与扩散类的横向比较；"
                    "数据自采，复现门槛高。"
                ),
                plain="用图神经网络做集合预报，强调预测的不确定度更可信。",
                provenance=(
                    "作者来自某高校气象 AI 组（摘要未注明实验室）；未提及延续的具体前作。"
                ),
                method="图神经算子，吸收稀疏雨量计 + 雷达观测；以 pySTEPS 光流外推为对照基线。",
                data="自采区域雷达 + 雨量计（私有，未公开）；评测对照 pySTEPS。",
                code="未提及",
                sota=(),
                repro={"开源": "未知", "代码完整度": "未知"},
            ),
        },
        glossary=[
            ("CSI", "临近预报常用的命中率指标，越高越准"),
            ("nowcasting", "0–3 小时的超短期天气预报，靠雷达外推而非数值模式"),
            ("DGMR", "DeepMind 2021 年的生成式降水预报模型，常作基线"),
            ("pySTEPS", "传统光流外推的开源降水预报库，常作对照基线"),
        ],
        repos=repos,
        repo_reviews={
            "openclimatefix/skillful_nowcasting": "合入 DiffCast 复现分支，值得 watch。",
        },
        analysis=(
            "对照你论点库中『扩散类方法将在 0–3h 临近预报全面超越 GAN（DGMR）路线』——"
            "本周 DiffCast-XL 的 +14% CSI 是该论点的强化证据。但其『单卡实时』声明需复核："
            "论文未给出 batch=1 的端到端延迟分布。"
        ),
        distillation=(
            "DRAFT[topics/nowcasting.theses.md]: 新增论点候选 —— "
            "『2026 起，开源 + 大厂署名 + SOTA 声明 三信号同时出现，是新论文值得读的最强先验』。"
        ),
        generated_at=datetime(2026, 6, 16, 8, 0, tzinfo=UTC).isoformat(),
        artifact_id="art_nowcasting_2026W24_001",
        trace_id="trace_abc123",
        memory_refs="topics/nowcasting.theses.md@r7, feedback/research_digest_style.md@r3",
    )


def render_daily() -> str:
    tpl = _env(SRC / "nvda" / "templates").get_template("daily.j2")
    news = [
        SimpleNamespace(
            id="n1",
            published_at=datetime(2026, 6, 15, 21, 30),
            source="Reuters",
            headline="Nvidia unveils next-gen Rubin platform, raises data-center guidance",
            url="https://example.com/reuters/rubin",
        ),
        SimpleNamespace(
            id="n2",
            published_at=datetime(2026, 6, 15, 14, 5),
            source="Nasdaq",
            headline="Analysts lift NVDA targets after sovereign-AI deal pipeline disclosed",
            url="https://example.com/nasdaq/targets",
        ),
    ]
    filings = [
        SimpleNamespace(
            accession_no="0001045810-26-000123",
            filed_at=datetime(2026, 6, 13),
            form_type="8-K",
            ticker="NVDA",
            body_url="https://www.sec.gov/example/8k",
        ),
    ]
    comparison = SimpleNamespace(
        compare_label="公司动态",
        prior_period="2026-06-13",
        buckets=[
            SimpleNamespace(
                label="新闻条目", current_n=2, prior_n=4, delta_n=-2,
                new=["Rubin platform"], carried=[], dropped=["老款 H200 降价"],
            ),
        ],
    )
    return tpl.render(
        period_label="2026-06-16",
        session_label="盘后",
        tldr=(
            "NVDA 收 $1,184.20（+2.31%），放量。催化剂为 Rubin 平台发布 + 上调数据中心指引；"
            "卖方普遍上修目标价。无重大 SEC 风险事件（一份 8-K 为常规披露）。"
        ),
        comparison=comparison,
        prices_by_symbol={
            "NVDA": SimpleNamespace(
                close=1184.20, chg_pct=2.31, volume=41_200_000, date="2026-06-16"
            ),
            "AMD": SimpleNamespace(
                close=212.40, chg_pct=1.10, volume=33_100_000, date="2026-06-16"
            ),
        },
        news=news,
        news_reviews={"n1": "硬催化：新平台 + 指引上修，是本轮上涨主因。"},
        filings=filings,
        filing_reviews={},
        analysis=(
            "对照论点库『数据中心需求由训练向推理 + 主权 AI 扩散』——本期主权 AI 订单管线披露"
            "是该论点的增量证据。提示：本报告不构成任何买卖建议，不计算盈亏，不喊单。"
        ),
        distillation="",
        generated_at=datetime(2026, 6, 16, 22, 35, tzinfo=UTC).isoformat(),
        artifact_id="art_nvda_20260616_pm",
        trace_id="trace_nvda_xyz",
        memory_refs="topics/nvda.theses.md@r4, feedback/finance_digest_style.md@r2",
    )


class _Capture:
    def __init__(self) -> None:
        self.messages: list[bytes] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        self.messages.append(envelope.content)
        return "250 OK captured"


def _save(label: str, raw: bytes) -> None:
    (OUT / f"{label}.eml").write_bytes(raw)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain":
            (OUT / f"{label}.txt").write_text(
                part.get_content(), encoding="utf-8"
            )
        elif ct == "text/html":
            (OUT / f"{label}.html").write_text(
                part.get_content(), encoding="utf-8"
            )


def main() -> None:
    # write the rendered artifacts (mirrors latest.md on disk)
    weekly_md = render_weekly()
    daily_md = render_daily()
    weekly_path = OUT / "nowcasting_2026-W24.md"
    daily_path = OUT / "nvda_2026-06-16.md"
    weekly_path.write_text(weekly_md, encoding="utf-8")
    daily_path.write_text(daily_md, encoding="utf-8")

    # local throwaway SMTP server
    handler = _Capture()
    controller = Controller(handler, hostname="127.0.0.1", port=8025)
    controller.start()

    os.environ.update(
        ISBE_SMTP_HOST="127.0.0.1",
        ISBE_SMTP_PORT="8025",
        ISBE_SMTP_FROM="isbe@localhost",
        ISBE_SMTP_TO="visitorindark@gmail.com",
        ISBE_SMTP_ALLOW_PLAINTEXT="1",  # local relay, no STARTTLS
    )
    # import AFTER env set is irrelevant (env read at call time), but keep clean
    from isbe.notify import is_configured, send_digest_notification

    assert is_configured(), "SMTP env not configured"

    ok_w = send_digest_notification(
        topic_label="nowcasting",
        period_label="2026-W24",
        artifact_path=weekly_path,
        excerpt="weekly excerpt",
    )
    ok_d = send_digest_notification(
        topic_label="nvda",
        period_label="2026-06-16",
        artifact_path=daily_path,
        excerpt="daily excerpt",
    )

    controller.stop()

    print(f"weekly send ok: {ok_w}")
    print(f"daily  send ok: {ok_d}")
    print(f"captured messages: {len(handler.messages)}")
    labels = ["weekly_nowcasting", "daily_nvda"]
    for label, raw in zip(labels, handler.messages, strict=False):
        _save(label, raw)
        msg = email.message_from_bytes(raw)
        parts = [p.get_content_type() for p in msg.walk() if not p.is_multipart()]
        print(f"  [{label}] subject={msg['Subject']!r} parts={parts} bytes={len(raw)}")
    print(f"\nArtifacts written to: {OUT}")


if __name__ == "__main__":
    main()
