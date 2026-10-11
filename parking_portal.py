"""Public booking, private client links and protected operator tools.

The original lots, slots and booking history remain in the same SQLite file.
All booking and exit operations hold a SQLite write transaction.
"""
import hashlib
import math
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from database import backup_before_portal_migration, get_db_connection, database_path
from smart_parking import initialize as initialize_original, plate_key, spot_number, next_session_id


def utcnow():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_time(value):
    dt = datetime.fromisoformat(value)
    # Preserve the original database's local wall timestamps.
    return dt.replace(tzinfo=timezone(timedelta(hours=5))) if dt.tzinfo is None else dt


def calculate_fee(seconds, free_minutes=60, hourly_fee=100, eligibility='not_requested'):
    if eligibility == 'confirmed':
        return 0
    extra = max(0, seconds - free_minutes * 60)
    return math.ceil(extra / 3600) * hourly_fee


def initialize():
    backup_before_portal_migration()
    initialize_original()
    conn = get_db_connection()
    try:
        with conn:
            additions = {
                'parking_slots': {'is_accessible': 'INTEGER NOT NULL DEFAULT 0 CHECK(is_accessible IN (0,1))'},
                'parking_sessions': {
                    'client_full_name': "TEXT NOT NULL DEFAULT ''",
                    'client_phone': "TEXT NOT NULL DEFAULT ''",
                    'access_token_hash': 'TEXT',
                    'eligibility': "TEXT NOT NULL DEFAULT 'not_requested' CHECK(eligibility IN ('not_requested','pending','confirmed','rejected'))",
                    'free_minutes': 'INTEGER NOT NULL DEFAULT 60',
                    'hourly_fee': 'INTEGER NOT NULL DEFAULT 100',
                    'final_fee': 'INTEGER',
                    'accessibility_requested': 'INTEGER NOT NULL DEFAULT 0',
                    'eligibility_verified_at': 'TEXT',
                    'eligibility_verified_by': 'INTEGER REFERENCES portal_admins(id)',
                },
            }
            # executescript runs before ALTERs; the enclosing transaction covers
            # all field additions and the one-time defaults below.
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS portal_admins (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES portal_admins(id),
                    expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS parking_messages (
                    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES parking_sessions(id),
                    admin_id INTEGER REFERENCES portal_admins(id), body TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS parking_tariff (
                    id INTEGER PRIMARY KEY CHECK(id=1), free_minutes INTEGER NOT NULL,
                    hourly_fee INTEGER NOT NULL);
                INSERT OR IGNORE INTO parking_tariff VALUES (1,60,100);
                CREATE TABLE IF NOT EXISTS request_limits (
                    key TEXT PRIMARY KEY, started_at REAL NOT NULL, hits INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS portal_migrations (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS deleted_parking_records (
                    session_id INTEGER PRIMARY KEY,
                    legacy_booking_id INTEGER UNIQUE REFERENCES parking_bookings(id),
                    deleted_at TEXT NOT NULL,
                    admin_id INTEGER NOT NULL REFERENCES portal_admins(id));
            ''')
            conn.execute('BEGIN IMMEDIATE')
            for table, fields in additions.items():
                columns = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
                for name, definition in fields.items():
                    if name not in columns:
                        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS client_token ON parking_sessions(access_token_hash) WHERE access_token_hash IS NOT NULL')
            if not conn.execute("SELECT 1 FROM portal_migrations WHERE name='client-portal-v1'").fetchone():
                # Allocate one existing free space per lot to accessibility.
                for lot in conn.execute('SELECT id FROM parking_lots').fetchall():
                    row = conn.execute("SELECT id FROM parking_slots WHERE parking_lot_id=? AND status='available' ORDER BY slot_number LIMIT 1", (lot['id'],)).fetchone()
                    if row:
                        conn.execute('UPDATE parking_slots SET is_accessible=1 WHERE id=?', (row['id'],))
                # Retain old credentials as hashes in their original table, but
                # never enable them as portal login credentials.
                for admin in conn.execute('SELECT id,password FROM admin').fetchall():
                    if not admin['password'].startswith(('scrypt:', 'pbkdf2:')):
                        conn.execute('UPDATE admin SET password=? WHERE id=?', (generate_password_hash(admin['password']), admin['id']))
                # Do not manufacture fees from imported or unverified history.
                conn.execute("INSERT INTO portal_migrations VALUES ('client-portal-v1')")
    finally:
        conn.close()


def session_info(row, now=None):
    now = now or utcnow()
    legacy = row['legacy_booking_id'] is not None
    start = end = None
    try:
        start = parse_time(row['entry_time'])
        end = parse_time(row['exit_time']) if row['status'] == 'completed' else now
        valid_dates = end >= start and start <= now and end <= now
        if legacy and row['status']=='completed' and 'legacy_exit_time' in row.keys():
            # Old imports substituted entry time when the source exit was
            # missing; a placeholder is not a verified historical duration.
            parse_time(row['legacy_exit_time'])
    except (ValueError, TypeError, OverflowError):
        valid_dates = False
    suspicious = row['status'] == 'active' and start is not None and (now-start) > timedelta(days=30)
    duration_status = 'invalid' if not valid_dates else 'unverified' if row['status']=='active' and (legacy or suspicious) else 'valid'
    seconds = (end-start).total_seconds() if duration_status == 'valid' else None
    fee_status = 'invalid' if not valid_dates else 'unverified' if legacy or suspicious or (row['status']=='completed' and row['final_fee'] is None) else 'valid'
    fee = None if fee_status != 'valid' else row['final_fee'] if row['status']=='completed' else calculate_fee(seconds,row['free_minutes'],row['hourly_fee'],row['eligibility'])
    quality = 'invalid' if not valid_dates else 'legacy' if legacy else 'suspicious' if suspicious else 'verified'
    quality_message = 'Некорректные данные: проверьте время въезда и выезда.' if not valid_dates else 'Импортированная запись. Даты и тариф требуют проверки; автоматическое начисление отключено.' if legacy else 'Длительная активная сессия: данные требуют проверки; автоматическое начисление отключено.' if suspicious else ''
    return dict(id=row['id'], license_plate=row['license_plate'], brand=row['brand'],
                client_full_name=row['client_full_name'], client_phone=row['client_phone'],
                spot_id=row['spot_id'], spot_number=spot_number(row['parking_lot_id'], row['slot_number']),
                entry_time=start.isoformat() if start else row['entry_time'], exit_time=end.isoformat() if row['status']=='completed' and end else row['exit_time'],
                status=row['status'], duration_seconds=int(seconds) if seconds is not None else None, fee=fee, final_fee=row['final_fee'],
                is_legacy=legacy, data_quality=quality, data_quality_message=quality_message,
                duration_status=duration_status, fee_status=fee_status,
                eligibility=row['eligibility'], accessibility_requested=bool(row['accessibility_requested']),
                free_minutes=row['free_minutes'], hourly_fee=row['hourly_fee'], server_time=now.isoformat())


SESSION_SQL = '''SELECT p.*,v.license_plate,v.brand,s.parking_lot_id,s.slot_number,b.unbooking_time AS legacy_exit_time
    FROM parking_sessions p JOIN vehicles v ON v.id=p.vehicle_id
    JOIN parking_slots s ON s.id=p.spot_id
    LEFT JOIN parking_bookings b ON b.id=p.legacy_booking_id'''


def register_smart_parking(app):
    initialize()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=16384)
    secure_cookies = os.environ.get('COOKIE_SECURE') == '1'
    app.config['SESSION_COOKIE_SECURE'] = secure_cookies
    bp = Blueprint('portal', __name__)

    def csrf():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        return session['csrf_token']

    def admin_identity():
        token = request.cookies.get('parking_admin', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        conn = get_db_connection()
        try:
            return conn.execute('''SELECT a.id,a.username FROM admin_sessions s
                JOIN portal_admins a ON a.id=s.admin_id WHERE s.token_hash=? AND s.expires_at>?''',
                (digest(token), utcnow().isoformat())).fetchone()
        finally:
            conn.close()

    def admin_required(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            if not admin_identity():
                if request.path.startswith('/api/'):
                    return jsonify(error='Требуется вход администратора.'), 401
                return redirect('/admin/login')
            return func(*args, **kwargs)
        return wrapped

    def rate_limited(category, limit, window=60, identifier=None):
        key = digest(category + ':' + (identifier or request.remote_addr or 'local'))
        now = utcnow().timestamp()
        conn = get_db_connection()
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('DELETE FROM request_limits WHERE started_at<?', (now - 86400,))
                row = conn.execute('SELECT * FROM request_limits WHERE key=?', (key,)).fetchone()
                if row and row['started_at'] > now - window:
                    if row['hits'] >= limit:
                        return True
                    conn.execute('UPDATE request_limits SET hits=hits+1 WHERE key=?', (key,))
                else:
                    conn.execute('INSERT OR REPLACE INTO request_limits VALUES (?,?,1)', (key, now))
            return False
        finally:
            conn.close()

    @bp.route('/', methods=['GET', 'OPTIONS'], provide_automatic_options=False)
    def public_home():
        return render_template('client-parking.html', csrf_token=csrf(), mode='home', token='')

    @bp.before_request
    def protect_requests():
        if request.path.startswith('/api/admin/') and request.path != '/api/admin/login' and not admin_identity():
            return jsonify(error='Требуется вход администратора.'), 401
        if request.method in ('POST', 'DELETE'):
            token = request.headers.get('X-CSRF-Token', '')
            if not token or not secrets.compare_digest(token, session.get('csrf_token', '')):
                return jsonify(error='Обновите страницу и повторите действие.'), 403
            if request.path != '/api/admin/login' and rate_limited('write', 40):
                return jsonify(error='Слишком много запросов. Повторите позже.'), 429

    @app.after_request
    def privacy_headers(response):
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith(('/api/', '/admin', '/my-parking/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route('/dashboard')
    @bp.route('/vehicles')
    @bp.route('/history')
    def old_navigation():
        return redirect('/admin')

    @bp.route('/spots')
    def public_map():
        return redirect('/')

    @bp.route('/my-parking/<token>')
    def private_page(token):
        row = private_row(token)
        if row is None:
            return render_template('portal-error.html', message='Ссылка на парковку недействительна.'), 404
        return render_template('client-parking.html', csrf_token=csrf(), mode='session', token=token)

    @bp.route('/admin/login')
    def login_page():
        if admin_identity():
            return redirect('/admin')
        conn = get_db_connection()
        try:
            configured = bool(conn.execute('SELECT 1 FROM portal_admins LIMIT 1').fetchone())
        finally:
            conn.close()
        return render_template('admin-login-portal.html', csrf_token=csrf(), configured=configured)

    @bp.route('/admin')
    @admin_required
    def admin_page():
        return render_template('admin-parking.html', csrf_token=csrf(), username=admin_identity()['username'])

    @bp.route('/api/admin/login', methods=['POST'])
    def login_admin():
        p = request.get_json(silent=True)
        if not isinstance(p, dict) or not isinstance(p.get('username'), str) or not isinstance(p.get('password'), str) or len(p['username']) > 80 or len(p['password']) > 256:
            return jsonify(error='Введите логин и пароль.'), 400
        username = p['username'].strip()
        if rate_limited('login-ip', 10, 600) or rate_limited('login-account', 15, 600, username.casefold()):
            response = jsonify(error='Слишком много попыток входа. Повторите через 10 минут.')
            response.headers['Retry-After'] = '600'
            return response, 429
        conn = get_db_connection()
        try:
            row = conn.execute('SELECT * FROM portal_admins WHERE username=?', (username,)).fetchone()
            valid = check_password_hash(row['password_hash'], p['password']) if row else check_password_hash(app.config['DUMMY_PASSWORD_HASH'], p['password'])
            if not row or not valid:
                return jsonify(error='Неверный логин или пароль.'), 401
            token = secrets.token_urlsafe(32)
            with conn:
                conn.execute('DELETE FROM admin_sessions WHERE expires_at<=?', (utcnow().isoformat(),))
                old = request.cookies.get('parking_admin', '')
                if old:
                    conn.execute('DELETE FROM admin_sessions WHERE token_hash=?', (digest(old),))
                conn.execute('INSERT INTO admin_sessions VALUES (?,?,?)', (digest(token), row['id'], (utcnow()+timedelta(hours=8)).isoformat()))
            session.clear()
            csrf()
            response = jsonify(message='Вход выполнен.')
            response.set_cookie('parking_admin', token, max_age=28800, httponly=True, secure=secure_cookies, samesite='Lax', path='/')
            return response
        finally:
            conn.close()

    @bp.route('/api/admin/logout', methods=['POST'])
    @admin_required
    def logout_admin():
        conn = get_db_connection()
        try:
            with conn:
                conn.execute('DELETE FROM admin_sessions WHERE token_hash=?', (digest(request.cookies.get('parking_admin', '')),))
        finally:
            conn.close()
        session.clear()
        response = jsonify(message='Вы вышли из системы.')
        response.delete_cookie('parking_admin')
        return response

    def tariff(conn):
        return dict(conn.execute('SELECT free_minutes,hourly_fee FROM parking_tariff WHERE id=1').fetchone())

    @bp.route('/api/spots')
    @bp.route('/api/spots/available')
    def public_spots():
        conn = get_db_connection()
        try:
            rows = conn.execute('''SELECT s.id,s.parking_lot_id,s.slot_number,s.status,s.is_accessible,l.location_name,
                EXISTS(SELECT 1 FROM parking_sessions p WHERE p.spot_id=s.id AND p.status='active') AS active
                FROM parking_slots s JOIN parking_lots l ON l.id=s.parking_lot_id
                WHERE s.status!='archived' ORDER BY s.parking_lot_id,s.slot_number''').fetchall()
            spots = [dict(id=r['id'], parking_lot_id=r['parking_lot_id'], number=spot_number(r['parking_lot_id'],r['slot_number']),
                          lot_name=r['location_name'], is_occupied=r['status']!='available' or bool(r['active']),
                          is_accessible=bool(r['is_accessible'])) for r in rows]
            if request.path.endswith('/available'):
                return jsonify([s for s in spots if not s['is_occupied']])
            return jsonify(spots=spots, tariff=tariff(conn), server_time=utcnow().isoformat(),
                           stats=dict(total=len(spots), available=sum(not s['is_occupied'] for s in spots),
                                      occupied=sum(s['is_occupied'] for s in spots), accessible=sum(s['is_accessible'] for s in spots)),
                           lots=[dict(id=r['id'], name=r['location_name'], address=r['address']) for r in conn.execute('SELECT * FROM parking_lots')])
        finally:
            conn.close()

    @bp.route('/api/parking/enter', methods=['POST'])
    def enter():
        if rate_limited('entry', 12, 600):
            return jsonify(error='Слишком много регистраций. Повторите позже.'), 429
        p = request.get_json(silent=True)
        if not isinstance(p, dict):
            return jsonify(error='Заполните форму парковки.'), 400
        name, phone, plate, spot = (p.get(k) for k in ('full_name','phone','license_plate','spot_id'))
        if not all(isinstance(v,str) for v in (name, phone, plate)) or type(spot) is not int:
            return jsonify(error='Укажите ФИО, телефон, номер автомобиля и место.'), 400
        name = ' '.join(name.split())
        if not 3 <= len(name) <= 120 or len(name.split()) < 2 or not re.fullmatch(r"[А-Яа-яЁёA-Za-z\s.'’\-]+", name):
            return jsonify(error='Укажите фамилию и имя буквами.'), 400
        if len(phone) > 30 or not re.fullmatch(r'[+\d\s()\-]+', phone):
            return jsonify(error='Укажите телефон в формате +7 777 123 45 67.'), 400
        phone = re.sub(r'\D', '', phone)
        if len(phone)==11 and phone[0]=='8':
            phone = '7' + phone[1:]
        if not re.fullmatch(r'7\d{10}', phone):
            return jsonify(error='Укажите телефон в формате +7 777 123 45 67.'), 400
        plate = plate_key(plate)
        if not re.fullmatch(r'[A-ZА-ЯЁ0-9]{2,16}', plate) or not any(c.isdigit() for c in plate):
            return jsonify(error='Проверьте государственный номер автомобиля.'), 400
        conn = get_db_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM parking_slots WHERE id=?', (spot,)).fetchone()
            if row is None:
                return jsonify(error='Парковочное место не найдено.'), 404
            if row['status'] != 'available':
                return jsonify(error='Это место уже занято. Выберите другое.'), 409
            if row['is_accessible'] and p.get('accessibility_request') is not True:
                return jsonify(error='Подтвердите запрос на специальное место. Право на бесплатную парковку проверит администратор.'), 400
            policy = tariff(conn)
            conn.execute('INSERT OR IGNORE INTO vehicles(license_plate,brand) VALUES (?,?)', (plate, 'Не указана'))
            vehicle = conn.execute('SELECT id FROM vehicles WHERE license_plate=?', (plate,)).fetchone()[0]
            token = secrets.token_urlsafe(32)
            now = utcnow().isoformat()
            cur = conn.execute('''INSERT INTO parking_sessions(id,vehicle_id,spot_id,entry_time,status,
                client_full_name,client_phone,access_token_hash,eligibility,accessibility_requested,free_minutes,hourly_fee)
                VALUES (?,?,?,?,'active',?,?,?,?,?,?,?)''',
                (next_session_id(conn),vehicle,spot,now,name,'+'+phone,digest(token),'pending' if row['is_accessible'] else 'not_requested',
                 int(row['is_accessible']),policy['free_minutes'],policy['hourly_fee']))
            conn.execute("UPDATE parking_slots SET status='booked' WHERE id=?", (spot,))
            conn.execute('INSERT INTO parking_messages(session_id,body,created_at) VALUES (?,?,?)', (cur.lastrowid, 'Ваш автомобиль зарегистрирован. Сохраните личную ссылку на парковку.', now))
            conn.commit()
            # Token appears only in this creation response and the private link.
            return jsonify(private_url='/my-parking/'+token), 201
        except sqlite3.IntegrityError:
            return jsonify(error='У автомобиля уже есть активная парковка или место занято.'), 409
        finally:
            conn.close()

    def private_row(token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            return None
        conn = get_db_connection()
        try:
            return conn.execute(SESSION_SQL+' WHERE p.access_token_hash=?', (digest(token),)).fetchone()
        finally:
            conn.close()

    def private_access():
        # Keep bearer tokens out of API query strings and server access logs.
        token = request.headers.get('Authorization','')
        if not token.startswith('Bearer '):
            return None
        return private_row(token[7:])

    def complete_session(conn, row, now, admin_id=None):
        """Caller holds BEGIN IMMEDIATE and has checked active ownership."""
        fee = session_info(row, now)['fee']
        conn.execute("UPDATE parking_sessions SET status='completed',exit_time=?,final_fee=? WHERE id=?",
                     (now.isoformat(), fee, row['id']))
        conn.execute("UPDATE parking_slots SET status='available' WHERE id=?", (row['spot_id'],))
        if row['legacy_booking_id']:
            conn.execute("UPDATE parking_bookings SET status='unbooked',unbooking_time=? WHERE id=?",
                         (now.isoformat(), row['legacy_booking_id']))
        text = f'Парковка завершена. К оплате: {fee} ₸.' if fee is not None else 'Парковка завершена. Сумма требует проверки администратором.'
        conn.execute('INSERT INTO parking_messages(session_id,admin_id,body,created_at) VALUES (?,?,?,?)',
                     (row['id'], admin_id, text, now.isoformat()))
        return fee

    @bp.route('/api/parking/my-session/exit', methods=['POST'])
    @bp.route('/api/parking/my-session/cancel', methods=['POST'])
    def client_exit():
        bearer = request.headers.get('Authorization', '')
        token = bearer[7:] if bearer.startswith('Bearer ') else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return jsonify(error='Ссылка на парковку недействительна.'), 404
        conn = get_db_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            # No client-supplied session ID is used. Ownership is checked under
            # the same lock as completion, including concurrent admin exits.
            row = conn.execute(SESSION_SQL+' WHERE p.access_token_hash=?', (digest(token),)).fetchone()
            if row is None:
                return jsonify(error='Ссылка на парковку недействительна.'), 404
            if row['status'] != 'active':
                return jsonify(error='Парковка уже завершена.'), 409
            if request.path.endswith('/cancel'):
                return jsonify(error='Для отмены активной парковки обратитесь к администратору.'), 409
            now = utcnow()
            fee = complete_session(conn, row, now)
            conn.commit()
            completed = conn.execute(SESSION_SQL+' WHERE p.id=?', (row['id'],)).fetchone()
            return jsonify(message='Парковка завершена', final_fee=fee, session=session_info(completed, now))
        finally:
            conn.close()

    @bp.route('/api/parking/my-session')
    @bp.route('/api/parking/my-messages')
    def my_session():
        if rate_limited('private-read', 180):
            return jsonify(error='Слишком много запросов. Повторите позже.'), 429
        row = private_access()
        if row is None:
            return jsonify(error='Ссылка на парковку недействительна.'), 404
        if request.path.endswith('my-session'):
            return jsonify(session_info(row))
        conn = get_db_connection()
        try:
            return jsonify([dict(r) for r in conn.execute('SELECT id,body,created_at FROM parking_messages WHERE session_id=? ORDER BY id DESC',(row['id'],))])
        finally:
            conn.close()

    @bp.route('/api/admin/sessions')
    @admin_required
    def admin_sessions():
        conn = get_db_connection()
        try:
            now = utcnow()
            rows = [session_info(r, now) for r in conn.execute(SESSION_SQL+' ORDER BY p.id DESC')]
            query = plate_key(request.args.get('q',''))[:50]
            status = request.args.get('status','all')
            stats = dict(active=sum(r['status']=='active' for r in rows),
                         total_fees_due=sum(r['fee'] for r in rows if r['status']=='active' and r['fee'] is not None),
                         unverified_fees=sum(r['status']=='active' and r['fee'] is None for r in rows),
                         pending=sum(r['eligibility']=='pending' and r['status']=='active' for r in rows))
            return jsonify(sessions=[r for r in rows if query in plate_key(r['license_plate']) and (status=='all' or r['status']==status)],
                           stats=stats, server_time=now.isoformat())
        finally:
            conn.close()

    @bp.route('/api/admin/sessions/<int:sid>/exit', methods=['POST'])
    @admin_required
    def exit_vehicle(sid):
        conn = get_db_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(SESSION_SQL+' WHERE p.id=?', (sid,)).fetchone()
            if row is None:
                return jsonify(error='Парковка не найдена.'),404
            if row['status']!='active':
                return jsonify(error='Автомобиль уже выехал.'),409
            now=utcnow()
            fee=complete_session(conn,row,now,admin_identity()['id'])
            conn.commit()
            return jsonify(message='Выезд зарегистрирован.',final_fee=fee)
        finally:
            conn.close()

    @bp.route('/api/admin/sessions/<int:sid>', methods=['DELETE'])
    @admin_required
    def delete_history(sid):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('confirm') is not True:
            return jsonify(error='Подтвердите безвозвратное удаление записи.'), 400
        conn = get_db_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM parking_sessions WHERE id=?', (sid,)).fetchone()
            if row is None:
                return jsonify(error='Запись не найдена.'), 404
            if row['status'] != 'completed':
                return jsonify(error='Активную парковку удалить нельзя. Сначала завершите её.'), 409
            # A separate reader can back up the committed snapshot while this
            # connection holds the write reservation, before any deletion.
            backup = str(database_path()) + f'.before-session-delete-{sid}-' + utcnow().strftime('%Y%m%d-%H%M%S-%f') + '.bak'
            source = target = None
            try:
                source = get_db_connection()
                target = sqlite3.connect(backup)
                source.backup(target)
            except (OSError, sqlite3.Error):
                return jsonify(error='Не удалось создать резервную копию. Запись не удалена.'), 503
            finally:
                if target is not None:
                    target.close()
                if source is not None:
                    source.close()
            conn.execute('INSERT INTO deleted_parking_records VALUES (?,?,?,?)',
                         (row['id'], row['legacy_booking_id'], utcnow().isoformat(), admin_identity()['id']))
            conn.execute('DELETE FROM parking_messages WHERE session_id=?', (sid,))
            conn.execute('DELETE FROM parking_sessions WHERE id=?', (sid,))
            # Never modify the vehicle, other sessions or any parking space.
            conn.commit()
            return jsonify(message='Историческая запись удалена.')
        finally:
            conn.close()

    @bp.route('/api/admin/sessions/<int:sid>/message', methods=['POST'])
    @admin_required
    def send_message(sid):
        p=request.get_json(silent=True)
        if not isinstance(p,dict) or not isinstance(p.get('body'),str) or not 1<=len(p['body'].strip())<=1000:
            return jsonify(error='Введите сообщение длиной до 1000 символов.'),400
        conn=get_db_connection()
        try:
            if not conn.execute('SELECT 1 FROM parking_sessions WHERE id=?',(sid,)).fetchone():
                return jsonify(error='Парковка не найдена.'),404
            with conn:
                conn.execute('INSERT INTO parking_messages(session_id,admin_id,body,created_at) VALUES (?,?,?,?)',
                             (sid,admin_identity()['id'],p['body'].strip(),utcnow().isoformat()))
            return jsonify(message='Сообщение отправлено.'),201
        finally:
            conn.close()

    @bp.route('/api/admin/sessions/<int:sid>/messages')
    @admin_required
    def admin_messages(sid):
        conn=get_db_connection()
        try:
            if not conn.execute('SELECT 1 FROM parking_sessions WHERE id=?',(sid,)).fetchone():
                return jsonify(error='Парковка не найдена.'),404
            return jsonify([dict(r) for r in conn.execute('SELECT id,body,created_at FROM parking_messages WHERE session_id=? ORDER BY id DESC',(sid,))])
        finally:
            conn.close()

    @bp.route('/api/admin/sessions/<int:sid>/verify-accessibility', methods=['POST'])
    @admin_required
    def verify(sid):
        p=request.get_json(silent=True)
        if not isinstance(p,dict) or type(p.get('confirmed')) is not bool:
            return jsonify(error='Укажите результат проверки.'),400
        conn=get_db_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM parking_sessions WHERE id=?',(sid,)).fetchone()
            if not row:
                return jsonify(error='Парковка не найдена.'),404
            if not row['accessibility_requested'] or row['status']!='active':
                return jsonify(error='Для этой сессии нет активного запроса.'),409
            status='confirmed' if p['confirmed'] else 'rejected'
            now=utcnow().isoformat()
            conn.execute('UPDATE parking_sessions SET eligibility=?,eligibility_verified_at=?,eligibility_verified_by=? WHERE id=?',
                         (status,now,admin_identity()['id'],sid))
            text='Право на бесплатную парковку подтверждено. К оплате: 0 ₸.' if p['confirmed'] else 'Право на бесплатную парковку не подтверждено. Применяется обычный тариф, указанный при въезде.'
            conn.execute('INSERT INTO parking_messages(session_id,admin_id,body,created_at) VALUES (?,?,?,?)',(sid,admin_identity()['id'],text,now))
            conn.commit()
            return jsonify(message='Результат проверки сохранён.')
        finally:
            conn.close()

    @bp.route('/api/admin/tariff',methods=['POST'])
    @admin_required
    def update_tariff():
        p=request.get_json(silent=True)
        if not isinstance(p,dict) or type(p.get('free_minutes')) is not int or not 0<=p['free_minutes']<=1440 or type(p.get('hourly_fee')) is not int or not 0<=p['hourly_fee']<=100000:
            return jsonify(error='Укажите от 0 до 1440 бесплатных минут и стоимость от 0 до 100000 ₸.'),400
        conn=get_db_connection()
        try:
            with conn:
                conn.execute('UPDATE parking_tariff SET free_minutes=?,hourly_fee=? WHERE id=1',(p['free_minutes'],p['hourly_fee']))
            return jsonify(message='Тариф сохранён. Он применяется только к новым въездам.')
        finally:
            conn.close()

    @bp.route('/api/admin/spots/<int:spot_id>',methods=['POST'])
    @admin_required
    def update_spot(spot_id):
        p=request.get_json(silent=True)
        if not isinstance(p,dict) or type(p.get('is_accessible')) is not bool:
            return jsonify(error='Укажите тип места.'),400
        conn=get_db_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT status,EXISTS(SELECT 1 FROM parking_sessions WHERE spot_id=parking_slots.id AND status='active') AS active FROM parking_slots WHERE id=?",(spot_id,)).fetchone()
            if not row:
                return jsonify(error='Место не найдено.'),404
            if row['status']!='available' or row['active']:
                return jsonify(error='Тип можно изменить только у свободного места.'),409
            conn.execute('UPDATE parking_slots SET is_accessible=? WHERE id=?',(int(p['is_accessible']),spot_id))
            conn.commit()
            return jsonify(message='Тип места изменён.')
        finally:
            conn.close()

    @bp.route('/api/admin/lots',methods=['POST'])
    @admin_required
    def create_lot():
        p=request.get_json(silent=True)
        if not isinstance(p,dict) or not all(isinstance(p.get(k),str) and 1<=len(p[k].strip())<=150 for k in ('name','address')) or type(p.get('total_spots')) is not int or not 1<=p['total_spots']<=500 or type(p.get('accessible_spots',0)) is not int or not 0<=p.get('accessible_spots',0)<=p['total_spots']:
            return jsonify(error='Проверьте название, адрес и количество мест (от 1 до 500).'),400
        conn=get_db_connection()
        try:
            with conn:
                cur=conn.execute('INSERT INTO parking_lots(location_name,address,pincode,price_per_hour,max_spots) VALUES (?,?,?,0,?)',(p['name'].strip(),p['address'].strip(),'',p['total_spots']))
                conn.executemany("INSERT INTO parking_slots(parking_lot_id,slot_number,status,is_accessible) VALUES (?,?,'available',?)",[(cur.lastrowid,n,int(n<=p.get('accessible_spots',0))) for n in range(1,p['total_spots']+1)])
            return jsonify(message='Парковка создана.'),201
        finally:
            conn.close()

    @bp.route('/api/dashboard')
    @bp.route('/api/sessions',methods=['POST'])
    @bp.route('/api/sessions/<int:sid>/exit',methods=['POST'])
    @bp.route('/api/lots',methods=['POST'])
    def retired_api(sid=None):
        return jsonify(error='Используйте новые защищённые маршруты.'),410

    @app.errorhandler(sqlite3.DatabaseError)
    def database_error(error):
        app.logger.error('Database operation failed: %s', type(error).__name__)
        return jsonify(error='Не удалось сохранить данные. Повторите попытку.'),503

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith('/api/'):
            return jsonify(error='Запись или адрес не найдены.'),404
        return render_template('portal-error.html',message='Страница не найдена.'),404

    @app.errorhandler(413)
    def oversized(error):
        return jsonify(error='Размер данных превышает допустимый.'),413

    @app.errorhandler(405)
    def wrong_method(error):
        return jsonify(error='Этот способ запроса не поддерживается.'),405

    app.config['DUMMY_PASSWORD_HASH']=generate_password_hash(secrets.token_hex(32))
    app.register_blueprint(bp)
