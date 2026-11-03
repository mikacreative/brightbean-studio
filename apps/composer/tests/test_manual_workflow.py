"""Integration checks for the manual publishing workflow (requires a test DB)."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.publisher.engine import PublishEngine
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class ManualWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="manual-workflow@example.com", password="testpass", tos_accepted_at=timezone.now()
        )
        self.org = Organization.objects.create(name="测试公司", default_timezone="Asia/Shanghai")
        self.workspace = Workspace.objects.create(organization=self.org, name="品牌")
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role="owner")
        WorkspaceMembership.objects.create(user=self.user, workspace=self.workspace, workspace_role="owner")
        self.client.force_login(self.user)
        self.xhs = SocialAccount.objects.create(
            workspace=self.workspace, platform="xiaohongshu", account_platform_id="manual-xhs", account_name="小红书"
        )
        self.wechat = SocialAccount.objects.create(
            workspace=self.workspace, platform="wechat_official", account_platform_id="manual-wx", account_name="公众号"
        )

    def payload(self, **kwargs):
        data = {
            "action": "save_draft",
            "title": "共享标题",
            "caption": "共享正文",
            "tags": "",
            "selected_accounts": f"{self.xhs.id},{self.wechat.id}",
            f"manual_panel_{self.xhs.id}": "1",
            f"override_title_{self.xhs.id}": "小红书标题",
            f"override_caption_{self.xhs.id}": "小红书正文",
            f"manual_tags_{self.xhs.id}": "#城市",
            f"manual_plan_{self.xhs.id}": "2026-12-01T09:30",
            f"manual_panel_{self.wechat.id}": "1",
            f"override_title_{self.wechat.id}": "公众号标题",
            f"manual_author_{self.wechat.id}": "编辑部",
            f"manual_article_{self.wechat.id}": "<p>文章 <strong>正文</strong></p>",
        }
        data.update(kwargs)
        return data

    def save(self, data, post=None):
        route = "composer:save_post_edit" if post else "composer:save_post"
        kwargs = {"workspace_id": self.workspace.id}
        if post:
            kwargs["post_id"] = post.id
        return self.client.post(reverse(route, kwargs=kwargs), data, HTTP_HX_REQUEST="true")

    def test_save_two_independent_versions_and_manual_planned_time(self):
        self.assertEqual(self.save(self.payload()).status_code, 204)
        post = Post.objects.get(workspace=self.workspace)
        xhs = post.platform_posts.get(social_account=self.xhs)
        wechat = post.platform_posts.get(social_account=self.wechat)
        self.assertEqual((xhs.effective_title, xhs.effective_caption), ("小红书标题", "小红书正文"))
        self.assertEqual(xhs.platform_extra["manual_tags"], "#城市")
        self.assertEqual(wechat.platform_extra["article_html"], "<p>文章 <strong>正文</strong></p>")
        self.assertEqual(xhs.scheduled_at.utcoffset().total_seconds(), 0)
        self.assertEqual(xhs.scheduled_at.hour, 1)
        self.assertEqual(xhs.status, "draft")
        self.assertIsNone(post.scheduled_at)

    def test_manual_accounts_are_rejected_by_automatic_publishing(self):
        response = self.save(self.payload(action="publish_now"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Post.objects.filter(workspace=self.workspace).exists())
        post = Post.objects.create(workspace=self.workspace, author=self.user)
        pp = PlatformPost.objects.create(
            post=post, social_account=self.xhs, status="scheduled", scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertNotIn(pp, PublishEngine()._get_due_platform_posts())

    def test_internal_feedback_only_changes_the_reviewed_version(self):
        post = Post.objects.create(workspace=self.workspace, author=self.user, title="共享标题")
        xhs = PlatformPost.objects.create(post=post, social_account=self.xhs, status="pending_review")
        wx = PlatformPost.objects.create(post=post, social_account=self.wechat, status="pending_review")
        url = reverse(
            "approvals:request_changes_platform",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id, "platform_post_id": xhs.id},
        )
        self.assertEqual(self.client.post(url, {"fingerprint": "old", "comment": "修改标题"}).status_code, 409)
        response = self.client.post(url, {"fingerprint": xhs.manual_review_fingerprint, "comment": "修改标题"})
        self.assertEqual(response.status_code, 204)
        xhs.refresh_from_db()
        wx.refresh_from_db()
        self.assertEqual(xhs.status, "changes_requested")
        self.assertEqual(wx.status, "pending_review")
        body = self.client.get(
            reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": post.id})
        ).content.decode()
        self.assertIn("前往审批", body)

    def test_editing_client_pending_version_returns_only_that_version_to_review(self):
        post = Post.objects.create(workspace=self.workspace, author=self.user, title="共享标题", caption="共享正文")
        xhs = PlatformPost.objects.create(post=post, social_account=self.xhs, status="pending_client")
        wx = PlatformPost.objects.create(post=post, social_account=self.wechat, status="pending_client")
        data = self.payload(selected_accounts=str(self.xhs.id), account_scope=str(self.xhs.id).upper())
        self.assertEqual(self.save(data, post).status_code, 204)
        xhs.refresh_from_db()
        wx.refresh_from_db()
        self.assertEqual(xhs.status, "pending_review")
        self.assertEqual(wx.status, "pending_client")

    def test_submitting_new_post_attaches_pending_media(self):
        asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            uploaded_by=self.user,
            file="example.png",
            filename="example.png",
            media_type="image",
            mime_type="image/png",
        )
        session = self.client.session
        session[f"pending_media_{self.workspace.id}"] = [str(asset.id)]
        session.save()
        data = self.payload(action="submit_for_approval", **{f"manual_media_{self.xhs.id}": str(asset.id)})
        with patch("apps.approvals.services._notify_reviewers"):
            self.assertEqual(self.save(data).status_code, 204)
        post = Post.objects.get(workspace=self.workspace)
        self.assertTrue(PostMedia.objects.filter(post=post, media_asset=asset).exists())
        self.assertEqual(post.platform_posts.get(social_account=self.xhs).platform_specific_media, [str(asset.id)])

    def test_manual_record_requires_current_version_and_freezes_content(self):
        post = Post.objects.create(workspace=self.workspace, author=self.user, title="共享标题", caption="共享正文")
        pp = PlatformPost.objects.create(post=post, social_account=self.xhs, status="approved")
        url = reverse(
            "composer:mark_manual_published",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id, "platform_post_id": pp.id},
        )
        self.assertEqual(
            self.client.post(url, {"url": "https://www.xiaohongshu.com/explore/123", "fingerprint": "old"}).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                url, {"url": "https://www.xiaohongshu.com/explore/123", "fingerprint": pp.manual_review_fingerprint}
            ).status_code,
            200,
        )
        pp.refresh_from_db()
        self.assertEqual(pp.status, "published")
        self.assertEqual(pp.platform_specific_title, "共享标题")
        self.assertTrue(pp.platform_extra["manual_media_frozen"])
