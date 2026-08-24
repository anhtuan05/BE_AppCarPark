import os
import hashlib
import hmac
import uuid
import logging
import requests

logger = logging.getLogger(__name__)

def create_momo_payment(amount):
    endpoint = os.environ.get("MOMO_ENDPOINT", "https://test-payment.momo.vn/v2/gateway/api/create")
    partner_code = os.environ.get("MOMO_PARTNER_CODE", "MOMO")
    access_key = os.environ.get("MOMO_ACCESS_KEY", "F8BBA842ECF85")
    secret_key = os.environ.get("MOMO_SECRET_KEY", "K951B6PE1waDMi640xX08PD3vg6EkVlz")
    request_type = "captureWallet"
    extra_data = ""
    order_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    order_info = "pay with MoMo"
    redirect_url = os.environ.get("MOMO_REDIRECT_URL", "https://webhook.site/b3088a6a-2d17-4f8d-a383-71389a6c600b")
    ipn_url = os.environ.get("MOMO_IPN_URL", "https://webhook.site/b3088a6a-2d17-4f8d-a383-71389a6c600b")

    # Tạo chữ ký HMAC SHA256
    raw_signature = (
        f"accessKey={access_key}&amount={amount}&extraData={extra_data}"
        f"&ipnUrl={ipn_url}&orderId={order_id}&orderInfo={order_info}"
        f"&partnerCode={partner_code}&redirectUrl={redirect_url}"
        f"&requestId={request_id}&requestType={request_type}"
    )

    h = hmac.new(bytes(secret_key, 'ascii'), bytes(raw_signature, 'ascii'), hashlib.sha256)
    signature = h.hexdigest()

    data = {
        'partnerCode': partner_code,
        'partnerName': "GreenCarPark",
        'storeId': "GreenCarParkStore",
        'requestId': request_id,
        'amount': amount,
        'orderId': order_id,
        'orderInfo': order_info,
        'redirectUrl': redirect_url,
        'ipnUrl': ipn_url,
        'lang': "vi",
        'extraData': extra_data,
        'requestType': request_type,
        'signature': signature
    }

    # Gửi yêu cầu POST tới MoMo API với timeout kiểm soát
    try:
        response = requests.post(
            endpoint,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=(5.0, 10.0)  # (connect_timeout, read_timeout)
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logger.error("MoMo payment API call timed out after 10s")
        return {"error": "Payment gateway timed out. Please try again."}
    except requests.exceptions.RequestException as e:
        logger.error(f"MoMo payment API error: {e}")
        return {"error": f"Payment gateway communication error: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error creating MoMo payment: {e}")
        return {"error": str(e)}
