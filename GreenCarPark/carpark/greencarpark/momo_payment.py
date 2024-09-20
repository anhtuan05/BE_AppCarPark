import hashlib
import hmac
import uuid
import requests

def create_momo_payment(amount):
    endpoint = "https://test-payment.momo.vn/v2/gateway/api/create"
    partner_code = "MOMO"
    access_key = "F8BBA842ECF85"
    secret_key = "K951B6PE1waDMi640xX08PD3vg6EkVlz"
    request_type = "captureWallet"
    extra_data = ""
    order_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    order_info = "pay with MoMo"
    redirect_url = "https://webhook.site/b3088a6a-2d17-4f8d-a383-71389a6c600b"
    ipn_url = "https://webhook.site/b3088a6a-2d17-4f8d-a383-71389a6c600b"

    # Tạo chữ ký
    raw_signature = f"accessKey={access_key}&amount={amount}&extraData={extra_data}&ipnUrl={ipn_url}&orderId={order_id}&orderInfo={order_info}&partnerCode={partner_code}&redirectUrl={redirect_url}&requestId={request_id}&requestType={request_type}"

    h = hmac.new(bytes(secret_key, 'ascii'), bytes(raw_signature, 'ascii'), hashlib.sha256)
    signature = h.hexdigest()

    data = {
        'partnerCode': partner_code,
        'partnerName': "Test",
        'storeId': "MomoTestStore",
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

    # Gửi yêu cầu POST tới MoMo API
    try:
        response = requests.post(endpoint, json=data, headers={'Content-Type': 'application/json'})
        response_data = response.json()
        return response_data
    except Exception as e:
        return {"error": str(e)}
