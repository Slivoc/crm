from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
import logging
import re
import os
from openai import OpenAI
from db import execute as db_execute, db_cursor
from models import (get_countries_by_continent, get_all_deepdives, get_customer_links_for_deepdive, add_customer_link_to_deepdive, remove_customer_link_from_deepdive,
                    get_deepdive_by_id, create_deepdive, update_deepdive, delete_deepdive,
                    get_all_tags_flat, get_all_countries, get_country_customers_by_tag,
                    match_companies_to_customers, get_curated_customers_for_deepdive, add_customer_to_deepdive, remove_customer_from_deepdive, update_customer_notes_in_deepdive, search_customers_for_deepdive, get_country_name)
geo_deepdive_bp = Blueprint('geo_deepdive', __name__)


def _using_postgres():
    """Detect whether DATABASE_URL indicates a Postgres connection."""
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    """Translate SQLite '?' placeholders to Postgres '%s' when needed."""
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    """Run a query on the provided cursor with placeholder translation."""
    cur.execute(_prepare_query(query), params or [])
    return cur


@geo_deepdive_bp.route('/geographic-deepdives')
def list_deepdives():
    """List all geographic deep dives"""
    deepdives_raw = get_all_deepdives()

    # Convert row objects to dictionaries and add country names
    deepdives = []
    for deepdive_row in deepdives_raw:
        # Convert row to dict (SQLite row or Postgres dict-row)
        deepdive = dict(deepdive_row)
        # Add country name
        deepdive['country_name'] = get_country_name(deepdive['country'])
        deepdives.append(deepdive)

    # Add breadcrumbs
    breadcrumbs = [
        ('Geographic Deepdives', None)  # Current page
    ]

    return render_template('geo_deepdive_list.html', deepdives=deepdives, breadcrumbs=breadcrumbs)


def generate_deepdive_content_with_perplexity(country_code, tag_description):
    """Generate deep dive content using Perplexity AI with real-time search"""

    # Use the same Perplexity key as in your existing code
    perplexity_key = "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"

    if not perplexity_key:
        return None, "Perplexity API key not found"

    # Convert country code to full name for better AI search results
    country_name = get_country_name(country_code)

    try:
        client = OpenAI(
            api_key=perplexity_key,
            base_url="https://api.perplexity.ai"
        )

        system_message = f"""You are an aviation industry analyst. Search the web for current information about operations in {country_name}, particularly focusing on {tag_description}.

Create a comprehensive market analysis based on your web search results. I've given an example using Rotary Wing area in Austria. Don't reference or compare to this. Structure your response using this EXACT format:

# {country_name} – Rotary-Wing Landscape
# Austria – Rotary-Wing Landscape

## What's Big Here
Austria is overwhelmingly a HEMS-driven market — **ÖAMTC** and **ARA** cover most of the country with a large EC135/145 fleet. Utility and heavy-lift operators are significant but secondary. Police and training fleets exist but are smaller.

---

## HEMS / Air Ambulance
This is by far the dominant sector (majority of helicopter flying hours in Austria).

- **ÖAMTC Christophorus** – national HEMS network, ~31 H135s, bases across the country; maintenance via HeliAir (Helikopter Air Maintenance).  
- **ARA Flugrettung (DRF group)** – regional HEMS in Tyrol/Carinthia, also SAR hoist ops.

---

## SAR / Rescue
SAR is not standalone but integrated with HEMS fleets (hoist-equipped aircraft, often dual-role).

- **ÖAMTC** (selected bases hoist-equipped)  
- **ARA / DRF** (hoist-equipped fleet for mountain rescue)

---

## Utility / Heavy-Lift
Strong in Alpine construction, ski infrastructure, and sling-load work.

- **Heli Austria** – mixed fleet (H125 to Super Puma); also charter and firefighting.  
- **Wucher Helicopter** – Alpine utility and heavy-lift; has in-house Part-145 MRO.

---

## Police Aviation
Medium-scale fleet, focused on EC135 for surveillance and support.

- **BMI Flugpolizei** – operates from Vienna + regional bases; centralized state operator.

---

## Military Support
Relevant mainly at the periphery; procurement goes via defence, but fleet coexists at the same airports.

- **Bundesheer** – Alouette III (being phased out) and Black Hawks; less accessible for commercial supply.

---

## Training / Flight Schools
Smaller fleet but steady demand for consumables due to high hours.

- **RotorSky** – major Austrian helicopter training provider (Linz HQ, multiple bases).

---

## Main MRO / Part-145 Shops
Most serious maintenance is concentrated in just two organizations.

- **HeliAir** – ÖAMTC's MRO, Innsbruck + Wiener Neustadt East; Part-145, CAMO, Part-21.  
- **Wucher Maintenance** – Part-145, Ludesch HQ.

---

## Key Hubs / Bases
Activity is clustered in Alpine regions plus Vienna area.

- **Innsbruck (LOWI)** – ÖAMTC/HeliAir + Wucher.  
- **St. Johann im Pongau** – Heli Austria HQ.  
- **Ludesch (LOIG)** – Wucher HQ.  
- **Wiener Neustadt East (LOAN)** – HeliAir maintenance base.  
- **Zell am See (LOWZ)** – Wucher + HEMS.  
- **Bad Vöslau (LOAV)** – Heli Austria Flight Academy.

---

## Buying Notes
- Biggest demand: **H135/H145** (HEMS & police) and **H125** (utility).  
- Procurement: centralized (**ÖAMTC, Heli Austria, Wucher**); public sector for police.  
- Contacts to target: **Stores/Logistics, Maintenance Planners, Base Engineers.**"""

        user_prompt = f"Search for current information about helicopter industry operators in {country_name}, particularly those involved in {tag_description}. Find specific companies, their fleet details, aircraft types, key bases, and procurement processes. Include recent market developments and opportunities for aviation suppliers."

        # Use sonar-pro for better search capabilities and add search parameters
        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            # Add Perplexity-specific parameters for better search
            extra_body={
                "return_citations": True,
                "search_recency_filter": "month",  # Focus on recent information
                "search_domain_filter": ["aviation-industry.com", "verticalmag.com", "ainonline.com",
                                         "flightglobal.com"]  # Aviation-focused domains
            }
        )

        return response.choices[0].message.content.strip(), None

    except Exception as e:
        return None, f"Error generating content: {str(e)}"



@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>/edit')
def edit_deepdive(deepdive_id):
    """Show form to edit deep dive"""
    deepdive = get_deepdive_by_id(deepdive_id)

    if not deepdive:
        flash('Deep dive not found', 'danger')
        return redirect(url_for('geo_deepdive.list_deepdives'))

    # Add breadcrumbs
    breadcrumbs = [
        ('Geographic Deepdives', url_for('geo_deepdive.list_deepdives')),
        (deepdive['title'], url_for('geo_deepdive.view_deepdive', deepdive_id=deepdive_id)),
        ('Edit', None)  # Current page
    ]

    return render_template('geo_deepdive_edit.html', deepdive=deepdive, breadcrumbs=breadcrumbs)



@geo_deepdive_bp.route('/api/generate-deepdive-content', methods=['POST'])
def generate_deepdive_content_api():
    """API endpoint to generate deep dive content using AI"""
    data = request.get_json()
    country_code = data.get('country', '').strip()
    tag_id = data.get('tag_id')

    # Convert tag_id to int
    try:
        tag_id = int(tag_id) if tag_id else None
    except (ValueError, TypeError):
        tag_id = None

    if not country_code or not tag_id:
        return jsonify({'error': 'Country and tag are required'}), 400

    # Get tag description
    tags = get_all_tags_flat()
    tag_description = next((tag['description'] for tag in tags if tag['id'] == tag_id), f"Tag {tag_id}")

    # Generate content using Perplexity (pass country_code, function will convert to name)
    content, error = generate_deepdive_content_with_perplexity(country_code, tag_description)

    if error:
        return jsonify({'error': error}), 500

    if not content:
        return jsonify({'error': 'No content generated'}), 500

    # Auto-generate title using full country name
    country_name = get_country_name(country_code)
    title = f"{country_name} - {tag_description}"

    return jsonify({
        'success': True,
        'content': content,
        'title': title
    })


@geo_deepdive_bp.route('/geographic-deepdives/new')
def new_deepdive():
    """Show form to create new deep dive"""
    tags = get_all_tags_flat()
    countries = get_all_countries()

    # Add breadcrumbs
    breadcrumbs = [
        ('Geographic Deepdives', url_for('geo_deepdive.list_deepdives')),
        ('New Deepdive', None)  # Current page
    ]

    return render_template('geo_deepdive_new.html', tags=tags, countries=countries, breadcrumbs=breadcrumbs)


@geo_deepdive_bp.route('/geographic-deepdives/create', methods=['POST'])
def create_deepdive_route():
    """Create a new geographic deep dive"""
    country_code = request.form.get('country', '').strip()
    tag_id = request.form.get('tag_id', type=int)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()

    # Validation
    if not country_code:
        flash('Country is required', 'danger')
        return redirect(url_for('geo_deepdive.new_deepdive'))

    if not tag_id:
        flash('Tag is required', 'danger')
        return redirect(url_for('geo_deepdive.new_deepdive'))

    if not content:
        flash('Content is required', 'danger')
        return redirect(url_for('geo_deepdive.new_deepdive'))

    # Auto-generate title if not provided (using full country name)
    if not title:
        tags = get_all_tags_flat()
        tag_description = next((tag['description'] for tag in tags if tag['id'] == tag_id), f"Tag {tag_id}")
        country_name = get_country_name(country_code)
        title = f"{country_name} - {tag_description}"

    # Create the deep dive (store country_code in database)
    deepdive_id, error = create_deepdive(country_code, tag_id, title, content)

    if error:
        flash(f'Error creating deep dive: {error}', 'danger')
        return redirect(url_for('geo_deepdive.new_deepdive'))

    flash('Deep dive created successfully', 'success')
    return redirect(url_for('geo_deepdive.view_deepdive', deepdive_id=deepdive_id))


@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>')
def view_deepdive(deepdive_id):
    """View a specific deep dive with curated customer list"""
    print(f"DEBUG: view_deepdive called with ID: {deepdive_id}")

    deepdive = get_deepdive_by_id(deepdive_id)

    if not deepdive:
        flash('Deep dive not found', 'danger')
        return redirect(url_for('geo_deepdive.list_deepdives'))

    # Get customer links for this deepdive
    customer_links = get_customer_links_for_deepdive(deepdive_id)
    print(f"DEBUG: Customer links: {customer_links}")

    # Process content to add customer tags
    processed_content = process_deepdive_content_with_customer_tags(deepdive['content'], customer_links)
    print(f"DEBUG: Processed content exists: {bool(processed_content)}")

    # Create a new deepdive dict with processed content
    deepdive_with_tags = dict(deepdive)
    deepdive_with_tags['processed_content'] = processed_content

    # Get curated customers for this deepdive
    curated_customers = get_curated_customers_for_deepdive(deepdive_id)

    # Get all customers for this country/tag for adding to curated list
    country_customers = get_country_customers_by_tag(deepdive['country'], deepdive['tag_id'])

    # Add breadcrumbs
    breadcrumbs = [
        ('Geographic Deepdives', url_for('geo_deepdive.list_deepdives')),
        (deepdive['title'], None)  # Current page
    ]

    return render_template('geo_deepdive_view.html',
                           deepdive=deepdive_with_tags,
                           curated_customers=curated_customers,
                           country_customers=country_customers,
                           customer_links=customer_links,
                           breadcrumbs=breadcrumbs)



@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>/update', methods=['POST'])
def update_deepdive_route(deepdive_id):
    """Update an existing deep dive"""
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()

    if not title:
        flash('Title is required', 'danger')
        return redirect(url_for('geo_deepdive.edit_deepdive', deepdive_id=deepdive_id))

    if not content:
        flash('Content is required', 'danger')
        return redirect(url_for('geo_deepdive.edit_deepdive', deepdive_id=deepdive_id))

    if update_deepdive(deepdive_id, title, content):
        flash('Deep dive updated successfully', 'success')
        return redirect(url_for('geo_deepdive.view_deepdive', deepdive_id=deepdive_id))
    else:
        flash('Error updating deep dive', 'danger')
        return redirect(url_for('geo_deepdive.edit_deepdive', deepdive_id=deepdive_id))



@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>/delete', methods=['POST'])
def delete_deepdive_route(deepdive_id):
    """Delete a deep dive"""
    if delete_deepdive(deepdive_id):
        flash('Deep dive deleted successfully', 'success')
    else:
        flash('Error deleting deep dive', 'danger')

    return redirect(url_for('geo_deepdive.list_deepdives'))


# Add new API endpoints
@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/customers', methods=['POST'])
def add_customer_to_deepdive_api(deepdive_id):
    """Add a customer to the deepdive's curated list"""
    data = request.get_json()
    customer_id = data.get('customer_id')
    notes = data.get('notes', '')

    if not customer_id:
        return jsonify({'error': 'Customer ID is required'}), 400

    success, error = add_customer_to_deepdive(deepdive_id, customer_id, notes)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': error}), 400


@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/customers/<int:customer_id>', methods=['DELETE'])
def remove_customer_from_deepdive_api(deepdive_id, customer_id):
    """Remove a customer from the deepdive's curated list"""
    success = remove_customer_from_deepdive(deepdive_id, customer_id)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to remove customer'}), 400


@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/customers/<int:customer_id>/notes', methods=['PUT'])
def update_customer_notes_api(deepdive_id, customer_id):
    """Update notes for a customer in the deepdive"""
    data = request.get_json()
    notes = data.get('notes', '')

    success = update_customer_notes_in_deepdive(deepdive_id, customer_id, notes)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to update notes'}), 400


@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/search-customers')
def search_customers_api(deepdive_id):
    """Search for customers to add to the deepdive"""
    search_term = request.args.get('q', '')

    if len(search_term) < 2:
        return jsonify({'customers': []})

    # Get deepdive info for filtering
    deepdive = get_deepdive_by_id(deepdive_id)
    if not deepdive:
        return jsonify({'error': 'Deepdive not found'}), 404

    customers = search_customers_for_deepdive(
        search_term,
        country=deepdive['country'],
        tag_id=deepdive['tag_id']
    )

    return jsonify({'customers': customers})


@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/improve-content', methods=['POST'])
def improve_deepdive_content_api(deepdive_id):
    """API endpoint to improve existing deep dive content with AI suggestions"""
    data = request.get_json()
    improvement_request = data.get('request', '').strip()

    if not improvement_request:
        return jsonify({'error': 'Improvement request is required'}), 400

    # Get the existing deepdive
    deepdive = get_deepdive_by_id(deepdive_id)
    if not deepdive:
        return jsonify({'error': 'Deep dive not found'}), 404

    # Get country name and tag description for context
    country_name = get_country_name(deepdive['country'])
    tags = get_all_tags_flat()
    tag_description = next((tag['description'] for tag in tags if tag['id'] == deepdive['tag_id']),
                           f"Tag {deepdive['tag_id']}")

    # Generate improved content
    improved_content, error = generate_improved_deepdive_content(
        deepdive['content'],
        country_name,
        tag_description,
        improvement_request
    )

    if error:
        return jsonify({'error': error}), 500

    if not improved_content:
        return jsonify({'error': 'No improved content generated'}), 500

    return jsonify({
        'success': True,
        'improved_content': improved_content,
        'original_content': deepdive['content']
    })


def generate_improved_deepdive_content(existing_content, country_name, tag_description, improvement_request):
    """Generate improved deep dive content using Perplexity AI based on user request"""

    perplexity_key = "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"

    if not perplexity_key:
        return None, "Perplexity API key not found"

    try:
        client = OpenAI(
            api_key=perplexity_key,
            base_url="https://api.perplexity.ai"
        )

        system_message = f"""You are an aviation industry analyst improving an existing market analysis. 

You have been asked to enhance the content for {country_name} in the {tag_description} sector.

INSTRUCTIONS:
1. Review the existing content carefully
2. Search for current information about the specific improvement request
3. Integrate new findings into the existing structure
4. Maintain the same format and style as the original
5. Add new sections if needed, or enhance existing ones
6. Preserve all valuable existing information
7. Ensure the enhanced content is comprehensive and up-to-date

Return the COMPLETE improved content (not just the additions). Maintain the same markdown structure and formatting style as the original.

Original content structure should be preserved:
- Keep the main heading format: # {country_name} – [Sector] Landscape
- Keep section headers with ---
- Maintain the detailed, industry-specific writing style
- Keep company names in **bold**
- Preserve specific details like fleet sizes, aircraft types, base locations

If adding new sections, follow the existing pattern and style."""

        user_prompt = f"""Here is the existing market analysis for {country_name} in {tag_description}:

EXISTING CONTENT:
{existing_content}

IMPROVEMENT REQUEST:
{improvement_request}

Please search for current information related to this request and provide an enhanced version of the complete content that addresses the user's request while preserving all existing valuable information."""

        response = client.chat.completions.create(
            model="sonar-pro",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=4000,  # Increased for longer content
            extra_body={
                "return_citations": True,
                "search_recency_filter": "month",
                "search_domain_filter": ["aviation-industry.com", "verticalmag.com", "ainonline.com",
                                         "flightglobal.com", "helicopterindustry.com", "rotor.org"]
            }
        )

        return response.choices[0].message.content.strip(), None

    except Exception as e:
        return None, f"Error improving content: {str(e)}"


@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>/improve')
def improve_deepdive_form(deepdive_id):
    """Show form to improve deep dive content"""
    deepdive = get_deepdive_by_id(deepdive_id)

    if not deepdive:
        flash('Deep dive not found', 'danger')
        return redirect(url_for('geo_deepdive.list_deepdives'))

    # Add breadcrumbs
    breadcrumbs = [
        {'name': 'Geographic Deepdives', 'url': url_for('geo_deepdive.list_deepdives')},
        {'name': deepdive['title'], 'url': url_for('geo_deepdive.view_deepdive', deepdive_id=deepdive_id)},
        {'name': 'Improve Content', 'url': None}  # Current page
    ]

    return render_template('geo_deepdive_improve.html', deepdive=deepdive, breadcrumbs=breadcrumbs)


@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>/apply-improvement', methods=['POST'])
def apply_improvement_route(deepdive_id):
    """Apply the improved content to the deep dive"""
    improved_content = request.form.get('improved_content', '').strip()

    if not improved_content:
        flash('Improved content is required', 'danger')
        return redirect(url_for('geo_deepdive.improve_deepdive_form', deepdive_id=deepdive_id))

    # Get current deepdive to preserve title
    deepdive = get_deepdive_by_id(deepdive_id)
    if not deepdive:
        flash('Deep dive not found', 'danger')
        return redirect(url_for('geo_deepdive.list_deepdives'))

    # Update with improved content (keep existing title)
    if update_deepdive(deepdive_id, deepdive['title'], improved_content):
        flash('Deep dive content improved successfully', 'success')
        return redirect(url_for('geo_deepdive.view_deepdive', deepdive_id=deepdive_id))
    else:
        flash('Error applying improved content', 'danger')
        return redirect(url_for('geo_deepdive.improve_deepdive_form', deepdive_id=deepdive_id))


@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/market-penetration')
def get_market_penetration_data(deepdive_id):
    """Get market penetration analytics by customer status and revenue for a specific deepdive"""

    print(f"DEBUG: Market penetration request for deepdive_id: {deepdive_id}")

    # Verify deepdive exists
    deepdive = get_deepdive_by_id(deepdive_id)
    if not deepdive:
        print(f"DEBUG: Deepdive {deepdive_id} not found")
        return jsonify({'error': 'Deepdive not found'}), 404

    print(f"DEBUG: Found deepdive: {dict(deepdive) if deepdive else None}")

    try:
        query = """
        SELECT 
            c.status_id,
            cs.status,
            c.estimated_revenue
        FROM deepdive_curated_customers dcc
        JOIN customers c ON dcc.customer_id = c.id
        LEFT JOIN customer_status cs ON c.status_id = cs.id
        WHERE dcc.deepdive_id = ?
        AND c.estimated_revenue IS NOT NULL
        AND c.status_id IS NOT NULL
        """

        with db_cursor() as cur:
            _execute_with_cursor(cur, query, (deepdive_id,))
            raw_results = cur.fetchall()

        status_groups = {}
        for row in raw_results:
            status_id = row['status_id']
            status_name = row['status'] or f"Status {status_id}"
            revenue = row['estimated_revenue']

            if status_id not in status_groups:
                status_groups[status_id] = {
                    'status_name': status_name,
                    'total_revenue': 0,
                    'customer_count': 0
                }

            status_groups[status_id]['total_revenue'] += revenue
            status_groups[status_id]['customer_count'] += 1

        total_revenue = sum(group['total_revenue'] for group in status_groups.values())

        penetration_data = []
        for status_id, group in status_groups.items():
            percentage = (group['total_revenue'] / total_revenue * 100) if total_revenue > 0 else 0
            penetration_data.append({
                'status': group['status_name'],
                'status_id': status_id,
                'revenue': group['total_revenue'],
                'customer_count': group['customer_count'],
                'percentage': round(percentage, 2)
            })

        penetration_data.sort(key=lambda x: x['status_id'])

        status_rows = db_execute("SELECT id, status FROM customer_status ORDER BY id", fetch='all') or []
        status_labels = {row['id']: row['status'] for row in status_rows}

        result = {
            'success': True,
            'deepdive_id': deepdive_id,
            'total_revenue': total_revenue,
            'penetration_data': penetration_data,
            'status_labels': status_labels
        }

        return jsonify(result)

    except Exception as e:
        logging.exception("Error fetching market penetration data")
        return jsonify({'error': f'Database error: {str(e)}'}), 500


def get_status_labels():
    """Get dynamic status labels from the database"""
    try:
        rows = db_execute("SELECT id, status FROM customer_status ORDER BY id", fetch='all') or []
        return {row['id']: row['status'] for row in rows}
    except Exception as e:
        logging.exception("Error getting status labels")
        return {}

@geo_deepdive_bp.route('/geographic-deepdives/<int:deepdive_id>/analytics')
def view_deepdive_analytics(deepdive_id):
    """View analytics page for a specific deep dive"""
    deepdive = get_deepdive_by_id(deepdive_id)

    if not deepdive:
        flash('Deep dive not found', 'danger')
        return redirect(url_for('geo_deepdive.list_deepdives'))

    # Add breadcrumbs
    breadcrumbs = [
        {'name': 'Geographic Deepdives', 'url': url_for('geo_deepdive.list_deepdives')},
        {'name': deepdive['title'], 'url': url_for('geo_deepdive.view_deepdive', deepdive_id=deepdive_id)},
        {'name': 'Analytics', 'url': None}  # Current page
    ]

    return render_template('geo_deepdive_analytics.html', deepdive=deepdive, breadcrumbs=breadcrumbs)


def process_deepdive_content_with_customer_tags(content, customer_links):
    """Process deepdive content to add customer tags for linked text"""
    import re

    if not customer_links:
        return content

    sorted_texts = sorted(customer_links.keys(), key=len, reverse=True)
    processed_content = content

    for linked_text in sorted_texts:
        customer_info = customer_links[linked_text]

        status_id = customer_info.get('status_id')
        status_text = customer_info.get('status') or (f"Status {status_id}" if status_id is not None else "Unknown status")
        status_slug = status_text.lower()

        status_badge_html = (
            f'<span class="badge badge-sm status-badge clickable"'
            f' data-status="{status_slug}"'
            f' data-status-id="{customer_info.get("status_id", "")}"'
            f' data-customer-id="{customer_info["customer_id"]}"'
            f' title="Click to advance status">{status_text}</span>'
        )
        # Create customer link - keep everything inline
        customer_tag = f'<a href="/customers/{customer_info["customer_id"]}" class="text-decoration-none">{linked_text}</a>{status_badge_html}'

        escaped_text = re.escape(linked_text)
        pattern = r'(?<!\w)' + escaped_text + r'(?!\w)'

        processed_content = re.sub(
            pattern,
            customer_tag,
            processed_content,
            flags=re.IGNORECASE
        )

    return processed_content

@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/customer-links', methods=['POST'])
def add_customer_link_api(deepdive_id):
    """Add a text-to-customer link"""
    data = request.get_json()
    customer_id = data.get('customer_id')
    linked_text = data.get('linked_text', '').strip()

    if not customer_id or not linked_text:
        return jsonify({'error': 'Customer ID and linked text are required'}), 400

    success, error = add_customer_link_to_deepdive(deepdive_id, customer_id, linked_text)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': error}), 400

@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/customer-links', methods=['DELETE'])
def remove_customer_link_api(deepdive_id):
    """Remove a text-to-customer link"""
    data = request.get_json()
    customer_id = data.get('customer_id')
    linked_text = data.get('linked_text', '').strip()

    if not customer_id or not linked_text:
        return jsonify({'error': 'Customer ID and linked text are required'}), 400

    success = remove_customer_link_from_deepdive(deepdive_id, customer_id, linked_text)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to remove customer link'}), 400


@geo_deepdive_bp.route('/api/geographic-deepdives/<int:deepdive_id>/customers/<int:customer_id>/quick-update',
                       methods=['PUT'])
def quick_update_customer_in_deepdive(deepdive_id, customer_id):
    """Quick update customer fields from deepdive view"""
    data = request.get_json()

    field = data.get('field')
    value = data.get('value')

    if not field:
        return jsonify({'error': 'Field is required'}), 400

    # Validate field
    allowed_fields = ['notes', 'fleet_size', 'estimated_revenue']
    if field not in allowed_fields:
        return jsonify({'error': 'Invalid field'}), 400

    try:
        with db_cursor(commit=True) as cur:
            if field == 'notes':
                _execute_with_cursor(
                    cur,
                    "UPDATE deepdive_curated_customers SET notes = ? WHERE deepdive_id = ? AND customer_id = ?",
                    (value, deepdive_id, customer_id),
                )
            elif field == 'fleet_size':
                parsed_value = int(value) if value else None
                _execute_with_cursor(
                    cur,
                    "UPDATE customers SET fleet_size = ? WHERE id = ?",
                    (parsed_value, customer_id),
                )
            elif field == 'estimated_revenue':
                parsed_value = float(value) if value else None
                _execute_with_cursor(
                    cur,
                    "UPDATE customers SET estimated_revenue = ? WHERE id = ?",
                    (parsed_value, customer_id),
                )

        return jsonify({'success': True})

    except Exception as e:
        logging.exception("Error quick-updating deepdive customer")
        return jsonify({'error': f'Database error: {str(e)}'}), 500
