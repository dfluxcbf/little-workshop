import pytest

from requirements.junit import JUnitParseError, parse_junit_xml
from requirements.models import TestVerdict

VALID_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4">
    <testcase classname="tests.test_a" name="test_pass" time="0.01" />
    <testcase classname="tests.test_a" name="test_fail" time="0.01">
      <failure message="assert 1 == 2">AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_a" name="test_skip" time="0.0">
      <skipped message="not implemented yet" />
    </testcase>
    <testcase classname="tests.test_a" name="test_error" time="0.0">
      <error message="boom">RuntimeError: boom</error>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_parse_junit_xml_maps_each_outcome_to_a_verdict():
    parsed = parse_junit_xml(VALID_XML)
    by_name = {p.test_name: p for p in parsed}

    assert by_name["tests.test_a::test_pass"].verdict == TestVerdict.PASSED
    assert by_name["tests.test_a::test_fail"].verdict == TestVerdict.FAILED
    assert by_name["tests.test_a::test_fail"].detail == "assert 1 == 2"
    assert by_name["tests.test_a::test_skip"].verdict == TestVerdict.SKIPPED
    assert by_name["tests.test_a::test_error"].verdict == TestVerdict.ERROR
    assert by_name["tests.test_a::test_error"].detail == "boom"


def test_parse_junit_xml_handles_a_bare_testsuite_root():
    xml = """<testsuite name="pytest" tests="1">
        <testcase classname="tests.test_a" name="test_pass" />
    </testsuite>"""
    parsed = parse_junit_xml(xml)
    assert [p.test_name for p in parsed] == ["tests.test_a::test_pass"]


def test_parse_junit_xml_handles_a_testcase_with_no_classname():
    xml = '<testsuite><testcase name="test_pass" /></testsuite>'
    parsed = parse_junit_xml(xml)
    assert parsed[0].test_name == "test_pass"


def test_parse_junit_xml_raises_on_invalid_xml():
    with pytest.raises(JUnitParseError):
        parse_junit_xml("not xml at all <<<")


def test_parse_junit_xml_returns_empty_list_for_an_empty_suite():
    assert parse_junit_xml('<testsuite tests="0"></testsuite>') == []


def test_parse_junit_xml_unrelated_root_tag_yields_no_testcases():
    assert parse_junit_xml("<somethingelse/>") == []
