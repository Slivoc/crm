from datetime import datetime

from routes.dashboard import _monthly_target_pace, _normalise_tv_briefing_fields


def test_monthly_target_pace_uses_calendar_days():
    assert _monthly_target_pace(datetime(2026, 8, 2, 12, 0)) == 6.5
    assert _monthly_target_pace(datetime(2024, 2, 29, 12, 0)) == 100.0


def test_tv_briefing_recovers_sections_embedded_in_summary():
    briefing = {
        'summary': (
            '• First fact\n• Second fact\n'
            '"commercial_angle":"• Commercial point one\n• Commercial point two",\n'
            '"suggested_action":"• Call customer\n• Review stock"'
        ),
        'commercial_angle': '',
        'suggested_action': '',
    }

    assert _normalise_tv_briefing_fields(briefing) == {
        'summary': '• First fact\n• Second fact',
        'commercial_angle': '• Commercial point one\n• Commercial point two',
        'suggested_action': '• Call customer\n• Review stock',
    }


def test_tv_briefing_preserves_correctly_separated_sections():
    briefing = {
        'summary': '• A fact',
        'commercial_angle': '• A reason',
        'suggested_action': '• An action',
    }

    assert _normalise_tv_briefing_fields(briefing) == briefing
