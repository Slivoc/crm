import requests

def import_parts_helper(file_id, mapping):
    """Triggers parts import via API"""
    response = requests.post(
        "http://127.0.0.1:5000/import/parts",
        json={"file_id": file_id, "mapping": mapping}
    )
    return response.json()

def import_customers_helper(file_id, mapping):
    """Triggers customers import via API"""
    response = requests.post(
        "http://127.0.0.1:5000/import/customers",
        json={"file_id": file_id, "mapping": mapping}
    )
    return response.json()

def import_sales_orders_helper(file_id, mapping):
    """Triggers sales orders import via API"""
    response = requests.post(
        "http://127.0.0.1:5000/import/sales_orders",
        json={"file_id": file_id, "mapping": mapping}
    )
    return response.json()

def import_order_lines_helper(file_id, mapping):
    """Triggers order lines import via API"""
    response = requests.post(
        "http://127.0.0.1:5000/import/order_lines",
        json={"file_id": file_id, "mapping": mapping}
    )
    return response.json()
