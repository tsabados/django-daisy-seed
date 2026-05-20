from authlib.integrations.django_client import OAuth

oauth = OAuth()

# Providers are registered from AUTHLIB_OAUTH_CLIENTS in settings.py.
# Each key in that dict becomes a named OAuth client.
# Registration happens at import time; authlib reads the config lazily.

oauth.register(name='facebook')
oauth.register(name='instagram')
oauth.register(name='linkedin')
oauth.register(name='tiktok')
