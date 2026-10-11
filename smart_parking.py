"""Smart Parking routes using the original SQLite lots and slots."""
import re
from datetime import datetime, timezone
from database import get_db_connection


def plate_key(value):
    return re.sub(r'[\s-]+', '', value).upper()


def next_session_id(conn):
    """Never recycle a deleted history ID, including during legacy imports."""
    tombstones = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deleted_parking_records'").fetchone()
    sql = 'SELECT COALESCE(MAX(id),0)+1 FROM parking_sessions'
    if tombstones:
        sql = 'SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM parking_sessions UNION ALL SELECT session_id AS id FROM deleted_parking_records)'
    return conn.execute(sql).fetchone()[0]


def initialize():
    conn = get_db_connection()
    try:
        with conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS vehicles (
                    id INTEGER PRIMARY KEY, license_plate TEXT NOT NULL UNIQUE,
                    brand TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS parking_sessions (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
                    spot_id INTEGER NOT NULL REFERENCES parking_slots(id),
                    entry_time TEXT NOT NULL, exit_time TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active','completed')),
                    legacy_booking_id INTEGER UNIQUE REFERENCES parking_bookings(id),
                    CHECK ((status='active' AND exit_time IS NULL) OR
                           (status='completed' AND exit_time IS NOT NULL)));
                CREATE UNIQUE INDEX IF NOT EXISTS active_vehicle ON parking_sessions(vehicle_id) WHERE status='active';
                CREATE UNIQUE INDEX IF NOT EXISTS active_spot ON parking_sessions(spot_id) WHERE status='active';
            ''')
            # Completed legacy sessions deleted explicitly by the administrator
            # must not be re-imported from the preserved source bookings.
            tombstone_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deleted_parking_records'").fetchone()
            deleted = {r[0] for r in conn.execute('SELECT legacy_booking_id FROM deleted_parking_records')} if tombstone_table else set()
            # Idempotent import retains original bookings and relationships.
            for b in conn.execute('SELECT * FROM parking_bookings ORDER BY id').fetchall():
                if b['id'] in deleted:
                    continue
                if conn.execute('SELECT 1 FROM parking_sessions WHERE legacy_booking_id=?', (b['id'],)).fetchone():
                    continue
                if not conn.execute('SELECT 1 FROM parking_slots WHERE id=?', (b['slot_id'],)).fetchone():
                    # Original lot resizing deleted spaces referenced by history.
                    # Restore the reference without adding sellable capacity.
                    conn.execute('INSERT INTO parking_slots(id,parking_lot_id,slot_number,status) VALUES (?,?,?,?)',
                                 (b['slot_id'], b['lot_id'], b['slot_id'], 'available' if b['status'] == 'booked' else 'archived'))
                plate = plate_key(b['vehicle_number']) or f"АРХИВ{b['id']}"
                conn.execute('INSERT OR IGNORE INTO vehicles(license_plate,brand) VALUES (?,?)', (plate, 'Не указана'))
                vehicle = conn.execute('SELECT id FROM vehicles WHERE license_plate=?', (plate,)).fetchone()[0]
                active = b['status'] == 'booked'
                if active and conn.execute("SELECT 1 FROM parking_sessions WHERE status='active' AND (vehicle_id=? OR spot_id=?)", (vehicle, b['slot_id'])).fetchone():
                    raise RuntimeError('В старой базе есть повторные активные бронирования. Исправьте их перед запуском.')
                conn.execute('INSERT INTO parking_sessions(id,vehicle_id,spot_id,entry_time,exit_time,status,legacy_booking_id) VALUES (?,?,?,?,?,?,?)',
                             (next_session_id(conn), vehicle, b['slot_id'], b['booking_time'] or datetime.now(timezone.utc).isoformat(),
                              None if active else (b['unbooking_time'] or b['booking_time'] or datetime.now(timezone.utc).isoformat()),
                              'active' if active else 'completed', b['id']))
                if active:
                    conn.execute("UPDATE parking_slots SET status='booked' WHERE id=?", (b['slot_id'],))
    finally:
        conn.close()


def spot_number(lot_id, number):
    return f"{chr(65 + (lot_id - 1) % 26)}{number:02d}" if lot_id <= 26 else f"{lot_id}-{number:02d}"


