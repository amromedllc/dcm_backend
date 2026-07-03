from functools import wraps
from ninja.errors import HttpError


def require_roles(*roles: str):
    """
    Decorator for Django Ninja endpoints.
    Usage: @require_roles('admin', 'supervisor')
    Assumes jwt_auth is set on the router — request.user is populated.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            if not hasattr(request, 'user') or not request.user.is_authenticated:
                raise HttpError(401, 'Authentication required')
            if request.user.role not in roles:
                raise HttpError(403, f'Required role: {" or ".join(roles)}')
            return func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_admin(func):
    return require_roles('admin')(func)


def require_supervisor_or_above(func):
    return require_roles('admin', 'supervisor')(func)


def require_staff_or_above(func):
    return require_roles('admin', 'supervisor', 'staff')(func)
