from django.shortcuts import redirect
class PasswordChangeMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated and request.user.must_change_password and request.path not in ("/password/", "/logout/", "/health/") and not request.path.startswith("/static/"):
            return redirect("password_change")
        response = self.get_response(request)
        if request.user.is_authenticated and "Cache-Control" not in response:
            response["Cache-Control"] = "private, no-store"
        response["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        return response
