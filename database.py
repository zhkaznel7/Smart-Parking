import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone
from contextlib import closing

def database_path():
    return Path(os.environ.get('PARKING_DATABASE', str(Path(__file__).with_name('parking_app.db'))))

def backup_before_portal_migration():
    path = database_path()
    if not path.exists():
        return
    with closing(sqlite3.connect(str(path))) as source:
        columns = {r[1] for r in source.execute('PRAGMA table_info(parking_sessions)')}
        deletion_schema = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deleted_parking_records'").fetchone()
        if 'access_token_hash' in columns and deletion_schema:
            return
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        target = sqlite3.connect(str(path) + '.before-portal-' + stamp + '.bak')
        try:
            source.backup(target)
        finally:
            target.close()

def get_db_connection():
    conn = sqlite3.connect(str(database_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_db():
    """Initialize the database, create tables, insert default data, and migrate schema."""
    conn = get_db_connection()
    c = conn.cursor()

    # Create admin table
    c.execute(''' 
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    # Administrator credentials are now provisioned with manage.py.
    # Never create a default password.

    # Create users table
    c.execute(''' 
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            fullname TEXT NOT NULL,
            address TEXT NOT NULL,
            pincode TEXT NOT NULL,
            slot_id INTEGER
        )
    ''')

    # Create parking_lots table
    c.execute(''' 
        CREATE TABLE IF NOT EXISTS parking_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_name TEXT NOT NULL,
            address TEXT NOT NULL,
            pincode TEXT NOT NULL,
            price_per_hour REAL NOT NULL,
            max_spots INTEGER NOT NULL
        )
    ''')

    # Create parking_slots table
    c.execute(''' 
        CREATE TABLE IF NOT EXISTS parking_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parking_lot_id INTEGER NOT NULL,
            slot_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            FOREIGN KEY (parking_lot_id) REFERENCES parking_lots(id)
        )
    ''')

    # Create parking_bookings table
    c.execute(''' 
        CREATE TABLE IF NOT EXISTS parking_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id INTEGER NOT NULL,
            lot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            vehicle_number TEXT NOT NULL,
            location_name TEXT,
            status TEXT NOT NULL DEFAULT 'booked',
            booking_time TIMESTAMP DEFAULT (datetime('now', '+5 hours', '30 minutes')),
            unbooking_time TIMESTAMP,
            total_cost REAL,
            FOREIGN KEY (slot_id) REFERENCES parking_slots(id),
            FOREIGN KEY (lot_id) REFERENCES parking_lots(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # Migrate existing database: Add location_name column if not exists
    c.execute("PRAGMA table_info(parking_bookings)")
    columns = [col['name'] for col in c.fetchall()]
    if 'location_name' not in columns:
        c.execute('ALTER TABLE parking_bookings ADD COLUMN location_name TEXT')

    # Migrate existing database: Add total_cost column if not exists
    if 'total_cost' not in columns:
        c.execute('ALTER TABLE parking_bookings ADD COLUMN total_cost REAL')

    # Populate location_name for existing bookings
    c.execute('''
        UPDATE parking_bookings
        SET location_name = (
            SELECT location_name
            FROM parking_lots
            WHERE parking_lots.id = parking_bookings.lot_id
        )
        WHERE location_name IS NULL
    ''')

    # Existing timestamps must never be shifted again on startup.

    conn.commit()
    conn.close()


# Initialize the database
backup_before_portal_migration()
init_db()
