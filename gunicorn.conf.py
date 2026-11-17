"""gunicorn settings read automatically from the working directory (/app in the image).

Only one thing lives here (S-11, 15.11.2026): gunicorn announces itself and
its version in a ``Server`` header on every answer. That tells a scanner which
known bugs to try and helps nobody else, so each worker drops the line. The
command line in the Dockerfile still sets bind, workers and timeout.
"""


def post_worker_init(worker):  # noqa: ARG001 - gunicorn hook signature
    from gunicorn.http import wsgi

    original = wsgi.Response.default_headers
    if getattr(original, "_photobox_no_server", False):
        return

    def default_headers(self):
        return [h for h in original(self) if not h.lower().startswith("server:")]

    default_headers._photobox_no_server = True
    wsgi.Response.default_headers = default_headers
