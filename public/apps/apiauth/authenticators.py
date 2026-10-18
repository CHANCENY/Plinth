"""
Tells the framework this app can identify API callers. On every request Server asks each registered authenticator
(core/services/RequestAuthService.py) whether the request carries credentials it understands.
"""

AUTHENTICATORS = [
    "public.apps.apiauth.services.ApiAuthenticator.ApiAuthenticator",
]
