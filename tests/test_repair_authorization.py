from repair_authorization import issue_repair_grant, consume_repair_grant


def test_repair_grant_is_one_time_and_bound_to_session():
    grant = issue_repair_grant(
        user_id="u1",
        session_id="s1",
        ip="10.0.0.1",
        action="restart_process",
        target="infer",
        ttl_seconds=60,
    )
    ok, reason = consume_repair_grant(
        token=grant["repair_token"],
        user_id="u1",
        session_id="s1",
        ip="10.0.0.1",
        action="restart_process",
        target="infer",
    )
    assert ok is True
    assert reason == "ok"

    ok2, _ = consume_repair_grant(
        token=grant["repair_token"],
        user_id="u1",
        session_id="s1",
        ip="10.0.0.1",
        action="restart_process",
        target="infer",
    )
    assert ok2 is False


def test_repair_grant_rejects_cross_session():
    grant = issue_repair_grant(
        user_id="u1",
        session_id="s1",
        ip="10.0.0.1",
        action="restart_process",
        target="infer",
    )
    ok, _ = consume_repair_grant(
        token=grant["repair_token"],
        user_id="u1",
        session_id="s2",
        ip="10.0.0.1",
        action="restart_process",
        target="infer",
    )
    assert ok is False
