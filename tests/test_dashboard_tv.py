from datetime import datetime
from unittest.mock import MagicMock

from routes.dashboard import (
    _commons_image_metadata,
    _commons_match,
    _group_tv_geographic_deepdives,
    _monthly_target_pace,
    _normalise_customer_focus_insight,
    _normalise_tv_company_type,
    _normalise_tv_briefing_fields,
    _specific_image_subject,
    _tv_aircraft_traffic,
    _tv_geographic_deepdives,
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


def test_commons_image_metadata_builds_safe_display_credit():
    candidate = {
        'imageinfo': [{
            'url': 'https://upload.wikimedia.org/example.jpg',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Example.jpg',
            'extmetadata': {
                'Artist': {'value': '<b>Example Photographer</b>'},
                'LicenseShortName': {'value': 'CC BY-SA 4.0'},
            },
        }],
    }

    _, metadata = _commons_image_metadata(candidate)

    assert metadata['image_source'] == 'Wikimedia Commons'
    assert metadata['image_source_url'].startswith('https://commons.wikimedia.org/')
    assert metadata['image_attribution'] == 'Example Photographer, CC BY-SA 4.0, Wikimedia Commons'


def test_customer_focus_insight_is_constrained_for_tv_display():
    insight = _normalise_customer_focus_insight({
        'description': 'A helicopter operator and maintenance organisation.',
        'similar_companies': [
            {'name': f'Operator {index}', 'reason': 'Similar fleet and maintenance needs'}
            for index in range(6)
        ],
        'source_urls': ['http://unsafe.example', 'https://example.com/company'],
    })

    assert insight['description'].startswith('A helicopter operator')
    assert len(insight['similar_companies']) == 4
    assert insight['source_urls'] == ['https://example.com/company']


def test_tv_company_types_are_distilled_for_landscape_slides():
    assert _normalise_tv_company_type('Rotary operator / Part-145 MRO') == 'Operator & MRO'
    assert _normalise_tv_company_type('Fixed-wing maintenance organisation') == 'MRO'
    assert _normalise_tv_company_type('Commercial airline operator') == 'Operator'


def test_tv_geographic_deepdives_include_crm_status_and_lifetime_spend():
    slides = _group_tv_geographic_deepdives([
        {
            'deepdive_id': 3, 'deepdive_title': 'UK Rotary Landscape',
            'country': 'GB', 'tag_description': 'Rotary Wing', 'deepdive_updated_at': '2026-08-08',
            'company_id': 10, 'company_name': 'Example Air', 'company_type': 'Operator',
            'is_main': True, 'display_order': 1, 'match_status': 'confirmed',
            'matched_customer_id': 7, 'matched_customer_name': 'Example Air Ltd',
            'matched_customer_status': 'Active Customer', 'lifetime_spend': 125000,
            'estimated_revenue': 18000000, 'fleet_size': 42, 'mro_score': None,
        },
        {
            'deepdive_id': 3, 'deepdive_title': 'UK Rotary Landscape',
            'country': 'GB', 'tag_description': 'Rotary Wing', 'deepdive_updated_at': '2026-08-08',
            'company_id': 11, 'company_name': 'Gap MRO', 'company_type': 'Part-145 maintenance',
            'is_main': True, 'display_order': 2, 'match_status': 'unmatched',
            'matched_customer_id': None, 'matched_customer_name': None,
            'matched_customer_status': None, 'lifetime_spend': 0,
            'estimated_revenue': None, 'fleet_size': None, 'mro_score': None,
        },
    ])

    assert slides[0]['company_count'] == 2
    assert slides[0]['matched_count'] == 1
    assert slides[0]['gap_count'] == 1
    assert slides[0]['coverage_percent'] == 50
    assert slides[0]['companies'][0]['customer_status'] == 'Active Customer'
    assert slides[0]['companies'][0]['lifetime_spend'] == 125000
    assert slides[0]['companies'][0]['estimated_revenue'] == 18000000
    assert slides[0]['companies'][0]['fleet_size'] == 42
    assert slides[0]['companies'][1]['type'] == 'MRO'
    assert slides[0]['estimated_revenue'] == 18000000
    assert slides[0]['revenue_company_count'] == 1
    assert slides[0]['sized_company_count'] == 1


def test_tv_geographic_deepdives_do_not_break_tv_when_migration_is_missing():
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError('missing table')

    assert _tv_geographic_deepdives(connection) == []
    connection.rollback.assert_called_once()


def test_tv_aircraft_traffic_returns_ranked_30_day_snapshot():
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = {
        'flight_count': 82,
        'aircraft_count': 14,
        'customer_count': 5,
        'estimated_flight_hours': '126.75',
        'aircraft_types': [{
            'name': 'H145', 'flight_count': 28, 'aircraft_count': 4,
            'estimated_flight_hours': 48.5,
        }],
        'customers': [{
            'customer_id': 7, 'name': 'Example Air', 'flight_count': 20,
            'aircraft_count': 3, 'estimated_flight_hours': 42.25,
        }],
        'aircraft': [{
            'name': 'G-TEST', 'aircraft_type': 'H145', 'customer_name': 'Example Air',
            'flight_count': 12, 'estimated_flight_hours': 21.5,
        }],
    }

    traffic = _tv_aircraft_traffic(connection)

    assert traffic['period_days'] == 30
    assert traffic['summary']['estimated_flight_hours'] == 126.75
    assert traffic['aircraft_types'][0]['name'] == 'H145'
    assert traffic['customers'][0]['name'] == 'Example Air'
    assert traffic['aircraft'][0]['name'] == 'G-TEST'
    query = connection.execute.call_args.args[0]
    assert "INTERVAL '30 days'" in query
    assert 'ORDER BY estimated_flight_hours DESC' in query


def test_tv_aircraft_traffic_does_not_break_tv_when_integration_is_missing():
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError('missing table')

    traffic = _tv_aircraft_traffic(connection)

    assert traffic['summary']['flight_count'] == 0
    assert traffic['aircraft_types'] == []
    connection.rollback.assert_called_once()
