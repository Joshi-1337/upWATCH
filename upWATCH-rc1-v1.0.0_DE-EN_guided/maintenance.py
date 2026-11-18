"""Evaluate once, daily and weekly maintenance schedules with IANA timezones."""
import datetime as dt,json,re
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

def parse_time(text):
    if not isinstance(text,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',text):raise ValueError('Uhrzeit muss HH:MM sein')
    h,m=map(int,text.split(':'));return h*60+m

def validate(rec,tz,weekdays,start_local,end_local):
    if rec not in ('once','daily','weekly'):raise ValueError('Ungültige Wiederholung')
    try:ZoneInfo(tz)
    except (ZoneInfoNotFoundError,TypeError,ValueError):raise ValueError('Ungültige IANA-Zeitzone')
    a=parse_time(start_local);b=parse_time(end_local)
    if rec!='once' and a==b:raise ValueError('Beginn und Ende dürfen nicht gleich sein')
    if not isinstance(weekdays,list) or any(type(x)!=int or x not in range(7) for x in weekdays) or len(set(weekdays))!=len(weekdays):raise ValueError('Ungültige Wochentage')
    if rec=='weekly' and not weekdays:raise ValueError('Mindestens ein Wochentag erforderlich')
    return dict(recurrence=rec,timezone=tz,weekdays=json.dumps(weekdays),start_local=start_local,end_local=end_local)

def active(window,now):
    if not window['starts']<=now<window['ends']:return False
    if window['recurrence']=='once':return True
    local=dt.datetime.fromtimestamp(now,dt.timezone.utc).astimezone(ZoneInfo(window['timezone']))
    minute=local.hour*60+local.minute
    start=parse_time(window['start_local']);end=parse_time(window['end_local'])
    overnight=end<start
    if not overnight:day=local.weekday();inside=start<=minute<end
    elif minute>=start:day=local.weekday();inside=True
    elif minute<end:day=(local.weekday()-1)%7;inside=True
    else:return False
    return inside and (window['recurrence']=='daily' or day in json.loads(window['weekdays']))
