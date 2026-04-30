"""Webhook client for sending notifications."""
import aiohttp
from typing import Dict, Optional

from notecast.core.models import User


class WebhookClient:
    """Client for sending webhook notifications."""

    def __init__(self, webhook_url: str = "", webhook_headers: Optional[Dict] = None):
        self._webhook_url = webhook_url
        self._webhook_headers = webhook_headers or {}

    async def post(
        self,
        user: User,
        title: str,
        message: str,
        link: Optional[str] = None,
    ) -> None:
        """Send a webhook notification.
        
        Args:
            user: User object
            title: Notification title
            message: Notification message
            link: Optional link to include
            
        Raises:
            aiohttp.ClientError: If webhook request fails
        """
        if not self._webhook_url:
            return

        payload = {
            "user": user.name,
            "title": title,
            "message": message,
            "email": user.email,
        }

        if link:
            payload["link"] = link

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._webhook_url,
                json=payload,
                headers=self._webhook_headers,
            ) as response:
                response.raise_for_status()

    async def notify_job_started(self, user: User, job_id: str, feed_name: str) -> None:
        """Notify that a job has started.
        
        Args:
            user: User object
            job_id: Job identifier
            feed_name: Feed name
        """
        await self.post(
            user,
            title="Job Started",
            message=f"Job {job_id} for feed '{feed_name}' has started.",
        )

    async def notify_job_completed(self, user: User, job_id: str, feed_name: str) -> None:
        """Notify that a job has completed.
        
        Args:
            user: User object
            job_id: Job identifier
            feed_name: Feed name
        """
        await self.post(
            user,
            title="Job Completed",
            message=f"Job {job_id} for feed '{feed_name}' has completed.",
        )

    async def notify_job_failed(self, user: User, job_id: str, feed_name: str, error: str) -> None:
        """Notify that a job has failed.
        
        Args:
            user: User object
            job_id: Job identifier
            feed_name: Feed name
            error: Error message
        """
        await self.post(
            user,
            title="Job Failed",
            message=f"Job {job_id} for feed '{feed_name}' failed: {error}",
        )
