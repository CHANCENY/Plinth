"""
PostController
-----------------
Demo controller for the "blog" app (public/apps/blog/) - proves the whole
apps pipeline end to end: routes declared with @route right on the method
(instead of Routing.py's central table, the way core controllers register),
discovered and cached by `python console.py apps:discover`, dispatched
through the exact same Routing/PermissionMiddleware path as every core
route, rendering views out of this app's own views/ directory
("blog:posts/index"), reading/writing this app's own model (Post).

Extends the same ControllerBase every core controller does - db()/view()/
validate()/etc. all work identically here; nothing app-specific about
ControllerBase itself.
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route

from public.apps.blog.models.Post import Post
from public.apps.blog.services.SlugService import SlugService


class PostController(ControllerBase):

    @route("/blog", methods=["GET"])
    def index(self, request):
        posts = list(Post.objects(self.db()).order_by("-created_at"))
        return self.view("blog:posts/index", {"posts": posts}, request=request)

    @route("/blog/new", methods=["GET", "POST"])
    def create(self, request):
        if request.is_method("POST"):
            validator = self.validate(request, {"title": "required|min:2", "body": "required"})
            if validator.fails():
                return self.view(
                    "blog:posts/create", {"errors": validator.get_errors(), "old": request.all()}, request=request
                )

            data = validator.validated()
            post = Post(self.db(), title=data["title"], slug=SlugService.slugify(data["title"]), body=data["body"])
            post.save()
            return self.redirect(f"/blog/{post.slug}")

        return self.view("blog:posts/create", {"errors": {}, "old": {}}, request=request)

    @route("/blog/<slug>", methods=["GET"])
    def show(self, request):
        slug = request.get_route_parameter("slug")
        post = Post.objects(self.db()).filter(slug=slug).first()
        if post is None:
            return self.not_found("Post not found")
        return self.view("blog:posts/show", {"post": post}, request=request)
