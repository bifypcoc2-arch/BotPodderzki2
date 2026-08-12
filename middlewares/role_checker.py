from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

from services.admin_service import AdminService


class RoleCheckerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        admin_service = AdminService()
        admin = await admin_service.get_admin(event.from_user.id)
        data['admin'] = admin
        data['role_level'] = admin.role_level if admin else 0

        return await handler(event, data)
