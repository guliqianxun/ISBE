"""A plan's concepts → its queries, per search engine (`isbe.question.compose`)."""

from isbe.question import compose

CONCEPTS = [
    {"role": "subject", "terms": ["partial differential equations", "PDE"]},
    {"role": "method", "terms": ["physics-informed neural network", "PINN"]},
    {"role": "method", "terms": ["neural operator", "operator learning"]},
    {"role": "landmark", "terms": ["DeepONet", "Fourier neural operator", "DeepXDE", "PDEBench"]},
    {"role": "outcome", "terms": ["generalization"]},
]


def test_openalex_queries_are_or_groups_of_word_groups() -> None:
    queries = compose.compose(CONCEPTS, compose.OPENALEX)
    subject = "((partial differential equations) OR PDE)"
    assert [(q["query"], q["layer"], q["priority"], q.get("limit")) for q in queries] == [
        # Narrow: the subject, with each method, with the landmarks.
        (subject, "canonical", 1, None),
        (f"{subject} ((physics-informed neural network) OR PINN)", "mainstream", 1, None),
        (f"{subject} ((neural operator) OR (operator learning))", "mainstream", 1, None),
        (f"(DeepONet OR (Fourier neural operator) OR DeepXDE) {subject}", "method_centric", 1, None),
        (f"PDEBench {subject}", "method_centric", 1, None),
        # Broad, deeper: the landmarks on their own, the outcome.
        ("(DeepONet OR (Fourier neural operator) OR DeepXDE)", "method_centric", 2, 200),
        ("PDEBench", "method_centric", 2, 200),
        (f"{subject} generalization", "mainstream", 2, 200),
    ]
    assert [q["id"] for q in queries] == [f"q{i}" for i in range(1, 9)]


def test_with_several_subjects_the_broad_tier_takes_one_at_a_time() -> None:
    concepts = [
        {"role": "subject", "terms": ["precipitation nowcasting"]},
        {"role": "subject", "terms": ["radar echo extrapolation"]},
        {"role": "context", "terms": ["weather radar"]},
        {"role": "method", "terms": ["deep learning"]},
    ]
    assert [(q["query"], q["priority"]) for q in compose.compose(concepts)] == [
        ("precipitation nowcasting radar echo extrapolation", 1),
        ("precipitation nowcasting radar echo extrapolation deep learning", 1),
        ("precipitation nowcasting radar echo extrapolation weather radar", 1),
        ("precipitation nowcasting", 2),
        ("radar echo extrapolation", 2),
        ("precipitation nowcasting deep learning", 2),
        ("radar echo extrapolation deep learning", 2),
    ]


def test_an_engine_without_boolean_search_gets_plain_words() -> None:
    queries = compose.compose(CONCEPTS, compose.PLAIN)
    assert [q["query"] for q in queries] == [
        "partial differential equations",
        "partial differential equations physics-informed neural network",
        "partial differential equations neural operator",
        "DeepONet partial differential equations",
        "PDEBench partial differential equations",
        "DeepONet",
        "PDEBench",
        "partial differential equations generalization",
    ]


def test_quoted_phrases_can_render_as_exact_phrases() -> None:
    quoting = compose.Engine("quoting", boolean=True, phrase="quote")
    assert compose.render(["green space", "greenness"], quoting) == '("green space" OR greenness)'


def test_terms_lose_the_syntax_a_model_adds() -> None:
    assert compose.term('"green space" OR (urban parks)?') == "green space urban parks"
    assert compose.render(["NDVI", "ndvi", ""], compose.OPENALEX) == "NDVI"


def test_without_a_subject_there_are_no_queries() -> None:
    assert compose.compose([{"role": "method", "terms": ["cohort"]}]) == []


def test_queries_are_capped_and_fit_the_engine() -> None:
    many = [{"role": "subject", "terms": [f"subject term {i}" for i in range(6)]}]
    many += [{"role": "method", "terms": [f"method {n} term {i}" for i in range(6)]} for n in range(10)]
    queries = compose.compose(many, compose.OPENALEX, limit=8)
    assert len(queries) == 8
    assert all(len(q["query"]) <= compose.OPENALEX.max_query for q in queries)
    # The longest ones keep fewer alternatives rather than being dropped.
    assert queries[-1]["query"].startswith("((subject term 0)")


def test_the_arxiv_index_gets_explicit_and_and_quoted_symbols() -> None:
    queries = compose.compose(CONCEPTS, compose.ARXIV)
    assert queries[1]["query"] == (
        '((partial differential equations) OR PDE) AND (("physics-informed" neural network) OR PINN)'
    )
    assert queries[4]["query"] == "PDEBench AND ((partial differential equations) OR PDE)"
    assert compose.render(["U-Net"], compose.ARXIV) == '"U-Net"'
