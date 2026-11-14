"""Keep check annotations useful without copying captured test diagnostics."""

import subprocess
import sys
from pathlib import Path


def annotate(path: Path) -> str:
    script = Path(__file__).parents[1] / "scripts" / "ci" / "test-failures.py"
    return subprocess.check_output([sys.executable, str(script), str(path)], text=True)


def test_failed_test_ids_are_escaped_without_tracebacks(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    _ = report.write_text(
        '<testsuite><testcase classname="tests.test_example" name="case%&#10;::warning::bad">'
        '<failure type="AssertionError">private-request-body</failure>'
        "<system-out>private-captured-output</system-out></testcase>"
        '<testcase name="passing"/><testcase name="skipped"><skipped/></testcase>'
        '<testcase name="setup"><error type="RuntimeError">private-error</error></testcase>'
        "</testsuite>"
    )
    output = annotate(report)
    assert output.splitlines() == [
        "::error title=Failed test::tests.test_example::case%25%0A::warning::bad (AssertionError)",
        "::error title=Failed test::setup (RuntimeError)",
    ]
    assert "private" not in output


def test_annotation_count_is_bounded(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    _ = report.write_text(
        "<testsuite>" + '<testcase name="failed"><failure/></testcase>' * 30 + "</testsuite>"
    )
    lines = annotate(report).splitlines()
    assert len(lines) == 21
    assert lines[-1].startswith("::notice::")


def test_failure_coordinates_omit_the_assertion_values(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    _ = report.write_text(
        '<testsuite><testcase classname="tests.test_example" name="case"><failure>'
        "private-request-body\n"
        "tests/test_example.py:42: AssertionError\n"
        ".venv/lib/private.py:99: Error\n"
        "</failure></testcase></testsuite>"
    )
    assert annotate(report).strip() == (
        "::error title=Failed test::tests.test_example::case (Test failed) "
        "at tests/test_example.py:42"
    )


def test_missing_and_malformed_results_do_not_add_failures(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    assert annotate(report) == ""
    _ = report.write_text("<not-xml")
    assert annotate(report) == ""


def test_fullscreen_trace_only_accepts_fixed_tokens_for_presenter_test(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    name = "test_presenter_click_opens_synchronized_audience_without_display_access[editor]"
    classname = "tests.integration.test_presentation_displays"
    report.write_text(
        f'<testsuite><testcase classname="{classname}" name="{name}">'
        '<failure>FULLSCREEN_TRACE=r,q0,k,rs,c1,e,es,c0</failure></testcase>'
        f'<testcase classname="{classname}" name="{name}">'
        '<failure>FULLSCREEN_TRACE=r,private-data</failure></testcase>'
        '<testcase name="other"><failure>FULLSCREEN_TRACE=r,c1</failure></testcase>'
        '</testsuite>'
    )
    lines = annotate(report).splitlines()
    assert lines[0].endswith("fullscreen events: r,q0,k,rs,c1,e,es,c0")
    assert "fullscreen events" not in lines[1]
    assert "fullscreen events" not in lines[2]
    assert "private" not in "".join(lines)
