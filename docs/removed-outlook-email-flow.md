# Removed Outlook Email Flow

This note documents the Windows-specific Outlook integration that was removed when the app moved to Microsoft Graph-only sending.

## What Was Removed

- `pywin32` from `requirements.txt`
- `pythoncom` and `win32com.client` imports from `routes/sales_orders.py`
- The helper `send_email_via_outlook(...)` in `routes/sales_orders.py`
- The route `sales_orders.generate_acknowledgment_outlook`
- The `Email via Outlook` action from `templates/view_acknowledgments.html`
- Frontend Outlook-send UI from:
  - `templates/components/email_modal.html`
  - `static/js/email_modal.js`
  - `templates/parts_list_email_suppliers.html`

## How The Old Backend Flow Worked

The removed acknowledgment flow lived in `routes/sales_orders.py`.

1. The route `/<int:sales_order_id>/generate_acknowledgment_outlook` loaded the sales order and its lines.
2. It generated the acknowledgment PDF by calling `generate_sales_order_acknowledgment_file(sales_order)`.
3. It built a placeholder email:
   - recipient: `customer_email@example.com`
   - subject: `Acknowledgment for Sales Order #{sales_order['id']}`
   - body: a plain-text greeting/body string
4. It passed those values into `send_email_via_outlook(...)`.

The removed helper then:

1. Called `pythoncom.CoInitialize()`
2. Resolved the attachment path to an absolute path
3. Checked the file existed
4. Opened Outlook via COM with:
   - `win32.Dispatch('outlook.application')`
5. Created a new mail item with:
   - `outlook.CreateItem(0)`
6. Set:
   - `mail.To`
   - `mail.Subject`
   - `mail.Body`
7. Added the PDF via:
   - `mail.Attachments.Add(attachment_path)`
8. Displayed the draft using:
   - `mail.Display()`
9. Called `pythoncom.CoUninitialize()`

Important detail:

- This flow did not send the email automatically.
- It opened a draft in the locally installed Windows Outlook client.
- Because it depended on COM automation, it required Windows plus Outlook desktop plus `pywin32`.

## How The Old Frontend Outlook Flow Worked

Two UI flows exposed Outlook as a send option.

### Shared email modal

Files:

- `templates/components/email_modal.html`
- `static/js/email_modal.js`

Behavior:

- After previewing an email, the modal showed an Outlook-oriented button.
- The JS personalized the email per recipient.
- For single-recipient flows it copied HTML to the clipboard and opened a `mailto:` link.
- For multi-recipient flows it stepped through recipients one by one in a helper modal and repeated that same copy/open pattern.
- The user then pasted the copied content into Outlook manually.

### Parts list supplier email modal

File:

- `templates/parts_list_email_suppliers.html`

Behavior:

- The modal offered both:
  - `Send via System`
  - `Copy & Open Outlook`
- The Outlook button copied HTML/plain text to the clipboard, opened a `mailto:` link, and then recorded the supplier-email log entries.

## Reinstatement Notes

If this ever needs to come back, the minimum work is:

1. Re-add `pywin32` to `requirements.txt`
2. Restore the removed COM imports in `routes/sales_orders.py`
3. Restore `send_email_via_outlook(...)`
4. Restore the acknowledgment route and the button in `templates/view_acknowledgments.html`
5. Restore the Outlook send button(s) and JS handlers in the email modals

If reintroduced, it would be better to keep it behind a feature flag so Graph remains the default and cross-platform path.
