from grenton_native import protocol as p


def test_build_request_format():
    msg_id, raw = p.build_request("192.168.0.9", "checkAlive()", msg_id="00ABCDEF")
    assert msg_id == "00abcdef"
    assert raw == "req:192.168.0.9:00abcdef:checkAlive()\r\n"


def test_build_request_no_terminator():
    _, raw = p.build_request("10.0.0.1", "x", msg_id="0001", terminator="")
    assert raw == "req:10.0.0.1:0001:x"


def test_parse_response_simple():
    r = p.parse_response("resp:192.168.2.200:0000be11:nil")
    assert r is not None
    assert (r.clu_ip, r.msg_id, r.payload) == ("192.168.2.200", "0000be11", "nil")
    assert not r.is_notification
    assert not r.is_client_report


def test_parse_response_client_report_keeps_colons_in_payload():
    r = p.parse_response("resp:192.168.2.200:00000000:clientReport:210:{0,1,0,0,1,1,0,0}")
    assert r is not None
    assert r.is_notification
    assert r.is_client_report
    assert r.payload == "clientReport:210:{0,1,0,0,1,1,0,0}"


def test_parse_response_rejects_non_resp():
    assert p.parse_response("req:...:x") is None
    assert p.parse_response("resp:too:few") is None
    assert p.parse_response("") is None


def test_parse_client_report_numbers():
    assert p.parse_client_report("clientReport:210:{0,1,0,0,1,1,0,0}") == (
        210, [0, 1, 0, 0, 1, 1, 0, 0]
    )


def test_parse_client_report_mixed_types():
    session, values = p.parse_client_report(
        'clientReport:49064:{27,nil,1,true,"2023-12-05","tempus1.gum.gov.pl",24.09,false}'
    )
    assert session == 49064
    assert values == [27, None, 1, True, "2023-12-05", "tempus1.gum.gov.pl", 24.09, False]


def test_parse_client_report_string_with_comma():
    _, values = p.parse_client_report('clientReport:1:{"a,b",1}')
    assert values == ["a,b", 1]


def test_parse_client_report_quoted_keyword_stays_string():
    _, values = p.parse_client_report('clientReport:1:{"true","42"}')
    assert values == ["true", "42"]


def test_parse_client_report_empty():
    assert p.parse_client_report("clientReport:7:{}") == (7, [])


def test_parse_client_report_rejects_other_payloads():
    assert p.parse_client_report("nil") is None


def test_client_register_payload():
    payload = p.client_register_payload(
        "10.0.0.5", 4344, 49064, [("CLU221011038", 0), ("CLU221011038", 1)]
    )
    assert payload == (
        'SYSTEM:clientRegister("10.0.0.5",4344,49064,'
        "{{CLU221011038,0},{CLU221011038,1}})"
    )


def test_client_destroy_payload():
    assert p.client_destroy_payload("10.0.0.5", 4344, 49064) == (
        'SYSTEM:clientDestroy("10.0.0.5",4344,49064)'
    )
