import requests, time
url = 'http://127.0.0.1:5002/api/models/1/execute'
r = requests.post(url, timeout=30)
print('status', r.status_code)
print(r.text[:500])
