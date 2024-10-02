from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *


class AdminSiteCarPark(admin.AdminSite):
    site_header = "ADMIN CAR PARK"


class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ('username', 'email', 'date_joined', 'is_staff', 'is_active')
    search_fields = ('username', 'email')
    filter_horizontal = ()
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info',
         {'fields': ('first_name', 'last_name', 'email', 'date_of_birth', 'phone_number')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2', 'date_of_birth', 'phone_number', 'is_staff'),
        }),
    )


class ParkingLotAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'address', 'price_per_hour')


class ParkingSpotAdmin(admin.ModelAdmin):
    list_display = ('id', 'parkinglot', 'status')


class VehicleAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'license_plate', 'color', 'brand', 'car_model')


class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'spot', 'subscription_type', 'start_date', 'end_date', 'status')


class SubscriptionTypeAdmin(admin.ModelAdmin):
    list_display = ('id', 'type', 'total_amount')


class BookingAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'spot', 'vehicle', 'start_time', 'end_time', 'status')


class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'booking', 'subscription', 'amount', 'payment_method', 'payment_status', 'payment_note')


class ParkingHistoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'spot', 'vehicle', 'booking', 'subscription', 'entry_time', 'exit_time')


car_park_admin_site = AdminSiteCarPark(name='car_park_admin')

car_park_admin_site.register(User, CustomUserAdmin)
car_park_admin_site.register(Reviews)
car_park_admin_site.register(ParkingLot, ParkingLotAdmin)
car_park_admin_site.register(ParkingSpot, ParkingSpotAdmin)
car_park_admin_site.register(Vehicle, VehicleAdmin)
car_park_admin_site.register(Subscription, SubscriptionAdmin)
car_park_admin_site.register(SubscriptionType, SubscriptionTypeAdmin)
car_park_admin_site.register(Booking, BookingAdmin)
car_park_admin_site.register(Payment, PaymentAdmin)
car_park_admin_site.register(ParkingHistory, ParkingHistoryAdmin)
car_park_admin_site.register(Complaint)
