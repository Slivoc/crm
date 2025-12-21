import csv
import sqlite3
from datetime import datetime

def update_customer_data(db_path, csv_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Read CSV file
    with open(csv_path, 'r') as file:
        csv_reader = csv.DictReader(file)
        
        for row in csv_reader:
            # Get customer ID using system_code
            cursor.execute(
                "SELECT id FROM customers WHERE system_code = ?",
                (row['system_code'],)
            )
            result = cursor.fetchone()
            
            if not result:
                print(f"Customer not found for system_code: {row['system_code']}")
                continue
                
            customer_id = result[0]
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Update customer budget and salesperson
            cursor.execute("""
                UPDATE customers 
                SET budget = ?,
                    salesperson_id = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                float(row['budget']) if row['budget'] else None,
                int(row['salesperson_id']) if row['salesperson_id'] else None,
                current_time,
                customer_id
            ))
            
            # Create customer_update if comment exists
            if row['comment'] and row['comment'].strip():
                cursor.execute("""
                    INSERT INTO customer_updates 
                    (date, customer_id, salesperson_id, update_text)
                    VALUES (?, ?, ?, ?)
                """, (
                    current_time,
                    customer_id,
                    int(row['salesperson_id']) if row['salesperson_id'] else None,
                    row['comment'].strip()
                ))
    
    # Commit changes and close connection
    conn.commit()
    conn.close()

if __name__ == "__main__":
    try:
        update_customer_data('database.db', 'budgetfrancesco.csv')
        print("Update completed successfully")
    except Exception as e:
        print(f"An error occurred: {str(e)}")