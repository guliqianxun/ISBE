from isbe.workflows.hello_world import hello_world_flow


def test_hello_world_returns_expected_payload():
    """In-memory flow execution; no Prefect server needed."""
    result = hello_world_flow(name="ISBE")
    assert result["greeting"] == "hello, ISBE"
    assert "timestamp" in result
