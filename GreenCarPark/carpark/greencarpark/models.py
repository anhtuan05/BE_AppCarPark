
from django.db import models
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
from django.core.validators import MinValueValidator, MaxValueValidator
from rest_framework.exceptions import ValidationError


class BaseModel(models.Model):
    created_date = models.DateField(auto_now_add=True, null=True)
    updated_date = models.DateField(auto_now=True, null=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    date_of_birth = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True, unique=True)
    face_description = models.TextField(null=True, blank=True)
    user_reviews = models.ManyToManyField('ParkingLot', through='Reviews', related_name='parkinglot_user')


# many-to-many user and parkinglot
class Reviews(BaseModel):
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name="reviews_user")
    parkinglot = models.ForeignKey('ParkingLot', on_delete=models.CASCADE, related_name="reviews_parkinglot")
    comment = models.TextField(null=True, blank=True)
    rate = models.IntegerField(default=0, validators=[MinValueValidator(1), MaxValueValidator(5)], null=False,
                               blank=False)

    def __str__(self):
        return f"({self.user} - {self.parkinglot} - {self.rate})"


class ParkingLot(BaseModel):
    name = models.CharField(max_length=50, null=False, blank=False)
    address = models.CharField(max_length=100, null=False, blank=False)
    price_per_hour = models.FloatField(null=False, blank=False)

    def __str__(self):
        return f"({self.name})"


class ParkingSpot(BaseModel):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('reserved', 'Reserved'),
        ('in_use', 'In Use'),
        ('maintenance', 'Maintenance'),
    ]
    parkinglot = models.ForeignKey('ParkingLot', on_delete=models.CASCADE, related_name="parking_spot")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')

    def __str__(self):
        return f"({self.id} - {self.parkinglot.name} - {self.status})"

    def delete(self, *args, **kwargs):
        if self.status in ['reserved', 'in_use']:
            raise ValidationError(f"Cannot delete ParkingSpot with status '{self.status}'.")
        super().delete(*args, **kwargs)


class Vehicle(BaseModel):
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name="vehicle")
    license_plate = models.CharField(null=False, blank=False, max_length=8, unique=True)
    color = models.CharField(null=False, blank=False, max_length=20)
    brand = models.CharField(null=False, blank=False, max_length=20)
    car_model = models.CharField(null=False, blank=False, max_length=20)

    class Meta:
        pass

    def __str__(self):
        return f"({self.license_plate})"


class Subscription(BaseModel):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('cancel', 'Cancel'),
    ]
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name="subscription_user")
    spot = models.ForeignKey('ParkingSpot', on_delete=models.CASCADE, related_name="subscription_spot")
    subscription_type = models.ForeignKey('SubscriptionType', on_delete=models.CASCADE, related_name="sub_type")
    start_date = models.DateField(null=False)
    end_date = models.DateField(null=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')

    def __str__(self):
        return f"({self.user} - {self.spot} - {self.status})"


class SubscriptionType(BaseModel):
    type = models.CharField(max_length=50, null=False, blank=False)
    total_amount = models.FloatField(null=False, blank=False)

    def __str__(self):
        return f"({self.type})"


class Booking(BaseModel):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('in_use', 'In Use'),
        ('disable', 'Disable'),
    ]
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name="booking_user")
    spot = models.ForeignKey('ParkingSpot', on_delete=models.CASCADE, related_name="booking_spot")
    vehicle = models.ForeignKey('Vehicle', on_delete=models.CASCADE, related_name="booking_vehicle")
    start_time = models.DateTimeField(null=False, blank=False)
    end_time = models.DateTimeField(null=False, blank=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')

    def __str__(self):
        return f"({self.vehicle} - {self.spot} - {self.status})"


class Payment(BaseModel):
    booking = models.ForeignKey('Booking', on_delete=models.CASCADE, null=True, blank=True,
                                related_name="payment_booking")
    subscription = models.ForeignKey('Subscription', on_delete=models.CASCADE, null=True, blank=True,
                                     related_name="payment_sub")
    amount = models.IntegerField(null=False, blank=False)
    payment_method = models.CharField(null=False, blank=False, max_length=50)
    payment_status = models.BooleanField(default=False)
    payment_note = models.CharField(null=True, blank=True, max_length=50)

    def __str__(self):
        return f"({self.payment_method} - {self.amount} - {self.payment_status} - {self.payment_note})"


class ParkingHistory(models.Model):
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name="history_user")
    spot = models.ForeignKey('ParkingSpot', on_delete=models.CASCADE, related_name="history_spot")
    vehicle = models.ForeignKey('Vehicle', on_delete=models.CASCADE, related_name="history_vehicle")
    booking = models.ForeignKey('Booking', on_delete=models.CASCADE, null=True, blank=True,
                                related_name="history_booking")
    subscription = models.ForeignKey('Subscription', on_delete=models.CASCADE, null=True, blank=True,
                                     related_name="history_sub")
    entry_time = models.DateTimeField(null=False, blank=False)
    exit_time = models.DateTimeField(null=True, blank=True)
    entry_image = CloudinaryField(null=True, blank=True)
    exit_image = CloudinaryField(null=True, blank=True)

    def __str__(self):
        return f"({self.vehicle} - {self.spot})"


class Complaint(BaseModel):
    STATUS_CHOICES = [
        ('wait', 'Wait'),
        ('resolved', 'Resolved'),
    ]
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name="complaint_user")
    parking_history = models.ForeignKey('ParkingHistory', on_delete=models.CASCADE, related_name="complaint_history")
    description = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    resolved_at = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"({self.user} - {self.parking_history} - {self.status})"
