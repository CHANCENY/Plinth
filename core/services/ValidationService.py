"""
ValidationService
-------------------
Validates a plain dict (typically request.all() or request.json()) against
Laravel-style rule strings. Controllers currently pull data straight off the
Request with no validation step in between - this fills that gap.

Project layout assumed - same as the other services:
    project/
        core/
            services/
                ValidationService.py   <- this file

Supported rules
----------------
    required            value must be present and not empty ("" / None fail)
    email                must look like a valid email address
    numeric              must be convertible to a number (int or float)
    integer               must be convertible to an int
    min:N                 string length (or numeric value, for numeric-looking input) must be >= N
    max:N                 string length (or numeric value, for numeric-looking input) must be <= N
    in:a,b,c              value must be one of the given options
    alpha                  letters only
    alpha_num              letters and digits only
    url                     must look like a valid http(s) URL
    confirmed               value must match data["<field>_confirmation"]

Rules for one field are combined with "|", e.g. "required|email".
Any rule other than "required" is skipped for a field that's absent/empty -
mark the field "required" as well if it must always be present.

Usage
-----
    from core.services.ValidationService import ValidationService

    validator = ValidationService(request.all(), {
        "email": "required|email",
        "password": "required|min:8",
        "password_confirmation": "required",
        "age": "integer|min:13",
    })

    if validator.fails():
        return JsonResponse({"errors": validator.get_errors()}, status=422)

    data = validator.validated()   # only the fields declared in the rules

Or, directly from a Request:

    validator = ValidationService.for_request(request, {"email": "required|email"})
"""

import re
from typing import Any, Dict, List, Optional

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


class ValidationService:
    def __init__(self, data: Dict[str, Any], rules: Dict[str, str]):
        self.data = data or {}
        self.rules = rules or {}
        self.errors: Dict[str, List[str]] = {}
        self._checked = False

    @classmethod
    def for_request(cls, request, rules: Dict[str, str]) -> "ValidationService":
        return cls(request.all(), rules)

    # ------------------------------------------------------------------ #
    # running
    # ------------------------------------------------------------------ #
    def passes(self) -> bool:
        self.errors = {}
        for field, rule_string in self.rules.items():
            self._validate_field(field, rule_string)
        self._checked = True
        return not self.errors

    def fails(self) -> bool:
        return not self.passes()

    def get_errors(self) -> Dict[str, List[str]]:
        if not self._checked:
            self.passes()
        return dict(self.errors)

    def first_error(self) -> Optional[str]:
        for messages in self.get_errors().values():
            if messages:
                return messages[0]
        return None

    def validated(self) -> Dict[str, Any]:
        """Returns only the fields declared in the rules that are actually present in the data."""
        return {field: self.data.get(field) for field in self.rules if field in self.data}

    # ------------------------------------------------------------------ #
    # field-level validation
    # ------------------------------------------------------------------ #
    def _validate_field(self, field: str, rule_string: str) -> None:
        value = self.data.get(field)
        is_present = field in self.data and self.data.get(field) not in (None, "")

        for rule in rule_string.split("|"):
            rule = rule.strip()
            if not rule:
                continue
            rule_name, _, rule_param = rule.partition(":")

            if rule_name == "required":
                if not is_present:
                    self._add_error(field, f"{field} is required.")
                    return  # no point checking further rules on a missing required field
                continue

            if not is_present:
                # optional field that's absent/empty - skip remaining rules for it
                continue

            method = getattr(self, f"_rule_{rule_name}", None)
            if method is None:
                raise ValueError(f"ValidationService: unknown rule '{rule_name}'")
            method(field, value, rule_param)

    def _add_error(self, field: str, message: str) -> None:
        self.errors.setdefault(field, []).append(message)

    # ------------------------------------------------------------------ #
    # individual rules
    # ------------------------------------------------------------------ #
    def _rule_email(self, field, value, param):
        if not EMAIL_RE.match(str(value)):
            self._add_error(field, f"{field} must be a valid email address.")

    def _rule_numeric(self, field, value, param):
        try:
            float(value)
        except (TypeError, ValueError):
            self._add_error(field, f"{field} must be numeric.")

    def _rule_integer(self, field, value, param):
        try:
            int(str(value))
        except (TypeError, ValueError):
            self._add_error(field, f"{field} must be an integer.")

    def _rule_min(self, field, value, param):
        size = self._sizeof(value)
        if size < float(param):
            self._add_error(field, f"{field} must be at least {param}.")

    def _rule_max(self, field, value, param):
        size = self._sizeof(value)
        if size > float(param):
            self._add_error(field, f"{field} must be at most {param}.")

    def _rule_in(self, field, value, param):
        allowed = [item.strip() for item in param.split(",")]
        if str(value) not in allowed:
            self._add_error(field, f"{field} must be one of: {', '.join(allowed)}.")

    def _rule_alpha(self, field, value, param):
        if not str(value).isalpha():
            self._add_error(field, f"{field} must contain only letters.")

    def _rule_alpha_num(self, field, value, param):
        if not str(value).isalnum():
            self._add_error(field, f"{field} must contain only letters and numbers.")

    def _rule_url(self, field, value, param):
        if not URL_RE.match(str(value)):
            self._add_error(field, f"{field} must be a valid URL.")

    def _rule_confirmed(self, field, value, param):
        if self.data.get(f"{field}_confirmation") != value:
            self._add_error(field, f"{field} confirmation does not match.")

    @staticmethod
    def _sizeof(value) -> float:
        # form/query values arrive as strings ("34"), so numeric-looking strings
        # are sized by their numeric value, not their character length - only
        # genuinely non-numeric values (like a password) fall back to length.
        if isinstance(value, bool):
            return float(len(str(value)))
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(len(str(value)))
