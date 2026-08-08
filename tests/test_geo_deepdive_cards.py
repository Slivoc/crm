from helpers.geo_deepdive import deepdive_summary_text
from routes.geo_deepdive import _normalise_deepdive_companies, _parse_deepdive_ai_result


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
