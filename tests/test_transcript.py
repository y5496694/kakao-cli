from kakaocli_win.transcript import loaded_messages, parse_transcript


def test_date_context_multiline_and_unknown_text_are_preserved():
    records = parse_transcript(
        "지원되지 않는 머리말\r\n"
        "--------------- 2026년 10월 29일 목요일 ---------------\r\n"
        "[친구] [오전 9:01] 안녕\r\n둘째 줄\r\n"
        "[나] [오후 1:02] 답장\r\n"
        "--------------- 2026년 10월 30일 금요일 ---------------\r\n"
        "[친구] [13:20] 다음 날"
    )
    assert len(records) == 4
    assert records[0].kind == "unparsed"
    assert records[0].date is None
    assert records[1].text == "안녕\n둘째 줄"
    assert records[1].date == "2026년 10월 29일"
    assert records[1].time == "오전 9:01"
    assert records[2].sender == "나"
    assert records[3].date == "2026년 10월 30일"


def test_no_date_or_sender_is_invented():
    records = parse_transcript("알 수 없는 복사 형식")
    assert records[0].kind == "unparsed"
    assert records[0].date is None
    assert records[0].time is None
    assert records[0].sender is None
    assert records[0].raw == "알 수 없는 복사 형식"


def test_filter_is_case_insensitive_and_keeps_recent_matches_in_order():
    result = loaded_messages(
        "[A] [AM 9:01] LUNCH one\n[B] [AM 9:02] 다른 내용\n"
        "[C] [AM 9:03] lunch two\n[D] [AM 9:04] Lunch three",
        contains="lunch", limit=2,
    )
    assert result["loaded_records"] == 4
    assert result["matched_records"] == 3
    assert [record["sender"] for record in result["records"]] == ["C", "D"]
    assert result["complete_history"] is False


def test_empty_transcript_returns_no_records():
    result = loaded_messages("")
    assert result["records"] == []
    assert result["loaded_records"] == 0
