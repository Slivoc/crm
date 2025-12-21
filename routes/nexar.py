from flask import Blueprint, jsonify
import requests
import json

nexar_bp = Blueprint('nexar', __name__)

# Nexar API credentials
CLIENT_ID = 'f1ecee4d-e885-4bbd-9355-0008c3e0f851'
CLIENT_SECRET = 'j3Xu-ZaLbykhykGvePDpAvuUeL7Z6IQ9r3Zg'

# Nexar API endpoints
TOKEN_URL = 'https://identity.nexar.com/connect/token'
GRAPHQL_URL = 'https://api.nexar.com/graphql'


def get_access_token():
    """Obtain an access token from Nexar."""
    payload = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scope': 'supply.domain'
    }

    print("Requesting access token...")
    response = requests.post(TOKEN_URL, data=payload)

    # Print response status for debugging
    print(f"Token response status: {response.status_code}")

    # If there's an error, print more details
    if response.status_code != 200:
        print(f"Token response error: {response.text}")

    response.raise_for_status()
    token_data = response.json()
    print("Access token obtained successfully")
    return token_data['access_token']


def search_part(mpn):
    """Search for a part by MPN using the Nexar API."""
    try:
        access_token = get_access_token()

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        # Simplified query to troubleshoot
        query = {
            'query': '''
            query SearchPart($mpn: String!) {
              supSearchMpn(q: $mpn, limit: 10) {
                results {
                  part {
                    mpn
                    manufacturer {
                      name
                    }
                    sellers {
                      company {
                        name
                      }
                      offers {
                        prices {
                          quantity
                          price
                          currency
                        }
                        inventoryLevel
                      }
                    }
                  }
                }
              }
            }
            ''',
            'variables': {'mpn': mpn}
        }

        print(f"Searching for MPN: {mpn}")
        print(f"GraphQL query: {json.dumps(query)}")

        response = requests.post(GRAPHQL_URL, json=query, headers=headers)

        # Print response status for debugging
        print(f"GraphQL response status: {response.status_code}")

        # If there's an error, print more details
        if response.status_code != 200:
            print(f"GraphQL response error: {response.text}")

        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error in search_part: {str(e)}")
        raise


@nexar_bp.route('/octopart_search/<path:part_number>', methods=['GET'])
def octopart_search(part_number):
    """API endpoint to search for a part by MPN"""
    try:
        print(f"Searching for part: {part_number}")
        result = search_part(part_number)

        # Debug print
        print(f"GraphQL response status: 200")
        print(f"API Response: {json.dumps(result)[:200]}...")

        # Check if we have data
        if 'data' in result and 'supSearchMpn' in result['data']:
            # Extract the results from the nested data structure
            search_results = result['data']['supSearchMpn']['results']

            if search_results:
                # Format the response to match what the frontend expects
                formatted_response = {
                    "results": search_results
                }
                return jsonify(formatted_response)
            else:
                print(f"No results found for {part_number}")
                return jsonify({"results": []})
        else:
            print(f"Unexpected response format: {json.dumps(result)}")
            return jsonify({"error": "Invalid response format from API", "raw_response": result}), 500

    except Exception as e:
        print(f"Error in octopart_search: {str(e)}")
        return jsonify({"error": str(e)}), 500