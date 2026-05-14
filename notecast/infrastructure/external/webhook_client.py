"""Webhook client for sending notifications."""
import aiohttp
import json
import logging
from typing import Dict, Optional

from notecast.core.models import User

logger = logging.getLogger(__name__)


class WebhookClient:
    """Client for sending webhook notifications."""

    def __init__(self, webhook_url: str = "", webhook_headers: Optional[Dict] = None):
        self._webhook_url = webhook_url
        self._webhook_headers = webhook_headers or {}
        logger.debug("WebhookClient initialized with URL: %s", self._webhook_url)

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
            logger.warning("Webhook URL not configured, skipping notification for user %s", user.name)
            return

        # Format payload for ntfy compatibility
        if "ntfy" in self._webhook_url:
            # ntfy format: title as topic prefix, message as body
            payload = f"{title}\n\n{message}"
            if link:
                payload += f"\n\n{link}"
            headers = self._webhook_headers.copy()
            headers["Title"] = title  # ntfy uses Title header for notifications
        else:
            # Standard JSON format for other webhook providers
            payload = {
                "user": user.name,
                "title": title,
                "message": message,
                "email": user.email,
            }
            if link:
                payload["link"] = link
            headers = self._webhook_headers

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._webhook_url,
                    data=payload if isinstance(payload, str) else json.dumps(payload),
                    headers=headers,
                ) as response:
                    response_text = await response.text()
                    logger.info(
                        "Webhook response - URL: %s, Status: %d, Response: %s",
                        self._webhook_url,
                        response.status,
                        response_text[:200]  # Log first 200 chars
                    )
                    response.raise_for_status()
        except Exception as exc:
            logger.error("Webhook delivery failed - URL: %s, Error: %s", 
                        self._webhook_url, str(exc))
            raise

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

    async def notify_job_completed(
        self, user: User, job_id: str, feed_name: str, episode_title: str = ""
    ) -> None:
        """Notify that a job has completed.
        
        Args:
            user: User object
            job_id: Job identifier
            feed_name: Feed name
            episode_title: Episode title (optional)
        """
        title = f"Job Completed: {episode_title}" if episode_title else "Job Completed"
        message = f"Job {job_id} for feed '{feed_name}' has completed."
        if episode_title:
            message += f"\nEpisode: {episode_title}"
        await self.post(user, title=title, message=message)

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
