"""PostgreSQL storage backend (Dev 8). Database is local to the existing LXC.

The lightweight adapter preserves the established query/cursor interface while all
writes use PostgreSQL transactions. Connections are closed on every operation.
"""
import os, time, json, re
from pathlib import Path
from contextlib import contextmanager

DB_DSN = os.environ.get('UPWATCH_POSTGRES_DSN', 'dbname=upwatch user=upwatch host=/var/run/postgresql')
SCHEMA_PATH = Path(__file__).with_name('postgres_schema.sql')

class Row:
    def __init__(self, columns, values):
        self._columns=tuple(columns)
        self._values=tuple(values)
        self._data=dict(zip(columns,values))
    def __getitem__(self,key):
        return self._values[key] if isinstance(key,int) else self._data[key]
    def keys(self):return self._data.keys()
    def __iter__(self):return iter(self._values)

# Defensive normalization for textual OIDs (binary fields must remain bytes).
# An SQL_ASCII database is not permitted: see assert_utf8().
_TEXT_OIDS = {18, 19, 25, 1042, 1043}  # char, name, text, bpchar, varchar

def _normalize_pg_value(value, type_code):
    if isinstance(value, (bytes, bytearray, memoryview)) and type_code in _TEXT_OIDS:
        return bytes(value).decode('utf-8')
    return value

class CursorAdapter:
    def __init__(self, cursor):
        self.cursor=cursor
        self.rowcount=cursor.rowcount
    def _wrap(self,value):
        if value is None:return None
        cols=self.cursor.description
        return Row([d.name for d in cols],
                   [_normalize_pg_value(v, getattr(d,'type_code',None)) for v,d in zip(value,cols)])
    def fetchone(self):return self._wrap(self.cursor.fetchone())
    def fetchall(self):return [self._wrap(r) for r in self.cursor.fetchall()]
    def __iter__(self):
        for r in self.cursor:yield self._wrap(r)

# The original application uses positional ? parameters. Convert only operators
# outside SQL strings; do NOT interpolate values into SQL.
def sql_pg(sql):
    sql=sql.replace('BEGIN IMMEDIATE','')
    sql=re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b','INSERT INTO',sql,flags=re.I)
    # Translate legacy parameter syntax to PostgreSQL semantics.
    sql=re.sub(r'\bIS\s+\?', 'IS NOT DISTINCT FROM ?',sql,flags=re.I)
    sql=re.sub(r'(?<![\w])MIN\(latency_min,excluded.latency_min\)','LEAST(latency_min,excluded.latency_min)',sql,flags=re.I)
    sql=re.sub(r'(?<![\w])MAX\(latency_max,excluded.latency_max\)','GREATEST(latency_max,excluded.latency_max)',sql,flags=re.I)
    out=[];quoted=False;i=0
    while i<len(sql):
        ch=sql[i]
        if ch=="'":
            if quoted and i+1<len(sql) and sql[i+1]=="'":out.extend(("'","'"));i+=2;continue
            quoted=not quoted
        if ch=='?' and not quoted:out.append('%s')
        else:out.append(ch)
        i+=1
    return ''.join(out)

class ConnectionAdapter:
    def __init__(self,conn):self.conn=conn
    def commit(self):
        """Commit durable security counters before raising an API exception."""
        self.conn.commit()
    def rollback(self):
        self.conn.rollback()
    def executemany(self,statement,parameters):
        cursor=self.conn.cursor()
        cursor.executemany(sql_pg(statement),parameters)
        return CursorAdapter(cursor)
    def execute(self,statement,parameters=()):
        if statement.strip().upper()=='BEGIN IMMEDIATE':
            # psycopg opens the transaction automatically on first statement.
            return self.conn.execute('SELECT 1 WHERE FALSE')
        sql=sql_pg(statement)
        if re.search(r'\bINSERT\s+OR\s+IGNORE\b',statement,re.I):
            sql+=' ON CONFLICT DO NOTHING'
        cursor=self.conn.execute(sql,parameters)
        return CursorAdapter(cursor)

@contextmanager
def db():
    import psycopg
    with psycopg.connect(DB_DSN,connect_timeout=5,options="-c client_encoding=UTF8") as conn:
        # The connection context commits on success, rolls back on exception.
        # Explicit commit() is needed for security counters persisted before APIError.
        yield ConnectionAdapter(conn)

def assert_utf8():
    # Reject SQL_ASCII, even if the client happens to return decodable bytes.
    with db() as c:
        encoding = c.execute("SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname=current_database()").fetchone()[0]
        client = c.execute('SHOW client_encoding').fetchone()[0]
        if encoding != 'UTF8' or client != 'UTF8':
            raise RuntimeError(f'PostgreSQL verlangt UTF8 (DB={encoding}, Client={client})')

def initialize():
    # Schema is provisioned by install, not on every WSGI request.
    assert_utf8()
    with db() as c:
        if not c.execute("SELECT to_regclass('public.monitors')").fetchone()[0]:
            raise RuntimeError('PostgreSQL-Schema fehlt. Bei einer Neuinstallation install.sh ausführen.')

def create_schema():
    import psycopg
    with psycopg.connect(DB_DSN,connect_timeout=5,options="-c client_encoding=UTF8") as conn:
        # DDL transaction is atomic; error cannot leave half-created tables.
        for statement in SCHEMA_PATH.read_text().split(";"):
            if statement.strip():conn.execute(statement)
        conn.commit()

def event(c,mid,typ,severity,detail):
    c.execute('INSERT INTO events(monitor_id,created,event_type,severity,detail) VALUES(?,?,?,?,?)',(mid,time.time(),typ,severity,detail[:500]))

def audit(c,actor,action,mid=None,ip='',detail='',success=True):
    c.execute('INSERT INTO audit(created,actor,action,object_id,client_ip,success,detail) VALUES(?,?,?,?,?,?,?)',
              (time.time(),actor,action,mid,ip,int(success),detail[:300]))

def maintenance_active(c,mid,now):
    m=c.execute('SELECT maintenance FROM monitors WHERE id=?',(mid,)).fetchone()
    if m and m['maintenance']:return True
    from maintenance import active
    return any(active(r,now) for r in c.execute('SELECT * FROM maintenance_windows WHERE monitor_id=? AND starts<=? AND ends>?',(mid,now,now)))

def dependency_blocked(c,mid):
    # Cycles are rejected on insert; an unavailable ancestor suppresses alarms.
    visited=set();todo=[mid]
    while todo:
        child=todo.pop()
        for p in c.execute('SELECT parent_id FROM dependencies WHERE child_id=?',(child,)):
            parent=p['parent_id']
            if parent in visited:continue
            visited.add(parent)
            state=c.execute('SELECT status FROM monitors WHERE id=?',(parent,)).fetchone()
            if state and state['status'] in ('down','warning','error',None):return True
            todo.append(parent)
    return False

def add_dependency(c,parent,child):
    if parent==child:raise ValueError('Selbstabhängigkeit nicht erlaubt')
    if not c.execute('SELECT 1 FROM monitors WHERE id=?',(parent,)).fetchone() or not c.execute('SELECT 1 FROM monitors WHERE id=?',(child,)).fetchone():raise ValueError('Monitor existiert nicht')
    seen=set();todo=[parent]
    while todo:
        node=todo.pop()
        # check if child is already an ancestor of parent (child -> ... -> parent)
        if node in seen:continue
        seen.add(node)
        todo.extend(r['parent_id'] for r in c.execute('SELECT parent_id FROM dependencies WHERE child_id=?',(node,)))
    if child in seen:raise ValueError('Zyklische Abhängigkeit')
    c.execute('INSERT INTO dependencies(parent_id,child_id) VALUES(?,?) ON CONFLICT DO NOTHING',(parent,child))


def remove_dependency(c,parent,child):
    return c.execute('DELETE FROM dependencies WHERE parent_id=? AND child_id=?',(parent,child)).rowcount
