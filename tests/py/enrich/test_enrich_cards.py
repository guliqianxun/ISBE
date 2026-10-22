"""Evidence cards: fact extraction, JSON recovery, caching, and the quote checks for numbers and URLs."""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from isbe.core import assets, db
from isbe.core.errors import describe
from isbe.pipeline.enrich import cards
from isbe.pipeline.enrich.cards import check, json_object
from isbe.pipeline.enrich.evidence import fold, found, squeeze, stated
from isbe.topics import Lang

TEXT = (
    "## Page 1\n\nDiffusion Precipitation\nGuowen Li 1 , Tsinghua University\n"
    "Code is available at https://github.com/\nexample/dpd .\n\n"
    "### Table 1: Probabilistic forecasting.\n| CRPS ↓ | ENS | 1.528 | 1.740 |\n"
    "| | GraphCast+ Ours | 1 . 477 | 1.569 |\n"
)


def kept(raw: dict[str, Any], text: str = TEXT) -> list[str]:
    return list(check(raw, text)["fields"])


def test_truncated_card_can_be_rebuilt_from_saved_fulltext_then_cached(
    serve: Any, conn: sqlite3.Connection, chat: Any, read_json: Any, local_llm: Any
) -> None:
    budgets: list[int] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        sent = read_json(req)
        budgets.append(sent["max_completion_tokens"])
        if sent["max_completion_tokens"] == 4000:
            serve.reply(req, 200, chat('{"source":', "length", 4000))
        else:
            serve.reply(req, 200, chat({"source": {
                "value": "Guowen Li, Tsinghua University",
                "quote": "Guowen Li 1 , Tsinghua University",
            }}, "stop", 8000))

    base = serve(handle) + "/v1"
    conn.execute("INSERT INTO items (id, kind, source, title, url, lang, published_at) VALUES ('arxiv:1', 'paper', 'essaies', 'Test', 'https://arxiv.org/abs/1', 'en', '2026-10-14')")
    conn.execute("INSERT INTO fulltext (item_id, via, body, figure, extracted_at) VALUES ('arxiv:1', 'metrail', ?, ?, '2026-10-16')",
                 (TEXT, '{"file":"figures/1.jpg"}'))
    for cap in ["4000", None]:
        llm = local_llm(base, **({"ISBE_LLM_MAX_TOKENS": cap} if cap else {}))
        try:
            db.job(conn, "enrich", None, lambda run, _: cards.refresh(conn, llm, run, Lang.EN, "arxiv:1"))
            error = None
        except Exception as caught:
            error = caught
        count = conn.execute("SELECT count(*) FROM cards").fetchone()[0]
        if cap:
            assert error is not None and "hit max_tokens (4000)" in describe(error)
            assert count == 0, "never cache a truncated card"
        else:
            assert error is None
            assert count == 1
            assert cards.refresh(conn, llm, "unused", Lang.EN, "arxiv:1") is None
        saved = tuple(conn.execute("SELECT body, figure FROM fulltext").fetchone())
        assert saved == (TEXT, '{"file":"figures/1.jpg"}')
    assert budgets == [4000, 8000]


def test_keeps_quoted_facts_and_rejects_the_rest() -> None:
    raw = {
        "source": {"value": "Guowen Li，清华大学", "quote": "Guowen Li 1 , Tsinghua University"},
        "method": {"value": "扩散生成", "quote": "Diffusion Precipitation … Guowen Li 1"},
        "code": {"value": "https://github.com/example/dpd", "quote": "Code is available at https://github.com/ example/dpd"},
        "results": [
            {"value": "CRPS@day1 vs ENS: 1.528→1.477", "quote": "| | GraphCast+ Ours | 1.477 | 1.569 |"},
            {"value": "CRPS@day2 vs ENS: 1.740→1.569", "quote": "| CRPS ↓ | ENS | 1.528 | 1.740 |"},
        ],
        "data": {"value": "ERA5", "quote": "ERA5 … this sentence is not in the paper"},
        "compute": {"value": "8×A100，训练 36.5 小时", "quote": "Code is available at https://github.com/example/dpd"},
        "limits": "not an object",
    }
    card = check(raw, TEXT)
    assert list(card["fields"]) == ["code", "method", "results", "source"]
    assert card["fields"]["results"]["value"] == (
        "CRPS@day1 vs ENS: 1.528→1.477; CRPS@day2 vs ENS: 1.740→1.569"
    )
    # a passage not in the text, an invented number, a malformed field
    assert card["rejected"] == ["data", "compute", "limits"]


def test_finds_the_json_in_a_fenced_reply() -> None:
    raw = json_object('```json\n{"data": {"value": "v", "quote": "q"}}\n```')
    assert raw is not None and "data" in raw
    assert json_object("no json here") is None
    assert json_object("} {") is None
    assert json_object("[{}]") == {}, "the outermost braces, as Rust sliced them"


def test_states_numbers_and_urls() -> None:
    assert stated("CSI@SEVIR: 0.41→0.47（见 https://github.com/a/b）. 2,288 站") == [
        "0.41", "0.47", "2,288", "https://github.com/a/b",
    ]


def test_card_rows_and_keys_keep_the_stored_format(
    serve: Any, conn: sqlite3.Connection, chat: Any, local_llm: Any, add_paper: Any
) -> None:
    reply = {"code": {"value": " https://github.com/example/dpd ",
                      "quote": "Code is available at https://github.com/ example/dpd"},
             "data": None}
    llm = local_llm(serve(lambda req: serve.reply(req, 200, chat("Sure:\n" + json.dumps(reply)))))
    add_paper("arxiv:1")
    conn.execute("INSERT INTO fulltext (item_id, via, body, extracted_at) VALUES ('arxiv:1', 'metrail', ?, 'x')", (TEXT,))
    counts: list[int | None] = []
    db.job(conn, "enrich", "t", lambda run, _: counts.append(cards.refresh(conn, llm, run, Lang.ZH, "arxiv:1")))
    assert counts == [1]
    row = conn.execute("SELECT lang, key, card FROM cards").fetchone()
    system = assets.text("prompts", "zh", "card.md")
    assert tuple(row) == ("zh", db.sha256(f"{system}\n{TEXT}"), (
        '{"fields":{"code":{"value":"https://github.com/example/dpd",'
        '"quote":"Code is available at https://github.com/ example/dpd"}},"rejected":["data"]}'
    ))


def test_a_reply_without_json_twice_fails_and_names_the_call(
    serve: Any, conn: sqlite3.Connection, chat: Any, read_json: Any, local_llm: Any, add_paper: Any
) -> None:
    def handle(req: BaseHTTPRequestHandler) -> None:
        read_json(req)
        serve.reply(req, 200, chat("no json"))

    llm = local_llm(serve(handle))
    add_paper("arxiv:1")
    conn.execute("INSERT INTO fulltext (item_id, via, body, extracted_at) VALUES ('arxiv:1', 'metrail', 'b', 'x')")
    with pytest.raises(Exception, match="the reply is not a JSON object twice; see llm_calls .*/cards/"):
        db.job(conn, "enrich", "t", lambda run, _: cards.refresh(conn, llm, run, Lang.EN, "arxiv:1"))
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
    assert cards.refresh(conn, llm, "run", Lang.EN, "missing") is None


# --- quote checks for numbers and URLs -----------------------------------------------

PAPER = (
    "## Page 1\n\nWe train a simple model on public observations.\n"
    "Seeds 1 2 3 were used for every run.\n\n## Page 2\n\n"
    "| Model | CSI |\n| Baseline | 10.53 |\n| Ours | 0.531 |\n"
    "The error changes by -0.3 on SEVIR.\n"
    "Code: https://github.com/foobar and data at https://zenodo.org/record/42.\n"
    "## Page 3\n\nTrained on 2,288 stations for 36.5 hours.\n"
    "See Section 3.1 for the 2023 release.\n"
)


def fact(value: str, quote: str) -> dict[str, Any]:
    return {"results": {"value": value, "quote": quote}}


def test_numbers_must_be_in_the_quote_not_anywhere_in_the_text() -> None:
    quote = "We train a simple model on public observations."
    # Page headings, seed lists and table rows used to vouch for any small number.
    for value in ["2 stages", "3 GPUs", "released 2023"]:
        assert kept(fact(value, quote), PAPER) == [], value
    assert kept(fact("2288 stations, 36.5 hours", "Trained on 2,288 stations for 36.5 hours."), PAPER) == ["results"]


def test_numbers_match_whole_with_their_decimals() -> None:
    rows = "| Baseline | 10.53 |\n| Ours | 0.531 |"
    assert kept(fact("CSI 0.53", rows), PAPER) == []
    # A passage may not start or end inside a number either.
    hay = squeeze(fold("CSI reaches 10.53 on SEVIR and 0.531 elsewhere, 1 . 477 in a PDF"))
    for cut in ["0.53 on SEVIR", "and 0.53", ".53 on SEVIR", "and 0.5", "477 in a PDF"]:
        assert not found(squeeze(fold(cut)), hay), cut
    for whole in ["10.53 on SEVIR", "and 0.531 elsewhere", "1.477 in a PDF"]:
        assert found(squeeze(fold(whole)), hay), whole
    assert kept(fact("CSI 0.531 vs 10.53", rows), PAPER) == ["results"]


def test_a_sign_is_part_of_the_number() -> None:
    change = "The error changes by -0.3 on SEVIR."
    assert kept(fact("error +0.3", change), PAPER) == []
    assert kept(fact("error -0.3", change), PAPER) == ["results"]
    assert kept(fact("error −0.3", change), PAPER) == ["results"], "a Unicode minus is a minus"
    assert kept(fact("error 0.3", change), PAPER) == ["results"]


def test_digits_kept_apart_never_merge() -> None:
    seeds = "Seeds 1 2 3 were used for every run."
    assert kept(fact("12 seeds", seeds), PAPER) == []
    assert kept(fact("12 seeds", "Seeds 12 3 were used for every run."), PAPER) == []
    assert kept(fact("seeds 1, 2, 3", seeds), PAPER) == ["results"]


def test_urls_must_be_whole_and_quoted() -> None:
    line = "Code: https://github.com/foobar and data at https://zenodo.org/record/42."
    assert kept({"code": {"value": "https://github.com/foo", "quote": line}}, PAPER) == []
    # Cutting the quote short does not help: the paper's URL is still foobar.
    assert kept({"code": {"value": "https://github.com/foo", "quote": "Code: https://github.com/foo"}}, PAPER) == []
    assert kept({"code": {"value": "https://github.com/foobar", "quote": line}}, PAPER) == ["code"]
    assert kept({"code": {"value": "github.com/foobar/", "quote": line}}, PAPER) == ["code"]
    assert kept({"data": {"value": "https://zenodo.org/record/42", "quote": line}}, PAPER) == ["data"]
    assert kept({"data": {"value": "https://zenodo.org/record/4", "quote": line}}, PAPER) == []
    # In the paper but not in the quote: the quote does not support it.
    other = {"code": {"value": "https://github.com/foobar",
                      "quote": "We train a simple model on public observations."}}
    assert kept(other, PAPER) == []


def test_short_or_invented_passages_reject_the_quote() -> None:
    sentence = "We train a simple model on public observations."
    assert kept(fact("accuracy +3.1%", f"+3.1%; {sentence}"), PAPER) == []
    assert kept(fact("accuracy +3.1%", sentence), PAPER) == []
    assert kept(fact("a model", "We train a huge model on private observations."), PAPER) == []
    assert kept(fact("a model", f"{sentence}\nModel"), PAPER) == []
    assert kept(fact("a model", f"{sentence}\n| |\n…"), PAPER) == ["results"], "no text, nothing to check"


def test_legitimate_cjk_full_width_and_wrapped_quotes_still_pass() -> None:
    paper = (
        "## Page 1\n\n在 SEVIR 数据集上，基线的 CSI 为 0.41，本文方法\n达到 ０．４７。\n"
        "We train a simple model on public\nobservations with ﬁne-grained “labels”.\n"
        "Ours reaches 98.2% accuracy on the held-out\nsplit.\n"
    )
    zh = {"results": {"value": "CSI@SEVIR vs 基线: 0.41→0.47",
                      "quote": "在 SEVIR 数据集上，基线的 CSI 为 0.41，本文方法达到 ０．４７。"}}
    assert kept(zh, paper) == ["results"]
    full_width_value = {"results": {"value": "准确率 ９８．２%",
                                    "quote": "Ours reaches 98.2% accuracy on the held-out split."}}
    assert kept(full_width_value, paper) == ["results"]
    # A line break of the model's own leaves a short tail that belongs to the passage.
    wrapped = {"method": {"value": "a simple model",
                          "quote": "Ours reaches 98.2% accuracy on the held-out\nsplit."}}
    assert kept(wrapped, paper) == ["method"]
    typographic = {"method": {"value": "fine-grained labels",
                              "quote": 'We train a simple model on public observations with fine-grained "labels".'}}
    assert kept(typographic, paper) == ["method"]
    names = {"results": {"value": "R@1 vs GPT-4 on ERA5 day1: 98.2", "quote": "Ours reaches 98.2% accuracy"}}
    assert kept(names, paper) == ["results"], "digits in names are not quantities"
