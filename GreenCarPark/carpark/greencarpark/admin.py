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


car_park_admin_site = AdminSiteCarPark(name='car_park_admin')

car_park_admin_site.register(User, CustomUserAdmin)
car_park_admin_site.register(Reviews)
car_park_admin_site.register(ParkingLot)
car_park_admin_site.register(ParkingSpot)
car_park_admin_site.register(Vehicle)
car_park_admin_site.register(Subscription)
car_park_admin_site.register(SubscriptionType)
car_park_admin_site.register(Booking)
car_park_admin_site.register(Payment)
car_park_admin_site.register(ParkingHistory)
car_park_admin_site.register(Complaint)
from django.contrib import admin

# Register your models here.
