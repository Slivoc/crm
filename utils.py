

# In a new file, e.g., /home/tom/crm/utils.py
def generate_breadcrumbs(path):
    breadcrumbs = []
    parts = path.split('/')
    for i in range(1, len(parts) + 1):
        breadcrumb_path = '/'.join(parts[:i])
        breadcrumbs.append((parts[i-1], breadcrumb_path))
    return breadcrumbs



def extract_focus_from_text(text):
    """Extract focus/industry from text"""
    # Check for focus label
    focus_match = re.search(r'(?:Main )?Focus:?\s*([^,\n]+)', text, re.IGNORECASE)
    if focus_match:
        return focus_match.group(1).strip()

    # Check for industry label
    industry_match = re.search(r'Industry:?\s*([^,\n]+)', text, re.IGNORECASE)
    if industry_match:
        return industry_match.group(1).strip()

    # Just get the text after the last comma if nothing else found
    last_comma = text.rfind(',')
    if last_comma != -1:
        return text[last_comma + 1:].strip()

    return ""

import os
import re
from openai import OpenAI


def process_perplexity_response(raw_perplexity_response, tag_name, tag_description, country_distribution):
    """
    Post-process the Perplexity API response to ensure it matches the expected format.

    Args:
        raw_perplexity_response (str): The raw analysis text from Perplexity API
        tag_name (str): The industry tag name
        tag_description (str): The description of the tag
        country_distribution (dict): Current customer distribution by country

    Returns:
        dict: Contains formatted analysis, suggestions, and extracted data
    """
    # Initialize OpenAI client using your existing API key
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY")
    )

    # Create a prompt for the post-processing
    system_message = """
    You are a formatter for market analysis data. You receive raw analysis text about an industry 
    sector that might have inconsistent formatting. Your job is to process it into a consistent format.

    The output MUST have these exact sections with markdown headings:

    ## Market Analysis
    [Well-structured analysis of how current customer distribution compares to the industry landscape]

    ## Growth Opportunities
    [List of company suggestions in the exact format below]

    For each company suggestion, format it EXACTLY like this:

    **Company Name:** [Company Name]
    **Website:** <a href="[URL]" target="_blank">[Website]</a>
    **Country:** [Country]
    **Annual Revenue:** [Revenue in EUR]
    **Main Focus:** [Main industry focus]

    IMPORTANT:
    1. Never use tables
    2. Format website links as raw HTML as shown above, not as Markdown
    3. Keep all company suggestions and don't remove any
    4. Don't add citations or references
    5. Maintain all the factual information from the original text
    """

    # Format the distribution data for the context
    distribution_text = "\n".join([
        f"- {country}: {data['percentage']:.1f}% ({data['customer_count']} customers)"
        for country, data in country_distribution.items()
    ])

    user_prompt = f"""
    Industry: {tag_name}
    Description: {tag_description or 'Not provided'}

    Current Customer Distribution:
    {distribution_text}

    Here is the raw analysis text that needs formatting:

    {raw_perplexity_response}

    Format the analysis according to the requirements, maintaining all the company suggestions.
    """

    # Call OpenAI API for formatting
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # or your preferred model
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )

        formatted_analysis = response.choices[0].message.content.strip()

        # Extract structured company data
        companies = extract_company_data(formatted_analysis)

        return {
            "success": True,
            "analysis": formatted_analysis,
            "suggestions": companies
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"OpenAI formatting error: {str(e)}",
            "analysis": raw_perplexity_response  # Return original as fallback
        }


def extract_company_data(formatted_text):
    """
    Extract structured company data from the formatted text
    """
    companies = []

    # Find all company sections - pattern matches from one company name to before the next
    company_pattern = r"\*\*Company Name:\*\* (.*?)(?=\n\n\*\*Company Name:|$)"
    company_matches = re.finditer(company_pattern, formatted_text, re.DOTALL)

    for match in company_matches:
        company_text = match.group(0).strip()

        # Extract individual fields
        company_name = extract_field(company_text, "Company Name")
        website = extract_website(company_text)
        country = extract_field(company_text, "Country")
        revenue_str = extract_field(company_text, "Annual Revenue")
        main_focus = extract_field(company_text, "Main Focus")

        # Process revenue to numeric format
        revenue = parse_revenue(revenue_str)

        # Get country code if available
        country_code = ""

        companies.append({
            "company_name": company_name,
            "website": website,
            "country": country,
            "country_code": country_code,
            "estimated_revenue": revenue,
            "product_focus": main_focus,
            "justification": f"Leading company in {main_focus} based in {country}"
        })

    return companies


def extract_field(text, field_name):
    """Extract a field value from the text"""
    pattern = rf"\*\*{field_name}:\*\* (.*?)(?:\n|$)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def extract_website(text):
    """Extract website URL from the HTML link"""
    pattern = r'<a href="([^"]+)"'
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def format_with_openai(raw_analysis, tag_info, country_breakdown):
    print("formatting with OpenAIIIIII")
    """
    Format raw Perplexity analysis using OpenAI to ensure consistent structure.

    Args:
        raw_analysis (str): Raw analysis text from Perplexity
        tag_info (dict): Information about the industry tag
        country_breakdown (dict): Current customer distribution by country

    Returns:
        str: Consistently formatted analysis text
    """
    import os
    from openai import OpenAI

    # Initialize OpenAI client
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY")
    )

    # Create distribution text for context
    distribution_text = "\n".join([
        f"- {country}: {data['percentage']:.1f}% ({data['customer_count']} customers)"
        for country, data in country_breakdown.items()
    ])

    # System message with explicit formatting instructions
    system_message = """
    You are a formatter for market analysis data. Your job is to process the raw analysis
    into a consistent, well-structured format that strictly follows these requirements:

    1. The output MUST have exactly these two main sections with markdown headings:
       "## Market Analysis" and "## Growth Opportunities"

    2. The Market Analysis section should analyze how well the current customer distribution
       matches the real-world landscape for this industry.

    3. The Growth Opportunities section must list company suggestions in EXACTLY this format:

       **Company Name:** [Company Name]
       **Website:** <a href="[URL]" target="_blank">[Website]</a>
       **Country:** [Country]
       **Annual Revenue:** [Revenue in EUR]
       **Main Focus:** [Main industry focus]

       Leave a blank line between each company listing.

    CRITICAL REQUIREMENTS:
    - Format ALL website links as raw HTML, not as Markdown links
    - Do NOT use tables under any circumstances
    - Preserve all company suggestions and factual information from the original text
    - Do NOT add any additional sections beyond the two required ones
    - Format Annual Revenue values consistently in EUR
    - Do NOT add citations or references
    """

    # User message with context and raw analysis
    user_message = f"""
    Industry: {tag_info['tag']}
    Description: {tag_info['description'] or 'Not provided'}

    Current Customer Distribution:
    {distribution_text}

    Here is the raw analysis that needs formatting:

    {raw_analysis}

    Please format this analysis according to the requirements, preserving all company suggestions.
    """

    try:
        # Call OpenAI API for formatting
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Using 3.5 for cost efficiency since it's just formatting
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            temperature=0.1
        )

        formatted_analysis = response.choices[0].message.content.strip()

        # Ensure the formatted analysis has both required sections
        if "## Market Analysis" not in formatted_analysis or "## Growth Opportunities" not in formatted_analysis:
            # If the formatting didn't produce the right structure, try again with a more direct approach
            fallback_system_message = """
            Format this market analysis text with EXACTLY these headings and order:

            ## Market Analysis
            [Keep all analysis content here]

            ## Growth Opportunities
            [Keep all company suggestions here, each formatted EXACTLY as:

            **Company Name:** [Name]
            **Website:** <a href="[URL]" target="_blank">[Website]</a>
            **Country:** [Country]
            **Annual Revenue:** [Revenue in EUR]
            **Main Focus:** [Focus]

            ]

            Do NOT add any other sections. Do NOT use tables.
            """

            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": fallback_system_message},
                    {"role": "user", "content": raw_analysis}
                ],
                temperature=0.1
            )

            formatted_analysis = response.choices[0].message.content.strip()

        return formatted_analysis

    except Exception as e:
        import logging
        logging.error(f"OpenAI formatting error: {str(e)}")
        # Fall back to the raw analysis if OpenAI fails
        return raw_analysis




def extract_website_url(text):
    """Extract website URL from HTML link or text"""
    # Try to find HTML link
    href_match = re.search(r'<a href="([^"]+)"', text)
    if href_match:
        return href_match.group(1)

    # Try to find URL in text
    url_match = re.search(r'https?://[^\s<>"]+', text)
    if url_match:
        return url_match.group(0)

    # Look for "Website:" text
    website_match = re.search(r'\*\*Website:\*\*\s*(.*?)(?:\n|$)', text, re.DOTALL)
    if website_match:
        website_text = website_match.group(1).strip()
        # Extract URL if present
        url_in_text = re.search(r'https?://[^\s<>"]+', website_text)
        if url_in_text:
            return url_in_text.group(0)

    return ""


def extract_url_from_text(text):
    """Extract URL from any text"""
    # Check for HTML link
    href_match = re.search(r'<a href="([^"]+)"', text)
    if href_match:
        return href_match.group(1)

    # Check for plain URL
    url_match = re.search(r'https?://[^\s<>"]+', text)
    if url_match:
        return url_match.group(0)

    return ""


def extract_country_from_text(text):
    """Extract country name from text"""
    # First check for "Country:" label
    country_match = re.search(r'Country:?\s*([^,\n]+)', text, re.IGNORECASE)
    if country_match:
        return country_match.group(1).strip()

    # Look for common European country names
    european_countries = [
        "Germany", "France", "United Kingdom", "UK", "Italy", "Spain", "Netherlands",
        "Belgium", "Sweden", "Switzerland", "Austria", "Poland", "Denmark", "Finland",
        "Norway", "Ireland", "Portugal", "Greece", "Czech Republic", "Romania"
    ]

    for country in european_countries:
        if re.search(r'\b' + re.escape(country) + r'\b', text):
            return country

    return ""


def extract_revenue_from_text(text):
    """Extract and parse revenue from text"""
    # Check for revenue label
    revenue_match = re.search(r'Revenue:?\s*([^,\n]+)', text, re.IGNORECASE)
    if revenue_match:
        return parse_revenue(revenue_match.group(1).strip())

    # Look for currency symbols and numbers
    currency_match = re.search(r'[€$£]\s*[\d,.]+(?:\s*(?:million|billion|m|b))?', text, re.IGNORECASE)
    if currency_match:
        return parse_revenue(currency_match.group(0))

    return 0



def parse_revenue(revenue_str):
    """
    Parse revenue string to a numeric value, handling various formats.

    Args:
        revenue_str (str): Revenue string to parse

    Returns:
        float: Revenue value in EUR
    """
    if not revenue_str:
        return 0

    # Remove currency symbols, commas and extra text
    cleaned = re.sub(r'[€$£¥]|EUR|USD|GBP|JPY|euro[s]?', '', revenue_str, flags=re.IGNORECASE)
    cleaned = cleaned.replace(',', '').strip()

    # Check for million/billion/thousand abbreviations
    multipliers = {
        'million': 1000000,
        'm': 1000000,
        'billion': 1000000000,
        'b': 1000000000,
        'k': 1000,
        'thousand': 1000
    }

    for text, multiplier in multipliers.items():
        if text in cleaned.lower():
            cleaned = cleaned.lower().replace(text, '').strip()
            try:
                return float(cleaned) * multiplier
            except ValueError:
                pass

    # Try direct conversion
    try:
        return float(cleaned)
    except ValueError:
        # Last resort - extract any numbers
        numbers = re.findall(r'\d+(?:\.\d+)?', cleaned)
        if numbers:
            try:
                return float(numbers[0])
            except ValueError:
                pass

    return 0

def parse_ai_suggestions(analysis_text):
    print("parsing AI suggestionsssss")
    """
    Extract structured company data from the formatted analysis text.
    Handles various formats and edge cases to ensure reliable extraction.

    Args:
        analysis_text (str): The formatted analysis text

    Returns:
        list: List of dictionaries containing company information
    """
    import re

    # List to store extracted companies
    companies = []

    # Step 1: Try to find the Growth Opportunities section
    section_match = re.search(r'##\s*Growth\s*Opportunities(.*?)(?=##|$)',
                              analysis_text, re.DOTALL | re.IGNORECASE)

    if section_match:
        section_text = section_match.group(1).strip()
    else:
        # Fallback: use the entire text if section not found
        section_text = analysis_text

    # Step 2: Extract company blocks - look for Company Name pattern
    # Pattern to match: "**Company Name:** [name]" and everything until the next company or end
    company_blocks = re.finditer(
        r'\*\*Company Name:\*\*\s*(.*?)(?=\n\s*\*\*Company Name:\*\*|$)',
        section_text,
        re.DOTALL
    )

    # Process each company block
    for block in company_blocks:
        company_text = block.group(0)
        company_name = block.group(1).strip()

        # Skip if company name is empty
        if not company_name:
            continue

        # Extract other fields
        website_url = extract_website_url(company_text)
        country = extract_field(company_text, "Country")
        annual_revenue = extract_field(company_text, "Annual Revenue")
        main_focus = extract_field(company_text, "Main Focus")

        # Parse revenue to numeric value
        revenue = parse_revenue(annual_revenue)

        # Add company to results
        companies.append({
            "company_name": company_name,
            "website": website_url,
            "country": country,
            "estimated_revenue": revenue,
            "product_focus": main_focus,
            "justification": f"Leading {main_focus} company in {country}"
        })

    # If no companies found with the primary method, try alternative patterns
    if not companies:
        # Alternative pattern 1: Look for bold text followed by details
        alt_companies = re.finditer(
            r'\*\*(.*?)\*\*\s*(?:[-:]|is|,)(.*?)(?=\n\s*\*\*|$)',
            section_text,
            re.DOTALL
        )

        for match in alt_companies:
            company_name = match.group(1).strip()
            details_text = match.group(2).strip()

            # Try to extract information from the details
            website_url = extract_url_from_text(details_text)
            country = extract_country_from_text(details_text)
            revenue = extract_revenue_from_text(details_text)
            focus = extract_focus_from_text(details_text)

            companies.append({
                "company_name": company_name,
                "website": website_url,
                "country": country,
                "estimated_revenue": revenue,
                "product_focus": focus,
                "justification": f"Company in {country or 'Europe'}"
            })

    # If still no companies found, try a line-by-line approach as last resort
    if not companies:
        lines = section_text.split('\n')
        current_company = {}

        for line in lines:
            line = line.strip()

            # Skip empty lines
            if not line:
                continue

            # Check if this line contains a company name (bold text)
            name_match = re.search(r'\*\*(.*?)\*\*', line)
            if name_match and ":" not in name_match.group(1):
                # If we have a partial company from previous lines, add it
                if current_company.get('company_name'):
                    companies.append(current_company)

                # Start a new company
                current_company = {"company_name": name_match.group(1).strip()}
                continue

            # Try to extract fields from the line
            if "website" in line.lower() or "http" in line.lower():
                current_company["website"] = extract_url_from_text(line)
            elif "country" in line.lower():
                current_company["country"] = extract_country_from_text(line)
            elif "revenue" in line.lower():
                current_company["estimated_revenue"] = extract_revenue_from_text(line)
            elif "focus" in line.lower() or "industry" in line.lower() or "product" in line.lower():
                current_company["product_focus"] = extract_focus_from_text(line)

        # Add the last company if we have one
        if current_company.get('company_name'):
            companies.append(current_company)

    # Fill in any missing fields with defaults
    for company in companies:
        if "website" not in company:
            company["website"] = ""
        if "country" not in company:
            company["country"] = "Europe"
        if "estimated_revenue" not in company:
            company["estimated_revenue"] = 0
        if "product_focus" not in company:
            company["product_focus"] = ""
        if "justification" not in company:
            focus = company.get("product_focus", "industry")
            country = company.get("country", "Europe")
            company["justification"] = f"Leading {focus} company in {country}"

    return companies
