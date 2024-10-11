from rest_framework.permissions import BasePermission, SAFE_METHODS


class HasParkingHistoryScope(BasePermission):
    def has_permission(self, request, view):
        return request.auth and 'parking_history' in request.auth.scope


class DenyParkingHistoryScope(BasePermission):
    def has_permission(self, request, view):
        return not (request.auth and 'parking_history' in request.auth.scope)


class IsOwnerOrReadOnly(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.user == request.user
