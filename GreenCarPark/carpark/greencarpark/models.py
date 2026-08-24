
from django.db import models
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
from django.core.validators import MinValueValidator, MaxValueValidator
from rest_framework.exceptions import ValidationError


class BaseModel(models.Model):
    created_date = models.DateTimeField(auto_now_add=True, null=True, db_index=True)
    updated_date = models.DateTimeField(auto_now=True, null=True)

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
    rate = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        null=False,
        blank=False
    )

    class Meta:
        indexes = [
            models.Index(fields=['parkinglot', 'rate'], name='idx_rev_lot_rate'),
        ]

    def __str__(self):
        return f"({self.user} - {self.parkinglot} - {self.rate})"


class ParkingLot(BaseModel):
    name = models.CharField(max_length=50, null=False, blank=False)
    address = models.CharField(max_length=100, null=False, blank=False)
    price_per_hour = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=False,
        blank=False,
        validators=[MinValueValidator(0)]
    )

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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available', db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['parkinglot', 'status'], name='idx_spot_lot_status'),
        ]

    def __str__(self):
        return f"({self.id} - {self.parkinglot.name} - {self.status})"

    def delete(self, using=None, keep_parents=False):
        if self.status in ['reserved', 'in_use']:
            raise ValidationError(f"Cannot delete ParkingSpot with status '{self.status}'.")
        return super().delete(using=using, keep_parents=keep_parents)


class Vehicle(BaseModel):
    user = models.ForeignKey('User', on_delete=models.PROTECT, related_name="vehicle")
    license_plate = models.CharField(null=False, blank=False, max_length=15, unique=True, db_index=True)
    color = models.CharField(null=False, blank=False, max_length=20)
    brand = models.CharField(null=False, blank=False, max_length=20)
    car_model = models.CharField(null=False, blank=False, max_length=20)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'license_plate'], name='idx_veh_user_plate'),
        ]

    def __str__(self):
        return f"({self.license_plate})"


class Subscription(BaseModel):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('cancel', 'Cancel'),
        ('expired', 'Expired'),
    ]
    user = models.ForeignKey('User', on_delete=models.PROTECT, related_name="subscription_user")
    spot = models.ForeignKey('ParkingSpot', on_delete=models.PROTECT, related_name="subscription_spot")
    subscription_type = models.ForeignKey('SubscriptionType', on_delete=models.PROTECT, related_name="sub_type")
    start_date = models.DateField(null=False, db_index=True)
    end_date = models.DateField(null=False, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available', db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status', 'end_date'], name='idx_sub_user_stat_date'),
            models.Index(fields=['spot', 'status'], name='idx_sub_spot_status'),
        ]

    def __str__(self):
        return f"({self.user} - {self.spot} - {self.status})"


class SubscriptionType(BaseModel):
    type = models.CharField(max_length=50, null=False, blank=False, unique=True)
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=False,
        blank=False,
        validators=[MinValueValidator(0)]
    )

    def __str__(self):
        return f"({self.type})"


class Booking(BaseModel):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('in_use', 'In Use'),
        ('disable', 'Disable'),
        ('cancelled', 'Cancelled'),
    ]
    user = models.ForeignKey('User', on_delete=models.PROTECT, related_name="booking_user")
    spot = models.ForeignKey('ParkingSpot', on_delete=models.PROTECT, related_name="booking_spot")
    vehicle = models.ForeignKey('Vehicle', on_delete=models.PROTECT, related_name="booking_vehicle")
    start_time = models.DateTimeField(null=False, blank=False, db_index=True)
    end_time = models.DateTimeField(null=False, blank=False, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available', db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status', 'start_time'], name='idx_bk_usr_stat_time'),
            models.Index(fields=['spot', 'status', 'start_time', 'end_time'], name='idx_bk_spot_stat_time'),
            models.Index(fields=['vehicle', 'status'], name='idx_bk_veh_stat'),
        ]

    def __str__(self):
        return f"({self.vehicle} - {self.spot} - {self.status})"


class Payment(BaseModel):
    booking = models.ForeignKey('Booking', on_delete=models.PROTECT, null=True, blank=True,
                                related_name="payment_booking")
    subscription = models.ForeignKey('Subscription', on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="payment_sub")
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=False,
        blank=False,
        validators=[MinValueValidator(0)]
    )
    payment_method = models.CharField(null=False, blank=False, max_length=50)
    payment_status = models.BooleanField(default=False, db_index=True)
    payment_note = models.CharField(null=True, blank=True, max_length=100)

    class Meta:
        indexes = [
            models.Index(fields=['payment_status', 'created_date'], name='idx_pay_stat_created'),
        ]

    def __str__(self):
        return f"({self.payment_method} - {self.amount} - {self.payment_status} - {self.payment_note})"


class ParkingHistory(BaseModel):
    user = models.ForeignKey('User', on_delete=models.PROTECT, related_name="history_user")
    spot = models.ForeignKey('ParkingSpot', on_delete=models.PROTECT, related_name="history_spot")
    vehicle = models.ForeignKey('Vehicle', on_delete=models.PROTECT, related_name="history_vehicle")
    booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="history_booking")
    subscription = models.ForeignKey('Subscription', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="history_sub")
    entry_time = models.DateTimeField(null=False, blank=False, db_index=True)
    exit_time = models.DateTimeField(null=True, blank=True, db_index=True)
    entry_image = CloudinaryField(null=True, blank=True)
    exit_image = CloudinaryField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'exit_time'], name='idx_hist_user_exit'),
            models.Index(fields=['vehicle', 'exit_time'], name='idx_hist_veh_exit'),
            models.Index(fields=['spot', 'exit_time'], name='idx_hist_spot_exit'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['vehicle'],
                condition=models.Q(exit_time__isnull=True),
                name='unique_active_parking_per_vehicle'
            )
        ]

    def __str__(self):
        return f"({self.vehicle} - {self.spot})"


class Complaint(BaseModel):
    STATUS_CHOICES = [
        ('wait', 'Wait'),
        ('resolved', 'Resolved'),
        ('rejected', 'Rejected'),
    ]
    user = models.ForeignKey('User', on_delete=models.PROTECT, related_name="complaint_user")
    parking_history = models.ForeignKey('ParkingHistory', on_delete=models.PROTECT, related_name="complaint_history")
    description = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='wait', db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_comp_usr_stat'),
        ]

    def __str__(self):
        return f"({self.user} - {self.parking_history} - {self.status})"

