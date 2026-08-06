import io
import sys
from types import SimpleNamespace

from flask import Flask

from routes import parts_list


def test_supplier_quote_extraction_keeps_man_and_test_certs_separate(monkeypatch):
    payload = """[{"part_number":"NAS123-4","quantity":25,"price":1.25,
        "certifications":"OEM certs","test_certs":"chemical test",
        "cage_code":"01234","manufacturer":"Example Aerospace","is_no_bid":false}]"""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response)
        )
    )
    monkeypatch.setattr(parts_list, 'client', fake_client)

    extracted = parts_list.extract_supplier_quote_data('quoted source text')

    assert extracted[0]['certifications'] == 'OEM certs'
    assert extracted[0]['test_certs'] == 'chemical test'
    assert extracted[0]['cage_code'] == '01234'


def test_supplier_quote_extraction_recovers_parenthesized_cage_from_item_line(monkeypatch):
    payload = """[{"part_number":"ASNA0045-120BCL","quantity":100,"price":10,
        "certifications":null,"test_certs":null,"cage_code":null,
        "manufacturer":"Bollhoff","is_no_bid":false}]"""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response)
        )
    )
    monkeypatch.setattr(parts_list, 'client', fake_client)

    quote_text = (
        'Certificate Codes: 1+FAI First Article Insp. Report; 1 MFG+TR+LS (all)\n'
        '1 100 EA ASNA0045-120BCL NUT, STEEL 10,00 EA Note '
        'Bollhoff SNEP (F2094) April 2027 1.000,00'
    )
    extracted = parts_list.extract_supplier_quote_data(quote_text)

    assert extracted[0]['cage_code'] == 'F2094'
    assert extracted[0]['certifications'] is None
    assert extracted[0]['test_certs'] is None


def test_parenthesized_cage_fallback_does_not_use_unrelated_legend_code():
    quote_text = (
        '1 100 EA ASNA0045-120BCL NUT, STEEL 10,00 EA Bollhoff\n'
        'Certificate Codes: First Article Inspection Report (FAIR1)'
    )

    assert parts_list._extract_cage_code_near_part(
        quote_text,
        'ASNA0045-120BCL',
    ) is None


def test_supplier_quote_source_is_stored_and_revalidated(tmp_path):
    app = Flask(__name__)
    app.config['UPLOAD_FOLDER'] = str(tmp_path)

    with app.app_context():
        stored = parts_list._store_supplier_quote_source(
            b'%PDF-test-content',
            'Supplier Quote.pdf',
            'application/pdf',
            'supplier_quote_pdf',
        )
        validated = parts_list._validated_supplier_quote_source(stored)

    assert validated['filename'] == 'Supplier_Quote.pdf'
    assert validated['kind'] == 'supplier_quote_pdf'
    assert validated['size'] == len(b'%PDF-test-content')
    assert validated['sha256'] == stored['sha256']


def test_extract_pdf_text_preserves_original_pdf_source(monkeypatch, tmp_path):
    class FakePdf:
        pages = [SimpleNamespace(extract_text=lambda: 'quoted PDF text')]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        'pdfplumber',
        SimpleNamespace(open=lambda stream: FakePdf()),
    )

    app = Flask(__name__)
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    app.register_blueprint(parts_list.parts_list_bp, url_prefix='/parts_list')

    pdf_bytes = b'%PDF-original-attachment-bytes'
    response = app.test_client().post(
        '/parts_list/extract-pdf-text',
        data={'file': (io.BytesIO(pdf_bytes), 'Mailbox Quote.pdf', 'application/pdf')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['text'] == 'quoted PDF text'
    assert payload['source_artifact']['filename'] == 'Mailbox_Quote.pdf'
    assert payload['source_artifact']['kind'] == 'supplier_quote_pdf'
    stored_path = tmp_path / 'supplier_quote_sources' / payload['source_artifact']['path']
    assert stored_path.read_bytes() == pdf_bytes


def test_store_supplier_email_source_preserves_tables_and_sanitizes_html(tmp_path):
    app = Flask(__name__)
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    app.register_blueprint(parts_list.parts_list_bp, url_prefix='/parts_list')

    response = app.test_client().post(
        '/parts_list/store-supplier-email-source',
        json={
            'subject': 'Quote ABC-123',
            'from': {'name': 'Supplier Sales', 'address': 'sales@example.com'},
            'to_recipients': [{'name': 'Buyer', 'address': 'buyer@example.com'}],
            'received_at': '2026-08-06T10:30:00Z',
            'body': {
                'content_type': 'html',
                'content': (
                    '<p>Please see our offer:</p>'
                    '<table><tr><th>Part</th><th>Price</th></tr>'
                    '<tr><td>ABC-123</td><td>$12.50</td></tr></table>'
                    '<script>alert("unsafe")</script>'
                    '<img src="https://tracker.example/pixel" onerror="alert(1)">'
                ),
            },
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['source_artifact']['kind'] == 'email_body_html'
    assert payload['source_artifact']['filename'] == 'Quote_ABC-123.html'
    stored_path = tmp_path / 'supplier_quote_sources' / payload['source_artifact']['path']
    snapshot = stored_path.read_text(encoding='utf-8')
    assert '<table>' in snapshot
    assert '<td>ABC-123</td>' in snapshot
    assert 'Supplier Sales &lt;sales@example.com&gt;' in snapshot
    assert '<script' not in snapshot
    assert '<img' not in snapshot
    assert 'onerror' not in snapshot
