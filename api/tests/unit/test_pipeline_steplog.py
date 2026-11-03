"""Pipeline step logging: one fixed key=value format, ids, counts, page numbers and class names only.

Rule 6: nothing a page says, and no exception message, may reach a log record.
"""

from __future__ import annotations

import logging
import re
import uuid

import pytest

from app.core.ai.bedrock import ServiceBusy
from app.core.ai.ocr import OcrFailed
from app.features.pipeline import steplog

JOB, CASE = uuid.uuid4(), uuid.uuid4()
NHS = "943 476 5919"
NAME = "Jane Testpatient"


class FakeResponseError(Exception):
    def __init__(self, status: int, request_id: str) -> None:
        super().__init__(f"{NAME} {NHS}")
        self.status = status
        self.request_id = request_id


def run(step: str = "ocr", todo: int = 10, done: int = 2) -> steplog.StepRun:
    return steplog.StepRun(JOB, CASE, step, unit="pages", todo=todo, done=done, concurrency=30, attempt=1)


def lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == steplog.logger.name]


def fields(line: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=(\S*)", line))


def test_start_and_end_carry_the_same_ids_and_counts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    r = run()
    r.begin()
    r.page_ok(1, 40.0)
    r.page_ok(2, 90.0)
    r.end()
    start, end = (fields(line) for line in lines(caplog))
    assert start["event"] == "start" and end["event"] == "end"
    for f in (start, end):
        assert f["step"] == "ocr" and f["job"] == str(JOB) and f["case"] == str(CASE)
    assert (start["todo"], start["done"], start["concurrency"], start["unit"]) == ("10", "2", "30", "pages")
    assert (end["ok"], end["failed"], end["skipped"], end["outcome"]) == ("2", "0", "2", "ok")
    assert int(end["slowest_ms"]) == 90 and int(end["ms"]) >= 0


def test_a_failed_page_logs_its_number_class_status_and_request(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    r = run()
    r.begin()
    r.page_failed(3, OcrFailed("http_422"), "failed")
    r.page_failed(7, FakeResponseError(503, "7f3a9c1e-1234-4abc-9def-0123456789ab"), "retry")
    r.end()
    failed = [fields(line) for line in lines(caplog) if "event=page_failed" in line]
    assert [f["page"] for f in failed] == ["3", "7"]
    assert (failed[0]["error"], failed[0]["status"], failed[0]["outcome"], failed[0]["attempt"]) == (
        "OcrFailed",
        "422",
        "failed",
        "1",
    )
    assert (failed[1]["status"], failed[1]["outcome"], failed[1]["request"]) == (
        "503",
        "retry",
        "7f3a9c1e-1234-4abc-9def-0123456789ab",
    )
    assert failed[0]["request"] == "-"
    pages = [fields(line) for line in lines(caplog) if "event=failed_pages" in line]
    assert pages[0]["pages"] == "3,7"
    assert fields(lines(caplog)[-1])["failed"] == "2"
    assert all(f["job"] == str(JOB) for f in failed)


def test_the_failed_page_list_is_capped_at_fifty(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    r = run(todo=80)
    r.begin()
    for n in range(1, 71):
        r.page_failed(n, OcrFailed("http_500"), "failed")
    r.end()
    listed = next(fields(line) for line in lines(caplog) if "event=failed_pages" in line)
    assert listed["pages"].startswith("1,2,3,") and listed["pages"].endswith(",50+20more")
    assert len(listed["pages"].split(",")) == 50


def test_a_pause_is_logged_with_its_reason_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    r = run()
    r.begin()
    r.page_failed(4, ServiceBusy(), "paused")
    r.end(outcome="paused", reason="service_busy")
    end = fields(lines(caplog)[-1])
    assert (end["outcome"], end["reason"]) == ("paused", "service_busy")


def test_no_exception_message_or_page_text_reaches_a_record(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    r = run()
    r.begin()
    r.page_failed(3, OcrFailed(f"{NAME} {NHS}"), "failed")
    r.page_failed(5, FakeResponseError(500, f"{NAME} {NHS}"), "failed")  # an id that is not an id is dropped
    r.end(outcome="failed", error=OcrFailed(f"{NAME} {NHS}"))
    text = "\n".join(r.getMessage() + (str(r.exc_info[1]) if r.exc_info else "") + str(r.args) for r in caplog.records)
    for secret in (NAME, "Testpatient", "943", "5919"):
        assert secret not in text
    assert "OcrFailed" in text


def test_a_running_step_is_found_by_the_stages_that_need_it() -> None:
    assert steplog.current() is None
