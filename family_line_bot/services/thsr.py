import logging
import time

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://tdx.transportdata.tw/api/basic"
_TOKEN_URL = (
    "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
)
_BOOKING_URL = "https://irs.thsrc.com.tw/IMINT/"
_TIMEOUT = 10.0
_MAX_TRAINS = 8
# TDX tokens live ~1 day; refresh a minute early to avoid edge-of-expiry 401s.
_TOKEN_SKEW = 60

# THSR 站名 → StationID。台/臺兩種寫法都收；順序即由北到南。
_ID_ORDER = [
    "0990", "1000", "1010", "1020", "1030", "1035",
    "1040", "1043", "1047", "1050", "1060", "1070",
]
_CANONICAL = [
    "南港", "台北", "板橋", "桃園", "新竹", "苗栗",
    "台中", "彰化", "雲林", "嘉義", "台南", "左營",
]
_ID_TO_NAME = dict(zip(_ID_ORDER, _CANONICAL))
_STATIONS = {
    "南港": "0990",
    "台北": "1000", "臺北": "1000",
    "板橋": "1010",
    "桃園": "1020",
    "新竹": "1030",
    "苗栗": "1035",
    "台中": "1040", "臺中": "1040",
    "彰化": "1043",
    "雲林": "1047",
    "嘉義": "1050",
    "台南": "1060", "臺南": "1060",
    "左營": "1070",
}


class ThsrService:
    """Queries Taiwan High Speed Rail timetables/fares via the TDX open API.

    All public methods return human-readable Traditional Chinese strings and
    never raise — network/HTTP failures come back as a readable error message so
    the tool-use loop can relay them to the user.
    """

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # --- auth -------------------------------------------------------------
    def _fetch_token(self) -> str:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 86400) - _TOKEN_SKEW
        return self._token

    def _valid_token(self) -> bool:
        return bool(self._token) and time.time() < self._token_expiry

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        token = self._token if self._valid_token() else self._fetch_token()
        url = f"{_BASE}{path}"
        query = {**(params or {}), "$format": "JSON"}
        headers = {"authorization": f"Bearer {token}"}
        resp = httpx.get(url, params=query, headers=headers, timeout=_TIMEOUT)
        # Token may have been revoked/expired server-side: refresh once, retry.
        if resp.status_code == 401:
            headers = {"authorization": f"Bearer {self._fetch_token()}"}
            resp = httpx.get(url, params=query, headers=headers, timeout=_TIMEOUT)
        # TDX free tier rate-limits bursts; one search() is 3 quick calls.
        for backoff in (1.5, 3.0):
            if resp.status_code != 429:
                break
            time.sleep(backoff)
            resp = httpx.get(url, params=query, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # --- helpers ----------------------------------------------------------
    @staticmethod
    def _resolve(name: str) -> str | None:
        if not name:
            return None
        key = name.strip().replace("高鐵", "").replace("站", "")
        return _STATIONS.get(key)

    @staticmethod
    def _normalize_time(value: str) -> str:
        try:
            hour, minute = value.strip().split(":")
            return f"{int(hour):02d}:{int(minute):02d}"
        except Exception:
            return "00:00"

    def _seat_markers(self, origin_id: str, dest_id: str) -> dict[str, str]:
        """TrainNo -> seat marker for trains departing origin toward dest.

        AvailableSeatStatusList covers roughly the next day of departures and
        reports status per destination stop (O=有位, L=有限, X=售完). Trains
        outside its horizon simply won't be in the map — callers omit markers.
        """
        data = self._get(f"/v2/Rail/THSR/AvailableSeatStatusList/{origin_id}")
        label = {"O": "✅有位", "L": "⚠️位少", "X": "❌標準廂售完"}
        markers: dict[str, str] = {}
        for train in data.get("AvailableSeats", []):
            for stop in train.get("StopStations", []):
                if stop.get("StationID") == dest_id:
                    status = stop.get("StandardSeatStatus")
                    if status in label:
                        markers[train.get("TrainNo")] = label[status]
                    break
        return markers

    @staticmethod
    def _standard_fare(data: list | dict) -> int | None:
        """Adult, standard-cabin, one-way fare from an ODFare response.

        TDX enums (verified against the TDX 軌道運輸資料 guide):
        TicketType 1=單程, FareClass 1=全票(成人), CabinClass 1=標準車廂.
        """
        for od in data or []:
            for fare in od.get("Fares", []):
                if (
                    fare.get("TicketType") == 1
                    and fare.get("FareClass") == 1
                    and fare.get("CabinClass") == 1
                ):
                    return fare.get("Price")
        return None

    # --- public API -------------------------------------------------------
    def search(
        self,
        origin: str,
        destination: str,
        date: str,
        time_after: str = "00:00",
    ) -> str:
        """Search THSR departures on `date` (YYYY-MM-DD) at/after `time_after`.

        Returns a compact Traditional-Chinese summary of the next few trains,
        the standard one-way fare, and the booking URL.
        """
        origin_id = self._resolve(origin)
        dest_id = self._resolve(destination)
        if origin_id is None or dest_id is None:
            unknown = [
                n for n, i in ((origin, origin_id), (destination, dest_id)) if i is None
            ]
            return (
                f"找不到高鐵站「{'、'.join(unknown)}」。"
                f"有效站名：{'、'.join(_CANONICAL)}。"
            )
        if origin_id == dest_id:
            return "出發站與抵達站不能相同。"

        after = self._normalize_time(time_after)

        try:
            timetable = self._get(
                f"/v2/Rail/THSR/DailyTimetable/OD/{origin_id}/to/{dest_id}/{date}"
            )
        except Exception as e:
            logger.error("THSR timetable query failed: %s", e)
            return "查詢高鐵時刻表時發生錯誤，請稍後再試。"

        trains = []
        for item in timetable or []:
            dep = item.get("OriginStopTime", {}).get("DepartureTime")
            arr = item.get("DestinationStopTime", {}).get("ArrivalTime")
            train_no = item.get("DailyTrainInfo", {}).get("TrainNo")
            if not (dep and arr and train_no):
                continue
            if dep >= after:
                trains.append((dep, arr, train_no))
        trains.sort()
        trains = trains[:_MAX_TRAINS]

        o_name = _ID_TO_NAME[origin_id]
        d_name = _ID_TO_NAME[dest_id]
        if not trains:
            return f"{date} {after} 之後查不到 {o_name}→{d_name} 的高鐵班次。"

        fare_line = ""
        try:
            fares = self._get(f"/v2/Rail/THSR/ODFare/{origin_id}/to/{dest_id}")
            price = self._standard_fare(fares)
            if price is not None:
                fare_line = f"\n標準車廂單程全票 NT${price}"
        except Exception as e:
            # Fare is a nice-to-have; a timetable-only answer is still useful.
            logger.warning("THSR fare query failed: %s", e)

        markers: dict[str, str] = {}
        try:
            markers = self._seat_markers(origin_id, dest_id)
        except Exception as e:
            logger.warning("THSR seat status query failed: %s", e)

        header = f"{o_name}→{d_name} {date}（{after} 起）"
        lines = []
        marked_any = False
        for dep, arr, no in trains:
            marker = markers.get(no)
            marked_any = marked_any or marker is not None
            lines.append(f"{no} {dep}→{arr}" + (f" {marker}" if marker else ""))
        body = "\n".join(lines)
        note = "\n（座位狀態為對號座即時資訊，僅涵蓋近期班次）" if marked_any else ""
        return f"{header}\n{body}{fare_line}{note}\n\n訂票：{_BOOKING_URL}"
