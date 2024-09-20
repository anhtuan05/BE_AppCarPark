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

from .serializers import *
from rest_framework import viewsets, generics, permissions, status


class UserViewSet(viewsets.ViewSet, generics.CreateAPIView, generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializers

    def get_permissions(self):
        if self.action in ['login_with_face', 'create']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

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

            if dist < 0.6:
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
            "token_type": "Bearer"
        })

    def generate_access_token(self, user, application):
        # Thời gian hết hạn của token
        expires = timezone.now() + timedelta(seconds=oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS)

        # Tạo access token mới
        access_token = AccessToken.objects.create(
            user=user,
            application=application,
            expires=expires,
            token=secrets.token_urlsafe(32)  # Tạo chuỗi ngẫu nhiên cho token
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
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

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
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        user = self.request.user

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
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated()]

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

        print(momo_response)

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
