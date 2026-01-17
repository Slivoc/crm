"""
Airbus Marketplace category suggestion and export functionality
"""
import logging
import random
import time
from openai import OpenAI
import json

logger = logging.getLogger(__name__)

# Initialize OpenAI client - assumes you have OPENAI_API_KEY in environment
client = OpenAI()

# All available marketplace categories
MARKETPLACE_CATEGORIES = [
    "Marketplace Categories/Hardware and Electrical/Bolts",
    "Marketplace Categories/Hardware and Electrical/Cables",
    "Marketplace Categories/Hardware and Electrical/Circular Connectors",
    "Marketplace Categories/Hardware and Electrical/Clamps",
    "Marketplace Categories/Hardware and Electrical/Clamps and Routing Supports",
    "Marketplace Categories/Hardware and Electrical/Collars",
    "Marketplace Categories/Hardware and Electrical/Electromechanical Devices",
    "Marketplace Categories/Hardware and Electrical/Fasteners (Blind & Panel)",
    "Marketplace Categories/Hardware and Electrical/Harness Protections",
    "Marketplace Categories/Hardware and Electrical/Hydraulic Fittings",
    "Marketplace Categories/Hardware and Electrical/Inserts",
    "Marketplace Categories/Hardware and Electrical/Lamps",
    "Marketplace Categories/Hardware and Electrical/Lightning Protection and Bonding",
    "Marketplace Categories/Hardware and Electrical/Lockbolts & Rivets",
    "Marketplace Categories/Hardware and Electrical/Miscellaneous",
    "Marketplace Categories/Hardware and Electrical/Nuts",
    "Marketplace Categories/Hardware and Electrical/Pins",
    "Marketplace Categories/Hardware and Electrical/Rectangular Connectors",
    "Marketplace Categories/Hardware and Electrical/Screws",
    "Marketplace Categories/Hardware and Electrical/Spings",
    "Marketplace Categories/Hardware and Electrical/Valves",
    "Marketplace Categories/Hardware and Electrical/Washers",
]

FALLBACK_CATEGORY = "Marketplace Categories/Hardware and Electrical/Miscellaneous"
FALLBACK_REASON_PARSE = "parse_error"
FALLBACK_REASON_API = "api_error"

MIN_REQUEST_INTERVAL_SECONDS = 0.6
MAX_RETRY_ATTEMPTS = 6
INITIAL_BACKOFF_SECONDS = 1.5
MAX_BACKOFF_SECONDS = 20.0
_last_request_time = 0.0


def _fallback_suggestion(reason):
    return {
        "category": FALLBACK_CATEGORY,
        "confidence": "low",
        "reasoning": reason,
    }


def _extract_json_object(raw_content):
    start = raw_content.find("{")
    end = raw_content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw_content[start:end + 1]


def _throttle_requests():
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
        time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _is_rate_limit_error(exc):
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    return "429" in str(exc) or "rate limit" in str(exc).lower()


def _request_with_retry(request_kwargs):
    backoff = INITIAL_BACKOFF_SECONDS
    last_error = None

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        _throttle_requests()
        try:
            try:
                return client.chat.completions.create(
                    **request_kwargs,
                    response_format={"type": "json_object"},
                )
            except TypeError:
                return client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            last_error = exc
            if _is_rate_limit_error(exc):
                sleep_for = min(backoff, MAX_BACKOFF_SECONDS)
                jitter = random.uniform(0.0, 0.3 * sleep_for)
                logger.warning(
                    "OpenAI rate limit hit (attempt %s/%s). Sleeping %.2fs.",
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                    sleep_for + jitter,
                )
                time.sleep(sleep_for + jitter)
                backoff *= 2
                continue
            if "response_format" in str(exc):
                logger.warning("response_format not supported, retrying without it.")
                return client.chat.completions.create(**request_kwargs)
            raise

    raise last_error


def _parse_category_response(raw_content):
    if not raw_content:
        return _fallback_suggestion(FALLBACK_REASON_PARSE)

    content = raw_content.strip()
    if content.startswith("```json"):
        content = content[7:-3].strip()
    elif content.startswith("```"):
        content = content[3:-3].strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        logger.debug("AI response parse failed. Raw (truncated): %s", raw_content[:1000])
        extracted = _extract_json_object(content)
        if extracted:
            try:
                result = json.loads(extracted)
            except json.JSONDecodeError:
                return _fallback_suggestion(FALLBACK_REASON_PARSE)
        else:
            return _fallback_suggestion(FALLBACK_REASON_PARSE)

    suggested_category = result.get("category")
    if suggested_category not in MARKETPLACE_CATEGORIES:
        logger.warning("AI suggested invalid category: %s", suggested_category)
        return _fallback_suggestion("invalid_category")

    confidence = result.get("confidence") or "low"
    reasoning = result.get("reasoning") or "no_reasoning"
    return {
        "category": suggested_category,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def suggest_marketplace_category(part_number, description="", additional_info=""):
    """
    Use OpenAI to suggest an Airbus Marketplace category for a part

    Args:
        part_number: The part number
        description: Part description (optional)
        additional_info: Any additional context (optional)

    Returns:
        dict with 'category' (best match) and 'confidence' (high/medium/low)
        Returns None if unable to determine
    """
    try:
        logger.info(f"Suggesting category for part: {part_number}")

        # Build context
        context_parts = [f"Part Number: {part_number}"]
        if description:
            context_parts.append(f"Description: {description}")
        if additional_info:
            context_parts.append(f"Additional Info: {additional_info}")

        context = "\n".join(context_parts)

        # Create category list for the prompt
        category_list = "\n".join([f"- {cat}" for cat in MARKETPLACE_CATEGORIES])

        request_kwargs = {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "system",
                    "content": f"""You are an expert in aviation hardware categorization for the Airbus Marketplace.

Given a part number and description, select the MOST APPROPRIATE category from this exact list:

{category_list}

Rules:
1. Output ONLY valid JSON with no markdown formatting
2. Choose ONE category from the list above - use the EXACT string
3. Provide a confidence level: "high", "medium", or "low"
4. If truly uncertain, use "Marketplace Categories/Hardware and Electrical/Miscellaneous"
5. Reasoning must be a single short sentence, max 12 words, no quotes
6. Common patterns:
   - Part numbers with "NAS", "AN", "MS" followed by numbers are usually hardware
   - "BOLT", "SCREW" in description → Bolts or Screws category
   - "NUT" → Nuts category
   - "WASHER" → Washers category
   - "RIVET" → Lockbolts & Rivets
   - "CLAMP" → Clamps
   - "CONNECTOR", "CONN" → Connectors (Circular or Rectangular)
   - "VALVE" → Valves
   - "FITTING" → Hydraulic Fittings
   - "LAMP", "LIGHT" → Lamps
   - "PIN" → Pins
   - "INSERT" → Inserts
   - "COLLAR" → Collars
   - "SPRING" → Spings (note: typo in original category list)

Output format:
{{
    "category": "Marketplace Categories/Hardware and Electrical/CategoryName",
    "confidence": "high|medium|low",
    "reasoning": "Brief explanation of why this category was chosen"
}}"""
                },
                {
                    "role": "user",
                    "content": f"""Categorize this part:

{context}

Return the most appropriate category from the list provided."""
                }
            ],
            "max_tokens": 500,
            "temperature": 0.2,
        }

        response = _request_with_retry(request_kwargs)

        raw_content = response.choices[0].message.content.strip()
        logger.debug("Raw AI response: %s", raw_content[:1000])

        result = _parse_category_response(raw_content)
        logger.info(
            "Suggested category: %s (confidence: %s)",
            result.get("category"),
            result.get("confidence", "unknown"),
        )
        return result

    except Exception as e:
        logger.exception(f"Error suggesting marketplace category: {e}")
        return _fallback_suggestion(FALLBACK_REASON_API)


def suggest_categories_batch(parts_list):
    """
    Suggest categories for multiple parts at once

    Args:
        parts_list: List of dicts with 'part_number', 'description', 'additional_info'

    Returns:
        List of dicts with part info and suggested category
    """
    results = []

    for part in parts_list:
        part_number = part.get('part_number', '')
        description = part.get('description', '')
        additional_info = part.get('additional_info', '')

        suggestion = suggest_marketplace_category(part_number, description, additional_info)
        if not suggestion:
            suggestion = _fallback_suggestion(FALLBACK_REASON_API)

        result = {
            'part_number': part_number,
            'suggested_category': suggestion.get('category') if suggestion else None,
            'confidence': suggestion.get('confidence') if suggestion else None,
            'reasoning': suggestion.get('reasoning') if suggestion else None,
        }
        results.append(result)

    return results


def get_available_categories():
    """Return list of all available marketplace categories"""
    return MARKETPLACE_CATEGORIES.copy()
