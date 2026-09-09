import json, sqlite3, os, uuid, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from email.parser import BytesParser
from email.policy import default
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'data.db')
UP = os.path.join(ROOT, 'uploads')
os.makedirs(UP, exist_ok=True)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'mohasat9526')
DATABASE_URL = os.environ.get('DATABASE_URL')

# فئة مخصصة لدعم الوصول للنتائج بأسماء الأعمدة (مثل SQLite Row)
class PGRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

# دالة مخصصة لإدارة الاتصالات بقواعد البيانات
class DBWrapper:
    def __init__(self):
        if DATABASE_URL:
            import psycopg2
            import psycopg2.extras
            self.db_type = 'postgres'
            self.conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        else:
            self.db_type = 'sqlite'
            self.conn = sqlite3.connect(DB)
            self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        if self.db_type == 'postgres':
            import psycopg2.extras
            sql_pg = sql.replace('?', '%s')
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql_pg, params)
            return cur
        else:
            return self.conn.execute(sql, params)

    def executescript(self, script):
        if self.db_type == 'postgres':
            cur = self.conn.cursor()
            cur.execute(script)
        else:
            self.conn.executescript(script)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def db():
    return DBWrapper()

def init():
    c = db()
    if c.db_type == 'postgres':
        c.executescript('''
        CREATE TABLE IF NOT EXISTS clients(id SERIAL PRIMARY KEY, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL, password TEXT NOT NULL, phone TEXT, email TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS albums(id SERIAL PRIMARY KEY, client_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(client_id) REFERENCES clients(id));
        CREATE TABLE IF NOT EXISTS photos(id SERIAL PRIMARY KEY, album_id INTEGER NOT NULL, filename TEXT NOT NULL, original_name TEXT, created_at TEXT NOT NULL, FOREIGN KEY(album_id) REFERENCES albums(id));
        CREATE TABLE IF NOT EXISTS orders(id SERIAL PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, client_id INTEGER, customer_name TEXT NOT NULL, phone TEXT, email TEXT, service TEXT NOT NULL, size TEXT, quantity INTEGER DEFAULT 1, notes TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(client_id) REFERENCES clients(id));
        CREATE TABLE IF NOT EXISTS order_photos(id SERIAL PRIMARY KEY, order_id INTEGER NOT NULL, filename TEXT NOT NULL, original_name TEXT, FOREIGN KEY(order_id) REFERENCES orders(id));
        CREATE TABLE IF NOT EXISTS messages(id SERIAL PRIMARY KEY, order_id INTEGER, client_id INTEGER, message TEXT NOT NULL, from_admin INTEGER DEFAULT 1, created_at TEXT NOT NULL, FOREIGN KEY(order_id) REFERENCES orders(id));
        ''')
    else:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL, password TEXT NOT NULL, phone TEXT, email TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS albums(id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(client_id) REFERENCES clients(id));
        CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY AUTOINCREMENT, album_id INTEGER NOT NULL, filename TEXT NOT NULL, original_name TEXT, created_at TEXT NOT NULL, FOREIGN KEY(album_id) REFERENCES albums(id));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE NOT NULL, client_id INTEGER, customer_name TEXT NOT NULL, phone TEXT, email TEXT, service TEXT NOT NULL, size TEXT, quantity INTEGER DEFAULT 1, notes TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(client_id) REFERENCES clients(id));
        CREATE TABLE IF NOT EXISTS order_photos(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, filename TEXT NOT NULL, original_name TEXT, FOREIGN KEY(order_id) REFERENCES orders(id));
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, client_id INTEGER, message TEXT NOT NULL, from_admin INTEGER DEFAULT 1, created_at TEXT NOT NULL, FOREIGN KEY(order_id) REFERENCES orders(id));
        ''')
    c.commit()
    
    cnt = c.execute('SELECT COUNT(*) AS n FROM clients').fetchone()
    if cnt['n'] == 0:
        now = datetime.now().isoformat(timespec='seconds')
        if c.db_type == 'postgres':
            cur = c.execute('INSERT INTO clients(name,code,password,phone,email,created_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id',
                            ('عميل تجريبي', 'BM-2026-001', '1234', '0555000000', 'demo@example.com', now))
            cid = cur.fetchone()['id']
            c.execute('INSERT INTO albums(client_id,name,created_at) VALUES(%s,%s,%s)', (cid, 'ألبوم تجريبي', now))
        else:
            cur = c.execute('INSERT INTO clients(name,code,password,phone,email,created_at) VALUES(?,?,?,?,?,?)',
                            ('عميل تجريبي', 'BM-2026-001', '1234', '0555000000', 'demo@example.com', now))
            cid = cur.lastrowid
            c.execute('INSERT INTO albums(client_id,name,created_at) VALUES(?,?,?)', (cid, 'ألبوم تجريبي', now))
    c.commit()
    c.close()

def save_upload(data, original):
    ext = os.path.splitext(original or '')[1].lower()[:10]
    if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.pdf']:
        ext = '.jpg'
    name = uuid.uuid4().hex + ext
    with open(os.path.join(UP, name), 'wb') as f:
        f.write(data)
    return name

def json_bytes(obj): 
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')

def rowdict(r): 
    if not r: 
        return None
    d = dict(r)
    d.pop('password', None)
    return d

class H(BaseHTTPRequestHandler):
    def send(self, code=200, obj=None, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        if obj is not None:
            self.wfile.write(json_bytes(obj) if ctype.startswith('application/json') else obj)

    def body(self):
        try:
            n = int(self.headers.get('Content-Length', '0'))
            return self.rfile.read(n)
        except Exception:
            return b''

    def postjson(self):
        try:
            return json.loads(self.body().decode('utf-8') or '{}')
        except Exception:
            return {}

    def multipart(self):
        ct = self.headers.get('Content-Type', '')
        raw = self.body()
        head = f'Content-Type: {ct}\r\nMIME-Version: 1.0\r\n\r\n'.encode('utf-8')
        msg = BytesParser(policy=default).parsebytes(head + raw)
        fields = {}
        files = []
        for p in msg.iter_parts():
            fn = p.get_filename()
            name = p.get_param('name', header='content-disposition')
            data = p.get_payload(decode=True) or b''
            if fn:
                files.append((name, fn, data, p.get_content_type()))
            elif name:
                fields[name] = data.decode('utf-8', 'ignore')
        return fields, files

    def do_OPTIONS(self): 
        self.send(204)

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        c = db()
        try:
            if p == '/': return self.static('index.html')
            if p == '/admin': return self.static('admin.html')
            if p.startswith('/uploads/'):
                fn = os.path.basename(p)
                path = os.path.join(UP, fn)
                if not os.path.exists(path): 
                    return self.send(404, {'error': 'الطلب غير موجود'})
                with open(path, 'rb') as f:
                    data = f.read()
                return self.send(200, data, mimetypes.guess_type(path)[0] or 'application/octet-stream')

            if p == '/api/stats':
                o = c.execute("SELECT COUNT(*) AS n FROM orders").fetchone()['n']
                clients = c.execute("SELECT COUNT(*) AS n FROM clients").fetchone()['n']
                ready = c.execute("SELECT COUNT(*) AS n FROM orders WHERE status='جاهز'").fetchone()['n']
                pending = c.execute("SELECT COUNT(*) AS n FROM orders WHERE status IN ('جديد','قيد المعالجة')").fetchone()['n']
                return self.send(obj={'orders': o, 'clients': clients, 'ready': ready, 'pending': pending})

            if p == '/api/orders':
                rows = c.execute('SELECT o.*, COALESCE(cl.code, \'زائر\') AS code FROM orders o LEFT JOIN clients cl ON cl.id=o.client_id ORDER BY o.id DESC').fetchall()
                return self.send(obj=[rowdict(r) for r in rows])

            if p == '/api/clients':
                rows = c.execute('SELECT id, name, code, phone, email, created_at FROM clients ORDER BY id DESC').fetchall()
                return self.send(obj=[rowdict(r) for r in rows])

            if p == '/api/client-login':
                code = q.get('code', [''])[0]
                pw = q.get('password', [''])[0]
                r = c.execute('SELECT id, name, code, phone, email, created_at FROM clients WHERE code=? AND password=?', (code, pw)).fetchone()
                if not r:
                    return self.send(401, {'error': 'بيانات الدخول غير صحيحة'})
                albums = []
                for a in c.execute('SELECT * FROM albums WHERE client_id=? ORDER BY id DESC', (r['id'],)).fetchall():
                    ar = rowdict(a)
                    ar['photos'] = [rowdict(x) for x in c.execute('SELECT * FROM photos WHERE album_id=? ORDER BY id DESC', (a['id'],)).fetchall()]
                    albums.append(ar)
                orders = [rowdict(x) for x in c.execute('SELECT * FROM orders WHERE client_id=? ORDER BY id DESC', (r['id'],)).fetchall()]
                msgs = [rowdict(x) for x in c.execute('SELECT * FROM messages WHERE client_id=? ORDER BY id DESC', (r['id'],)).fetchall()]
                return self.send(obj={'client': rowdict(r), 'albums': albums, 'orders': orders, 'messages': msgs})

            if p == '/api/track':
                no = q.get('order', [''])[0]
                r = c.execute('SELECT * FROM orders WHERE order_no=?', (no,)).fetchone()
                if not r:
                    return self.send(404, {'error': 'رقم الطلب غير موجود'})
                msgs = [rowdict(x) for x in c.execute('SELECT * FROM messages WHERE order_id=? ORDER BY id DESC', (r['id'],)).fetchall()]
                return self.send(obj={'order': rowdict(r), 'messages': msgs})

            if p == '/api/order-photos':
                oid = q.get('id', ['0'])[0]
                rows = c.execute('SELECT * FROM order_photos WHERE order_id=?', (int(oid),)).fetchall()
                return self.send(obj=[rowdict(r) for r in rows])

            if p == '/api/albums':
                rows = c.execute('SELECT a.*, cl.name AS client_name, cl.code FROM albums a JOIN clients cl ON cl.id=a.client_id ORDER BY a.id DESC').fetchall()
                return self.send(obj=[rowdict(r) for r in rows])

            return self.static(p.lstrip('/'))
        finally:
            c.close()

    def static(self, fn):
        path = os.path.join(ROOT, fn or 'index.html')
        if not os.path.isfile(path): 
            return self.send(404, {'error': 'الصفحة غير موجودة'})
        with open(path, 'rb') as f:
            data = f.read()
        self.send(200, data, mimetypes.guess_type(path)[0] or 'text/html; charset=utf-8')

    def do_POST(self):
        p = urlparse(self.path).path
        c = db()
        try:
            if p == '/api/admin-login':
                d = self.postjson()
                if d.get('password') == ADMIN_PASSWORD:
                    return self.send(obj={'ok': True, 'token': 'admin-session-ok'})
                return self.send(401, {'error': 'كلمة المرور غير صحيحة'})

            if p == '/api/orders':
                ct = self.headers.get('Content-Type', '')
                if ct.startswith('multipart/form-data'):
                    fields, files = self.multipart()
                elif ct.startswith('application/x-www-form-urlencoded'):
                    fields = {k: v[0] for k, v in parse_qs(self.body().decode('utf-8', 'ignore')).items()}
                    files = []
                else:
                    fields = self.postjson()
                    files = []

                now = datetime.now().isoformat(timespec='seconds')
                no = 'BM-' + datetime.now().strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:6].upper()
                cid = fields.get('client_id') or None
                cid = int(cid) if cid else None
                name = fields.get('customer_name') or fields.get('name') or 'زبون'
                phone = fields.get('phone', '')
                email = fields.get('email', '')
                service = fields.get('service', 'طباعة')
                size = fields.get('size', '')
                qty = int(fields.get('quantity', 1) or 1)
                notes = fields.get('notes', '')

                if c.db_type == 'postgres':
                    cur = c.execute('INSERT INTO orders(order_no,client_id,customer_name,phone,email,service,size,quantity,notes,status,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                                    (no, cid, name, phone, email, service, size, qty, notes, 'جديد', now, now))
                    oid = cur.fetchone()['id']
                else:
                    cur = c.execute('INSERT INTO orders(order_no,client_id,customer_name,phone,email,service,size,quantity,notes,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (no, cid, name, phone, email, service, size, qty, notes, 'جديد', now, now))
                    oid = cur.lastrowid

                selected = fields.get('selected_photo_ids', '')
                if selected and cid:
                    try:
                        ids = [int(x) for x in selected.split(',') if x.strip()]
                    except Exception:
                        ids = []
                    for pid in ids:
                        pr = c.execute('SELECT p.* FROM photos p JOIN albums a ON a.id=p.album_id WHERE p.id=? AND a.client_id=?', (pid, cid)).fetchone()
                        if pr:
                            c.execute('INSERT INTO order_photos(order_id,filename,original_name) VALUES(?,?,?)', (oid, pr['filename'], pr['original_name']))

                for _, fn, data, _ in files:
                    if data:
                        c.execute('INSERT INTO order_photos(order_id,filename,original_name) VALUES(?,?,?)', (oid, save_upload(data, fn), fn))

                if cid:
                    c.execute('INSERT INTO messages(order_id,client_id,message,from_admin,created_at) VALUES(?,?,?,?,?)', (oid, cid, 'تم استلام طلبك بنجاح. سنراجع ونحدّث حالته هنا.', 1, now))
                
                c.commit()
                return self.send(obj={'ok': True, 'order_no': no, 'id': oid})

            if p == '/api/status':
                d = self.postjson()
                now = datetime.now().isoformat(timespec='seconds')
                c.execute('UPDATE orders SET status=?, updated_at=? WHERE id=?', (d.get('status'), now, int(d.get('id'))))
                c.commit()
                return self.send(obj={'ok': True})

            if p == '/api/message':
                d = self.postjson()
                now = datetime.now().isoformat(timespec='seconds')
                c.execute('INSERT INTO messages(order_id,client_id,message,from_admin,created_at) VALUES(?,?,?,?,?)', (d.get('order_id'), d.get('client_id'), d.get('message', ''), 1, now))
                c.commit()
                return self.send(obj={'ok': True})

            if p == '/api/clients':
                d = self.postjson()
                now = datetime.now().isoformat(timespec='seconds')
                if c.db_type == 'postgres':
                    cur = c.execute('INSERT INTO clients(name,code,password,phone,email,created_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id', (d.get('name'), d.get('code'), d.get('password'), d.get('phone', ''), d.get('email', ''), now))
                    nid = cur.fetchone()['id']
                else:
                    cur = c.execute('INSERT INTO clients(name,code,password,phone,email,created_at) VALUES(?,?,?,?,?,?)', (d.get('name'), d.get('code'), d.get('password'), d.get('phone', ''), d.get('email', ''), now))
                    nid = cur.lastrowid
                c.commit()
                return self.send(obj={'ok': True, 'id': nid})

            if p == '/api/albums':
                d = self.postjson()
                now = datetime.now().isoformat(timespec='seconds')
                if c.db_type == 'postgres':
                    cur = c.execute('INSERT INTO albums(client_id,name,created_at) VALUES(%s,%s,%s) RETURNING id', (int(d['client_id']), d['name'], now))
                    nid = cur.fetchone()['id']
                else:
                    cur = c.execute('INSERT INTO albums(client_id,name,created_at) VALUES(?,?,?)', (int(d['client_id']), d['name'], now))
                    nid = cur.lastrowid
                c.commit()
                return self.send(obj={'ok': True, 'id': nid})

            if p == '/api/photos':
                fields, files = self.multipart()
                aid = int(fields['album_id'])
                ids = []
                for _, fn, data, _ in files:
                    if data:
                        name = save_upload(data, fn)
                        now_str = datetime.now().isoformat(timespec='seconds')
                        if c.db_type == 'postgres':
                            cur = c.execute('INSERT INTO photos(album_id,filename,original_name,created_at) VALUES(%s,%s,%s,%s) RETURNING id', (aid, name, fn, now_str))
                            ids.append(cur.fetchone()['id'])
                        else:
                            cur = c.execute('INSERT INTO photos(album_id,filename,original_name,created_at) VALUES(?,?,?,?)', (aid, name, fn, now_str))
                            ids.append(cur.lastrowid)
                c.commit()
                return self.send(obj={'ok': True, 'ids': ids})

            return self.send(404, {'error': 'API غير موجود'})
        finally:
            c.close()

if __name__ == '__main__':
    init()
    host = '0.0.0.0'
    port = int(os.environ.get('PORT', 8000))
    print(f'BAIDJI MOHA PHOTO running on port {port}')
    ThreadingHTTPServer((host, port), H).serve_forever()
