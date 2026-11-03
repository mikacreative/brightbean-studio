"""Views for the Approval Workflow (F-2.2)."""

import difflib
import re

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_GET, require_POST

from apps.common.htmx import toast_response
from apps.common.ui_messages import localize_ui_error
from apps.composer.models import PlatformPost, Post, PostVersion
from apps.members.decorators import require_permission, require_workspace_role
from apps.social_accounts.models import MANUAL_PLATFORMS
from apps.workspaces.models import Workspace

from . import comments as comment_service
from . import services
from .models import PostComment


def _get_workspace(request, workspace_id):
    from django.core.exceptions import PermissionDenied

    from apps.members.models import WorkspaceMembership

    workspace = get_object_or_404(Workspace, id=workspace_id)
    if not request.user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    if not WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).exists():
        raise PermissionDenied("You do not have access to this workspace.")
    return workspace


def _toast_response(*, tone, title, body="", refresh=None):
    """Return a 204 whose ``HX-Trigger`` carries a toast and an optional refresh event.

    Every approval surface listens for ``showToast`` (renders the toast) and for the
    ``refresh`` event (``approvalAction`` / ``bulkActionComplete``) to re-fetch its list
    in place. This single response shape replaces the old per-caller partial branch.
    """
    return toast_response(
        tone=tone, title=title, body=localize_ui_error(body), events={refresh: True} if refresh else None
    )


# ---------------------------------------------------------------------------
# Approval Actions
# ---------------------------------------------------------------------------


def _review_manual_platform(request, workspace_id, post_id, platform_post_id, decision):
    """Decide one manual version only when the displayed content is current."""
    workspace = _get_workspace(request, workspace_id)
    comment = request.POST.get("comment", "").strip()
    if decision != "approve" and not comment:
        return HttpResponse("请填写修改或驳回原因。", status=400)
    actions = {"approve": services.approve_post, "changes": services.request_changes, "reject": services.reject_post}
    with transaction.atomic():
        pp = get_object_or_404(
            PlatformPost.objects.select_for_update().select_related("post", "social_account"),
            pk=platform_post_id,
            post_id=post_id,
            post__workspace=workspace,
            social_account__platform__in=MANUAL_PLATFORMS,
            status="pending_review",
        )
        if not constant_time_compare(request.POST.get("fingerprint", ""), pp.manual_review_fingerprint):
            return HttpResponse("版本已变更，请刷新后重新审核。", status=409)
        try:
            moved = actions[decision](pp, request.user, workspace, comment)
        except ValueError as exc:
            return _toast_response(tone="error", title="审核失败", body=str(exc))
    if not moved:
        return _toast_response(tone="warn", title="此版本已处理")
    title = {"approve": "此版本已通过内部审核", "changes": "已要求修改此版本", "reject": "已驳回此版本"}[decision]
    return _toast_response(tone="success", title=title, refresh="approvalAction")


@login_required
@require_permission("approve_posts")
@require_POST
def approve_platform(request, workspace_id, post_id, platform_post_id):
    return _review_manual_platform(request, workspace_id, post_id, platform_post_id, "approve")


@login_required
@require_permission("approve_posts")
@require_POST
def request_changes_platform(request, workspace_id, post_id, platform_post_id):
    return _review_manual_platform(request, workspace_id, post_id, platform_post_id, "changes")


@login_required
@require_permission("approve_posts")
@require_POST
def reject_platform(request, workspace_id, post_id, platform_post_id):
    return _review_manual_platform(request, workspace_id, post_id, platform_post_id, "reject")


@login_required
@require_permission("approve_posts")
@require_POST
def approve(request, workspace_id, post_id):
    """Approve a post (or advance it to client review in two-stage mode)."""
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)
    if post.platform_posts.filter(
        social_account__platform__in=MANUAL_PLATFORMS, status__in=("pending_review", "pending_client")
    ).exists():
        return HttpResponse("请展开后逐平台审核人工管理版本。", status=409)
    comment_text = request.POST.get("comment", "")

    try:
        moved = services.approve_post(post, request.user, workspace, comment_text)
    except ValueError as e:
        return _toast_response(tone="error", title="无法通过审核", body=str(e))

    if not moved:
        return _toast_response(tone="warn", title="无需更新", body="这条内容已处理。")

    if post.platform_posts.filter(status="pending_client").exists():
        return _toast_response(tone="success", title="内部审核已通过", body="已提交客户审核", refresh="approvalAction")
    return _toast_response(tone="success", title="已通过", body="准备发布", refresh="approvalAction")


@login_required
@require_permission("approve_posts")
@require_POST
def request_changes_view(request, workspace_id, post_id):
    """Request changes on a post."""
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)
    comment_text = request.POST.get("comment", "")

    try:
        moved = services.request_changes(post, request.user, workspace, comment_text)
    except ValueError as e:
        return _toast_response(tone="error", title="无法退回修改", body=str(e))

    if not moved:
        return _toast_response(tone="warn", title="无需更新", body="这条内容已处理。")

    return _toast_response(tone="info", title="已退回修改", body="已通知作者", refresh="approvalAction")


@login_required
@require_permission("approve_posts")
@require_POST
def reject(request, workspace_id, post_id):
    """Reject a post."""
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)
    comment_text = request.POST.get("comment", "")

    try:
        moved = services.reject_post(post, request.user, workspace, comment_text)
    except ValueError as e:
        return _toast_response(tone="error", title="无法驳回", body=str(e))

    if not moved:
        return _toast_response(tone="warn", title="无需更新", body="这条内容已处理。")

    return _toast_response(tone="error", title="内容已驳回", body="已通知作者", refresh="approvalAction")


@login_required
@require_permission("approve_posts")
@require_POST
def resume(request, workspace_id, post_id):
    """Lift a client-requested hold (on_hold → approved)."""
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)

    moved = services.resume_hold(post, request.user, workspace)
    if not moved:
        return _toast_response(tone="warn", title="无需更新", body="这条内容未暂停。")

    return _toast_response(tone="success", title="已解除暂停", body="已恢复为通过状态", refresh="approvalAction")


@login_required
@require_permission("approve_posts")
@require_POST
def bulk_action(request, workspace_id):
    """Bulk approve or reject posts."""
    workspace = _get_workspace(request, workspace_id)
    action = request.POST.get("action")
    post_ids = request.POST.getlist("post_ids")

    if not post_ids:
        return _toast_response(tone="warn", title="请选择内容")

    if (
        action == "approve"
        and PlatformPost.objects.filter(
            post_id__in=post_ids,
            post__workspace=workspace,
            social_account__platform__in=MANUAL_PLATFORMS,
            status__in=("pending_review", "pending_client"),
        ).exists()
    ):
        return HttpResponse("请展开后逐平台审核人工管理版本。", status=409)

    if action == "approve":
        results = services.bulk_approve(post_ids, request.user, workspace)
    elif action == "reject":
        comment_text = request.POST.get("comment", "")
        try:
            results = services.bulk_reject(post_ids, request.user, workspace, comment_text)
        except ValueError as e:
            return _toast_response(tone="error", title="无法驳回", body=str(e))
    else:
        return _toast_response(tone="error", title="操作无效")

    n = sum(1 for _, success, _ in results if success)

    if n == 0:
        return _toast_response(
            tone="warn", title="无需更新", body="没有内容仍在待审核状态。", refresh="bulkActionComplete"
        )
    if action == "approve":
        return _toast_response(
            tone="success",
            title=f"已通过 {n} 条内容",
            body="已进入下一阶段",
            refresh="bulkActionComplete",
        )
    return _toast_response(tone="error", title=f"已驳回 {n} 条内容", body="已通知创作者", refresh="bulkActionComplete")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@login_required
@require_workspace_role("viewer")
@require_POST
def add_comment(request, workspace_id, post_id):
    """Add a comment to a post."""
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)

    body = request.POST.get("body", "").strip()
    if not body:
        return HttpResponse("请填写评论内容。", status=400)

    visibility = request.POST.get("visibility", PostComment.Visibility.EXTERNAL)
    parent_id = request.POST.get("parent_id") or None
    attachment = request.FILES.get("attachment")

    try:
        comment_service.create_comment(
            post=post,
            author=request.user,
            body=body,
            visibility=visibility,
            parent_id=parent_id,
            attachment=attachment,
        )
    except ValueError as e:
        return HttpResponse(localize_ui_error(e), status=400)

    # Return updated comment list
    comments = comment_service.get_comments_for_post(post, request.user)
    return render(
        request,
        "approvals/partials/comment_list.html",
        {
            "comments": comments,
            "post": post,
            "workspace": workspace,
        },
    )


@login_required
@require_workspace_role("viewer")
@require_POST
def edit_comment(request, workspace_id, post_id, comment_id):
    """Edit a comment."""
    workspace = _get_workspace(request, workspace_id)
    body = request.POST.get("body", "").strip()

    if not body:
        return HttpResponse("请填写评论内容。", status=400)

    # Scope the post + comment lookup to the request workspace so a viewer
    # cannot edit / surface comments belonging to a different workspace.
    post = get_object_or_404(Post, id=post_id, workspace=workspace)

    try:
        comment_service.update_comment(comment_id, request.user, body, workspace=workspace)
    except (ValueError, PermissionError) as e:
        return HttpResponse(localize_ui_error(e), status=400 if isinstance(e, ValueError) else 403)

    comments = comment_service.get_comments_for_post(post, request.user)
    return render(
        request,
        "approvals/partials/comment_list.html",
        {
            "comments": comments,
            "post": post,
            "workspace": workspace,
        },
    )


@login_required
@require_workspace_role("viewer")
@require_POST
def delete_comment(request, workspace_id, post_id, comment_id):
    """Soft-delete a comment."""
    workspace = _get_workspace(request, workspace_id)

    try:
        comment_service.delete_comment(comment_id, request.user, workspace)
    except (ValueError, PermissionError) as e:
        return HttpResponse(localize_ui_error(e), status=400 if isinstance(e, ValueError) else 403)

    post = get_object_or_404(Post, id=post_id, workspace=workspace)
    comments = comment_service.get_comments_for_post(post, request.user)
    return render(
        request,
        "approvals/partials/comment_list.html",
        {
            "comments": comments,
            "post": post,
            "workspace": workspace,
        },
    )


@login_required
@require_workspace_role("viewer")
@require_GET
def comment_attachment(request, workspace_id, post_id, comment_id):
    """Serve a comment's attachment behind the workspace membership check.

    Not routed through MEDIA_URL like the rest of the uploads: a comment can be
    ``visibility=internal``, which apps/client_portal/views.py filters out of
    what portal clients are shown. A public /media/comment_attachments/ URL
    would hand back exactly what that filter withholds, so config/urls.py
    leaves this prefix off PUBLIC_MEDIA_PREFIXES and sends readers here.
    """
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)
    comment = get_object_or_404(
        PostComment,
        id=comment_id,
        post=post,
        deleted_at__isnull=True,
    )

    if not comment.attachment:
        raise Http404("This comment has no attachment.")

    # The stored object can outlive the row (lifecycle rule, manual deletion);
    # opening it then raises a backend error rather than returning an empty
    # FieldFile, so map that to 404 instead of a 500.
    try:
        handle = comment.attachment.open("rb")
    except Exception:  # noqa: BLE001 - storage backends raise varied errors (OSError, botocore ClientError)
        raise Http404("Attachment is no longer stored.") from None

    response = FileResponse(handle)
    # Belt and braces: these are ImageFields, so Django's own
    # validate_image_file_extension already refuses an .html suffix, but this
    # response never passes through a CDN or proxy that would add it.
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    return response


# ---------------------------------------------------------------------------
# Version Diff
# ---------------------------------------------------------------------------


@login_required
@require_workspace_role("viewer")
@require_GET
def version_diff(request, workspace_id, post_id):
    """Show diff between two post versions."""
    workspace = _get_workspace(request, workspace_id)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)

    versions = PostVersion.objects.filter(post=post).order_by("-version_number")

    v1_num = request.GET.get("v1")
    v2_num = request.GET.get("v2")

    if v1_num and v2_num:
        version_old = versions.filter(version_number=int(v1_num)).first()
        version_new = versions.filter(version_number=int(v2_num)).first()
    elif versions.count() >= 2:
        version_new = versions[0]
        version_old = versions[1]
    else:
        version_old = None
        version_new = versions.first()

    # Build diff data
    diff_data = _build_diff(
        version_old.snapshot if version_old else {},
        version_new.snapshot if version_new else {},
    )

    context = {
        "post": post,
        "workspace": workspace,
        "versions": versions,
        "version_old": version_old,
        "version_new": version_new,
        "diff_data": diff_data,
    }

    if request.htmx:
        return render(request, "approvals/partials/version_diff.html", context)

    return render(request, "approvals/version_diff.html", context)


def _word_diff(old_str, new_str):
    """Word-level diff tokens for caption changes.

    Returns a list of ``{"t": text, "k": "same"|"add"|"del"}`` so the template can
    render additions/removals inline (the approved design's ``DiffText``). Tokens
    keep their surrounding whitespace so the rendered text reads naturally.
    """
    a = re.split(r"(\s+)", old_str or "")
    b = re.split(r"(\s+)", new_str or "")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append({"t": "".join(a[i1:i2]), "k": "same"})
            continue
        if i1 != i2:
            out.append({"t": "".join(a[i1:i2]), "k": "del"})
        if j1 != j2:
            out.append({"t": "".join(b[j1:j2]), "k": "add"})
    return out


def _build_diff(old_snapshot, new_snapshot):
    """Build a structured diff between two version snapshots."""
    diff = {
        "caption_changed": old_snapshot.get("caption", "") != new_snapshot.get("caption", ""),
        "caption_diff": _word_diff(old_snapshot.get("caption", ""), new_snapshot.get("caption", "")),
        "media_changed": old_snapshot.get("media", []) != new_snapshot.get("media", []),
        "media_old": old_snapshot.get("media", []),
        "media_new": new_snapshot.get("media", []),
        "platforms_changed": old_snapshot.get("platform_posts", []) != new_snapshot.get("platform_posts", []),
    }
    return diff
