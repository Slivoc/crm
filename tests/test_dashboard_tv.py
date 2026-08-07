from datetime import datetime

from routes.dashboard import (
    _commons_match,
    _monthly_target_pace,
    _normalise_tv_briefing_fields,
    _specific_image_subject,
)


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


def test_specific_aircraft_requires_high_confidence_commons_match():
    candidate = {
        'title': 'File:Airbus H145 rescue helicopter.jpg',
        'imageinfo': [{
            'width': 2400, 'height': 1350, 'mime': 'image/jpeg',
            'extmetadata': {'ImageDescription': {'value': '<p>Airbus H145 on a HEMS mission</p>'}},
        }],
    }

    match = _commons_match(candidate, 'Airbus H145 HEMS helicopter', specific_subject=True)

    assert match['confidence'] == 'HIGH'
    assert _specific_image_subject('How the Airbus H145 supports HEMS', 'Airbus H145 helicopter')


def test_specific_aircraft_rejects_generic_helicopter_image():
    candidate = {
        'title': 'File:Rescue helicopter.jpg',
        'imageinfo': [{
            'width': 2400, 'height': 1350, 'mime': 'image/jpeg',
            'extmetadata': {'ImageDescription': {'value': 'A generic rescue helicopter'}},
        }],
    }

    assert _commons_match(candidate, 'Airbus H145 helicopter', specific_subject=True) is None


def test_commons_match_rejects_portrait_and_low_resolution_images():
    candidate = {
        'title': 'File:Aircraft rivet.jpg',
        'imageinfo': [{
            'width': 700, 'height': 1000, 'mime': 'image/jpeg',
            'extmetadata': {'ImageDescription': {'value': 'Aircraft rivet close up'}},
        }],
    }

    assert _commons_match(candidate, 'aircraft rivet close up') is None
