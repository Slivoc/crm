"""
Legacy HubSpot helpers.

HubSpot integration has been retired from the app surface. These helpers are
kept as no-op shims so existing mail/API flows can continue without the
external HubSpot dependency installed.
"""


def get_or_create_hubspot_company(company):
    return None

def get_or_create_hubspot_contact(contact, company=None):
    return None

def log_email_to_hubspot(contact_id, company_id, subject, body, recipient_email):
    return None
