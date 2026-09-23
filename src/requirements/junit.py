"""JUnit-XML ingestion (`little-requirements`' own never-built E6: "JUnit-
XML ingestion beyond pytest"). Parses a real `<testsuite>`/`<testsuites>`
report — the format pytest's own `--junit-xml` emits, and the same
format most other test runners (Jest, JUnit itself, go test -junitfile,
...) can produce — into this pillar's own `TestResult` rows, independent
of whether the run that produced it went through `run_tests` at all.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from requirements.models import TestVerdict


@dataclass
class ParsedResult:
    test_name: str
    verdict: TestVerdict
    detail: str


class JUnitParseError(Exception):
    pass


def parse_junit_xml(xml_text: str) -> list[ParsedResult]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise JUnitParseError(f"invalid JUnit XML: {exc}") from exc

    testcases = root.iter("testcase") if root.tag in ("testsuites", "testsuite") else []
    parsed: list[ParsedResult] = []
    for case in testcases:
        classname = case.get("classname", "")
        name = case.get("name", "")
        test_name = f"{classname}::{name}" if classname else name

        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")

        if error is not None:
            verdict = TestVerdict.ERROR
            detail = error.get("message", "") or (error.text or "").strip()
        elif failure is not None:
            verdict = TestVerdict.FAILED
            detail = failure.get("message", "") or (failure.text or "").strip()
        elif skipped is not None:
            verdict = TestVerdict.SKIPPED
            detail = skipped.get("message", "") or (skipped.text or "").strip()
        else:
            verdict = TestVerdict.PASSED
            detail = ""

        parsed.append(ParsedResult(test_name=test_name, verdict=verdict, detail=detail))

    return parsed
