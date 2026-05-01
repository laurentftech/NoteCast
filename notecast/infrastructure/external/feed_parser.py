"""Feed parser wrapper for RSS/Atom feeds."""
from typing import Any, Tuple

import feedparser

from notecast.core.models import Episode, Feed


def fetch_episodes(url: str) -> Tuple[str, list[Episode]]:
    """Fetch and parse an RSS/Atom feed.
    
    Args:
        url: Feed URL
        
    Returns:
        Tuple of (feed title, list of Episode objects)
        
    Raises:
        ValueError: If feed cannot be parsed
    """
    parsed: Any = feedparser.parse(url)

    if parsed.bozo:
        raise ValueError(f"Failed to parse feed: {parsed.bozo_exception}")

    feed_title = parsed.feed.get("title", "Unknown Feed")

    episodes = []
    for entry in parsed.entries:
        enclosures = entry.get("enclosures", [])
        audio_url = next(
            (e["href"] for e in enclosures if e.get("type", "").startswith("audio/")),
            None,
        )
        article_url = entry.get("link", "")
        # episode_url = unique dedup key (MP3 preferred, fallback to article)
        # source_url  = what NotebookLM scrapes (article page)
        episode_url = audio_url or article_url
        if not episode_url:
            continue
        episode = Episode(
            url=episode_url,
            source_url=article_url,
            title=entry.get("title", "Untitled"),
            feed_name="",
            feed_title=feed_title,
            style="deep-dive",
        )
        episodes.append(episode)

    return feed_title, episodes


def parse_feed_config(config_data: dict) -> list[Feed]:
    """Parse feed configuration from dictionary.
    
    Args:
        config_data: Dictionary with 'feeds' key containing list of feed configs
        
    Returns:
        List of Feed objects
    """
    feeds_data = config_data.get("feeds", [])
    return [Feed(**f) for f in feeds_data]
