"""Provision a portal administrator without default credentials."""
import argparse
import getpass
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash
from database import get_db_connection
from parking_portal import initialize


def main():
    parser=argparse.ArgumentParser(description='Настройка администратора Smart Parking')
    parser.add_argument('command',choices=['create-admin'])
    parser.add_argument('--username',required=True)
    args=parser.parse_args()
    username=args.username.strip()
    if not 3<=len(username)<=80 or not all(c.isalnum() or c in '._-' for c in username):
        parser.error('Логин: от 3 до 80 букв, цифр, точек, дефисов или подчёркиваний.')
    password=getpass.getpass('Пароль (не менее 12 символов): ')
    confirmation=getpass.getpass('Повторите пароль: ')
    if password!=confirmation or not 12<=len(password)<=256:
        parser.error('Пароли должны совпадать и содержать от 12 до 256 символов.')
    initialize()
    conn=get_db_connection()
    try:
        with conn:
            conn.execute('''INSERT INTO portal_admins(username,password_hash,created_at) VALUES (?,?,?)
                ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash''',
                (username,generate_password_hash(password),datetime.now(timezone.utc).isoformat()))
            admin_id=conn.execute('SELECT id FROM portal_admins WHERE username=?',(username,)).fetchone()[0]
            conn.execute('DELETE FROM admin_sessions WHERE admin_id=?',(admin_id,))
        print('Администратор настроен. Откройте /admin и войдите с выбранным паролем.')
    finally:
        conn.close()


if __name__=='__main__':
    main()
