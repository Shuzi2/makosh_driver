from django.urls import path
from django.contrib.auth.views import LogoutView
from fleet import views, auth_views
urlpatterns=[
path("health/",views.health,name="health"),path("login/",auth_views.PhoneLoginView.as_view(),name="login"),path("logout/",LogoutView.as_view(),name="logout"),path("password/",auth_views.password_change,name="password_change"),
path("",views.dashboard,name="dashboard"),path("trips/",views.trip_list,name="trip_list"),path("trips/new/",views.trip_create,name="trip_create"),path("trips/<int:pk>/",views.trip_detail,name="trip_detail"),path("trips/<int:pk>/status/",views.trip_status,name="trip_status"),path("trips/<int:pk>/assign/",views.assign_driver,name="assign_driver"),path("trips/<int:pk>/documents/",views.document_upload,name="document_upload"),path("trips/<int:pk>/original/",views.original_received,name="original_received"),path("documents/<int:pk>/download/",views.document_download,name="document_download"),path("report/",views.report,name="report"),path("accounts/",views.accounts,name="accounts"),path("accounts/<int:pk>/action/",views.account_action,name="account_action"),path("vehicles/<int:pk>/repair/",views.vehicle_repair,name="vehicle_repair")]
