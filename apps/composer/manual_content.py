"""Helpers for previews and hand-off to manually managed channels."""

import json
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.utils.crypto import salted_hmac


class _ArticleSanitizer(HTMLParser):
    """Keep a small, predictable rich-text vocabulary and no active content."""

    allowed = {"div", "p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "blockquote", "h2", "h3", "a"}
    blocked = {"script", "style", "iframe", "object", "svg", "math"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked_depth = 0
        self.open_tags = []

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag not in self.allowed:
            return
        if tag == "a":
            href = next((v for k, v in attrs if k == "href"), "") or ""
            # Explicit absolute web URLs only; no JS, protocol-relative or control characters.
            try:
                parsed = urlsplit(href)
                safe_link = parsed.scheme.lower() in {"https", "http"} and bool(parsed.netloc)
            except ValueError:
                safe_link = False
            if safe_link and not any(ord(c) < 32 for c in href):
                self.parts.append(f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer">')
            else:
                self.parts.append("<a>")
        else:
            self.parts.append(f"<{tag}>")
        if tag != "br":
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self.blocked_depth:
            self.parts.append("<br>")

    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if self.blocked_depth or tag not in self.open_tags:
            return
        while self.open_tags:
            opened = self.open_tags.pop()
            self.parts.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data):
        if not self.blocked_depth:
            self.parts.append(escape(data))

    def result(self):
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


def sanitize_article_html(value):
    """Sanitize untrusted editor HTML on both save and preview."""
    parser = _ArticleSanitizer()
    parser.feed((value or "")[:200_000])
    parser.close()
    return parser.result()


def manual_review_fingerprint(platform_post):
    """Bind an approval to the version the reviewer actually saw."""
    extra = platform_post.platform_extra or {}
    content = {
        "platform_post": str(platform_post.pk),
        "title": platform_post.effective_title,
        "caption": platform_post.effective_caption,
        "author": extra.get("author", ""),
        "digest": extra.get("digest", ""),
        "tags": extra.get("manual_tags", ""),
        "article_html": sanitize_article_html(extra.get("article_html", "")),
        "media_order": platform_post.platform_specific_media or [],
        "attachments": [str(attachment.media_asset_id) for attachment in platform_post.post.media_attachments.all()],
    }
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return salted_hmac("socialos.manual-review.v1", encoded, algorithm="sha256").hexdigest()


def manual_preview_data(platform_post, post=None):
    """Build one safe preview, using only attachments belonging to its post."""
    extra = platform_post.platform_extra or {}
    order = platform_post.platform_specific_media or []
    ranks = {str(asset_id): index for index, asset_id in enumerate(order)} if isinstance(order, list) else {}
    assets = []
    post = post or platform_post.post
    for attachment in post.media_attachments.all():
        asset = attachment.media_asset
        if asset.file:
            assets.append(
                {"id": str(asset.id), "url": asset.file.url, "filename": asset.filename, "is_video": asset.is_video}
            )
    if extra.get("manual_media_frozen"):
        assets = [asset for asset in assets if asset["id"] in ranks]
    return {
        "platform_post": platform_post,
        "account": platform_post.social_account,
        "title": platform_post.effective_title,
        "caption": platform_post.effective_caption,
        "author": extra.get("author", ""),
        "digest": extra.get("digest", ""),
        "tags": extra.get("manual_tags", ""),
        "article_html": sanitize_article_html(extra.get("article_html", "")),
        "media_items": sorted(assets, key=lambda asset: ranks.get(asset["id"], len(ranks))),
    }
