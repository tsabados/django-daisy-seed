from django.urls import path
from .views import home, settings, about, article

urlpatterns = [
    path("", home, name="home"),
    path("settings", settings, name="settings"),
    path("about", about, name="about"),
    path("article", article, name="article")

]
