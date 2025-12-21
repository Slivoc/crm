import hubspot
from hubspot.crm.contacts import ApiException as ContactApiException
from hubspot.crm.companies import ApiException as CompanyApiException
from routes.hubspot_integration import get_hubspot_client
import time
import os


def get_or_create_hubspot_company(company):
    """
    Check if company exists in HubSpot, create if not found
    Returns HubSpot company ID
    """
    try:
        client = get_hubspot_client()

        # Search for company using filter
        public_object_search_request = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "name",
                    "operator": "EQ",
                    "value": company['name']
                }]
            }]
        }

        response = client.crm.companies.search_api.do_search(
            public_object_search_request=public_object_search_request
        )

        if response.results and len(response.results) > 0:
            return response.results[0].id

        # Company not found, create new one
        properties = {
            "name": company['name'],
            "website": company.get('website', ''),
            "phone": company.get('phone', ''),
            "address": company.get('address', '')
        }

        new_company = client.crm.companies.basic_api.create(
            simple_public_object_input_for_create={"properties": properties}
        )

        return new_company.id

    except Exception as e:
        print(f"Error in get_or_create_hubspot_company: {str(e)}")
        raise

def get_or_create_hubspot_contact(contact, company=None):
    """
    Check if contact exists in HubSpot, create if not found
    Returns HubSpot contact ID
    """
    try:
        client = get_hubspot_client()

        # Search for contact using filter
        public_object_search_request = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": contact['email']
                }]
            }]
        }

        response = client.crm.contacts.search_api.do_search(
            public_object_search_request=public_object_search_request
        )

        if response.results and len(response.results) > 0:
            return response.results[0].id

        # Contact not found, create new one
        properties = {
            "email": contact['email'],
            "firstname": contact['name'].split()[0],
            "lastname": " ".join(contact['name'].split()[1:]),
            "jobtitle": contact.get('job_title', '')
        }

        if company:
            properties["company"] = company['name']

        new_contact = client.crm.contacts.basic_api.create(
            simple_public_object_input_for_create={"properties": properties}  # Fixed parameter name here
        )

        return new_contact.id

    except Exception as e:
        print(f"Error in get_or_create_hubspot_contact: {str(e)}")
        raise

def log_email_to_hubspot(contact_id, company_id, subject, body, recipient_email):
    """
    Log an email in HubSpot using the EMAIL object type
    """
    try:
        client = get_hubspot_client()

        # Create email first
        email_data = {
            "properties": {
                "hs_timestamp": str(int(time.time() * 1000)),
                "hs_email_direction": "EMAIL",
                "hs_email_subject": subject,
                "hs_email_text": body,
                "hs_email_status": "SENT"
            }
        }

        # Create the email record
        email_record = client.crm.objects.basic_api.create(
            object_type="email",
            simple_public_object_input_for_create=email_data
        )

        # Create associations using batch_api
        if contact_id:
            client.crm.associations.batch_api.create(
                from_object_type="email",
                to_object_type="contact",
                batch_input_public_association={
                    "inputs": [{
                        "from": {"id": email_record.id},
                        "to": {"id": contact_id},
                        "type": "email_to_contact"
                    }]
                }
            )

        if company_id:
            client.crm.associations.batch_api.create(
                from_object_type="email",
                to_object_type="company",
                batch_input_public_association={
                    "inputs": [{
                        "from": {"id": email_record.id},
                        "to": {"id": company_id},
                        "type": "email_to_company"
                    }]
                }
            )

        return email_record.id

    except Exception as e:
        print(f"Error logging email activity: {str(e)}")
        raise