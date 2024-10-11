import pytz
from django.db.models import Avg, Count, Q, Sum
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from oauth2_provider.models import AccessToken, Application, RefreshToken
from oauth2_provider.settings import oauth2_settings

from django.utils import timezone

from django.core.mail import send_mail
from django.conf import settings

from datetime import timedelta

import ast
import numpy as np
import secrets
from .momo_payment import create_momo_payment
from .permission import HasParkingHistoryScope, DenyParkingHistoryScope, IsOwnerOrReadOnly

from .serializers import *
from rest_framework import viewsets, generics, permissions, status
from .models import *


class UserViewSet(viewsets.ViewSet, generics.CreateAPIView, generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializers

    def get_permissions(self):
        if self.action in ['login_with_face', 'create']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    @action(detail=False, methods=['post'], url_path='login-with-face')
    def login_with_face(self, request):
        face_description_client = request.data.get('face_description')

        if not face_description_client:
            return Response({"error": "Face description is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            face_desc_client = self.get_face_description_as_list(face_description_client)
        except Exception as e:
            return Response({"error": "Invalid face description format"}, status=status.HTTP_400_BAD_REQUEST)

        # Lấy tất cả các user từ cơ sở dữ liệu để so sánh
        users = User.objects.exclude(face_description__isnull=True)

        matching_user = None
        for user in users:
            face_description_db = user.face_description
            face_desc_db = self.get_face_description_as_list(face_description_db)

            try:
                dist = self.euclidean_distance(face_desc_client, face_desc_db)
            except Exception as e:
                print(f"Error calculating distance: {e}")
                continue

            if dist < 0.4:
                matching_user = user
                break

        if not matching_user:
            return Response({"error": "No matching user found"}, status=status.HTTP_404_NOT_FOUND)

        # Tạo access token cho user
        application = Application.objects.get(name="CarParkApp")
        token = self.generate_access_token(matching_user, application)

        return Response({
            "access_token": token.token,
            "expires_in": oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            "token_type": "Bearer",
            "scope": token.scope
        })

    def generate_access_token(self, user, application):
        # Thời gian hết hạn của token
        expires = timezone.now() + timedelta(seconds=oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS)

        # Tạo access token mới
        access_token = AccessToken.objects.create(
            user=user,
            application=application,
            expires=expires,
            token=secrets.token_urlsafe(30),  # Tạo chuỗi ngẫu nhiên cho token
            scope='parking_history'
        )

        # # (Tùy chọn) Tạo refresh token nếu cần
        # refresh_token = RefreshToken.objects.create(
        #     user=user,
        #     token=secrets.token_urlsafe(32),
        #     access_token=access_token,
        #     application=application
        # )

        return access_token

    def get_face_description_as_list(self, face_description):
        if face_description:
            try:
                # Chuyển đổi chuỗi danh sách thành danh sách số thực
                description_list = ast.literal_eval(face_description)
                # Chuyển đổi từng phần tử sang float
                return [float(x) for x in description_list]
            except (ValueError, SyntaxError):
                return []

    def euclidean_distance(self, array1, array2):
        # Chuyển đổi các danh sách thành mảng numpy
        arr1 = np.array(array1, dtype=float)
        arr2 = np.array(array2, dtype=float)

        if arr1.shape != arr2.shape:
            raise ValueError("Arrays must have the same length for distance calculation")

        # Tính toán khoảng cách Euclidean
        return np.linalg.norm(arr1 - arr2)

    @action(detail=False, methods=['get'], url_path='current-user')
    def current_user(self, request):
        user = request.user
        if user.is_authenticated:
            serializer = self.get_serializer(user)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Authentication credentials were not provided."},
                            status=status.HTTP_401_UNAUTHORIZED)

    def update(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated:
            # the current user is the one being updated
            partial = kwargs.pop('partial', False)
            instance = user  # Use the current user instance
            serializer = self.get_serializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)
        else:
            return Response({"detail": "Authentication credentials were not provided."},
                            status=status.HTTP_401_UNAUTHORIZED)

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            user.is_active = True  # Kích hoạt người dùng
            user.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.all()
    serializer_class = VehicleSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Vehicle.objects.filter(user=user)
        return Vehicle.objects.none()

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]
        return [permissions.AllowAny(), DenyParkingHistoryScope()]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        vehicle = self.get_object()
        if vehicle.user != self.request.user:
            raise PermissionDenied("You do not have permission to edit this vehicle.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.user != self.request.user:
            raise PermissionDenied("You do not have permission to delete this vehicle.")
        instance.delete()


class BookingViewSet(viewsets.ViewSet, generics.ListAPIView, generics.CreateAPIView):
    queryset = Booking.objects.all()
    serializer_class = BookingSerializers

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Booking.objects.filter(user=user)
        return Booking.objects.none()

    def get_permissions(self):
        if self.action in ['list', 'create']:
            return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def perform_create(self, serializer):
        user = self.request.user

        local_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        current_time = datetime.now(local_tz)
        start_time = serializer.validated_data['start_time']
        end_time = serializer.validated_data['end_time']
        if current_time.tzinfo is not None:
            current_time = current_time.replace(tzinfo=None)
        if start_time.tzinfo is not None:
            start_time = start_time.replace(tzinfo=None)
        if end_time.tzinfo is not None:
            end_time = end_time.replace(tzinfo=None)
        if start_time < current_time:
            raise ValidationError("Start time cannot be in the past.")
        elif start_time > current_time + timedelta(hours=5):
            raise ValidationError("Start time cannot be more than 5 hours from now.")
        if end_time < start_time + timedelta(hours=1):
            raise ValidationError("The end time must be at least 1 hour greater than the start time.")

        spot = serializer.validated_data['spot']
        vehicle = serializer.validated_data['vehicle']

        if vehicle.user != user:
            raise ValidationError("Vehicle does not belong to the current user.")

        if spot.status != 'available':
            raise ValidationError("Parking spot is not available.")

        booking = serializer.save(user=user, status='disable')  # Tạo Booking với trạng thái 'disable'

        spot = ParkingSpot.objects.get(id=booking.spot.id)
        spot.status = 'reserved'
        spot.save()

        amount = (int)(self.calculate_amount(booking))

        payment = Payment.objects.create(
            booking=booking,
            amount=amount,  # Số tiền dựa trên thời gian sử dụng
            payment_method='MoMo',
            payment_status=False
        )
        payment.save()

        momo_response = create_momo_payment(
            amount=amount
        )

        if isinstance(momo_response, dict):
            if momo_response.get('resultCode') == 0:
                short_link = momo_response.get('payUrl')
                booking.short_link = short_link  # Thêm short_link vào đối tượng Booking
                booking.status = 'available'
                booking.save()

                payment.payment_status = True
                payment_note_content = (f"Booking")
                payment.payment_note = payment_note_content
                payment.save()

                content = (f"Bạn đã đặt chỗ thành công tại {booking.spot.parkinglot.name}"
                           f"\nĐịa chỉ: {booking.spot.parkinglot.address}"
                           f"\nMã Booking: {booking.id}\nMã chỗ: {booking.spot.id}")

                send_mail(
                    subject="Đặt chỗ tại Green Car Park thành công",
                    message=content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[booking.user.email],
                    fail_silently=False,
                )

                serializer = BookingSerializers(booking)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                return Response({'error': momo_response.get('message', 'Unknown error')},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'Invalid response format'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def calculate_amount(self, booking):
        start_time = booking.start_time
        end_time = booking.end_time
        time_difference = end_time - start_time
        total_hours = time_difference.total_seconds() / 3600
        return int(total_hours * booking.spot.parkinglot.price_per_hour)


class SubscriptionViewSet(viewsets.ViewSet, generics.ListAPIView, generics.CreateAPIView):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializers

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Subscription.objects.filter(user=user)
        return Subscription.objects.none()

    def get_permissions(self):
        if self.action in ['list', 'create']:
            return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def perform_create(self, serializer):
        user = self.request.user

        spot = serializer.validated_data['spot']

        if spot.status != 'available':
            raise ValidationError("Parking spot is not available.")

        sub = serializer.save(user=user, status='cancel')  # Tạo Sub với trạng thái 'cancel'

        spot = ParkingSpot.objects.get(id=sub.spot.id)
        spot.status = 'reserved'
        spot.save()

        subtype = serializer.validated_data['subscription_type']
        amount = (int)(subtype.total_amount)

        payment = Payment.objects.create(
            subscription=sub,
            amount=amount,
            payment_method='MoMo',
            payment_status=False
        )
        payment.save()

        momo_response = create_momo_payment(
            amount=amount
        )

        if isinstance(momo_response, dict):
            if momo_response.get('resultCode') == 0:
                short_link = momo_response.get('payUrl')
                sub.short_link = short_link  # Thêm short_link vào đối tượng Sub
                sub.status = 'available'
                sub.save()

                payment.payment_status = True
                payment_note_content = (f"Subscription")
                payment.payment_note = payment_note_content
                payment.save()

                content = (f"Bạn đã đăng ký chỗ thành công tại {sub.spot.parkinglot.name}"
                           f"\nĐịa chỉ: {sub.spot.parkinglot.address}"
                           f"\nMã Subscription: {sub.id}\nMã chỗ: {sub.spot.id}")

                send_mail(
                    subject="Đăng ký chỗ tại Green Car Park thành công",
                    message=content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[sub.user.email],
                    fail_silently=False,
                )

                serializer = SubscriptionSerializers(sub)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                return Response({'error': momo_response.get('message', 'Unknown error')},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'Invalid response format'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='renew-subscription')
    def renew_subscription(self, request, pk=None):
        user = request.user
        new_subtype_id = request.data.get('subscription_type')

        try:
            subscription = Subscription.objects.get(id=pk, user=user)

            if subscription.status != 'available':
                return Response({'error': 'Subscription cannot be renewed. Current status is not available.'},
                                status=status.HTTP_400_BAD_REQUEST)

            try:
                new_subtype = SubscriptionType.objects.get(id=int(new_subtype_id))
            except SubscriptionType.DoesNotExist:
                return Response({'error': 'Invalid subscription type.'}, status=status.HTTP_400_BAD_REQUEST)

            new_end_date = self.calculate_new_end_date(subscription.end_date, new_subtype.type)

            payment = Payment.objects.create(
                subscription=subscription,
                amount=(int)(new_subtype.total_amount),
                payment_method='MoMo',
                payment_status=False
            )
            payment.save()

            momo_response = create_momo_payment((int)(new_subtype.total_amount))
            if isinstance(momo_response, dict):
                if momo_response.get('resultCode') == 0:
                    short_link = momo_response.get('payUrl')
                    subscription.short_link = short_link
                    subscription.subscription_type = new_subtype
                    subscription.end_date = new_end_date
                    subscription.save()

                    payment.payment_status = True
                    payment_note_content = (f"{new_subtype.type} lease renewal")
                    payment.payment_note = payment_note_content
                    payment.save()

            content = (f"Bạn đã gia hạn đăng ký chỗ thành công tại {subscription.spot.parkinglot.name}"
                       f"\nĐịa chỉ: {subscription.spot.parkinglot.address}"
                       f"\nLoại đăng ký: {new_subtype.type}"
                       f"\nMã Subscription: {subscription.id}\nMã chỗ: {subscription.spot.id}")

            send_mail(
                subject="Gia hạn chỗ tại Green Car Park thành công",
                message=content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[subscription.user.email],
                fail_silently=False,
            )

            serializer = SubscriptionSerializers(subscription)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Subscription.DoesNotExist:
            return Response({'error': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)

    def calculate_new_end_date(self, current_end_date, subtype):
        if subtype == 'monthly':
            return current_end_date + timedelta(days=30)
        elif subtype == 'quarterly':
            return current_end_date + timedelta(days=90)
        # Thêm các loại đăng ký khác nếu cần
        return current_end_date


class ParkingLotViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = ParkingLot.objects.all()
    serializer_class = ParkingLotSerializers

    def get_permissions(self):
        if self.action == 'ratings':
            return [permissions.IsAdminUser()]  # superuser
        return [permissions.AllowAny(), DenyParkingHistoryScope()]

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser])
    def ratings(self, request):
        parking_lot_ratings = ParkingLot.objects.annotate(
            average_rate=Avg('reviews_parkinglot__rate'),
            total_reviews=Count('reviews_parkinglot'),
            rates_1=Count('reviews_parkinglot', filter=models.Q(reviews_parkinglot__rate=1)),
            rates_2=Count('reviews_parkinglot', filter=models.Q(reviews_parkinglot__rate=2)),
            rates_3=Count('reviews_parkinglot', filter=models.Q(reviews_parkinglot__rate=3)),
            rates_4=Count('reviews_parkinglot', filter=models.Q(reviews_parkinglot__rate=4)),
            rates_5=Count('reviews_parkinglot', filter=models.Q(reviews_parkinglot__rate=5)),
        )

        serializer = ParkingLotSerializers(parking_lot_ratings, many=True)
        return Response(serializer.data)


class ParkingSpotViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = ParkingSpot.objects.all()
    serializer_class = ParkingSpotSerializers

    def get_permissions(self):
        return [permissions.AllowAny(), DenyParkingHistoryScope()]


class SubscriptionTypeViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = SubscriptionType.objects.all()
    serializer_class = SubscriptionTypeSerializers

    def get_permissions(self):
        return [permissions.AllowAny(), DenyParkingHistoryScope()]


class ParkingHistoryViewSet(viewsets.ViewSet, generics.CreateAPIView, generics.ListAPIView, generics.UpdateAPIView):
    queryset = ParkingHistory.objects.all()
    serializer_class = ParkingHistorySerializers

    def get_permissions(self):
        return [HasParkingHistoryScope(), permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return ParkingHistory.objects.filter(user=user)
        return ParkingHistory.objects.none()

    def perform_create(self, serializer):
        user = self.request.user
        license_plate = self.request.data.get('license_plate')
        entry_image = self.request.data.get('entry_image')
        local_tz = pytz.timezone('Asia/Ho_Chi_Minh')

        if entry_image is None:
            raise ValidationError({"error": "Entry image is required."})

        try:
            vehicle = Vehicle.objects.get(user=user, license_plate=license_plate)
        except Vehicle.DoesNotExist:
            raise ValidationError({"error": "License plate not found for this user"})

        subscription = Subscription.objects.filter(
            user=user,
            status='available',
            start_date__lte=datetime.now().date(),
            end_date__gte=datetime.now().date()
        )

        available_subscription = None

        for sub in subscription:
            if sub.spot.status == 'reserved':
                available_subscription = sub
                break

        if available_subscription:
            spot = available_subscription.spot
            spot.status = 'in_use'
            spot.save()
            current_time = datetime.now(local_tz)

            serializer.save(
                user=user,
                spot=spot,
                vehicle=vehicle,
                subscription=available_subscription,
                entry_time=current_time.replace(tzinfo=None),
                entry_image=entry_image,
                exit_time=None
            )
            content = (f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã vào bãi"
                       f"\nĐịa chỉ: {spot.parkinglot.address}"
                       f"\nChỗ đỗ: {spot.id}"
                       f"\nThời gian vào: {current_time.replace(tzinfo=None)}"
                       f"\nThời gian hết hạn đăng ký: {available_subscription.end_date}")

            send_mail(
                subject="Xe đã vào bãi tại Green Car Park",
                message=content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[available_subscription.user.email],
                fail_silently=False,
            )
            return
        else:
            current_time = datetime.now(local_tz)
            current_time_naive = current_time.replace(tzinfo=None)
            booking = Booking.objects.filter(
                user=user,
                vehicle=vehicle,
                start_time__lte=current_time_naive,
                end_time__gte=current_time_naive,
                status='available'
            ).first()

            if booking:
                spot = booking.spot
                spot.status = 'in_use'
                spot.save()

                booking.status = 'in_use'
                booking.save()

                serializer.save(
                    user=user,
                    spot=spot,
                    vehicle=vehicle,
                    booking=booking,
                    entry_time=current_time.replace(tzinfo=None),
                    entry_image=entry_image
                )
                content = (f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã vào bãi"
                           f"\nĐịa chỉ: {spot.parkinglot.address}"
                           f"\nChỗ đỗ: {spot.id}"
                           f"\nThời gian vào: {current_time.replace(tzinfo=None)}"
                           f"\nThời gian ra dự kiến: {booking.end_time}")

                send_mail(
                    subject="Xe đã vào bãi tại Green Car Park",
                    message=content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[booking.user.email],
                    fail_silently=False,
                )
                return

        raise ValidationError({"error": "No valid Subscription or Booking found"})

    def update(self, request, *args, **kwargs):
        user = self.request.user
        license_plate = self.request.data.get('license_plate')
        exit_image = self.request.data.get('exit_image')
        local_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        current_time = datetime.now(local_tz)

        if exit_image is None:
            raise ValidationError({"error": "Exit image is required."})

        try:
            vehicle = Vehicle.objects.get(user=user, license_plate=license_plate)
        except Vehicle.DoesNotExist:
            raise ValidationError({"error": "License plate not found for this user."})

        parking_history = ParkingHistory.objects.filter(
            user=user,
            vehicle=vehicle,
            exit_time__isnull=True,
            exit_image__isnull=True
        ).first()

        if not parking_history:
            raise ValidationError({"error": "No active parking history found for this vehicle."})

        if parking_history.subscription:
            subscription = parking_history.subscription
            if subscription.start_date <= datetime.now().date() <= subscription.end_date:
                spot = parking_history.spot
                spot.status = 'reserved'
                spot.save()
            else:
                exit_time = current_time
                end_time = datetime.combine(subscription.end_date, datetime.min.time())
                duration = exit_time.replace(tzinfo=None) - end_time.replace(tzinfo=None)
                penalty_amount = (int)(self.calculate_penalty(duration))
                spot = parking_history.spot
                spot.status = 'available'
                spot.save()

                subscription.status = 'cancel'
                subscription.save()

                if penalty_amount > 0:
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=penalty_amount,
                        payment_method='MoMo',
                        payment_status=False,
                    )
                    payment.save()

                    momo_response = create_momo_payment(penalty_amount)
                    if isinstance(momo_response, dict):
                        if momo_response.get('resultCode') == 0:
                            short_link = momo_response.get('payUrl')

                            content = (
                                f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã ĐỖ QUÁ GIỜ"
                                f"\nĐịa chỉ: {parking_history.spot.parkinglot.address}"
                                f"\nChỗ đỗ: {parking_history.spot.id}"
                                f"\nHÓA ĐƠN: {short_link}")

                            send_mail(
                                subject="BẠN BỊ PHẠT DO QUÁ GIỜ",
                                message=content,
                                from_email=settings.DEFAULT_FROM_EMAIL,
                                recipient_list=[subscription.user.email],
                                fail_silently=False,
                            )

                            payment.payment_status = True
                            payment.payment_note = "penalty_payment"
                            payment.save()

        else:
            if parking_history.booking:
                booking = parking_history.booking
                if booking.start_time.replace(tzinfo=None) <= current_time.replace(
                        tzinfo=None) <= booking.end_time.replace(tzinfo=None):
                    spot = parking_history.spot
                    spot.status = 'available'
                    spot.save()

                    booking.status = 'disabled'
                    booking.save()
                else:
                    spot = booking.spot
                    spot.status = 'available'
                    spot.save()

                    booking.status = 'disabled'
                    booking.save()

                    exit_time = current_time
                    end_time = booking.end_time
                    duration = exit_time.replace(tzinfo=None) - end_time.replace(tzinfo=None)
                    penalty_amount = (int)(self.calculate_penalty(duration))

                    if penalty_amount > 0:
                        payment = Payment.objects.create(
                            booking=booking,
                            amount=penalty_amount,
                            payment_method='MoMo',
                            payment_status=False,
                        )
                        payment.save()

                        momo_response = create_momo_payment(penalty_amount)
                        if isinstance(momo_response, dict):
                            if momo_response.get('resultCode') == 0:
                                short_link = momo_response.get('payUrl')

                                content = (
                                    f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã ĐỖ QUÁ GIỜ"
                                    f"\nĐịa chỉ: {parking_history.spot.parkinglot.address}"
                                    f"\nChỗ đỗ: {parking_history.spot.id}"
                                    f"\nHÓA ĐƠN: {short_link}")

                                send_mail(
                                    subject="BẠN BỊ PHẠT DO QUÁ GIỜ",
                                    message=content,
                                    from_email=settings.DEFAULT_FROM_EMAIL,
                                    recipient_list=[booking.user.email],
                                    fail_silently=False,
                                )

                                payment.payment_status = True
                                payment.payment_note = "penalty_payment"
                                payment.save()

        parking_history.exit_image = exit_image
        parking_history.exit_time = current_time.replace(tzinfo=None)
        parking_history.save()

        content = (f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã ra bãi"
                   f"\nĐịa chỉ: {parking_history.spot.parkinglot.address}"
                   f"\nChỗ đỗ: {parking_history.spot.id}"
                   f"\nThời gian: {current_time.replace(tzinfo=None)}")

        send_mail(
            subject="Xe đã ra bãi tại Green Car Park",
            message=content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[parking_history.user.email],
            fail_silently=False,
        )

        return Response({"success": "Parking history updated successfully."})

    def calculate_penalty(self, duration):
        total_minutes = duration.total_seconds() / 60
        if total_minutes <= 15:
            return 0  # Free for the first 15 minutes
        elif 15 < total_minutes <= 120:  # 1-2 hours
            return 50000
        elif 120 < total_minutes <= 240:  # 2-4 hours
            return 100000
        elif 240 < total_minutes <= 480:  # 4-8 hours
            return 200000
        elif 480 < total_minutes <= 480 * 2:  # 8 hours
            return 500000
        else:  # Over 8 hours
            excess_hours = (total_minutes - 480) / 60
            return 500000 + (excess_hours * 70000)


class ReviewsViewSet(viewsets.ModelViewSet):
    queryset = Reviews.objects.all()
    serializer_class = ReviewsSerializer

    def get_permissions(self):
        return [IsOwnerOrReadOnly(), permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        if instance.user != request.user:
            return Response({"detail": "You do not have permission to modify this review."},
                            status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.user != request.user:
            return Response({"detail": "You do not have permission to delete this review."},
                            status=status.HTTP_403_FORBIDDEN)
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ComplaintViewSet(viewsets.ModelViewSet):
    queryset = Complaint.objects.all()
    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated(), DenyParkingHistoryScope()]


class PaymentViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializers

    def get_permissions(self):
        if self.action == 'revenue_statistics':
            return [permissions.IsAdminUser()]  # superuser
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def get_queryset(self):
        return Payment.objects.filter(
            Q(booking__user=self.request.user) | Q(subscription__user=self.request.user),
            Q(booking__isnull=True) | Q(subscription__isnull=True)
        )

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser])
    def revenue_statistics(self, request):
        payments = Payment.objects.filter(payment_status=True)
        revenue = payments.values('created_date__year', 'created_date__month').annotate(total_amount=Sum('amount'))
        revenue_by_month = {}
        for entry in revenue:
            year_month = f"{entry['created_date__year']}-{entry['created_date__month']:02d}"
            revenue_by_month[year_month] = entry['total_amount']

        return Response(revenue_by_month, status=status.HTTP_200_OK)
