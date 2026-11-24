"""Session inventory: session credentials remain server-side and are never rendered."""
from datetime import timedelta
from django.contrib.auth import SESSION_KEY, HASH_SESSION_KEY
from django.contrib.sessions.models import Session
from django.db.models import Max
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from .models import UserSessionActivity

def session_token(key):
    return salted_hmac('mtruck.session.inventory.v1', key, algorithm='sha256').hexdigest()

def device_labels(agent):
    agent = agent[:1000]
    browser = 'Edge' if 'Edg/' in agent else 'Opera' if 'OPR/' in agent else 'Firefox' if 'Firefox/' in agent else 'Chrome' if ('Chrome/' in agent or 'CriOS/' in agent) else 'Safari' if 'Safari/' in agent else 'Невідомий браузер'
    system = 'Android' if 'Android' in agent else 'iPhone / iPad' if any(x in agent for x in ('iPhone','iPad','iPod')) else 'Windows' if 'Windows' in agent else 'macOS' if 'Macintosh' in agent else 'Linux' if 'Linux' in agent else 'Невідома система'
    return browser, system

def observe(request, login=False):
    key=request.session.session_key
    if not key or not request.user.is_authenticated: return
    now=timezone.now(); token=session_token(key)
    browser, platform=device_labels(request.META.get('HTTP_USER_AGENT',''))
    defaults={'user_id':request.user.pk,'browser':browser,'platform':platform,'last_seen_at':now,'login_at':now if login else None}
    record, created=UserSessionActivity.objects.get_or_create(session_hash=token, defaults=defaults)
    if not created:
        if login:
            UserSessionActivity.objects.filter(pk=token).update(user_id=request.user.pk,browser=browser,platform=platform,last_seen_at=now,login_at=now)
        else:
            UserSessionActivity.objects.filter(pk=token,user_id=request.user.pk,last_seen_at__lt=now-timedelta(seconds=60)).update(last_seen_at=now)

def valid_sessions(account, current_key=None):
    if not account.is_active: return []
    expected=[account.get_session_auth_hash(), *account.get_session_auth_fallback_hash()]
    rows=[]
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator(chunk_size=200):
        data=session.get_decoded()
        if str(data.get(SESSION_KEY, '')) != str(account.pk): continue
        actual=data.get(HASH_SESSION_KEY, '')
        if not actual or not any(constant_time_compare(actual, value) for value in expected): continue
        rows.append({'token':session_token(session.session_key),'key':session.session_key,'current':session.session_key==current_key})
    activity={item.pk:item for item in UserSessionActivity.objects.filter(user=account,session_hash__in=[row['token'] for row in rows])}
    for row in rows: row['activity']=activity.get(row['token'])
    rows.sort(key=lambda row: row['activity'].last_seen_at.timestamp() if row['activity'] else 0,reverse=True)
    return rows

def presence_for_accounts(accounts):
    """Recent connections from valid sessions, plus the last recorded connection."""
    accounts = {account.pk: account for account in accounts}
    now = timezone.now()
    result = {pk: {'online': False, 'last_seen': None} for pk in accounts}
    for row in UserSessionActivity.objects.filter(user_id__in=accounts).values('user_id').annotate(last=Max('last_seen_at')):
        result[row['user_id']]['last_seen'] = row['last']
    recent = dict(UserSessionActivity.objects.filter(
        user_id__in=[pk for pk, account in accounts.items() if account.is_active],
        last_seen_at__gte=now-timedelta(minutes=5), last_seen_at__lte=now,
    ).values_list('session_hash', 'user_id'))
    if not recent:
        return result
    for session in Session.objects.filter(expire_date__gt=now).iterator(chunk_size=200):
        user_id = recent.get(session_token(session.session_key))
        if user_id is None or result[user_id]['online']:
            continue
        data = session.get_decoded()
        account = accounts[user_id]
        actual = data.get(HASH_SESSION_KEY, '')
        if str(data.get(SESSION_KEY, '')) == str(user_id) and actual and any(
            constant_time_compare(actual, expected)
            for expected in [account.get_session_auth_hash(), *account.get_session_auth_fallback_hash()]
        ):
            result[user_id]['online'] = True
    return result
