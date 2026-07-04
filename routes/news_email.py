from html import escape

from db import execute as db_execute
from routes.portal_admin import get_email_config, send_email


def get_salesperson_user_email(salesperson_id):
    row = db_execute("""
        SELECT u.email
        FROM salesperson_user_link sul
        JOIN users u ON u.id = sul.user_id
        WHERE sul.legacy_salesperson_id = ?
        LIMIT 1
    """, (salesperson_id,), fetch='one')
    return (row['email'] or '').strip() if row else ''


def build_news_email(salesperson_name, result):
    news_items = result.get('news_items', []) or []
    total_news_items = result.get('total_news_items', len(news_items))
    last_updated = result.get('last_updated', '')

    watched_items = [item for item in news_items if item.get('is_watched')]
    other_items = [item for item in news_items if not item.get('is_watched')]

    def render_items(items):
        list_items_html = []
        list_items_text = []
        for item in items:
            headline = escape(str(item.get('headline', '') or ''))
            summary = escape(str(item.get('summary', '') or ''))
            source = escape(str(item.get('source', '') or ''))
            published_date = escape(str(item.get('published_date', '') or ''))
            customer_name = escape(str(item.get('customer_name', '') or ''))
            watched_label = "Watched customer" if item.get('is_watched') else "Customer"

            list_items_html.append(
                "<li>"
                f"<strong>{headline}</strong><br>"
                f"<em>{watched_label}: {customer_name}</em><br>"
                f"{summary}<br>"
                f"Source: {source} | Date: {published_date}"
                "</li>"
            )
            list_items_text.append(
                f"- {headline}\n"
                f"  {watched_label}: {customer_name}\n"
                f"  Summary: {summary}\n"
                f"  Source: {source} | Date: {published_date}\n"
            )
        return ''.join(list_items_html), ''.join(list_items_text)

    watched_html, watched_text = render_items(watched_items)
    other_html, other_text = render_items(other_items)

    sections_html = []
    sections_text = []
    if watched_items:
        sections_html.append(f"<h3>Watched Customers</h3><ol>{watched_html}</ol>")
        sections_text.append(f"Watched Customers\n{watched_text}")
    if other_items:
        sections_html.append(f"<h3>Other Customer Matches</h3><ol>{other_html}</ol>")
        sections_text.append(f"Other Customer Matches\n{other_text}")

    if not sections_html:
        sections_html.append("<p>No customer news items.</p>")
        sections_text.append("No customer news items.\n")

    html_sections = ''.join(sections_html)
    text_sections = '\n'.join(sections_text)

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #0066cc;">Customer News Update</h2>
        <p><strong>Salesperson:</strong> {escape(str(salesperson_name or ''))}</p>
        <p><strong>Total items:</strong> {total_news_items}</p>
        <p><strong>Last updated:</strong> {escape(str(last_updated))}</p>
        {html_sections}
    </body>
    </html>
    """

    text_body = (
        "Customer News Update\n\n"
        f"Salesperson: {salesperson_name}\n"
        f"Total items: {total_news_items}\n"
        f"Last updated: {last_updated}\n\n"
        "Items:\n"
        f"{text_sections}"
    )

    subject = f"Customer news update ({total_news_items})"
    return subject, html_body, text_body


def send_news_email(salesperson_id, salesperson_name, result):
    total_news_items = result.get('total_news_items', 0)
    if not total_news_items:
        return False

    salesperson_email = get_salesperson_user_email(salesperson_id)
    if not salesperson_email:
        return False

    subject, html_body, text_body = build_news_email(salesperson_name, result)
    return send_email(salesperson_email, subject, html_body, text_body)


def get_news_email_addresses(salesperson_id):
    config = get_email_config()
    return {
        'from_email': (config.get('from_email') or '').strip(),
        'to_email': get_salesperson_user_email(salesperson_id)
    }
