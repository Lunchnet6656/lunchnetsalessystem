from django.urls import path

from . import views

app_name = "fees"

urlpatterns = [
    path("", views.statement_list, name="list"),
    path("destinations/", views.destinations, name="destinations"),
    path("destinations/add/", views.destination_add, name="destination_add"),
    path("destinations/<int:location_pk>/", views.destination_edit, name="destination_edit"),
    path("issuer/", views.issuer, name="issuer"),
    path("issue/", views.issue, name="issue"),
    path("issue/preview/", views.issue_preview, name="issue_preview"),
    path("pdf/", views.statement_pdf_month, name="pdf_month"),
    path("<int:pk>/", views.statement_detail, name="detail"),
    path("<int:pk>/void/", views.statement_void, name="void"),
    path("<int:pk>/pdf/", views.statement_pdf, name="pdf"),
]
