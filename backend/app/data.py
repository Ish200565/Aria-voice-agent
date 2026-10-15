"""Mock order database (single source of truth; sessions get their own deep copy)."""

MOCK_ORDERS = {
    "ORD-101": {
        "order_id": "ORD-101",
        "customer": "Priya Sharma",
        "product": "Vitamin C Serum (30ml)",
        "value_inr": 699,
        "status": "Out for Delivery",
        "courier": "BlueDart",
        "tracking_id": "BD-982103",
        "note": "Expected by 6 PM today",
        "hours_since_delivery": None,
    },
    "ORD-102": {
        "order_id": "ORD-102",
        "customer": "Rahul Verma",
        "product": "Hydrating Sunscreen SPF 50",
        "value_inr": 499,
        "status": "Delivered",
        "courier": "Delhivery",
        "tracking_id": "DL-441029",
        "note": "Delivered 14 days ago",
        "hours_since_delivery": 14 * 24,
    },
    "ORD-103": {
        "order_id": "ORD-103",
        "customer": "Ananya Patel",
        "product": "Green Tea Face Wash + Toner",
        "value_inr": 850,
        "status": "Processing",
        "courier": None,
        "tracking_id": None,
        "note": "Ordered 3 hours ago. Eligible for cancellation",
        "hours_since_delivery": None,
    },
}
