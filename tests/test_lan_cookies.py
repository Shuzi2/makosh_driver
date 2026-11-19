from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from fleet.middleware import LanSecureCookieMiddleware


class SecureCookieTests(SimpleTestCase):
    def test_secure_only_on_https(self):
        def response(request):
            result=HttpResponse('ok')
            result.set_cookie('sessionid','session')
            result.set_cookie('csrftoken','token')
            return result
        middleware=LanSecureCookieMiddleware(response)
        for secure in (False,True):
            result=middleware(RequestFactory().get('/',secure=secure))
            for name in ('sessionid','csrftoken'):
                self.assertEqual(bool(result.cookies[name]['secure']),secure)
