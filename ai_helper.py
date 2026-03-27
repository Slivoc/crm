import openai
import os
from dotenv import load_dotenv
import logging
import re
from flask import jsonify
import json
from models import get_all_company_types, get_db_connection, get_consolidated_customer_ids, get_consolidated_customer_orders
from datetime import datetime, timedelta
import time
from openai import OpenAI
from flask import current_app

from pathlib import Path

# Load .env from the parent directory (where it actually exists)
current_dir = Path(__file__).parent  # C:\crm\routes
parent_dir = current_dir.parent      # C:\crm
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = openai.Client(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
if client is None:
    logging.warning("OPENAI_API_KEY not found. AI-assisted features will run in fallback mode.")

logging.basicConfig(level=logging.DEBUG)

def extract_part_numbers_and_quantities(request_data):
    print("Starting extract_part_numbers_and_quantities function")
    print(f"Input request_data:\n{request_data}")

    try:
        if client is None:
            logging.warning("OPENAI_API_KEY is not set; skipping AI part/quantity extraction.")
            return []

        print("Attempting to send request to OpenAI API")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "You are an assistant tasked with extracting specific information from text. Provide concise part numbers and quantities without additional commentary or formatting. Just give 'Part number:' and 'Quantity:'"},
                {"role": "user",
                 "content": f"Please extract part numbers and quantities from the following text:\n\n{request_data}"}
            ],
            max_tokens=500,
            temperature=0.2,
        )
        print("Successfully received response from OpenAI API")
        print(f"Full API response:\n{response}")

        extracted_data = response.choices[0].message.content.strip()
        print(f"Extracted data from API response:\n{extracted_data}")

        parsed_data = parse_extracted_data(extracted_data)
        print(f"Parsed data: {parsed_data}")

        return parsed_data

    except openai.AuthenticationError as e:
        print(f"Authentication error: {str(e)}")
        print("Check your OpenAI API key.")
        raise
    except openai.APIError as e:
        print(f"OpenAI API error: {str(e)}")
        raise
    except Exception as e:
        print(f"Unexpected error in extract_part_numbers_and_quantities: {str(e)}")
        raise

def extract_quote_info(request_data):
    logging.debug("Sending request data to OpenAI API")

    if client is None:
        logging.warning("OPENAI_API_KEY is not set; skipping AI quote extraction.")
        return []

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system",
             "content": "You are an assistant tasked with extracting specific information from text. Provide concise details including 'Part number:', 'Quantity:', 'Price:', 'Lead time:', and 'Manufacturer:'. If any information is not available, leave it blank but maintain the structure."},
            {"role": "user",
             "content": f"Please extract part numbers, quantities, prices (strip currency symbols), lead times (round days up to weeks and only give the number - do not say 'weeks'), and manufacturers from the following text, giving multiple lines if there are multiple part numbers:\n\n{request_data}"}
        ],
        max_tokens=1500,
        temperature=0.2,
    )

    logging.debug(f"Received response: {response}")

    extracted_data = response.choices[0].message.content.strip()
    logging.debug(f"Extracted data: {extracted_data}")

    return parse_extracted_quote_info(extracted_data)



def parse_extracted_quote_info(extracted_data):
    extracted_lines = []
    parts = extracted_data.split('\n\n')

    for part in parts:
        part_number = quantity = price = lead_time = manufacturer = None
        lines = part.split('\n')
        for line in lines:
            if 'Part number:' in line:
                part_number = line.split('Part number:')[1].strip()
            elif 'Quantity:' in line:
                quantity = line.split('Quantity:')[1].strip()
            elif 'Price:' in line:
                price = line.split('Price:')[1].strip()
            elif 'Lead time:' in line:
                lead_time = line.split('Lead time:')[1].strip()
            elif 'Manufacturer:' in line:
                manufacturer = line.split('Manufacturer:')[1].strip()

        if part_number and quantity:
            extracted_lines.append((part_number, quantity, price, lead_time, manufacturer))

    return extracted_lines


def parse_extracted_data(extracted_data):
    print("Starting parse_extracted_data function")
    print(f"Input extracted_data:\n{extracted_data}")

    pattern = r'Part\s*number:\s*(.*?)\s*\nQuantity:\s*(\d+)\s*'
    matches = re.findall(pattern, extracted_data, re.IGNORECASE)
    print(f"Regex matches: {matches}")

    extracted_lines = [(part_number.strip(), int(quantity)) for part_number, quantity in matches]
    print(f"Extracted lines: {extracted_lines}")

    return extracted_lines


def generate_industry_insights_with_custom_prompt(prompt, customer_names):
    """Modified version of generate_industry_insights that accepts a custom prompt"""
    try:
        logging.debug(f"Using custom AI Prompt: {prompt}")

        client = OpenAI()  # This will use OPENAI_API_KEY from environment

        response = client.chat.completions.create(
            model="gpt-4o",  # Updated from gpt-4o to gpt-4
            messages=[
                {"role": "system",
                 "content": "You are a business development assistant. Return only valid JSON arrays without markdown tags. Always provide revenue estimates as numbers, not text strings."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.2,
        )

        response_content = response.choices[0].message.content.strip()
        logging.debug(f"Raw AI response content: {response_content}")

        # Rest of the processing remains the same as in original generate_industry_insights
        if response_content.startswith('```'):
            parts = response_content.split('```')
            if len(parts) >= 2:
                response_content = parts[1]
                if response_content.startswith('json'):
                    response_content = response_content[4:]

        response_content = response_content.strip()
        logging.debug(f"Cleaned response content: {response_content}")

        try:
            industry_insights = json.loads(response_content)

            if not isinstance(industry_insights, list):
                logging.error("Parsed JSON is not a list")
                return [], prompt

            # Convert revenues to numbers if they're strings
            for insight in industry_insights:
                if isinstance(insight.get('estimated_revenue'), str):
                    revenue_str = ''.join(filter(str.isdigit, insight['estimated_revenue']))
                    insight['estimated_revenue'] = int(revenue_str) if revenue_str else 0

            logging.debug(f"Successfully parsed insights: {industry_insights}")
            return industry_insights, prompt

        except json.JSONDecodeError as e:
            logging.error(f"JSON decoding error: {str(e)}")
            logging.error(f"Failed to parse content: {response_content}")
            return [], prompt

    except Exception as e:
        logging.error(f"Error in generate_industry_insights: {str(e)}")
        logging.error("Stack trace:", exc_info=True)
        return [], prompt

def generate_industry_insights(customer_names, tag_description, continent=None, countries=None):
    try:
        # Generate the prompt using the same function as preview
        prompt = generate_preview_prompt(customer_names, tag_description, continent, countries)
        logging.debug(f"Generated AI Prompt: {prompt}")

        # Call the OpenAI API with simpler system prompt
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "You are a business development assistant. Return only valid JSON arrays without markdown tags. Always provide revenue estimates as numbers, not text strings."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.2,
        )

        response_content = response.choices[0].message['content'].strip()
        logging.debug(f"Raw AI response content: {response_content}")

        # Remove markdown code blocks if present
        if response_content.startswith('```'):
            parts = response_content.split('```')
            if len(parts) >= 2:
                response_content = parts[1]
                if response_content.startswith('json'):
                    response_content = response_content[4:]

        response_content = response_content.strip()
        logging.debug(f"Cleaned response content: {response_content}")

        try:
            industry_insights = json.loads(response_content)

            if not isinstance(industry_insights, list):
                logging.error("Parsed JSON is not a list")
                return [], prompt

            # Convert revenues to numbers if they're strings
            for insight in industry_insights:
                if isinstance(insight.get('estimated_revenue'), str):
                    revenue_str = ''.join(filter(str.isdigit, insight['estimated_revenue']))
                    insight['estimated_revenue'] = int(revenue_str) if revenue_str else 0

            logging.debug(f"Successfully parsed insights: {industry_insights}")
            return industry_insights, prompt

        except json.JSONDecodeError as e:
            logging.error(f"JSON decoding error: {str(e)}")
            logging.error(f"Failed to parse content: {response_content}")
            return [], prompt

    except Exception as e:
        logging.error(f"Error in generate_industry_insights: {str(e)}")
        logging.error(f"Stack trace: ", exc_info=True)
        return [], prompt


def generate_preview_prompt(customer_names, tag_description, continent=None, countries=None):
    """Generate a preview of the prompt without making the API call"""

    # Build the geography part of the prompt
    geography_filter = ""
    if continent:
        geography_filter = f"focusing on {continent}"
        if countries and any(countries):
            country_list = ", ".join(countries)
            geography_filter = f"focusing specifically on country {country_list} in {continent}"
    else:
        geography_filter = "focusing on Europe"  # Default case

    prompt = (
        f"Based on the following existing customer names and the industry tag description '{tag_description}', "
        f"please suggest potential target companies in this industry, {geography_filter}. "
        "Do not include customers that are already in the list. Remember that we are a connector manufacturer and distributor. "
        "Only suggest companies that would need this service. Include ISO alpha-2 country codes in your responses using the 'country' field.\n\n"
        "Return a JSON array containing companies with this exact format:\n"
        "{\n"
        '    "name": "Company Name",\n'
        '    "description": "Company description",\n'
        '    "estimated_revenue": 1000000,\n'
        '    "website": "https://www.example.com",\n'  # Changed to include full URL
        '    "country": "IT"\n'        
        "}\n\n"
        "Important: Always provide complete website URLs including https://\n\n"  # Added explicit instruction
        "Existing customer names:\n"
    )

    for name in customer_names:
        prompt += f"- {name}\n"

    return prompt

def enrich_customer_data(customer_data, available_tags):
    """Call OpenAI API to enrich customer data"""
    try:
        if client is None:
            raise ValueError("OPENAI_API_KEY is not set; AI enrichment is unavailable.")

        prompt = generate_enrichment_prompt(customer_data, available_tags)
        logging.debug(f"Generated AI Prompt for enrichment: {prompt}")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "You are a business data enrichment assistant. Return ONLY the raw JSON object. Do not add markdown formatting, code blocks, or any other text. The response should start with { and end with } with no other characters."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.2,
        )

        # Access content from response
        response_content = response.choices[0].message.content
        logging.debug(f"Raw AI Response: {response_content}")

        # Clean up response
        cleaned_content = response_content.strip()
        if cleaned_content.startswith("```"):
            cleaned_content = cleaned_content.split("```")[1]
            if cleaned_content.startswith("json"):
                cleaned_content = cleaned_content[4:]
        cleaned_content = cleaned_content.strip()

        enrichment_data = json.loads(cleaned_content)
        logging.debug(f"Parsed enrichment data: {enrichment_data}")
        return enrichment_data

    except json.JSONDecodeError as je:
        logging.error(f"JSON parsing error: {str(je)}")
        logging.error(f"Failed to parse content: {cleaned_content}")
        raise ValueError("Invalid JSON response from AI service")

    except Exception as e:
        logging.error(f"Error in AI enrichment: {str(e)}")
        raise ValueError(f"AI enrichment failed: {str(e)}")

def generate_enrichment_prompt(customer_data, available_tags):
    """Generate prompt for the AI to enrich customer data"""
    try:
        # Format tags using the correct column names
        tags_text = "\n".join([f"ID: {tag['id']} - {tag['name']}" for tag in available_tags])

        # Get company types
        company_types = get_all_company_types()
        company_types_text = "\n".join([f"ID: {ct['id']} - {ct['name']}" for ct in company_types])

        prompt = f"""Analyze this customer and return a raw JSON object only.

Customer Information:
Name: {customer_data['name']}
Description: {customer_data['description'] or 'Not provided'}
Website: {customer_data['website'] or 'Not provided'}

Available industry tags:
{tags_text}

Available company types:
{company_types_text}

Return only a raw JSON object with these exact fields:
- estimated_revenue (number)
- suggested_tag_ids (array of numbers, max 3)
- suggested_company_type_ids (array of numbers, max 2)
- country_code (string, ISO alpha-2)
- fleet_size (optional, integer if available)

Example (return exactly like this):
{{"estimated_revenue": 1500000, "suggested_tag_ids": [1, 4, 7], "suggested_company_type_ids": [1, 2], "country_code": "IT", "fleet_size": 120}}
"""

        return prompt

    except Exception as e:
        print(f"Error in prompt generation: {str(e)}")
        print(f"Available tags first item: {dict(available_tags[0])}")
        if company_types:
            print(f"Company types first item: {dict(company_types[0])}")
        raise


def validate_bulk_enrichment_data(data):
    """Validate the AI-generated enrichment data for bulk processing"""
    required_fields = [
        'estimated_revenue',
        'country_code',
        'matched_tag_ids',
        'suggested_new_tags',
        'matched_company_type_ids'
    ]

    # Check all required fields exist
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    # Validate data types
    if not isinstance(data['estimated_revenue'], (int, float)) or data['estimated_revenue'] < 0:
        raise ValueError("Revenue must be a positive number")

    if not isinstance(data['country_code'], str) or len(data['country_code']) != 2:
        raise ValueError("Invalid country code format")

    if not isinstance(data['matched_tag_ids'], list):
        raise ValueError("matched_tag_ids must be a list")

    if not isinstance(data['suggested_new_tags'], list):
        raise ValueError("suggested_new_tags must be a list")

    if not isinstance(data['matched_company_type_ids'], list):
        raise ValueError("matched_company_type_ids must be a list")


from openai import OpenAI
import json
import logging

def bulk_enrich_customer_data(customer, available_tags, company_types):
    """Bulk enrichment version that handles tag suggestions separately"""
    try:
        example = '''{
    "estimated_revenue": 5000000,
    "country_code": "US",
    "matched_tag_ids": [1, 4, 7],
    "suggested_new_tags": ["automotive parts", "manufacturing"],
    "matched_company_type_ids": [2, 3]
}'''

        # Format the prompt for bulk processing
        prompt = f"""Based on this company information, provide enriched data in JSON format:

Company Name: {customer['name']}
Description: {customer['description'] if customer['description'] else 'Not provided'}
Website: {customer['website'] if customer['website'] else 'Not provided'}

Available Industry Tags:
{format_tags_for_prompt(available_tags)}

Available Company Types:
{format_types_for_prompt(company_types)}

Instructions:
- Match ONLY to existing tags when a close match exists
- Suggest new tags ONLY if no similar existing tag is available
- For broad industry categories, prefer existing general tags over specific new ones
- DO NOT suggest variations of existing tags

Return ONLY a JSON object with these exact fields:
- estimated_revenue (number in GBP, convert from other currencies if needed)
- country_code (string, ISO alpha-2)
- matched_tag_ids (array of existing tag IDs that match)
- suggested_new_tags (array of strings for new tag suggestions)
- matched_company_type_ids (array of existing company type IDs that match)

Example response:
{example}"""

        logging.debug(f"Sending prompt for customer {customer['id']}: {prompt}")

        # Initialize the OpenAI client
        client = OpenAI()

        # Create the chat completion using the new format
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "You are a business data analyst. Return only valid JSON. Revenue should be a number, not string. Country code must be ISO alpha-2 format (two uppercase letters, e.g., 'US', 'GB'). Do not include any explanation or markdown formatting."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.2
        )

        # Get the content from the new response format
        content = response.choices[0].message.content.strip()
        logging.debug(f"Response content for customer {customer['id']}: {content}")

        # Try to parse the JSON
        try:
            enrichment_data = json.loads(content)
            logging.debug(f"Parsed JSON for customer {customer['id']}: {enrichment_data}")
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON for customer {customer['id']}, content: {content}")
            raise

        validate_bulk_enrichment_data(enrichment_data)
        return enrichment_data

    except json.JSONDecodeError as e:
        logging.error(f"JSON parsing error for customer {customer['id']}: {str(e)}")
        raise
    except Exception as e:
        logging.error(f"OpenAI API error for customer {customer['id']}: {str(e)}")
        raise

def format_tags_for_prompt(tags):
    """Format tags for the OpenAI prompt"""
    return "\n".join([f"ID: {tag['id']} - {tag['name']}" for tag in tags])


def format_types_for_prompt(types):
    """Format company types for the OpenAI prompt"""
    return "\n".join([f"ID: {t['id']} - {t['name']}" for t in types])


def validate_enrichment_data(data, available_tags):
    """Validate the AI-generated enrichment data against available tags"""
    required_fields = ['estimated_revenue', 'country_code', 'suggested_tag_ids',
                       'suggested_company_type_ids']  # updated field names

    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    if not isinstance(data['estimated_revenue'], (int, float)):
        raise ValueError("Revenue must be a number")

    if not isinstance(data['country_code'], str) or len(data['country_code']) != 2:
        raise ValueError("Invalid country code format")

    if 'fleet_size' in data:
        if not isinstance(data['fleet_size'], int):
            raise ValueError("Fleet size must be an integer if provided")

    # Validate tag IDs
    available_tag_ids = {tag['id'] for tag in available_tags}
    for tag_id in data['suggested_tag_ids']:
        if tag_id not in available_tag_ids:
            raise ValueError(f"Invalid tag ID: {tag_id}")



def get_enrichment_progress():
    """Get current enrichment progress stats"""
    db = get_db_connection()
    try:
        return db.execute('''
            SELECT 
                (SELECT COUNT(*) FROM customers) as total_customers,
                COUNT(*) as processed,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                MAX(last_attempt) as last_update
            FROM customer_enrichment_status
        ''').fetchone()
    finally:
        db.close()


def log_enrichment_error(customer_id, error_message):
    """Log enrichment errors to database"""
    db = get_db_connection()
    try:
        db.execute('''
            INSERT INTO customer_enrichment_status 
            (customer_id, status, error_message, last_attempt, attempts)
            VALUES (?, 'failed', ?, ?, 1)
            ON CONFLICT(customer_id) 
            DO UPDATE SET 
                status = 'failed',
                error_message = ?,
                last_attempt = ?,
                attempts = attempts + 1
        ''', (customer_id, error_message, datetime.now(),
              error_message, datetime.now()))
        db.commit()
    finally:
        db.close()


def update_enrichment_status(customer_id, status, error_message=None):
    """Update the status of enrichment for a customer"""
    db = get_db_connection()
    try:
        db.execute('''
            INSERT INTO customer_enrichment_status 
                (customer_id, status, last_attempt, error_message, attempts)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(customer_id) 
            DO UPDATE SET 
                status = ?,
                last_attempt = ?,
                error_message = ?,
                attempts = attempts + 1
        ''', (
            customer_id, status, datetime.now(), error_message,
            status, datetime.now(), error_message
        ))
        db.commit()
    finally:
        db.close()


def store_tag_suggestions(customer_id, suggested_tags):
    """Store new tag suggestions"""
    db = get_db_connection()
    try:
        for tag in suggested_tags:
            # Check if this suggestion already exists
            existing = db.execute('''
                SELECT id, frequency 
                FROM ai_tag_suggestions 
                WHERE customer_id = ? AND suggested_tag = ? AND reviewed = 0
            ''', (customer_id, tag)).fetchone()

            if existing:
                # Update frequency if it exists
                db.execute('''
                    UPDATE ai_tag_suggestions 
                    SET frequency = frequency + 1
                    WHERE id = ?
                ''', (existing['id'],))
            else:
                # Insert new suggestion if it doesn't
                db.execute('''
                    INSERT INTO ai_tag_suggestions 
                        (customer_id, suggested_tag, frequency, reviewed, created_at)
                    VALUES (?, ?, 1, 0, ?)
                ''', (customer_id, tag, datetime.now()))

        db.commit()
    finally:
        db.close()

def apply_enrichment_updates(customer_id, enrichment_data):
    """Apply the enrichment updates to the database"""
    db = get_db_connection()
    try:
        # Begin transaction
        db.execute('BEGIN TRANSACTION')

        # Update main customer data
        db.execute('''
            UPDATE customers 
            SET estimated_revenue = ?,
                country = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            enrichment_data['estimated_revenue'],
            enrichment_data['country_code'],
            datetime.now(),
            customer_id
        ))

        # Update company types
        db.execute('DELETE FROM customer_company_types WHERE customer_id = ?', (customer_id,))
        for type_id in enrichment_data['matched_company_type_ids']:
            db.execute('''
                INSERT INTO customer_company_types (customer_id, company_type_id)
                VALUES (?, ?)
            ''', (customer_id, type_id))

        # Update industry tags
        db.execute('DELETE FROM customer_industry_tags WHERE customer_id = ?', (customer_id,))
        for tag_id in enrichment_data['matched_tag_ids']:
            db.execute('''
                INSERT INTO customer_industry_tags (customer_id, tag_id)
                VALUES (?, ?)
            ''', (customer_id, tag_id))

        db.execute('COMMIT')
    except Exception as e:
        db.execute('ROLLBACK')
        raise
    finally:
        db.close()


def start_bulk_enrichment(batch_size=20):
    """Main controller for bulk enrichment process"""
    db = get_db_connection()
    try:
        # Get pending customers
        customers = db.execute('''
            SELECT c.id, c.name, c.description, c.website 
            FROM customers c
            LEFT JOIN customer_enrichment_status ces ON c.id = ces.customer_id
            WHERE ces.status IS NULL 
               OR ces.status = 'pending'
            ORDER BY c.id
            LIMIT ?
        ''', (batch_size,)).fetchall()

        # Get all existing tags and company types once
        tags = db.execute('SELECT id, tag as name, description FROM industry_tags').fetchall()
        company_types = db.execute('SELECT id, type as name FROM company_types').fetchall()

        for customer in customers:
            try:
                # Update status to processing
                update_enrichment_status(customer['id'], 'processing')

                # Process customer
                enrichment_data = bulk_enrich_customer_data(customer, tags, company_types)

                # Apply updates
                apply_enrichment_updates(customer['id'], enrichment_data)

                # Store new tag suggestions
                store_tag_suggestions(customer['id'], enrichment_data['suggested_new_tags'])

                # Mark as completed
                update_enrichment_status(customer['id'], 'completed')

                # Small delay to respect API rate limits
                time.sleep(1)

            except Exception as e:
                logging.error(f"Error processing customer {customer['id']}: {str(e)}")
                update_enrichment_status(customer['id'], 'failed', error_message=str(e))
                continue

    finally:
        db.close()


# =============================================================================
# Perplexity-based Customer Enrichment (with live data)
# =============================================================================

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

def enrich_customer_with_perplexity(customer, available_tags, company_types):
    """
    Enrich customer data using Perplexity AI with live web search.

    This replaces the OpenAI-based enrichment to get accurate, up-to-date information.
    Returns enrichment data including company type detection and MRO scoring.
    """
    try:
        client = OpenAI(
            api_key=PERPLEXITY_API_KEY,
            base_url="https://api.perplexity.ai"
        )

        # Build context about the customer
        company_name = customer.get('name', '')
        description = customer.get('description', '') or ''
        website = customer.get('website', '') or ''

        # Format available tags and company types for the prompt (include IDs)
        tags_list = ", ".join([f"{t['id']}:{t['name']}" for t in available_tags[:30]])  # Limit to avoid token overflow
        types_list = ", ".join([f"{t['id']}:{t['name']}" for t in company_types])

        system_message = f"""You are a business intelligence analyst specializing in the aviation industry.
Research the company and provide accurate, factual information based on current web data.

Available industry tags to choose from: {tags_list}
Available company types: {types_list}

IMPORTANT DEFINITIONS:
- Operator: Airlines, helicopter operators, charter companies, corporate flight departments, air ambulance/HEMS, cargo operators - companies that FLY aircraft
- MRO: Maintenance, Repair & Overhaul facilities that SERVICE aircraft/engines/components for other companies
- OEM: Original Equipment Manufacturers like Boeing, Airbus, Pratt & Whitney, Rolls-Royce
- Distributor: Parts distributors, brokers, aviation supply chain companies
- Parts Manufacturer: PMA manufacturers, hardware manufacturers, component makers (not OEMs)

A company can be MULTIPLE types (e.g., CHC Helicopter is both Operator and MRO via Heli-One).

For MRO companies, calculate an MRO_SCORE (1-100) based on:
- Facility size and locations (global presence = higher score)
- Range of capabilities (engines, airframes, components, avionics)
- Certifications (EASA, FAA, CAAC, etc.)
- Major customer base
- Specializations

For Operators, estimate FLEET_SIZE as the number of aircraft they operate.

Return ONLY a valid JSON object with these fields:
{{
  "estimated_revenue": <number in GBP (British Pounds), convert from other currencies if needed>,
  "country_code": "<ISO alpha-2 country code of headquarters>",
  "company_type_ids": [<array of NUMERIC IDs from the company types list above, e.g. [1, 2]>],
  "matched_tag_ids": [<array of NUMERIC IDs from the industry tags list above, e.g. [5, 12]>],
  "suggested_new_tags": [<array of new tag names if no existing tags match>],
  "fleet_size": <integer or null if not an operator>,
  "mro_score": <integer 1-100 or null if not an MRO>,
  "summary": "<brief 1-2 sentence company description>"
}}

IMPORTANT: For company_type_ids and matched_tag_ids, use the NUMERIC ID numbers from the lists provided (the number before the colon), NOT the names.

Be accurate. If you cannot find reliable information, use null rather than guessing."""

        user_prompt = f"""Research this aviation industry company and provide enrichment data:

Company Name: {company_name}
Description: {description}
Website: {website}

Find accurate information about their business type, revenue, fleet size (if operator), MRO capabilities (if MRO), and relevant industry classifications."""

        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        response_text = response.choices[0].message.content.strip()

        # Remove thinking tags if present (Perplexity sometimes includes these)
        response_text = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL)
        response_text = response_text.strip()

        # Extract JSON from response (handle markdown code blocks)
        if '```json' in response_text:
            response_text = response_text.split('```json')[1].split('```')[0].strip()
        elif '```' in response_text:
            response_text = response_text.split('```')[1].split('```')[0].strip()

        enrichment_data = json.loads(response_text)

        # Validate and normalize the response
        validated_data = {
            'estimated_revenue': enrichment_data.get('estimated_revenue'),
            'country_code': enrichment_data.get('country_code', '').upper()[:2] if enrichment_data.get('country_code') else None,
            'matched_company_type_ids': enrichment_data.get('company_type_ids', []),
            'matched_tag_ids': enrichment_data.get('matched_tag_ids', []),
            'suggested_new_tags': enrichment_data.get('suggested_new_tags', []),
            'fleet_size': enrichment_data.get('fleet_size'),
            'mro_score': enrichment_data.get('mro_score'),
            'summary': enrichment_data.get('summary', '')
        }

        # Ensure revenue is a number
        if validated_data['estimated_revenue']:
            try:
                validated_data['estimated_revenue'] = float(validated_data['estimated_revenue'])
            except (ValueError, TypeError):
                validated_data['estimated_revenue'] = None

        # Ensure fleet_size and mro_score are integers
        for field in ['fleet_size', 'mro_score']:
            if validated_data[field] is not None:
                try:
                    validated_data[field] = int(validated_data[field])
                except (ValueError, TypeError):
                    validated_data[field] = None

        # Convert company type names to IDs if AI returned names instead of IDs
        type_name_to_id = {t['name'].lower(): t['id'] for t in company_types}
        resolved_type_ids = []
        for item in validated_data['matched_company_type_ids']:
            if isinstance(item, int):
                resolved_type_ids.append(item)
            elif isinstance(item, str):
                # Try to find by name (case-insensitive)
                type_id = type_name_to_id.get(item.lower())
                if type_id:
                    resolved_type_ids.append(type_id)
        validated_data['matched_company_type_ids'] = resolved_type_ids

        # Convert tag names to IDs if AI returned names instead of IDs
        tag_name_to_id = {t['name'].lower(): t['id'] for t in available_tags}
        resolved_tag_ids = []
        new_tags = list(validated_data.get('suggested_new_tags', []))
        for item in validated_data['matched_tag_ids']:
            if isinstance(item, int):
                resolved_tag_ids.append(item)
            elif isinstance(item, str):
                # Try to find by name (case-insensitive)
                tag_id = tag_name_to_id.get(item.lower())
                if tag_id:
                    resolved_tag_ids.append(tag_id)
                else:
                    # Tag doesn't exist, add to suggested new tags
                    if item not in new_tags:
                        new_tags.append(item)
        validated_data['matched_tag_ids'] = resolved_tag_ids
        validated_data['suggested_new_tags'] = new_tags

        logging.info(f"Perplexity enrichment for {company_name}: {validated_data}")
        return validated_data

    except json.JSONDecodeError as e:
        logging.error(f"JSON parsing error in Perplexity enrichment for {customer.get('name')}: {e}")
        logging.error(f"Response was: {response_text[:500] if 'response_text' in locals() else 'N/A'}")
        raise ValueError(f"Failed to parse Perplexity response: {e}")
    except Exception as e:
        error_str = str(e)
        # Check for authentication errors
        if '401' in error_str or 'Authorization' in error_str or 'Unauthorized' in error_str:
            logging.error(f"Perplexity API authentication failed - API key may be invalid or expired")
            raise ValueError("Perplexity API key is invalid or expired. Please update the API key.")
        logging.error(f"Perplexity enrichment error for {customer.get('name')}: {e}")
        raise


def apply_perplexity_enrichment(customer_id, enrichment_data):
    """Apply Perplexity enrichment results to the customer record"""
    from db import execute as db_execute, _using_postgres

    db = get_db_connection()
    try:
        # Build update query dynamically based on available data
        updates = []
        params = []

        # Use %s for Postgres, ? for SQLite
        placeholder = '%s' if _using_postgres() else '?'

        if enrichment_data.get('estimated_revenue') is not None:
            updates.append(f"estimated_revenue = {placeholder}")
            params.append(enrichment_data['estimated_revenue'])

        if enrichment_data.get('country_code'):
            updates.append(f"country = {placeholder}")
            params.append(enrichment_data['country_code'])

        if enrichment_data.get('fleet_size') is not None:
            updates.append(f"fleet_size = {placeholder}")
            params.append(enrichment_data['fleet_size'])

        if enrichment_data.get('mro_score') is not None:
            updates.append(f"mro_score = {placeholder}")
            params.append(enrichment_data['mro_score'])

        if enrichment_data.get('summary'):
            # Only update description if it's currently empty
            updates.append(f"description = COALESCE(NULLIF(description, ''), {placeholder})")
            params.append(enrichment_data['summary'])

        if updates:
            params.append(customer_id)
            query = f"UPDATE customers SET {', '.join(updates)} WHERE id = {placeholder}"
            db.execute(query, params)

        # Update company types
        if enrichment_data.get('matched_company_type_ids'):
            # Clear existing and add new
            db.execute(f'DELETE FROM customer_company_types WHERE customer_id = {placeholder}', (customer_id,))
            for type_id in enrichment_data['matched_company_type_ids']:
                try:
                    db.execute(
                        f'INSERT INTO customer_company_types (customer_id, company_type_id) VALUES ({placeholder}, {placeholder})',
                        (customer_id, type_id)
                    )
                except Exception:
                    pass  # Ignore duplicates or invalid IDs

        # Update industry tags
        if enrichment_data.get('matched_tag_ids'):
            db.execute(f'DELETE FROM customer_industry_tags WHERE customer_id = {placeholder}', (customer_id,))
            for tag_id in enrichment_data['matched_tag_ids']:
                try:
                    db.execute(
                        f'INSERT INTO customer_industry_tags (customer_id, tag_id) VALUES ({placeholder}, {placeholder})',
                        (customer_id, tag_id)
                    )
                except Exception:
                    pass

        db.commit()

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def start_perplexity_enrichment(batch_size=20):
    """Main controller for Perplexity-based bulk enrichment process"""
    db = get_db_connection()
    try:
        # Get pending customers
        customers = db.execute('''
            SELECT c.id, c.name, c.description, c.website
            FROM customers c
            LEFT JOIN customer_enrichment_status ces ON c.id = ces.customer_id
            WHERE ces.status IS NULL
               OR ces.status = 'pending'
            ORDER BY c.id
            LIMIT ?
        ''', (batch_size,)).fetchall()

        # Get all existing tags and company types once
        tags = db.execute('SELECT id, tag as name, description FROM industry_tags').fetchall()
        company_types = db.execute('SELECT id, type as name FROM company_types').fetchall()

        # Convert to list of dicts
        tags = [dict(t) for t in tags]
        company_types = [dict(ct) for ct in company_types]

        for customer in customers:
            customer_dict = dict(customer)
            try:
                # Update status to processing
                update_enrichment_status(customer_dict['id'], 'processing')

                # Process customer with Perplexity
                enrichment_data = enrich_customer_with_perplexity(customer_dict, tags, company_types)

                # Apply updates
                apply_perplexity_enrichment(customer_dict['id'], enrichment_data)

                # Store new tag suggestions
                if enrichment_data.get('suggested_new_tags'):
                    store_tag_suggestions(customer_dict['id'], enrichment_data['suggested_new_tags'])

                # Mark as completed
                update_enrichment_status(customer_dict['id'], 'completed')

                # Delay to respect API rate limits (Perplexity is more rate-limited)
                time.sleep(2)

            except Exception as e:
                logging.error(f"Error processing customer {customer_dict['id']}: {str(e)}")
                update_enrichment_status(customer_dict['id'], 'failed', error_message=str(e))
                continue

    finally:
        db.close()


def extract_quote_info_with_examples(text, examples):
    system_message = """You are an assistant tasked with extracting specific information from text. 
    For each item, provide the information in this exact format:

    Part number: <part>
    Quantity: <quantity>
    Price: <price>
    Lead time: <weeks>
    Manufacturer: <manufacturer>

    Rules:
    - Each item should be separated by a blank line
    - Labels must match exactly as shown above
    - Handle European number formats (using commas as decimal separators)
    - Remove any currency symbols from prices
    - For lead times, convert to weeks and only return the number
    - If any field is not found, still include its label with empty value
    - Convert any European decimal formatting to standard (dots instead of commas) 
    """

    user_content = ""
    if examples and examples[0].get('part'):
        user_content = "Use these patterns to identify information:\n"
        for field, value in examples[0].items():
            if value and field != 'raw_text':
                user_content += f"{field}: {value}\n"
        user_content += "\n"

    user_content += f"Extract similar information from this text:\n\n{text}"

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_content}
        ],
        max_tokens=1500,
        temperature=0.2,
    )

    extracted_data = response.choices[0].message.content.strip()
    print("Raw OpenAI response:", extracted_data)
    result = parse_extracted_quote_info(extracted_data)
    print("Parsed result:", result)
    return result


def get_top_customers_for_news(salesperson_id, limit=10):
    """Get top customers for news checking - by consolidated sales value"""
    try:
        db = get_db_connection()

        # Get consolidated customer data using your existing helper functions
        consolidated_customers = get_consolidated_customer_ids(salesperson_id)

        if not consolidated_customers:
            return []

        top_customers = []

        # Calculate total sales for each consolidated customer group
        for main_customer_id, customer_info in consolidated_customers.items():
            # Get all orders for this customer group (main + associated)
            orders = get_consolidated_customer_orders(customer_info['all_customer_ids'])

            # Calculate total value
            total_sales_value = sum(order['total_value'] for order in orders)

            if total_sales_value > 0:  # Only include customers with sales
                # Get the main customer details
                customer_query = """
                    SELECT 
                        c.id,
                        c.name,
                        c.description,
                        c.country,
                        c.website,
                        c.fleet_size
                    FROM customers c
                    WHERE c.id = ?
                """

                customer_row = db.execute(customer_query, (main_customer_id,)).fetchone()

                if customer_row:
                    customer_dict = dict(customer_row)
                    customer_dict['total_sales_value'] = total_sales_value
                    customer_dict['associated_customer_count'] = len(customer_info['all_customer_ids']) - 1
                    top_customers.append(customer_dict)

        # Sort by total sales value and limit results
        top_customers.sort(key=lambda x: x['total_sales_value'], reverse=True)

        return top_customers[:limit]

    except Exception as e:
        print(f"Error in get_top_customers_for_news: {str(e)}")
        return []
    finally:
        if 'db' in locals():
            db.close()


def get_watched_customers_for_news(salesperson_id, limit=25):
    """Get watched customers for news checking."""
    db = get_db_connection()
    try:
        rows = db.execute(
            """
            SELECT id, name, description, country, website, fleet_size
            FROM customers
            WHERE watch = TRUE AND salesperson_id = ?
            ORDER BY name
            LIMIT ?
            """,
            (salesperson_id, limit)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error in get_watched_customers_for_news: {str(e)}")
        return []
    finally:
        db.close()


def fetch_customer_news_perplexity(customer):
    """Fetch news for a customer using Perplexity API"""

    # Only use environment variables - don't use current_app.config in streaming context
    perplexity_key = os.getenv("PERPLEXITY_API_KEY")


    print(f"DEBUG: Perplexity key found: {perplexity_key is not None}")
    if perplexity_key:
        print(f"DEBUG: Key starts with: {perplexity_key[:10]}...")

    if not perplexity_key:
        print("ERROR: PERPLEXITY_API_KEY not found in environment variables")
        return None

    try:
        client = OpenAI(
            api_key=perplexity_key,
            base_url="https://api.perplexity.ai"
        )
        print("DEBUG: OpenAI client created successfully")

        # Create focused search query using available customer data
        company_name = customer['name']
        description = customer.get('description', '')
        country_context = customer.get('country', '')
        website = customer.get('website', '')
        fleet_size = customer.get('fleet_size')

        # Build search context from available information
        search_context = f"{company_name}"

        # Add context clues from customer data
        if description:
            search_context += f" {description}"
        if country_context:
            search_context += f" {country_context}"
        if fleet_size and fleet_size > 0:
            search_context += f" fleet vehicles"

        # Try to infer industry from company name and description
        industry_hints = []
        if any(word in company_name.lower() for word in ['logistics', 'transport', 'freight', 'shipping']):
            industry_hints.append("logistics and transportation")
        if any(word in company_name.lower() for word in ['construction', 'building', 'infrastructure']):
            industry_hints.append("construction")
        if any(word in company_name.lower() for word in ['manufacturing', 'industrial', 'factory']):
            industry_hints.append("manufacturing")
        if fleet_size and fleet_size > 20:
            industry_hints.append("fleet operations")

        industry_context = " ".join(industry_hints) if industry_hints else "commercial business"

        system_message = f"""You are a business intelligence analyst. Find recent news and developments about {company_name}, which appears to be involved in {industry_context}.

Focus on:
- Financial results and business performance
- New contracts, partnerships, or major deals
- Strategic initiatives, expansions, or investments
- Industry trends affecting the company
- Management changes or corporate announcements
- Market position or competitive developments
- Fleet expansion or equipment purchases (if applicable)

Provide 1-3 most relevant and recent news items from the last 3 months. For each item include:
- Headline (concise, business-focused)
- 2-sentence summary 
- Source and publication date
- Business impact assessment (High/Medium/Low)

Exclude:
- General industry news not specific to the company
- Stock price movements only
- Irrelevant companies with similar names
- News older than 3 months"""

        user_prompt = f"Find recent business news for: {search_context}"

        print(f"DEBUG: About to call Perplexity API for {company_name}")

        response = client.chat.completions.create(
            model="sonar-reasoning-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        print(f"DEBUG: Perplexity API call successful for {company_name}")
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"Perplexity API error for {customer['name']}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def process_customer_news_chatgpt(customer, raw_news_text):
    """Process raw Perplexity response with ChatGPT for consistent formatting"""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    system_message = """CRITICAL: Return ONLY valid JSON. No explanations, no markdown, no extra text.

    Extract news items and return this EXACT format:
    {"news_items": [{"headline": "...", "summary": "...", "source": "...", "published_date": "YYYY-MM-DD", "business_impact": "High|Medium|Low", "relevance_score": 1-10, "customer_name": "..."}]}

    If no relevant news: {"news_items": []}

    JSON ONLY. NO OTHER TEXT."""

    user_prompt = f"""Customer: {customer['name']}
Industry: {customer.get('industry', 'Not specified')}

Raw news text to process:
{raw_news_text}

Format into structured JSON with relevance scoring."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        response_text = response.choices[0].message.content.strip()

        # ADD THESE DEBUG LINES:
        print(f"\n=== ChatGPT Raw Response for {customer['name']} ===")
        print(f"Response length: {len(response_text)}")
        print(f"First 200 chars: {response_text[:200]}")
        print(f"Last 200 chars: {response_text[-200:]}")
        print("=== End Raw Response ===\n")

        # Parse JSON response
        news_data = json.loads(response_text)

        # Add customer_id to each news item
        if 'news_items' in news_data:
            for item in news_data['news_items']:
                item['customer_id'] = customer['id']
                item['customer_name'] = customer['name']

        return news_data

    except json.JSONDecodeError as e:
        print(f"JSON parsing error for {customer['name']}: {str(e)}")
        return None
    except Exception as e:
        print(f"ChatGPT processing error for {customer['name']}: {str(e)}")
        return None


def get_cache_directory():
    """Get or create cache directory"""
    if current_app:
        cache_dir = os.path.join(current_app.instance_path, 'cache')
    else:
        cache_dir = os.path.join(os.getcwd(), 'cache')

    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_cache_key(salesperson_id):
    """Generate cache key for salesperson"""
    return f"customer_news_{salesperson_id}"


def cache_news(cache_key, data):
    """Cache news data"""
    try:
        cache_dir = get_cache_directory()
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")

        cache_data = {
            'cache_date': datetime.now().strftime('%Y-%m-%d'),
            'cached_at': datetime.now().isoformat(),
            'data': data
        }

        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)

        print(f"Cached news data for {cache_key}")

    except Exception as e:
        print(f"Cache write error for {cache_key}: {str(e)}")


def get_cached_news(cache_key):
    """Get cached news if still valid (same day)"""
    try:
        cache_dir = get_cache_directory()
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")

        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)

            # Check if cache is from today
            cache_date = cached_data.get('cache_date')
            today = datetime.now().strftime('%Y-%m-%d')

            if cache_date == today:
                print(f"Using cached news for {cache_key}")
                # Include the cached_at timestamp in the returned data
                result = cached_data['data'].copy()
                result['last_checked'] = cached_data.get('cached_at')  # Add this line
                return result
            else:
                print(f"Cache expired for {cache_key} (cache: {cache_date}, today: {today})")
                return None

        return None

    except Exception as e:
        print(f"Cache read error for {cache_key}: {str(e)}")
        return None

def cleanup_old_cache_files():
    """Remove cache files older than 7 days"""
    try:
        cache_dir = get_cache_directory()
        cutoff_date = datetime.now() - timedelta(days=7)

        for filename in os.listdir(cache_dir):
            if filename.startswith('customer_news_') and filename.endswith('.json'):
                file_path = os.path.join(cache_dir, filename)
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))

                if file_time < cutoff_date:
                    os.remove(file_path)
                    print(f"Removed old cache file: {filename}")

    except Exception as e:
        print(f"Cache cleanup error: {str(e)}")


# ============================================================================
# NEWS DEDUPLICATION FUNCTIONS
# ============================================================================

import hashlib


def compute_news_hash(headline):
    """Compute a hash of the headline for exact duplicate detection.
    
    Normalizes the headline (lowercase, strip whitespace, remove punctuation)
    before hashing to catch near-exact duplicates.
    """
    import re
    # Normalize: lowercase, remove punctuation, collapse whitespace
    normalized = headline.lower().strip()
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def get_sent_news_hashes(salesperson_id, customer_id, days_back=90):
    """Get hashes of news items sent to this salesperson for this customer.
    
    Returns a set of news_hash values for quick lookup.
    """
    db = get_db_connection()
    try:
        rows = db.execute("""
            SELECT news_hash
            FROM sent_customer_news
            WHERE salesperson_id = ?
            AND customer_id = ?
            AND sent_at > NOW() - INTERVAL '%s days'
        """.replace('?', '%s'), (salesperson_id, customer_id, days_back)).fetchall()
        return {row['news_hash'] for row in rows}
    except Exception as e:
        print(f"Error getting sent news hashes: {str(e)}")
        return set()
    finally:
        db.close()


def get_recent_headlines_for_customer(salesperson_id, customer_id, limit=20):
    """Get recent headlines sent for this customer to use in AI comparison.
    
    Returns list of headline strings.
    """
    db = get_db_connection()
    try:
        rows = db.execute("""
            SELECT headline, sent_at
            FROM sent_customer_news
            WHERE salesperson_id = %s
            AND customer_id = %s
            ORDER BY sent_at DESC
            LIMIT %s
        """, (salesperson_id, customer_id, limit)).fetchall()
        return [row['headline'] for row in rows]
    except Exception as e:
        print(f"Error getting recent headlines: {str(e)}")
        return []
    finally:
        db.close()


def store_sent_news_items(salesperson_id, news_items):
    """Store news items that have been sent to prevent future duplicates.
    
    Args:
        salesperson_id: ID of the salesperson
        news_items: List of news item dicts with headline, summary, source, 
                   published_date, customer_id
    """
    if not news_items:
        return
    
    db = get_db_connection()
    try:
        for item in news_items:
            news_hash = compute_news_hash(item.get('headline', ''))
            customer_id = item.get('customer_id')
            
            if not customer_id:
                continue
            
            # Parse published_date if it's a string
            pub_date = item.get('published_date')
            if isinstance(pub_date, str) and pub_date:
                try:
                    pub_date = datetime.strptime(pub_date, '%Y-%m-%d').date()
                except:
                    pub_date = None
            
            # Use INSERT ... ON CONFLICT to handle duplicates gracefully
            db.execute("""
                INSERT INTO sent_customer_news 
                    (salesperson_id, customer_id, news_hash, headline, summary, source, published_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (salesperson_id, customer_id, news_hash) DO NOTHING
            """, (
                salesperson_id,
                customer_id,
                news_hash,
                item.get('headline', '')[:500],  # Truncate if too long
                item.get('summary', '')[:1000] if item.get('summary') else None,
                item.get('source', '')[:255] if item.get('source') else None,
                pub_date
            ))
        
        db.commit()
        print(f"Stored {len(news_items)} news items for salesperson {salesperson_id}")
    except Exception as e:
        print(f"Error storing sent news items: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def check_semantic_duplicates_ai(new_headlines, previous_headlines, customer_name):
    """Use AI to check if new headlines are semantically similar to previous ones.
    
    Args:
        new_headlines: List of new headline strings to check
        previous_headlines: List of previously sent headline strings
        customer_name: Name of the customer for context
        
    Returns:
        List of booleans, True if the headline at that index is genuinely NEW
    """
    if not new_headlines:
        return []
    
    if not previous_headlines:
        # No previous headlines to compare against - all are new
        return [True] * len(new_headlines)
    
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    # Format previous headlines
    prev_list = "\n".join([f"{i+1}. {h}" for i, h in enumerate(previous_headlines)])
    
    # Format new headlines
    new_list = "\n".join([f"{i+1}. {h}" for i, h in enumerate(new_headlines)])
    
    system_message = """You are a news analyst. Determine which new headlines represent genuinely NEW stories vs duplicates/follow-ups of previously reported news.

A headline is a DUPLICATE if:
- It reports the same event/announcement as a previous headline
- It's a follow-up or update to a previously reported story
- It covers the same deal/contract/partnership/result

A headline is NEW if:
- It covers a completely different event or topic
- It's about a genuinely new development

Return ONLY a JSON array of booleans, where true = NEW story, false = DUPLICATE.
Example: [true, false, true]

The array MUST have exactly the same number of elements as new headlines provided."""

    user_prompt = f"""Customer: {customer_name}

Previously sent headlines (last 60 days):
{prev_list}

New headlines to evaluate:
{new_list}

For each new headline, is it genuinely NEW (true) or a duplicate/follow-up (false)?
Return only the JSON array."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Using mini for cost efficiency
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )
        
        response_text = response.choices[0].message.content.strip()
        
        # Parse the JSON array
        result = json.loads(response_text)
        
        # Validate length matches
        if len(result) != len(new_headlines):
            print(f"Warning: AI returned {len(result)} results for {len(new_headlines)} headlines")
            # Pad with True (assume new) if too short, or truncate if too long
            if len(result) < len(new_headlines):
                result.extend([True] * (len(new_headlines) - len(result)))
            else:
                result = result[:len(new_headlines)]
        
        return result
        
    except Exception as e:
        print(f"Error in AI semantic duplicate check: {str(e)}")
        # On error, assume all are new to avoid losing news
        return [True] * len(new_headlines)


def filter_duplicate_news(salesperson_id, news_items):
    """Filter out news items that have already been sent.
    
    Uses a two-pass approach:
    1. Hash-based filtering for exact/near-exact duplicates (free)
    2. AI-based filtering for semantic duplicates (smart)
    
    Args:
        salesperson_id: ID of the salesperson
        news_items: List of news item dicts
        
    Returns:
        List of news items that are genuinely new
    """
    if not news_items:
        return []
    
    # Group news items by customer
    by_customer = {}
    for item in news_items:
        cid = item.get('customer_id')
        if cid:
            if cid not in by_customer:
                by_customer[cid] = []
            by_customer[cid].append(item)
    
    filtered_items = []
    
    for customer_id, items in by_customer.items():
        # PASS 1: Hash-based filtering
        sent_hashes = get_sent_news_hashes(salesperson_id, customer_id)
        
        hash_filtered = []
        for item in items:
            news_hash = compute_news_hash(item.get('headline', ''))
            if news_hash not in sent_hashes:
                hash_filtered.append(item)
            else:
                print(f"Hash-filtered duplicate: {item.get('headline', '')[:50]}...")
        
        if not hash_filtered:
            continue
        
        # PASS 2: AI semantic filtering
        previous_headlines = get_recent_headlines_for_customer(
            salesperson_id, customer_id, limit=20
        )
        
        if previous_headlines:
            new_headlines = [item.get('headline', '') for item in hash_filtered]
            customer_name = hash_filtered[0].get('customer_name', 'Unknown')
            
            is_new = check_semantic_duplicates_ai(
                new_headlines, previous_headlines, customer_name
            )
            
            for item, is_genuinely_new in zip(hash_filtered, is_new):
                if is_genuinely_new:
                    filtered_items.append(item)
                else:
                    print(f"AI-filtered semantic duplicate: {item.get('headline', '')[:50]}...")
        else:
            # No previous headlines, all items pass
            filtered_items.extend(hash_filtered)
    
    print(f"News deduplication: {len(news_items)} -> {len(filtered_items)} items")
    return filtered_items


def cleanup_old_sent_news(days_to_keep=180):
    """Remove sent news records older than specified days to prevent table bloat."""
    db = get_db_connection()
    try:
        result = db.execute("""
            DELETE FROM sent_customer_news
            WHERE sent_at < NOW() - INTERVAL '%s days'
        """, (days_to_keep,))
        db.commit()
        print(f"Cleaned up old sent news records")
    except Exception as e:
        print(f"Error cleaning up old sent news: {str(e)}")
    finally:
        db.close()
