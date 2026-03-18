from datetime import datetime

from src.models.database import get_vip_users, remove_vip


async def check_expired_vips(bot):
    """Revisa y remueve VIPs expirados. Se ejecuta diariamente."""
    vips = await get_vip_users()
    today = datetime.now().strftime("%Y-%m-%d")

    for user in vips:
        if user["vip_expires"] and user["vip_expires"] < today:
            await remove_vip(user["user_id"])
            try:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text="⏰ Tu suscripción VIP ha expirado.\n\n"
                         "Usa /vip para renovar y seguir recibiendo tips exclusivos. 👑",
                )
            except Exception:
                pass
