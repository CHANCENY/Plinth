"""
ApiTokenController
--------------------
The tokens issued to you by the token endpoint (each "login" from an app or device is one) - see them, and end
any of them. Any authenticated caller can manage their own.

    GET     /api/tokens          your issued tokens: when, from where (IP / user agent), expiry, last use
    DELETE  /api/tokens/<id>     revoke one - its access and refresh tokens stop working
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from public.apps.apiauth.models.ApiToken import ApiToken
from public.apps.apiauth.services import Http
from public.apps.apiauth.services.CredentialService import CredentialService


class ApiTokenController(ControllerBase):

    @route("/api/tokens", methods=["GET"], permission=["authenticated"])
    def index(self, request):
        user = self.require_auth(request)
        credentials = CredentialService(self.db())
        tokens = user.api_tokens.select_related("api_key").order_by("-id")[:100]
        return self.json({"tokens": [credentials.token_summary(token) for token in tokens]})

    @route("/api/tokens/<id>", methods=["DELETE"], permission=["authenticated"])
    def revoke(self, request):
        user = self.require_auth(request)
        token = ApiToken.find(self.db(), request.get_route_parameter("id"))
        if token is None or token.uid != user.uid:
            return Http.error("not_found", "no such token", status=404)
        CredentialService(self.db()).revoke_row(token)
        return self.no_content()
