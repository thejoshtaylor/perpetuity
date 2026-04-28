"""Backend service-layer modules (M004/S05).

Houses cross-route service helpers like the GitHub webhook dispatcher
(``app.services.dispatch``) so the route module stays focused on HTTP
shape, signature verification, and persistence — and the dispatch hook
has a single import target that M005 can extend without touching the
receiver route.
"""
