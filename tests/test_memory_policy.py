import user_memory_store as ums


def test_query_does_not_become_project_preference(monkeypatch):
    writes = []
    monkeypatch.setattr(
        ums,
        "upsert_memory",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )
    ums.extract_memories_from_conversation(
        "u1", "诊断一下德会项目的设备", "已完成", "诊断"
    )
    assert writes == []


def test_explicit_long_term_preference_is_saved(monkeypatch):
    writes = []
    monkeypatch.setattr(
        ums,
        "upsert_memory",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )
    ums.extract_memories_from_conversation(
        "u1", "记住，以后默认用表格展示德会项目的结果", "好的", "偏好"
    )
    assert writes
