import requests
from django.conf import settings


class PayStack:
    PAYSTACK_SECRET_KEY = settings.PAYSTACK_PRIVATE_KEY
    base_url = "https://api.paystack.co/"

    def verify_payment(self, ref, **kwargs):
        path = f'transaction/verify/{ref}'
        headers = {
            'Authorization': f'Bearer {self.PAYSTACK_SECRET_KEY}',
            'content-type': 'application/json',
        }
        url = self.base_url + path
        response = requests.get(url, headers=headers)

        try:
            response_data = response.json()
        except ValueError:
            return False, "Invalid response from PayStack"

        if response.status_code == 200:
            return response_data['status'], response_data['data']

        return False, response_data.get('message', 'Unknown error')