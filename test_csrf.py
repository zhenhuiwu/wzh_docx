import sys
sys.path.insert(0, 'D:/项目/智能化/server')

from app.identity import get_csrf
from fastapi import Request, Response
from unittest.mock import Mock
import uuid
import traceback

# 创建模拟请求
request = Mock(spec=Request)
request.state = Mock()
request.state.request_id = str(uuid.uuid4())
request.cookies = {}

response = Mock(spec=Response)
response.set_cookie = Mock()

try:
    result = get_csrf(request, response)
    print('✅ CSRF Success!')
    print('Result:', result)
except Exception as e:
    print('❌ CSRF Error:')
    print('Type:', type(e).__name__)
    print('Message:', str(e))
    print('\nFull traceback:')
    traceback.print_exc()
