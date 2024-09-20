from datetime import datetime
from dateutil.relativedelta import relativedelta

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from .models import *


class UserSerializers(ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'username', 'password', 'email', 'date_of_birth', 'phone_number',
                  'face_description']
        extra_kwargs = {
            'password': {
                'write_only': True
            },
            'face_description': {
                'write_only': True
            }
        }

    def create(self, validated_data):
        user = User(**validated_data)
        user.set_password(validated_data['password'])
        user.save()

        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()

        return user


class VehicleSerializer(ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ['id', 'user', 'license_plate', 'color', 'brand', 'car_model']
        read_only_fields = ['user']


class BookingSerializers(ModelSerializer):
    total_hours = serializers.SerializerMethodField()
    short_link = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = ['id', 'user', 'spot', 'vehicle', 'start_time', 'end_time', 'status', 'total_hours', 'short_link']
        read_only_fields = ['user', 'status']

    def get_total_hours(self, obj):
        # Tính toán số giờ và số tiền dựa trên start_time và end_time
        time_difference = obj.end_time - obj.start_time
        total_hours = time_difference.total_seconds() / 3600
        return total_hours

    def get_short_link(self, obj):
        return obj.short_link if hasattr(obj, 'short_link') else None


class SubscriptionSerializers(ModelSerializer):
    short_link = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = ['id', 'user', 'spot', 'subscription_type', 'start_date', 'end_date', 'status', 'short_link']
        read_only_fields = ['user', 'start_date', 'end_date']

    def get_short_link(self, obj):
        return obj.short_link if hasattr(obj, 'short_link') else None

    def calculate_end_date(self, subscription_type):
        start_date = datetime.now().date()

        if subscription_type == 'monthly':
            end_date = start_date + relativedelta(months=1)
        elif subscription_type == 'quarterly':
            end_date = start_date + relativedelta(months=3)
        else:
            raise ValueError("Invalid subscription type")

        return start_date, end_date

    def create(self, validated_data):
        subscription_type = validated_data.get('subscription_type')

        start_date, end_date = self.calculate_end_date(subscription_type.type)

        validated_data['start_date'] = start_date
        validated_data['end_date'] = end_date

        return super().create(validated_data)

    # update start_day end_day theo subtype
    def update(self, instance, validated_data):
        subscription_type = validated_data.get('subscription_type', instance.subscription_type)

        if subscription_type != instance.subscription_type:
            start_date, end_date = self.calculate_end_date(subscription_type.type)
            instance.start_date = start_date
            instance.end_date = end_date

        return super().update(instance, validated_data)