import os
import sys
import shutil
import secrets
import io
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class PortalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = os.environ.get('PARKING_DATABASE')
        os.environ['PARKING_DATABASE'] = str(Path(self.directory.name) / 'test.db')
        from database import init_db, get_db_connection
        init_db()
        from flask import Flask
        from parking_portal import register_smart_parking, utcnow
        from werkzeug.security import generate_password_hash
        self.app = Flask('parking_test', template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'static'))
        self.app.config.update(TESTING=True, SECRET_KEY=secrets.token_hex(32))
        register_smart_parking(self.app)
        self.password = secrets.token_urlsafe(20)
        self.username = 'operator'
        conn = get_db_connection()
        with conn:
            conn.execute('INSERT INTO portal_admins(username,password_hash,created_at) VALUES (?,?,?)', (self.username,generate_password_hash(self.password),utcnow().isoformat()))
            cur = conn.execute('INSERT INTO parking_lots(location_name,address,pincode,price_per_hour,max_spots) VALUES (?,?,?,0,4)', ('Тестовая парковка','Алматы',''))
            conn.executemany("INSERT INTO parking_slots(parking_lot_id,slot_number,status,is_accessible) VALUES (?,?,'available',?)", [(cur.lastrowid,n,int(n==1)) for n in range(1,5)])
        conn.close()
        self.client = self.app.test_client()
        self.admin = self.app.test_client()
        self.client.get('/')
        self.admin.get('/admin/login')
        self.assertEqual(self.post(self.admin,'/api/admin/login',{'username':self.username,'password':self.password}).status_code,200)
        self.admin.get('/admin')

    def tearDown(self):
        if self.previous is None:
            os.environ.pop('PARKING_DATABASE',None)
        else:
            os.environ['PARKING_DATABASE'] = self.previous
        self.directory.cleanup()

    def post(self,client,url,payload):
        with client.session_transaction() as s:
            token = s.get('csrf_token','')
        return client.post(url,json=payload,headers={'X-CSRF-Token':token})

    def enter(self,client=None,spot=2,plate='777 abc 02',accessible=False):
        client=client or self.client
        return self.post(client,'/api/parking/enter',{'full_name':'Иван Иванов','phone':'+7 777 123 45 67','license_plate':plate,'spot_id':spot,'accessibility_request':accessible})

    def private(self,token,path='my-session'):
        return self.client.get('/api/parking/'+path,headers={'Authorization':'Bearer '+token})

    def client_action(self, token, action='exit', client=None, payload=None):
        client=client or self.client
        with client.session_transaction() as s:
            csrf=s.get('csrf_token','')
        return client.post('/api/parking/my-session/'+action,json=payload or {},
                           headers={'X-CSRF-Token':csrf,'Authorization':'Bearer '+token})

    def token(self,response):
        self.assertEqual(response.status_code,201,response.json)
        return response.json['private_url'].rsplit('/',1)[-1]

    def age(self,sid,seconds):
        from database import get_db_connection
        from parking_portal import utcnow
        conn=get_db_connection()
        with conn:
            conn.execute('UPDATE parking_sessions SET entry_time=? WHERE id=?',((utcnow()-timedelta(seconds=seconds)).isoformat(),sid))
        conn.close()

    def mark_legacy(self,sid):
        from database import get_db_connection
        conn=get_db_connection()
        with conn:
            row=conn.execute('''SELECT p.*,s.parking_lot_id,v.license_plate FROM parking_sessions p
                JOIN parking_slots s ON s.id=p.spot_id JOIN vehicles v ON v.id=p.vehicle_id WHERE p.id=?''',(sid,)).fetchone()
            user=conn.execute('INSERT INTO users(email,username,password,fullname,address,pincode) VALUES (?,?,?,?,?,?)',
                             ('archive@example.test','archive',secrets.token_hex(16),'Архивный клиент','Адрес',''))
            booking=conn.execute('''INSERT INTO parking_bookings(slot_id,lot_id,user_id,vehicle_number,booking_time,unbooking_time,status)
                VALUES (?,?,?,?,?,?,?)''',(row['spot_id'],row['parking_lot_id'],user.lastrowid,row['license_plate'],row['entry_time'],row['exit_time'],'booked' if row['status']=='active' else 'unbooked'))
            conn.execute('UPDATE parking_sessions SET legacy_booking_id=? WHERE id=?',(booking.lastrowid,sid))
        conn.close()
        return booking.lastrowid

    def delete_record(self,sid,payload=None,client=None,csrf=True):
        client=client or self.admin
        headers={}
        if csrf:
            with client.session_transaction() as s:
                headers['X-CSRF-Token']=s.get('csrf_token','')
        return client.delete(f'/api/admin/sessions/{sid}',json={'confirm':True} if payload is None else payload,headers=headers)

    def test_complete_client_admin_flow(self):
        from database import get_db_connection
        home=self.client.get('/')
        self.assertEqual(home.status_code,200)
        self.assertIn('Без регистрации',home.text)
        token=self.token(self.enter())
        row=self.private(token).json
        sid=row['id']
        self.assertEqual(row['license_plate'],'777ABC02')
        self.assertEqual(row['client_phone'],'+77771234567')
        self.assertEqual(row['fee'],0)
        self.assertEqual(row['status'],'active')
        self.assertEqual(len(self.client.get('/api/spots/available').json),3)
        self.assertEqual(self.client.get('/api/spots').json['stats']['occupied'],1)
        first_entry=row['entry_time']
        self.client.get('/my-parking/'+token)
        self.assertEqual(self.private(token).json['entry_time'],first_entry)
        fresh=self.app.test_client()
        self.assertEqual(fresh.get('/api/parking/my-session',headers={'Authorization':'Bearer '+token}).json['id'],sid)
        conn=get_db_connection()
        stored=conn.execute('SELECT access_token_hash FROM parking_sessions WHERE id=?',(sid,)).fetchone()[0]
        conn.close()
        self.assertNotEqual(stored,token)
        self.assertEqual(len(stored),64)
        self.assertEqual(self.enter(spot=2,plate='888ABC02').status_code,409)
        self.assertEqual(self.enter(spot=3).status_code,409)
        self.assertEqual(self.admin.get('/api/admin/sessions?q=777abc').json['sessions'][0]['id'],sid)
        self.assertEqual(self.post(self.admin,f'/api/admin/sessions/{sid}/message',{'body':'Пожалуйста, освободите парковочное место.'}).status_code,201)
        messages=self.private(token,'my-messages').json
        self.assertIn('освободите',messages[0]['body'])
        self.age(sid,61*60)
        self.assertEqual(self.private(token).json['fee'],100)
        self.age(sid,121*60)
        self.assertEqual(self.private(token).json['fee'],200)
        exited=self.post(self.admin,f'/api/admin/sessions/{sid}/exit',{})
        self.assertEqual(exited.status_code,200)
        self.assertEqual(exited.json['final_fee'],200)
        self.assertEqual(len(self.client.get('/api/spots/available').json),4)
        history=self.private(token).json
        self.assertEqual(history['status'],'completed')
        self.assertIsNotNone(history['exit_time'])
        self.assertEqual(history['final_fee'],200)
        self.assertEqual(self.post(self.admin,f'/api/admin/sessions/{sid}/exit',{}).status_code,409)
        self.assertEqual(len(self.admin.get('/api/admin/sessions?status=completed').json['sessions']),1)
        self.assertEqual(self.enter(spot=2).status_code,201)

    def test_fee_boundaries(self):
        from parking_portal import calculate_fee
        for seconds,expected in [(0,0),(30*60,0),(60*60,0),(60*60+0.01,100),(61*60,100),(120*60,100),(120*60+0.01,200),(121*60,200)]:
            self.assertEqual(calculate_fee(seconds),expected)
            self.assertEqual(calculate_fee(seconds,eligibility='confirmed'),0)
        self.assertEqual(calculate_fee(3600,30,250),250)
        self.assertEqual(calculate_fee(7200,0,50),100)

    def test_delete_only_completed_session_preserves_occupied_space_and_vehicle(self):
        from database import get_db_connection
        token=self.token(self.enter())
        sid=self.private(token).json['id']
        self.assertEqual(self.delete_record(sid).status_code,409)
        self.assertEqual(self.post(self.admin,f'/api/admin/sessions/{sid}/exit',{}).status_code,200)
        current=self.token(self.enter())  # Same vehicle and space are occupied again.
        current_id=self.private(current).json['id']
        conn=get_db_connection()
        current_before=tuple(conn.execute('SELECT * FROM parking_sessions WHERE id=?',(current_id,)).fetchone())
        vehicle_count=conn.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0]
        conn.close()
        self.assertEqual(self.delete_record(sid,payload={}).status_code,400)
        self.assertEqual(self.delete_record(sid,payload={'confirm':False}).status_code,400)
        self.assertEqual(self.delete_record(sid,csrf=False).status_code,403)
        guest=self.app.test_client();guest.get('/')
        self.assertEqual(self.delete_record(sid,client=guest).status_code,401)
        self.assertEqual(self.delete_record(sid).status_code,200)
        self.assertEqual(self.delete_record(sid).status_code,404)
        self.assertEqual(self.private(token).status_code,404)
        self.assertEqual(self.private(current).json['status'],'active')
        conn=get_db_connection()
        self.assertEqual(tuple(conn.execute('SELECT * FROM parking_sessions WHERE id=?',(current_id,)).fetchone()),current_before)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0],vehicle_count)
        self.assertEqual(conn.execute('SELECT status FROM parking_slots WHERE id=2').fetchone()[0],'booked')
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM parking_messages WHERE session_id=?',(sid,)).fetchone()[0],0)
        self.assertGreater(conn.execute('SELECT COUNT(*) FROM parking_messages WHERE session_id=?',(current_id,)).fetchone()[0],0)
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        conn.close()
        self.assertTrue(list(Path(self.directory.name).glob('*.before-session-delete-*.bak')))

    def test_legacy_delete_is_not_reimported_and_source_booking_is_preserved(self):
        from database import get_db_connection
        from parking_portal import initialize
        token=self.token(self.enter())
        sid=self.private(token).json['id']
        self.post(self.admin,f'/api/admin/sessions/{sid}/exit',{})
        legacy_id=self.mark_legacy(sid)
        conn=get_db_connection()
        original=tuple(conn.execute('SELECT * FROM parking_bookings WHERE id=?',(legacy_id,)).fetchone())
        conn.close()
        self.assertEqual(self.delete_record(sid).status_code,200)
        initialize();initialize()
        conn=get_db_connection()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM parking_sessions WHERE legacy_booking_id=?',(legacy_id,)).fetchone()[0],0)
        self.assertEqual(tuple(conn.execute('SELECT * FROM parking_bookings WHERE id=?',(legacy_id,)).fetchone()),original)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM deleted_parking_records WHERE legacy_booking_id=?',(legacy_id,)).fetchone()[0],1)
        conn.close()

    def test_deleted_highest_id_is_not_reused_by_new_parking(self):
        token=self.token(self.enter())
        sid=self.private(token).json['id']
        self.client_action(token)
        self.assertEqual(self.delete_record(sid).status_code,200)
        next_token=self.token(self.enter())
        self.assertGreater(self.private(next_token).json['id'],sid)
        self.assertEqual(self.delete_record(sid).status_code,404)
        self.assertEqual(self.private(next_token).json['status'],'active')

    def test_invalid_history_dates_and_unverified_legacy_are_read_only(self):
        from database import get_db_connection
        from parking_portal import utcnow
        token=self.token(self.enter())
        sid=self.private(token).json['id']
        start=utcnow()-timedelta(hours=1)
        conn=get_db_connection()
        with conn:
            conn.execute("UPDATE parking_sessions SET status='completed',entry_time=?,exit_time=?,final_fee=99999999 WHERE id=?",
                         (start.isoformat(),(start-timedelta(hours=2)).isoformat(),sid))
        before=tuple(conn.execute('SELECT * FROM parking_sessions WHERE id=?',(sid,)).fetchone())
        conn.close()
        row=self.private(token).json
        self.assertEqual(row['data_quality'],'invalid')
        self.assertEqual(row['duration_status'],'invalid')
        self.assertIsNone(row['duration_seconds'])
        self.assertIsNone(row['fee'])
        conn=get_db_connection()
        self.assertEqual(tuple(conn.execute('SELECT * FROM parking_sessions WHERE id=?',(sid,)).fetchone()),before)
        with conn:
            conn.execute('UPDATE parking_sessions SET entry_time=? WHERE id=?',('not-a-date',sid))
        conn.close()
        self.assertEqual(self.private(token).json['data_quality'],'invalid')
        active=self.token(self.enter(spot=3,plate='123DEF01'))
        aid=self.private(active).json['id']
        self.age(aid,400*86400)
        self.mark_legacy(aid)
        legacy=self.private(active).json
        self.assertTrue(legacy['is_legacy'])
        self.assertEqual(legacy['duration_status'],'unverified')
        self.assertIsNone(legacy['fee'])
        self.assertIsNone(legacy['duration_seconds'])
        totals=self.admin.get('/api/admin/sessions').json['stats']
        self.assertEqual(totals['total_fees_due'],0)
        self.assertEqual(totals['unverified_fees'],1)
        self.assertEqual(self.private(active).json['status'],'active')
        placeholder=self.token(self.enter(spot=4,plate='456DEF01'))
        pid=self.private(placeholder).json['id']
        self.client_action(placeholder)
        booking_id=self.mark_legacy(pid)
        conn=get_db_connection()
        with conn:
            conn.execute('UPDATE parking_bookings SET unbooking_time=NULL WHERE id=?',(booking_id,))
            conn.execute('UPDATE parking_sessions SET exit_time=entry_time WHERE id=?',(pid,))
        conn.close()
        self.assertEqual(self.private(placeholder).json['duration_status'],'invalid')
        self.assertIsNone(self.private(placeholder).json['duration_seconds'])

    def test_long_legacy_plate_is_preserved_but_new_long_plate_rejected(self):
        from database import get_db_connection
        token=self.token(self.enter())
        sid=self.private(token).json['id']
        plate='123ABC'+'Z'*250
        conn=get_db_connection()
        with conn:
            conn.execute('UPDATE vehicles SET license_plate=? WHERE id=(SELECT vehicle_id FROM parking_sessions WHERE id=?)',(plate,sid))
        conn.close()
        rows=self.admin.get('/api/admin/sessions?q=123ABC').json['sessions']
        self.assertEqual(rows[0]['license_plate'],plate)
        self.assertEqual(self.enter(spot=3,plate=plate).status_code,400)
        self.assertEqual(self.private(token).json['license_plate'],plate)
        page=self.admin.get('/admin')
        self.assertIn('confirmation-dialog',page.text)
        self.assertIn('Удалить запись',page.text)
        self.assertIn('Завершить парковку',page.text)
        self.assertIn('dialog-scroll',page.text)

    def test_client_exit_saves_fee_releases_spot_and_keeps_history(self):
        token=self.token(self.enter())
        page=self.client.get('/my-parking/'+token)
        self.assertEqual(page.status_code,200)
        for label in ['Завершить парковку','Отменить парковку','Подтвердить выезд','Вы уверены, что хотите завершить парковку?']:
            self.assertIn(label,page.text)
        self.age(self.private(token).json['id'],61*60)
        response=self.client_action(token)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['final_fee'],100)
        row=response.json['session']
        self.assertEqual(row['status'],'completed')
        self.assertIsNotNone(row['exit_time'])
        self.assertGreaterEqual(row['duration_seconds'],61*60)
        self.assertLess(row['duration_seconds'],62*60)
        self.assertEqual(len(self.client.get('/api/spots/available').json),4)
        self.assertEqual(self.private(token).json['final_fee'],100)
        self.assertEqual(len(self.admin.get('/api/admin/sessions?status=completed').json['sessions']),1)
        self.assertEqual(self.client_action(token).status_code,409)
        self.assertEqual(self.post(self.admin,f"/api/admin/sessions/{row['id']}/exit",{}).status_code,409)
        self.assertIn('Парковка завершена',self.private(token,'my-messages').json[0]['body'])

    def test_client_exit_token_ownership_csrf_and_cancellation(self):
        from database import get_db_connection
        token=self.token(self.enter())
        other=self.token(self.enter(spot=3,plate='123DEF01'))
        own=self.private(token).json
        other_id=self.private(other).json['id']
        self.age(own['id'],61*60)
        conn=get_db_connection()
        before=tuple(conn.execute('SELECT * FROM parking_sessions WHERE id=?',(own['id'],)).fetchone())
        conn.close()
        cancelled=self.client_action(token,'cancel')
        self.assertEqual(cancelled.status_code,409)
        self.assertEqual(cancelled.json['error'],'Для отмены активной парковки обратитесь к администратору.')
        conn=get_db_connection()
        self.assertEqual(tuple(conn.execute('SELECT * FROM parking_sessions WHERE id=?',(own['id'],)).fetchone()),before)
        conn.close()
        self.assertEqual(self.private(token).json['fee'],100)
        self.assertEqual(len(self.client.get('/api/spots/available').json),2)
        for invalid in ['', 'invalid', 'a'*43]:
            self.assertEqual(self.client_action(invalid).status_code,404)
            self.assertEqual(self.client_action(invalid,'cancel').status_code,404)
        self.assertEqual(self.client.post('/api/parking/my-session/exit',json={},headers={'Authorization':'Bearer '+token}).status_code,403)
        # Arbitrary IDs in JSON cannot select another client's session.
        result=self.client_action(token,payload={'id':other_id,'session_id':other_id})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['session']['id'],own['id'])
        self.assertEqual(self.private(other).json['status'],'active')
        self.assertEqual(self.client_action(token,'cancel').status_code,409)

    def test_concurrent_client_and_admin_exit_only_completes_once(self):
        token=self.token(self.enter())
        sid=self.private(token).json['id']
        with self.client.session_transaction() as s:
            client_csrf=s['csrf_token']
        with self.admin.session_transaction() as s:
            admin_csrf=s['csrf_token']
        client_cookie=self.client.get_cookie('session').value
        admin_cookie=self.admin.get_cookie('parking_admin').value
        admin_session_cookie=self.admin.get_cookie('session').value
        barrier=Barrier(2)
        def exit_once(kind):
            client=self.app.test_client()
            if kind=='client':
                client.set_cookie('session',client_cookie)
                headers={'X-CSRF-Token':client_csrf,'Authorization':'Bearer '+token}
                url='/api/parking/my-session/exit'
            else:
                client.set_cookie('session',admin_session_cookie)
                client.set_cookie('parking_admin',admin_cookie)
                headers={'X-CSRF-Token':admin_csrf}
                url=f'/api/admin/sessions/{sid}/exit'
            barrier.wait()
            return client.post(url,json={},headers=headers).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(exit_once,['client','admin']))
        self.assertEqual(sorted(statuses),[200,409])
        self.assertEqual(self.private(token).json['status'],'completed')
        self.assertEqual(len(self.client.get('/api/spots/available').json),4)
        messages=self.private(token,'my-messages').json
        self.assertEqual(sum('Парковка завершена' in m['body'] for m in messages),1)

    def test_accessibility_verification(self):
        self.assertEqual(self.enter(spot=1).status_code,400)
        token=self.token(self.enter(spot=1,accessible=True))
        row=self.private(token).json
        sid=row['id']
        self.assertEqual(row['eligibility'],'pending')
        self.age(sid,4.5*3600)
        self.assertEqual(self.private(token).json['fee'],500-100)
        self.assertEqual(self.post(self.admin,f'/api/admin/sessions/{sid}/verify-accessibility',{'confirmed':False}).status_code,200)
        self.assertEqual(self.private(token).json['eligibility'],'rejected')
        self.assertGreater(self.private(token).json['fee'],0)
        self.assertEqual(self.post(self.admin,f'/api/admin/sessions/{sid}/verify-accessibility',{'confirmed':True}).status_code,200)
        self.assertEqual(self.private(token).json['fee'],0)
        self.assertEqual(self.private(token).json['eligibility'],'confirmed')
        self.assertIn('подтверждено',self.private(token,'my-messages').json[0]['body'])
        self.assertEqual(self.post(self.admin,f'/api/admin/sessions/{sid}/exit',{}).json['final_fee'],0)

    def test_tariff_snapshot_and_space_management(self):
        token=self.token(self.enter())
        self.assertEqual(self.post(self.admin,'/api/admin/tariff',{'free_minutes':30,'hourly_fee':250}).status_code,200)
        self.assertEqual(self.private(token).json['hourly_fee'],100)
        token2=self.token(self.enter(spot=3,plate='123DEF01'))
        self.assertEqual(self.private(token2).json['hourly_fee'],250)
        self.assertEqual(self.private(token2).json['free_minutes'],30)
        self.assertEqual(self.post(self.admin,'/api/admin/spots/2',{'is_accessible':True}).status_code,409)
        self.assertEqual(self.post(self.admin,'/api/admin/spots/4',{'is_accessible':True}).status_code,200)
        self.assertTrue(self.client.get('/api/spots').json['spots'][3]['is_accessible'])
        self.assertEqual(self.post(self.admin,'/api/admin/lots',{'name':'Новая','address':'Адрес','total_spots':3,'accessible_spots':1}).status_code,201)
        self.assertEqual(self.client.get('/api/spots').json['stats']['total'],7)

    def test_authorization_and_privacy(self):
        token=self.token(self.enter())
        public=self.app.test_client()
        public.get('/')
        for url in ['/api/admin/sessions','/api/admin/sessions/1/messages']:
            self.assertEqual(public.get(url).status_code,401)
        for suffix,payload in [('exit',{}),('message',{'body':'Недопустимо'}),('verify-accessibility',{'confirmed':True})]:
            self.assertEqual(self.post(public,'/api/admin/sessions/1/'+suffix,payload).status_code,401)
        with public.session_transaction() as s:
            s['admin_id']=1
        self.assertEqual(public.get('/api/admin/sessions').status_code,401)
        self.assertEqual(public.get('/admin').status_code,302)
        for url in ['/api/spots','/api/spots/available']:
            body=public.get(url).text
            for sensitive in ['Иван','777ABC02','77771234567','access_token_hash','client_phone','client_full_name']:
                self.assertNotIn(sensitive,body)
        self.assertEqual(public.get('/api/dashboard').status_code,410)
        self.assertEqual(self.private('a'*43).status_code,404)
        self.assertEqual(public.get('/api/parking/my-session').status_code,404)
        self.assertEqual(self.client.post('/api/parking/enter',json={}).status_code,403)
        self.assertEqual(self.admin.post('/api/admin/sessions/1/exit',json={}).status_code,403)
        self.assertEqual(self.post(self.admin,'/api/admin/logout',{}).status_code,200)
        self.assertEqual(self.admin.get('/api/admin/sessions').status_code,401)
        self.assertEqual(self.private(token).status_code,200)

    def test_rate_limit_and_input_validation(self):
        p={'full_name':'А','phone':'not-a-phone','license_plate':'<script>','spot_id':2}
        self.assertEqual(self.post(self.client,'/api/parking/enter',p).status_code,400)
        self.assertEqual(self.post(self.client,'/api/parking/enter',[]).status_code,400)
        self.assertEqual(self.post(self.admin,'/api/admin/tariff',{'free_minutes':-1,'hourly_fee':100}).status_code,400)
        bad=self.app.test_client()
        bad.get('/admin/login')
        for _ in range(9):
            self.assertEqual(self.post(bad,'/api/admin/login',{'username':'missing','password':self.password}).status_code,401)
        self.assertEqual(self.post(bad,'/api/admin/login',{'username':'missing','password':self.password}).status_code,429)

    def test_simultaneous_spot_booking(self):
        barrier=Barrier(2)
        def book(plate):
            client=self.app.test_client()
            client.get('/')
            barrier.wait()
            return self.enter(client=client,spot=2,plate=plate).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(book,['111ABC01','222ABC01']))
        self.assertEqual(sorted(statuses),[201,409])
        self.assertEqual(self.client.get('/api/spots').json['stats']['occupied'],1)
        self.assertEqual(len(self.admin.get('/api/admin/sessions').json['sessions']),1)

    def test_admin_setup_revokes_old_sessions(self):
        from manage import main
        from database import get_db_connection
        replacement=secrets.token_urlsafe(20)
        with patch.object(sys,'argv',['manage.py','create-admin','--username',self.username]), patch('getpass.getpass',side_effect=[replacement,replacement]), patch('sys.stdout',new_callable=io.StringIO):
            main()
        self.assertEqual(self.admin.get('/api/admin/sessions').status_code,401)
        login=self.app.test_client()
        login.get('/admin/login')
        self.assertEqual(self.post(login,'/api/admin/login',{'username':self.username,'password':self.password}).status_code,401)
        self.assertEqual(self.post(login,'/api/admin/login',{'username':self.username,'password':replacement}).status_code,200)
        login.get('/admin')
        conn=get_db_connection()
        row=conn.execute('SELECT password_hash FROM portal_admins WHERE username=?',(self.username,)).fetchone()
        self.assertTrue(row['password_hash'].startswith('scrypt:'))
        self.assertNotEqual(row['password_hash'],replacement)
        from parking_portal import utcnow
        with conn:
            conn.execute('UPDATE admin_sessions SET expires_at=?',((utcnow()-timedelta(seconds=1)).isoformat(),))
        conn.close()
        self.assertEqual(login.get('/api/admin/sessions').status_code,401)

    def test_existing_history_and_idempotent_migration(self):
        from database import get_db_connection,init_db
        from parking_portal import initialize
        old_path=os.environ['PARKING_DATABASE']
        copy_path=str(Path(self.directory.name)/'original.db')
        shutil.copyfile(ROOT/'parking_app.db',copy_path)
        os.environ['PARKING_DATABASE']=copy_path
        try:
            conn=get_db_connection()
            bookings=[tuple(r) for r in conn.execute('SELECT id,booking_time,unbooking_time FROM parking_bookings ORDER BY id')]
            sessions=[tuple(r) for r in conn.execute('SELECT id,vehicle_id,spot_id,entry_time,exit_time,status FROM parking_sessions ORDER BY id')]
            conn.close()
            init_db();initialize();initialize()
            conn=get_db_connection()
            self.assertEqual([tuple(r) for r in conn.execute('SELECT id,booking_time,unbooking_time FROM parking_bookings ORDER BY id')],bookings)
            self.assertEqual([tuple(r) for r in conn.execute('SELECT id,vehicle_id,spot_id,entry_time,exit_time,status FROM parking_sessions ORDER BY id')],sessions)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            conn.close()
        finally:
            os.environ['PARKING_DATABASE']=old_path

if __name__=='__main__':
    unittest.main()
