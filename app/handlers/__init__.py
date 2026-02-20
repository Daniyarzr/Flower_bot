from aiogram import Router
from .user import router as user_router
from .admin import router as admin_router
from .user_request import router as request_router
from .user_my_requests import router as my_req_router

# Порядок важен: admin обычно первым для фильтрации
routers = [
    admin_router,
    user_router,
    request_router,
    my_req_router
]