from django.contrib import admin
from django.urls import path, include
from rest_framework import routers

from .views import *
from .admin import car_park_admin_site

router = routers.DefaultRouter()
router.register('user', UserViewSet)
router.register('vehicle', VehicleViewSet)
router.register('booking', BookingViewSet)
router.register('subscription', SubscriptionViewSet)
router.register('parkinglot', ParkingLotViewSet)
router.register('parkingspot', ParkingSpotViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('admin/', car_park_admin_site.urls),
]
