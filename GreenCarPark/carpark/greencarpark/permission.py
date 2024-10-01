from rest_framework.permissions import BasePermission


class HasParkingHistoryScope(BasePermission):
    def has_permission(self, request, view):
        return request.auth and 'parking_history' in request.auth.scope


class DenyParkingHistoryScope(BasePermission):
    def has_permission(self, request, view):
        return not (request.auth and 'parking_history' in request.auth.scope)
