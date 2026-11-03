"""Per-platform client review and separation between approval stages."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.client_portal.views import _manual_fingerprints_match, _manual_previews, _portal_decide_platform
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class ManualPreviewUnitTests(SimpleTestCase):
    def test_only_client_visible_versions_use_attached_assets_and_sanitized_html(self):
        def asset(asset_id):
            return SimpleNamespace(
                id=asset_id, file=SimpleNamespace(url=f"/media/{asset_id}.png"), filename=asset_id, is_video=False
            )

        first, second = asset("first"), asset("second")
        account = SimpleNamespace(platform="wechat_official", account_name="公众号")
        shown = SimpleNamespace(
            social_account=account,
            status="pending_client",
            platform_extra={"article_html": '<p onclick="evil()">正文<script>alert(1)</script></p>'},
            platform_specific_media=["second", "other-workspace", "first"],
            effective_title="标题",
            effective_caption="正文",
        )
        hidden = SimpleNamespace(social_account=account, status="pending_review")
        post = SimpleNamespace(
            media_attachments=SimpleNamespace(
                all=Mock(return_value=[SimpleNamespace(media_asset=first), SimpleNamespace(media_asset=second)])
            ),
            platform_posts=SimpleNamespace(all=Mock(return_value=[shown, hidden])),
        )

        previews = _manual_previews(post)
        self.assertEqual(len(previews), 1)
        self.assertEqual([item["id"] for item in previews[0]["media_items"]], ["second", "first"])
        self.assertEqual(previews[0]["article_html"], "<p>正文</p>")

    def test_client_card_renders_a_sanitized_version_and_scoped_actions(self):
        account = SimpleNamespace(
            platform="wechat_official", account_name="公众号", get_platform_display=lambda: "微信公众号"
        )
        version = SimpleNamespace(
            id=uuid4(),
            social_account=account,
            status="pending_client",
            scheduled_at=None,
            platform_extra={
                "article_html": '<p onclick="evil()">完整<strong>正文</strong><script>alert(1)</script></p>'
            },
            platform_specific_media=[],
            manual_review_fingerprint="a" * 64,
            effective_title="标题 <draft>",
            effective_caption="备用正文",
        )
        post = SimpleNamespace(
            id=uuid4(),
            caption="共享文案",
            author=SimpleNamespace(display_name="编辑"),
            scheduled_at=None,
            proposed_publish_at=None,
            visible_comments=[],
            client_pending=True,
            client_approved=False,
            client_on_hold=False,
            media_attachments=SimpleNamespace(all=lambda: []),
            platform_posts=SimpleNamespace(all=lambda: [version]),
        )
        post.manual_previews = _manual_previews(post)
        html = render_to_string(
            "client_portal/_client_card.html",
            {"post": post, "workspace": SimpleNamespace(name="工作区", effective_timezone="Asia/Shanghai")},
        )
        self.assertIn("标题 &lt;draft&gt;", html)
        self.assertIn("<strong>正文</strong>", html)
        self.assertNotIn('onclick="evil()"', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn(
            reverse("client_portal:approve_platform", kwargs={"post_id": post.id, "platform_post_id": version.id}), html
        )
        self.assertIn("通过此选题全部待审版本", html)
        self.assertIn('hx-vals=\'{"fingerprint":"' + "a" * 64 + "\"}'", html)
        self.assertIn(f'name="fingerprint_{version.id}"', html)

    def test_bulk_fingerprint_validation_requires_every_manual_version(self):
        first = SimpleNamespace(
            id=uuid4(), social_account=SimpleNamespace(platform="xiaohongshu"), manual_review_fingerprint="first"
        )
        second = SimpleNamespace(
            id=uuid4(), social_account=SimpleNamespace(platform="wechat_official"), manual_review_fingerprint="second"
        )
        other = SimpleNamespace(id=uuid4(), social_account=SimpleNamespace(platform="facebook"))
        request = SimpleNamespace(POST={f"fingerprint_{first.id}": "first", f"fingerprint_{second.id}": "second"})
        self.assertTrue(_manual_fingerprints_match(request, [first, second, other]))
        request.POST[f"fingerprint_{second.id}"] = "stale"
        self.assertFalse(_manual_fingerprints_match(request, [first, second]))
        del request.POST[f"fingerprint_{second.id}"]
        self.assertFalse(_manual_fingerprints_match(request, [first, second]))

    def test_single_decision_rejects_stale_fingerprint_before_calling_approval(self):
        version = SimpleNamespace(manual_review_fingerprint="current")
        request = SimpleNamespace(portal_workspace=object(), user=object(), POST={"fingerprint": "old"}, htmx=True)
        with (
            patch("apps.client_portal.views.transaction.atomic", return_value=nullcontext()),
            patch("apps.client_portal.views.get_object_or_404", return_value=version),
            patch("apps.client_portal.views.approval_services.approve_post") as approve,
        ):
            response = _portal_decide_platform(request, uuid4(), uuid4(), "approved")
            self.assertEqual(response.status_code, 409)
            approve.assert_not_called()
            request.POST["fingerprint"] = "current"
            response = _portal_decide_platform(request, uuid4(), uuid4(), "approved")
            self.assertEqual(response.status_code, 204)
            approve.assert_called_once()


class ManualClientApprovalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="测试机构")
        self.workspace = Workspace.objects.create(
            organization=self.org, name="测试工作区", approval_workflow_mode="required_internal_and_client"
        )
        self.user = User.objects.create_user(
            email="portal-manual@example.com", password="testpass", tos_accepted_at=timezone.now()
        )
        WorkspaceMembership.objects.create(user=self.user, workspace=self.workspace, workspace_role="client")
        self.xhs = SocialAccount.objects.create(
            workspace=self.workspace, platform="xiaohongshu", account_platform_id="xhs-manual", account_name="红书账号"
        )
        self.wechat = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="wechat_official",
            account_platform_id="wechat-manual",
            account_name="公众号账号",
        )
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, title="选题", caption="共享文案")
        self.xhs_post = PlatformPost.objects.create(
            post=self.post,
            social_account=self.xhs,
            status="pending_client",
            platform_specific_title="红书标题",
            platform_specific_caption="红书正文",
            platform_extra={"manual_tags": "#主题"},
        )
        self.wechat_post = PlatformPost.objects.create(
            post=self.post,
            social_account=self.wechat,
            status="pending_client",
            platform_specific_title="公众号标题",
            platform_extra={
                "article_html": "<p>公众号正文<script>alert(1)</script></p>",
                "author": "编辑",
                "digest": "摘要",
            },
        )
        self._login_portal(self.client)

    def _login_portal(self, client):
        client.force_login(self.user)
        session = client.session
        session["is_portal_session"] = True
        session["portal_workspace_id"] = str(self.workspace.id)
        session.save()

    def _url(self, action, pp=None):
        return reverse(
            f"client_portal:{action}", kwargs={"post_id": self.post.id, "platform_post_id": (pp or self.xhs_post).id}
        )

    def test_approve_one_version_preserves_other_and_records_actor(self):
        response = self.client.post(
            self._url("approve_platform"),
            {"fingerprint": self.xhs_post.manual_review_fingerprint},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 204)
        self.xhs_post.refresh_from_db()
        self.wechat_post.refresh_from_db()
        self.assertEqual(self.xhs_post.status, "approved")
        self.assertEqual(self.wechat_post.status, "pending_client")
        self.assertTrue(
            ApprovalAction.objects.filter(platform_post=self.xhs_post, user=self.user, action="approved").exists()
        )
        self.assertIn("portalAction", response.headers["HX-Trigger"])

    def test_request_changes_and_reject_are_independent(self):
        response = self.client.post(
            self._url("request_platform_changes"),
            {"comment": "修改标题", "fingerprint": self.xhs_post.manual_review_fingerprint},
        )
        self.assertEqual(response.status_code, 302)
        self.xhs_post.refresh_from_db()
        self.wechat_post.refresh_from_db()
        self.assertEqual(self.xhs_post.status, "changes_requested")
        self.assertEqual(self.wechat_post.status, "pending_client")
        response = self.client.post(
            self._url("reject_platform", self.wechat_post),
            {"comment": "请重写", "fingerprint": self.wechat_post.manual_review_fingerprint},
        )
        self.assertEqual(response.status_code, 302)
        self.wechat_post.refresh_from_db()
        self.assertEqual(self.wechat_post.status, "rejected")

    def test_target_must_be_waiting_for_client_and_post_ids_must_match(self):
        self.xhs_post.status = "pending_review"
        self.xhs_post.save(update_fields=["status"])
        self.assertEqual(self.client.post(self._url("approve_platform")).status_code, 404)
        other_post = Post.objects.create(workspace=self.workspace, author=self.user)
        mismatch = reverse(
            "client_portal:approve_platform", kwargs={"post_id": other_post.id, "platform_post_id": self.wechat_post.id}
        )
        self.assertEqual(self.client.post(mismatch).status_code, 404)

    def test_workspace_isolation_session_and_csrf_are_enforced(self):
        other = Workspace.objects.create(organization=self.org, name="其他工作区")
        self.wechat_post.post.workspace = other
        self.wechat_post.post.save(update_fields=["workspace"])
        self.assertEqual(self.client.post(self._url("approve_platform", self.wechat_post)).status_code, 404)
        anonymous = Client()
        self.assertEqual(anonymous.post(self._url("approve_platform")).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        self._login_portal(csrf_client)
        self.assertEqual(csrf_client.post(self._url("approve_platform")).status_code, 403)

    def test_bulk_approve_does_not_approve_an_internal_stage(self):
        self.wechat_post.status = "pending_review"
        self.wechat_post.save(update_fields=["status"])
        response = self.client.post(
            reverse("client_portal:approve", kwargs={"post_id": self.post.id}),
            {f"fingerprint_{self.xhs_post.id}": self.xhs_post.manual_review_fingerprint},
        )
        self.assertEqual(response.status_code, 302)
        self.xhs_post.refresh_from_db()
        self.wechat_post.refresh_from_db()
        self.assertEqual(self.xhs_post.status, "approved")
        self.assertEqual(self.wechat_post.status, "pending_review")

    def test_single_decisions_reject_missing_or_stale_fingerprint(self):
        old = self.xhs_post.manual_review_fingerprint
        self.xhs_post.platform_specific_caption = "已经更新的文案"
        self.xhs_post.save(update_fields=["platform_specific_caption"])
        for action in ("approve_platform", "request_platform_changes", "reject_platform"):
            with self.subTest(action=action):
                for fingerprint in ("", old):
                    response = self.client.post(self._url(action), {"fingerprint": fingerprint, "comment": "请修改"})
                    self.assertEqual(response.status_code, 409)
        self.xhs_post.refresh_from_db()
        self.assertEqual(self.xhs_post.status, "pending_client")
        self.assertFalse(ApprovalAction.objects.filter(platform_post=self.xhs_post).exists())

    def test_bulk_decisions_reject_entire_batch_when_any_fingerprint_is_stale(self):
        payload = {
            f"fingerprint_{self.xhs_post.id}": self.xhs_post.manual_review_fingerprint,
            f"fingerprint_{self.wechat_post.id}": "stale",
            "comment": "请修改",
        }
        for action in ("approve", "request_changes", "reject"):
            with self.subTest(action=action):
                response = self.client.post(
                    reverse(f"client_portal:{action}", kwargs={"post_id": self.post.id}), payload
                )
                self.assertEqual(response.status_code, 409)
                self.xhs_post.refresh_from_db()
                self.wechat_post.refresh_from_db()
                self.assertEqual(self.xhs_post.status, "pending_client")
                self.assertEqual(self.wechat_post.status, "pending_client")
        self.assertFalse(ApprovalAction.objects.filter(post=self.post).exists())

    def test_portal_shows_exact_versions_without_unsubmitted_content(self):
        hidden = SocialAccount.objects.create(
            workspace=self.workspace, platform="xiaohongshu", account_platform_id="xhs-draft", account_name="内部草稿"
        )
        PlatformPost.objects.create(
            post=self.post,
            social_account=hidden,
            status="pending_review",
            platform_specific_caption="不应给客户看的内容",
        )
        asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            uploaded_by=self.user,
            file="test/example.png",
            filename="example.png",
            media_type=MediaAsset.MediaType.IMAGE,
            mime_type="image/png",
        )
        PostMedia.objects.create(post=self.post, media_asset=asset)
        response = self.client.get(reverse("client_portal:approval_queue"))
        self.assertContains(response, "红书正文")
        self.assertContains(response, "公众号正文")
        self.assertContains(response, self._url("approve_platform"))
        self.assertContains(response, "example.png")
        self.assertNotContains(response, "不应给客户看的内容")
        self.assertNotContains(response, "<script>alert(1)</script>")
