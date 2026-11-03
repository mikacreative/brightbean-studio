"""Smoke checks for the main Simplified Chinese user journeys."""

import pytest
from django.template import Context, Template
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.ui_messages import localize_ui_error
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


def test_builtin_choice_labels_are_translated_without_changing_custom_values():
    template = Template(
        "{% load common_extras %}{{ status|zh_ui_label }} / {{ weekday|zh_ui_label }} / {{ custom|zh_ui_label }}"
    )
    rendered = template.render(Context({"status": "Pending Review", "weekday": "Monday", "custom": "My custom label"}))
    assert rendered == "待内部审核 / 周一 / My custom label"


def test_status_filter_keeps_machine_values_while_translating_labels():
    template = Template('{% load common_extras %}{% ui_select model="filter" options=choices icon="status" %}')
    rendered = template.render(Context({"choices": [("pending_review", "Pending Review")]}))
    assert "pending_review" in rendered
    assert "待内部审核" in rendered


def test_analytics_formula_is_localized_at_the_template_boundary():
    template = Template("{% load common_extras %}{{ formula|zh_analytics_formula }}")
    rendered = template.render(Context({"formula": "(Likes + Comments) ÷ Views"}))
    assert rendered == "(点赞数 + 评论数) ÷ 观看次数"


def test_service_error_localization_preserves_unknown_details():
    assert localize_ui_error(ValueError("A comment is required when rejecting a post.")) == "请填写驳回原因。"
    assert localize_ui_error(ValueError("Provider-specific error 123")) == "Provider-specific error 123"


@pytest.mark.django_db
def test_new_users_receive_chinese_workspace_defaults():
    user = User.objects.create_user(email="new-locale-test@example.invalid", password="testpass")
    workspace = Workspace.objects.get(id=user.last_workspace_id)

    assert workspace.name == "我的工作区"
    assert workspace.organization.name == "我的组织"


@pytest.fixture
def localized_workspace(db):
    user = User.objects.create_user(
        email="locale-test@example.invalid", password="testpass", tos_accepted_at=timezone.now()
    )
    org = Organization.objects.create(name="测试组织")
    workspace = Workspace.objects.create(name="测试品牌", organization=org)
    OrgMembership.objects.create(user=user, organization=org, org_role="owner")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    return user, workspace


@pytest.mark.django_db
def test_login_page_uses_chinese():
    response = Client().get(reverse("account_login"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'lang="zh-CN"' in body
    assert "欢迎回来" in body
    assert "登录" in body
    assert "忘记密码" in body


@pytest.mark.django_db
def test_workspace_navigation_and_settings_use_chinese(localized_workspace):
    user, workspace = localized_workspace
    client = Client()
    client.force_login(user)
    response = client.get(reverse("workspaces:settings", kwargs={"workspace_id": workspace.id}))
    assert response.status_code == 200
    body = response.content.decode()
    assert "工作区设置" in body
    assert "基本信息" in body
    assert "团队成员" in body


@pytest.mark.django_db
def test_calendar_shows_chinese_add_account_and_timezone(localized_workspace):
    user, workspace = localized_workspace
    workspace.timezone = "Asia/Shanghai"
    workspace.save(update_fields=["timezone"])
    client = Client()
    client.force_login(user)

    response = client.get(reverse("calendar:calendar", kwargs={"workspace_id": workspace.id}))
    body = response.content.decode()
    assert response.status_code == 200
    assert "添加账号" in body
    assert reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id}) in body
    assert "北京时间（工作区）" in body
    assert "Asia/Shanghai" in body


@pytest.mark.django_db
def test_new_idea_board_has_chinese_defaults(localized_workspace):
    user, workspace = localized_workspace
    client = Client()
    client.force_login(user)

    response = client.get(reverse("composer:create_landing", kwargs={"workspace_id": workspace.id}))
    body = response.content.decode()
    assert response.status_code == 200
    for label in ("待分配", "待开始", "进行中", "已完成"):
        assert label in body
    assert "在这里规划你的内容" in body


@pytest.mark.django_db
def test_client_portal_navigation_uses_chinese(localized_workspace):
    user, workspace = localized_workspace
    client = Client()
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(workspace.id)
    session.save()
    response = client.get(reverse("client_portal:dashboard"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'lang="zh-CN"' in body
    assert "首页" in body
    assert "审批" in body
    assert "已发布" in body
