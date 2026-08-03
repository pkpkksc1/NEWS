"""중국 법정 공휴일을 로컬 JSON 파일로 판정합니다."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

HOLIDAY_FILE = Path(__file__).with_name("china_holidays.json")


def _load_data() -> dict[str, Any]:
    try:
        value = json.loads(HOLIDAY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"주의: 중국 공휴일 파일을 읽지 못했습니다: {error}")
        return {}
    return value if isinstance(value, dict) else {}


def get_china_holiday_name(day: date) -> str:
    """해당 날짜가 중국 법정 공휴일이면 이름을, 아니면 빈 문자열을 반환합니다."""
    data = _load_data()
    holidays = data.get("holidays", {})
    if not isinstance(holidays, dict):
        return ""
    value = holidays.get(day.isoformat(), "")
    return str(value).strip() if value else ""
