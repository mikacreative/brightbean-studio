"""The sidebar leads to the shared account selection page."""

import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

VIDEO_GLYPH = 'd="M15.75 10.5l4.72-4.72'


class SidebarConnectChannelsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Org")
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        self.ws = Workspace.objects.create(organization=self.org, name="WS", timezone="Europe/Berlin")
        WorkspaceMembership.objects.create(
            user=self.user, workspace=self.ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        self.client.force_login(self.user)

    def test_sidebar_uses_one_entry_for_all_platforms(self):
        resp = self.client.get(reverse("calendar:calendar", kwargs={"workspace_id": self.ws.id}))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        block = re.search(r'<div class="sidebar-connect-channels".*?</a>\s*</div>', html, re.S).group()
        self.assertEqual(block.count("<a "), 1)
        self.assertIn(reverse("social_accounts:connect", kwargs={"workspace_id": self.ws.id}), block)
        self.assertIn("添加账号", block)
        self.assertNotIn(reverse("social_accounts:add_manual", kwargs={"workspace_id": self.ws.id}), block)

    def test_youtube_still_uses_generic_video_glyph_after_disconnect(self):
        SocialAccount.objects.create(
            workspace=self.ws,
            platform="youtube",
            account_platform_id="yt-1",
            account_name="Channel",
            connection_status=SocialAccount.ConnectionStatus.DISCONNECTED,
        )
        response = self.client.get(reverse("social_accounts:connect", kwargs={"workspace_id": self.ws.id}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(VIDEO_GLYPH, html)
        self.assertNotIn("youtube-label", html)
        self.assertNotIn("icon-red.png", html)
