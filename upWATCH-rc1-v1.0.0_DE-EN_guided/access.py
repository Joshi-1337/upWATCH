"""Server-side RBAC for upWATCH! RC3. Never trust frontend-supplied IDs."""
import re

ROLES = {'admin', 'manager', 'viewer'}

def role(c, username):
    row=c.execute('SELECT role FROM users WHERE username=?',(username,)).fetchone()
    return row['role'] if row and row['role'] in ROLES else 'viewer'

def grants(c, username):
    groups={r['group_id'] for r in c.execute('SELECT group_id FROM user_group_grants WHERE username=?',(username,))}
    tags={r['tag_id'] for r in c.execute('SELECT tag_id FROM user_tag_grants WHERE username=?',(username,))}
    return groups,tags

def visible_ids(c,username,role_name=None):
    if (role_name or role(c,username))=='admin':
        return {r['id'] for r in c.execute('SELECT id FROM monitors')}
    # Both grants are additive. No grants means no visible monitors.
    return {r['id'] for r in c.execute("""SELECT DISTINCT m.id FROM monitors m
        LEFT JOIN widget_placement p ON p.monitor_id=m.id
        LEFT JOIN user_group_grants g ON g.group_id=p.group_id AND g.username=?
        LEFT JOIN widget_tag_links l ON l.monitor_id=m.id
        LEFT JOIN user_tag_grants t ON t.tag_id=l.tag_id AND t.username=?
        WHERE g.username IS NOT NULL OR t.username IS NOT NULL""",(username,username))}

def can_see(c,username,monitor_id,role_name=None):
    return monitor_id in visible_ids(c,username,role_name)

def filter_ids(c,username,ids,role_name=None):
    allowed=visible_ids(c,username,role_name)
    return [mid for mid in ids if mid in allowed]

def can_assign(c, username, group_id, tag_ids, role_name=None):
    level=role_name or role(c,username)
    if level=='admin':return True
    if level!='manager':return False
    if not isinstance(tag_ids,list) or len(tag_ids)>20 or any(not isinstance(t,str) for t in tag_ids):return False
    groups,tags=grants(c,username)
    # Every new assignment must remain in scope and cannot attach ungranted tags/groups.
    if group_id and group_id not in groups:return False
    if any(tag not in tags for tag in tag_ids):return False
    return bool(group_id in groups or any(tag in tags for tag in tag_ids))

def checked_role(value):
    if value not in ROLES:raise ValueError('Ungültige Rolle')
    return value
