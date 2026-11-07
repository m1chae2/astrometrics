"""Purpose: Contract test of the stand-in PHD2 against real PHD2.

Description: The simulated observatory plays PHD2 with `mock_phd2.py`, which
replays a guide log as PHD2 events. The app reads PHD2 only through the event
stream and the guide log, so the stand-in is only worth anything while it
sends what real PHD2 sends. `fixtures/phd2_contract_events.jsonl` and
`fixtures/phd2_contract_guide_log.txt` are a recording of the user's own PHD2
build driven against the INDI simulators (made by
`build/tpm/record_phd2_contract.py`). These tests compare the stand-in with it:

- every event type the app consumes is in the recording and in the stand-in;
- for every event type both send, the field names and value types agree
  (extra fields in real PHD2 are reported as information);
- the app's own guide log parser reads the stand-in's log and the real log
  the same way;
- the events around a dither come in the same order;
- the app's own GuideStep parser accepts every GuideStep of both.

After a PHD2 upgrade, run `build/tpm/record_phd2_contract.py` again and then
this file. A failure means the stand-in no longer matches real PHD2 (or PHD2
changed what it sends).

Run with
``.venv/bin/pytest simulator/test/test_phd2_contract.py -q -s``.
"""

import json
import math
import re
import socket
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from simulator import guide_log_replay, mock_phd2
from wayfindinglib.drivers.phd2.guide_log_parser import parse_guide_log_file
from wayfindinglib.drivers.phd2.phd2_events import parse_guide_step_event

SIMULATION_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORDED_EVENTS = FIXTURES / "phd2_contract_events.jsonl"
RECORDED_GUIDE_LOG = FIXTURES / "phd2_contract_guide_log.txt"
#: The guide log the stand-in replays.
STANDIN_GUIDE_LOG = SIMULATION_DIR / "guide_log_sample.txt"

#: The event types the app consumes. `phd2_events.py` handles only
#: GuideStep, and the client reads no other type.
CONSUMED_EVENT_TYPES = {"GuideStep"}
#: The GuideStep fields the app reads. Timestamp, the distances and SNR are
#: always sent by real PHD2. The duration and direction fields are sent only
#: when the axis was pulsed, and the app defaults them to zero.
APP_REQUIRED_FIELDS = {
    "GuideStep": {
        "Timestamp",
        "RADistanceGuide",
        "RADistanceRaw",
        "DECDistanceGuide",
        "DECDistanceRaw",
        "SNR",
    }
}
APP_OPTIONAL_FIELDS = {"GuideStep": {"RADuration", "RADirection", "DECDuration", "DECDirection"}}
#: Event types the stand-in sends that the recording does not hold, because
#: the recording run never lost its star. Their shape is written from PHD2's
#: documentation. Re-record with a star loss to close this.
NOT_IN_RECORDING = {"StarLost"}
#: Stand-in fields that real PHD2 documents but that the recording cannot
#: show: PHD2 adds ErrorCode to a GuideStep only when it is not zero.
DOCUMENTED_BUT_NOT_RECORDED = {"GuideStep": {"ErrorCode"}}
#: The direction names PHD2 uses in GuideStep events.
DIRECTION_NAMES = {"East", "West", "North", "South"}
#: A frame row of a guide log: frame number, seconds, then the quoted mount.
FRAME_ROW = re.compile(r'^\d+,[\d.]+,"')
#: Section fields the real PHD2 log fills and the Ekos sample does not, with
#: the reason. The sample comes from a real Ekos log, which writes the hour
#: angle as "N/A". It is a difference in the data, not in the stand-in.
KNOWN_LOG_DIFFERENCES = {"hour_angle_hours"}


def json_type(value: object) -> str:
    """Name the JSON type of a decoded value.

    Parameters
    ----------
    value : `object`
        A value decoded from JSON.

    Returns
    -------
    name : `str`
        ``number`` (int and float count as one), ``string``, ``bool``,
        ``array``, ``object`` or ``null``.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def read_recording() -> tuple[dict, list[dict]]:
    """Read the recorded events.

    Returns
    -------
    header : `dict`
        The first line: PHD2 version and recording date.
    events : `list` [`dict`]
        Every recorded event, in order.
    """
    lines = [
        json.loads(line) for line in RECORDED_EVENTS.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return lines[0], lines[1:]


def collect_standin_events() -> list[dict]:
    """Run the stand-in server on a local port and read what it sends.

    The server replays the guide log sample with every event due at once
    (the "plan" mode with offset zero), so the test does not wait for the
    pace of the log. The events are read from the socket, so the test sees
    the greeting and the JSON lines exactly as a client does.

    Returns
    -------
    events : `list` [`dict`]
        Every event received, greeting first.
    """
    items = guide_log_replay.load_replay_items(STANDIN_GUIDE_LOG)
    scheduled = [(0.0, item.event) for item in items if item.event is not None]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    scratch = tempfile.TemporaryDirectory()
    start_file = Path(scratch.name) / "start"
    start_file.write_text("0.0")
    server = mock_phd2.MockPhd2Server(port, [], None, scheduled, start_file)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    events: list[dict] = []
    try:
        client = None
        for _ in range(50):
            try:
                client = socket.create_connection(("127.0.0.1", port), timeout=10)
                break
            except ConnectionRefusedError:
                time.sleep(0.1)
        assert client is not None, "the stand-in server did not start"
        buffer = b""
        deadline = time.time() + 20
        while len(events) < len(scheduled) + 2 and time.time() < deadline:
            chunk = client.recv(65536)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                events.append(json.loads(line))
        client.close()
    finally:
        server.stop()
        thread.join(timeout=5)
        scratch.cleanup()
    return events


@pytest.fixture(scope="module")
def recording() -> tuple[dict, list[dict]]:
    """Give the recorded header and events.

    Returns
    -------
    recording : `tuple`
        The result of `read_recording`.
    """
    return read_recording()


@pytest.fixture(scope="module")
def standin_events() -> list[dict]:
    """Give the events the stand-in sends.

    Returns
    -------
    events : `list` [`dict`]
        The result of `collect_standin_events`.
    """
    events = collect_standin_events()
    assert len(events) > 10, "the stand-in sent almost nothing"
    return events


def field_types(events: list[dict], event_type: str) -> dict[str, set[str]]:
    """Collect, for one event type, each field name and its JSON types.

    Parameters
    ----------
    events : `list` [`dict`]
        Events to look through.
    event_type : `str`
        The ``Event`` value.

    Returns
    -------
    fields : `dict` [`str`, `set` [`str`]]
        Field name to the set of JSON type names seen.
    """
    fields: dict[str, set[str]] = {}
    for event in events:
        if event["Event"] == event_type:
            for name, value in event.items():
                fields.setdefault(name, set()).add(json_type(value))
    return fields


def always_present(events: list[dict], event_type: str) -> set[str]:
    """Give the fields that every event of one type carries.

    Parameters
    ----------
    events : `list` [`dict`]
        Events to look through.
    event_type : `str`
        The ``Event`` value.

    Returns
    -------
    names : `set` [`str`]
        Field names present in every event of that type.
    """
    sets = [set(event) for event in events if event["Event"] == event_type]
    return set.intersection(*sets) if sets else set()


def test_recording_is_real_phd2(recording: tuple[dict, list[dict]]) -> None:
    """The recording is from a real PHD2 run, with a version in its header."""
    header, events = recording
    assert header["Recording"] == "PHD2 contract"
    assert header["PHD2"] not in ("", "unknown")
    version = next(event for event in events if event["Event"] == "Version")
    assert version["PHDVersion"], "no PHD2 version in the Version event"
    print(f"\nRecording: PHD2 {header['PHD2']}, {header['Recorded']}, {header['PHD2Source']}")


def test_consumed_event_types_are_in_both(
    recording: tuple[dict, list[dict]], standin_events: list[dict]
) -> None:
    """Each event type the app consumes is in both sources."""
    _, events = recording
    recorded_types = {event["Event"] for event in events}
    standin_types = {event["Event"] for event in standin_events}
    assert CONSUMED_EVENT_TYPES <= recorded_types, CONSUMED_EVENT_TYPES - recorded_types
    assert CONSUMED_EVENT_TYPES <= standin_types, CONSUMED_EVENT_TYPES - standin_types


def test_standin_event_types_are_recorded_or_known(
    recording: tuple[dict, list[dict]], standin_events: list[dict]
) -> None:
    """Each stand-in event type is recorded, or listed as not recorded."""
    _, events = recording
    recorded_types = {event["Event"] for event in events}
    unknown = {event["Event"] for event in standin_events} - recorded_types - NOT_IN_RECORDING
    assert not unknown, f"the stand-in sends event types real PHD2 did not: {sorted(unknown)}"


def test_field_names_and_types_match(recording: tuple[dict, list[dict]], standin_events: list[dict]) -> None:
    """The stand-in's fields exist in real PHD2 with the same types."""
    _, events = recording
    problems: list[str] = []
    for event_type in sorted({event["Event"] for event in standin_events} - NOT_IN_RECORDING):
        real = field_types(events, event_type)
        standin = field_types(standin_events, event_type)
        allowed_extra = DOCUMENTED_BUT_NOT_RECORDED.get(event_type, set())
        for name, types in standin.items():
            if name not in real:
                if name not in allowed_extra:
                    problems.append(f"{event_type}.{name}: the stand-in sends it, real PHD2 did not")
            elif not types <= real[name]:
                problems.append(
                    f"{event_type}.{name}: stand-in types {sorted(types)}, real {sorted(real[name])}"
                )
        required = always_present(events, event_type) & APP_REQUIRED_FIELDS.get(event_type, set())
        for name in sorted(required - always_present(standin_events, event_type)):
            problems.append(
                f"{event_type}.{name}: the app reads it and real PHD2 always sends it; the stand-in does not"
            )
    assert not problems, "\n".join(problems)


def test_app_required_fields_are_always_sent_by_real_phd2(recording: tuple[dict, list[dict]]) -> None:
    """Real PHD2 sends every field the app depends on, in every step."""
    _, events = recording
    missing = APP_REQUIRED_FIELDS["GuideStep"] - always_present(events, "GuideStep")
    assert not missing, f"real PHD2 did not always send {sorted(missing)}"


def test_pulse_fields_come_in_pairs_and_only_when_pulsed(
    recording: tuple[dict, list[dict]], standin_events: list[dict]
) -> None:
    """Duration and direction come together, only for a pulsed axis."""
    _, events = recording
    for label, source in (("real", events), ("stand-in", standin_events)):
        for event in (event for event in source if event["Event"] == "GuideStep"):
            for axis in ("RA", "DEC"):
                has_duration = f"{axis}Duration" in event
                has_direction = f"{axis}Direction" in event
                assert has_duration == has_direction, (
                    f"{label}: {axis} duration/direction not paired: {event}"
                )
                if has_duration:
                    assert event[f"{axis}Duration"] > 0, f"{label}: {axis}Duration is zero but sent: {event}"
                    assert event[f"{axis}Direction"] in DIRECTION_NAMES, f"{label}: {event}"


def test_report_extra_real_fields(recording: tuple[dict, list[dict]], standin_events: list[dict]) -> None:
    """Report the fields only real PHD2 sends (information only)."""
    _, events = recording
    for event_type in sorted(
        {event["Event"] for event in standin_events} & {event["Event"] for event in events}
    ):
        extra = set(field_types(events, event_type)) - set(field_types(standin_events, event_type))
        optional = APP_OPTIONAL_FIELDS.get(event_type, set())
        print(f"\nINFO {event_type}: real PHD2 also sends {sorted(extra - optional)}")
    never_recorded = sorted({event["Event"] for event in standin_events} & NOT_IN_RECORDING)
    print(f"\nINFO not in the recording (shape from PHD2 documentation): {never_recorded}")


def dither_window(events: list[dict]) -> list[str]:
    """Reduce the events around the first dither to a short sequence.

    Parameters
    ----------
    events : `list` [`dict`]
        Events in order.

    Returns
    -------
    sequence : `list` [`str`]
        From the first ``GuidingDithered`` event through the first
        ``GuideStep`` after ``SettleDone``. Only these types are kept
        (``Settling`` is dropped), and a run of ``GuideStep`` counts once as
        ``GuideStep+``.
    """
    start = next(index for index, event in enumerate(events) if event["Event"] == "GuidingDithered")
    kept = [
        event["Event"]
        for event in events[start:]
        if event["Event"] in ("GuidingDithered", "SettleBegin", "GuideStep", "SettleDone")
    ]
    sequence: list[str] = []
    seen_done = False
    for name in kept:
        label = "GuideStep+" if name == "GuideStep" else name
        if sequence and sequence[-1] == label:
            continue
        sequence.append(label)
        if seen_done and name == "GuideStep":
            break
        seen_done = seen_done or name == "SettleDone"
    return sequence


def test_dither_event_order_matches(recording: tuple[dict, list[dict]], standin_events: list[dict]) -> None:
    """Events around a dither come in the same order in both."""
    _, events = recording
    expected = ["GuidingDithered", "SettleBegin", "GuideStep+", "SettleDone", "GuideStep+"]
    assert dither_window(events) == expected, "the recording changed shape"
    assert dither_window(standin_events) == expected


def test_greeting_matches(recording: tuple[dict, list[dict]], standin_events: list[dict]) -> None:
    """The first two events are Version and AppState."""
    _, events = recording
    assert [event["Event"] for event in events[:2]] == ["Version", "AppState"]
    assert [event["Event"] for event in standin_events[:2]] == ["Version", "AppState"]
    assert set(events[0]) <= set(standin_events[0]) | {"PHDSubver"}, set(events[0]) - set(standin_events[0])
    assert standin_events[1]["State"] in {
        "Stopped",
        "Selected",
        "Calibrating",
        "Guiding",
        "LostLock",
        "Paused",
        "Looping",
    }


def test_app_parser_accepts_every_guide_step(
    recording: tuple[dict, list[dict]], standin_events: list[dict]
) -> None:
    """The app's GuideStep parser accepts every step of both sources."""
    _, events = recording
    for label, source in (("real", events), ("stand-in", standin_events)):
        steps = [event for event in source if event["Event"] == "GuideStep"]
        assert steps, label
        for event in steps:
            sample = parse_guide_step_event(event)
            assert sample is not None, f"{label}: {event}"
            for value in (sample.time, sample.dra, sample.ddec, sample.pulse_ra, sample.pulse_dec):
                assert math.isfinite(value), f"{label}: {event}"
            assert sample.snr is not None, f"{label}: {event}"
            assert (event.get("RADirection") in ("West", None)) or sample.pulse_ra >= 0
            assert (event.get("RADirection") != "West") or sample.pulse_ra < 0
            assert (event.get("DECDirection") != "South") or sample.pulse_dec < 0


def populated_fields(section: object) -> set[str]:
    """Name the attributes of a parsed guiding section that have a value.

    Parameters
    ----------
    section : `GuidingSection`
        A section from the app's guide log parser.

    Returns
    -------
    names : `set` [`str`]
        Attribute names that are not `None` (and not the sample list).
    """
    return {name for name, value in vars(section).items() if name != "samples" and value is not None}


def test_guide_log_columns_and_markers_match() -> None:
    """The frame table header and the section markers are the same."""
    real = RECORDED_GUIDE_LOG.read_text(encoding="utf-8").splitlines()
    standin = STANDIN_GUIDE_LOG.read_text(encoding="utf-8").splitlines()
    real_header = next(line for line in real if line.startswith("Frame,"))
    standin_header = next(line for line in standin if line.startswith("Frame,"))
    assert real_header == standin_header
    columns = len(real_header.split(","))
    for label, lines in (("real", real), ("stand-in", standin)):
        rows = [line for line in lines if FRAME_ROW.match(line)]
        assert rows, label
        for row in rows:
            assert len(row.split(",")) == columns, f"{label}: {row}"
        for marker in ("Guiding Begins at ", "Guiding Ends at "):
            assert any(line.startswith(marker) for line in lines), f"{label}: no '{marker}' line"
        assert any("SETTLING STATE CHANGE, Settling started" in line for line in lines), label
        assert any(line.startswith("INFO: DITHER by ") for line in lines), label


def test_app_guide_log_parser_reads_both_the_same_way(tmp_path: Path) -> None:
    """The app's parser reads the real log and the stand-in log alike."""
    items = guide_log_replay.load_replay_items(STANDIN_GUIDE_LOG)
    rendered = tmp_path / "guide_log_standin.txt"
    rendered.write_text(
        "\n".join(guide_log_replay.render_line(item, datetime.now()) for item in items) + "\n",
        encoding="utf-8",
    )
    real_parsed = parse_guide_log_file(str(RECORDED_GUIDE_LOG))
    standin_parsed = parse_guide_log_file(str(rendered))
    for label, parsed in (("real", real_parsed), ("stand-in", standin_parsed)):
        assert parsed.sections, label
        assert parsed.calibrations, label
        for section in parsed.sections:
            assert section.pixel_scale_arcsec_per_px, label
            assert section.samples, label
            assert section.frames_total >= len(section.samples), label
    real_sample = real_parsed.sections[0].samples[0]
    standin_sample = standin_parsed.sections[0].samples[0]
    assert set(real_sample) == set(standin_sample)
    for key in real_sample:
        assert json_type(real_sample[key]) == json_type(standin_sample[key]) or None in (
            real_sample[key],
            standin_sample[key],
        ), key
    real_fields = populated_fields(real_parsed.sections[0])
    standin_fields = populated_fields(standin_parsed.sections[0])
    only_real = sorted(real_fields - standin_fields - KNOWN_LOG_DIFFERENCES)
    only_standin = sorted(standin_fields - real_fields)
    print(f"\nINFO log section fields only real fills: {only_real}; only the stand-in fills: {only_standin}")
    assert not only_real, (
        f"the parser reads these from the real log but not from the stand-in log: {only_real}"
    )
    assert not only_standin, (
        f"the parser reads these from the stand-in log but not from the real log: {only_standin}"
    )
    calibration_real = populated_fields(real_parsed.calibrations[0])
    calibration_standin = populated_fields(standin_parsed.calibrations[0])
    print(
        f"INFO calibration fields real: {sorted(calibration_real)}; stand-in: {sorted(calibration_standin)}"
    )
