from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from rest_framework import status

from greencarpark.models import (
    ParkingLot,
    ParkingSpot,
    Vehicle,
    Booking,
    Subscription,
    SubscriptionType,
    Payment,
    ParkingHistory,
)
from greencarpark.serializers import UserSerializers, BookingSerializers

User = get_user_model()


class GreenCarParkRemediationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Tạo user thường
        self.user1 = User.objects.create_user(
            username='user1',
            password='password123',
            email='user1@example.com',
            first_name='Nguyen',
            last_name='Van A',
            phone_number='0901234567'
        )

        self.user2 = User.objects.create_user(
            username='user2',
            password='password123',
            email='user2@example.com',
            first_name='Tran',
            last_name='Van B',
            phone_number='0909876543'
        )

        # Tạo bãi đỗ xe và chỗ đỗ
        self.parking_lot = ParkingLot.objects.create(
            name='Central Park',
            address='123 Le Loi, D1, HCMC',
            price_per_hour=Decimal('20000.00')
        )

        self.spot = ParkingSpot.objects.create(
            parkinglot=self.parking_lot,
            status='available'
        )

        # Tạo xe
        self.vehicle1 = Vehicle.objects.create(
            user=self.user1,
            license_plate='51A-12345',
            color='White',
            brand='Toyota',
            car_model='Vios'
        )

        self.vehicle2 = Vehicle.objects.create(
            user=self.user2,
            license_plate='51B-67890',
            color='Black',
            brand='Honda',
            car_model='City'
        )

    # ---------------------------------------------------------------------
    # Test 1: Privilege Escalation Prevention
    # ---------------------------------------------------------------------
    def test_user_registration_cannot_escalate_privilege(self):
        """Kiểm tra người dùng không thể tự cấp quyền is_superuser hoặc is_staff qua serializer/registration"""
        payload = {
            'username': 'attacker',
            'password': 'Password123!',
            'email': 'attacker@example.com',
            'phone_number': '0988888888',
            'is_superuser': True,
            'is_staff': True,
        }

        serializer = UserSerializers(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        created_user = serializer.save()

        # Xác minh user tạo ra KHÔNG được là superuser hoặc staff
        self.assertFalse(created_user.is_superuser)
        self.assertFalse(created_user.is_staff)

    # ---------------------------------------------------------------------
    # Test 2: Booking Concurrency Protection
    # ---------------------------------------------------------------------
    @patch('greencarpark.views.create_momo_payment')
    def test_booking_concurrency_same_spot(self, mock_momo):
        """Kiểm tra bảo vệ Concurrency: 2 người cùng book 1 chỗ chỉ 1 người thành công"""
        mock_momo.return_value = {
            'resultCode': 0,
            'payUrl': 'https://test-payment.momo.vn/pay/fake123'
        }

        from greencarpark.views import BookingViewSet

        start_time = timezone.now() + timedelta(hours=1)
        end_time = start_time + timedelta(hours=2)

        # Giả lập Request 1 từ User 1
        view = BookingViewSet()
        
        serializer1 = BookingSerializers(data={
            'spot': self.spot.id,
            'vehicle': self.vehicle1.id,
            'start_time': start_time,
            'end_time': end_time,
        })
        self.assertTrue(serializer1.is_valid())

        # Gán context request cho user1
        class MockRequest1:
            user = self.user1
        view.request = MockRequest1()

        # Thực hiện perform_create lần 1 -> Thành công
        view.perform_create(serializer1)

        # Chỗ đỗ lúc này đã chuyển sang reserved
        self.spot.refresh_from_db()
        self.assertEqual(self.spot.status, 'reserved')

        # Giả lập Request 2 từ User 2 cố book cùng chỗ đỗ đó
        serializer2 = BookingSerializers(data={
            'spot': self.spot.id,
            'vehicle': self.vehicle2.id,
            'start_time': start_time,
            'end_time': end_time,
        })
        self.assertTrue(serializer2.is_valid())

        class MockRequest2:
            user = self.user2
        view.request = MockRequest2()

        # Thực hiện perform_create lần 2 -> Phải bị từ chối với ValidationError
        with self.assertRaises(ValidationError):
            view.perform_create(serializer2)

    # ---------------------------------------------------------------------
    # Test 3: Payment Gateway Failure Rollback (No Zombie Spot)
    # ---------------------------------------------------------------------
    @patch('greencarpark.views.create_momo_payment')
    def test_payment_failure_rolls_back_spot_status(self, mock_momo):
        """Nếu cổng thanh toán MoMo trả về lỗi, ParkingSpot không bị kẹt ở trạng thái 'reserved'"""
        mock_momo.return_value = {
            'resultCode': 99,
            'message': 'Cổng thanh toán MoMo đang bảo trì'
        }

        from greencarpark.views import BookingViewSet
        view = BookingViewSet()

        class MockRequest:
            user = self.user1
        view.request = MockRequest()

        start_time = timezone.now() + timedelta(hours=1)
        end_time = start_time + timedelta(hours=2)

        serializer = BookingSerializers(data={
            'spot': self.spot.id,
            'vehicle': self.vehicle1.id,
            'start_time': start_time,
            'end_time': end_time,
        })
        self.assertTrue(serializer.is_valid())

        # Gọi perform_create -> Bị ném ValidationError do MoMo fail
        with self.assertRaises(ValidationError):
            view.perform_create(serializer)

        # Kiểm tra ParkingSpot: Phải được rollback về 'available', KHÔNG bị kẹt 'reserved'
        self.spot.refresh_from_db()
        self.assertEqual(self.spot.status, 'available')

    # ---------------------------------------------------------------------
    # Test 4: Duplicate Check-in Protection (Active Session Uniqueness)
    # ---------------------------------------------------------------------
    def test_duplicate_checkin_prevented(self):
        """Không cho phép 1 xe có 2 phiên đỗ xe active (exit_time is null) đồng thời"""
        from greencarpark.views import ParkingHistoryViewSet
        view = ParkingHistoryViewSet()

        # Tạo một booking hợp lệ
        now = timezone.now()
        booking = Booking.objects.create(
            user=self.user1,
            spot=self.spot,
            vehicle=self.vehicle1,
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(hours=2),
            status='available'
        )

        class MockRequest:
            user = self.user1
            data = {
                'license_plate': self.vehicle1.license_plate,
                'entry_image': 'image_data_mock'
            }

        view.request = MockRequest()

        from greencarpark.serializers import ParkingHistorySerializers
        serializer = ParkingHistorySerializers(data={})
        self.assertTrue(serializer.is_valid())

        # Check-in lần 1 -> Thành công
        view.perform_create(serializer)

        # Kiểm tra đã có 1 active ParkingHistory
        active_count = ParkingHistory.objects.filter(vehicle=self.vehicle1, exit_time__isnull=True).count()
        self.assertEqual(active_count, 1)

        # Thử Check-in lần 2 với cùng phương tiện khi chưa check-out -> Bị từ chối
        serializer2 = ParkingHistorySerializers(data={})
        self.assertTrue(serializer2.is_valid())
        with self.assertRaises(ValidationError):
            view.perform_create(serializer2)

    # ---------------------------------------------------------------------
    # Test 5: Double Check-out Protection
    # ---------------------------------------------------------------------
    def test_double_checkout_protection(self):
        """Check-out 2 lần liên tiếp: Lần 1 thành công, lần 2 báo lỗi không tìm thấy phiên active"""
        from greencarpark.views import ParkingHistoryViewSet
        view = ParkingHistoryViewSet()

        now = timezone.now()
        # Tạo sẵn 1 active session
        history = ParkingHistory.objects.create(
            user=self.user1,
            spot=self.spot,
            vehicle=self.vehicle1,
            entry_time=now - timedelta(hours=1),
            exit_time=None
        )
        self.spot.status = 'in_use'
        self.spot.save()

        class MockRequest:
            user = self.user1
            data = {
                'license_plate': self.vehicle1.license_plate,
                'exit_image': 'exit_image_mock'
            }

        view.request = MockRequest()

        # Check-out lần 1 -> Trả về HTTP 200
        response1 = view.update(view.request)
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        # Chỗ đỗ được giải phóng
        self.spot.refresh_from_db()
        self.assertEqual(self.spot.status, 'available')

        # Thử Check-out lần 2 -> Bị ném ValidationError vì không còn phiên active
        with self.assertRaises(ValidationError):
            view.update(view.request)

    # ---------------------------------------------------------------------
    # Test 6: Health Check API Endpoint
    # ---------------------------------------------------------------------
    def test_health_check_endpoint(self):
        """Endpoint /health/ trả về 200 OK và trạng thái healthy của Database"""
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data.get('status'), 'healthy')
        self.assertEqual(data.get('database'), 'healthy')
        self.assertIn('db_latency_ms', data)
