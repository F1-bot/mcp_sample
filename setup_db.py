import sqlite3
import random
from datetime import datetime, timedelta

def setup_database():
    conn = sqlite3.connect('commerce.db')
    cursor = conn.cursor()

    # Create Tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER NOT NULL
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        quantity INTEGER,
        order_date TEXT,
        status TEXT,
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    ''')

    # Clear existing data
    cursor.execute('DELETE FROM orders')
    cursor.execute('DELETE FROM products')

    # Insert Sample Products
    products = [
        ('Laptop Pro X', 'Electronics', 1299.99, 50),
        ('Wireless Mouse', 'Electronics', 29.99, 200),
        ('Ergonomic Chair', 'Furniture', 349.50, 15),
        ('Coffee Maker', 'Appliances', 89.99, 30),
        ('Bluetooth Headphones', 'Electronics', 159.00, 75),
        ('Desk Lamp', 'Furniture', 45.00, 60)
    ]
    cursor.executemany('INSERT INTO products (name, category, price, stock) VALUES (?, ?, ?, ?)', products)

    # Get actual product IDs
    cursor.execute('SELECT id FROM products')
    product_ids = [row[0] for row in cursor.fetchall()]

    # Insert Sample Orders
    orders = []
    statuses = ['pending', 'shipped', 'delivered', 'cancelled']
    
    for _ in range(20):
        product_id = random.choice(product_ids)
        quantity = random.randint(1, 3)
        date = (datetime.now() - timedelta(days=random.randint(0, 30))).strftime('%Y-%m-%d %H:%M:%S')
        status = random.choice(statuses)
        orders.append((product_id, quantity, date, status))

    cursor.executemany('INSERT INTO orders (product_id, quantity, order_date, status) VALUES (?, ?, ?, ?)', orders)

    conn.commit()
    conn.close()
    print("Database 'commerce.db' initialized with sample data.")

if __name__ == "__main__":
    setup_database()
