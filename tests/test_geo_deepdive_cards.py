from routes.geo_deepdive import _deepdive_card_summary


def test_card_summary_prefers_whats_big_here_section():
    content = """# Austria – Rotary-Wing Landscape

## What's Big Here
Austria is overwhelmingly a HEMS-driven market with several major operators.

---

## HEMS / Air Ambulance
- **Example Air** – national operator.
"""

    assert _deepdive_card_summary(content) == (
        'Austria is overwhelmingly a HEMS-driven market with several major operators.'
    )


def test_card_summary_removes_markdown_and_truncates_cleanly():
    content = '# Market\n\nThis is a **busy** market with [important operators](https://example.com). ' + ('Growth is strong. ' * 30)

    summary = _deepdive_card_summary(content, limit=120)

    assert '**' not in summary
    assert '](' not in summary
    assert len(summary) <= 121
    assert summary.endswith('…')
