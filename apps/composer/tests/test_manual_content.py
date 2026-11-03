"""Database-free tests for manual-channel content and approval fingerprints."""

import uuid
from types import SimpleNamespace

from django.http import QueryDict
from django.test import SimpleTestCase

from apps.composer.manual_content import manual_review_fingerprint, sanitize_article_html
from apps.composer.services import publish_blocker
from apps.composer.views import _parse_selected_account_ids, _update_manual_fields


class AttachmentList:
    def __init__(self, ids):
        self.ids = ids

    def all(self):
        return [SimpleNamespace(media_asset_id=value) for value in self.ids]

    def values_list(self, *_args, **_kwargs):
        return self.ids


class ManualContentTests(SimpleTestCase):
    def test_article_preserves_formatting_without_active_content(self):
        result = sanitize_article_html(
            '<div><p onclick="evil()">中文 <strong>重点</strong>'
            '<a href="javascript:alert(1)">危险</a>'
            '<a href="https://[invalid">坏链接</a><script>alert(1)</script></p></div>'
        )
        self.assertIn("<strong>重点</strong>", result)
        self.assertIn("中文", result)
        self.assertNotIn("onclick", result)
        self.assertNotIn("javascript", result)
        self.assertNotIn("alert(1)", result)
        self.assertNotIn("https://[invalid", result)

    def test_only_attached_media_are_stored_in_the_requested_order(self):
        account_id, first, second, foreign = [uuid.uuid4() for _ in range(4)]
        data = QueryDict("", mutable=True)
        data.update(
            {
                f"manual_media_{account_id}": f"{second},{foreign},{first},{second}",
                f"manual_plan_{account_id}": "2026-12-01T09:30",
                f"manual_article_{account_id}": "<p>内容<script>alert(1)</script></p>",
                f"manual_author_{account_id}": "编辑",
            }
        )
        pp = SimpleNamespace(
            social_account_id=account_id,
            post=SimpleNamespace(media_attachments=AttachmentList([first, second])),
            platform_extra={},
        )
        _update_manual_fields(SimpleNamespace(POST=data), pp, SimpleNamespace(effective_timezone="Asia/Shanghai"))
        self.assertEqual(pp.platform_specific_media, [str(second), str(first)])
        self.assertEqual(pp.scheduled_at.utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(pp.platform_extra["article_html"], "<p>内容</p>")

    def test_fingerprint_changes_for_visible_content_and_media(self):
        media = AttachmentList([uuid.uuid4(), uuid.uuid4()])
        post = SimpleNamespace(media_attachments=media)
        pp = SimpleNamespace(
            pk=uuid.uuid4(),
            post=post,
            effective_title="标题",
            effective_caption="正文",
            platform_extra={"article_html": "<p>文章</p>"},
            platform_specific_media=[],
        )
        before = manual_review_fingerprint(pp)
        pp.platform_extra["article_html"] = "<p>新文章</p>"
        self.assertNotEqual(before, manual_review_fingerprint(pp))
        pp.platform_extra["article_html"] = "<p>文章</p>"
        pp.platform_specific_media = [str(media.ids[1]), str(media.ids[0])]
        self.assertNotEqual(before, manual_review_fingerprint(pp))

    def test_manual_platforms_are_never_publishable(self):
        for platform in ("xiaohongshu", "wechat_official"):
            self.assertIn("人工管理", publish_blocker(SimpleNamespace(platform=platform), media_types=set()))

    def test_account_uuids_are_normalized(self):
        account_id = uuid.uuid4()
        self.assertEqual(
            _parse_selected_account_ids(f"{str(account_id).upper()},{{{account_id}}},invalid"),
            [str(account_id), str(account_id)],
        )
