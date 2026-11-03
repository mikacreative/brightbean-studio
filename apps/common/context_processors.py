"""Context processors for sidebar and global template data."""

from django.db.models import Count, Q


def sidebar_context(request):
    """Inject sidebar data into every template context.

    Provides:
        sidebar_workspaces: list of workspace objects the user belongs to
        sidebar_channels: connected social accounts for the current workspace
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}

    from apps.members.models import WorkspaceMembership
    from apps.social_accounts.models import AnalyticsPlatformConfig, SocialAccount

    analytics_enabled_platforms = AnalyticsPlatformConfig.enabled_platforms()

    # User's workspaces (non-archived)
    workspace_memberships = (
        WorkspaceMembership.objects.filter(
            user=request.user,
            workspace__is_archived=False,
        )
        .select_related("workspace")
        .order_by("workspace__name")
    )
    sidebar_workspaces = [wm.workspace for wm in workspace_memberships]

    # Connected channels for the current workspace
    sidebar_channels = []
    sidebar_unhealthy_channels = []

    workspace = getattr(request, "workspace", None)

    if workspace:
        from apps.composer.models import PlatformPost

        sidebar_channels = list(
            SocialAccount.objects.for_workspace(workspace.id)
            .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
            .annotate(
                queued_post_count=Count(
                    "platform_posts",
                    filter=Q(platform_posts__status=PlatformPost.Status.SCHEDULED),
                )
            )
            .order_by("platform", "account_name")
        )

        sidebar_unhealthy_channels = list(
            SocialAccount.objects.for_workspace(workspace.id)
            .filter(
                connection_status__in=[
                    SocialAccount.ConnectionStatus.DISCONNECTED,
                    SocialAccount.ConnectionStatus.ERROR,
                    SocialAccount.ConnectionStatus.TOKEN_EXPIRING,
                ]
            )
            .order_by("platform", "account_name")
        )

    # Unread inbox count for sidebar badge
    sidebar_unread_inbox_count = 0
    if workspace:
        from apps.inbox.models import InboxMessage

        sidebar_unread_inbox_count = (
            InboxMessage.objects.for_workspace(workspace.id).filter(status=InboxMessage.Status.UNREAD).count()
        )

    # Pending approval count for badge
    sidebar_pending_approvals = 0
    if workspace:
        from apps.composer.models import PlatformPost

        sidebar_pending_approvals = (
            PlatformPost.objects.filter(
                post__workspace_id=workspace.id,
                status__in=["pending_review", "pending_client"],
            )
            .values("post_id")
            .distinct()
            .count()
        )

    # Idea columns and tags for the quick-create modal in the sidebar
    sidebar_idea_columns = []
    sidebar_idea_tags = []
    if workspace:
        from apps.composer.models import IdeaGroup, Tag

        groups = IdeaGroup.objects.for_workspace(workspace.id).order_by("position", "created_at")
        sidebar_idea_columns = [{"id": str(g.id), "label": g.name} for g in groups] if groups.exists() else []
        sidebar_idea_tags = list(Tag.objects.for_workspace(workspace.id).values_list("name", flat=True))

    # Workspace creation permission (org owners and admins only)
    can_create_workspace = False
    org_membership = getattr(request, "org_membership", None)
    if org_membership and org_membership.org_role in ("owner", "admin"):
        can_create_workspace = True

    # Trustpilot review ask: only for users with at least one connected channel,
    # in any of their workspaces, so pages without a workspace (settings,
    # members) don't make it flicker in and out. Closing it lasts until the
    # next login.
    show_review_banner = False
    dismissed_at = request.user.review_banner_dismissed_at
    last_login = request.user.last_login
    if dismissed_at is None or (last_login is not None and last_login > dismissed_at):
        show_review_banner = bool(sidebar_channels) or (
            SocialAccount.objects.filter(
                workspace_id__in=[ws.id for ws in sidebar_workspaces],
                connection_status=SocialAccount.ConnectionStatus.CONNECTED,
            ).exists()
        )

    return {
        "sidebar_workspaces": sidebar_workspaces,
        "can_create_workspace": can_create_workspace,
        "sidebar_channels": sidebar_channels,
        "sidebar_unhealthy_channels": sidebar_unhealthy_channels,
        "sidebar_unread_inbox_count": sidebar_unread_inbox_count,
        "sidebar_pending_approvals": sidebar_pending_approvals,
        "sidebar_idea_columns": sidebar_idea_columns,
        "sidebar_idea_tags": sidebar_idea_tags,
        "analytics_enabled_platforms": analytics_enabled_platforms,
        "show_review_banner": show_review_banner,
    }
