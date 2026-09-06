"""THSR tool: station resolution, TDX response parsing, answer formatting.

`_get` is the only I/O boundary; every test stubs it with fixture payloads
shaped like real TDX responses, so nothing here touches the network.
"""
import pytest

from family_line_bot.services.thsr import ThsrService


@pytest.fixture
def thsr():
    return ThsrService(client_id="id", client_secret="secret")


def stub_get(service, responses):
    """Route `_get` by URL fragment; unknown paths raise like a failed call."""
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        for fragment, payload in responses.items():
            if fragment in path:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected TDX call: {path}")

    service._get = fake_get
    return calls


def timetable(entries):
    return [
        {
            "DailyTrainInfo": {"TrainNo": no},
            "OriginStopTime": {"DepartureTime": dep},
            "DestinationStopTime": {"ArrivalTime": arr},
        }
        for no, dep, arr in entries
    ]


FARES = [{"Fares": [
    {"TicketType": 2, "FareClass": 1, "CabinClass": 1, "Price": 9999},
    {"TicketType": 1, "FareClass": 1, "CabinClass": 2, "Price": 2440},
    {"TicketType": 1, "FareClass": 1, "CabinClass": 1, "Price": 1490},
]}]


# --- station resolution ---------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("台北", "1000"),
        ("臺北", "1000"),      # 台/臺 are both written in practice
        ("高鐵台中站", "1040"),  # "高鐵"/"站" affixes are stripped
        ("  左營  ", "1070"),
    ],
)
def test_station_names_resolve_to_tdx_ids(name, expected):
    assert ThsrService._resolve(name) == expected


@pytest.mark.parametrize("name", ["", "香港", "基隆"])
def test_unknown_station_names_resolve_to_none(name):
    assert ThsrService._resolve(name) is None


# --- time normalisation ---------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [("9:5", "09:05"), ("18:30", "18:30"), (" 7:00 ", "07:00"), ("下午三點", "00:00"), ("", "00:00")],
)
def test_time_after_is_normalised_to_hh_mm(raw, expected):
    assert ThsrService._normalize_time(raw) == expected


# --- fare selection -------------------------------------------------------

def test_standard_fare_picks_one_way_adult_standard_cabin():
    assert ThsrService._standard_fare(FARES) == 1490


def test_standard_fare_is_none_when_no_matching_combination():
    assert ThsrService._standard_fare([{"Fares": [{"TicketType": 2, "FareClass": 1, "CabinClass": 1}]}]) is None


def test_standard_fare_tolerates_an_empty_response():
    assert ThsrService._standard_fare([]) is None


# --- seat markers ---------------------------------------------------------

def test_seat_markers_pick_the_status_for_the_requested_destination(thsr):
    stub_get(thsr, {"AvailableSeatStatusList": {"AvailableSeats": [
        {"TrainNo": "0801", "StopStations": [
            {"StationID": "1040", "StandardSeatStatus": "O"},
            {"StationID": "1070", "StandardSeatStatus": "X"},
        ]},
        {"TrainNo": "0803", "StopStations": [{"StationID": "1070", "StandardSeatStatus": "L"}]},
    ]}})
    assert thsr._seat_markers("1000", "1070") == {"0801": "❌標準廂售完", "0803": "⚠️位少"}


def test_seat_markers_omit_trains_with_an_unrecognised_status(thsr):
    stub_get(thsr, {"AvailableSeatStatusList": {"AvailableSeats": [
        {"TrainNo": "0801", "StopStations": [{"StationID": "1070", "StandardSeatStatus": "?"}]},
    ]}})
    assert thsr._seat_markers("1000", "1070") == {}


# --- search: validation ---------------------------------------------------

def test_search_reports_unknown_stations_without_calling_tdx(thsr):
    calls = stub_get(thsr, {})
    out = thsr.search("台北", "香港", "2026-09-10")
    assert "找不到高鐵站「香港」" in out
    assert "台北" in out and "左營" in out  # lists the valid station names
    assert calls == []


def test_search_rejects_identical_origin_and_destination(thsr):
    calls = stub_get(thsr, {})
    assert thsr.search("台北", "臺北", "2026-09-10") == "出發站與抵達站不能相同。"
    assert calls == []


# --- search: formatting ---------------------------------------------------

def test_search_formats_trains_fare_and_booking_link(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0801", "08:00", "09:36"), ("0803", "09:00", "10:30")]),
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": []},
    })
    out = thsr.search("台北", "左營", "2026-09-10")
    assert out.startswith("台北→左營 2026-09-10（00:00 起）")
    assert "0801 08:00→09:36" in out
    assert "標準車廂單程全票 NT$1490" in out
    assert "訂票：https://irs.thsrc.com.tw/IMINT/" in out


def test_search_filters_departures_before_time_after(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0801", "08:00", "09:36"), ("0899", "19:00", "20:30")]),
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": []},
    })
    out = thsr.search("台北", "左營", "2026-09-10", time_after="18:00")
    assert "0899" in out and "0801" not in out


def test_search_caps_the_number_of_trains_listed(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([(f"08{i:02d}", f"{6 + i:02d}:00", f"{8 + i:02d}:00") for i in range(12)]),
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": []},
    })
    out = thsr.search("台北", "左營", "2026-09-10")
    train_lines = [line for line in out.splitlines() if line.startswith("08")]
    assert len(train_lines) == 8


def test_search_orders_trains_by_departure_time(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0899", "19:00", "20:30"), ("0801", "08:00", "09:36")]),
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": []},
    })
    out = thsr.search("台北", "左營", "2026-09-10")
    assert out.index("0801") < out.index("0899")


def test_search_skips_incomplete_timetable_rows(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0801", "08:00", "09:36")]) + [{"DailyTrainInfo": {}}],
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": []},
    })
    assert "0801" in thsr.search("台北", "左營", "2026-09-10")


def test_search_appends_seat_markers_and_the_caveat_note(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0801", "08:00", "09:36")]),
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": [
            {"TrainNo": "0801", "StopStations": [{"StationID": "1070", "StandardSeatStatus": "O"}]},
        ]},
    })
    out = thsr.search("台北", "左營", "2026-09-10")
    assert "0801 08:00→09:36 ✅有位" in out
    assert "座位狀態為對號座即時資訊" in out


def test_no_seat_note_when_no_train_has_a_marker(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0801", "08:00", "09:36")]),
        "ODFare": FARES,
        "AvailableSeatStatusList": {"AvailableSeats": []},
    })
    assert "座位狀態為對號座即時資訊" not in thsr.search("台北", "左營", "2026-09-10")


def test_empty_timetable_produces_a_readable_message(thsr):
    stub_get(thsr, {"DailyTimetable": []})
    out = thsr.search("台北", "左營", "2026-09-10", time_after="23:30")
    assert out == "2026-09-10 23:30 之後查不到 台北→左營 的高鐵班次。"


# --- search: degradation --------------------------------------------------

def test_timetable_failure_returns_a_friendly_error_not_an_exception(thsr):
    stub_get(thsr, {"DailyTimetable": RuntimeError("503")})
    assert thsr.search("台北", "左營", "2026-09-10") == "查詢高鐵時刻表時發生錯誤，請稍後再試。"


def test_fare_and_seat_failures_still_yield_a_timetable_answer(thsr):
    stub_get(thsr, {
        "DailyTimetable": timetable([("0801", "08:00", "09:36")]),
        "ODFare": RuntimeError("429"),
        "AvailableSeatStatusList": RuntimeError("429"),
    })
    out = thsr.search("台北", "左營", "2026-09-10")
    assert "0801 08:00→09:36" in out
    assert "NT$" not in out


# --- token caching --------------------------------------------------------

def test_a_cached_token_is_reused_until_it_expires(thsr, monkeypatch):
    import family_line_bot.services.thsr as mod

    monkeypatch.setattr(mod.time, "time", lambda: 1000.0)
    assert thsr._valid_token() is False
    thsr._token, thsr._token_expiry = "tok", 2000.0
    assert thsr._valid_token() is True
    monkeypatch.setattr(mod.time, "time", lambda: 2001.0)
    assert thsr._valid_token() is False
