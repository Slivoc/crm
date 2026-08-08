from helpers.geo_deepdive import deepdive_summary_text
from models.part_4 import _match_geographic_deepdive_company
from routes.geo_deepdive import (
    _build_deepdive_company_summary,
    _normalise_deepdive_companies,
    _parse_deepdive_ai_result,
)


def test_card_summary_prefers_whats_big_here_section():
    content = """# Austria – Rotary-Wing Landscape

## What's Big Here
Austria is overwhelmingly a HEMS-driven market with several major operators.

---

## HEMS / Air Ambulance
- **Example Air** – national operator.
"""

    assert deepdive_summary_text(content) == (
        'Austria is overwhelmingly a HEMS-driven market with several major operators.'
    )


def test_card_summary_removes_markdown_and_truncates_cleanly():
    content = '# Market\n\nThis is a **busy** market with [important operators](https://example.com). ' + ('Growth is strong. ' * 30)

    summary = deepdive_summary_text(content, limit=120)

    assert '**' not in summary
    assert '](' not in summary
    assert len(summary) <= 121
    assert summary.endswith('…')


def test_ai_result_returns_markdown_and_deduplicated_companies():
    content, companies = _parse_deepdive_ai_result('''{
        "content_markdown": "# Market\\n\\nOverview",
        "mentioned_companies": [
            {"name":"Example Air", "company_type":"operator", "is_main":true},
            {"name":"Example-Air", "company_type":"operator", "is_main":false},
            {"name":"", "company_type":"invalid"}
        ]
    }''')

    assert content.startswith('# Market')
    assert companies == [{
        'name': 'Example Air',
        'company_type': 'operator',
        'role_summary': '',
        'why_relevant': '',
        'website': '',
        'country': '',
        'source_urls': [],
        'mention_sections': [],
        'is_main': True,
    }]


def test_company_normaliser_rejects_non_https_sources():
    companies = _normalise_deepdive_companies([{
        'name': 'Rotor MRO',
        'source_urls': ['http://unsafe.example', 'https://safe.example/source'],
        'mention_sections': ['MRO', ''],
    }])

    assert companies[0]['source_urls'] == ['https://safe.example/source']
    assert companies[0]['mention_sections'] == ['MRO']


def test_explicit_body_link_is_authoritative_for_company_match():
    customer = {
        'id': 17,
        'name': 'HeliService International',
        'normalised_name': 'heliserviceinternational',
        'domain': '',
    }
    context = {
        'customers': [customer],
        'customer_by_id': {17: customer},
        'canonical': {'heliserviceinternational': [customer]},
        'domains': {},
        'aliases': {},
        'links': {
            'heliserviceinternationalgmbh': [
                {'customer_id': 17, 'linked_text': 'HeliService International GmbH'}
            ]
        },
    }

    match, confidence, method, status = _match_geographic_deepdive_company(
        {'name': 'HeliService International GmbH'}, context
    )

    assert match['id'] == 17
    assert confidence == 1.0
    assert method == 'explicit_text_link'
    assert status == 'confirmed'


def test_company_summary_prioritises_main_categories_and_unmatched_companies():
    summary = _build_deepdive_company_summary([
        {
            'company_name': 'Matched Air',
            'company_type': 'Operator',
            'is_main': True,
            'matched_customer_id': 1,
            'match_status': 'confirmed',
        },
        {
            'company_name': 'Unmatched Air',
            'company_type': 'Operator',
            'is_main': True,
            'matched_customer_id': None,
            'match_status': 'unmatched',
        },
        {
            'company_name': 'Possible MRO',
            'company_type': 'MRO',
            'is_main': False,
            'matched_customer_id': 2,
            'match_status': 'suggested',
        },
    ])

    assert summary['total'] == 3
    assert summary['main_count'] == 2
    assert summary['matched_count'] == 1
    assert summary['suggested_count'] == 1
    assert summary['coverage_percent'] == 33
    assert summary['unmatched_count'] == 1
    assert [row['company_name'] for row in summary['unmatched_main']] == ['Unmatched Air']
    assert [category['name'] for category in summary['categories']] == ['Operator', 'MRO']


def test_company_summary_handles_an_empty_market_map():
    summary = _build_deepdive_company_summary([])

    assert summary['total'] == 0
    assert summary['matched_count'] == 0
    assert summary['coverage_percent'] == 0
