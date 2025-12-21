import sqlite3
from collections import defaultdict

def extract_domain(email):
    """Extract domain from email address."""
    if not email or '@' not in email:
        return None
    return email.split('@')[1].lower()

def populate_domains():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # Create a dictionary to store customer_id -> set of domains
    customer_domains = defaultdict(set)
    supplier_domains = defaultdict(set)

    # Get domains from contacts table
    cursor.execute("""
        SELECT customer_id, email 
        FROM contacts 
        WHERE email IS NOT NULL AND customer_id IS NOT NULL
    """)
    for customer_id, email in cursor.fetchall():
        domain = extract_domain(email)
        if domain:
            customer_domains[customer_id].add(domain)

    # Get domains from supplier contacts
    cursor.execute("""
        SELECT supplier_id, email_address 
        FROM supplier_contacts 
        WHERE email_address IS NOT NULL AND supplier_id IS NOT NULL
    """)
    for supplier_id, email in cursor.fetchall():
        domain = extract_domain(email)
        if domain:
            supplier_domains[supplier_id].add(domain)

    # Get domains from suppliers table directly
    cursor.execute("""
        SELECT id, contact_email 
        FROM suppliers 
        WHERE contact_email IS NOT NULL
    """)
    for supplier_id, email in cursor.fetchall():
        domain = extract_domain(email)
        if domain:
            supplier_domains[supplier_id].add(domain)

    # Insert customer domains
    for customer_id, domains in customer_domains.items():
        for domain in domains:
            try:
                cursor.execute("""
                    INSERT INTO customer_domains (customer_id, domain)
                    VALUES (?, ?)
                    ON CONFLICT (customer_id, domain) DO NOTHING
                """, (customer_id, domain))
            except sqlite3.Error as e:
                print(f"Error inserting customer domain {domain} for customer {customer_id}: {e}")

    # Insert supplier domains
    for supplier_id, domains in supplier_domains.items():
        for domain in domains:
            try:
                cursor.execute("""
                    INSERT INTO supplier_domains (supplier_id, domain)
                    VALUES (?, ?)
                    ON CONFLICT (supplier_id, domain) DO NOTHING
                """, (supplier_id, domain))
            except sqlite3.Error as e:
                print(f"Error inserting supplier domain {domain} for supplier {supplier_id}: {e}")

    # Commit changes and close connection
    conn.commit()

    # Print summary
    cursor.execute("SELECT COUNT(*) FROM customer_domains")
    customer_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM supplier_domains")
    supplier_count = cursor.fetchone()[0]

    print(f"Inserted {customer_count} customer domain entries")
    print(f"Inserted {supplier_count} supplier domain entries")

    conn.close()

    # Return the counts for the web interface
    return customer_count, supplier_count

if __name__ == "__main__":
    populate_domains()