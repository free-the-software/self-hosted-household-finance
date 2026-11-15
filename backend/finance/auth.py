# Copyright (C) 2026 free-the-software
# SPDX-License-Identifier: AGPL-3.0-or-later
# This file is part of Save More Money, under the GNU AGPL v3 or later: see LICENSE.
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.static import serve
from rest_framework import serializers, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from .idle import idle_limit_seconds
from .models import must_change_password, require_password_change


# The one endpoint the React app may call before login. It reports whether a
# session exists, tells the frontend where to send the browser if not, and
# sets the csrftoken cookie the frontend echoes back on writes.
@ensure_csrf_cookie
@api_view(['GET'])
@permission_classes([AllowAny])
def me(request):
    user = request.user
    if not user.is_authenticated:
        return Response({'authenticated': False, 'login_url': settings.LOGIN_URL, 'password_login': settings.PASSWORD_LOGIN})

    return Response({
        'authenticated': True,
        # The page signs the user out after this long without activity (0 = never)
        'idle_timeout_seconds': idle_limit_seconds(),
        'username': user.get_username(),
        'email': user.email,
        'name': user.get_full_name(),
        # Admins also manage users, backups and storage
        'is_admin': user.is_staff,
        # Passwords are this app's to manage (with single sign-on, the identity provider's)
        'password_login': settings.PASSWORD_LOGIN,
        # Signed in with a password someone else chose: choose your own first
        'must_change_password': must_change_password(user),
    })


MENU_ITEMS_MAX = 30


@api_view(['GET', 'PUT'])
def preferences(request):
    """Your own settings that follow you to every device: the menu's order."""
    from .models import UserProfile
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.method == 'PUT':
        order = request.data.get('menu_order', [])
        if not isinstance(order, list) or len(order) > MENU_ITEMS_MAX or not all(
                isinstance(item, str) and item.startswith('/') and len(item) <= 100 for item in order):
            return Response({'error': 'menu_order must be a list of page addresses.'}, status=status.HTTP_400_BAD_REQUEST)
        profile.menu_order = list(dict.fromkeys(order))      # each page once
        profile.save(update_fields=['menu_order'])
    return Response({'menu_order': profile.menu_order})


# While a user must choose a new password, these are all they can reach
OPEN_UNTIL_CHANGED = ('/api/me/', '/api/logout/', '/api/account/password/', '/api/sign-in/')


class PasswordChangeRequired:
    """Middleware: someone signed in with a temporary password can do nothing else until they change it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (user is not None and request.path.startswith(('/api/', '/media/', '/admin/'))
                and request.path not in OPEN_UNTIL_CHANGED and must_change_password(user)):
            return JsonResponse({'detail': 'Choose a new password first.', 'must_change_password': True}, status=403)
        return self.get_response(request)


def _password_problems(password, user=None):
    """Django's password rules (length, not too common, not like the username...), as one message."""
    try:
        validate_password(password, user)
    except ValidationError as error:
        return ' '.join(error.messages)
    return None


def _password_login_only():
    if not settings.PASSWORD_LOGIN:
        raise Http404


@api_view(['POST'])
@permission_classes([AllowAny])
def sign_in(request):
    """The app's own sign-in, when there is no single sign-on. Limited per address (throttles.py)."""
    _password_login_only()
    # DRF checks CSRF only for signed-in users; check it here too, so another
    # site can't sign a visitor in to an account of its choosing
    SessionAuthentication().enforce_csrf(request)
    username = (request.data.get('username') or '').strip()
    user = authenticate(request, username=username, password=request.data.get('password') or '')
    if user is None:
        # Wrong password, unknown username and switched-off account all look the same
        return Response({'error': "That username and password don't match."}, status=status.HTTP_400_BAD_REQUEST)
    auth_login(request, user)
    return Response({'authenticated': True})


@api_view(['POST'])
def change_password(request):
    """Your own password: the current one, and the new one."""
    _password_login_only()
    user = request.user
    if not user.check_password(request.data.get('current_password') or ''):
        return Response({'error': 'Your current password is not right.'}, status=status.HTTP_400_BAD_REQUEST)
    new = request.data.get('new_password') or ''
    if user.check_password(new):
        return Response({'error': 'Choose a password different from the current one.'}, status=status.HTTP_400_BAD_REQUEST)
    problem = _password_problems(new, user)
    if problem:
        return Response({'error': problem}, status=status.HTTP_400_BAD_REQUEST)
    user.set_password(new)
    user.save(update_fields=['password'])
    require_password_change(user, False)
    update_session_auth_hash(request, user)    # stay signed in here; other sessions end
    return Response({'changed': True})


# --- Users (Settings → Users) ---

class IsAdmin(BasePermission):
    message = 'Only an admin can manage users.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)


class UserSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='first_name', required=False, allow_blank=True, max_length=150)
    is_admin = serializers.BooleanField(source='is_staff', required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = get_user_model()
        fields = ['id', 'username', 'name', 'email', 'is_admin', 'is_active', 'last_login', 'date_joined', 'password']
        read_only_fields = ['last_login', 'date_joined']

    def validate(self, data):
        creating = self.instance is None
        if creating and not settings.PASSWORD_LOGIN:
            raise serializers.ValidationError('With single sign-on, people are added in your identity provider; they appear here after their first sign-in.')
        if creating and not data.get('password'):
            raise serializers.ValidationError({'password': 'A new user needs a password.'})
        if not creating and 'password' in data:
            raise serializers.ValidationError({'password': 'Use "Set password" to change a password.'})
        if 'password' in data:
            problem = _password_problems(data['password'], get_user_model()(username=data.get('username', ''), email=data.get('email', '')))
            if problem:
                raise serializers.ValidationError({'password': problem})
        return data

    def create(self, data):
        password = data.pop('password')
        user = get_user_model()(**data)
        user.is_superuser = user.is_staff     # the Django admin, where it's switched on, agrees
        user.set_password(password)
        user.save()
        # The admin chose this password: they choose their own at first sign-in
        require_password_change(user)
        return user

    def update(self, user, data):
        for field, value in data.items():
            setattr(user, field, value)
        user.is_superuser = user.is_staff
        user.save()
        return user


class UserViewSet(viewsets.ModelViewSet):
    """
    Everyone signed in shares the same ledger; admins also manage the users.
    There must always be an active admin, so nobody can lock everyone out:
    you can't switch off or remove yourself, or the last admin.
    """
    serializer_class = UserSerializer
    permission_classes = [IsAdmin]
    queryset = get_user_model().objects.order_by('username')

    def _would_leave_no_admin(self, user, is_admin, is_active):
        if user.is_staff and user.is_active and not (is_admin and is_active):
            return not get_user_model().objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk).exists()
        return False

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        is_admin = request.data.get('is_admin', user.is_staff)
        is_active = request.data.get('is_active', user.is_active)
        if user == request.user and (not is_admin or not is_active):
            return Response({'error': "You can't remove your own admin rights or switch off your own account. Ask another admin."},
                            status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            if self._would_leave_no_admin(user, is_admin, is_active):
                return Response({'error': 'There must be at least one active admin.'}, status=status.HTTP_400_BAD_REQUEST)
            return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user == request.user:
            return Response({'error': "You can't delete your own account."}, status=status.HTTP_400_BAD_REQUEST)
        if self._would_leave_no_admin(user, False, False):
            return Response({'error': 'There must be at least one active admin.'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='set-password')
    def set_password(self, request, pk=None):
        """
        A temporary password for someone (a forgotten one, say): their other
        sessions end, and they choose their own at their next sign-in.
        """
        _password_login_only()
        user = self.get_object()
        if user == request.user:
            return Response({'error': 'Change your own password in Settings → My account.'}, status=status.HTTP_400_BAD_REQUEST)
        new = request.data.get('new_password') or ''
        problem = _password_problems(new, user)
        if problem:
            return Response({'error': problem}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new)
        user.save(update_fields=['password'])
        require_password_change(user)
        return Response({'changed': True})


@api_view(['POST'])
@permission_classes([AllowAny])   # also after the server has ended an idle session
def logout(request):
    """Ends the Django session and tells the frontend where to go next."""
    # Must be read before logout() flushes the session
    id_token = request.session.get('oidc_id_token')
    auth_logout(request)

    redirect_url = settings.LOGOUT_REDIRECT_URL
    if settings.OIDC_ENABLED:
        # Also end the Authentik session, otherwise the login redirect
        # would sign the user straight back in.
        params = {'post_logout_redirect_uri': settings.LOGOUT_REDIRECT_URL}
        if id_token:
            params['id_token_hint'] = id_token
        redirect_url = f"{settings.OIDC_OP_END_SESSION_ENDPOINT}?{urlencode(params)}"

    return Response({'redirect_url': redirect_url})


def protected_media(request, path):
    """Serves uploaded files (receipt images) to logged-in users only."""
    if not request.user.is_authenticated:
        return HttpResponseForbidden()
    return serve(request, path, document_root=settings.MEDIA_ROOT)
