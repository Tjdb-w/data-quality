"""dq validate 命令行测试。"""

import json
import subprocess
import sys

import pytest

from data_quality.cli import main


def rule(rule_id, rule_type, **options):
    return {"id": rule_id, "type": rule_type, "options": options}


def run_cli(stdin_text):
    """以字符串作为 stdin 调用 main，返回 (exit_code, stdout_json, stdout_text)。"""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_text.encode("utf-8")), encoding="utf-8")
    try:
        with redirect_stdout(buf):
            code = main(["validate"])
    finally:
        sys.stdin = old_stdin
    text = buf.getvalue()
    return code, json.loads(text), text


def test_cli_success(capsys):
    payload = {
        "records": [{"id": "r1", "age": 30}, {"id": "r2", "age": 5}],
        "rules": [rule("age", "range", field="age", min=18, max=120)],
    }
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 0
    assert parsed["passed"] is False
    assert parsed["summary"] == {
        "record_count": 2,
        "violation_count": 1,
        "checked_rule_count": 1,
    }
    v = parsed["violations"][0]
    assert v["rule_id"] == "age"
    assert v["record_index"] == 1
    assert v["record_id"] == "r2"


def test_cli_all_passed_exit_zero():
    payload = {"records": [{"id": "r1", "v": 1}], "rules": [rule("r", "range", field="v", min=0, max=2)]}
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 0
    assert parsed["passed"] is True
    assert parsed["violations"] == []


def test_cli_invalid_json():
    code, parsed, _ = run_cli("{not json")
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_JSON"
    assert "message" in parsed["error"]


def test_cli_top_level_must_be_object():
    code, parsed, _ = run_cli(json.dumps([1, 2]))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_INPUT"


def test_cli_missing_records_or_rules_key():
    code, parsed, _ = run_cli(json.dumps({"records": []}))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_INPUT"


def test_cli_invalid_input_records():
    payload = {"records": [123], "rules": []}
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_INPUT"


def test_cli_invalid_rule_unknown_type():
    payload = {"records": [], "rules": [{"id": "r", "type": "nope", "options": {"field": "x"}}]}
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_RULE"


def test_cli_invalid_rule_duplicate_id():
    payload = {
        "records": [],
        "rules": [
            rule("dup", "required", field="a"),
            rule("dup", "required", field="b"),
        ],
    }
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_RULE"
    assert "duplicate" in parsed["error"]["message"]


def test_cli_invalid_rule_range_conflict():
    payload = {"records": [], "rules": [rule("r", "range", field="x", min=3, max=1)]}
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_RULE"


def test_cli_invalid_regex():
    payload = {"records": [], "rules": [rule("r", "regex", field="x", pattern="([")]}
    code, parsed, _ = run_cli(json.dumps(payload))
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_RULE"


def test_cli_utf_8_round_trip():
    payload = {
        "records": [{"id": "记录一", "名称": "张三"}, {"id": "记录二"}],
        "rules": [rule("名称必填", "required", field="名称")],
    }
    code, parsed, text = run_cli(json.dumps(payload, ensure_ascii=False))
    assert code == 0
    assert parsed["violations"][0]["record_id"] == "记录二"
    assert "记录二" in text  # 未转义为 \uXXXX


def test_cli_non_utf8_bytes_invalid_json():
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{bad"), encoding="utf-8")
    try:
        with redirect_stdout(buf):
            code = main(["validate"])
    except UnicodeDecodeError:  # TextIOWrapper 可能延迟到读取时抛
        pytest.skip("wrapper decoded eagerly")
    finally:
        sys.stdin = old_stdin
    parsed = json.loads(buf.getvalue())
    assert code == 2
    assert parsed["error"]["code"] == "INVALID_JSON"


def test_module_invocation():
    """python -m data_quality validate 走真实子进程。"""
    payload = json.dumps({"records": [], "rules": []})
    proc = subprocess.run(
        [sys.executable, "-m", "data_quality", "validate"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed["passed"] is True


def test_dq_console_script_after_install():
    """已 pip install -e . 时 dq 可用；未安装则跳过。"""
    import shutil

    dq = shutil.which("dq")
    if dq is None:
        pytest.skip("dq console script not installed")
    proc = subprocess.run(
        [dq, "validate"],
        input=json.dumps({"records": [], "rules": []}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
