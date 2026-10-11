from core.controllers.ControllerBase import ControllerBase

# The default controllers (Page/Auth/User/File) now live in the admin app:
#     public/apps/admin/controllers/
# ControllerBase stays here because every app's controllers extend it.

__all__ = ["ControllerBase"]
