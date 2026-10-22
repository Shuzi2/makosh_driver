import hashlib
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from .forms import PhoneLoginForm
from .models import LoginThrottle, AuditEvent, normalize_phone

class PhoneLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = PhoneLoginForm
    redirect_authenticated_user = True
    def post(self, request, *args, **kwargs):
        raw = request.POST.get("username", "")[:100]
        try: raw = normalize_phone(raw)
        except Exception: pass
        # Phone plus direct peer IP buckets; never trust client-supplied forwarding headers.
        keys = [hashlib.sha256(("phone:"+raw).encode()).hexdigest(), hashlib.sha256(("ip:"+request.META.get("REMOTE_ADDR", "unknown")).encode()).hexdigest()]
        now = timezone.now()
        with transaction.atomic():
            buckets=[]
            for index,key in enumerate(keys):
                b,_=LoginThrottle.objects.select_for_update().get_or_create(key=key, defaults={"window_start":now})
                if b.window_start < now-timedelta(minutes=15): b.failures=0; b.window_start=now
                if b.failures >= (10 if index == 0 else 60):
                    return HttpResponse("Забагато спроб. Спробуйте через 15 хвилин.",status=429,headers={"Retry-After":"900"})
                b.failures+=1; b.save(); buckets.append(b)
        response=super().post(request,*args,**kwargs)
        if request.user.is_authenticated:
            LoginThrottle.objects.filter(key=keys[0]).delete()
            from django.db.models import F
            LoginThrottle.objects.filter(key=keys[1],failures__gt=0).update(failures=F("failures")-1)
            AuditEvent.objects.create(actor=request.user,action="Вхід у систему")
        return response

@login_required
def password_change(request):
    form=PasswordChangeForm(request.user,request.POST or None)
    if request.method=="POST" and form.is_valid():
        user=form.save(); user.must_change_password=False; user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request,user)
        AuditEvent.objects.create(actor=user,action="Змінено пароль")
        messages.success(request,"Пароль змінено.")
        return redirect("dashboard")
    return render(request,"fleet/form.html",{"form":form,"title":"Зміна пароля","hint":"Після першого входу встановіть власний пароль. Мінімум 12 символів.","submit":"Зберегти пароль"})
