"""Local planning identities never authorize or contact social platforms."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.api_keys.services import issue_api_key
from apps.approvals.models import ApprovalAction
from apps.composer.manual_content import manual_review_fingerprint
from apps.composer.models import PlatformPost, Post
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


@pytest.fixture
def manual_account(manager_workspace):
    return SocialAccount.objects.create(
        workspace=manager_workspace,
        platform="xiaohongshu",
        account_platform_id="manual-test",
        account_name="原账号名称",
        account_handle="original",
    )


def edit_url(workspace, account):
    return f"/social-accounts/{workspace.id}/{account.id}/edit/"


@pytest.mark.django_db
def test_account_addition_uses_one_platform_selection_page(client, user, manager_workspace, manual_url):
    client.force_login(user)
    connect_url = reverse("social_accounts:connect", kwargs={"workspace_id": manager_workspace.id})
    listing = client.get(reverse("social_accounts:list", kwargs={"workspace_id": manager_workspace.id}))
    assert listing.status_code == 200
    assert f'href="{connect_url}"' in listing.content.decode()
    assert f'href="{manual_url}' not in listing.content.decode()

    selection = client.get(connect_url)
    assert selection.status_code == 200
    html = selection.content.decode()
    assert f'href="{manual_url}?platform=xiaohongshu"' in html
    assert f'href="{manual_url}?platform=wechat_official"' in html
    assert "小红书" in html and "微信公众号" in html
    assert 'name="platform" value="bluesky"' in html
    assert "不会自动发布" in html


@pytest.mark.django_db
def test_edit_preserves_account_content_approval_and_agent_access(client, user, manager_workspace, manual_account):
    post = Post.objects.create(workspace=manager_workspace, author=user, title="保留的标题", caption="保留的正文")
    version = PlatformPost.objects.create(
        post=post,
        social_account=manual_account,
        status=PlatformPost.Status.APPROVED,
        platform_extra={"manual_tags": "品牌", "published_url": "https://example.invalid/post"},
    )
    approval = ApprovalAction.objects.create(post=post, platform_post=version, user=user, action="approved")
    key = issue_api_key(
        workspace=manager_workspace, social_accounts=[manual_account], issued_by=user, name="测试 AI", permissions=[]
    ).api_key
    fingerprint = manual_review_fingerprint(version)
    original_platform_id = manual_account.account_platform_id
    original_created_at = manual_account.created_at

    client.force_login(user)
    url = edit_url(manager_workspace, manual_account)
    page = client.get(url)
    assert page.status_code == 200
    assert "原账号名称" in page.content.decode()
    assert "original" in page.content.decode()
    assert "保存修改" in page.content.decode()
    response = client.post(url, {"name": " 新账号名称 ", "handle": " updated "})
    assert response.status_code == 302
    assert response.url == reverse("social_accounts:list", kwargs={"workspace_id": manager_workspace.id})

    manual_account.refresh_from_db()
    version.refresh_from_db()
    assert (manual_account.account_name, manual_account.account_handle) == ("新账号名称", "updated")
    assert manual_account.platform == "xiaohongshu"
    assert manual_account.account_platform_id == original_platform_id
    assert manual_account.created_at == original_created_at
    assert version.social_account_id == manual_account.id and version.post_id == post.id
    assert version.status == PlatformPost.Status.APPROVED
    assert version.platform_extra["published_url"] == "https://example.invalid/post"
    assert manual_review_fingerprint(version) == fingerprint
    assert ApprovalAction.objects.get(id=approval.id).platform_post_id == version.id
    assert list(key.social_accounts.values_list("id", flat=True)) == [manual_account.id]


@pytest.mark.django_db
def test_edit_rejects_invalid_values_and_platform_changes(client, user, manager_workspace, manual_account):
    client.force_login(user)
    url = edit_url(manager_workspace, manual_account)
    for data in (
        {"name": "  ", "handle": "ok"},
        {"name": "x" * 256, "handle": "ok"},
        {"name": "合法名称", "handle": "x" * 256},
        {"name": "合法名称", "platform": "wechat_official"},
    ):
        assert client.post(url, data).status_code == 400
    manual_account.refresh_from_db()
    assert (manual_account.account_name, manual_account.account_handle, manual_account.platform) == (
        "原账号名称",
        "original",
        "xiaohongshu",
    )
    assert client.post(url, {"name": "合法名称", "handle": ""}).status_code == 302
    manual_account.refresh_from_db()
    assert manual_account.account_handle == ""


@pytest.mark.django_db
def test_edit_is_limited_to_manual_accounts_and_workspace_managers(
    client, user, organization, manager_workspace, manual_account
):
    foreign_workspace = Workspace.objects.create(name="Other", organization=organization)
    foreign_account = SocialAccount.objects.create(
        workspace=foreign_workspace,
        platform="wechat_official",
        account_platform_id="manual-foreign",
        account_name="其他",
    )
    oauth_account = SocialAccount.objects.create(
        workspace=manager_workspace,
        platform="facebook",
        account_platform_id="remote-test",
        account_name="授权账号",
        connection_status=SocialAccount.ConnectionStatus.DISCONNECTED,
    )
    client.force_login(user)
    assert client.post(edit_url(manager_workspace, foreign_account), {"name": "改名"}).status_code == 404
    assert client.post(edit_url(foreign_workspace, foreign_account), {"name": "改名"}).status_code == 403
    assert client.post(edit_url(manager_workspace, oauth_account), {"name": "改名"}).status_code == 404
    membership = WorkspaceMembership.objects.get(user=user, workspace=manager_workspace)
    membership.workspace_role = "viewer"
    membership.save(update_fields=["workspace_role"])
    assert client.get(edit_url(manager_workspace, manual_account)).status_code == 403
    assert client.post(edit_url(manager_workspace, manual_account), {"name": "改名"}).status_code == 403
    foreign_account.refresh_from_db()
    oauth_account.refresh_from_db()
    manual_account.refresh_from_db()
    assert (foreign_account.account_name, oauth_account.account_name, manual_account.account_name) == (
        "其他",
        "授权账号",
        "原账号名称",
    )


@pytest.mark.django_db
def test_only_manual_account_cards_offer_editing(client, user, manager_workspace, manual_account):
    oauth_account = SocialAccount.objects.create(
        workspace=manager_workspace,
        platform="facebook",
        account_platform_id="remote-test",
        account_name="授权账号",
        connection_status=SocialAccount.ConnectionStatus.DISCONNECTED,
    )
    client.force_login(user)
    listing = client.get(reverse("social_accounts:list", kwargs={"workspace_id": manager_workspace.id}))
    html = listing.content.decode()
    assert f'href="{edit_url(manager_workspace, manual_account)}"' in html
    assert f'href="{edit_url(manager_workspace, oauth_account)}"' not in html


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
