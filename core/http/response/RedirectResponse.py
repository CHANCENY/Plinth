from .Response import Response

class RedirectResponse(Response):
    def __init__(self, url, status=Response.HTTP_FOUND, headers=None):
        if status not in {301, 302, 303, 307, 308}:
            raise ValueError(
                "Redirect status must be one of 301, 302, 303, 307 or 308."
            )
        response_headers = dict(headers or {})
        response_headers["Location"] = url
        super().__init__(b"", status=status, headers=response_headers)
        self.url = url

    def get_target_url(self):
        return self.url
