"""Agent-facing behavior for the manually managed Chinese channels."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys.services import issue_api_key
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def scope(db):
    user = User.objects.create_user(
        email="manual-agent@example.invalid", password="test", tos_accepted_at=timezone.now()
    )
    org = Organization.objects.create(name="Agent test")
    workspace = Workspace.objects.create(name="Brand", organization=org, approval_workflow_mode="required_internal")
    OrgMembership.objects.create(user=user, organization=org, org_role="owner")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    xhs = SocialAccount.objects.create(
        workspace=workspace, platform="xiaohongshu", account_platform_id="xhs-test", account_name="XHS"
    )
    wechat = SocialAccount.objects.create(
        workspace=workspace, platform="wechat_official", account_platform_id="wx-test", account_name="WeChat"
    )
    issued = issue_api_key(
        workspace=workspace,
        social_accounts=[xhs, wechat],
        issued_by=user,
        name="agent test",
        permissions=["create_posts", "upload_media"],
    )
    return {
        "user": user,
        "org": org,
        "workspace": workspace,
        "xhs": xhs,
        "wechat": wechat,
        "token": issued.plaintext_token,
        "client": SecureClient(HTTP_AUTHORIZATION=f"Bearer {issued.plaintext_token}"),
    }


def call_tool(scope, name, arguments):
    response = scope["client"].post(
        "/api/v1/mcp",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        ),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def tool_result(response):
    assert "error" not in response, response
    return json.loads(response["result"]["content"][0]["text"])


@pytest.mark.django_db
def test_accounts_explicitly_identify_manual_management(scope):
    result = tool_result(call_tool(scope, "list_accounts", {}))
    assert {account["management_mode"] for account in result["accounts"]} == {"manual"}


@pytest.mark.django_db
def test_ai_media_search_matches_all_tags_exactly(scope):
    for filename, tags in (("match.png", ["hero", "新品"]), ("other.png", ["superhero", "新品"])):
        MediaAsset.objects.create(
            organization=scope["org"],
            workspace=scope["workspace"],
            uploaded_by=scope["user"],
            file=filename,
            filename=filename,
            media_type="image",
            mime_type="image/png",
            tags=tags,
            processing_status="completed",
        )
    result = tool_result(call_tool(scope, "search_media", {"tags": ["hero", "新品"]}))
    assert [item["filename"] for item in result["items"]] == ["match.png"]


@pytest.mark.django_db
def test_ai_can_attach_organization_shared_media_found_in_search(scope):
    asset = MediaAsset.objects.create(
        organization=scope["org"],
        workspace=None,
        uploaded_by=scope["user"],
        file="shared.png",
        filename="shared.png",
        media_type="image",
        mime_type="image/png",
        processing_status="completed",
    )
    found = tool_result(call_tool(scope, "search_media", {"query": "shared.png"}))
    assert str(asset.id) in {item["id"] for item in found["items"]}
    result = tool_result(
        call_tool(
            scope,
            "create_manual_draft",
            {
                "media_asset_ids": [str(asset.id)],
                "variants": [{"social_account_id": str(scope["xhs"].id), "title": "共享素材"}],
            },
        )
    )
    assert PostMedia.objects.filter(post_id=result["id"], media_asset=asset).exists()


@pytest.mark.django_db(transaction=True)
def test_real_mcp_client_creates_a_draft_through_the_local_stdio_bridge(scope, live_server, settings, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    settings.DEBUG = True
    token_path = tmp_path / "synthetic-token"
    token_path.write_text(scope["token"], encoding="utf-8")
    token_path.chmod(0o600)
    bridge = Path(__file__).resolve().parents[3] / "scripts" / "socialos_mcp_bridge.py"
    env = {
        **os.environ,
        "SOCIALOS_MCP_TOKEN_FILE": str(token_path),
        "SOCIALOS_MCP_URL": live_server.url.replace("localhost", "127.0.0.1") + "/api/v1/mcp",
    }

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[str(bridge)], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "create_manual_draft" in {tool.name for tool in tools.tools}
            result = await session.call_tool(
                "create_manual_draft",
                {"variants": [{"social_account_id": str(scope["xhs"].id), "title": "MCP 实际调用", "caption": "正文"}]},
            )
            assert not result.isError
            return json.loads(result.content[0].text)

    result = asyncio.run(exercise())
    assert Post.objects.get(id=result["id"]).platform_posts.get().effective_title == "MCP 实际调用"


@pytest.mark.django_db
def test_ai_can_create_one_draft_with_two_independent_platform_versions(scope):
    result = tool_result(
        call_tool(
            scope,
            "create_manual_draft",
            {
                "variants": [
                    {
                        "social_account_id": str(scope["xhs"].id),
                        "title": "小红书标题",
                        "caption": "小红书正文",
                        "tags": "#咖啡",
                        "planned_at": "2026-12-01T09:30:00+08:00",
                    },
                    {
                        "social_account_id": str(scope["wechat"].id),
                        "title": "公众号标题",
                        "author": "编辑部",
                        "digest": "摘要",
                        "article_html": "<p>正文<script>alert(1)</script><strong>重点</strong></p>",
                    },
                ],
            },
        )
    )
    post = Post.objects.get(id=result["id"])
    xhs = post.platform_posts.get(social_account=scope["xhs"])
    wechat = post.platform_posts.get(social_account=scope["wechat"])
    assert post.status == "draft"
    assert post.title == "小红书标题"
    assert post.scheduled_at is None
    assert (xhs.effective_title, xhs.effective_caption, xhs.platform_extra["manual_tags"]) == (
        "小红书标题",
        "小红书正文",
        "#咖啡",
    )
    assert xhs.scheduled_at.hour == 1
    assert (wechat.effective_title, wechat.platform_extra["author"], wechat.platform_extra["digest"]) == (
        "公众号标题",
        "编辑部",
        "摘要",
    )
    assert wechat.effective_caption == ""
    assert wechat.platform_extra["article_html"] == "<p>正文<strong>重点</strong></p>"
    versions = {item["platform"]: item for item in result["platform_posts"]}
    assert versions["xiaohongshu"]["manual"]["tags"] == "#咖啡"
    assert versions["wechat_official"]["manual"]["article_html"] == "<p>正文<strong>重点</strong></p>"

    browser = Client()
    browser.force_login(scope["user"])
    editor_url = reverse("composer:compose_edit", kwargs={"workspace_id": scope["workspace"].id, "post_id": post.id})
    editor = browser.get(editor_url)
    assert editor.status_code == 200
    assert editor.context["manual_variants"][str(scope["xhs"].id)]["title"] == "小红书标题"
    assert editor.context["platform_extras"][str(scope["wechat"].id)]["digest"] == "摘要"

    save_url = reverse("composer:save_post_edit", kwargs={"workspace_id": scope["workspace"].id, "post_id": post.id})
    form = {
        "action": "submit_for_approval",
        "title": post.title,
        "caption": "",
        "tags": "",
        "selected_accounts": f"{scope['xhs'].id},{scope['wechat'].id}",
        f"manual_panel_{scope['xhs'].id}": "1",
        f"override_title_{scope['xhs'].id}": "小红书标题",
        f"override_caption_{scope['xhs'].id}": "小红书正文",
        f"manual_tags_{scope['xhs'].id}": "#咖啡",
        f"manual_panel_{scope['wechat'].id}": "1",
        f"override_title_{scope['wechat'].id}": "公众号标题",
        f"manual_author_{scope['wechat'].id}": "编辑部",
        f"manual_digest_{scope['wechat'].id}": "摘要",
        f"manual_article_{scope['wechat'].id}": "<p>正文<strong>重点</strong></p>",
    }
    with patch("apps.approvals.services._notify_reviewers"):
        submitted = browser.post(save_url, form, HTTP_HX_REQUEST="true")
    assert submitted.status_code == 204, submitted.content
    assert set(post.platform_posts.values_list("status", flat=True)) == {"pending_review"}


@pytest.mark.django_db
def test_ai_can_update_one_draft_version_without_changing_another(scope):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"])
    xhs = PlatformPost.objects.create(post=post, social_account=scope["xhs"], status="draft")
    wechat = PlatformPost.objects.create(
        post=post, social_account=scope["wechat"], status="approved", platform_specific_title="已审批"
    )
    tool_result(
        call_tool(
            scope,
            "update_manual_draft",
            {"post_id": str(post.id), "social_account_id": str(scope["xhs"].id), "title": "新标题", "tags": "#新话题"},
        )
    )
    xhs.refresh_from_db()
    wechat.refresh_from_db()
    assert xhs.effective_title == "新标题"
    assert xhs.platform_extra["manual_tags"] == "#新话题"
    assert wechat.status == "approved"
    assert wechat.effective_title == "已审批"


@pytest.mark.django_db
def test_ai_cannot_update_an_approved_platform_version(scope):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"])
    pp = PlatformPost.objects.create(post=post, social_account=scope["xhs"], status="approved")
    result = call_tool(
        scope,
        "update_manual_draft",
        {"post_id": str(post.id), "social_account_id": str(scope["xhs"].id), "caption": "不应写入"},
    )
    assert "error" in result
    assert "draft" in result["error"]["message"]
    pp.refresh_from_db()
    assert pp.status == "approved"
    assert pp.effective_caption == ""


@pytest.mark.django_db
def test_ai_create_rolls_back_if_a_platform_field_is_invalid(scope):
    result = call_tool(
        scope,
        "create_manual_draft",
        {
            "variants": [
                {"social_account_id": str(scope["xhs"].id), "title": "有效"},
                {"social_account_id": str(scope["wechat"].id), "tags": "公众号不支持"},
            ]
        },
    )
    assert "error" in result
    assert not Post.objects.filter(workspace=scope["workspace"]).exists()


@pytest.mark.django_db
def test_read_only_agent_cannot_see_manual_versions_awaiting_internal_review(scope):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"])
    PlatformPost.objects.create(
        post=post,
        social_account=scope["wechat"],
        status="pending_review",
        platform_specific_title="内部标题",
        platform_extra={"article_html": "<p>内部正文</p>"},
    )
    issued = issue_api_key(
        workspace=scope["workspace"],
        social_accounts=[scope["wechat"]],
        issued_by=scope["user"],
        name="read only",
        permissions=[],
    )
    client = SecureClient(HTTP_AUTHORIZATION=f"Bearer {issued.plaintext_token}")
    response = client.get(f"/api/v1/posts/{post.id}")
    assert response.status_code == 200
    version = response.json()["platform_posts"][0]
    assert version["effective_title"] == ""
    assert version["manual"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("platform", ["xhs", "wechat"])
def test_rest_changes_to_approved_manual_content_require_review_again(scope, platform):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"], caption="已审批正文")
    pp = PlatformPost.objects.create(post=post, social_account=scope[platform], status="approved")
    response = scope["client"].patch(
        f"/api/v1/posts/{post.id}", data=json.dumps({"caption": "修改后的正文"}), content_type="application/json"
    )
    assert response.status_code == 200, response.content
    pp.refresh_from_db()
    assert pp.status == "pending_review"


@pytest.mark.django_db
def test_rest_edits_only_reopen_versions_whose_effective_content_changed(scope):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"], caption="shared")
    PlatformPost.objects.create(post=post, social_account=scope["xhs"], status="draft")
    wechat = PlatformPost.objects.create(
        post=post, social_account=scope["wechat"], status="approved", platform_specific_caption="自己的正文"
    )
    response = scope["client"].patch(
        f"/api/v1/posts/{post.id}", data=json.dumps({"caption": "new shared"}), content_type="application/json"
    )
    assert response.status_code == 200, response.content
    wechat.refresh_from_db()
    assert wechat.status == "approved"


@pytest.mark.django_db
def test_rest_edit_reopens_a_client_pending_version_in_a_mixed_post(scope):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"], caption="shared")
    PlatformPost.objects.create(post=post, social_account=scope["xhs"], status="draft")
    wechat = PlatformPost.objects.create(post=post, social_account=scope["wechat"], status="pending_client")
    response = scope["client"].patch(
        f"/api/v1/posts/{post.id}", data=json.dumps({"caption": "new shared"}), content_type="application/json"
    )
    assert response.status_code == 200, response.content
    wechat.refresh_from_db()
    assert wechat.status == "pending_review"


@pytest.mark.django_db
def test_rest_cannot_remove_media_frozen_by_a_published_manual_version(scope):
    post = Post.objects.create(workspace=scope["workspace"], author=scope["user"])
    asset = MediaAsset.objects.create(
        organization=scope["org"],
        workspace=scope["workspace"],
        uploaded_by=scope["user"],
        file="sample.png",
        filename="sample.png",
        media_type="image",
        mime_type="image/png",
    )
    PostMedia.objects.create(post=post, media_asset=asset, position=0)
    PlatformPost.objects.create(
        post=post,
        social_account=scope["xhs"],
        status="published",
        platform_specific_media=[str(asset.id)],
        platform_extra={"manual_media_frozen": True},
    )
    PlatformPost.objects.create(post=post, social_account=scope["wechat"], status="draft")
    response = scope["client"].patch(
        f"/api/v1/posts/{post.id}", data=json.dumps({"media_asset_ids": []}), content_type="application/json"
    )
    assert response.status_code in {409, 422}
    assert post.media_attachments.filter(media_asset=asset).exists()
