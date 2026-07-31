from routes.dashboard import _normalise_tv_briefing_fields


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
