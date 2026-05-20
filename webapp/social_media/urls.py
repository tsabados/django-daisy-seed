from django.urls import path

from . import views

app_name = 'social_media'

urlpatterns = [
    path('', views.post_list, name='post_list'),
    path('save/', views.post_save, name='post_save'),
    path('create/', views.post_form, name='post_form'),
    path('<int:pk>/edit/', views.post_form, name='post_form'),
    path('<int:pk>/delete/', views.post_delete, name='post_delete'),
    path('<int:pk>/publish/', views.post_publish, name='post_publish'),
    path('publish-panel/', views.post_publish_panel, name='post_publish_panel_new'),
    path('<int:pk>/publish-panel/', views.post_publish_panel, name='post_publish_panel'),
    path('<int:pk>/card/', views.post_card, name='post_card'),
    path('<int:pk>/schedule/', views.post_schedule, name='post_schedule'),
    path('<int:pk>/unschedule/', views.post_unschedule, name='post_unschedule'),
    path('<int:pk>/save-scheduled-at/', views.post_save_scheduled_at, name='post_save_scheduled_at'),
    path('ai/suggest-topic/', views.ai_suggest_topic, name='ai_suggest_topic'),
    path('ai/edit-text/', views.ai_edit_text, name='ai_edit_text'),
]
