# tests/integration/test_calendar.py
"""Tests de integración del servicio Google Calendar."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_tenant_obj(visit_duration: int = 60):
    class _T:
        visit_duration_minutes = visit_duration
    return _T()


async def test_create_event_returns_event_id(test_lead, test_property):
    """create_calendar_event retorna event_id no vacío."""
    from app.calendar.service import create_calendar_event

    mock_service = MagicMock()
    mock_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "google-event-abc123"
    }

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"):
            event_id = await create_calendar_event(
                lead=test_lead,
                tenant=_make_tenant_obj(60),
                prop=test_property,
            )

    assert event_id == "google-event-abc123"


async def test_event_has_correct_timezone(test_lead, test_property):
    """Evento creado con timezone America/Caracas."""
    from app.calendar.service import create_calendar_event

    captured_body = {}

    def mock_insert(**kwargs):
        captured_body.update(kwargs.get("body", {}))
        mock_result = MagicMock()
        mock_result.execute.return_value = {"id": "ev-001"}
        return mock_result

    mock_service = MagicMock()
    mock_service.events.return_value.insert.side_effect = mock_insert

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"):
            await create_calendar_event(
                lead=test_lead,
                tenant=_make_tenant_obj(60),
                prop=test_property,
            )

    assert captured_body.get("start", {}).get("timeZone") == "America/Caracas"
    assert captured_body.get("end", {}).get("timeZone") == "America/Caracas"


async def test_event_start_not_equal_end(test_lead, test_property):
    """start != end — duración > 0."""
    from app.calendar.service import create_calendar_event

    captured_body = {}

    def mock_insert(**kwargs):
        captured_body.update(kwargs.get("body", {}))
        mock_result = MagicMock()
        mock_result.execute.return_value = {"id": "ev-002"}
        return mock_result

    mock_service = MagicMock()
    mock_service.events.return_value.insert.side_effect = mock_insert

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"):
            await create_calendar_event(
                lead=test_lead,
                tenant=_make_tenant_obj(visit_duration=90),
                prop=test_property,
            )

    start_dt = captured_body.get("start", {}).get("dateTime", "")
    end_dt   = captured_body.get("end",   {}).get("dateTime", "")
    assert start_dt != end_dt
    assert start_dt < end_dt


async def test_calendar_error_on_api_failure(test_lead, test_property):
    """CalendarError lanzado cuando Google API falla."""
    from app.calendar.service import CalendarError, create_calendar_event

    with patch("googleapiclient.discovery.build", side_effect=Exception("API error")):
        with patch("google.oauth2.service_account.Credentials.from_service_account_file"):
            with pytest.raises(CalendarError):
                await create_calendar_event(
                    lead=test_lead,
                    tenant=_make_tenant_obj(60),
                    prop=test_property,
                )
