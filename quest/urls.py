from django.urls import path
from . import views

app_name = 'quest'

urlpatterns = [
    path('', views.quest_dashboard, name='dashboard'),
    path('start/', views.quest_start, name='start'),
    path('join/', views.quest_join, name='join'),
    path('celebration/', views.quest_celebration, name='celebration'),
    path('missions/', views.mission_history, name='missions'),
    path('achievements/', views.achievement_gallery, name='achievements'),
    path('profile/', views.quest_profile, name='profile'),
    path('api/status/', views.quest_api_status, name='api_status'),
    path('title/<int:level>/', views.title_detail, name='title_detail'),
]
