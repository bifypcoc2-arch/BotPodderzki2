from telebot.types import Message

from services.admin_service import AdminService


async def role_filter(message: Message, min_level: int) -> bool:
    admin_service = AdminService()
    admin = await admin_service.get_admin(message.from_user.id)
    return admin and admin.role_level >= min_level
