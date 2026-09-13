import plistlib
from pathlib import Path

PLIST_PATH = Path("launchd/com.stockagent.intraday.plist")


def test_plist_is_valid_and_has_exactly_seven_checkpoints():
    with open(PLIST_PATH, "rb") as f:
        data = plistlib.load(f)

    assert data["Label"] == "com.stockagent.intraday"
    assert data["ProgramArguments"][1:] == ["-m", "src.run_intraday"]

    intervals = data["StartCalendarInterval"]
    assert len(intervals) == 7
    hhmm_pairs = sorted((entry["Hour"], entry["Minute"]) for entry in intervals)
    assert hhmm_pairs == [(9, 25), (10, 25), (11, 25), (12, 25), (13, 25), (14, 25), (15, 25)]

    for entry in intervals:
        assert "Weekday" not in entry  # launchd fires daily; the app itself gates on trading days
