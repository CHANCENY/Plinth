import json
from .Response import Response

class JsonResponse(Response):
    def __init__(self, data=None, status=Response.HTTP_OK, headers=None, *, ensure_ascii=False):
        self.data = data
        self.ensure_ascii = ensure_ascii
        response_headers = dict(headers or {})
        if not any(k.lower() == "content-type" for k in response_headers):
            response_headers["Content-Type"] = "application/json; charset=utf-8"
        super().__init__(
            self._encode(data),
            status=status,
            headers=response_headers,
        )

    def _encode(self, data):
        return json.dumps(
            data,
            ensure_ascii=self.ensure_ascii,
            separators=(",", ":"),
            default=self._json_default,
        )

    @staticmethod
    def _json_default(value):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "__dict__"):
            return value.__dict__
        raise TypeError(
            f"Object of type {type(value).__name__} is not JSON serializable"
        )

    def set_data(self, data):
        self.data = data
        self.set_content(self._encode(data))
        return self

    def get_data(self):
        return self.data
