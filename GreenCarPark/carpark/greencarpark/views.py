import os
import ast
import secrets
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Any, ContextManager, cast

import pytz
import numpy as np

from django.db import models, transaction, connection
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse

from rest_framework import viewsets, generics, permissions, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from oauth2_provider.models import AccessToken, Application
from oauth2_provider.settings import oauth2_settings

from .momo_payment import create_momo_payment
from .permission import HasParkingHistoryScope, DenyParkingHistoryScope, IsOwnerOrReadOnly
from .models import (
    User,
    Reviews,
    ParkingLot,
    ParkingSpot,
    Vehicle,
    Subscription,
    SubscriptionType,
    Booking,
    Payment,
    ParkingHistory,
    Complaint,
)
from .serializers import (
    UserSerializers,
    VehicleSerializer,
    BookingSerializers,
    SubscriptionSerializers,
    ParkingLotSerializers,
    ParkingSpotSerializers,
    SubscriptionTypeSerializers,
    ParkingHistorySerializers,
    ComplaintSerializer,
    ReviewsSerializer,
    PaymentSerializers,
)

logger = logging.getLogger(__name__)


def atomic_transaction() -> ContextManager[Any]:
    """Helper bọc transaction.atomic với Type Hint rõ ràng cho Pyright / static type checkers."""
    return cast(ContextManager[Any], transaction.atomic())


# -------------------------------------------------------------------------
# Health Check Endpoint
# -------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def health_check(request):
    """
    Endpoint kiểm tra trạng thái hoạt động của Database và Service
    Dùng cho Container Orchestration (Kubernetes/Docker) và Load Balancer probes.
    """
    db_status = "healthy"
    db_latency_ms = None
    try:
        start_time = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        duration = timezone.now() - start_time
        db_latency_ms = round(duration.total_seconds() * 1000, 2)
    except Exception as e:
        logger.error(f"Health check database connection failed: {e}")
        db_status = "unhealthy"
        return JsonResponse(
            {"status": "unhealthy", "database": "disconnected", "error": str(e)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )

    return JsonResponse({
        "status": "healthy",
        "database": db_status,
        "db_latency_ms": db_latency_ms,
        "timestamp": timezone.now().isoformat()
    }, status=status.HTTP_200_OK)


# -------------------------------------------------------------------------
# User ViewSet
# -------------------------------------------------------------------------
class UserViewSet(viewsets.ViewSet, generics.CreateAPIView, generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializers

    def get_permissions(self):
        if self.action in ['login_with_face', 'create']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def create(self, request, *args, **kwargs):
        # Đảm bảo người dùng thông thường không thể tự cấp quyền admin
        data = request.data.copy()
        data.pop('is_staff', None)
        data.pop('is_superuser', None)
        serializer = self.serializer_class(data=data)
        if serializer.is_valid():
            serializer.save(is_active=True)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='login-with-face')
    def login_with_face(self, request):
        """
        Nhận diện khuôn mặt:
        Hỗ trợ Pre-filtering theo username / phone_number / license_plate nếu client cung cấp.
        Nếu không, duyệt tối ưu qua danh sách user có face_description.
        """
        face_description_client = request.data.get('face_description')
        identifier = request.data.get('identifier')  # username, phone_number hoặc license_plate

        if not face_description_client:
            return Response({"error": "Face description is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            face_desc_client = self.get_face_description_as_list(face_description_client)
            if not face_desc_client:
                return Response({"error": "Invalid face description array"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"error": "Invalid face description format"}, status=status.HTTP_400_BAD_REQUEST)

        # Pre-filter danh tính nếu có identifier
        users_query = User.objects.exclude(face_description__isnull=True).exclude(face_description='')
        if identifier:
            id_filter = Q(username=identifier)
            id_filter.add(Q(phone_number=identifier), Q.OR)
            id_filter.add(Q(vehicle__license_plate=identifier), Q.OR)
            users_query = users_query.filter(id_filter).distinct()

        # Dùng .only() để giảm bộ nhớ ORM
        matching_user = None
        min_distance = 0.4  # Ngưỡng nhận diện (Threshold)

        for user in users_query.only('id', 'face_description', 'username'):
            face_desc_db = self.get_face_description_as_list(user.face_description)
            if not face_desc_db:
                continue

            try:
                dist = self.euclidean_distance(face_desc_client, face_desc_db)
                if dist < min_distance:
                    matching_user = user
                    break
            except Exception as e:
                logger.warning(f"Error calculating distance for user {user.id}: {e}")
                continue

        if not matching_user:
            return Response({"error": "No matching user found"}, status=status.HTTP_404_NOT_FOUND)

        # Lấy hoặc tạo application CarParkApp an toàn
        application = Application.objects.filter(name="CarParkApp").first()
        if not application:
            application = Application.objects.first()
            if not application:
                return Response({"error": "OAuth2 application not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        token = self.generate_access_token(matching_user, application)

        return Response({
            "access_token": token.token,
            "expires_in": oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            "token_type": "Bearer",
            "scope": token.scope
        })

    def generate_access_token(self, user, application):
        expires = timezone.now() + timedelta(seconds=oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS)
        access_token = AccessToken.objects.create(
            user=user,
            application=application,
            expires=expires,
            token=secrets.token_urlsafe(30),
            scope='parking_history'
        )
        return access_token

    def get_face_description_as_list(self, face_description):
        if face_description:
            try:
                description_list = ast.literal_eval(face_description)
                return [float(x) for x in description_list]
            except (ValueError, SyntaxError):
                return []
        return []

    def euclidean_distance(self, array1, array2):
        arr1 = np.array(array1, dtype=float)
        arr2 = np.array(array2, dtype=float)
        if arr1.shape != arr2.shape:
            raise ValueError("Arrays must have the same length for distance calculation")
        return float(np.linalg.norm(arr1 - arr2))

    @action(detail=False, methods=['get'], url_path='current-user')
    def current_user(self, request):
        user = request.user
        if user.is_authenticated:
            serializer = self.get_serializer(user)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response({"detail": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

    def update(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated:
            partial = kwargs.pop('partial', False)
            data = request.data.copy()
            # Ngăn chặn cập nhật quyền admin từ phía client
            data.pop('is_staff', None)
            data.pop('is_superuser', None)
            serializer = self.get_serializer(user, data=data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)
        return Response({"detail": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)


# -------------------------------------------------------------------------
# Vehicle ViewSet
# -------------------------------------------------------------------------
class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.all()
    serializer_class = VehicleSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Vehicle.objects.filter(user=user).select_related('user')
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


# -------------------------------------------------------------------------
# Booking ViewSet (Concurrency Hardened)
# -------------------------------------------------------------------------
class BookingViewSet(viewsets.ViewSet, generics.ListAPIView, generics.CreateAPIView):
    queryset = Booking.objects.all()
    serializer_class = BookingSerializers

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Booking.objects.filter(user=user).select_related('spot__parkinglot', 'vehicle', 'user')
        return Booking.objects.none()

    def get_permissions(self):
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def perform_create(self, serializer):
        user = self.request.user
        start_time = serializer.validated_data['start_time']
        end_time = serializer.validated_data['end_time']
        spot_data = serializer.validated_data['spot']
        vehicle = serializer.validated_data['vehicle']

        now = timezone.now()
        if start_time < now - timedelta(minutes=5):
            raise ValidationError("Thời gian bắt đầu không được ở trong quá khứ.")
        if start_time > now + timedelta(hours=5):
            raise ValidationError("Thời gian bắt đầu không được vượt quá 5 giờ kể từ hiện tại.")
        if end_time < start_time + timedelta(hours=1):
            raise ValidationError("Thời gian kết thúc phải lớn hơn thời gian bắt đầu ít nhất 1 giờ.")

        if vehicle.user != user:
            raise ValidationError("Phương tiện không thuộc về tài khoản hiện tại.")

        # Xử lý Concurrency & Transaction với Pessimistic Lock (select_for_update)
        with atomic_transaction():
            # Khóa dòng ParkingSpot để ngăn 2 user book cùng lúc
            try:
                spot = ParkingSpot.objects.select_for_update().select_related('parkinglot').get(id=spot_data.id)
            except ParkingSpot.DoesNotExist:
                raise ValidationError("Chỗ đỗ không tồn tại.")

            if spot.status != 'available':
                raise ValidationError("Chỗ đỗ này hiện không khả dụng.")

            # Kiểm tra xem có booking nào khác trùng thời gian đang hiệu lực không
            conflict_booking = Booking.objects.filter(
                spot=spot,
                status__in=['available', 'in_use'],
                start_time__lt=end_time,
                end_time__gt=start_time
            ).exists()

            if conflict_booking:
                raise ValidationError("Chỗ đỗ đã có lịch đặt trùng với khoảng thời gian đã chọn.")

            # Kiểm tra xem xe đã có booking trùng giờ chưa
            vehicle_conflict = Booking.objects.filter(
                vehicle=vehicle,
                status__in=['available', 'in_use'],
                start_time__lt=end_time,
                end_time__gt=start_time
            ).exists()

            if vehicle_conflict:
                raise ValidationError("Phương tiện này đã có lịch đặt trong khoảng thời gian trên.")

            # Tạo booking với trạng thái ban đầu 'disable' (chờ thanh toán)
            booking = serializer.save(user=user, status='disable')

            # Đánh dấu spot là reserved trong lúc xử lý thanh toán
            spot.status = 'reserved'
            spot.save(update_fields=['status', 'updated_date'])

            # Tính toán số tiền
            amount = self.calculate_amount(booking, spot.parkinglot.price_per_hour)

            payment = Payment.objects.create(
                booking=booking,
                amount=amount,
                payment_method='MoMo',
                payment_status=False
            )

        # Gọi cổng thanh toán MoMo bên ngoài lock để tránh giữ DB lock quá lâu
        momo_response = create_momo_payment(amount=int(amount))

        if isinstance(momo_response, dict) and momo_response.get('resultCode') == 0:
            short_link = momo_response.get('payUrl')
            booking.short_link = short_link
            booking.status = 'available'
            booking.save(update_fields=['status', 'updated_date'])

            payment.payment_status = True
            payment.payment_note = "Booking payment"
            payment.save(update_fields=['payment_status', 'payment_note', 'updated_date'])

            # Gửi email thông báo (fail_silently để không phá vỡ transaction)
            content = (
                f"Bạn đã đặt chỗ thành công tại {spot.parkinglot.name}\n"
                f"Địa chỉ: {spot.parkinglot.address}\n"
                f"Mã Booking: {booking.id}\nMã chỗ: {spot.id}\n"
                f"Link thanh toán: {short_link}"
            )
            try:
                send_mail(
                    subject="Đặt chỗ tại Green Car Park thành công",
                    message=content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception as e:
                logger.warning(f"Failed to send booking email: {e}")
        else:
            # MoMo thất bại: Rollback trạng thái chỗ đỗ để tránh Zombie Reserved State
            with atomic_transaction():
                spot.refresh_from_db()
                spot.status = 'available'
                spot.save(update_fields=['status', 'updated_date'])

                booking.refresh_from_db()
                booking.status = 'cancelled'
                booking.save(update_fields=['status', 'updated_date'])

            error_msg = momo_response.get('message', 'Không thể khởi tạo thanh toán MoMo.') if isinstance(momo_response, dict) else 'Lỗi kết nối cổng thanh toán'
            raise ValidationError({'error': error_msg})

    def calculate_amount(self, booking, price_per_hour):
        time_difference = booking.end_time - booking.start_time
        total_hours = Decimal(str(time_difference.total_seconds() / 3600))
        return Decimal(str(round(total_hours * Decimal(str(price_per_hour)), 2)))


# -------------------------------------------------------------------------
# Subscription ViewSet (Concurrency Hardened)
# -------------------------------------------------------------------------
class SubscriptionViewSet(viewsets.ViewSet, generics.ListAPIView, generics.CreateAPIView):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializers

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Subscription.objects.filter(user=user).select_related('spot__parkinglot', 'subscription_type', 'user')
        return Subscription.objects.none()

    def get_permissions(self):
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def perform_create(self, serializer):
        user = self.request.user
        spot_data = serializer.validated_data['spot']
        subtype = serializer.validated_data['subscription_type']
        amount = subtype.total_amount

        with atomic_transaction():
            try:
                spot = ParkingSpot.objects.select_for_update().select_related('parkinglot').get(id=spot_data.id)
            except ParkingSpot.DoesNotExist:
                raise ValidationError("Chỗ đỗ không tồn tại.")

            if spot.status != 'available':
                raise ValidationError("Chỗ đỗ không khả dụng.")

            # Kiểm tra xem chỗ đỗ đã có ai đăng ký dài hạn còn hạn không
            today = timezone.now().date()
            overlap_sub = Subscription.objects.filter(
                spot=spot,
                status='available',
                end_date__gte=today
            ).exists()

            if overlap_sub:
                raise ValidationError("Chỗ đỗ đã có gói đăng ký đang hoạt động.")

            sub = serializer.save(user=user, status='cancel')
            spot.status = 'reserved'
            spot.save(update_fields=['status', 'updated_date'])

            payment = Payment.objects.create(
                subscription=sub,
                amount=amount,
                payment_method='MoMo',
                payment_status=False
            )

        # Gọi MoMo ngoài transaction lock
        momo_response = create_momo_payment(amount=int(amount))

        if isinstance(momo_response, dict) and momo_response.get('resultCode') == 0:
            short_link = momo_response.get('payUrl')
            sub.short_link = short_link
            sub.status = 'available'
            sub.save(update_fields=['status', 'updated_date'])

            payment.payment_status = True
            payment.payment_note = f"Subscription {subtype.type}"
            payment.save(update_fields=['payment_status', 'payment_note', 'updated_date'])

            content = (
                f"Bạn đã đăng ký chỗ thành công tại {spot.parkinglot.name}\n"
                f"Địa chỉ: {spot.parkinglot.address}\n"
                f"Mã Subscription: {sub.id}\nMã chỗ: {spot.id}"
            )
            try:
                send_mail(
                    subject="Đăng ký chỗ tại Green Car Park thành công",
                    message=content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception as e:
                logger.warning(f"Failed to send subscription email: {e}")
        else:
            with atomic_transaction():
                spot.refresh_from_db()
                spot.status = 'available'
                spot.save(update_fields=['status', 'updated_date'])
                sub.refresh_from_db()
                sub.status = 'cancel'
                sub.save(update_fields=['status', 'updated_date'])

            error_msg = momo_response.get('message', 'Không thể khởi tạo thanh toán MoMo.') if isinstance(momo_response, dict) else 'Lỗi kết nối cổng thanh toán'
            raise ValidationError({'error': error_msg})

    @action(detail=True, methods=['post'], url_path='renew-subscription')
    def renew_subscription(self, request, pk=None):
        user = request.user
        new_subtype_id = request.data.get('subscription_type')

        with atomic_transaction():
            try:
                subscription = Subscription.objects.select_for_update().select_related('spot__parkinglot').get(id=pk, user=user)
            except Subscription.DoesNotExist:
                return Response({'error': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)

            if subscription.status != 'available':
                return Response({'error': 'Subscription cannot be renewed. Current status is not available.'},
                                status=status.HTTP_400_BAD_REQUEST)

            try:
                new_subtype = SubscriptionType.objects.get(id=int(new_subtype_id))
            except (SubscriptionType.DoesNotExist, TypeError, ValueError):
                return Response({'error': 'Invalid subscription type.'}, status=status.HTTP_400_BAD_REQUEST)

            new_end_date = self.calculate_new_end_date(subscription.end_date, new_subtype.type)
            amount = new_subtype.total_amount

            payment = Payment.objects.create(
                subscription=subscription,
                amount=amount,
                payment_method='MoMo',
                payment_status=False
            )

        momo_response = create_momo_payment(amount=int(amount))
        if isinstance(momo_response, dict) and momo_response.get('resultCode') == 0:
            short_link = momo_response.get('payUrl')
            subscription.short_link = short_link
            subscription.subscription_type = new_subtype
            subscription.end_date = new_end_date
            subscription.save(update_fields=['subscription_type', 'end_date', 'updated_date'])

            payment.payment_status = True
            payment.payment_note = f"{new_subtype.type} lease renewal"
            payment.save(update_fields=['payment_status', 'payment_note', 'updated_date'])

            content = (
                f"Bạn đã gia hạn đăng ký chỗ thành công tại {subscription.spot.parkinglot.name}\n"
                f"Địa chỉ: {subscription.spot.parkinglot.address}\n"
                f"Loại đăng ký: {new_subtype.type}\n"
                f"Hạn mới: {new_end_date}\n"
                f"Mã Subscription: {subscription.id}\nMã chỗ: {subscription.spot.id}"
            )
            try:
                send_mail(
                    subject="Gia hạn chỗ tại Green Car Park thành công",
                    message=content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception as e:
                logger.warning(f"Failed to send renewal email: {e}")

            serializer = SubscriptionSerializers(subscription)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Payment gateway error during renewal.'}, status=status.HTTP_502_BAD_GATEWAY)

    def calculate_new_end_date(self, current_end_date, subtype):
        if subtype == 'monthly':
            return current_end_date + timedelta(days=30)
        elif subtype == 'quarterly':
            return current_end_date + timedelta(days=90)
        return current_end_date + timedelta(days=30)


# -------------------------------------------------------------------------
# ParkingLot & ParkingSpot ViewSets
# -------------------------------------------------------------------------
class ParkingLotViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = ParkingLot.objects.all()
    serializer_class = ParkingLotSerializers

    def get_permissions(self):
        if self.action == 'ratings':
            return [permissions.IsAdminUser()]
        return [permissions.AllowAny(), DenyParkingHistoryScope()]

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser])
    def ratings(self, request):
        parking_lot_ratings = ParkingLot.objects.annotate(
            average_rate=Avg('reviews_parkinglot__rate'),
            total_reviews=Count('reviews_parkinglot'),
            rates_1=Count('reviews_parkinglot', filter=Q(reviews_parkinglot__rate=1)),
            rates_2=Count('reviews_parkinglot', filter=Q(reviews_parkinglot__rate=2)),
            rates_3=Count('reviews_parkinglot', filter=Q(reviews_parkinglot__rate=3)),
            rates_4=Count('reviews_parkinglot', filter=Q(reviews_parkinglot__rate=4)),
            rates_5=Count('reviews_parkinglot', filter=Q(reviews_parkinglot__rate=5)),
        )
        serializer = ParkingLotSerializers(parking_lot_ratings, many=True)
        return Response(serializer.data)


class ParkingSpotViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = ParkingSpot.objects.all().select_related('parkinglot')
    serializer_class = ParkingSpotSerializers

    def get_permissions(self):
        return [permissions.AllowAny(), DenyParkingHistoryScope()]


class SubscriptionTypeViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = SubscriptionType.objects.all()
    serializer_class = SubscriptionTypeSerializers

    def get_permissions(self):
        return [permissions.AllowAny(), DenyParkingHistoryScope()]


# -------------------------------------------------------------------------
# ParkingHistory ViewSet (Check-in / Check-out Concurrency Hardened)
# -------------------------------------------------------------------------
class ParkingHistoryViewSet(viewsets.ViewSet, generics.CreateAPIView, generics.ListAPIView, generics.UpdateAPIView):
    queryset = ParkingHistory.objects.all()
    serializer_class = ParkingHistorySerializers

    def get_permissions(self):
        if self.action == 'list':
            return [permissions.IsAuthenticated()]
        return [HasParkingHistoryScope(), permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return ParkingHistory.objects.filter(user=user).select_related(
                'spot__parkinglot', 'vehicle', 'user', 'booking', 'subscription'
            )
        return ParkingHistory.objects.none()

    def perform_create(self, serializer):
        """
        Check-in Flow:
        Bảo vệ chống duplicate active session và race condition bằng Transaction + Pessimistic Lock.
        """
        user = self.request.user
        license_plate = self.request.data.get('license_plate')
        entry_image = self.request.data.get('entry_image')

        if not entry_image:
            raise ValidationError({"error": "Ảnh lúc vào bãi là bắt buộc."})

        try:
            vehicle = Vehicle.objects.get(user=user, license_plate=license_plate)
        except Vehicle.DoesNotExist:
            raise ValidationError({"error": "Biển số xe không tồn tại cho người dùng này."})

        with atomic_transaction():
            # Kiểm tra xem xe có phiên gửi xe nào chưa hoàn thành không
            active_session = ParkingHistory.objects.select_for_update().filter(
                vehicle=vehicle,
                exit_time__isnull=True
            ).first()

            if active_session:
                raise ValidationError({"error": "Phương tiện này hiện đang có phiên gửi xe đang hoạt động trong bãi."})

            today = timezone.now().date()
            current_time = timezone.now()

            # Tìm Subscription hợp lệ
            subscription = Subscription.objects.select_for_update().select_related('spot__parkinglot').filter(
                user=user,
                status='available',
                start_date__lte=today,
                end_date__gte=today
            ).first()

            if subscription:
                spot = subscription.spot
                spot.status = 'in_use'
                spot.save(update_fields=['status', 'updated_date'])

                serializer.save(
                    user=user,
                    spot=spot,
                    vehicle=vehicle,
                    subscription=subscription,
                    entry_time=current_time,
                    entry_image=entry_image,
                    exit_time=None
                )

                content = (
                    f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã vào bãi\n"
                    f"Địa chỉ: {spot.parkinglot.address}\n"
                    f"Chỗ đỗ: {spot.id}\n"
                    f"Thời gian vào: {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Thời gian hết hạn đăng ký: {subscription.end_date}"
                )
                try:
                    send_mail(
                        subject="Xe đã vào bãi tại Green Car Park",
                        message=content,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=True,
                    )
                except Exception as e:
                    logger.warning(f"Checkin email send failed: {e}")
                return

            # Tìm Booking hợp lệ
            booking = Booking.objects.select_for_update().select_related('spot__parkinglot').filter(
                user=user,
                vehicle=vehicle,
                start_time__lte=current_time,
                end_time__gte=current_time,
                status='available'
            ).first()

            if booking:
                spot = booking.spot
                spot.status = 'in_use'
                spot.save(update_fields=['status', 'updated_date'])

                booking.status = 'in_use'
                booking.save(update_fields=['status', 'updated_date'])

                serializer.save(
                    user=user,
                    spot=spot,
                    vehicle=vehicle,
                    booking=booking,
                    entry_time=current_time,
                    entry_image=entry_image,
                    exit_time=None
                )

                content = (
                    f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã vào bãi\n"
                    f"Địa chỉ: {spot.parkinglot.address}\n"
                    f"Chỗ đỗ: {spot.id}\n"
                    f"Thời gian vào: {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Thời gian ra dự kiến: {booking.end_time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                try:
                    send_mail(
                        subject="Xe đã vào bãi tại Green Car Park",
                        message=content,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=True,
                    )
                except Exception as e:
                    logger.warning(f"Checkin email send failed: {e}")
                return

            raise ValidationError({"error": "Không tìm thấy gói Subscription hoặc lịch Booking hợp lệ cho xe này."})

    def update(self, request, *args, **kwargs):
        """
        Check-out Flow:
        Khóa phiên gửi xe và chỗ đỗ, ngăn chặn tính tiền phạt 2 lần hoặc double checkout.
        """
        user = self.request.user
        license_plate = self.request.data.get('license_plate')
        exit_image = self.request.data.get('exit_image')

        if not exit_image:
            raise ValidationError({"error": "Ảnh lúc ra bãi là bắt buộc."})

        try:
            vehicle = Vehicle.objects.get(user=user, license_plate=license_plate)
        except Vehicle.DoesNotExist:
            raise ValidationError({"error": "Không tìm thấy phương tiện cho người dùng này."})

        current_time = timezone.now()
        penalty_payment = None
        short_link = None

        with atomic_transaction():
            parking_history = ParkingHistory.objects.select_for_update().select_related(
                'spot__parkinglot', 'subscription', 'booking'
            ).filter(
                user=user,
                vehicle=vehicle,
                exit_time__isnull=True
            ).first()

            if not parking_history:
                raise ValidationError({"error": "Không tìm thấy phiên gửi xe đang hoạt động cho phương tiện này."})

            spot = ParkingSpot.objects.select_for_update().get(id=parking_history.spot_id)

            if parking_history.subscription:
                subscription = parking_history.subscription
                today = current_time.date()
                if subscription.start_date <= today <= subscription.end_date:
                    spot.status = 'reserved'
                    spot.save(update_fields=['status', 'updated_date'])
                else:
                    # Quá hạn subscription
                    end_datetime = timezone.make_aware(
                        datetime.combine(subscription.end_date, datetime.min.time())
                    ) if timezone.is_naive(current_time) else datetime.combine(subscription.end_date, datetime.min.time(), tzinfo=timezone.utc)
                    duration = current_time - end_datetime
                    penalty_amount = Decimal(str(self.calculate_penalty(duration)))

                    spot.status = 'available'
                    spot.save(update_fields=['status', 'updated_date'])

                    subscription.status = 'cancel'
                    subscription.save(update_fields=['status', 'updated_date'])

                    if penalty_amount > 0:
                        penalty_payment = Payment.objects.create(
                            subscription=subscription,
                            amount=penalty_amount,
                            payment_method='MoMo',
                            payment_status=False,
                            payment_note="penalty_payment"
                        )
            elif parking_history.booking:
                booking = parking_history.booking
                spot.status = 'available'
                spot.save(update_fields=['status', 'updated_date'])

                booking.status = 'disable'
                booking.save(update_fields=['status', 'updated_date'])

                if current_time > booking.end_time:
                    duration = current_time - booking.end_time
                    penalty_amount = Decimal(str(self.calculate_penalty(duration)))
                    if penalty_amount > 0:
                        penalty_payment = Payment.objects.create(
                            booking=booking,
                            amount=penalty_amount,
                            payment_method='MoMo',
                            payment_status=False,
                            payment_note="penalty_payment"
                        )
            else:
                spot.status = 'available'
                spot.save(update_fields=['status', 'updated_date'])

            parking_history.exit_image = exit_image
            parking_history.exit_time = current_time
            parking_history.save(update_fields=['exit_image', 'exit_time', 'updated_date'])

        # Xử lý thanh toán phạt nếu có
        if penalty_payment:
            momo_resp = create_momo_payment(int(penalty_payment.amount))
            if isinstance(momo_resp, dict) and momo_resp.get('resultCode') == 0:
                short_link = momo_resp.get('payUrl')
                penalty_payment.payment_status = True
                penalty_payment.save(update_fields=['payment_status', 'updated_date'])

                mail_content = (
                    f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã ĐỖ QUÁ GIỜ\n"
                    f"Địa chỉ: {spot.parkinglot.address}\nChỗ đỗ: {spot.id}\n"
                    f"Số tiền phạt: {penalty_payment.amount:,.0f} VNĐ\n"
                    f"Link thanh toán: {short_link}"
                )
                try:
                    send_mail(
                        subject="BẠN BỊ PHẠT DO QUÁ GIỜ",
                        message=mail_content,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=True,
                    )
                except Exception as e:
                    logger.warning(f"Penalty email send failed: {e}")

        # Gửi thông báo ra bãi thành công
        exit_content = (
            f"{user.first_name} {user.last_name} ơi! Xe {vehicle.license_plate} của bạn đã ra bãi an toàn.\n"
            f"Địa chỉ: {spot.parkinglot.address}\nChỗ đỗ: {spot.id}\n"
            f"Thời gian ra: {current_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            send_mail(
                subject="Xe đã ra bãi tại Green Car Park",
                message=exit_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception as e:
            logger.warning(f"Exit email send failed: {e}")

        return Response({
            "success": "Xe đã check-out thành công.",
            "penalty_amount": penalty_payment.amount if penalty_payment else 0,
            "penalty_pay_url": short_link
        }, status=status.HTTP_200_OK)

    def calculate_penalty(self, duration):
        total_minutes = duration.total_seconds() / 60
        if total_minutes <= 15:
            return 0
        elif 15 < total_minutes <= 120:
            return 50000
        elif 120 < total_minutes <= 240:
            return 100000
        elif 240 < total_minutes <= 480:
            return 200000
        elif 480 < total_minutes <= 960:
            return 500000
        else:
            excess_hours = (total_minutes - 480) / 60
            return 500000 + int(excess_hours * 70000)


# -------------------------------------------------------------------------
# Reviews, Complaint, Payment ViewSets
# -------------------------------------------------------------------------
class ReviewsViewSet(viewsets.ModelViewSet):
    queryset = Reviews.objects.all().select_related('user', 'parkinglot')
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
    queryset = Complaint.objects.all().select_related('user', 'parking_history')
    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return Complaint.objects.all().select_related('user', 'parking_history')
        return Complaint.objects.filter(user=user).select_related('user', 'parking_history')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class PaymentViewSet(viewsets.ViewSet, generics.ListAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializers

    def get_permissions(self):
        if self.action == 'revenue_statistics':
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated(), DenyParkingHistoryScope()]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Payment.objects.none()
        if user.is_staff or user.is_superuser:
            return Payment.objects.all().select_related(
                'booking__spot__parkinglot', 'subscription__spot__parkinglot', 'booking__vehicle'
            )
        return Payment.objects.filter(
            Q(booking__user=user) | Q(subscription__user=user)
        ).select_related(
            'booking__spot__parkinglot', 'subscription__spot__parkinglot', 'booking__vehicle'
        )

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser])
    def revenue_statistics(self, request):
        revenue = Payment.objects.filter(payment_status=True).values(
            'created_date__year', 'created_date__month'
        ).annotate(total_amount=Sum('amount')).order_by('created_date__year', 'created_date__month')

        revenue_by_month = {}
        for entry in revenue:
            if entry['created_date__year'] and entry['created_date__month']:
                year_month = f"{entry['created_date__year']}-{entry['created_date__month']:02d}"
                revenue_by_month[year_month] = float(entry['total_amount'] or 0)

        return Response(revenue_by_month, status=status.HTTP_200_OK)
