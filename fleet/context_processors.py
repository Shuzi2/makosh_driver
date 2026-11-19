from django.conf import settings

def maps(request):
    return {'map_tile_url':settings.MAP_TILE_URL,'map_tile_attribution':settings.MAP_TILE_ATTRIBUTION}

def attention(request):
    if not request.user.is_authenticated or not request.user.manages_fleet: return {}
    if not hasattr(request,'attention_nav_count'):
        from .attention import issue_snapshot
        request.attention_nav_count=sum(not issue['skip'] for issue in issue_snapshot())
    from .maintenance import snapshot
    result={'attention_nav_count':request.attention_nav_count,'maintenance_nav_count':sum(row['state']['key'] in ['overdue','soon','unknown'] for row in snapshot())}
    from .models import User, DriverServiceRequest
    result['service_nav_count']=DriverServiceRequest.objects.filter(status='new').count()
    result['driver_report_nav']=User.objects.filter(role='driver').order_by('full_name')
    return result
