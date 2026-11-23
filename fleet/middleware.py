from django.shortcuts import redirect
from django.conf import settings
from urllib.parse import urlsplit

class LanSecureCookieMiddleware:
    """Mark HTTPS session/CSRF cookies secure while keeping localhost HTTP usable."""
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        response = self.get_response(request)
        if request.is_secure():
            for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                if name in response.cookies:
                    response.cookies[name]['secure'] = True
        return response

class PasswordChangeMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated and request.user.must_change_password and request.path not in ("/password/", "/logout/", "/health/", "/service-worker.js", "/manifest.webmanifest") and not request.path.startswith("/static/"):
            return redirect("password_change")
        response = self.get_response(request)
        if request.user.is_authenticated and "Cache-Control" not in response:
            response["Cache-Control"] = "private, no-store"
        tiles=urlsplit(settings.MAP_TILE_URL)
        tile_origin=f'https://{tiles.hostname}'+(f':{tiles.port}' if tiles.port else '') if tiles.scheme=='https' and tiles.hostname else ''
        if 'Content-Security-Policy' not in response:
            response["Content-Security-Policy"] = f"default-src 'self'; img-src 'self' data: blob: {tile_origin}; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        return response

class SessionActivityMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated:
            if request.user.role in ('driver','moderator','admin'):
                from django.utils import timezone
                now = int(timezone.now().timestamp())
                renewed = request.session.get('driver_session_renewed_at', 0)
                if not isinstance(renewed, int) or now - renewed >= 86400:
                    request.session.set_expiry(settings.DRIVER_SESSION_AGE)
                    request.session['driver_session_renewed_at'] = now
            from .session_activity import observe
            observe(request)
        return self.get_response(request)
