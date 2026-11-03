"""Local planning identities never authorize or contact social platforms."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.members.models import OrgMembership, WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.social_accounts.tasks import check_social_account_health, schedule_all_health_checks
from apps.workspaces.models import Workspace


@pytest.fixture
def manager_workspace(db, user, organization):
    workspace = Workspace.objects.create(name="Brand", organization=organization)
    OrgMembership.objects.create(user=user, organization=organization, org_role="owner")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="manager")
    return workspace


@pytest.fixture
def manual_url(manager_workspace):
    return reverse("social_accounts:add_manual", kwargs={"workspace_id": manager_workspace.id})


@pytest.mark.django_db
@pytest.mark.parametrize(("platform", "label"), [("xiaohongshu", "小红书"), ("wechat_official", "微信公众号")])
def test_create_local_account_without_credentials_or_backfill(
    client, user, manager_workspace, manual_url, platform, label
):
    client.force_login(user)
    with patch("apps.analytics.tasks.backfill_account_analytics") as backfill:
        response = client.post(manual_url, {"platform": platform, "name": "品牌号", "handle": "brand"})
    assert response.status_code == 302
    account = SocialAccount.objects.get(workspace=manager_workspace, platform=platform)
    assert account.is_manual
    assert account.get_platform_display() == label
    assert account.account_platform_id.startswith("manual-")
    assert account.oauth_access_token == ""
    assert account.oauth_refresh_token == ""
    backfill.assert_not_called()
    listing = client.get(reverse("social_accounts:list", kwargs={"workspace_id": manager_workspace.id}))
    assert "人工管理 · 未连接平台" in listing.content.decode()


@pytest.mark.django_db
def test_invalid_manual_account_is_not_created(client, user, manager_workspace, manual_url):
    client.force_login(user)
    for data in (
        {"platform": "facebook", "name": "品牌号"},
        {"platform": "xiaohongshu", "name": "   "},
        {"platform": "wechat_official", "name": "x" * 256},
    ):
        assert client.post(manual_url, data).status_code == 400
    assert not SocialAccount.objects.filter(workspace=manager_workspace).exists()


@pytest.mark.django_db
def test_only_managers_can_add_to_their_workspace(client, user, organization, manager_workspace, manual_url):
    client.force_login(user)
    foreign_workspace = Workspace.objects.create(name="Other", organization=organization)
    foreign_url = reverse("social_accounts:add_manual", kwargs={"workspace_id": foreign_workspace.id})
    assert client.post(foreign_url, {"platform": "xiaohongshu", "name": "x"}).status_code == 403
    membership = WorkspaceMembership.objects.get(user=user, workspace=manager_workspace)
    membership.workspace_role = "viewer"
    membership.save(update_fields=["workspace_role"])
    assert client.post(manual_url, {"platform": "xiaohongshu", "name": "x"}).status_code == 403
    assert not SocialAccount.objects.filter(platform="xiaohongshu").exists()


@pytest.mark.django_db
def test_health_checks_never_resolve_manual_provider(manager_workspace):
    account = SocialAccount.objects.create(
        workspace=manager_workspace,
        platform="xiaohongshu",
        account_platform_id="manual-test",
        account_name="品牌号",
    )
    with patch("providers.get_provider") as provider:
        check_social_account_health.now(str(account.id))
    provider.assert_not_called()
    with patch("apps.social_accounts.tasks.check_social_account_health") as enqueue:
        schedule_all_health_checks.now()
    assert str(account.id) not in [args[0][0] for args in enqueue.call_args_list]
