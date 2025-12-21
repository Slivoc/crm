import hubspot
from hubspot.crm.contacts import ApiException as ContactApiException
from hubspot.crm.companies import ApiException as CompanyApiException
from hubspot.crm.timeline.exceptions import ApiException as TimelineApiException
from flask import Blueprint, jsonify
import os

hubspot_bp = Blueprint('hubspot', __name__)

def get_hubspot_client():
    api_key = os.getenv('HUBSPOT_API_KEY')
    if not api_key:
        raise ValueError("HUBSPOT_API_KEY environment variable is not set")
    return hubspot.Client.create(access_token=api_key)


@hubspot_bp.route('/setup', methods=['POST'])
def setup_hubspot():
    """Set up required HubSpot configurations"""
    try:
        client = get_hubspot_client()

        # Create engagement property if needed
        properties_config = {
            "name": "hs_email_sent",
            "label": "Email Sent",
            "type": "string",
            "fieldType": "text",
            "groupName": "email_tracking",
            "options": []
        }

        try:
            client.crm.properties.core_api.create(
                object_type="contacts",
                property_create=properties_config
            )
        except Exception as e:
            print(f"Property might already exist: {str(e)}")

        return jsonify({
            "success": True,
            "message": "HubSpot configuration completed successfully"
        })

    except Exception as e:
        print(f"Setup error: {str(e)}")
        return jsonify({"success": False, "error": str(e)})


@hubspot_bp.route('/test', methods=['GET'])
def test_hubspot_connection():
    """
    Route to test if HubSpot API is properly configured
    """
    try:
        client = get_hubspot_client()

        # Try to fetch a simple API response
        response = client.crm.contacts.basic_api.get_page(limit=1)

        return jsonify({
            'success': True,
            'message': 'HubSpot connection successful'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def create_email_activity_type(client):
    """
    Create a custom activity type for email logging if it doesn't exist
    """
    try:
        activity_type = {
            "name": "Email Sent",
            "primaryDisplayProperty": "subject",
            "properties": [
                {
                    "name": "subject",
                    "label": "Subject",
                    "type": "STRING",
                    "fieldType": "TEXT"
                },
                {
                    "name": "body",
                    "label": "Email Body",
                    "type": "STRING",
                    "fieldType": "TEXT"
                },
                {
                    "name": "recipient",
                    "label": "Recipient",
                    "type": "STRING",
                    "fieldType": "TEXT"
                }
            ]
        }

        response = client.crm.timeline.timeline_api.create(
            object_type="contact",
            activity_type=activity_type
        )

        return response.id
    except Exception as e:
        print(f"Error creating activity type: {str(e)}")
        raise