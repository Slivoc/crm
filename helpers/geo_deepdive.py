import re


def deepdive_summary_text(content, limit=280):
    """Extract a short plain-text overview from an AI-authored Markdown deep dive."""
    content = str(content or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = content.split('\n')
    preferred_heading = re.compile(
        r"^#{1,6}\s*(?:what(?:'|’)s big here|overview|market summary|executive summary)\s*$",
        re.IGNORECASE,
    )
    start = next((index + 1 for index, line in enumerate(lines) if preferred_heading.match(line.strip())), 0)
    candidates = lines[start:] if start else lines
    summary_lines = []
    for line in candidates:
        stripped = line.strip()
        if not stripped or stripped == '---':
            if summary_lines:
                break
            continue
        if stripped.startswith('#'):
            if summary_lines:
                break
            continue
        if re.match(r'^[-*]\s+', stripped):
            if summary_lines:
                break
            continue
        summary_lines.append(stripped)

    if not summary_lines and start:
        for line in lines:
            stripped = line.strip()
            if (stripped and stripped != '---' and not stripped.startswith('#')
                    and not re.match(r'^[-*]\s+', stripped)):
                summary_lines.append(stripped)
                break
    summary = ' '.join(summary_lines)
    summary = re.sub(r'!\[[^]]*]\([^)]*\)', '', summary)
    summary = re.sub(r'\[([^]]+)]\([^)]*\)', r'\1', summary)
    summary = re.sub(r'[*_`>#]', '', summary)
    summary = re.sub(r'\s+', ' ', summary).strip(' -–')
    if len(summary) <= limit:
        return summary
    shortened = summary[:limit + 1].rsplit(' ', 1)[0].rstrip(' ,;:-')
    return f'{shortened}…'
