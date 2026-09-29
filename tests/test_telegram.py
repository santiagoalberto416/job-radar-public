from job_radar.telegram import MAX_LEN, build_digest, chats_from_updates, format_job


def make_job(i, **kw):
    base = {
        "id": i,
        "score": 90 - i % 20,
        "title": f"Senior Angular Developer #{i}",
        "company": "Acme & Sons",
        "location": "Remote <LatAm>",
        "source": "greenhouse",
        "reason": "Stack Angular + TypeScript, remoto para LatAm y pago en USD.",
        "url": f"https://jobs.example.com/{i}?a=1&b=2",
    }
    return {**base, **kw}


def test_format_escapes_html():
    text = format_job(make_job(1))
    assert "Acme &amp; Sons" in text
    assert "Remote &lt;LatAm&gt;" in text
    assert 'href="https://jobs.example.com/1?a=1&amp;b=2"' in text
    assert text.startswith("<b>89</b>")


def test_single_message_when_small():
    messages = build_digest([make_job(1), make_job(2)])
    assert len(messages) == 1
    text, ids = messages[0]
    assert ids == [1, 2]
    assert "2 ofertas nuevas" in text


def test_splits_under_limit_and_keeps_every_job_once():
    jobs = [make_job(i) for i in range(60)]
    messages = build_digest(jobs)
    assert len(messages) > 1
    assert all(len(text) < MAX_LEN for text, _ in messages)
    all_ids = [i for _, ids in messages for i in ids]
    assert all_ids == list(range(60))
    # jobs are never cut in half across messages
    for text, ids in messages:
        assert text.count("<a href=") == len(ids)


def test_huge_single_entry_is_trimmed():
    messages = build_digest([make_job(1, reason="x" * 10000)])
    assert len(messages) == 1 and len(messages[0][0]) < MAX_LEN


def test_empty_digest():
    assert build_digest([]) == []


def test_chats_from_updates():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 42, "type": "private", "first_name": "S"}, "text": "hola"}},
        {"update_id": 2, "message": {"chat": {"id": 42, "type": "private", "first_name": "S"}, "text": "otra"}},
        {"update_id": 3, "my_chat_member": {}},
    ]
    assert [c["id"] for c in chats_from_updates(updates)] == [42]
