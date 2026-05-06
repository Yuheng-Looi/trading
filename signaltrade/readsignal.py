import os
import json
import asyncio
import sys

async def iter_whatsapp_notifications(poll_interval=0.0):
    """Yield new WhatsApp toast notifications from Windows Action Center."""
    try:
        from winrt.windows.ui.notifications.management import (  # type: ignore
            UserNotificationListener,
            UserNotificationListenerAccessStatus,
        )
        from winrt.windows.ui.notifications import NotificationKinds  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "WinRT notification packages are not installed in this environment."
        ) from e

    listener = UserNotificationListener.current
    access_status = await listener.request_access_async()
    if access_status != UserNotificationListenerAccessStatus.ALLOWED:
        raise RuntimeError("Notification access not granted")

    seen_notification_ids = set()

    while True:
        notifications = await listener.get_notifications_async(NotificationKinds.TOAST)

        for notification in notifications:
            notification_id = notification.id
            if notification_id in seen_notification_ids:
                continue

            seen_notification_ids.add(notification_id)

            try:
                app_name = notification.app_info.display_info.display_name
            except Exception:
                app_name = ""

            if "whatsapp" not in app_name.lower():
                continue

            lines = []
            try:
                visual = notification.notification.visual
                binding = visual.bindings[0] if len(visual.bindings) > 0 else None
                text_elements = binding.get_text_elements() if binding else []
                lines = [te.text for te in text_elements if getattr(te, "text", "")]
            except Exception:
                lines = []

            yield {
                "id": notification_id,
                "app_name": app_name,
                "lines": lines,
                "text": " | ".join(lines) if lines else "(no text)",
            }

        if poll_interval > 0:
            await asyncio.sleep(poll_interval)


async def read_whatsapp_notifications():
    """Read WhatsApp messages from Windows notifications"""
    try:
        print("Listening for WhatsApp notifications. Press Ctrl+C to stop.")
        async for notification in iter_whatsapp_notifications(poll_interval=0.0):
            print(f"App: {notification['app_name']}")
            print(f"Message: {notification['text']}")
            print("-" * 50)
    
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(read_whatsapp_notifications())
    except KeyboardInterrupt:
        print("Stopped by user.")